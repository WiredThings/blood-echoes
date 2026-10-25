"""Two-object ABI-preserving, opt-in logical-coverage candidate. No game/app replacement."""
from pathlib import Path
import hashlib,json,shutil,subprocess,time,shlex
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
P=Path(__file__).resolve().parents[1];B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build');M=P/'vendor/metal_api'
def sha(f):
 with Path(f).open('rb')as s:return hashlib.file_digest(s,'sha256').hexdigest()
def main():
 require_no_games();e=P/'evidence'/('tiler-bounds-candidate-'+str(time.time_ns()));e.mkdir();base=json.loads((P/'evidence/first-upload-candidate-current.json').read_text());lib=Path(base['candidate']);assert sha(lib)==base['sha256']
 generated=json.loads((P/'evidence/tiler-bounds-generated-current.json').read_text());inc=Path(generated['include']);assert inc.is_dir()
 snapshot=e/'source';snapshot.mkdir();shutil.copy2(P/'src/bb_tiler_bounds.h',snapshot/'bb_tiler_bounds.h')
 overlay=snapshot/'video_core/renderer_metal';overlay.mkdir(parents=True)
 for name in ['mtl_runtime_impl.h','mtl_texture_tiling.h']:shutil.copy2(M/'shadps4/video_core/renderer_metal'/name,overlay/name)
 commands=subprocess.check_output(['ninja','-C',str(B),'-t','commands','bbgpu'],text=True).splitlines();link=list(base['link']);builds=[]
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4;policy=MemoryPolicy(before);started=time.monotonic()
 def run(command,log):
  require_no_games();proc=subprocess.Popen(command,cwd=B,stdout=log,stderr=subprocess.STDOUT)
  try:
   while proc.poll()is None:
    reason=policy.observe(time.monotonic()-started,observer.system())
    if reason or time.monotonic()-started>240:raise RuntimeError('isolated tiler build guard: '+str(reason or 'timeout'))
    time.sleep(.25)
   if proc.returncode:raise RuntimeError('build failed: '+str(e/'build.log'))
  finally:
   if proc.poll()is None:
    proc.terminate()
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
 with(e/'build.log').open('w')as log:
  for relative in ['texture_cache/tile_manager.cpp','renderer_metal/mtl_runtime_kernels.mm']:
   source=M/'shadps4/video_core'/relative;f=snapshot/source.name;shutil.copy2(source,f)
   cmd=next(shlex.split(l)for l in commands if ' -c 'in l and l.endswith('/'+relative));old='/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/bbport-mac-optimized/engines/metal/gpu';cmd=[x.replace(old,str(M))for x in cmd];cmd[1:1]=['-I'+str(snapshot),'-I'+str(inc),'-I'+str(P/'src')];obj=e/(source.name+'.o');old_obj=str(B/cmd[cmd.index('-o')+1]);assert link.count(old_obj)==1;link[link.index(old_obj)]=str(obj);cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(f);run(cmd,log);builds.append(dict(command=cmd,source_sha256=sha(f),object_sha256=sha(obj)))
  out=lib.with_name('libbbgpu.tiler-bounds-'+e.name.rsplit('-',1)[-1]+'.dylib');assert not out.exists();link[link.index('-o')+1]=str(out);run(link,log)
 def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['/usr/bin/otool','-L',str(f)],text=True).splitlines()[1:]]
 expected={Path(x).name:x for x in deps(lib)}
 for item in deps(out):
  replacement=expected.get(Path(item).name)
  if replacement and replacement!=item:subprocess.run(['/usr/bin/install_name_tool','-change',item,replacement,str(out)],check=True)
 subprocess.run(['/usr/bin/install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(out)],check=True);subprocess.run(['/usr/bin/codesign','--verify','--strict',str(out)],check=True)
 assert sha(lib)==base['sha256'];assert subprocess.check_output(['/usr/bin/lipo','-archs',str(out)],text=True).strip()=='arm64'
 r=dict(evidence=str(e),candidate=str(out),sha256=sha(out),baseline=str(lib),baseline_sha256=base['sha256'],generated=generated,builds=builds,link=link,default_enabled=False,changed_objects=2,formal_library_changed=False,public_TilingInfo_bytes=460,private_ParamsV1_bytes=652,memory_policy=MemoryPolicy.MANIFEST,before=before,after=observer.system(),source_hashes={str(f.relative_to(snapshot)):sha(f)for f in snapshot.rglob('*')if f.is_file()})
 (e/'build.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/tiler-bounds-candidate-current.json').write_text(json.dumps(r,indent=2)+'\n');print(e)
if __name__=='__main__':main()
