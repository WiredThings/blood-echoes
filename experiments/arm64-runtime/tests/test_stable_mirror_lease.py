"""Compile real Layer source and authored stable identity controls; no GPU/window/game."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('stable-mirror-controls-'+str(time.time_ns()));e.mkdir()
source=P/'tests/test_stable_mirror_lease.cpp';layer=P/'vendor/metal_api/layer';exe=e/'test'
cmd=['/usr/bin/clang++','-std=c++20','-arch','arm64','-O2','-Wall','-Wextra','-Werror','-UNDEBUG','-pthread','-I'+str(layer),str(source),str(layer/'bblayer_gpu_memory.cpp'),'-o',str(exe)]
record=dict(passed=False,game_started=False,GPU_started=False,physical_payload_bytes=0,source_sha256={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest()for f in [source,layer/'bblayer_gpu_memory.h',layer/'bblayer_gpu_memory.cpp']},compile=cmd)
with(e/'compile.log').open('w')as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
record['compile_exit']=r.returncode
if not r.returncode:
 with(e/'console.log').open('w')as log:r=subprocess.run([str(exe)],stdout=log,stderr=subprocess.STDOUT,timeout=15)
 record['run_exit']=r.returncode;record['passed']=r.returncode==0
(e/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(e);assert record['passed'],str(e)
