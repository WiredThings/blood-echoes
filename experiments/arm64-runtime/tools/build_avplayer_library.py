from pathlib import Path
import hashlib,json,shlex,shutil,subprocess,sys,time
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'));from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
require_no_games();M=P/'vendor/metal_api';B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build');base=json.loads((P/'evidence/menu-candidate-build.json').read_text());lib=Path(base['candidate']);sha=lambda f:hashlib.file_digest(Path(f).open('rb'),'sha256').hexdigest();assert sha(lib)==base['sha256']
e=P/'evidence'/('avplayer-host-candidate-'+str(time.time_ns()));e.mkdir();observer=NativeObserver();initial=observer.system();assert not initial['locked']and initial['pressure']!=4;policy=MemoryPolicy(initial);start=time.monotonic()
def run(cmd,log):
 require_no_games();process=subprocess.Popen(cmd,cwd=B,stdout=log,stderr=subprocess.STDOUT)
 try:
  while process.poll()is None:
   reason=policy.observe(time.monotonic()-start,observer.system())
   if reason or time.monotonic()-start>180:raise RuntimeError('guarded build stopped: '+str(reason or'time limit'))
   time.sleep(.25)
  assert process.returncode==0,str(e/'build.log')
 finally:
  if process.poll()is None:
   process.terminate()
   try:process.wait(timeout=3)
   except subprocess.TimeoutExpired:process.kill();process.wait()
commands=subprocess.check_output(['ninja','-C',str(B),'-t','commands'],text=True).splitlines();old='/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/bbport-mac-optimized/engines/metal/gpu';link=list(base['link']);built=[]
header=e/'source/shim/core/libraries/kernel/threads.h';header.parent.mkdir(parents=True);shutil.copy2(M/'shim/core/libraries/kernel/threads.h',header)
with(e/'build.log').open('w')as log:
 for name in ['avplayer_state','avplayer_source']:
  rel='shadps4/core/libraries/avplayer/'+name+'.cpp';source=e/'source'/rel;source.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(M/rel,source)
  cmd=next(shlex.split(x)for x in commands if x.startswith(('/usr/bin/c++','/usr/bin/clang++'))and x.endswith('/'+rel));cmd=[x.replace(old,str(M))for x in cmd];cmd[1:1]=['-I'+str(e/'source/shim'),'-I'+str(P/'src')];obj=e/(name+'.o');cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(source);run(cmd,log)
  slots=[i for i,x in enumerate(link)if x.endswith('/'+rel+'.o')];assert len(slots)==1;link[slots[0]]=str(obj);built.append(dict(compile=cmd,source_sha256=sha(source)))
 out=lib.with_name('libbbgpu.avplayer-'+str(time.time_ns())+'.dylib');link[link.index('-o')+1]=str(out);run(link,log)
def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['otool','-L',str(f)],text=True).splitlines()[1:]]
expected={Path(x).name:x for x in deps(lib)}
for edge in deps(out):
 replacement=expected.get(Path(edge).name)
 if replacement and edge!=replacement:subprocess.run(['install_name_tool','-change',edge,replacement,str(out)],check=True)
subprocess.run(['install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['codesign','--force','--sign','-',str(out)],check=True);assert sha(lib)==base['sha256']
stable=lib.with_name('libbbgpu.avplayer-candidate.dylib')
if stable.exists():
 assert stable.is_file()and not stable.is_symlink()
 shutil.copy2(stable,e/'previous-stable.dylib')
shutil.copy2(out,stable)
record=dict(evidence=str(e),candidate=str(out),stable_candidate=str(stable),host_bridge_revision=1,sha256=sha(out),baseline=str(lib),baseline_sha256=base['sha256'],compile=built,link=link,header_sha256=sha(header),memory_policy=MemoryPolicy.MANIFEST,before=initial,after=observer.system(),game_cpu_wired=False,normal_app_changed=False)
(e/'build.json').write_text(json.dumps(record,indent=2)+'\n');(P/'evidence/avplayer-host-candidate-current.json').write_text(json.dumps(record,indent=2)+'\n');print(e)
