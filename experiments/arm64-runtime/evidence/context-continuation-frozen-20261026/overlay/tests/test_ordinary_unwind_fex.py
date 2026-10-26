#!/usr/bin/env python3
"""Short real-FEX ordinary-unwind control, isolated copies of prepared dependencies.

Builds only the current engine translation unit and one authored test. Never builds
or launches the game and never replaces the prepared embedding archive.
"""
from pathlib import Path
import argparse,hashlib,json,os,shlex,shutil,signal,subprocess,sys,time
D=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True);ap.add_argument('--suite',choices=('ordinary','architectural','consumer'),default='ordinary');a=ap.parse_args()
P=a.project.absolute();assert P==P.resolve(strict=True) and P.name=='arm64-runtime-prototype'
assert (P/'build/fex/build.ninja').is_file()and(P/'state').is_dir()
sys.path[:0]=[str(D/'tools'),str(P/'tools')]
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games

def sha(p):
 with Path(p).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
require_no_games();B=P/'build/fex';X=P/'evidence'/({'ordinary':'ordinary-unwind-current-','architectural':'architectural-continuation-current-','consumer':'owned-signal-libc-consumer-'}[a.suite]+str(time.time_ns()));X.mkdir(mode=0o700)
state=P/'state'/X.name;state.mkdir(mode=0o700);(state/'gpu').mkdir();(state/'tmp').mkdir()
source_paths=[D/'vendor/aether/src/core/fex'/n for n in ('fex_guest_engine.h','fex_guest_engine.cpp')]+[D/'src'/n for n in ('guest_ordinary_unwind.h','guest_signal_return_classifier.h','ordinary_unwind_bytes.h','ordinary_unwind.asm','bb_darwin_veneer_slab.h','static_range_index.h','bb_architectural_continuation.h','owned_signal_libc_contract.h')]+[Path(__file__),D/'tests/test_ordinary_unwind.cpp',D/'tests/test_architectural_continuation.cpp',D/'tests/test_owned_signal_libc_consumer.cpp']
source_hashes={str(p):sha(p)for p in source_paths};original_archive=sha(B/'libbb_fex_embedding.a')
observer=NativeObserver();before=observer.system();assert before['pressure']!=4 and not before['locked'];policy=MemoryPolicy(before);start=time.monotonic();samples=[];steps=[]
report=dict(schema=1,status='incomplete',execution_root=str(X),source_root=str(D),source_sha256=source_hashes,prepared_archive_sha256=original_archive,before=before,memory_policy=MemoryPolicy.MANIFEST,game_started=False,GUI_started=False,formal_data_modified=False,old_cache_modified=False)
def check():
 elapsed=time.monotonic()-start;s=observer.system();samples.append(dict(seconds=elapsed,**s));reason=policy.observe(elapsed,s)
 if reason or elapsed>180:raise RuntimeError(reason or 'bounded ordinary control build/run')
def run(label,cmd,cwd=B,env=None,expected=0,seconds=60):
 require_no_games();check();log=X/(label+'.log');t=time.monotonic()
 with log.open('w')as output:
  proc=subprocess.Popen(cmd,cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
  try:
   while proc.poll()is None:
    check()
    if time.monotonic()-t>seconds:raise RuntimeError(label+' deadline')
    if log.stat().st_size>4*1024*1024:raise RuntimeError('bounded authored output')
    time.sleep(.05)
  finally:
   if proc.poll()is None:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=2)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
 text=log.read_text(errors='replace');row=dict(label=label,command=list(map(str,cmd)),exit=proc.returncode,expected_exit=expected,seconds=time.monotonic()-t,log=str(log));steps.append(row);assert proc.returncode==expected,(row,text[-2000:]);return text
try:
 commands=subprocess.check_output(['ninja','-C',str(B),'-t','commands','Bin/bb-runtime-arm64'],text=True).splitlines()
 engine=shlex.split(next(c for c in commands if 'fex_guest_engine.cpp.o -c 'in c))
 engine=[s.replace(str(P/'vendor/aether/src/core/fex/fex_guest_engine.cpp'),str(D/'vendor/aether/src/core/fex/fex_guest_engine.cpp'))for s in engine]
 engine[1:1]=['-I'+str(D/'vendor/aether/src'),'-I'+str(D/'src'),'-I'+str(P/'vendor/aether/src/core/fex')]
 for flag in ('-o','-MT','-MF'):
  if flag in engine:engine[engine.index(flag)+1]=str(X/('engine.d'if flag=='-MF'else'fex_guest_engine.cpp.o'))
 run('engine-compile',engine);archive=X/'libbb_fex_embedding.a';shutil.copy2(B/'libbb_fex_embedding.a',archive)
 run('archive-replace',['ar','r',str(archive),str(X/'fex_guest_engine.cpp.o')]);run('archive-index',['ranlib',str(archive)])
 base=shlex.split(next(c for c in commands if 'runtime.cpp.o -c 'in c));compile=[];i=0
 while i<len(base):
  v=base[i]
  if v in('-MD','-DNDEBUG'):i+=1;continue
  if v in('-MT','-MF','-o','-c'):i+=2;continue
  compile.append(v);i+=1
 compile[1:1]=['-I'+str(D/'vendor/aether/src'),'-I'+str(D/'src'),'-I'+str(P/'vendor/aether/src/core/fex')]
 compile+=['-UNDEBUG','-o',str(X/'test.o'),'-c',str(D/'tests'/dict(ordinary='test_ordinary_unwind.cpp',architectural='test_architectural_continuation.cpp',consumer='test_owned_signal_libc_consumer.cpp')[a.suite])]
 run('author-compile',compile)
 base=shlex.split(commands[-1]);base=base[base.index('/usr/bin/c++'):];base=base[:base.index('&&')];link=[];i=0
 while i<len(base):
  v=base[i]
  if v=='-o':i+=2;continue
  if v.endswith('.o'):i+=1;continue
  link.append(str(archive)if v=='libbb_fex_embedding.a'else v);i+=1
 binary=X/'ordinary-unwind-control';link[1:1]=[str(X/'test.o')];link+=['-o',str(binary)];run('author-link',link)
 # Reassembling the published project-owned bytes guards against stale arrays.
 if a.suite=='ordinary'and shutil.which('nasm'):
  raw=X/'ordinary.bin';run('assemble',['nasm','-f','bin',str(D/'src/ordinary_unwind.asm'),'-o',str(raw)])
  import re
  expected=bytes(int(x,16)for x in re.findall(r'0x([0-9a-f]+)',(D/'src/ordinary_unwind_bytes.h').read_text().split('{{',1)[1]))
  assert raw.read_bytes()==expected,'restore byte array differs from authored ASM'
 boot=P.parent/'assets/boot-linked.bin';env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','FEX_','DYLD_'))}
 env.update(BB_ARM_HW_TSO='0',BB_GPU_USER_DIR=str(state/'gpu'),BB_ONLINE='0',BB_QUICKSAVE='0',BB_AUDIO='none',HOME=str(state),TMPDIR=str(state/'tmp'))
 if a.suite=='ordinary':
  ordinary=run('actual-fex',[str(binary),str(boot)],env=env,seconds=15)
  nested=run('actual-nested-fex',[str(binary),str(boot),'--nested-only'],env=env,expected=74,seconds=15)
  lines=[json.loads(line)for line in ordinary.splitlines()if line.startswith('{')];assert lines[-1]['real_fex']and lines[-1]['actual_hle_adapter']and not lines[-1]['signal_imports_registered']and lines[-1]['cases']==16 and lines[-1]['assertions']>=265,lines
  rows=[json.loads(line)for line in nested.splitlines()if line.startswith('{')];assert rows[-1]['restoration_rejected']and rows[-1]['depth']==2 and not rows[-1]['launcher_restart_emitted']
  report.update(ordinary=lines[-1],nested=rows[-1])
 elif a.suite=='architectural':
  text=run('actual-fex',[str(binary)],env=env,seconds=15);lines=[json.loads(line)for line in text.splitlines()if line.startswith('{')]
  assert lines[-1]['real_fex']and lines[-1]['cases']==15 and not lines[-1]['sigreturn_registered']and not lines[-1]['PS4_signal_frame_tested'],lines
  report['architectural']=lines[-1]
 else:
  text=run('actual-fixed-libc',[str(binary),str(boot)],env=env,seconds=15);lines=[json.loads(line)for line in text.splitlines()if line.startswith('{')]
  assert lines[-1]['real_fex']and lines[-1]['actual_fixed_libc_calls']==19 and lines[-1]['classification_and_unwind']and not lines[-1]['owned_signal_producer_tested'],lines
  report['fixed_libc_consumer']=lines[-1]
 report.update(status='passed',executable_sha256=sha(binary),private_embedding_archive_sha256=sha(archive))
finally:
 report.update(steps=steps,after=observer.system(),seconds=time.monotonic()-start,samples=samples,prepared_archive_unchanged=sha(B/'libbb_fex_embedding.a')==original_archive,source_unchanged=all(sha(p)==h for p,h in source_hashes.items()))
 (X/'result.json').write_text(json.dumps(report,indent=2)+'\n');observer.close()
 assert report['prepared_archive_unchanged']and report['source_unchanged']
print(json.dumps(dict(status=report['status'],execution_root=str(X),ordinary=report.get('ordinary'),seconds=report['seconds']),indent=2))
