"""Build the existing canonical layout emitter against one explicit source root.

The emitter loads only system libraries. This is ABI metadata, not a shader
compiler or a game launch. Preparation components call build_from_plan so layout
stamps and the renderer/consumers refer to the same immutable source snapshot.
"""
from pathlib import Path
import argparse, hashlib, json, os, re, shlex, subprocess, time

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
  deps=subprocess.check_output(['/usr/bin/otool','-L',str(binary)],text=True,timeout=10)
  need(all(line.strip().startswith(('/usr/lib/','/System/'))for line in deps.splitlines()[1:]),'layout emitter must not load a renderer or third-party library')
  text=runner.run([str(binary)],10,guard,cwd=out);layout=json.loads(text)
  need(layout['schema']==1 and layout['pointer_bytes']==8,'native canonical layout envelope')
  layout.update(source_sha256=before_headers,binary_sha256=sha(binary),
                profile_fields=[dict(type=t,name=n)for t,n in fields],renderer_loaded=False,cached_code_registered=False)
  need(all('Profile.'+name in layout for _,name in fields),'all canonical profile fields emitted')
  fixtures=out/'fixtures';fixtures.mkdir();layout['authored_writer_fixtures']={}
  for mode in('plain','fetch','motion','geometry','graphics','compute','profile-padding'):
   path=fixtures/(mode+'.bin');result=json.loads(runner.run([str(binary),'--fixture',str(path),mode],10,guard,cwd=out,outputs=((path,1024*1024),)))
   result.update(path=str(path),sha256=sha(path));layout['authored_writer_fixtures'][mode]=result
  need({name:sha(source/name)for name in HEADERS}==before_headers and sha(emitter)==emitter_sha,'layout sources changed during build')
  write(out/'layout.json',layout)
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
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--component-plan',type=Path)
 ap.add_argument('--project',type=Path,default=D)
 args=ap.parse_args()
 if args.component_plan:result=build_from_plan(args.component_plan)
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
