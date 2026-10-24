"""Private present-dump validation candidate; reuse the existing trigger/readback."""
from pathlib import Path
import hashlib,json,plistlib,shlex,shutil,subprocess,time
from qa_build_guard import require_no_games
P=Path(__file__).resolve().parents[1];M=P/'vendor/metal_api';B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build')
require_no_games();e=P/'evidence'/('present-image-candidate-'+str(time.time_ns()));e.mkdir()
base=json.loads((P/'evidence/first-upload-candidate-current.json').read_text());library=P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.first-upload-candidate.dylib'
def sha(f):return hashlib.file_digest(Path(f).open('rb'),'sha256').hexdigest()
assert sha(library)==base['sha256']
commands=subprocess.check_output(['ninja','-C',str(B),'-t','commands'],text=True).splitlines();rel='shadps4/video_core/renderer_metal/mtl_presenter.mm'
cmd=next(shlex.split(x)for x in commands if x.startswith(('/usr/bin/c++','/usr/bin/clang++'))and x.endswith('/'+rel))
original='/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/bbport-mac-optimized/engines/metal/gpu'
snapshot=e/'source/shadps4/video_core/renderer_metal';snapshot.mkdir(parents=True)
shutil.copy2(M/rel,snapshot/'mtl_presenter.mm');shutil.copy2(M/'shadps4/video_core/renderer_metal/mtl_present_dump.h',snapshot/'mtl_present_dump.h')
cmd=[x.replace(original,str(M))for x in cmd];cmd[1:1]=['-I'+str(e/'source/shadps4'),'-I'+str(P/'src')];obj=e/'presenter.o';cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(snapshot/'mtl_presenter.mm')
link=list(base['link']);slots=[i for i,x in enumerate(link)if x.endswith('/renderer_metal/mtl_presenter.mm.o')];assert len(slots)==1;link[slots[0]]=str(obj);out=library.with_name('libbbgpu.present-image-candidate.dylib');link[link.index('-o')+1]=str(out)
with(e/'build.log').open('w')as log:
 subprocess.run(cmd,cwd=B,stdout=log,stderr=subprocess.STDOUT,check=True);subprocess.run(link,cwd=B,stdout=log,stderr=subprocess.STDOUT,check=True)
def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['otool','-L',str(f)],text=True).splitlines()[1:]]
expected={Path(x).name:x for x in deps(library)}
for x in deps(out):
 replacement=expected.get(Path(x).name)
 if replacement and replacement!=x:subprocess.run(['install_name_tool','-change',x,replacement,str(out)],check=True)
subprocess.run(['install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['codesign','--force','--sign','-',str(out)],check=True);assert sha(library)==base['sha256']
r=dict(evidence=str(e),candidate=str(out),sha256=sha(out),baseline_sha256=base['sha256'],source_sha256=sha(M/rel),helper_sha256=sha(snapshot/'mtl_present_dump.h'),compile=cmd,link=link,changed_objects=1,diagnostic_only=True,normal_render_behavior_changed=False,formal_library_changed=False)
(e/'build.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/present-image-candidate-current.json').write_text(json.dumps(r,indent=2)+'\n')
meta=json.loads((P/'evidence/first-upload-gpu-app.json').read_text());app=P/'backend/bbport/bin'/('Bloodborne Metal Image QA '+str(time.time_ns())+'.app');shutil.copytree(Path(meta['target']).parents[2],app,symlinks=True);exe=app/'Contents/MacOS/bb-gpu'
subprocess.run(['install_name_tool','-change',str(library),str(out),str(exe)],check=True);info=app/'Contents/Info.plist';data=plistlib.loads(info.read_bytes());data['CFBundleIdentifier']='io.github.bbport.optimized.image-qa.'+str(time.time_ns());info.write_bytes(plistlib.dumps(data));subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
(P/'evidence/present-image-gpu-app.json').write_text(json.dumps(dict(target=str(exe),original_target=meta['original_target'],library=str(out),library_sha256=sha(out),diagnostic_only=True),indent=2)+'\n');print(e)
