#!/usr/bin/env python3
"""Actual schema2 worker: per-tiling-library admission failures in fresh clones."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,signal,subprocess,sys,time
ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True);ap.add_argument('--native-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
P=a.project.resolve(strict=True);N=a.native_dir.resolve(strict=True);assert not a.output.exists();a.output.mkdir(parents=True);E=a.output.resolve()
b=json.loads((N/'build.json').read_text());f=json.loads((N/'fixed-helpers-validation.json').read_text());O=Path(b['source_root'])
sys.path[:0]=[str(O/'tools'),str(Path(__file__).resolve().parent),str(P/'tools')]
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from launcher_fixed_helpers import validate_result
from prepare_game_psos import dyld_gpu_image,verify_loaded_gpu
import local_metal_builder as builder

def sha(p):
 with Path(p).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def inventory(root):
 assert root==root.resolve(strict=True)
 rows={}
 for p in root.rglob('*'):
  assert not p.is_symlink()
  if p.is_file():rows[str(p.relative_to(root))]=sha(p)
 return rows
worker=Path(b['binaries']['worker']['binary']);assert sha(worker)==b['binaries']['worker']['sha256'];assert sha(b['library'])==b['library_sha256'];assert f['all_passed']
items=f['calls'][2]['result']['tiling_libraries']['items'];assert len(items)==14 and len({i['request_key']for i in items})==14
original=Path(f['state'])/'gpu/local-metal'/f['cohort'];baseline=inventory(original)
require_no_games();state=P/'state'/('tiling-library-negatives-'+str(time.time_ns()));state.mkdir(mode=0o700)
observer=NativeObserver();before=observer.system();assert before['pressure']!=4 and not before['locked'];policy=MemoryPolicy(before);start=time.monotonic();cases=[];samples=[]
report=dict(schema=1,status='incomplete',state=str(state),cases=cases,gpu_library_sha256=b['library_sha256'],worker_sha256=sha(worker),formal_data_modified=False,loader_checks_modified=False,game_started=False,performance_evidence=False,before=before,memory_policy=MemoryPolicy.MANIFEST)
def check():
 elapsed=time.monotonic()-start;s=observer.system();samples.append(dict(seconds=elapsed,**s));reason=policy.observe(elapsed,s)
 if reason or elapsed>300:raise RuntimeError(reason or 'bounded admission negative suite')
def run(label,command,env,cwd):
 require_no_games();check();log=cwd/(label+'.log')
 with log.open('w')as out:
  p=subprocess.Popen(command,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
  try:
   while p.poll()is None:
    check()
    if log.stat().st_size>8*1024*1024:raise RuntimeError('bounded native log')
    time.sleep(.1)
  finally:
   if p.poll()is None:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 assert p.returncode==0,(p.returncode,log)
 return p.pid,log,log.read_text()
try:
 # Authored valid Metal binary with no main0, made by the existing bounded builder.
 authored=state/'authored';(authored/'gpu/local-metal').mkdir(parents=True);root=authored/'gpu/local-metal';tmp=authored/'tmp';tmp.mkdir()
 source=b'#include <metal_stdlib>\nusing namespace metal;\nkernel void unrelated(device uint* p [[buffer(0)]],uint i [[thread_position_in_grid]]){p[i]=i;}\n'
 request=dict(schema=2,recipe=builder.LIBRARY_RECIPE,cohort=f['cohort'],source_sha256=hashlib.sha256(source).hexdigest(),language='metal3.1',math_mode='fast',math_fp32='fast');request['key']=builder.derive_key(request)
 folder=root/f['cohort']/'requests'/request['key'][:2];folder.mkdir(parents=True);(folder/(request['key']+'.json')).write_bytes(builder.canonical(request));(folder/(request['key']+'.metal')).write_bytes(source)
 builder.PROJECT=P;other,guard,toolchain=builder.real_services(tmp)
 class CombinedGuard:
  def check(self,*,preflight=False):check();return guard.check(preflight=preflight)
 prior=os.environ.get('BB_GPU_USER_DIR');os.environ['BB_GPU_USER_DIR']=str(authored/'gpu')
 try:built=builder.build(root,f['cohort'],enabled=True,guard=CombinedGuard(),toolchain=toolchain)
 finally:
  if prior is None:os.environ.pop('BB_GPU_USER_DIR',None)
  else:os.environ['BB_GPU_USER_DIR']=prior
  other.close()
 assert built['status']=='passed'and built['built']==1
 foreign=root/f['cohort']/'artifacts'/built['build_id']/(request['key']+'.metallib');foreign_blob=foreign.read_bytes();report['missing_entry_artifact']=dict(authored_source_sha256=request['source_sha256'],library_sha256=sha(foreign),entry='unrelated',no_main0_in_authored_source=True)
 for mode,item in [('positive',None)]+[(mode,item)for mode in ('missing-library','missing-entry','bad-digest','corrupt-blob')for item in items]:
  label=mode+('-'+item['request_key'][:12]if item else '');case=state/label;case.mkdir();gpu=case/'gpu';local=gpu/'local-metal';local.mkdir(parents=True);shutil.copytree(original,local/f['cohort']);assert inventory(local/f['cohort'])==baseline
  artifact=gpu/'cache/CUSA03173/metal';artifact.mkdir(parents=True);old_artifact=Path(f['state'])/'gpu/cache/CUSA03173/metal'
  shutil.copytree(old_artifact,artifact,dirs_exist_ok=True)
  if item:
   key=item['request_key'];art=local/f['cohort']/'artifacts'/f['local_builder']['build_id'];receipt=art/(key+'.json');blob=art/(key+'.metallib');r=json.loads(receipt.read_text());assert sha(blob)==r['library_sha256']==item['library_sha256']
   if mode=='missing-library':blob.unlink()
   elif mode=='missing-entry':blob.write_bytes(foreign_blob);r.update(library_sha256=hashlib.sha256(foreign_blob).hexdigest(),library_bytes=len(foreign_blob));receipt.write_text(json.dumps(r))
   elif mode=='bad-digest':r['library_sha256']='0'*64;receipt.write_text(json.dumps(r))
   elif mode=='corrupt-blob':blob.write_bytes(blob.read_bytes()+b'AUTHORED_CORRUPTION')
  scratch=case/'scratch';scratch.mkdir();tmp=case/'tmp';tmp.mkdir();packet=case/'packet.json';packet.write_text(json.dumps(dict(packet_schema=1,fixed_helper_seed_schema=2,candidate_only=True,runtime_cache_import_allowed=False,current_cohort=f['cohort'],artifact_cache_dir=str(artifact))))
  env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(HOME=str(case),TMPDIR=str(tmp),BB_GPU_USER_DIR=str(gpu),BB_MTL_ARCHIVE_COMPATIBILITY_ID=f['cohort'],BB_MTL_PERSIST_WARM_ARCHIVE='0',BB_MTL_RELEASE_WARM_ARCHIVE='1',BB_MTL_REPLAY_THREAD='0',BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_ROOT=str(local),BB_MTL_LOCAL_AOT_COHORT=f['cohort'],BB_MTL_LOCAL_AOT_BUILD_ID=f['local_builder']['build_id'],BB_MTL_STRICT_FLOAT='1',BB_MTL_FLOAT_RULES='strict',MTL_DEBUG_LAYER='1',DYLD_PRINT_LIBRARIES='1')
  pid,log,text=run('worker',[str(worker),'helpers-probe',str(packet),str(scratch)],env,case)
  records=[json.loads(line.split(' ',1)[1])for line in text.splitlines()if line.startswith('FIXED_HELPER_RESULT ')];assert len(records)==1
  loaded={image for line in text.splitlines()if(image:=dyld_gpu_image(line,pid))};identity=verify_loaded_gpu(loaded,b)
  row=dict(exit=0,result=records[0]);result,complete=validate_result(row,'helpers-probe');actual=result['tiling_libraries'];assert actual['processed']==14 and result['ready']==8 and result['all_fixed_native_ready']
  assert result['artifact_counts']['compile_attempts']==0 and result['artifact_counts']['hits']==8
  assert not any(result[k]for k in ('storage_opened','renderer_owners_constructed','guest_Runtime_constructed'))
  if item:
   assert not complete and actual['local_ready']==13 and not actual['complete']
   target=(item['bits'],item['macro'],item['texture'])
   for v in actual['items']:
    assert v['entry_ready'] and v['actual_route']==(1 if(v['bits'],v['macro'],v['texture'])==target else 2)
   hit_keys=set(__import__('re').findall(r'Metal helper local AOT: hit key=([0-9a-f]{64})',text));assert item['request_key']not in hit_keys
  else:assert complete and actual['local_ready']==14 and actual['complete']
  cases.append(dict(mode=mode,target={k:item[k]for k in ('bits','macro','texture','request_key')}if item else None,exit=0,complete=complete,local_ready=actual['local_ready'],items=actual['items'],fixed_ready=result['ready'],strict_fixed_hits=result['artifact_counts']['hits'],ordinary_pso_compiles=0,loaded_gpu=identity,log=str(log)))
  print(label,'local',actual['local_ready'],'complete',complete,flush=True)
 report['status']='passed'
finally:
 report.update(after=observer.system(),seconds=time.monotonic()-start,samples=samples,original_cache_modified=inventory(original)!=baseline)
 if report['original_cache_modified']:report['status']='failed-original-cache-mutated'
 (E/'result.json').write_text(json.dumps(report,indent=2)+'\n');observer.close();assert not report['original_cache_modified']
print(json.dumps(dict(status=report['status'],cases=len(cases),output=str(E))))
