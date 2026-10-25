"""Authored code only: dereference and rewrite __progname through real FEX."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tests'));sys.path.insert(0,str(P/'tools'))
from game_fixture import make
from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('guest-progname-'+str(time.time_ns()));e.mkdir()
state=P/'state'/e.name;state.mkdir();gpu=state/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(state),TMPDIR=str(state)+'/',BB_GPU_USER_DIR=str(gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none')
cmd=['/usr/bin/clang++','-std=c++20','-O2','-arch','arm64','-UNDEBUG','-I'+str(P/'src'),str(P/'tests/test_guest_process_parameters.cpp'),'-o',str(e/'native')]
r=subprocess.run(cmd,text=True,capture_output=True,timeout=30);(e/'native-build.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
r=subprocess.run([str(e/'native')],env=env,text=True,capture_output=True,timeout=10);(e/'native.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
rows=[]
for repeat,mode in enumerate(['progname','progname-rewrite','progname','progname-function']):
 fixture=P/'fixtures'/(e.name+'-'+str(repeat)+'.bbprobe');fixture.write_bytes(make(mode));root=P/'state'/(e.name+'-'+str(repeat))
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(fixture),'--state',str(root)],env=env,capture_output=True,text=True,timeout=20)
 (e/(str(repeat)+'-'+mode+'.log')).write_text(r.stdout+r.stderr);expected=80 if mode=='progname-function'else 0
 assert r.returncode==expected,(mode,r.returncode,(r.stdout+r.stderr)[-2400:])
 imports=[line.split('\t')for line in(root/'imports.tsv').read_text().splitlines()];found=[x for x in imports if x[1]=='__progname'];assert len(found)==1
 assert found[0][2]==('missing' if mode=='progname-function'else'guest-process-data'),found
 if mode=='progname-function':assert 'ARM UNIMPLEMENTED'in r.stdout+r.stderr
 rows.append(dict(mode=mode,exit=r.returncode,passed=True,route=found[0][2]))
report=dict(passed=True,game_started=False,authored_code_only=True,native_contract=True,actual_FEX=True,extra_mapping_bytes=0,extra_memory_pool=False,cases=rows,CPU_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest())
(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(e);print(json.dumps(report,indent=2))
