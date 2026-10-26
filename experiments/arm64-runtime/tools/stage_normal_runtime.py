"""Stage normal native artifacts into the existing private QA schema1 contract.

Copies/signs and checks native metadata plus a bounded scalar capability;
never builds, creates a GPU Device, starts a game or imports old QA binaries.
Original normal artifacts remain byte-identical. Dependencies are the declared
configured prefixes, not claimed freshly built or portable.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,shlex,shutil,stat,subprocess,time
from game_pso_component import sha
from game_pso_owned_audit import load_canonical_layout
from gpu_candidate_identity import resolve_gpu_edge
from warm_archive_manifest import resolve_gpu_identity
from build_resource_cpu_runtime import protocol_versions,core_command

P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
D=Path(__file__).resolve().parents[1]
HEX=re.compile(r'[0-9a-f]{64}')
def need(ok,why):
 if not ok:raise ValueError(why)
def directory(path):
 path=Path(path).absolute();s=path.lstat()
 need(path==path.resolve(strict=True)and path.is_relative_to(P/'evidence')and stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned canonical normal evidence directory required');return path
def regular(path):
 path=Path(path).absolute();s=path.lstat()
 need(path==path.resolve(strict=True)and path.is_relative_to(P/'evidence')and stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_nlink==1,'owned canonical normal evidence file required')
 return path
def read_json(path,inputs):
 path=regular(path);need(path.stat().st_size<=32*1024*1024,'bounded normal receipt required');before=sha(path);value=json.loads(path.read_text());need(sha(path)==before,'normal receipt changed');inputs[str(path)]=before;return value
def checked_hash(path,want,inputs):
 path=regular(path);need(isinstance(want,str)and HEX.fullmatch(want)and sha(path)==want,'normal artifact/source SHA mismatch: '+path.name);inputs[str(path)]=want;return path
def check_inputs(inputs):
 for path,want in inputs.items():need(sha(regular(path))==want,'normal staging input changed: '+path)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def output_root(output=None):
 root=Path(output).absolute()if output else P/'evidence'/('resource-cpu-execution-'+str(time.time_ns()))
 need(root.parent==P/'evidence'and re.fullmatch(r'resource-cpu-execution-[0-9]+',root.name)and root==root.resolve()and not root.exists(),'fresh exact private CPU execution root required')
 return root
def write(path,value):
 with Path(path).open('x')as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n')
def command(argv,**kwargs):return subprocess.check_output(argv,env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'},text=True,timeout=30,**kwargs)
def dependencies(path):return[line.strip().split(' (compatibility')[0]for line in command(['/usr/bin/otool','-L',str(path)]).splitlines()[1:]]
def gpu_edge(path,library):
 edges=[e for e in dependencies(path)if Path(e).name.startswith('libbbgpu')]
 need(len(edges)==1 and resolve_gpu_edge(path,edges[0])==library,'normal executable must resolve exactly to selected normal renderer');return edges[0]
def file_api(build,target,inputs):
 reply=directory(build/'.cmake/api/v1/reply');indices=sorted(reply.glob('index-*.json'));need(indices,'normal CMake File API required')
 def item(name):
  p=Path(name);need(not p.is_absolute()and '..'not in p.parts,'CMake reply escaped');return reply/p
 index=read_json(indices[-1],inputs);model=read_json(item(index['reply']['codemodel-v2']['jsonFile']),inputs)
 need(len(model['configurations'])==1,'single normal CMake configuration required')
 ref=next((r for r in model['configurations'][0]['targets']if r['name']==target),None);need(ref,'normal target missing: '+target)
 row=read_json(item(ref['jsonFile']),inputs);need(row['name']==target and len(row.get('artifacts',[]))==1,'normal target/artifact identity')
 relative=Path(row['artifacts'][0]['path']);need(not relative.is_absolute()and '..'not in relative.parts,'normal target artifact escaped build')
 path=regular(build/relative);return path,row
def cache_values(build,inputs):
 path=regular(build/'CMakeCache.txt');inputs[str(path)]=sha(path);result={}
 for line in path.read_text().splitlines():
  m=re.match(r'([^/#][^:=]*):[^=]+=(.*)$',line)
  if m:result[m[1]]=m[2]
 return result
def object_of(argv):
 need(argv.count('-o')==1,'one compiler output required');return argv[argv.index('-o')+1]
def normalize_compile(argv):
 argv=list(argv);need(argv.count('-c')==1,'one compilation source required');argv[argv.index('-c')+1]='<main-source>'
 for flag in('-o','-MF','-MT'):
  while flag in argv:
   i=argv.index(flag);del argv[i:i+2]
 return[a for a in argv if a not in('-MD','-MMD')]
def normalize_link(argv,main_object):
 argv=list(argv);need(argv.count(main_object)==1 and argv.count('-o')==1,'one main object/link output required');argv[argv.index(main_object)]='<main-object>'
 i=argv.index('-o');del argv[i:i+2];return argv
def relocate_copy(executable,old_library,new_library):
 """One exact declared edge, only on the new owned copy."""
 edge=gpu_edge(executable,old_library)
 subprocess.run(['/usr/bin/install_name_tool','-change',edge,str(new_library),str(executable)],check=True,capture_output=True)
 subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(executable)],check=True,capture_output=True)
 need(gpu_edge(executable,new_library)==str(new_library),'relocated copy did not retain exact absolute host edge')
def select_worker_repair(*,result_path,plan_path,build,source,library,library_sha,inputs):
 result=read_json(result_path,inputs);plan=read_json(plan_path,inputs)
 need(Path(result_path).parent==Path(plan_path).parent and result['status']=='native-batch-main-built-not-run','normal main-only repair result/plan contract')
 need(result['plan_sha256']==sha(Path(plan_path))and result['normal_renderer_sha256']==plan['normal_renderer_sha256']==library_sha,'repair plan/renderer identity')
 need(plan['shader_compiler_overrides']is False,'shader compiler override prohibited')
 main=checked_hash(plan['source'],plan['source_sha256'],inputs);worker=checked_hash(result['worker'],result['worker_sha256'],inputs)
 need(result['source_sha256']==plan['source_sha256']and main.parent==worker.parent==regular(result_path).parent,'repair main/result output ownership')
 need(sha(D/'src/game_pso_preparer.mm')==plan['source_sha256'],'repair main differs from explicitly executing development source')
 normal_main=source/'src/game_pso_preparer.mm';need(Path(plan['normal_compile_source'])==normal_main,'repair changed original source role')
 units=read_json(build/'compile_commands.json',inputs);unit=next((u for u in units if Path(u['file'])==normal_main),None);need(unit,'normal worker compile source missing')
 original=unit.get('arguments')or shlex.split(unit['command']);need(normalize_compile(original)==normalize_compile(plan['compile']),'repair changed native compiler flags/headers')
 need(Path(plan['compile'][plan['compile'].index('-c')+1])==main,'repair compile source mismatch')
 for relative in re.findall(r'^\s*#\s*include\s*"([^"]+)"',main.read_text(),re.M):
  need(not (main.parent/relative).exists(),'repair main may not shadow normal ABI headers')
 obj=checked_hash(object_of(plan['compile']),sha(regular(object_of(plan['compile']))),inputs)
 need(obj.parent==main.parent,'repair object escaped its owned build')
 ninja=Path(cache_values(build,inputs)['CMAKE_MAKE_PROGRAM']).resolve(strict=True)
 lines=command([str(ninja),'-t','commands','game-pso-preparer'],cwd=build).splitlines();normal_link=None
 for line in lines:
  if ' -o game-pso-preparer 'not in line:continue
  parsed=core_command(line)
  if '-o'in parsed and object_of(parsed)=='game-pso-preparer':normal_link=parsed
 need(normal_link is not None and normalize_link(normal_link,object_of(original))==normalize_link(plan['link'],str(obj)),'repair is not a main-only normal link')
 need(Path(object_of(plan['link']))==worker,'repair link output mismatch')
 edge=resolve_gpu_identity(worker,P/'evidence');need(edge['library']==str(library)and edge['library_sha256']==library_sha,'repair actual DSO mismatch')
 return worker,dict(result=str(regular(result_path)),result_sha256=sha(Path(result_path)),plan=str(regular(plan_path)),plan_sha256=sha(Path(plan_path)),
    main_source=str(main),main_source_sha256=plan['source_sha256'],normal_main_sha256=sha(normal_main),normal_flags_and_link_unchanged=True,shader_compiler_overrides=False,actual_gpu_edge=edge)

def stage(*,renderer_build,cpu_build,gpu_result,cpu_result,layout_result,output=None,worker_repair_result=None,worker_repair_plan=None):
 from qa_build_guard import require_no_games
 from package_local_arm64_qa import load_cpu_runtime_manifest
 require_no_games();root=output_root(output);inputs={};renderer=directory(renderer_build);cpu=directory(cpu_build)
 g=read_json(gpu_result,inputs);c=read_json(cpu_result,inputs);l=read_json(layout_result,inputs)
 need(g.get('schema')==1 and g['status']=='normal-targets-built'and c['status']=='normal-CPU-targets-built','successful normal build receipts required')
 source=directory(g['source_root']);need(c['source_root']==str(source)and g['renderer_build']==str(renderer)and c['cpu_build']==str(cpu),'normal source/build receipts mismatch')
 rg=cache_values(renderer,inputs);cg=cache_values(cpu,inputs)
 need(rg.get('CMAKE_HOME_DIRECTORY')==str(source/'vendor/metal_api')and rg.get('BB_GAME_PREPARATION_SOURCE_ROOT')==str(source)and rg.get('CMAKE_OSX_ARCHITECTURES')=='arm64','normal renderer source/architecture mismatch')
 need(cg.get('BB_ARM64_PROTOTYPE_DIR')==str(source)and cg.get('BB_ARM64_RUNTIME_ROOT')==str(P)and cg.get('BB_ARM64_RENDERER_BUILD_DIR')==str(renderer)and cg.get('CMAKE_OSX_ARCHITECTURES')=='arm64','normal CPU source/runtime/architecture mismatch')
 gpu_plan=read_json(regular(gpu_result).parent/'plan.json',inputs)
 need(gpu_plan['selected']['build']==str(renderer)and gpu_plan['selected']['source']==str(source/'vendor/metal_api'),'normal GPU plan/build mismatch')
 for path,want in gpu_plan['source_sha256'].items():
  need(Path(path).is_relative_to(source),'GPU source escaped selected source tree');checked_hash(path,want,inputs)
 need(len(gpu_plan['source_sha256'])==g['renderer_translation_units'],'normal source TU count mismatch')
 library,_=file_api(renderer,'bbgpu',inputs);library=checked_hash(library,g['artifacts']['libbbgpu.dylib']['sha256'],inputs)
 need(g['artifacts']['libbbgpu.dylib']['path']==str(library)and c['normal_renderer']==str(library)and cg.get('BB_ARM64_CPU_HOST_LIBRARY')==str(library),'normal selected CPU/GPU library mismatch')
 library_sha=sha(library);native={}
 for name in('game-pso-preparer','game-module-preparer'):
  binary,_=file_api(renderer,name,inputs);need(g['artifacts'][name]['path']==str(binary),'normal consumer target mismatch');checked_hash(binary,g['artifacts'][name]['sha256'],inputs)
  actual=resolve_gpu_identity(binary,P/'evidence');need(actual['library']==str(library)and actual['library_sha256']==library_sha,'normal consumer actual GPU identity mismatch');native[name]=binary
 game,_=file_api(cpu,'bb-game-arm64',inputs);need(c['bb-game-arm64']['path']==str(game),'normal CPU target mismatch');checked_hash(game,c['bb-game-arm64']['sha256'],inputs)
 cpu_edge=gpu_edge(game,library);actual=resolve_gpu_identity(game,P/'evidence');need(actual['library_sha256']==library_sha,'normal CPU actual host mismatch')
 identity=read_json(cpu/'generated/cpu_cache_build.json',inputs);need(identity['source_root']==str(source)and identity['build_root']==str(cpu)and identity['renderer_root']==str(renderer),'normal CPU identity build roots mismatch')
 need(l['status']=='collected-normal-target'and l['strict_canonical_layout_passed']is True and l['renderer_loaded']is False and l['cached_code_registered']is False and l['renderer_build']==str(renderer),'sealed normal layout result required')
 layout=checked_hash(l['layout'],l['layout_sha256'],inputs);shaders=directory(source/'vendor/metal_api/shadps4');need(l['source_root']==str(shaders),'sealed layout source mismatch')
 payload=load_canonical_layout(layout,shaders,l['layout_sha256']);need(payload['binary_sha256']==l['binary_sha256'],'layout binary receipt mismatch')
 for path,want in l['normal_input_sha256'].items():checked_hash(path,want,inputs)
 for name,want in payload['source_sha256'].items():checked_hash(shaders/name,want,inputs)
 repair=None;need(bool(worker_repair_result)==bool(worker_repair_plan),'repair requires explicit result and plan together')
 if worker_repair_result:
  native['game-pso-preparer'],repair=select_worker_repair(result_path=worker_repair_result,plan_path=worker_repair_plan,build=renderer,source=source,library=library,library_sha=library_sha,inputs=inputs)
 check_inputs(inputs);root.mkdir(mode=0o700);host=root/'libbbgpu.normal-host.dylib';copied_game=root/'bb-game-arm64'
 shutil.copy2(library,host);shutil.copy2(game,copied_game)
 need(sha(host)==library_sha and sha(copied_game)==sha(game),'normal copies changed before relocation')
 need(gpu_edge(copied_game,library)==cpu_edge,'CPU copy original RPATH did not resolve to exact normal DSO')
 relocate_copy(copied_game,library,host)
 actual=resolve_gpu_identity(copied_game,P/'evidence');need(actual['library']==str(host)and actual['library_sha256']==library_sha,'staged CPU/host exact edge mismatch')
 need(str(host)in dependencies(copied_game)and cpu_edge not in dependencies(copied_game),'staged CPU must retain the old absolute-host admission contract')
 versions=protocol_versions(source);provenance=dict(normal_gpu_result=str(regular(gpu_result)),normal_gpu_result_sha256=sha(Path(gpu_result)),normal_cpu_result=str(regular(cpu_result)),normal_cpu_result_sha256=sha(Path(cpu_result)),normal_layout_result=str(regular(layout_result)),normal_layout_result_sha256=sha(Path(layout_result)),normal_gpu_plan_sha256=sha(regular(gpu_result).parent/'plan.json'))
 fingerprint=digest(gpu_plan['source_sha256'])
 record=dict(schema=1,kind='blood-echoes-isolated-cpu-runtime',production=False,execution_root=str(root),game=dict(path=str(copied_game),sha256=sha(copied_game)),host=dict(path=str(host),sha256=sha(host)),gpu_library=str(library),gpu_library_sha256=library_sha,source_fingerprint=fingerprint,source_identity=fingerprint,**versions,normal_provenance=provenance,prepared_dependencies_reused=True,clean_checkout_build_proven=False,validation=dict(status='staged-not-run',game_started=False))
 component=dict(schema=1,kind='blood-echoes-game-pso-component',production=False,enabled=True,layout=str(layout),layout_sha256=l['layout_sha256'],source_root=str(shaders),library=str(library),library_sha256=library_sha,
  worker=str(native['game-pso-preparer']),worker_sha256=sha(native['game-pso-preparer']),module_consumer=str(native['game-module-preparer']),module_consumer_sha256=sha(native['game-module-preparer']),coherent_renderer=True,diagnostic_only=False,automatic_capture_schema=1,coherent_source_identity=fingerprint,coherent_build_result_sha256=sha(Path(gpu_result)),normal_provenance=provenance,prepared_dependencies_reused=True,clean_checkout_build_proven=False)
 if repair:component['worker_main_repair']=repair
 check_inputs(inputs);need(sha(host)==library_sha,'host copy must retain original normal GPU bytes')
 write(root/'cpu-runtime-manifest.json',record);write(root/'GAME-PSO-COMPONENT.source.json',component)
 load_cpu_runtime_manifest(root/'cpu-runtime-manifest.json',expected_gpu_sha256=library_sha)
 # Use the existing packager boundary and strict runtime loader too. The only
 # native execution here is its bounded scalar capability query (no Device);
 # its fresh temporary state is removed, with no old cache read or written.
 from game_pso_component import freeze_inputs,load
 require_no_games();(root/'bin').mkdir()
 frozen=freeze_inputs(root,root/'GAME-PSO-COMPONENT.source.json',host,rewrite=relocate_copy,admission_root=P)
 loaded=load(root);need(loaded is not None and loaded['library']==host and loaded['library_sha256']==library_sha,'existing strict component admission failed')
 check_inputs(inputs);need(sha(host)==library_sha,'normal host bytes changed during component admission')
 report=dict(status='normal-runtime-staged-not-run',cpu_runtime_manifest=str(root/'cpu-runtime-manifest.json'),game_pso_component=str(root/'GAME-PSO-COMPONENT.source.json'),gpu_library_sha256=library_sha,original_inputs_sha256=inputs,worker_main_repair=repair,old_cpu_manifest_admission_passed=True,strict_component_admission_passed=True,fixed_helper_seed_schema=frozen['fixed_helper_seed_schema'],original_normal_artifacts_modified=False,game_started=False,GPU_started=False,prepared_dependencies_reused=True,clean_checkout_build_proven=False)
 write(root/'result.json',report);return report
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in('renderer-build','cpu-build','gpu-result','cpu-result','layout-result'):p.add_argument('--'+name,type=Path,required=True)
 p.add_argument('--output',type=Path);p.add_argument('--worker-repair-result',type=Path);p.add_argument('--worker-repair-plan',type=Path);a=p.parse_args()
 print(json.dumps(stage(**{k:v for k,v in vars(a).items()}),indent=2))
if __name__=='__main__':main()
