"""Private present-dump validation candidate; reuse the existing trigger/readback."""
from pathlib import Path
import argparse,hashlib,json,plistlib,shlex,shutil,subprocess,time
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games
P=Path(__file__).resolve().parents[1];M=P/'vendor/metal_api';B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build')
ap=argparse.ArgumentParser();ap.add_argument('--range-audit',action='store_true');ap.add_argument('--shader-phases',action='store_true');ap.add_argument('--uniform-range',action='store_true');a=ap.parse_args()
require_no_games();e=P/'evidence'/('present-image-candidate-'+str(time.time_ns()));e.mkdir()
first=json.loads((P/'evidence/first-upload-candidate-current.json').read_text());base=first;library=P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.first-upload-candidate.dylib'
if a.range_audit:
 base=json.loads((P/'evidence/partial-audit-candidate-current.json').read_text());library=Path(base['candidate'])
 assert base['baseline_sha256']==first['sha256']and base['diagnostic_only']and not base['render_behavior_changed']
 assert 'full_range=1'in(Path(base['evidence'])/'texture_cache.cpp').read_text()
def sha(f):return hashlib.file_digest(Path(f).open('rb'),'sha256').hexdigest()
assert sha(library)==base['sha256']
if a.range_audit:assert sha(Path(base['evidence'])/'texture_cache.cpp')==base['source_fixed_sha256'],'range audit source changed since build'
observer=NativeObserver();initial=observer.system();assert not initial['locked']and initial['pressure']!=4
policy=MemoryPolicy(initial);started=time.monotonic()
def run(command,log,cwd=B):
 require_no_games();proc=subprocess.Popen(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
 try:
  while proc.poll()is None:
   reason=policy.observe(time.monotonic()-started,observer.system())
   if reason or time.monotonic()-started>180:raise RuntimeError('diagnostic build guard: '+str(reason or 'time budget'))
   time.sleep(.25)
  if proc.returncode:raise RuntimeError('diagnostic build failed: '+str(e/'build.log'))
 finally:
  if proc.poll()is None:
   proc.terminate()
   try:proc.wait(timeout=3)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
commands=subprocess.check_output(['ninja','-C',str(B),'-t','commands'],text=True).splitlines();rel='shadps4/video_core/renderer_metal/mtl_presenter.mm'
cmd=next(shlex.split(x)for x in commands if x.startswith(('/usr/bin/c++','/usr/bin/clang++'))and x.endswith('/'+rel))
original='/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/bbport-mac-optimized/engines/metal/gpu'
snapshot=e/'source/shadps4/video_core/renderer_metal';snapshot.mkdir(parents=True)
shutil.copy2(M/rel,snapshot/'mtl_presenter.mm');shutil.copy2(M/'shadps4/video_core/renderer_metal/mtl_present_dump.h',snapshot/'mtl_present_dump.h')
if a.range_audit:shutil.copy2(Path(base['evidence'])/'texture_cache.cpp',e/'range-audit-source.cpp')
cmd=[x.replace(original,str(M))for x in cmd];cmd[1:1]=['-I'+str(e/'source/shadps4'),'-I'+str(P/'src')];obj=e/'presenter.o';cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[-1]=str(snapshot/'mtl_presenter.mm')
link=list(base['link']);slots=[i for i,x in enumerate(link)if x.endswith('/renderer_metal/mtl_presenter.mm.o')];assert len(slots)==1;link[slots[0]]=str(obj);out=library.with_name('libbbgpu.present-image'+('-phase-'+e.name.rsplit('-',1)[-1]if a.shader_phases else '-range-'+e.name.rsplit('-',1)[-1]if a.range_audit else '-candidate')+'.dylib');link[link.index('-o')+1]=str(out)
phase_compile=None;uniform_compile=None
with(e/'build.log').open('w')as log:
 run(cmd,log)
 if a.shader_phases:
  rel_phase='shadps4/video_core/pipeline_cache_core.cpp';source_phase=e/'pipeline_cache_core.cpp';shutil.copy2(M/rel_phase,source_phase)
  for name in['bb_shader_phase_audit.h','bb_stutter_audit.h']:shutil.copy2(P/'src'/name,e/name)
  phase_compile=next(shlex.split(x)for x in commands if x.startswith(('/usr/bin/c++','/usr/bin/clang++'))and x.endswith('/'+rel_phase))
  phase_compile=[x.replace(original,str(M))for x in phase_compile];phase_compile[1:1]=['-I'+str(e),'-I'+str(M/'shadps4/video_core'),'-I'+str(P/'src')]
  phase_obj=e/'pipeline_cache_core.o';phase_compile[phase_compile.index('-o')+1]=str(phase_obj);phase_compile[phase_compile.index('-MF')+1]=str(phase_obj.with_suffix('.d'));phase_compile[-1]=str(source_phase)
  phase_slots=[i for i,x in enumerate(link)if x.endswith(('/pipeline_cache_core.cpp.o','/native-pipeline_cache_core.o'))];assert len(phase_slots)==1
  link[phase_slots[0]]=str(phase_obj);run(phase_compile,log)
 if a.uniform_range:
  rel_uniform='shadps4/video_core/renderer_metal/mtl_shader.mm';uniform_source=snapshot/'mtl_shader.mm';shutil.copy2(M/rel_uniform,uniform_source);shutil.copy2(M/'shadps4/video_core/renderer_metal/mtl_fragment_uniforms.h',snapshot/'mtl_fragment_uniforms.h')
  uniform_compile=next(shlex.split(x)for x in commands if x.startswith(('/usr/bin/c++','/usr/bin/clang++'))and x.endswith('/'+rel_uniform))
  uniform_compile=[x.replace(original,str(M))for x in uniform_compile];uniform_compile[1:1]=['-I'+str(e/'source/shadps4'),'-I'+str(P/'src')]
  uniform_obj=e/'mtl_shader.o';uniform_compile[uniform_compile.index('-o')+1]=str(uniform_obj);uniform_compile[uniform_compile.index('-MF')+1]=str(uniform_obj.with_suffix('.d'));uniform_compile[-1]=str(uniform_source)
  uniform_slots=[i for i,x in enumerate(link)if x.endswith(('/renderer_metal/mtl_shader.mm.o','/native-mtl_shader.o'))];assert len(uniform_slots)==1
  link[uniform_slots[0]]=str(uniform_obj);run(uniform_compile,log)
 run(link,log)
def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['otool','-L',str(f)],text=True).splitlines()[1:]]
expected={Path(x).name:x for x in deps(library)}
for x in deps(out):
 replacement=expected.get(Path(x).name)
 if replacement and replacement!=x:subprocess.run(['install_name_tool','-change',x,replacement,str(out)],check=True)
subprocess.run(['install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['codesign','--force','--sign','-',str(out)],check=True);assert sha(library)==base['sha256']
r=dict(evidence=str(e),candidate=str(out),sha256=sha(out),baseline_sha256=base['sha256'],source_sha256=sha(M/rel),helper_sha256=sha(snapshot/'mtl_present_dump.h'),compile=cmd,link=link,changed_objects=1+int(a.range_audit)+int(a.shader_phases)+int(a.uniform_range),uniform_range=a.uniform_range,uniform_compile=uniform_compile,uniform_source_sha256=sha(snapshot/'mtl_shader.mm')if a.uniform_range else None,uniform_header_sha256=sha(snapshot/'mtl_fragment_uniforms.h')if a.uniform_range else None,shader_phases=a.shader_phases,phase_compile=phase_compile,phase_source_sha256=sha(e/'pipeline_cache_core.cpp')if a.shader_phases else None,phase_header_sha256=sha(e/'bb_shader_phase_audit.h')if a.shader_phases else None,range_audit=a.range_audit,range_source_sha256=sha(e/'range-audit-source.cpp')if a.range_audit else None,first_upload_baseline_sha256=first['sha256'],memory_policy=MemoryPolicy.MANIFEST,before=initial,after=observer.system(),diagnostic_only=True,normal_render_behavior_changed=False,formal_library_changed=False)
(e/'build.json').write_text(json.dumps(r,indent=2)+'\n');(P/'evidence/present-image-candidate-current.json').write_text(json.dumps(r,indent=2)+'\n')
meta=json.loads((P/'evidence/first-upload-gpu-app.json').read_text())
from gpu_candidate_identity import verify
verify(meta,P/'backend')
app=P/'backend/bbport/bin'/('Bloodborne Metal Image QA '+str(time.time_ns())+'.app');shutil.copytree(Path(meta['target']).parents[2],app,symlinks=True);exe=app/'Contents/MacOS/bb-gpu'
assert str(Path(meta['library']))in deps(exe),'copied child did not reference expected source library'
subprocess.run(['install_name_tool','-change',meta['library'],str(out),str(exe)],check=True);info=app/'Contents/Info.plist';data=plistlib.loads(info.read_bytes());data['CFBundleIdentifier']='io.github.bbport.optimized.image-qa.'+str(time.time_ns());info.write_bytes(plistlib.dumps(data));subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
from gpu_candidate_identity import verify
child=dict(target=str(exe),original_target=meta['original_target'],library=str(out),library_sha256=sha(out),range_audit=a.range_audit,shader_phases=a.shader_phases,uniform_range=a.uniform_range,diagnostic_only=True)
child['load_identity']=verify(child,P/'backend')
(P/'evidence/present-image-gpu-app.json').write_text(json.dumps(child,indent=2)+'\n');print(e)
