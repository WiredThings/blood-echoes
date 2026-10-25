"""Execute actual BuildLibrary entry/memo/path controls with authored fake entry factories."""
from pathlib import Path
import json,os,subprocess,time
from qa_build_guard import require_no_games
from warm_archive_manifest import resolve_gpu_identity
P=Path(__file__).resolve().parents[1];require_no_games();m=json.loads((P/'evidence/first-upload-build-current.json').read_text());assert m['suite']=='local-aot-entry'
binary=Path(m['binary']);identity=resolve_gpu_identity(binary,P);assert identity['library_sha256']==m['library_sha256']
case=P/'state'/('local-aot-entry-authored-'+str(time.time_ns()));case.mkdir(mode=0o700)
for name in ['gpu','tmp']:(case/name).mkdir(mode=0o700)
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(HOME=str(case),TMPDIR=str(case/'tmp')+'/',BB_GPU_USER_DIR=str(case/'gpu'),DYLD_PRINT_LIBRARIES='1')
proc=subprocess.run([str(binary),str(case/'gpu')],env=env,capture_output=True,text=True,timeout=30);text=proc.stdout+proc.stderr;(case/'console.log').write_text(text)
loaded=[x for x in proc.stderr.splitlines()if'libbbgpu'in x and'.dylib'in x];actual=len(loaded)==1 and identity['library']in loaded[0]
r=dict(passed=proc.returncode==0 and actual,exit=proc.returncode,case=str(case),identity=identity,actual_shader_compiler_and_build_library=True,device_capabilities_real=True,library_and_function_factories_mocked=True,gpu_commands_submitted=False,game_started=False)
(case/'result.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/local-aot-entry-control-current.json').write_text(json.dumps(r,indent=2)+'\n');print(case);print('\n'.join(proc.stdout.splitlines()[-15:]));assert r['passed'],str(case/'console.log')
