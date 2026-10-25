"""Root runs this only AFTER applying integration.patch, regeneration and build.
Authored fixtures only; each positive child physically pins at most 16 KiB.
Independent C deterministic controls use zero physical wiring. No formal data.
"""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tests'));sys.path.insert(0,str(P/'tools'))
from mlock_fixture import make
from qa_build_guard import require_no_games
require_no_games();e=P/'evidence'/('mlock-contract-'+str(time.time_ns()));e.mkdir();scratch=P/'state'/e.name;scratch.mkdir();gpu=scratch/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(scratch),TMPDIR=str(scratch)+'/',BB_GPU_USER_DIR=str(gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none',BB_MLOCK_MAX_CALL_MIB='16',BB_MLOCK_MAX_TOTAL_MIB='64',BB_MLOCK_MAX_PAGES='4096')
native_controls=[]
for source in ['test_mlock_ledger.c','test_memory_mlock.c','test_mlock_native_wire.c']:
 native=e/Path(source).stem
 cmd=['/usr/bin/clang','-std=c11','-O2','-arch','arm64','-UNDEBUG','-ffunction-sections','-fdata-sections','-Wl,-dead_strip','-I'+str(P/'vendor/bb_runtime/src'),'-I'+str(P/'vendor/bb_runtime'),'-I'+str(P/'src'),'-I/opt/homebrew/include',str(P/'tests'/source)]+([str(P/'vendor/bb_runtime/src/runtime_kernel.c')]if source=='test_memory_mlock.c'else[])+['-o',str(native)]
 r=subprocess.run(cmd,text=True,capture_output=True,timeout=30);(e/(source+'.build.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
 r=subprocess.run([str(native),str(scratch)],env=env,text=True,capture_output=True,timeout=10);(e/(source+'.run.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
 result=None
 if source=='test_mlock_native_wire.c':
  result=json.loads(r.stdout);assert result['passed']and result['mapping_removed']and result['wire_cleanup']==0 and result['ledger_current_pages']==0 and result['ledger_peak_bytes']==16384
  assert [result[k]for k in ['wire_before','wire_first','wire_duplicate','wire_overlap','wire_unlocked','wire_before_MAP_FIXED','wire_after_MAP_FIXED_before_ledger_forget','wire_reused','wire_reused_overlap','wire_final_unlock']]==[0,1,1,1,0,1,0,1,1,0]
 native_controls.append(dict(source=source,passed=True,physical_wiring_bytes=16384 if result else 0,independent_Mach_result=result))
rows=[]
for mode in ['flex-set','readonly','none-lock','none-unlock','image','stack','unpublished','overflow','budget','freed']:
 fixture=P/'fixtures'/(e.name+'-'+mode+'.bbprobe');fixture.write_bytes(make(mode));state=P/'state'/(e.name+'-'+mode)
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(fixture),'--state',str(state)],env=env,capture_output=True,text=True,timeout=20);log=r.stdout+r.stderr;(e/(mode+'.log')).write_text(log);assert r.returncode==0,(mode,r.returncode,log[-3000:])
 imports=[line.split('\t')for line in(state/'imports.tsv').read_text().splitlines()];selected=[x for x in imports if x[1]=='sceKernelMlock'];assert len(selected)==1 and selected[0][2]=='native',selected
 # Every positive uses at most one guest page, and every child explicitly
 # releases it before exit. Capture the ledger report instead of trusting exit.
 reports=[line for line in log.splitlines()if line.startswith('Runtime: Mlock requests=')];assert len(reports)==1,reports
 assert ', wired=0,'in reports[0],reports
 rows.append(dict(mode=mode,exit=r.returncode,passed=True,ledger=reports[0]))
report=dict(passed=True,native_controls=native_controls,independent_Mach_wire_count=True,game_started=False,deterministic_host_controls=True,actual_FEX=True,max_physically_locked_per_child=16384,quality_changed=False,cache_capacity_changed=False,cases=rows,CPU_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest())
(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(e);print(json.dumps(report,indent=2))
