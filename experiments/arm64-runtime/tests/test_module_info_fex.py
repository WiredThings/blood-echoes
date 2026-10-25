"""Original module contracts through authored real-FEX call sites; no game."""
from pathlib import Path
import hashlib,json,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tests'));sys.path.insert(0,str(P/'tools'))
from module_info_fixture import make
from qa_build_guard import require_no_games
require_no_games();protected=[P.parent/'assets/boot-linked.bin',*[P.parent/'assets/CUSA03173-1.09'/x for x in ['eboot.bin','sce_module/libc.prx','sce_module/libSceFios2.prx']],*[P/'launcher-component-stage/scripts'/x for x in ['prepare.py','link_modules.py','link_libc.py','game_check.py']]]
def hashes():return {str(f):hashlib.file_digest(f.open('rb'),'sha256').hexdigest()for f in protected}
before=hashes()
e=P/'evidence'/('module-info-fex-'+str(time.time_ns()));e.mkdir();scratch=P/'state'/e.name;scratch.mkdir();gpu=scratch/'gpu';gpu.mkdir()
env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(scratch),TMPDIR=str(scratch)+'/',BB_GPU_USER_DIR=str(gpu),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none')
cmd=['/usr/bin/clang++','-std=c++20','-O2','-arch','arm64','-UNDEBUG','-Wno-deprecated-declarations','-DBB_PROTOTYPE_ROOT="'+str(P)+'"','-I'+str(P/'src'),str(P/'tests/test_guest_module_info.cpp'),'-o',str(e/'native')]
r=subprocess.run(cmd,capture_output=True,text=True,timeout=30);(e/'build.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
rows=[]
for mode in ['valid','dynamic-align','bad-output','missing','flags','negative-flags','bad-count','bad-match']:
 boot,metadata=make(mode);fixture=P/'fixtures'/(e.name+'-'+mode+'.bbprobe');fixture.write_bytes(boot);meta=P/'fixtures'/(e.name+'-'+mode+'.modules');meta.write_bytes(metadata)
 if mode=='valid':
  r=subprocess.run([str(e/'native'),str(fixture),str(meta)],env=env,capture_output=True,text=True,timeout=10);(e/'native.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
 r=subprocess.run([str(P/'build/fex/Bin/bb-game-arm64'),'--authored-fixture','--boot',str(fixture),'--state',str(P/'state'/(e.name+'-'+mode))],env=env|{'BB_ARM_AUTHORED_MODULE_INFO':str(meta)},capture_output=True,text=True,timeout=20)
 (e/(mode+'.log')).write_text(r.stdout+r.stderr);expected=74 if mode=='bad-match'else 0
 assert r.returncode==expected,(mode,r.returncode,(r.stdout+r.stderr)[-3500:])
 imports=[line.split('\t')for line in(P/'state'/(e.name+'-'+mode)/'imports.tsv').read_text().splitlines()];module_imports=[x for x in imports if x[1]in {'sceKernelGetModuleInfoFromAddr','__elf_phdr_match_addr'}];assert len(module_imports)==2 and all(x[2]=='native-module-info'for x in module_imports),imports
 rows.append(dict(mode=mode,exit=r.returncode,passed=True))
# Same exact consumer also validates the actual original-loader helper invocation.
actual=scratch/'actual';actual.mkdir();r=subprocess.run([str(e/'native'),str(P.parent/'assets/boot-linked.bin'),str(actual),str(P.parent/'assets/CUSA03173-1.09')],env=env,capture_output=True,text=True,timeout=30);(e/'actual-loader.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
helper_failures=[]
for status in [1,75,76,0]:
 helper=scratch/('authored-python-exit-'+str(status));helper.write_text('#!/bin/sh\nexit '+str(status)+'\n');helper.chmod(0o700)
 command=[str(P/'build/fex/Bin/bb-game-arm64'),'--boot',str(P.parent/'assets/boot-linked.bin'),'--app0',str(P.parent/'assets/CUSA03173-1.09'),'--state',str(P/'state'/(e.name+'-helper-failure-'+str(status))),'--modules-only']
 r=subprocess.run(command,env=env|{'BB_QUICKSAVE_PYTHON':str(helper)},capture_output=True,text=True,timeout=10);(e/('helper-failure-'+str(status)+'.log')).write_text(r.stdout+r.stderr)
 assert r.returncode==1,(status,r.returncode,r.stdout+r.stderr)
 assert ('module metadata preparation failed'if status else'module metadata must be regular')in r.stderr,r.stderr
 helper_failures.append(dict(helper_status=status,CPU_status=r.returncode,passed=True,restart_requested=False))
assert hashes()==before,'source/boot/original scripts changed'
report=dict(passed=True,game_started=False,actual_FEX=True,native_registry_controls=True,original_loader_process_bridge=True,source_files_unmodified=True,helper_failure_exit_contract=helper_failures,cases=rows,CPU_sha256=hashlib.file_digest((P/'build/fex/Bin/bb-game-arm64').open('rb'),'sha256').hexdigest())
(e/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(e);print(json.dumps(report,indent=2))
