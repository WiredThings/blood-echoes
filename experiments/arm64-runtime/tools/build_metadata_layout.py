"""Build the existing canonical layout emitter against one explicit source root.

The emitter loads only system libraries. This is ABI metadata, not a shader
compiler or a game launch. Preparation components call build_from_plan so layout
stamps and the renderer/consumers refer to the same immutable source snapshot.
"""
from pathlib import Path
import argparse, hashlib, json, os, re, shlex, subprocess, time, struct

D=Path(__file__).resolve().parents[1]
HEADERS=('common/serdes.h','common/hash.h','shader_recompiler/backend/bindings.h',
 'shader_recompiler/profile.h','shader_recompiler/specialization.h','shader_recompiler/info.h',
 'shader_recompiler/resource.h','shader_recompiler/runtime_info.h',
 'shader_recompiler/frontend/fetch_shader.h','shader_recompiler/ir/passes/srt.h',
 'video_core/pipeline_cache_core_types.h','video_core/pipeline_cache_core_serialization.cpp')

def need(ok,why):
 if not ok:raise ValueError(why)
def sha(path):
 with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def write(path,value):
 with Path(path).open('x')as stream:json.dump(value,stream,indent=2);stream.write('\n')
def directory(path):
 path=Path(path).absolute();need(path==path.resolve(strict=True)and path.is_dir()and path.stat().st_uid==os.getuid(),'owned canonical layout directory');return path
def checked(path,limit=4*1024*1024):
 from game_pso_owned_audit import checked_bytes
 return checked_bytes(Path(path).absolute(),limit=limit)[0]
def profile_fields(source):
 fields=re.findall(r'^\s*(u32|u64|bool)\s+(\w+)\s*(?:\{[^{}]*\})?;',checked(source/'shader_recompiler/profile.h').decode(),re.M)
 need(len(fields)>40 and len({name for _,name in fields})==len(fields),'canonical complete profile field declarations');return fields
def system_edges(text):
 rows=text.decode().splitlines()if isinstance(text,bytes)else text.splitlines()
 edges=[line.strip().split(' (compatibility')[0]for line in rows[1:]if line.strip()]
 need(edges and all(p.startswith(('/usr/lib/','/System/'))for p in edges),'layout emitter must not load a renderer or third-party library')
 return edges
def seal_layout(*,source,out,binary,runner,guard,before_headers,emitter,emitter_sha,fields):
 """One complete sealing path for both compiled and normal-CMake emitters."""
 from game_pso_owned_audit import load_canonical_layout
 from decode_shader_cache import decode_meta,decode_key,semantic_profile
 before_binary=hashlib.sha256(checked(binary,32*1024*1024)).hexdigest()
 need(runner.run(['/usr/bin/lipo','-archs',str(binary)],10,guard,cwd=out).strip()==b'arm64','native arm64 layout emitter required')
 edges=system_edges(runner.run(['/usr/bin/otool','-L',str(binary)],10,guard,cwd=out))
 layout=json.loads(runner.run([str(binary)],10,guard,cwd=out))
 need(layout['schema']==1 and layout['pointer_bytes']==8 and layout['little_endian'],'native canonical layout envelope')
 layout.update(source_sha256=before_headers,binary_sha256=before_binary,
               profile_fields=[dict(type=t,name=n)for t,n in fields],renderer_loaded=False,cached_code_registered=False)
 need(all('Profile.'+name in layout and 'Profile.'+name+'.bytes'in layout for _,name in fields),'all canonical profile fields emitted')
 fixtures=out/'fixtures';fixtures.mkdir();layout['authored_writer_fixtures']={}
 for mode in('plain','fetch','motion','geometry','graphics','compute','profile-padding'):
  path=fixtures/(mode+'.bin');result=json.loads(runner.run([str(binary),'--fixture',str(path),mode],10,guard,cwd=out,outputs=((path,1024*1024),)))
  blob=checked(path,1024*1024);need(result['bytes']==len(blob)and result['used']+result['tail']==len(blob),'canonical writer fixture envelope')
  if mode in('graphics','compute'):decode_key(blob,layout)
  elif mode=='profile-padding':
   size=layout['Profile.size'];need(result['native_equality']is True and len(blob)==2*size,'native profile padding equality')
   # This native fixture emits two bare Profile values. Supply the canonical
   # authored envelope to the unchanged strict profile reader, never relax it.
   prefix=struct.pack('<4I',0x42425043,10,7,5)
   need(semantic_profile(prefix+blob[:size],layout)['semantic_sha256']==semantic_profile(prefix+blob[size:],layout)['semantic_sha256'],'profile padding semantic equality')
  else:decode_meta(blob,layout)
  result.update(path=str(path),sha256=hashlib.sha256(blob).hexdigest(),canonical_decode_checked=True);layout['authored_writer_fixtures'][mode]=result
 need({name:hashlib.sha256(checked(source/name)).hexdigest()for name in HEADERS}==before_headers and sha(emitter)==emitter_sha,'layout sources changed during preparation')
 need(sha(binary)==before_binary,'layout binary changed during preparation')
 write(out/'layout.json',layout);digest=sha(out/'layout.json')
 load_canonical_layout(out/'layout.json',source,digest)
 return dict(layout=str(out/'layout.json'),layout_sha256=digest,binary_sha256=before_binary,loaded_edges=edges,strict_canonical_layout_passed=True)

def normal_contract(renderer_build,source_root):
 """Read current CMake provenance; neither invoke a compiler nor trust old evidence."""
 from configure_native_build import cache_values
 build=directory(renderer_build);source=directory(source_root);renderer=directory(source.parent);runtime=directory(source.parents[2])
 need(source.name=='shadps4','explicit canonical shadps4 source root required')
 checked(build/'CMakeCache.txt');cache=cache_values(build/'CMakeCache.txt')
 need(cache.get('CMAKE_HOME_DIRECTORY')==str(renderer)and cache.get('BB_GAME_PREPARATION_SOURCE_ROOT')==str(runtime),'normal layout source/build identity mismatch')
 need(cache.get('CMAKE_OSX_ARCHITECTURES')=='arm64'and cache.get('BB_BUILD_GAME_PREPARATION')=='ON','normal native preparation target required')
 reply=directory(build/'.cmake/api/v1/reply');indices=sorted(reply.glob('index-*.json'));need(indices,'normal CMake codemodel required')
 snapshots={}
 def read(path):
  path=Path(path).absolute();data=checked(path);snapshots[str(path)]=hashlib.sha256(data).hexdigest();return data
 def item(name):
  relative=Path(name);need(not relative.is_absolute()and '..'not in relative.parts,'CMake reply escaped build root');return reply/relative
 index=json.loads(read(indices[-1]));model=json.loads(read(item(index['reply']['codemodel-v2']['jsonFile'])))
 need(len(model['configurations'])==1,'single normal build configuration required')
 record=next((t for t in model['configurations'][0]['targets']if t['name']=='metadata-layout'),None);need(record,'normal metadata-layout target absent')
 target=json.loads(read(item(record['jsonFile'])));need(target.get('name')=='metadata-layout'and target['type']=='EXECUTABLE','metadata target identity/type mismatch')
 artifacts=target.get('artifacts',[]);need(len(artifacts)==1,'one actual metadata artifact required')
 relative=Path(artifacts[0]['path']);need(not relative.is_absolute()and '..'not in relative.parts,'metadata artifact escaped normal build')
 binary=build/relative;need(binary.name=='metadata-layout','wrong normal metadata artifact');read(binary)
 emitter=runtime/'tests/metadata_layout.cpp';read(emitter)
 sources=[Path(s['path'])if Path(s['path']).is_absolute()else renderer/s['path']for s in target['sources']if Path(s['path']).suffix in('.cpp','.mm','.c','.m','.S')]
 need(sources==[emitter],'normal metadata target has unexpected/override sources')
 need(not any('bbgpu'in d['id']or'host-services'in d['id']for d in target.get('dependencies',[])),'metadata target depends on a renderer/host')
 units=json.loads(read(build/'compile_commands.json'));units=[u for u in units if Path(u['file']).absolute()==emitter]
 need(len(units)==1,'one current metadata compile unit required');unit=units[0];argv=unit.get('arguments')or shlex.split(unit['command'])
 need(directory(unit['directory'])==build,'metadata compiler cwd is outside its normal build')
 need(argv.count('-arch')==1 and argv[argv.index('-arch')+1]=='arm64'and argv.count('-o')==1,'metadata compile architecture/output domain')
 obj=Path(argv[argv.index('-o')+1]);obj=obj if obj.is_absolute()else Path(unit['directory'])/obj
 need(obj.is_relative_to(build)and 'CMakeFiles/metadata-layout.dir/'in str(obj),'normal metadata object escaped build target');read(obj)
 need(str(renderer/'shim/bbport_platform.h')in argv and '-std=gnu++23'in argv,'normal metadata compiler fields mismatch')
 fields=profile_fields(source);inc=build/'game-preparation/profile_fields.inc';data=read(inc).decode()
 need(data==''.join('PROFILE_FIELD('+n+')\n'for _,n in fields),'normal profile member list is stale/incomplete')
 for path in(build/'CMakeCache.txt',build/'build.ninja',build/'CMakeFiles/rules.ninja'):read(path)
 return dict(build=build,source=source,binary=binary,emitter=emitter,fields=fields,snapshots=snapshots,
             ninja=Path(cache['CMAKE_MAKE_PROGRAM']).resolve(strict=True),compile_argv=argv)

def collect_normal(*,project,renderer_build,source_root,output):
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from local_metal_builder import FixedRunner
 project=directory(project);out=Path(output).absolute()
 need(out==out.resolve()and out.is_relative_to(project/'evidence')and not out.exists(),'fresh isolated layout output required')
 directory(out.parent);out.mkdir(mode=0o700);contract=normal_contract(renderer_build,source_root)
 source=contract['source'];before_headers={name:hashlib.sha256(checked(source/name)).hexdigest()for name in HEADERS}
 require_no_games();observer=NativeObserver();before=observer.system();policy=MemoryPolicy(before);began=time.monotonic()
 class Guard:
  def check(self,**kw):
   if kw.get('preflight'):require_no_games()
   reason=policy.observe(time.monotonic()-began,observer.system());need(not reason,reason or'layout memory guard')
   need(time.monotonic()-began<240,'normal layout collection total timeout')
 guard=Guard();runner=FixedRunner(out)
 try:
  guard.check(preflight=True)
  # Identical graph bytes, alternate primary filename only: do not rebuild
  # CMake's always-dirty glob manifest. Ninja still checks the original cwd's
  # command log/dependency database and every actual metadata target input.
  manifest=out/'normal-build.ninja';manifest.write_bytes(checked(contract['build']/'build.ninja'))
  dry=runner.run([str(contract['ninja']),'-f',str(manifest),'-n','metadata-layout'],10,guard,cwd=contract['build'])
  need(dry.strip()==b'ninja: no work to do.','normal metadata target is dirty; build it before collecting')
  result=seal_layout(source=source,out=out,binary=contract['binary'],runner=runner,guard=guard,before_headers=before_headers,
      emitter=contract['emitter'],emitter_sha=sha(contract['emitter']),fields=contract['fields'])
  need(normal_contract(renderer_build,source_root)==contract,'normal CMake/binary/source provenance changed during collection')
  result.update(status='collected-normal-target',source_root=str(source),renderer_build=str(contract['build']),
                normal_target='metadata-layout',normal_input_sha256=contract['snapshots'],compile_argv=contract['compile_argv'],
                renderer_loaded=False,cached_code_registered=False,targets_built=False,game_started=False,
                before=before,after=observer.system(),seconds=time.monotonic()-began)
  write(out/'result.json',result);return result
 finally:observer.close()

def build_layout(*,project,source_root,compile_template,output,cwd):
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 from local_metal_builder import FixedRunner
 project=directory(project);source=directory(source_root);out=Path(output).absolute()
 need(out.is_relative_to(project/'evidence')and not out.exists(),'fresh isolated layout output required')
 directory(out.parent);out.mkdir(mode=0o700)
 require_no_games();observer=NativeObserver();before=observer.system();policy=MemoryPolicy(before);began=time.monotonic()
 class Guard:
  def check(self,**kw):
   if kw.get('preflight'):require_no_games()
   reason=policy.observe(time.monotonic()-began,observer.system())
   need(not reason,reason or'layout memory guard')
   need(time.monotonic()-began<240,'layout preparation total timeout')
 guard=Guard();runner=FixedRunner(out);guard.check(preflight=True)
 before_headers={name:sha(source/name)for name in HEADERS}
 emitter=D/'tests/metadata_layout.cpp';emitter_sha=sha(emitter)
 target=out/'metadata_layout.cpp';target.write_bytes(emitter.read_bytes());target.chmod(0o444)
 fields=re.findall(r'^\s*(u32|u64|bool)\s+(\w+)\s*(?:\{[^{}]*\})?;',
                   (source/'shader_recompiler/profile.h').read_text(),re.M)
 need(len(fields)>40 and len({name for _,name in fields})==len(fields),'canonical complete profile field declarations')
 (out/'profile_fields.inc').write_text(''.join('PROFILE_FIELD('+name+')\n'for _,name in fields))
 command=list(compile_template);command.pop() # explicit source in the admitted template
 for flag in ('-o','-MF','-MT'):
  while flag in command:
   i=command.index(flag);del command[i:i+2]
 command=[item for item in command if item not in('-c','-MD','-MMD')]
 obj=out/'layout.o';binary=out/'layout';dep=out/'layout.d'
 # Preserve the renderer's shim-before-upstream header precedence. Reversing
 # these selects upstream spdlog instead of the existing bbport logging shim.
 command[1:1]=['-I'+str(source.parent/'shim'),'-I'+str(source),'-I'+str(out)]
 command+=['-DNDEBUG','-Wno-invalid offsetof'.replace(' ', '-'),'-MMD','-MF',str(dep),'-MT',str(obj),'-c',str(target),'-o',str(obj)]
 link=['/usr/bin/clang++','-arch','arm64','-flto=thin',str(obj),'-o',str(binary)]
 try:
  runner.run(command,180,guard,cwd=cwd,outputs=((obj,32*1024*1024),))
  runner.run(link,30,guard,cwd=cwd,outputs=((binary,32*1024*1024),))
  seal_layout(source=source,out=out,binary=binary,runner=runner,guard=guard,
      before_headers=before_headers,emitter=emitter,emitter_sha=emitter_sha,fields=fields)
  result=dict(status='built',layout=str(out/'layout.json'),layout_sha256=sha(out/'layout.json'),
              source_root=str(source),emitter_source_sha256=emitter_sha,compile=command,link=link,
              renderer_loaded=False,game_started=False,before=before,after=observer.system(),seconds=time.monotonic()-began)
  write(out/'result.json',result);return result
 finally:observer.close()

def build_from_plan(plan_path):
 plan_path=Path(plan_path).resolve(strict=True);plan=json.loads(plan_path.read_text());output=directory(plan['output'])
 need(plan_path==output/'plan.json','exact coherent plan required')
 result_path=output/'result.json';result=json.loads(result_path.read_text())
 need(result['coherent_renderer']is True and result['diagnostic_only']is False and result['source_identity']==plan['source_identity'],
      'completed coherent non-diagnostic source identity required')
 from game_pso_component import sha as component_sha
 for field in('library','worker','module_consumer'):
  need(component_sha(Path(result[field]))==result[field+'_sha256'],'coherent artifact changed: '+field)
 native=next(unit for unit in plan['native_units']if unit['name']=='host')
 metadata=build_layout(project=plan['project'],source_root=Path(plan['snapshot'])/'vendor/metal_api/shadps4',
      compile_template=native['command'],output=output/('metadata-layout-'+str(time.time_ns())),cwd=plan['prepared_cwd'])
 from game_pso_owned_audit import load_canonical_layout
 load_canonical_layout(Path(metadata['layout']),Path(metadata['source_root']),metadata['layout_sha256'])
 component=dict(schema=1,kind='blood-echoes-game-pso-component',production=False,enabled=True,
     layout=metadata['layout'],layout_sha256=metadata['layout_sha256'],source_root=metadata['source_root'],
     coherent_renderer=True,diagnostic_only=False,automatic_capture_schema=1,
     coherent_source_identity=result['source_identity'],coherent_build_result_sha256=sha(result_path),
     prepared_dependencies_reused=True,clean_checkout_build_proven=False)
 for field in('library','worker','module_consumer'):
  component[field]=result[field];component[field+'_sha256']=result[field+'_sha256']
 component_path=output/'GAME-PSO-COMPONENT.source.json';write(component_path,component)
 return dict(status='complete-component',manifest=str(component_path),sha256=sha(component_path),metadata=metadata,
             game_started=False,GPU_started=False,formal_data_modified=False)

def main():
 ap=argparse.ArgumentParser(description=__doc__);mode=ap.add_mutually_exclusive_group();mode.add_argument('--component-plan',type=Path);mode.add_argument('--renderer-build',type=Path)
 ap.add_argument('--project',type=Path,default=D)
 ap.add_argument('--source-root',type=Path);ap.add_argument('--output',type=Path)
 args=ap.parse_args()
 if args.component_plan:result=build_from_plan(args.component_plan)
 elif args.renderer_build:
  need(args.source_root and args.output,'normal target collection requires explicit source-root and output')
  result=collect_normal(project=args.project,renderer_build=args.renderer_build,source_root=args.source_root,output=args.output)
 else:
  # Preserve the pre-existing author regression command. It uses the same
  # generic emitter, explicitly preferring the declared current source root.
  project=directory(args.project);meta=json.loads((project/'evidence/texture-expansion-build.json').read_text())
  result=build_layout(project=project,source_root=project/'vendor/metal_api/shadps4',
      compile_template=meta['compile'],output=project/'evidence'/('metadata-layout-'+str(time.time_ns())),
      cwd='/private/tmp/blood-echoes-arm64-metal-20261023/build')
  (project/'evidence/metadata-layout-current.json').write_text(json.dumps(dict(evidence=str(Path(result['layout']).parent),layout=result['layout']))+'\n')
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
