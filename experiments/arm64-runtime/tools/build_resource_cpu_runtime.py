"""Parameterised cpu-v2 production-host build; coherent GPU objects are read only.

--plan stages a fresh private CPU frontend and freezes hashes, never compiles.
--build-plan executes that exact plan at -j1, without starting game/GPU processes.
Prepared FEX/dependency artifacts are reused; this is not a clean-source proof.
"""
from pathlib import Path
import argparse,collections,hashlib,json,os,re,shlex,shutil,signal,stat,subprocess,sys,time

D=Path(__file__).resolve().parents[1]
EXPECTED=collections.Counter({'bb_native_runtime':23,'bb_fex_embedding':4,'bb_gpu_adapters':1,'bb_avplayer_adapters':1,'bb-game-arm64':1})
CAPTURE={'fetch_compile_observer.cpp','fetch_compile_producer.cpp','fetch_compile_canonical.cpp','fetch_companion_store.cpp','fetch_register_store.cpp'}
SOURCE_SUFFIXES={'.h','.hpp','.cpp','.mm','.c','.inc','.m'}

def need(ok,why):
 if not ok:raise ValueError(why)
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def write(p,v):
 with Path(p).open('x')as f:json.dump(v,f,sort_keys=True,indent=2);f.write('\n')
def canonical(p):
 p=Path(p).absolute();need(p==p.resolve(strict=True),'canonical path required: '+str(p));return p
def regular(p):
 p=canonical(p);s=p.stat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid(),'owned regular input required: '+str(p));return p
def output_root(project,path,fresh=False):
 p=Path(path).absolute();need(p.parent==project/'evidence'and re.fullmatch(r'resource-cpu-execution-[0-9]+',p.name),'private CPU execution root required')
 need(not p.is_symlink(),'no linked output root')
 if fresh:need(not p.exists(),'preserve old CPU outputs')
 return p

def protocol_versions(root):
 result={}
 for key,rel,name in [('protocol_version','vendor/metal_api/shim/remote/bb_protocol.h','ProtocolVersion'),('live_payload_version','vendor/metal_api/shim/remote/bb_live_payload.h','LivePayloadVersion')]:
  m=re.search(r'\b'+name+r'\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)\s*;', (root/rel).read_text());need(m,'protocol constant missing');result[key]=int(m[1],0)
 return result

def core_command(line):
 c=shlex.split(line)
 while c and c[0]in(':','&&'):c.pop(0)
 if c[:1]==['cd']:c=c[c.index('&&')+1:]
 if c[-2:]==['&&',':']:c=c[:-2]
 need('&&'not in c,'unhandled shell command');return c

def object_arg(c):return c[c.index('-o')+1]
def target(c):return object_arg(c).split('.dir/')[0].split('CMakeFiles/')[-1]

def verify_snapshot(snapshot,files):
 # Same complete current source membership as the already built GPU closure.
 actual={str(p.relative_to(snapshot))for p in snapshot.rglob('*')if p.is_file()}
 need(actual==set(files),'coherent snapshot membership changed')
 for rel,row in files.items():
  p=regular(snapshot/rel);need(p.stat().st_size==row['bytes']and sha(p)==row['sha256'],'coherent source changed: '+rel)

def coherent_inputs(plan_path,result_path):
 plan_path=regular(plan_path);result_path=regular(result_path);g=json.loads(plan_path.read_text());r=json.loads(result_path.read_text())
 need(r['plan_sha256']==sha(plan_path)and r['source_identity']==g['source_identity'],'coherent plan/result identity')
 need(r.get('coherent_renderer')is True and r.get('targeted_compiler_overrides')is False and r.get('diagnostic_only')is False,'complete non-diagnostic renderer required')
 need(len(g['gpu_units'])==r['all_GPU_units_rebuilt']==235,'reviewed 230+5 coherent closure required')
 need({Path(u['source']).name for u in g['gpu_units']if Path(u['source']).name in CAPTURE}==CAPTURE,'complete capture TUs')
 snapshot=canonical(g['snapshot']);need(r['source_closure']==g['source_closure'],'source closure mismatch');verify_snapshot(snapshot,g['source_closure']['files'])
 library=regular(r['library']);need(sha(library)==r['library_sha256'],'renderer library SHA')
 for path,h in r['compiled_header_sha256'].items():need(sha(path)==h,'GPU compiled header changed: '+path)
 for section in('external_header_inputs','external_link_inputs'):
  for path,row in g[section].items():need(sha(row.get('canonical',path))==row['sha256'],'GPU dependency changed: '+path)
 for u in g['gpu_units']:
  need(Path(u['source'])==snapshot/u['relative_source']and object_arg(u['command'])==u['object'],'coherent unit provenance')
  need('-DBB_DEV_FETCH_CAPTURE=1'in u['command'],'capture macro missing')
 return g,r,snapshot,library

def current_cpu_gpu_shared_inputs(current,snapshot,gpu,result):
 # Use only the actual 235 GPU TU dependency/source closure. Native helper
 # mains and their private headers are not renderer ABI dependencies.
 admitted={};gpu_dependencies={};depfile_hashes={}
 for unit in gpu['gpu_units']:
  dep=regular(unit['depfile']);need(dep.stat().st_size<=4*1024*1024,'bounded GPU depfile')
  text=dep.read_text().replace('\\\n',' ');left,tail=text.split(':',1)
  need(shlex.split(left)==[unit['object']],'GPU depfile target identity')
  depfile_hashes[str(dep)]=sha(dep)
  paths={str(Path(value).resolve(strict=True))for value in shlex.split(tail)}
  paths.add(str(Path(unit['source']).resolve(strict=True)))
  for path in paths:
   p=Path(path);expected=result['compiled_header_sha256'].get(path)
   if path==str(Path(unit['source']).resolve(strict=True)):
    expected=gpu['source_closure']['files'][unit['relative_source']]['sha256']
   need(expected is not None and(gpu_dependencies[path]==expected if path in gpu_dependencies else sha(p)==expected),'GPU actual dependency identity: '+path)
   gpu_dependencies[path]=expected
   if p.is_relative_to(snapshot):
    rel=p.relative_to(snapshot);source=regular(current/rel)
    need(admitted[str(source)]==expected if str(source)in admitted else sha(source)==expected,'current CPU source changes actual GPU dependency: '+str(rel))
    admitted[str(source)]=expected
 return dict(current_sha256=admitted,gpu_dependency_sha256=gpu_dependencies,depfile_sha256=depfile_hashes)

def stage_small_sources(project,snapshot,stage,current=None):
 current=current or project
 inputs={};transforms=[]
 for folder in('src','generated'):
  candidates={p.name:p for p in(project/folder).iterdir()if p.is_file()and not p.is_symlink()}
  if(snapshot/folder).is_dir():candidates.update({p.name:p for p in(snapshot/folder).iterdir()if p.is_file()})
  if(current/folder).is_dir():candidates.update({p.name:p for p in(current/folder).iterdir()if p.is_file()and not p.is_symlink()and not re.search(r' 2(?:\.|$)',p.name)})
  (stage/folder).mkdir(parents=True)
  for name,source in sorted(candidates.items()):
   # Derived output must be regenerated after the actual host is linked.
   if name=='bb_cpu_cache_build.h':continue
   source=regular(source);content=source.read_bytes();inputs[str(source)]=sha(source)
   if folder=='generated'and source.suffix in('.c','.cpp'):
    changes=[]
    def change(m):
     old=Path(m[1]);new=snapshot/old.relative_to(project)
     if not new.is_file():return m[0]
     changes.append([str(old),str(new)]);return '#include "'+str(new)+'"'
    content=re.sub(r'#include\s+"('+re.escape(str(project))+r'/vendor/(?:bb_runtime|metal_api)/[^"\n]+)"',change,content.decode()).encode()
    if changes:transforms.append(dict(source=str(source),includes=changes))
   dst=stage/folder/name;dst.write_bytes(content);inputs[str(dst)]=sha(dst)
 # Preserve cpu-v2 relative includes from copied frontend wrappers. These
 # are ordinary owned copies of the exact admitted sources, never symlinks.
 for base,relative in [(current if(current/'vendor/bb_runtime').exists()else snapshot,Path('vendor/bb_runtime')),(current,Path('vendor/dev_world_warper')),(current,Path('vendor/aether/src'))]:
  root=base/relative
  for source in sorted(root.rglob('*')):
   if not source.is_file()or source.suffix not in SOURCE_SUFFIXES or re.search(r' 2(?:\.|$)',source.name):continue
   source=regular(source);shared=snapshot/relative/source.relative_to(root)
   if relative==Path('vendor/bb_runtime')and shared.is_file():need(sha(source)==sha(shared),'CPU/GPU shared runtime source mismatch')
   destination=stage/relative/source.relative_to(root);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(source.read_bytes());inputs[str(source)]=sha(source);inputs[str(destination)]=sha(destination)
 return inputs,transforms

def external_headers(commands,snapshot,stage):
 roots=set();result={};total=0
 for c in commands:
  for i,v in enumerate(c):
   q=v[2:]if v.startswith('-I')and len(v)>2 else c[i+1]if v in('-isystem','-include')else None
   if q:
    p=Path(q)
    if p.is_absolute()and not p.is_relative_to(snapshot)and not p.is_relative_to(stage):roots.add(p)
 for root in sorted(roots):
  need(root.exists(),'prepared include missing: '+str(root))
  for p in([root]if root.is_file()else root.rglob('*')):
   if not p.is_file()or p.suffix not in('.h','.hpp','.inc','.inl','.ipp','.tcc','.def'):continue
   a=p.resolve(strict=True)
   if str(a)in result:continue
   s=a.stat();total+=s.st_size;need(s.st_size<=32*1024*1024 and total<=512*1024*1024 and len(result)<40000,'bounded external headers')
   result[str(a)]=sha(a)
 return result

def make_plan(*,project,gpu_plan,gpu_result,output):
 project=canonical(project);B=project/'build/fex';g,r,O,library=coherent_inputs(gpu_plan,gpu_result);shared_inputs=current_cpu_gpu_shared_inputs(D,O,g,r);R=output_root(project,output,True);R.mkdir(mode=0o700);stage=R/'private-project'
 inputs,transforms=stage_small_sources(project,O,stage,D);objects={};units=[]
 includes=['-I'+str(stage/'src'),'-I'+str(stage/'generated'),'-I'+str(stage/'vendor/bb_runtime'),'-I'+str(stage/'vendor/bb_runtime/src'),*['-I'+str(O/p)for p in('vendor/metal_api/shim','vendor/metal_api/layer','vendor/metal_api/shadps4','vendor/bb_runtime/src','vendor/bb_runtime')]]
 def remap(v):
  for folder in('src','generated'):
   old=str(project/folder)
   if v.startswith('-I'+old):return '-I'+str(stage/folder)+v[len('-I'+old):]
  for folder in('vendor/metal_api','vendor/bb_runtime','vendor/aether/src'):
   old=str(project/folder)
   if v.startswith('-I'+old):return '-I'+str((stage if folder in('vendor/aether/src','vendor/bb_runtime')else O)/folder)+v[len('-I'+old):]
  return v
 rows=json.loads((B/'compile_commands.json').read_text());inputs[str(B/'compile_commands.json')]=sha(B/'compile_commands.json')
 for row in rows:
  c=shlex.split(row['command']);t=target(c)
  if t not in EXPECTED:continue
  rel=Path(row['file']).relative_to(project);source=stage/rel if rel.parts[0]in('src','generated')or str(rel).startswith('vendor/aether/src/')else O/rel
  if not source.is_file():source=Path(row['file'])
  source=regular(source);origin=regular(D/rel);inputs[str(origin)]=sha(origin)
  if not source.is_relative_to(stage/'generated'):need(sha(source)==sha(origin),'selected CPU source differs from current repository: '+str(rel))
  oldobj=object_arg(c);obj=R/'objects'/(str(len(units))+'.o');dep=obj.with_suffix('.d');c=[remap(v)for v in c];c[1:1]=includes;c[c.index('-o')+1]=str(obj);c[c.index('-c')+1]=str(source)
  # Real depfiles record all actually consumed headers, including implicit SDK.
  for flag,value in(('-MF',dep),('-MT',obj)):
   if flag in c:c[c.index(flag)+1]=str(value)
   else:c.extend([flag,str(value)])
  if '-MMD'not in c:c.append('-MMD')
  inputs[str(source)]=sha(source);objects[oldobj]=str(obj);units.append(dict(target=t,source_origin=str(origin),source_origin_sha256=sha(origin),source=str(source),source_sha256=sha(source),original_object=oldobj,object=str(obj),depfile=str(dep),command=c))
 need(collections.Counter(u['target']for u in units)==EXPECTED,'exact original CPU target/TU selection')
 lines=subprocess.check_output(['ninja','-C',str(B),'-t','commands','bb-game-arm64'],text=True).splitlines();archives=[];archive_map={}
 for name,t in [('libbb_native_runtime.a','bb_native_runtime'),('libbb_fex_embedding.a','bb_fex_embedding')]:
  dest=R/name;archive_map[name]=str(dest);members=[u['object']for u in units if u['target']==t];archives.append(dict(output=str(dest),commands=[['/usr/bin/ar','rcs',str(dest),*members],['/usr/bin/ranlib',str(dest)]]))
 avpath=project/'evidence/avplayer-host-candidate-current.json';av=json.loads(avpath.read_text());inputs[str(avpath)]=sha(avpath)
 need(len(av['compile'])==2,'original two AvPlayer host overrides');host_link=g['gpu_link'][:];host=R/'libbbgpu.resource-cpu.dylib';host_link[host_link.index('-o')+1]=str(host);overrides=[]
 for authored in av['compile']:
  original=Path(authored['compile'][-1]);rel=original.relative_to(Path(av['evidence'])/'source');source=O/'vendor/metal_api'/rel
  need(sha(original)==sha(source)==authored['source_sha256'],'AvPlayer override requires source re-audit')
  u=next(u for u in g['gpu_units']if Path(u['source'])==source);c=u['command'][:];obj=R/('cpu-'+source.stem+'.o')
  for flag,v in(('-o',obj),('-MF',obj.with_suffix('.d')),('-MT',obj)):
   c[c.index(flag)+1]=str(v)
  need(host_link.count(u['object'])==1,'exact AvPlayer host object replacement');host_link[host_link.index(u['object'])]=str(obj)
  overrides.append(dict(source=str(source),object=str(obj),depfile=str(obj.with_suffix('.d')),command=c))
 threads=O/'vendor/metal_api/shim/core/libraries/kernel/threads.h';need(sha(threads)==av['header_sha256'],'AvPlayer completion header changed')
 gpu_objects={str(regular(u['object'])):sha(u['object'])for u in g['gpu_units']}
 for v in host_link:
  if v.endswith(('.a','.dylib'))and Path(v).is_file():inputs[str(Path(v).resolve())]=sha(v)
 deps=[v.strip().split(' (compatibility')[0]for v in subprocess.check_output(['otool','-L',str(library)],text=True).splitlines()[1:]]
 need(deps[0]==str(library),'renderer must have frozen own LC_ID')
 for dep in deps[1:]:
  p=library.parent/dep.removeprefix('@loader_path/')if dep.startswith('@loader_path/')else Path(dep)
  if p.is_file()and not str(p).startswith(('/System/','/usr/lib/')):inputs[str(p.resolve())]=sha(p)
 link=core_command(lines[-1]);need('Bin/bb-game-arm64'in link,'real Ninja game link')
 for i,v in enumerate(link):
  if v in objects:link[i]=objects[v]
  elif v in archive_map:link[i]=archive_map[v]
  elif v.endswith('.dylib')and Path(v).name.startswith('libbbgpu.'):link[i]=str(host)
  elif v.endswith('.a'):link[i]=str((B/v).resolve())if not Path(v).is_absolute()else v;inputs[link[i]]=sha(link[i])
  elif v=='Bin/bb-game-arm64':link[i]=str(R/'bb-game-arm64.resource')
 identity=core_command(next(v for v in lines if'make_cpu_cache_build.py'in v));helper=D/'tools/cpu_cache_build_identity.py';identity[identity.index(str(project/'tools/make_cpu_cache_build.py'))]=str(helper)
 for i,v in enumerate(identity):
  if '='in v:
   label,path=v.split('=',1);p=Path(path)
   if path in objects:identity[i]=label+'='+objects[path]
   elif str(p) in {str((B/k).resolve())for k in objects}:identity[i]=label+'='+next(objects[k]for k in objects if(B/k).resolve()==p)
   elif p.name in archive_map and p.resolve()==(B/p.name).resolve():identity[i]=label+'='+archive_map[p.name]
   elif path.startswith(str(project/'src')):identity[i]=label+'='+str(stage/'src'/p.name)
   elif p.name.startswith('libbbgpu.')and path.endswith('.dylib'):identity[i]=label+'='+str(host)
   elif p.is_file():inputs[str(p.resolve())]=sha(p)
   elif not p.is_absolute()and(B/p).is_file():identity[i]=label+'='+str(B/p);inputs[str(B/p)]=sha(B/p)
  if v=='--header':identity[i+1]=str(stage/'generated/bb_cpu_cache_build.h')
  if v=='--manifest':identity[i+1]=str(R/'cpu_cache_build.json')
 # Add all current private frontend headers, not only the original CMake list.
 existing={v.split('=',1)[0]for v in identity if'='in v}
 for p in sorted((stage/'src').glob('*.h')):
  label='frontend-header-'+p.name
  if label not in existing:identity+=['--bridge-part',label+'='+str(p)]
 identity+=['--plan',str(R/'plan.json'),'--runtime-part','private-build-policy='+str(Path(__file__).resolve())]
 for path in [Path(__file__).resolve(),helper,Path(gpu_plan),Path(gpu_result),B/'Bin/bb-game-arm64']:inputs[str(path)]=sha(path)
 for rel,row in g['source_closure']['files'].items():inputs[str(O/rel)]=row['sha256']
 inputs.update(r['compiled_header_sha256'])
 for section in('external_header_inputs','external_link_inputs'):
  inputs.update({str(Path(row.get('canonical',path))):row['sha256']for path,row in g[section].items()})
 headers=external_headers([u['command']for u in units],O,stage);inputs.update(headers)
 # Prepared FEXCore binaries remain pinned; their actually available API/header
 # source must agree with current repository bytes rather than hidden P drift.
 fex_header_proof={}
 for path,h in headers.items():
  p=Path(path)
  if p.is_relative_to(project/'vendor'):
   current=regular(D/p.relative_to(project));need(sha(current)==h,'prepared vendor API differs from repository: '+str(current));inputs[str(current)]=h;fex_header_proof[str(p)]=dict(current_source=str(current),sha256=h)
 versions=protocol_versions(O)
 for section in shared_inputs.values():inputs.update(section)
 cpu_input_sha256=digest(inputs)
 plan=dict(schema=1,kind='blood-echoes-coherent-cpu-plan',project=str(project),execution_root=str(R),stage=str(stage),source_snapshot=str(O),source_identity=g['source_identity'],gpu_source_identity=g['source_identity'],cpu_input_sha256=cpu_input_sha256,shared_gpu_inputs=shared_inputs,source_closure=g['source_closure'],source_versions=versions,gpu_plan=str(Path(gpu_plan).resolve()),gpu_result=str(Path(gpu_result).resolve()),gpu_library=str(library),gpu_library_sha256=r['library_sha256'],gpu_units_reused=len(g['gpu_units']),fex_header_proof=fex_header_proof,current_cpu_source_root=str(D),selected_units=units,archives=archives,host_overrides=overrides,host_link=host_link,host_dependencies=deps,identity_command=identity,game_link=link,input_sha256=inputs,gpu_object_sha256=gpu_objects,generated_absolute_include_rewrites=transforms,build_cwd=str(B),ready_for_build=True,author_fixture=False,game_started=False,formal_writes=False,prepared_dependencies_reused=True,clean_checkout_build_proven=False)
 write(R/'OWNER.json',dict(builder=str(Path(__file__).resolve()),execution_root=str(R)));write(R/'plan.json',plan);return R/'plan.json'

def verify_inputs(plan):
 project=canonical(plan['project']);R=output_root(project,plan['execution_root']);regular(R/'OWNER.json');need(json.loads((R/'OWNER.json').read_text())==dict(builder=str(Path(__file__).resolve()),execution_root=str(R)),'private output ownership')
 need(digest(plan['input_sha256'])==plan['cpu_input_sha256']and plan['gpu_source_identity']==plan['source_identity'],'separate CPU/GPU input identities')
 need(plan.get('schema')==1 and plan.get('kind')=='blood-echoes-coherent-cpu-plan'and plan['ready_for_build']and plan.get('author_fixture')is False,'admitted CPU plan')
 verify_snapshot(Path(plan['source_snapshot']),plan['source_closure']['files']);need(protocol_versions(Path(plan['source_snapshot']))==plan['source_versions'],'CPU/GPU protocol identity')
 need(collections.Counter(u['target']for u in plan['selected_units'])==EXPECTED and len(plan['host_overrides'])==2,'original production CPU target counts')
 for group in('input_sha256','gpu_object_sha256'):
  for path,h in plan[group].items():need(sha(path)==h,'input changed since plan: '+path)
 for u in plan['selected_units']:need(sha(u['source'])==u['source_sha256'],'CPU source changed')
 need(not any('game_pso_host_services'in v for v in plan['host_link']),'preparer-only host prohibited')
 return R

def execute(plan_path):
 plan_path=regular(plan_path);plan=json.loads(plan_path.read_text());R=verify_inputs(plan);need(not(R/'result.json').exists(),'preserve completed CPU build');P=Path(plan['project']);sys.path.insert(0,str(P/'tools'))
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(not before['locked']and before['pressure']!=4,'locked/severe pressure');policy=MemoryPolicy(before);start=time.monotonic();steps=[];(R/'objects').mkdir(exist_ok=True)
 def run(label,args):
  require_no_games()
  with(R/'build.log').open('a')as log:
   log.write('\n'+label+'\n');log.flush();process=subprocess.Popen(args,cwd=plan['build_cwd'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   try:
    while process.poll()is None:
     elapsed=time.monotonic()-start;reason=policy.observe(elapsed,observer.system())
     if reason or elapsed>1200:raise RuntimeError(reason or'CPU build bounded timeout')
     time.sleep(.5)
    need(process.returncode==0,label+' failed: '+str(R/'build.log'))
   finally:
    if process.poll()is None:
     os.killpg(process.pid,signal.SIGTERM)
     try:process.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
  steps.append(dict(label=label,argv=args,exit=process.returncode))
 for u in plan['selected_units']:
  if u['target']!='bb-game-arm64':run(u['target']+':'+Path(u['source']).name,u['command'])
 for a in plan['archives']:
  need(not Path(a['output']).exists(),'private archive already exists')
  for c in a['commands']:run('private archive',c)
 for u in plan['host_overrides']:run('same-source AvPlayer:'+Path(u['source']).name,u['command'])
 run('production CPU host link',plan['host_link']);host=Path(object_arg(plan['host_link']));library=Path(plan['gpu_library']);expected={Path(v).name:str((library.parent/v.removeprefix('@loader_path/')).resolve())if v.startswith('@loader_path/')else v for v in plan['host_dependencies']}
 for edge in subprocess.check_output(['otool','-L',str(host)],text=True).splitlines()[1:]:
  edge=edge.strip().split(' (compatibility')[0];replacement=expected.get(Path(edge).name)
  if replacement and replacement!=edge:run('CPU host dependency identity',['install_name_tool','-change',edge,replacement,str(host)])
 run('production host LC_ID',['install_name_tool','-id',str(host),str(host)]);run('production host sign',['codesign','--force','--sign','-',str(host)])
 run('actual CPU cache identity',plan['identity_command']);gameunit=next(u for u in plan['selected_units']if u['target']=='bb-game-arm64');run('game inline Gate/frontend',gameunit['command']);run('private game link',plan['game_link']);game=Path(object_arg(plan['game_link']))
 signed=subprocess.run(['codesign','-d','--entitlements',':-',str(P/'build/fex/Bin/bb-game-arm64')],capture_output=True,check=True);args=['codesign','--force','--sign','-']
 if signed.stdout:
  import plistlib
  need(isinstance(plistlib.loads(signed.stdout),dict),'existing prototype entitlements');ent=R/'existing-prototype-entitlements.plist';ent.write_bytes(signed.stdout);args+=['--entitlements',str(ent)]
 run('private game sign',args+[str(game)])
 for binary in(host,game):
  run('strict signature verify',['codesign','--verify','--strict',str(binary)]);need(subprocess.check_output(['lipo','-archs',str(binary)],text=True).strip()=='arm64','ARM64 artifact required')
 need(str(host)in subprocess.check_output(['otool','-L',str(game)],text=True),'exact production game host load edge')
 symbols=subprocess.check_output(['nm','-m',str(host)],text=True)
 for symbol in('_bb_arm_native_host_thread_detach','_runtime_thread_attach_host','_bbgpu_arm_avplayer_bridge_revision'):need(symbol in symbols,'AvPlayer completion bridge missing')
 verify_inputs(plan);after=observer.system();runtime=dict(schema=1,kind='blood-echoes-isolated-cpu-runtime',production=False,execution_root=str(R),game=dict(path=str(game),sha256=sha(game)),host=dict(path=str(host),sha256=sha(host)),gpu_library=str(library),gpu_library_sha256=plan['gpu_library_sha256'],source_fingerprint=digest({k:v['sha256']for k,v in plan['source_closure']['files'].items()}),source_identity=plan['source_identity'],gpu_source_identity=plan['gpu_source_identity'],cpu_input_sha256=plan['cpu_input_sha256'],**plan['source_versions'],author_instrumentation=False,validation=dict(status='built-not-run',game_started=False))
 write(R/'cpu-runtime-manifest.json',runtime);report=dict(schema=1,status='built-not-run',cpu_runtime_manifest=str(R/'cpu-runtime-manifest.json'),cpu_runtime_manifest_sha256=sha(R/'cpu-runtime-manifest.json'),game=str(game),game_sha256=sha(game),host=str(host),host_sha256=sha(host),gpu_library_sha256=plan['gpu_library_sha256'],source_identity=plan['source_identity'],plan_sha256=sha(plan_path),selected_cpu_units=len(plan['selected_units']),avplayer_overrides=2,gpu_units_recompiled=0,steps=steps,before=before,after=after,seconds=time.monotonic()-start,game_started=False,formal_writes=False,memory_policy=MemoryPolicy.MANIFEST,prepared_dependencies_reused=True,clean_checkout_build_proven=False);write(R/'result.json',report);return report

def main():
 ap=argparse.ArgumentParser();sub=ap.add_mutually_exclusive_group(required=True);sub.add_argument('--plan',action='store_true');sub.add_argument('--build-plan',type=Path);ap.add_argument('--project',type=Path);ap.add_argument('--gpu-plan',type=Path);ap.add_argument('--gpu-result',type=Path);ap.add_argument('--output',type=Path);a=ap.parse_args()
 if a.plan:
  need(all([a.project,a.gpu_plan,a.gpu_result,a.output]),'plan parameters required');p=make_plan(project=a.project,gpu_plan=a.gpu_plan,gpu_result=a.gpu_result,output=a.output);print(json.dumps(dict(plan=str(p),plan_sha256=sha(p),status='prepared-not-built')))
 else:print(json.dumps({k:v for k,v in execute(a.build_plan).items()if k!='steps'},indent=2))
if __name__=='__main__':main()
