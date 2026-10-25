"""Serial real compiler/cache regression, with an actual fully rebuilt GPU candidate."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,required=True);a=ap.parse_args();candidate=a.candidate.resolve(strict=True);assert candidate.is_relative_to(P/'evidence');record=json.loads(candidate.read_text());library=record['candidate']
assert record['all_GPU_units_rebuilt']==230 and hashlib.sha256(Path(library).read_bytes()).hexdigest()==record['sha256']
e=P/'evidence'/('runtime-key-regressions-'+str(time.time_ns()));e.mkdir();steps=[]
def run(name,args):
 with(e/(name+'.log')).open('w')as log:r=subprocess.run([sys.executable,*args],cwd=P,stdout=log,stderr=subprocess.STDOUT)
 steps.append(dict(name=name,exit=r.returncode));(e/'steps.json').write_text(json.dumps(steps,indent=2)+'\n');assert r.returncode==0,(name,str(e));print('PASS',name,flush=True)
old_layout=json.loads((P/'evidence/metadata-layout-current.json').read_text())['layout'];old=json.loads(Path(old_layout).read_text())
run('consumer-build',['tools/build_first_upload_test.py','--suite','runtime-keys','--library',library])
run('actual-consumers',['tools/run_runtime_key_compilation_controls.py'])
run('cache-build',['tools/build_first_upload_test.py','--suite','runtime-key-cache','--library',library])
run('actual-fixed-cache',['tools/run_runtime_key_cache_controls.py','--expect','fixed'])
run('metadata-layout',['tools/build_metadata_layout.py'])
new_layout=json.loads((P/'evidence/metadata-layout-current.json').read_text())['layout'];new=json.loads(Path(new_layout).read_text())
# Check every compiler-derived offset/size/type, excluding file identities and fixture paths.
ignore={'source_sha256','binary_sha256','authored_writer_fixtures'}
assert {k:v for k,v in old.items()if k not in ignore}=={k:v for k,v in new.items()if k not in ignore},'serialization/ABI layout changed'
run('existing-regressions',['tools/run_warm_persistence_regressions.py','--candidate',str(candidate)])
result=dict(passed=True,candidate=record,steps=steps,actual_runtime_key_cache=json.loads((P/'evidence/runtime-key-cache-fixed-current.json').read_text()),actual_compilation_consumers=json.loads((P/'evidence/runtime-key-compilation-current.json').read_text()),layout_unchanged=True,old_layout=old_layout,new_layout=new_layout,game_started=False)
assert result['actual_runtime_key_cache']['identity']['library_sha256']==record['sha256']and result['actual_compilation_consumers']['identity']['library_sha256']==record['sha256']
(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');(P/'evidence/runtime-key-regressions-current.json').write_text(json.dumps(dict(evidence=str(e),candidate_sha256=record['sha256']),indent=2)+'\n');print(e,flush=True)
