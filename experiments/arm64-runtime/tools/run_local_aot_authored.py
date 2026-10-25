"""Serial actual-source local builder proof. Run under run_authored_controls_guard.py."""
from pathlib import Path
import argparse,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--candidate',type=Path,default=P/'evidence/local-aot-candidate-current.json');args=parser.parse_args();metadata=args.candidate.resolve(strict=True);assert metadata.is_relative_to(P/'evidence')
e=P/'evidence'/('local-aot-authoritative-controls-'+str(time.time_ns()));e.mkdir();m=json.loads(metadata.read_text());library=m['candidate'];steps=[]
def run(name,args):
 with(e/(name+'.log')).open('w')as log:r=subprocess.run([sys.executable,*args],cwd=P,stdout=log,stderr=subprocess.STDOUT)
 steps.append(dict(name=name,exit=r.returncode));(e/'steps.json').write_text(json.dumps(steps,indent=2)+'\n');assert r.returncode==0,(name,str(e));print('PASS',name,flush=True)
run('native-build',['tools/build_first_upload_test.py','--suite','local-aot','--library',library]);fixture=json.loads((P/'evidence/first-upload-build-current.json').read_text());native=fixture['binary']
run('record',['tools/record_local_aot_controls.py','--binary',native,'--library',library])
run('phase-build',['tools/build_first_upload_test.py','--suite','shader-phases','--library',library]);phase=json.loads((P/'evidence/first-upload-build-current.json').read_text())['binary']
run('phase-record',['tools/run_local_aot_phase.py','--binary',phase,'--mode','record'])
base=json.loads((P/'evidence/local-aot-authored-current.json').read_text());root=Path(base['root']);cohort=base['cohort']
run('actual-build',['tools/local_metal_builder.py','--enable','--root',str(root),'--cohort',cohort]);summary=json.loads((root/cohort/'artifacts/builder-summary.json').read_text());assert summary['status']=='passed'and summary['built']==4
run('native-load',['tools/load_local_aot_controls.py','--binary',native])
run('phase-load',['tools/run_local_aot_phase.py','--binary',phase,'--mode','load','--build-id',summary['build_id']])
# A second preparation pass must reuse valid successful artifacts byte-for-byte.
run('actual-verified-skip',['tools/local_metal_builder.py','--enable','--root',str(root),'--cohort',cohort]);again=json.loads((root/cohort/'artifacts/builder-summary.json').read_text());assert again['status']=='passed'and again['built']==0 and again['skipped']==4 and again['build_id']==summary['build_id']
r=dict(passed=True,evidence=str(e),case=base['case'],library=library,library_sha256=m['sha256'],build_id=summary['build_id'],steps=steps,actual_metal_and_metallib_called=True,game_started=False,default_enabled=False,performance_claim=False)
(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(e)
