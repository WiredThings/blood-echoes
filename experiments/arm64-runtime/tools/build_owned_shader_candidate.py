"""Full private GPU rebuild on the prepared dependency stage; optional positional resume directory.
No current game caches or normal runtime libraries are changed.
"""
from pathlib import Path
import subprocess,shlex,json,time,hashlib,sys,os,signal
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
B=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build');M=P/'vendor/metal_api';R='/Users/k.d./Documents/Codex/2026-10-16/https-github-com-loreanxavier-pt-pc/work/bbport-mac-optimized/engines/metal/gpu'
require_no_games();e=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else P/'evidence'/('owned-shader-gpu-build-'+str(time.time_ns()));assert e.is_relative_to(P/'evidence');assert not (e/'result.json').exists(),'Do not replace a completed candidate';e.mkdir(exist_ok=len(sys.argv)>1);(P/'evidence/owned-shader-gpu-build-current.json').write_text(json.dumps(dict(evidence=str(e),status='building'))+'\n')
def sha(f):return hashlib.file_digest(Path(f).open('rb'),'sha256').hexdigest()
sources={str(f.relative_to(M)):sha(f) for f in M.rglob('*')if f.is_file()and not f.is_symlink() and f.suffix in {'.h','.hpp','.inc','.cpp','.mm','.comp','.frag','.vert'}}
meta=json.loads((P/'evidence/tiler-bounds-candidate-current.json').read_text());base=Path(meta['candidate']);assert sha(base)==meta['sha256'];inc=Path(meta['generated']['include']);assert inc.is_dir()
commands=[shlex.split(x)for x in subprocess.check_output(['ninja','-C',str(B),'-t','commands','bbgpu'],text=True).splitlines()if ' -c 'in x and ' -o CMakeFiles/bbgpu.dir/'in x];oldobjects=[x for x in meta['link'] if x.endswith('.o')];assert len(commands)==len(oldobjects)==229
link=list(meta['link']);units=[];new=[];rules=[]
for n,c0 in enumerate(commands+[next(list(c)for c in commands if c[-1].endswith('/pipeline_cache_core.cpp'))]):
 c=[x.replace(R,str(M))for x in c0];source=Path(c[-1])
 if n==len(commands):source=M/'shadps4/shader_recompiler/compile_snapshot.cpp';c[-1]=str(source)
 assert source.is_file()and source.is_relative_to(M)
 obj=e/('unit-'+str(n)+'.o');dep=obj.with_suffix('.d');c[c.index('-o')+1]=str(obj);c[c.index('-MF')+1]=str(dep);c[c.index('-MT')+1]=str(obj);c[1:1]=['-I'+str(inc),'-I'+str(P/'src')]
 if n<len(commands):
  old=oldobjects[n];original=str(B/c0[c0.index('-o')+1]);assert original==old or (source.stem in Path(old).name), (source,old)
  assert link.count(old)==1;link[link.index(old)]=str(obj)
 else:link.insert(link.index('-o'),str(obj))
 name='compile_'+str(n);rules.extend([f'rule {name}', '  command = '+shlex.join(c).replace('$','$$'), '  depfile = '+str(dep), '  deps = gcc',f'build {obj}: {name} {source}'])
 units.append(dict(source=str(source),source_sha256=sha(source),object=str(obj),command=c));new.append(str(obj))
out=P/'backend/bbport/bin/gpu-metal-arm64'/('libbbgpu.owned-shader-'+e.name.rsplit('-',1)[-1]+'.dylib');link[link.index('-o')+1]=str(out)
(e/'build.ninja').write_text('\n'.join(rules)+ '\nbuild objects: phony '+' '.join(new)+'\ndefault objects\n')

if (e/'inputs.json').exists():(e/('inputs-previous-'+str(time.time_ns())+'.json')).write_bytes((e/'inputs.json').read_bytes())
(e/'inputs.json').write_text(json.dumps(dict(base=str(base),base_sha256=meta['sha256'],units=units,link=link,source_headers=sources,source_root=str(M),generated_include=str(inc)),indent=2)+'\n')
observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4;policy=MemoryPolicy(before);started=time.monotonic()
def run(cmd):
 with (e/'build.log').open('a')as log:
  proc=subprocess.Popen(cmd,cwd=B,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  try:
   while proc.poll()is None:
    why=policy.observe(time.monotonic()-started,observer.system())
    if why or time.monotonic()-started>1200:raise RuntimeError(why or 'build timeout')
    time.sleep(.5)
   if proc.returncode:raise RuntimeError('build failed: '+str(e/'build.log')+'\n'+'\n'.join(x[:350] for x in (e/'build.log').read_text().splitlines()[-18:]))
  finally:
   if proc.poll()is None:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
try:
 print('Evidence:',e,flush=True);run(['ninja','-f',str(e/'build.ninja'),'-j1']);run(link)
 for name,digest in sources.items():assert sha(M/name)==digest,name
 def deps(f):return[x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['otool','-L',str(f)],text=True).splitlines()[1:]]
 expected={Path(x).name:x for x in deps(base)}
 for x in deps(out):
  if Path(x).name in expected and expected[Path(x).name]!=x:subprocess.run(['install_name_tool','-change',x,expected[Path(x).name],str(out)],check=True)
 subprocess.run(['install_name_tool','-id',str(out),str(out)],check=True);subprocess.run(['codesign','--force','--sign','-',str(out)],check=True);subprocess.run(['codesign','--verify','--strict',str(out)],check=True);assert subprocess.check_output(['lipo','-archs',str(out)],text=True).strip()=='arm64';assert sha(base)==meta['sha256']
 result=dict(status='built-not-tested',evidence=str(e),candidate=str(out),sha256=sha(out),source_root=str(M),all_GPU_units_rebuilt=230,original_library_unchanged=True,before=before,after=observer.system(),memory_policy=MemoryPolicy.MANIFEST,seconds=time.monotonic()-started)
 (e/'result.json').write_text(json.dumps(result,indent=2)+'\n');(P/'evidence/owned-shader-gpu-build-current.json').write_text(json.dumps(result,indent=2)+'\n');print(out,flush=True)
except BaseException as ex:
 (e/'failure.json').write_text(json.dumps(dict(error=str(ex),seconds=time.monotonic()-started),indent=2)+'\n');raise
