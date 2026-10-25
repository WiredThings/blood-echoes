"""Exercise real Layer symbols in a private GPU library, without starting Metal/game."""
from pathlib import Path
import json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from gpu_candidate_identity import verify
require_no_games();m=json.loads((P/'evidence/first-upload-build-current.json').read_text());assert m['suite']=='stable-mirror'
identity=verify(dict(target=m['binary'],library=m['library'],library_sha256=m['library_sha256']),P)
e=P/'evidence'/('stable-mirror-library-controls-'+str(time.time_ns()));e.mkdir();private=P/'state'/e.name;private.mkdir();gpu=private/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(private),TMPDIR=str(private)+'/',BB_GPU_USER_DIR=str(gpu),BB_AUDIO='none',BB_ONLINE='0',BB_PIPELINE_CACHE='0',DYLD_PRINT_LIBRARIES='1')
with(e/'console.log').open('w')as log:r=subprocess.run([m['binary']],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=15)
text=(e/'console.log').read_text();loaded=[x for x in text.splitlines()if'dyld['in x and'libbbgpu'in x];assert len(loaded)==1 and m['library']in loaded[0],loaded
passed=r.returncode==0 and'controls=46 'in text
(e/'result.json').write_text(json.dumps(dict(passed=passed,exit=r.returncode,controls=46,game_started=False,Metal_device_started=False,private_state=str(private),gpu_load_identity=identity,console_summary=[x for x in text.splitlines()if x.startswith('PASS ')]),indent=2)+'\n');print(e);assert passed
