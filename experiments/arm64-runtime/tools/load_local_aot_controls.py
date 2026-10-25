"""Native disk-library admission and GPU output controls, private authored artifacts only."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess,time
from qa_build_guard import require_no_games
from warm_archive_manifest import resolve_gpu_identity
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--binary',required=True,type=Path);a=ap.parse_args();require_no_games()
base=json.loads((P/'evidence/local-aot-authored-current.json').read_text());case=Path(base['case']);root=Path(base['root']);cohort=base['cohort'];summary=json.loads((root/cohort/'artifacts/builder-summary.json').read_text());assert summary['status']=='passed'
build=summary['build_id'];artifacts=root/cohort/'artifacts'/build;binary=a.binary.resolve(strict=True);identity=resolve_gpu_identity(binary,P);assert identity['library_sha256']==base['identity']['library_sha256']
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
env.update(BB_GPU_USER_DIR=str(case/'gpu'),HOME=str(case),TMPDIR=str(case/'tmp')+'/',DYLD_PRINT_LIBRARIES='1',BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_ROOT=str(root),BB_MTL_LOCAL_AOT_COHORT=cohort,BB_MTL_LOCAL_AOT_BUILD_ID=build)
results=[]
def run(name,hit,math='safe',source='plain',changes=None):
 d=case/(name+'-'+str(time.time_ns()));d.mkdir();child_env=dict(env)
 for k,v in(changes or{}).items():
  if v is None:child_env.pop(k,None)
  else:child_env[k]=v
 proc=subprocess.run([str(binary),str(d),str(int(hit)),math,source],env=child_env,capture_output=True,text=True,timeout=30);(d/'console.log').write_text(proc.stdout+proc.stderr);assert proc.returncode==0,str(d)
 loaded=[x for x in proc.stderr.splitlines()if'libbbgpu'in x and'.dylib'in x];assert len(loaded)==1 and identity['library']in loaded[0]
 r=json.loads((d/'result.json').read_text());assert r['readback_correct']and bool(r['hit'])==hit;results.append(dict(name=name,**r));return r
safe=run('load-safe',True);run('load-relaxed',True,'relaxed');run('load-source',True,'safe','changed')
run('off-existing-cache',False,changes={'BB_MTL_LOCAL_AOT_MODE':None})
run('wrong-cohort',False,changes={'BB_MTL_LOCAL_AOT_COHORT':'f'*64})
run('no-build-id',False,changes={'BB_MTL_LOCAL_AOT_BUILD_ID':None});run('different-build-id',False,changes={'BB_MTL_LOCAL_AOT_BUILD_ID':'e'*64})
key=safe['key'];receipt=artifacts/(key+'.json');library=artifacts/(key+'.metallib');original_receipt=receipt.read_bytes();original_library=library.read_bytes()
def receipt_negative(name,**changes):
 data=json.loads(original_receipt);data.update(changes)
 try:receipt.write_text(json.dumps(data));run(name,False)
 finally:receipt.write_bytes(original_receipt)
for field,value in [('schema',True),('source_sha256','0'*64),('entry','not_main'),('math_mode','relaxed'),('build_id','a'*64),('library_sha256','0'*64),('library_bytes',float(len(original_library)))]:receipt_negative('receipt-'+field,**{field:value})
try:library.write_bytes(b'authored damaged library');run('corrupt-library',False)
finally:library.write_bytes(original_library)
saved=library.with_suffix('.saved');assert not saved.exists()
try:library.rename(saved);run('missing-library',False)
finally:saved.rename(library)
assert receipt.read_bytes()==original_receipt and library.read_bytes()==original_library
r=dict(passed=True,case=str(case),root=str(root),cohort=cohort,build_id=build,identity=identity,tests=len(results),results=results,successful_artifacts_preserved=True,game_started=False)
(case/'load-summary.json').write_text(json.dumps(r,indent=2)+'\n');print(case);print('PASS',len(results),'native admission/output controls');print('build_id',build)
