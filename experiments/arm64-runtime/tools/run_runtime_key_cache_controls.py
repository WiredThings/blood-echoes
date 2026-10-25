"""Real cache selection/Storage controls; explicit expected old defect versus fixed library."""
from pathlib import Path
import argparse,json,os,re,subprocess,time
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify
P=Path(__file__).resolve().parents[1]
a=argparse.ArgumentParser();a.add_argument('--expect',choices=['baseline','fixed'],required=True);args=a.parse_args();require_no_games();meta=json.loads((P/'evidence/first-upload-build-current.json').read_text());assert meta['suite']=='runtime-key-cache'
identity=verify(dict(target=meta['binary'],library=meta['library'],library_sha256=meta['library_sha256']),P)
stamp=str(time.time_ns());e=P/'evidence'/('runtime-key-cache-'+stamp);e.mkdir();state=P/'state'/('runtime-key-cache-'+stamp);state.mkdir();(state/'gpu').mkdir();(state/'tmp').mkdir()
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(HOME=str(state),TMPDIR=str(state/'tmp')+'/',BB_GPU_USER_DIR=str(state/'gpu'),DYLD_PRINT_LIBRARIES='1')
r=subprocess.run([meta['binary'],str(state)],capture_output=True,text=True,env=env,timeout=30);(e/'stdout.log').write_text(r.stdout);(e/'stderr.log').write_text(r.stderr)
loaded=[s for s in r.stderr.splitlines()if 'libbbgpu'in s and '.dylib'in s];correct=len(loaded)==1 and meta['library']in loaded[0];assert 'implemented in both'not in r.stderr
failed=[s for s in r.stdout.splitlines()if s.startswith('FAIL ')];expected=['FAIL stored-gs-invocations','FAIL stored-gs-input-ring','FAIL stored-gs-output-ring','FAIL stored-cs-shared-size','FAIL ordered-shared-size-must-create-module','FAIL worker-unknown-must-not-reuse-or-compile']if args.expect=='baseline'else[]
passed=correct and r.returncode==(2 if args.expect=='baseline'else 0)and sorted(failed)==sorted(expected)
result=dict(expected=args.expect,outcomes_matched=passed,production_fix_proven=False,exit=r.returncode,failures=failed,identity=identity,backend_module_factory='counting mock only',actual_storage_and_getprogram=True,game_started=False,GPU_commands_submitted=False,evidence=str(e));(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(e);print(r.stdout[-2600:]);print(r.stderr[-800:]if not passed else'');assert passed,str(e)
(P/('evidence/runtime-key-cache-'+args.expect+'-current.json')).write_text(json.dumps(result,indent=2)+'\n')
