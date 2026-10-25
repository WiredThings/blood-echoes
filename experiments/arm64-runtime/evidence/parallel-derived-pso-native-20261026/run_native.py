#!/usr/bin/env python3
"""Serial seed/warm native controls under the unchanged protection policy."""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,signal,subprocess,sys,time
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
E=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from warm_archive_manifest import make_manifest
ap=argparse.ArgumentParser();ap.add_argument('--state-root',required=True,type=Path)
a=ap.parse_args();require_no_games()
state=a.state_root.resolve(strict=True)
assert state.parent==P/'state' and not a.state_root.is_symlink()
assert not any(state.iterdir()),'requires an empty freshly authored state directory'
b=json.loads((E/'build.json').read_text());exe=Path(b['binary']);lib=Path(b['library'])
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
assert sha(exe)==b['binary_sha256'] and sha(lib)==b['library_sha256']
assert exe.parent.parent==P/'evidence'
for n in ('authored.vert.spv','authored.frag.spv'):shutil.copy2(exe.parent/n,state/n)
for n in ('tmp','gpu','msl'): (state/n).mkdir()
token,manifest=make_manifest(translator_artifacts={'actual_gpu_library':lib},
 game_artifacts={'authored_vs':state/'authored.vert.spv','authored_fs':state/'authored.frag.spv'},
 options={'math':'safe','derived':'portable-author-controls'},profile='authored-derived-pso')
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
env.update(HOME=str(state),TMPDIR=str(state/'tmp'),BB_GPU_USER_DIR=str(state/'gpu'),
 BB_MTL_ARCHIVE_COMPATIBILITY_ID=token,BB_MTL_PERSIST_WARM_ARCHIVE='1',
 BB_MTL_RELEASE_WARM_ARCHIVE='0',BB_MTL_LOCAL_AOT_MODE='off',BB_MTL_STRICT_FLOAT='1',
 BB_MTL_FLOAT_RULES='strict',BB_PIPELINE_CACHE='1',MTL_DEBUG_LAYER='1',DYLD_PRINT_LIBRARIES='1')
observer=NativeObserver();before=observer.system()
assert before['pressure']!=4 and not before['locked'],before
policy=MemoryPolicy(before);start=time.monotonic();cases=[];samples=[]
result=dict(before=before,state=str(state),build=b,manifest=manifest,token=token,
 memory_policy=MemoryPolicy.MANIFEST,game_launched=False,existing_cache_walker=False,
 actual_portable_storage=True,actual_native_factories=True,cases=cases)
try:
 for mode in ('seed','warm'):
  require_no_games();log=E/(mode+'.log');command=[str(exe),mode,str(state)]
  with log.open('w')as out:
   proc=subprocess.Popen(command,stdout=out,stderr=subprocess.STDOUT,env=env,
    cwd=state,start_new_session=True)
   stopped=None;case_start=time.monotonic()
   try:
    while proc.poll() is None:
     now=time.monotonic();sample=observer.system();samples.append(dict(seconds=now-start,**sample))
     stopped=policy.observe(now-start,sample)
     if stopped or now-case_start>60:raise RuntimeError(stopped or 'native author case timeout60')
     time.sleep(.1)
   finally:
    if proc.poll() is None:
     os.killpg(proc.pid,signal.SIGTERM)
     try:proc.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
  text=log.read_text();rows=[json.loads(line.split('AUTHOR_RESULT ',1)[1])for line in text.splitlines()if line.startswith('AUTHOR_RESULT ')]
  loaded=[]
  for line in text.splitlines():
   if 'dyld' in line and 'libbbgpu' in line:
    path=line.split()[-1]
    if path.startswith('/'):loaded.append(str(Path(path).resolve()))
  case=dict(mode=mode,exit=proc.returncode,pid=proc.pid,seconds=time.monotonic()-case_start,
   actual_loaded_gpu_libraries=sorted(set(loaded)),author=rows[-1]if rows else None,log=str(log))
  cases.append(case)
  assert proc.returncode==0,case
  assert case['actual_loaded_gpu_libraries']==[str(lib)],case
  assert len(rows)==1 and rows[0]['pid']==proc.pid,case
 assert cases[0]['pid']!=cases[1]['pid']
 assert cases[1]['author']['strict_archive_hits']==2
 result['all_passed']=True
finally:
 result.update(after=observer.system(),seconds=time.monotonic()-start,samples=samples)
 (E/'validation.json').write_text(json.dumps(result,indent=2)+'\n');observer.close()
print(json.dumps({k:v for k,v in result.items()if k not in ('build','manifest','samples')},indent=2))
