"""Freeze a read-only raw-write probe on the tested logical-bounds GPU candidate."""
from pathlib import Path
import json,shutil,subprocess,time,hashlib,sys
P=Path(__file__).resolve().parents[1];B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build');D=Path('/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/blood-echoes-arm64-audit');PIN=D/'experiments/arm64-runtime'
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
def sha(f):
 with Path(f).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def main():
 require_no_games()
 # The renderer candidate links frozen baseline objects. Reject any header/context
 # drift from the reviewed source revision, while allowing this observer's source.
 changed=subprocess.check_output(['git','diff','--name-only','f349bd44ed37bc2658ef1327f9e2a1ecba6ef5ef','--','experiments/arm64-runtime/vendor/metal_api','experiments/arm64-runtime/src'],cwd=D,text=True).splitlines()
 assert set(changed).issubset({'experiments/arm64-runtime/src/bb_raw_write_audit.h','experiments/arm64-runtime/vendor/metal_api/shadps4/video_core/texture_cache/texture_cache.cpp'}),changed
 e=P/'evidence'/('raw-write-observer-'+str(time.time_ns()));e.mkdir();base=json.loads((P/'evidence/tiler-bounds-candidate-current.json').read_text());lib=Path(base['candidate']);assert sha(lib)==base['sha256'];first=json.loads((P/'evidence/first-upload-candidate-current.json').read_text());original=Path(first['evidence'])/'texture_cache.cpp';assert sha(original)==first['source_fixed_sha256']
 source=original.read_text();current=(P/'vendor/metal_api/shadps4/video_core/texture_cache/texture_cache.cpp').read_text();start=current.index('\n    if (const auto probe = BbRawWriteAudit::Begin())');end=current.index('\n\n    // bbport: only an image',start);block=current[start:end];needle='void TextureCache::InvalidateMemoryFromGPU(VAddr address, size_t max_size) {\n    std::scoped_lock lock{mutex};';assert source.count(needle)==1;source=source.replace(needle,needle+block,1);needle='#include "bb_stutter_audit.h"';assert source.count(needle)==1;source=source.replace(needle,needle+'\n#include "bb_raw_write_audit.h"',1)
 snapshot=e/'source';snapshot.mkdir();f=snapshot/'texture_cache.cpp';f.write_text(source);shutil.copy2(P/'src/bb_raw_write_audit.h',snapshot/'bb_raw_write_audit.h')
 cmd=[x.replace(str(P/'vendor/metal_api'),str(PIN/'vendor/metal_api')).replace('-I'+str(P/'src'),'-I'+str(PIN/'src'))for x in first['compile']];cmd[1:1]=['-I'+str(snapshot)];obj=e/'texture_cache.o';old=first['compile'][first['compile'].index('-o')+1];cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(f)
 link=list(base['link']);assert link.count(old)==1;link[link.index(old)]=str(obj);out=lib.with_name('libbbgpu.raw-write-audit-'+e.name.rsplit('-',1)[-1]+'.dylib');assert not out.exists();link[link.index('-o')+1]=str(out)
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4;policy=MemoryPolicy(before);start=time.monotonic()
 with(e/'build.log').open('w')as log:
  for c in [cmd,link]:
   proc=subprocess.Popen(c,cwd=B,stdout=log,stderr=subprocess.STDOUT)
   try:
    while proc.poll()is None:
     why=policy.observe(time.monotonic()-start,observer.system())
     if why or time.monotonic()-start>180:raise RuntimeError(why or 'compile timeout')
     time.sleep(.25)
    assert not proc.returncode,str(e/'build.log')
   finally:
    if proc.poll()is None:
     proc.terminate()
     try:proc.wait(timeout=3)
     except subprocess.TimeoutExpired:proc.kill();proc.wait()
 def deps(f):return[l.strip().split(' (compatibility')[0]for l in subprocess.check_output(['otool','-L',str(f)],text=True).splitlines()[1:]]
 expected={Path(x).name:x for x in deps(lib)}
 for x in deps(out):
  if Path(x).name in expected and x!=expected[Path(x).name]:subprocess.run(['install_name_tool','-change',x,expected[Path(x).name],str(out)],check=True)
 subprocess.run(['install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['codesign','--force','--sign','-',str(out)],check=True);subprocess.run(['codesign','--verify','--strict',str(out)],check=True);assert sha(lib)==base['sha256']
 r=dict(candidate=str(out),sha256=sha(out),baseline=str(lib),baseline_sha256=base['sha256'],evidence=str(e),compile=cmd,link=link,source_sha256=sha(f),header_sha256=sha(snapshot/'bb_raw_write_audit.h'),pinned_header_revision='f349bd44',diagnostic_only=True,default_enabled=False,selection_changed=False,invalidation_changed=False,maximum_probes=256,maximum_records=64,maximum_images_per_probe=4,maximum_probe_bytes=16<<20,before=before,after=observer.system(),memory_policy=MemoryPolicy.MANIFEST)
 (e/'build.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/raw-write-audit-candidate-current.json').write_text(json.dumps(r,indent=2)+'\n');print(e)
if __name__=='__main__':main()
