"""One-object opt-in layout observation on the existing first-upload GPU candidate.
No resource-selection changes, no game launch, no current app/library replacement.
"""
from pathlib import Path
import hashlib,json,shutil,subprocess,time
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
P=Path(__file__).resolve().parents[1];B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build')
def sha(f):
 with Path(f).open('rb')as s:return hashlib.file_digest(s,'sha256').hexdigest()
def main():
 require_no_games();e=P/'evidence'/('layout-audit-candidate-'+str(time.time_ns()));e.mkdir()
 base=json.loads((P/'evidence/first-upload-candidate-current.json').read_text());library=Path(base['candidate']);assert sha(library)==base['sha256']
 baseline=Path(base['evidence'])/'texture_cache.cpp';assert sha(baseline)==base['source_fixed_sha256']
 current=(P/'vendor/metal_api/shadps4/video_core/texture_cache/texture_cache.cpp').read_text()
 begin='        const auto& selected = slot_images[id].info;\n';end='        if (!IsViewTypeCompatible'
 assert current.count(begin)==1
 block=current.split(begin,1)[1].split(end,1)[0];assert 'BbLayoutAudit::Print'in block and 'Counter().Claim()'in block
 source=baseline.read_text();needle=begin+end;assert source.count(needle)==1
 source=source.replace(needle,begin+block+end).replace('#include "bb_stutter_audit.h"','#include "bb_stutter_audit.h"\n#include "bb_layout_audit.h"',1)
 snapshot=e/'source';snapshot.mkdir();fixed=snapshot/'texture_cache.cpp';fixed.write_text(source);shutil.copy2(P/'src/bb_layout_audit.h',snapshot/'bb_layout_audit.h')
 cmd=list(base['compile']);cmd[1:1]=['-I'+str(snapshot)];obj=e/'texture_cache.o';cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(fixed)
 link=list(base['link']);old_obj=base['compile'][base['compile'].index('-o')+1];assert link.count(old_obj)==1;link[link.index(old_obj)]=str(obj)
 out=library.with_name('libbbgpu.layout-audit-'+e.name.rsplit('-',1)[-1]+'.dylib');assert not out.exists();link[link.index('-o')+1]=str(out)
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4
 policy=MemoryPolicy(before);started=time.monotonic()
 def run(command,log):
  require_no_games();proc=subprocess.Popen(command,cwd=B,stdout=log,stderr=subprocess.STDOUT)
  try:
   while proc.poll()is None:
    reason=policy.observe(time.monotonic()-started,observer.system())
    if reason or time.monotonic()-started>180:raise RuntimeError('layout build guard: '+str(reason or 'timeout'))
    time.sleep(.25)
   if proc.returncode:raise RuntimeError('layout build failed: '+str(e/'build.log'))
  finally:
   if proc.poll()is None:
    proc.terminate()
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
 with(e/'build.log').open('w')as log:run(cmd,log);run(link,log)
 def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['/usr/bin/otool','-L',str(f)],text=True).splitlines()[1:]]
 expected={Path(x).name:x for x in deps(library)}
 for x in deps(out):
  replacement=expected.get(Path(x).name)
  if replacement and replacement!=x:subprocess.run(['/usr/bin/install_name_tool','-change',x,replacement,str(out)],check=True)
 subprocess.run(['/usr/bin/install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(out)],check=True);subprocess.run(['/usr/bin/codesign','--verify','--strict',str(out)],check=True)
 assert sha(library)==base['sha256'];assert subprocess.check_output(['/usr/bin/lipo','-archs',str(out)],text=True).strip()=='arm64'
 r=dict(evidence=str(e),candidate=str(out),sha256=sha(out),baseline=str(library),baseline_sha256=base['sha256'],source_baseline_sha256=sha(baseline),source_sha256=sha(fixed),header_sha256=sha(snapshot/'bb_layout_audit.h'),compile=cmd,link=link,changed_objects=1,diagnostic_only=True,default_enabled=False,maximum_records=64,selection_changed=False,cache_budget_changed=False,quality_changed=False,normal_app_changed=False,memory_policy=MemoryPolicy.MANIFEST,before=before,after=observer.system())
 (e/'build.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/layout-audit-candidate-current.json').write_text(json.dumps(r,indent=2)+'\n');print(e)
if __name__=='__main__':main()
