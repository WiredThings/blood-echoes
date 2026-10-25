"""Body-only private PSO archive rebuild on an already coherent GPU object set.
No public header/ABI changes allowed; changes outside this exact whitelist fail.
"""
from pathlib import Path
import argparse,hashlib,json,subprocess,time,sys,os,signal
ap=argparse.ArgumentParser();ap.add_argument("--warm-stage",action="store_true");args=ap.parse_args()
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
require_no_games();M=P/'vendor/metal_api';B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build')
base=P/'evidence/owned-shader-gpu-build-1792918138731312000';old=json.loads((base/'inputs.json').read_text());accepted=json.loads((base/'result.json').read_text())
def sha(f):
 with Path(f).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
assert sha(accepted['candidate'])==accepted['sha256']=='a36c7bd3cdfad5924a50a6b165ec9940b16af032855b90c7c505f4f8a52b91ad'
changed={rel for rel,h in old['source_headers'].items()if sha(M/rel)!=h}
allowed={'shadps4/video_core/renderer_metal/mtl_pso_cache.mm'}
if args.warm_stage:allowed.add('shadps4/video_core/pipeline_cache_core_serialization.cpp')
assert changed==allowed,changed
all_sources={str(f.relative_to(M)):sha(f)for f in M.rglob('*')if f.is_file()and not f.is_symlink()and f.suffix in{'.h','.hpp','.inc','.cpp','.mm','.comp','.frag','.vert'}}
assert set(all_sources)-set(old['source_headers'])=={'shadps4/video_core/renderer_metal/mtl_warm_archive_identity.h'}
assert sum('mtl_warm_archive_identity.h'in (M/rel).read_text(errors='replace')for rel in old['source_headers']if rel.endswith(('.cpp','.mm')))==1
units=[u for u in old['units']if u['source']in {str(M/rel)for rel in allowed}];assert len(units)==len(allowed)
name='warm-stage'if args.warm_stage else 'warm-archive'
e=P/'evidence'/(name+'-gpu-build-'+str(time.time_ns()));e.mkdir();out=P/'backend/bbport/bin/gpu-metal-arm64'/('libbbgpu.'+name+'-'+e.name.rsplit('-',1)[-1]+'.dylib')
commands=[];link=list(old['link'])
for index,unit in enumerate(units):
 obj=e/(str(index)+'-'+Path(unit['source']).stem+'.o');cmd=list(unit['command']);cmd[cmd.index('-o')+1]=str(obj);cmd[cmd.index('-MF')+1]=str(obj.with_suffix('.d'));cmd[cmd.index('-MT')+1]=str(obj);commands.append(cmd)
 assert link.count(unit['object'])==1;link[link.index(unit['object'])]=str(obj)
link[link.index('-o')+1]=str(out)
old_objects={x:sha(x)for x in old['link']if x.endswith('.o')};assert len(old_objects)==230
record=dict(base_library=accepted['candidate'],base_sha256=accepted['sha256'],changed_source=list(changed),new_private_header='mtl_warm_archive_identity.h',public_headers_unchanged=True,compile=commands,link=link,source_headers=all_sources,reused_object_sha256=old_objects)
(e/'inputs.json').write_text(json.dumps(record,indent=2)+'\n')
observer=NativeObserver();before=observer.system();assert before['pressure']!=4 and not before['locked'];policy=MemoryPolicy(before);start=time.monotonic()
def run(command):
 with(e/'build.log').open('a')as log:
  proc=subprocess.Popen(command,cwd=B,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  try:
   while proc.poll()is None:
    reason=policy.observe(time.monotonic()-start,observer.system())
    if reason or time.monotonic()-start>600:raise RuntimeError(reason or 'build timeout')
    time.sleep(.3)
   assert proc.returncode==0,str(e/'build.log')
  finally:
   if proc.poll()is None:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
print(e,flush=True);
for cmd in commands:run(cmd)
run(link)
for rel,h in all_sources.items():assert sha(M/rel)==h,rel
for f,h in old_objects.items():assert sha(f)==h,f
subprocess.run(['install_name_tool','-id',str(out),str(out)],check=True)
def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['otool','-L',str(f)],text=True).splitlines()[1:]]
expected={Path(x).name:x for x in deps(accepted['candidate'])}
for x in deps(out):
 if Path(x).name in expected and x!=expected[Path(x).name]:subprocess.run(['install_name_tool','-change',x,expected[Path(x).name],str(out)],check=True)
subprocess.run(['codesign','--force','--sign','-',str(out)],check=True);subprocess.run(['codesign','--verify','--strict',str(out)],check=True);assert subprocess.check_output(['lipo','-archs',str(out)],text=True).strip()=='arm64'
result=dict(status='built-not-tested',candidate=str(out),sha256=sha(out),baseline=str(base),body_units_rebuilt=len(units),coherent_baseline_GPU_units=230,public_headers_unchanged=True,original_libraries_unchanged=sha(accepted['candidate'])==accepted['sha256'],before=before,after=observer.system(),seconds=time.monotonic()-start,memory_policy=MemoryPolicy.MANIFEST)
(e/'result.json').write_text(json.dumps(result,indent=2)+'\n');(P/('evidence/'+name+'-candidate-current.json')).write_text(json.dumps(result,indent=2)+'\n');print(out)
