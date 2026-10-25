#!/usr/bin/env python3
"""Run only a root-built authored Metal fixture, never compile or launch a game."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess,time
from warm_archive_manifest import make_manifest,resolve_gpu_identity
from qa_build_guard import require_no_games
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--library',type=Path,required=True);a=ap.parse_args()
 binary=a.binary.resolve(strict=True);library=a.library.resolve(strict=True)
 require_no_games()
 actual=resolve_gpu_identity(binary,P)
 assert Path(actual['library'])==library,'--library does not match actual executable load edge'
 case=P/'state'/('warm-archive-authored-'+str(time.time_ns()));case.mkdir(mode=0o700)
 cache=case/'cache';cache.mkdir();(case/'tmp').mkdir();(case/'gpu').mkdir();game=case/'authored-game-identity';game.write_text('authored test image v1\n')
 patch=case/'authored-patch-identity';patch.write_text('authored applied patch v1\n')
 env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
 env.update(HOME=str(case),TMPDIR=str(case/'tmp')+'/',BB_GPU_USER_DIR=str(case/'gpu'),DYLD_PRINT_LIBRARIES='1',BB_MTL_PERSIST_WARM_ARCHIVE='1',BB_MTL_RELEASE_WARM_ARCHIVE='1',BB_MTL_STRICT_FLOAT='0',BB_MTL_RELAXED_MATH='0')
 options={'language':'3.1','math':'Safe','threadgroup':16}
 def identity(extra=None,artifacts=None):
  return make_manifest(translator_artifacts=artifacts or {'actual_loaded_gpu_library':library},game_artifacts={'image':game,'patch':patch},options=extra or options,profile={'authored':'compute-v1','profile_abi':'SPV10-meta7-key5-msl3'})
 token,manifest=identity();env['BB_MTL_ARCHIVE_COMPATIBILITY_ID']=token
 (case/'manifest.json').write_text(json.dumps(dict(token=token,manifest=manifest),indent=2)+'\n')
 results=[]
 def run(mode,loaded,hits,changes=None,folder=None):
  child_env=env.copy()
  for k,v in (changes or {}).items():
   if v is None:child_env.pop(k,None)
   else:child_env[k]=v
  require_no_games()
  assert resolve_gpu_identity(binary,P)==actual,'GPU artifact changed before child launch'
  proc=subprocess.run([str(binary),mode,str(folder or cache),str(int(loaded)),str(hits)],env=child_env,text=True,capture_output=True,timeout=60)
  results.append(dict(mode=mode,exit=proc.returncode,stdout=proc.stdout,stderr=proc.stderr))
  (case/'results.json').write_text(json.dumps(results,indent=2)+'\n')
  assert proc.returncode==0,(mode,proc.stdout,proc.stderr)
  loaded=[line for line in proc.stderr.splitlines()if 'libbbgpu' in line and '.dylib' in line]
  assert len(loaded)==1 and str(library) in loaded[0],('actual loaded GPU library mismatch',loaded)
  results[-1]['actual_load_verified']=True
  results[-1]['load_identity']=actual
  (case/'results.json').write_text(json.dumps(results,indent=2)+'\n')
  return proc
 run('seed',False,0)
 files=list(cache.glob('pipelines-v3-*.binaryarchive'));assert len(files)==1
 original=files[0].read_bytes();assert original
 # Separate child PID and true FailOnBinaryArchiveMiss hit; speed is not the criterion.
 run('load',True,1)
 run('source',True,0)
 # A render attachment format changes the final PSO while the exact authored
 # vertex/fragment source and manifest stay fixed. Correct pixels are required;
 # Metal may legally reuse native function binaries across fixed render state.
 render_cache=case/'render-cache'
 run('render-seed',False,0,folder=render_cache)
 run('render-load',True,1,folder=render_cache)
 run('render-descriptor',True,-1,folder=render_cache)
 run('write-fail',True,0)
 run('parent-write-fail',True,0)
 legacy=cache/'pipelines.binaryarchive';legacy.write_bytes(b'authored legacy sentinel')
 run('off',False,0,{'BB_MTL_PERSIST_WARM_ARCHIVE':'0'})
 run('missing-token',False,0,{'BB_MTL_ARCHIVE_COMPATIBILITY_ID':None})
 run('invalid-token',False,0,{'BB_MTL_ARCHIVE_COMPATIBILITY_ID':'no'})
 for role,file in [('game',game),('patch',patch)]:
  saved=file.read_bytes();file.write_bytes(saved+b'changed\n');different,_=identity();file.write_bytes(saved);assert different!=token
  run(role+'-identity',False,0,{'BB_MTL_ARCHIVE_COMPATIBILITY_ID':different})
 different,_=identity(extra=dict(options,math='Relaxed'));assert different!=token
 run('options-identity',False,0,{'BB_MTL_ARCHIVE_COMPATIBILITY_ID':different})
 # Translator mismatch is tested from actual different artifact bytes, not a label.
 changed=case/'authored-translator-negative';changed.write_bytes(b'authored distinct translator identity\n')
 different,_=identity(artifacts={'actual_loaded_gpu_library':changed});assert different!=token
 run('translator-identity',False,0,{'BB_MTL_ARCHIVE_COMPATIBILITY_ID':different})
 assert files[0].read_bytes()==original and legacy.read_bytes()==b'authored legacy sentinel'
 summary=dict(status='passed-authored-native-controls',case=str(case),children=len(results),cold_and_warm_are_distinct_processes=True,strict_hits_required=True,system_driver_cache_not_cleared=True,game_launched=False,gpu_os_identity_negatives='authored pure-helper arguments; actual other GPU/OS not available',archive_sha256=hashlib.sha256(original).hexdigest())
 (case/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
