"""Coherent GPU/module/PSO build from one owned source snapshot.

Uses an explicitly hashed prepared dependency plan, not a clean-checkout claim.
--plan performs no compilation. --build-plan verifies and freezes that exact
source closure, builds all GPU TUs at -j1, then links only main+production host
services to the resulting same DSO. No targeted translator overrides.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,shlex,shutil,signal,stat,subprocess,sys,time

D=Path(__file__).resolve().parents[1]
BASE=D/'evidence/resource-writeback-integration/gpu-build-inputs.json'
BASE_SHA='ff0bd17871cc325d40245b8ec72d3381363cd7fb7b4627f69c0a84fd68bd5e6e'
NATIVE=D/'evidence/tiling-library-integration-20261026/native/build.json'
CAPTURE=('fetch_compile_observer.cpp','fetch_compile_producer.cpp','fetch_compile_canonical.cpp','fetch_companion_store.cpp','fetch_register_store.cpp')
ROOTS=('vendor/metal_api','src','vendor/bb_runtime/src')
MAX_SOURCE=512*1024*1024

def need(ok,why):
 if not ok:raise ValueError(why)
def sha(path):
 with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def directory(path):
 p=Path(path).absolute();s=p.lstat();need(p==p.resolve(strict=True)and stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned canonical build directory');return p
def duplicate(path):
 # Existing Finder-style copies are neither compiled nor removed.
 match=re.match(r'^(.*) 2(\.[^.]*)?$',path.name)
 return bool(match and(path.with_name(match[1]+(match[2]or''))).exists())
def source_closure(root):
 root=directory(root);result={};excluded=[];total=0
 for name in ROOTS:
  for parent,dirs,files in os.walk(root/name,followlinks=False):
   for name in list(dirs):
    p=Path(parent)/name
    if p.is_symlink()or name in('.git','__pycache__','build')or duplicate(p):dirs.remove(name);excluded.append(str(p.relative_to(root)))
   for name in sorted(files):
    p=Path(parent)/name
    if p.is_symlink()or duplicate(p)or p.suffix in('.o','.d','.pyc'):
     excluded.append(str(p.relative_to(root)));continue
    s=p.stat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_size<=64*1024*1024,'bounded owned source file');total+=s.st_size;need(total<=MAX_SOURCE and len(result)<20000,'bounded source closure')
    result[str(p.relative_to(root))]=dict(sha256=sha(p),bytes=s.st_size)
 return dict(files=result,bytes=total,excluded=sorted(excluded))
def write(path,value):
 with Path(path).open('x')as stream:json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n')
def external_headers(commands,snapshot):
 """Hash explicit prepared/generated/Homebrew includes before compilation.
 Implicit SDK headers are additionally recorded from compiler depfiles later.
 """
 paths=set()
 for command in commands:
  for i,value in enumerate(command):
   candidate=value[2:]if value.startswith('-I')and len(value)>2 else command[i+1]if value in('-isystem','-include')else None
   if not candidate:continue
   p=Path(candidate)
   if p.is_absolute()and not p.is_relative_to(snapshot):paths.add(p)
 result={};total=0
 for path in sorted(paths):
  need(path.exists(),'prepared include unavailable: '+str(path))
  files=[path]if path.is_file()else path.rglob('*')
  for p in files:
   if not p.is_file():continue
   # Third-party include symlinks are dependency references, never source
   # snapshots or accepted module roots; bind their canonical actual bytes.
   actual=p.resolve(strict=True);size=actual.stat().st_size;total+=size
   need(size<=32*1024*1024 and total<=512*1024*1024 and len(result)<40000,'prepared header closure bound')
   result[str(p)]=dict(canonical=str(actual),sha256=sha(actual),bytes=size)
 return result
def rewrite(command,oldroot,snapshot):
 result=[]
 for item in command:
  if item.startswith('-DBB_FONT_PATH='):result.append(item);continue
  for old in(oldroot,str(D),'/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype'):
   # Remap source paths only, never prepared libraries/state/toolchain roots.
   for name in ROOTS:item=item.replace(old+'/'+name,str(snapshot/name))
  result.append(item)
 return result

def make_plan(*,project,output,baseline=BASE,baseline_sha=BASE_SHA,native_baseline=NATIVE,source_root=D):
 project=directory(project);output=Path(output).absolute();need(output.is_relative_to(project/'evidence')and not output.exists(),'new isolated build evidence required');directory(output.parent)
 need(sha(baseline)==baseline_sha,'prepared GPU baseline SHA');base=json.loads(Path(baseline).read_text());native=json.loads(Path(native_baseline).read_text());need(len(base['units'])==230,'reviewed 229+Snapshot baseline')
 output.mkdir(mode=0o700);snapshot=output/'source';objects=output/'objects';closure=source_closure(source_root);oldroot=base['units'][0]['source'].split('/vendor/metal_api/')[0]
 sources=[];mapping={};units=[]
 for i,unit in enumerate(base['units']):
  relative=Path(unit['source']).relative_to(oldroot);need(str(relative)in closure['files']and not duplicate(Path(source_root)/relative),'current source for every prepared GPU TU')
  command=rewrite(unit['command'],oldroot,snapshot);source=snapshot/relative;obj=objects/f'gpu-{i:03d}.o';dep=obj.with_suffix('.d')
  oldobj=unit['command'][unit['command'].index('-o')+1]
  for flag,value in(('-o',obj),('-MT',obj),('-MF',dep)):command[command.index(flag)+1]=str(value)
  command[-1]=str(source);command[1:1]=['-DBB_DEV_FETCH_CAPTURE=1','-I'+str(snapshot/'src')]
  units.append(dict(source=str(source),relative_source=str(relative),command=command,object=str(obj),depfile=str(dep)));mapping[oldobj]=str(obj);sources.append(str(relative))
 template=next(u for u in units if u['relative_source'].endswith('/pipeline_cache_core.cpp'))
 for name in CAPTURE:
  relative='src/'+name;need(relative in closure['files'],'capture producer source missing');command=template['command'][:];obj=objects/('capture-'+name+'.o');dep=obj.with_suffix('.d')
  for flag,value in(('-o',obj),('-MT',obj),('-MF',dep)):command[command.index(flag)+1]=str(value)
  command[-1]=str(snapshot/relative);units.append(dict(source=str(snapshot/relative),relative_source=relative,command=command,object=str(obj),depfile=str(dep)));sources.append(relative)
 need(len(set(sources))==235,'unique complete GPU source set')
 library=output/'libbbgpu.game-preparation.dylib';link=[]
 for value in base['link']:link.append(mapping.get(value,value))
 need(sum(1 for v in link if v.endswith('.o'))==230 and all(v in mapping.values()for v in link if v.endswith('.o')),'all baseline GPU objects must be rebuilt')
 link[link.index('-o')+1]=str(library);link[link.index('-o'):link.index('-o')]=[u['object']for u in units[230:]]
 native_template=native['units'][0]['command'];native_root=native['source_root'];mains=[]
 for name,relative in(('host','src/game_pso_host_services.cpp'),('worker','src/game_pso_preparer.mm'),('module','src/game_module_preparer.mm')):
  c=rewrite(native_template,native_root,snapshot);obj=objects/(name+'.o');c[c.index('-o')+1]=str(obj);c[-1]=str(snapshot/relative)
  c[1:1]=['-DBB_DEV_FETCH_CAPTURE=1','-I'+str(snapshot/'src'),'-I'+str(snapshot/'vendor/bb_runtime/src')]
  if name=='module':c.insert(1,'-DBB_WITH_OWNED_COMPANION_CONSUMER=1')
  mains.append(dict(name=name,source=str(snapshot/relative),relative_source=relative,object=str(obj),command=c))
 binaries={}
 for name in('worker','module'):
  old=native['binaries']['worker']['link'];oldobjects=[x for x in old if x.endswith('.o')];need(len(oldobjects)==2,'main plus real host-only services baseline')
  command=[]
  for value in old:
   if value==oldobjects[0]:command.append(str(objects/(name+'.o')))
   elif value==oldobjects[1]:command.append(str(objects/'host.o'))
   elif Path(value).name.startswith('libbbgpu')and value.endswith('.dylib'):command.append(str(library))
   else:command.append(value)
  binary=output/('game-pso-preparer'if name=='worker'else'game-module-preparer');command[command.index('-o')+1]=str(binary)
  need([v for v in command if v.endswith('.o')]==[str(objects/(name+'.o')),str(objects/'host.o')],'no targeted compiler objects in executable')
  binaries[name]=dict(binary=str(binary),link=command)
 external={}
 for value in link+sum((b['link']for b in binaries.values()),[]):
  p=Path(value)
  if p.is_absolute()and p.is_file()and value not in{str(library),str(output)}and not value.endswith('.o'):
   external[value]=dict(sha256=sha(p),bytes=p.stat().st_size)
 prepared=Path('/private/tmp/blood-echoes-arm64-metal-20261023/build');need(prepared.is_dir(),'reviewed prepared dependency stage required')
 deps=external_headers([u['command']for u in units+mains],snapshot)
 runtime_lib=Path(native['library']);need(sha(runtime_lib)==native['library_sha256'],'prepared runtime dependency edge source changed')
 plan=dict(schema=1,kind='coherent-game-preparation-plan',project=str(project),output=str(output),source_root=str(Path(source_root).resolve()),snapshot=str(snapshot),source_closure=closure,source_identity=digest(closure['files']),gpu_units=units,all_GPU_units_rebuilt=235,capture_units=list(CAPTURE),native_units=mains,binaries=binaries,library=str(library),gpu_link=link,prepared_cwd=str(prepared),external_link_inputs=external,external_header_inputs=deps,dependency_edge_source=dict(path=str(runtime_lib),sha256=sha(runtime_lib)),baseline=dict(path=str(baseline),sha256=baseline_sha,native=str(native_baseline),native_sha256=sha(native_baseline)),clean_checkout_build_proven=False,prepared_dependencies_reused=True,game_started=False,GPU_started=False,coherent_renderer_built=False)
 write(output/'plan.json',plan);return plan

def freeze_sources(plan):
 root=Path(plan['source_root']);snapshot=Path(plan['snapshot'])
 if snapshot.exists():
  directory(snapshot);current=source_closure(snapshot)
  need(current['files']==plan['source_closure']['files']and not current['excluded'],'existing immutable snapshot changed')
  return
 need(source_closure(root)==plan['source_closure'],'source changed after plan; regenerate after producers freeze')
 snapshot.mkdir(mode=0o700)
 for relative,ref in plan['source_closure']['files'].items():
  source=root/relative;dest=snapshot/relative;dest.parent.mkdir(parents=True,exist_ok=True);need(sha(source)==ref['sha256'],'source changed during snapshot');shutil.copyfile(source,dest);need(sha(dest)==ref['sha256'],'snapshot bytes changed');dest.chmod(0o444)
 need(source_closure(root)==plan['source_closure'],'source changed during snapshot')

def build(plan_path):
 plan_path=Path(plan_path).absolute();need(plan_path==plan_path.resolve(strict=True)and plan_path.stat().st_uid==os.getuid()and plan_path.stat().st_size<=32*1024*1024,'owned bounded canonical build plan')
 plan=json.loads(plan_path.read_text());need(plan.get('schema')==1 and plan.get('kind')=='coherent-game-preparation-plan','reviewed coherent build plan');project=directory(plan['project']);output=directory(plan['output']);need(output.is_relative_to(project/'evidence')and plan_path==output/'plan.json','plan/output remain in owned isolated evidence');need(not(output/'result.json').exists(),'completed build immutable')
 snapshot=Path(plan['snapshot']);need(snapshot==output/'source'and Path(plan['library']).parent==output,'owned snapshot/library placement')
 for unit in plan['gpu_units']+plan['native_units']:
  command=unit['command'];need(command.count('-o')==1 and command[command.index('-o')+1]==unit['object']and Path(unit['object']).parent==output/'objects'and Path(unit['source']).is_relative_to(snapshot),'all compile outputs remain in owned build')
 for binary in plan['binaries'].values():need(Path(binary['binary']).parent==output and binary['link'].count('-o')==1 and binary['link'][binary['link'].index('-o')+1]==binary['binary'],'owned binary placement')
 need(plan['gpu_link'].count('-o')==1 and plan['gpu_link'][plan['gpu_link'].index('-o')+1]==plan['library'],'owned GPU link placement')
 for path,ref in plan['external_link_inputs'].items():need(sha(path)==ref['sha256'],'prepared dependency changed')
 for path,ref in plan['external_header_inputs'].items():need(str(Path(path).resolve(strict=True))==ref['canonical']and sha(path)==ref['sha256'],'prepared header changed')
 freeze_sources(plan);(output/'objects').mkdir(mode=0o700,exist_ok=True);directory(output/'objects');rules=[]
 for i,u in enumerate(plan['gpu_units']):
  rules +=[f'rule compile_{i}','  command = '+shlex.join(u['command']).replace('$','$$'),'  depfile = '+u['depfile'],'  deps = gcc',f'build {u["object"]}: compile_{i} {u["source"]}']
 rules+=['build objects: phony '+' '.join(u['object']for u in plan['gpu_units']),'default objects'];(output/'build.ninja').write_text('\n'.join(rules)+'\n')
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe coherent build entry');policy=MemoryPolicy(before);began=time.monotonic()
 def run(command):
  with(output/'build.log').open('a')as log:
   p=subprocess.Popen(command,cwd=plan['prepared_cwd'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   try:
    while p.poll()is None:
     reason=policy.observe(time.monotonic()-began,observer.system());need(not reason and time.monotonic()-began<1800,reason or'coherent build timeout');time.sleep(.25)
    need(p.returncode==0,'coherent command failed: '+str(output/'build.log'))
   finally:
    if p.poll()is None:
     os.killpg(p.pid,signal.SIGTERM)
     try:p.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
 try:
  run(['ninja','-d','keepdepfile','-f',str(output/'build.ninja'),'-j1']);run(plan['gpu_link'])
  for unit in plan['native_units']:run(unit['command'])
  for binary in plan['binaries'].values():run(binary['link'])
  library=Path(plan['library']);targets=[library]+[Path(b['binary'])for b in plan['binaries'].values()]
  def deps(path):return [x.strip().split(' (compatibility')[0]for x in subprocess.check_output(['/usr/bin/otool','-L',str(path)],text=True).splitlines()[1:]]
  # Normalize prepared dylib edges to actual canonical absolute paths; the GPU
  # DSO and both executables retain one shared load edge, never overrides.
  original_parent=Path(plan['dependency_edge_source']['path']).parent
  need(sha(plan['dependency_edge_source']['path'])==plan['dependency_edge_source']['sha256'],'original runtime edge source changed')
  expected={Path(edge).name:str((original_parent/edge.split('/',1)[1]).resolve())if edge.startswith('@loader_path/')else edge for edge in deps(Path(plan['dependency_edge_source']['path']))}
  for target in targets:
   for edge in deps(target):
    replacement=str(library)if Path(edge).name.startswith('libbbgpu')else expected.get(Path(edge).name)
    if replacement and replacement!=edge:run(['/usr/bin/install_name_tool','-change',edge,replacement,str(target)])
   if target==library:run(['/usr/bin/install_name_tool','-id',str(library),str(library)])
   run(['/usr/bin/codesign','--force','--sign','-',str(target)]);run(['/usr/bin/codesign','--verify','--strict',str(target)])
   need(subprocess.check_output(['/usr/bin/lipo','-archs',str(target)],text=True).strip()=='arm64','coherent ARM64 outputs')
  for relative,ref in plan['source_closure']['files'].items():need(sha(Path(plan['snapshot'])/relative)==ref['sha256'],'immutable snapshot changed during build')
  for path,ref in plan['external_link_inputs'].items():need(sha(path)==ref['sha256'],'prepared link dependency changed during build')
  for path,ref in plan['external_header_inputs'].items():need(str(Path(path).resolve(strict=True))==ref['canonical']and sha(path)==ref['sha256'],'prepared header changed during build')
  exports=subprocess.check_output(['/usr/bin/nm','-gU',str(library)],text=True)
  need('_bb_fetch_companion_configure_v1'in exports,'actual automatic capture export required')
  from warm_archive_manifest import resolve_gpu_identity
  identities={name:resolve_gpu_identity(Path(value['binary']),output)for name,value in plan['binaries'].items()}
  need(all(value['library']==str(library)and value['library_sha256']==sha(library)for value in identities.values()),'actual same DSO load edges')
  headers={}
  for unit in plan['gpu_units']:
   text=Path(unit['depfile']).read_text().replace('\\\n',' ')
   for value in shlex.split(text.split(':',1)[1]):
    p=Path(value)
    if p.is_file():headers[str(p.resolve())]=sha(p)
  result=dict(schema=1,status='coherent-built-not-game-tested',source_root=plan['snapshot'],source_identity=plan['source_identity'],source_closure=plan['source_closure'],compiled_header_sha256=headers,library=str(library),library_sha256=sha(library),worker=plan['binaries']['worker']['binary'],worker_sha256=sha(plan['binaries']['worker']['binary']),module_consumer=plan['binaries']['module']['binary'],module_consumer_sha256=sha(plan['binaries']['module']['binary']),automatic_capture_schema=1,all_GPU_units_rebuilt=len(plan['gpu_units']),native_units=plan['native_units'],binaries={name:dict(value,sha256=sha(value['binary']),dependencies=deps(value['binary']),actual_load_edge=identities[name])for name,value in plan['binaries'].items()},coherent_renderer=True,diagnostic_only=False,targeted_compiler_overrides=False,host_services='production host-only; unsupported guest/runtime calls explicitly fenced',prepared_dependencies_reused=True,clean_checkout_build_proven=False,before=before,after=observer.system(),seconds=time.monotonic()-began,GPU_started=False,game_started=False,plan_sha256=sha(plan_path))
  write(output/'result.json',result)
  # Complete the component using the existing native offsetof/sizeof emitter,
  # compiled against this same frozen header snapshot. Not a second GPU build.
  from build_metadata_layout import build_from_plan
  component=build_from_plan(plan_path)
  return dict(result,component_manifest=component['manifest'],component_manifest_sha256=component['sha256'])
 finally:observer.close()

def repair_main(base_result,output):
 """New native-main artifact, same coherent DSO and exact compiled headers.
 No completed result/snapshot is changed; no GPU/compiler TU is overridden.
 """
 base_result=Path(base_result).resolve(strict=True);base=json.loads(base_result.read_text());plan=json.loads((base_result.parent/'plan.json').read_text());project=directory(plan['project']);output=Path(output).absolute();need(output.is_relative_to(project/'evidence')and not output.exists(),'fresh owned native repair root');directory(output.parent)
 need(base.get('coherent_renderer')is True and base.get('targeted_compiler_overrides')is False,'coherent base required');library=Path(base['library']);need(sha(library)==base['library_sha256'],'unchanged coherent DSO')
 for unit in plan['gpu_units']:
  need(sha(D/unit['relative_source'])==plan['source_closure']['files'][unit['relative_source']]['sha256'],'GPU source changed; full coherent build required')
 for path,value in base['compiled_header_sha256'].items():need(sha(path)==value,'coherent compiled dependency changed')
 output.mkdir(mode=0o700);template=next(u for u in plan['native_units']if u['name']=='module');source=D/'src/game_module_preparer.mm';main_sha=sha(source);command=template['command'][:];command[command.index('-o')+1]=str(output/'module.o');command[-1]=str(source);command[1:1]=['-MMD','-MF',str(output/'module.d')]
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe native repair entry');policy=MemoryPolicy(before);started=time.monotonic()
 def run(command):
  with(output/'build.log').open('a')as log:
   process=subprocess.Popen(command,cwd=plan['prepared_cwd'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   try:
    while process.poll()is None:
     reason=policy.observe(time.monotonic()-started,observer.system());need(not reason and time.monotonic()-started<180,reason or'bounded native repair timeout');time.sleep(.1)
    need(process.returncode==0,'native main repair failed; '+str(output/'build.log'))
   finally:
    if process.poll()is None:
     os.killpg(process.pid,signal.SIGTERM)
     try:process.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
 try:
  run(command);link=base['binaries']['module']['link'][:];need(link.count(template['object'])==1,'one native main replacement only');link[link.index(template['object'])]=str(output/'module.o');binary=output/'game-module-preparer';link[link.index('-o')+1]=str(binary);run(link)
  deps=lambda path:[s.strip().split(' (compatibility')[0]for s in subprocess.check_output(['/usr/bin/otool','-L',str(path)],text=True).splitlines()[1:]]
  expected={Path(edge).name:edge for edge in base['binaries']['module']['dependencies']}
  for edge in deps(binary):
   target=str(library)if Path(edge).name.startswith('libbbgpu')else expected.get(Path(edge).name)
   if target and target!=edge:run(['/usr/bin/install_name_tool','-change',edge,target,str(binary)])
  run(['/usr/bin/codesign','--force','--sign','-',str(binary)]);run(['/usr/bin/codesign','--verify','--strict',str(binary)])
  from warm_archive_manifest import resolve_gpu_identity
  identity=resolve_gpu_identity(binary,project/'evidence');need(identity['library_sha256']==base['library_sha256'],'same actual coherent DSO')
  headers={};text=(output/'module.d').read_text().replace('\\\n',' ')
  for name in shlex.split(text.split(':',1)[1]):
   path=Path(name)
   if not path.is_file():continue
   value=sha(path);headers[str(path.resolve())]=value
   if path!=source and path.is_relative_to(D):
    relative=str(path.relative_to(D));need(relative in plan['source_closure']['files']and plan['source_closure']['files'][relative]['sha256']==value,'native current header differs from coherent snapshot')
  need(sha(source)==main_sha,'native source changed during repair');record=dict(schema=1,kind='coherent-game-module-main-repair',status='native-main-repaired-not-tested',base_result=str(base_result),base_result_sha256=sha(base_result),source_identity=base['source_identity'],source_root=base['source_root'],library=str(library),library_sha256=base['library_sha256'],module_consumer=str(binary),module_consumer_sha256=sha(binary),main_source=str(source),main_source_sha256=main_sha,compiled_header_sha256=headers,command=command,link=link,actual_load_edge=identity,dependencies=deps(binary),coherent_renderer=True,diagnostic_only=False,targeted_compiler_overrides=False,all_GPU_units_rebuilt_this_repair=0,unchanged_GPU_units_from_base=235,shared_abi_headers_unchanged=True,original_result_modified=False,before=before,after=observer.system(),GPU_started=False,game_started=False)
  write(output/'repair.json',record);return record
 finally:observer.close()

def main():
 ap=argparse.ArgumentParser(description=__doc__);group=ap.add_mutually_exclusive_group(required=True);group.add_argument('--plan',action='store_true');group.add_argument('--build-plan',type=Path);group.add_argument('--repair-native-main',type=Path)
 ap.add_argument('--project',type=Path);ap.add_argument('--output',type=Path);ap.add_argument('--source-root',type=Path,default=D);ap.add_argument('--baseline',type=Path,default=BASE);ap.add_argument('--baseline-sha256',default=BASE_SHA);ap.add_argument('--native-baseline',type=Path,default=NATIVE);a=ap.parse_args()
 if a.plan:
  need(a.project and a.output,'plan requires project/output');r=make_plan(project=a.project,output=a.output,baseline=a.baseline,baseline_sha=a.baseline_sha256,native_baseline=a.native_baseline,source_root=a.source_root);print(json.dumps(dict(status='planned-no-compilation',plan=str(a.output/'plan.json'),source_identity=r['source_identity'],GPU_units=len(r['gpu_units']))))
 elif a.repair_native_main:
  need(a.output,'native repair output required');r=repair_main(a.repair_native_main,a.output);print(json.dumps({k:v for k,v in r.items()if k not in('compiled_header_sha256','command','link','dependencies')},sort_keys=True))
 else:r=build(a.build_plan);print(json.dumps({k:v for k,v in r.items()if k not in('source_closure','native_units','binaries','compiled_header_sha256')},sort_keys=True))
if __name__=='__main__':main()
