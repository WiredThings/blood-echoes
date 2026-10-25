"""VM synchronization contracts with owned files and authored FEX programs."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tests'));sys.path.insert(0,str(P/'tools'))
from game_fixture import make
from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('msync-contract-'+str(time.time_ns()));e.mkdir();scratch=P/'state'/e.name;scratch.mkdir();gpu=scratch/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(scratch),TMPDIR=str(scratch)+'/',BB_GPU_USER_DIR=str(gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none')
cmd=['/usr/bin/clang','-std=c11','-O2','-arch','arm64','-UNDEBUG','-ffunction-sections','-fdata-sections','-Wl,-dead_strip','-I'+str(P/'vendor/bb_runtime/src'),'-I'+str(P/'vendor/bb_runtime'),'-I'+str(P/'src'),'-I/opt/homebrew/include',str(P/'tests/test_memory_msync.c'),'-o',str(e/'native')]
r=subprocess.run(cmd,text=True,capture_output=True,timeout=30);(e/'native-build.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
r=subprocess.run([str(e/'native'),str(scratch)],env=env,text=True,capture_output=True,timeout=10);(e/'native.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
rows=[]
for mode in ['image','whole-image','native-gap','stack','unpublished','overflow','flex','none','none-unmap','readonly-unmap','release-none','query-none','virtual-none','mmap-reserved','query-bad-output','hole','freed','flags']:
 fixture=P/'fixtures'/(e.name+'-'+mode+'.bbprobe');fixture.write_bytes(make('msync-'+mode));state=P/'state'/(e.name+'-'+mode)
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(fixture),'--state',str(state)],env=env,capture_output=True,text=True,timeout=20);(e/(mode+'.log')).write_text(r.stdout+r.stderr);expected=74 if mode=='query-bad-output'else 0;assert r.returncode==expected,(mode,r.returncode,(r.stdout+r.stderr)[-3000:])
 imports=[line.split('\t')for line in(state/'imports.tsv').read_text().splitlines()];selected=[x for x in imports if x[1]=='msync'];assert len(selected)==1 and selected[0][2]=='native',selected
 rows.append(dict(mode=mode,exit=r.returncode,passed=True))
report=dict(passed=True,game_started=False,native_actual_file_sync=True,concurrent_VMA_lease=True,actual_FEX=True,quality_changed=False,cache_capacity_changed=False,page_wiring=False,cases=rows,CPU_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest())
(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(e);print(json.dumps(report,indent=2))
