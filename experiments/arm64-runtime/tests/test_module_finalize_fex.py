"""Run only after integration/rebuild. Authored fixtures, private fresh state."""
from pathlib import Path
import hashlib,importlib.util,json,subprocess,sys,time
P=next(p for p in Path(__file__).resolve().parents if(p/'tools/qa_build_guard.py').is_file());sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
require_no_games()
spec=importlib.util.spec_from_file_location('candidate_fixture',Path(__file__).with_name('module_finalize_fixture.py'));fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
e=P/'evidence'/('module-finalizer-fex-'+str(time.time_ns()));e.mkdir();state=P/'state'/e.name;state.mkdir();gpu=state/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(state),TMPDIR=str(state)+'/',BB_GPU_USER_DIR=str(gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none')
rows=[]
for mode in ['valid','preserve-nondtor','global-null','unknown-dso','non-guest-callback','data-callback','veneer-callback','bad-info-span','bad-info-identity','bad-atexit-callback','reentrant-finalize']:
 boot,metadata=fixture.make(mode);f=P/'fixtures'/(e.name+'-'+mode+'.bbprobe');f.write_bytes(boot);m=P/'fixtures'/(e.name+'-'+mode+'.modules');m.write_bytes(metadata);root=P/'state'/(e.name+'-'+mode)
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(f),'--state',str(root)],env=env|{'BB_ARM_AUTHORED_MODULE_INFO':str(m)},capture_output=True,text=True,timeout=35)
 (e/(mode+'.log')).write_text(r.stdout+r.stderr);expect=74 if mode in ['bad-info-span','bad-info-identity','bad-atexit-callback','reentrant-finalize']else 0
 assert r.returncode==expect,(mode,r.returncode,(r.stdout+r.stderr)[-4000:])
 imports=[line.split('\t')for line in(root/'imports.tsv').read_text().splitlines()];module_rows=[x for x in imports if x[1]=='__pthread_cxa_finalize'];assert len(module_rows)==1 and module_rows[0][2]=='native-module-finalize'
 rows.append(dict(mode=mode,exit=r.returncode,passed=True))
(e/'result.json').write_text(json.dumps(dict(passed=True,authored_only=True,game_started=False,cases=rows,CPU_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest()),indent=2)+'\n');print(e)
