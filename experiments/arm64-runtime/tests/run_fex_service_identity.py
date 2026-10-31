#!/usr/bin/env python3
"""Real native rebuild/reuse controls in an expendable source snapshot, no game."""
from pathlib import Path
import argparse,hashlib,json,os,sys,time
D=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(D/'tools'))
from build_native_source import BuildGuard
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games
from local_metal_builder import FixedRunner
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
def sha(path):
 with path.open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--source',type=Path,required=True);ap.add_argument('--build',type=Path,required=True);a=ap.parse_args()
 for path in(a.source,a.build):
  assert path.is_absolute()and path==path.resolve(strict=True)and str(path).startswith('/private/tmp/')and path.stat().st_uid==os.getuid(),'owned expendable native snapshot only'
 source=a.source/'experiments/arm64-runtime';assert(source/'src/fex_disk_service.cpp').is_file()and(a.source/'SOURCE-MANIFEST.json').is_file()
 require_no_games();tag=str(time.time_ns());e=P/'evidence'/('service-identity-'+tag);state=P/'state'/('service-identity-'+tag);e.mkdir(mode=0o700);state.mkdir(mode=0o700)
 loader=source/'src/fex_disk_loader.h';abi=source/'src/fex_disk_service.h';original={p:p.read_bytes()for p in(loader,abi)}
 report=dict(schema=1,status='running',game_started=False,formal_data_modified=False,source=str(a.source),build=str(a.build),source_revision=json.loads((a.source/'SOURCE-MANIFEST.json').read_text())['source_commit'],steps=[])
 scratch=a.build/'identity-control-tmp';scratch.mkdir(mode=0o700,exist_ok=True)
 observer=NativeObserver();guard=BuildGuard(observer,MemoryPolicy(observer.system()),require_no_games);runner=FixedRunner(scratch)
 def publish():
  (e/'result.json').write_text(json.dumps(report,indent=2)+'\n')
 def run(label,args):
  guard.check(preflight=True);start=time.monotonic();raw=runner.run(list(map(str,args)),1800,guard,cwd=a.source);(e/(label+'.log')).write_bytes(raw);report['steps'].append(dict(label=label,seconds=time.monotonic()-start));publish();return raw
 def build(label):run(label,['/opt/homebrew/bin/cmake','--build',a.build,'--target','bb-game-arm64','test_fex_prepared_disk','--parallel','2'])
 def snapshot():
  value=json.loads((a.build/'generated/cpu_cache_build.json').read_text());assert value['schema']==2
  return dict(runtime=value['runtime_build'],bridge=value['bridge_build'],runtime_parts=value['runtime_parts'],bridge_parts=value['bridge_parts'],raw_input_sha256=value['raw_input_sha256'],provenance=value['provenance_parts'],cpu=sha(a.build/'Bin/bb-game-arm64'),identity_header=sha(a.build/'generated/bb_cpu_cache_build.h'))
 binary=a.build/'Bin/test_fex_prepared_disk'
 def control(label,mode,base):return run(label,[binary,mode,state,base,'actual-build-identity'])
 try:
  baseline=snapshot();report['baseline']=baseline
  control('generate-baseline','generate','0x7000000000');native={p.name:sha(p)for p in state.glob('*.becc')};assert native
  old=b'FEX DISK proof-cache evictions=';assert original[loader].count(old)==1
  loader.write_bytes(original[loader].replace(old,b'FEX DISK proof-cache identity-control evictions='))
  build('rebuild-storage-change');storage=snapshot();report['storage_change']=storage
  assert storage['runtime']==baseline['runtime']and storage['bridge']==baseline['bridge'],'storage-only change invalidated native compilation identity'
  assert storage['identity_header']==baseline['identity_header']and storage['cpu']!=baseline['cpu'],'rebuild did not prove a changed actual CPU with stable compile header'
  assert storage['provenance']['disk-service']!=baseline['provenance']['disk-service']and storage['provenance']!=baseline['provenance']
  control('load-existing-after-storage-change','load','0x7100000000')
  assert {p.name:sha(p)for p in state.glob('*.becc')}==native,'native products were changed'
  # An ABI-input change must remain fail-closed. This is an explicit authored
  # compatibility-input negative; it does not claim a game needs a new ABI.
  abi.write_bytes(original[abi]+b'\n// Authored ABI-input rejection control.\n')
  build('rebuild-abi-input-change');changed=snapshot();report['abi_input_change']=changed
  assert changed['bridge']!=baseline['bridge'],'ABI input omitted from compatibility identity'
  control('reject-old-after-abi-input-change','reject-build','0x7100000000')
  assert {p.name:sha(p)for p in state.glob('*.becc')}==native
  report['status']='controls-passed'
 finally:
  for path,body in original.items():path.write_bytes(body)
  report['source_restored']=all(p.read_bytes()==v for p,v in original.items());publish()
  try:
   build('restore-native-build');restored=snapshot();report['restored']=restored
   assert restored['runtime']==report['baseline']['runtime']and restored['bridge']==report['baseline']['bridge']
   if report['status']=='controls-passed':
    control('load-after-restoration','load','0x7100000000');report['status']='passed'
  finally:report['game_started']=False;publish();observer.close()
 print('Evidence:',e,flush=True)
 if report['status']!='passed':raise RuntimeError('native service identity controls incomplete')
if __name__=='__main__':main()
