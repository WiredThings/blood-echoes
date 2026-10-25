"""Authored actual compiler consumers; no guest/game execution or visual polling."""
from pathlib import Path
import json,os,subprocess,time
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify
P=Path(__file__).resolve().parents[1];require_no_games();meta=json.loads((P/'evidence/first-upload-build-current.json').read_text());assert meta['suite']=='runtime-keys'
identity=verify(dict(target=meta['binary'],library=meta['library'],library_sha256=meta['library_sha256']),P)
stamp=str(time.time_ns());e=P/'evidence'/('runtime-key-compilation-'+stamp);e.mkdir();state=P/'state'/('runtime-key-authored-'+stamp);state.mkdir();(state/'gpu').mkdir();(state/'tmp').mkdir()
env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))};env.update(HOME=str(state),TMPDIR=str(state/'tmp')+'/',BB_GPU_USER_DIR=str(state/'gpu'),DYLD_PRINT_LIBRARIES='1')
proc=subprocess.run([meta['binary']],capture_output=True,text=True,env=env,timeout=30);(e/'stdout.log').write_text(proc.stdout);(e/'stderr.log').write_text(proc.stderr)
loaded=[s for s in proc.stderr.splitlines()if 'libbbgpu'in s and '.dylib'in s];correct=len(loaded)==1 and meta['library']in loaded[0];assert 'implemented in both'not in proc.stderr
r=dict(passed=proc.returncode==0 and correct,exit=proc.returncode,actual_GPU_library_verified=correct,identity=identity,game_started=False,GPU_commands_submitted=False,source=str(P/'tests/metal/test_runtime_key_compilation.mm'),evidence=str(e));(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(e);print(proc.stdout[-1800:]);print(proc.stderr[-1800:]if proc.returncode else'');assert r['passed'],str(e)
(P/'evidence/runtime-key-compilation-current.json').write_text(json.dumps(r,indent=2)+'\n')
