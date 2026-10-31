"""Explicit configuration/bridge generation, never historical command replay.

Reads current normal renderer CMake fields; does not build renderer/FEX, launch
games or change the existing private runtime data admission.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,shlex,stat,subprocess,sys
PRIVATE_RUNTIME=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
SOURCE=Path(__file__).resolve().parents[1]
def need(ok,why):
    if not ok:raise ValueError(why)
def sha(path):
    with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def directory(path):
    path=Path(path).absolute();need(path==path.resolve(strict=True)and path.is_dir(),'canonical source/build directory required');return path
def cache_values(path):
    result={}
    for line in path.read_text().splitlines():
        m=re.match(r'([^/#][^:=]*):[^=]+=(.*)$',line)
        if m:result[m[1]]=m[2]
    return result
def configured_tool(cache,key):
    """Resolve only the renderer's explicit tool; never search the runner PATH."""
    selected=Path(cache.get(key,''))
    need(bool(cache.get(key))and selected.is_absolute(),key+' must select an absolute configured tool')
    path=selected.resolve(strict=True)
    need(stat.S_ISREG(path.stat().st_mode)and os.access(path,os.X_OK),key+' must select an executable regular tool')
    return dict(cache_key=key,configured_path=str(selected),path=str(path),sha256=sha(path))
def renderer_inputs(source,build):
    source=directory(source);build=directory(build);renderer=directory(source/'vendor/metal_api');cache=cache_values(build/'CMakeCache.txt')
    need(Path(cache.get('CMAKE_HOME_DIRECTORY','')).resolve()==renderer,'renderer build belongs to another source root')
    need(cache.get('CMAKE_OSX_ARCHITECTURES')=='arm64','normal renderer must select exactly arm64')
    need(cache.get('BB_BUILD_GAME_PREPARATION')=='ON','normal renderer must include preparation targets')
    need(Path(cache.get('BB_GAME_PREPARATION_SOURCE_ROOT','')).resolve()==source,'renderer preparation source mismatch')
    need(cache.get('CMAKE_GENERATOR')=='Ninja','normal renderer must use the declared Ninja generator')
    tools=dict(cmake=configured_tool(cache,'CMAKE_COMMAND'),ninja=configured_tool(cache,'CMAKE_MAKE_PROGRAM'))
    reply=build/'.cmake/api/v1/reply';indices=sorted(reply.glob('index-*.json'));need(indices,'request codemodel-v2 before renderer configuration')
    index=json.loads(indices[-1].read_text());model=json.loads((reply/index['reply']['codemodel-v2']['jsonFile']).read_text())
    need(len(model['configurations'])==1,'single renderer configuration required')
    record=next((r for r in model['configurations'][0]['targets']if r['name']=='bbgpu'),None);need(record,'normal bbgpu target missing')
    data=json.loads((reply/record['jsonFile']).read_text());artifacts=data.get('artifacts',[]);need(len(artifacts)==1,'one renderer library required')
    library=(build/artifacts[0]['path']).resolve();need(library.is_relative_to(build)and library.name=='libbbgpu.dylib','renderer artifact escaped normal build')
    units=json.loads((build/'compile_commands.json').read_text());unit=next((u for u in units if Path(u['file']).resolve()==renderer/'shim/bbgpu.cpp'),None)
    need(unit,'actual normal renderer command missing');argv=unit.get('arguments')or shlex.split(unit['command'])
    need(any('CMakeFiles/bbgpu.dir/'in a for a in argv),'command is not from normal bbgpu target')
    includes=[];flags=[];i=1
    while i<len(argv):
        item=argv[i]
        if item in('-I','-isystem'):
            i+=1;need(i<len(argv),'include value missing');includes.append(argv[i])
        elif item.startswith('-I')and len(item)>2:includes.append(item[2:])
        elif item in('-D','-U'):
            i+=1;need(i<len(argv),'definition missing');flags.append(item+argv[i])
        elif item.startswith(('-D','-U'))and len(item)>2:flags.append(item)
        i+=1
    includes=list(dict.fromkeys(str((Path(unit['directory'])/p).resolve())for p in includes))
    need(str(renderer/'shim')in includes and str(renderer/'shadps4')in includes,'canonical headers missing')
    link=[p for item in data.get('link',{}).get('commandFragments',[])for p in shlex.split(item['fragment'])]
    sdl=[Path(p).resolve(strict=True)for p in link if Path(p).is_absolute()and re.match(r'libSDL3(?:[.0-9]*)\.dylib$',Path(p).name)]
    need(len(set(sdl))==1,'one explicit current renderer SDL required')
    avplayer={}
    for name in('avplayer_state.cpp','avplayer_source.cpp'):
        path=renderer/'shadps4/core/libraries/avplayer'/name
        rows=[u for u in units if Path(u['file']).resolve()==path]
        need(len(rows)==1,'one current renderer AvPlayer TU required: '+name)
        words=rows[0].get('arguments')or shlex.split(rows[0]['command'])
        need(any('CMakeFiles/bbgpu.dir/'in a for a in words),'AvPlayer TU belongs to another target')
        avplayer[name]=dict(source_sha256=sha(path),configured_command_sha256=hashlib.sha256(json.dumps(words).encode()).hexdigest())
    thread_header=renderer/'shim/core/libraries/kernel/threads.h'
    avplayer['threads.h']=dict(source_sha256=sha(thread_header))
    return dict(source=str(renderer),build=str(build),target='bbgpu',library=str(library),sdl_library=str(sdl[0]),includes=includes,flags=flags,
                deployment_target=cache.get('CMAKE_OSX_DEPLOYMENT_TARGET',''),sysroot=cache.get('CMAKE_OSX_SYSROOT',''),
                avplayer_configured_inputs=avplayer,build_tools=tools,generator='Ninja',
                cache_sha256=sha(build/'CMakeCache.txt'),compile_commands_sha256=sha(build/'compile_commands.json'),target_description_sha256=sha(reply/record['jsonFile']))
def codegen_commands(source,generated,renderer):
    """Identical host ABI generators for either independent CPU engine."""
    native=[sys.executable,str(source/'tools/generate_native.py'),'--source-root',str(source),'--output-dir',str(generated),'--compiler','/usr/bin/clang']
    for include in renderer['includes']:native+=['--include-dir',include]
    gpu=[sys.executable,str(source/'tools/generate_gpu.py'),'--source-root',str(source),'--output-dir',str(generated),'--header-manifest',str(source/'config/gpu-adapter-headers.json')]
    gpu+=['--renderer-source-root',renderer['source']]
    return [native,gpu]

def plan(*,source_root,build_dir,generated_dir,dependency_prefixes,renderer_build,runtime_root=PRIVATE_RUNTIME,renderer_source_root=None):
    source=directory(source_root)
    # This literal only selects the unbundled development fallback compiled
    # into the binary. No source, generated file or dependency is read there.
    # A fresh build host need not have a private QA runtime tree. Keep the
    # literal fixed: runtime admission remains enforced by the runtime itself.
    runtime=Path(runtime_root)
    need(runtime==PRIVATE_RUNTIME,'existing Shared runtime root must not be relocated')
    build=Path(build_dir).absolute();generated=Path(generated_dir).absolute()
    for path in(build,generated):need(path==path.resolve()and not path.is_relative_to(source),'independent nonlinked build/generated outputs required')
    need(generated!=build,'generated output must be distinct from build')
    prefixes=[directory(p)for p in dependency_prefixes];need(prefixes,'explicit dependency prefixes required')
    renderer_source=directory(renderer_source_root or source)
    renderer=renderer_inputs(renderer_source,renderer_build);need(build!=Path(renderer['build']),'separate CPU/renderer builds required')
    native,gpu=codegen_commands(source,generated,renderer)
    aether=source/'vendor/aether';harness=[aether/'src/core/fex/fex_guest_engine.cpp',aether/'src/core/guest_cpu/fex_guest_cpu.cpp',aether/'src/core/guest_cpu/hle_call_adapter.cpp',aether/'src/core/guest_cpu/fex_hle_bridge.cpp',aether/'runtime/probes/fexcore-guest-harness.cpp']
    command=[renderer['build_tools']['cmake']['path'],'-S',str(aether/'runtime/sources/fexcore-darwin'),'-B',str(build),'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+renderer['build_tools']['ninja']['path'],'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_OSX_ARCHITECTURES=arm64','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DCMAKE_DISABLE_SOURCE_CHANGES=ON','-DCMAKE_PREFIX_PATH='+';'.join(map(str,prefixes)),'-DTUNE_CPU=none','-DBUILD_FEXCORE_ONLY=ON',
        '-DFEXCORE_PROJECT_SOURCE_DIR='+str(aether/'src'),'-DFEXCORE_SMOKE_SOURCE='+str(aether/'runtime/probes/fexcore-smoke.cpp'),'-DFEXCORE_GUEST_HARNESS_SOURCES='+';'.join(map(str,harness)),
        '-DBB_ARM64_PROTOTYPE_DIR='+str(source),'-DBB_ARM64_GENERATED_DIR='+str(generated),'-DBB_ARM64_RUNTIME_ROOT='+str(runtime),'-DBB_ARM64_DEPENDENCY_INCLUDE_DIRS='+';'.join(renderer['includes']),
        '-DBB_ARM64_SDL_LIBRARY='+renderer['sdl_library'],'-DBB_ARM64_RENDERER_LIBRARY='+renderer['library'],'-DBB_ARM64_CPU_HOST_LIBRARY='+renderer['library'],'-DBB_ARM64_RENDERER_BUILD_DIR='+renderer['build'],'-DBB_ARM64_RENDERER_TARGET=bbgpu','-DBB_ARM64_RENDERER_BUILD_JOBS=1']
    for flag in('BUILD_TESTING','BUILD_FEX_LINUX_TESTS','BUILD_THUNKS','BUILD_FEXCONFIG','ENABLE_GDB_SYMBOLS','ENABLE_LTO','ENABLE_JEMALLOC_GLIBC_ALLOC','ENABLE_OFFLINE_TELEMETRY','ENABLE_VIXL_DISASSEMBLER','ENABLE_VIXL_SIMULATOR','ENABLE_ZYDIS','ENABLE_FEXCORE_PROFILER','ENABLE_CCACHE'):command+=['-D'+flag+'=OFF']
    command+=['-DCMAKE_DISABLE_FIND_PACKAGE_fmt=TRUE']
    if renderer['deployment_target']:command+=['-DCMAKE_OSX_DEPLOYMENT_TARGET='+renderer['deployment_target']]
    if renderer['sysroot']:command+=['-DCMAKE_OSX_SYSROOT='+renderer['sysroot']]
    return dict(schema=1,source_root=str(source),renderer_source_root=str(renderer_source),build_dir=str(build),generated_dir=str(generated),runtime_root=str(runtime),renderer=renderer,generators=[native,gpu],configure=command,
                configure_ready=True,cpu_host=dict(status='same-normal-renderer-candidate',actual_load_and_native_ABI_validated=False,duplicate_host_objects_required=False),
                runtime_tree_read_for_build=False,
                clean_checkout_build_proven=False,ABI_validated=False,targets_built=False)
def prepare(value):
    renderer_source=Path(value.get('renderer_source_root',value['source_root']))
    need(renderer_inputs(renderer_source,Path(value['renderer']['build']))==value['renderer'],'renderer configuration changed after planning')
    output=Path(value['generated_dir']);need(not output.exists()or(output.is_dir()and not any(output.iterdir())),'fresh or empty generated output required');output.mkdir(parents=True,exist_ok=True)
    for command in value['generators']:subprocess.run(command,check=True,timeout=180)
    need(renderer_inputs(renderer_source,Path(value['renderer']['build']))==value['renderer'],'renderer configuration changed during generation')
    def bracket(item):
        need(']==]'not in item and ';'not in item,'ambiguous CMake scalar');return ' [==['+item+']==]\n'
    renderer=value['renderer'];text='set(BB_GPU_HEADER_FLAGS\n'+''.join(bracket(p)for p in renderer['flags'])+')\n'
    text+='set(BB_GPU_HEADER_INCLUDES\n'+''.join(bracket(p)for p in renderer['includes'])+')\n'
    (output/'gpu_flags.cmake').write_text(text);(output/'native-build-inputs.json').write_text(json.dumps(value,indent=2)+'\n')
    query=Path(value['build_dir'])/'.cmake/api/v1/query';query.mkdir(parents=True,exist_ok=True);(query/'codemodel-v2').touch()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root',type=Path,default=SOURCE)
    p.add_argument('--renderer-source-root',type=Path)
    for n in('build-dir','generated-dir','renderer-build'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--dependency-prefix',type=Path,action='append',required=True);p.add_argument('--runtime-root',type=Path,default=PRIVATE_RUNTIME)
    g=p.add_mutually_exclusive_group();g.add_argument('--plan',action='store_true');g.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    value=plan(source_root=a.source_root,build_dir=a.build_dir,generated_dir=a.generated_dir,dependency_prefixes=a.dependency_prefix,renderer_build=a.renderer_build,runtime_root=a.runtime_root,renderer_source_root=a.renderer_source_root)
    if not a.plan:
        prepare(value)
        if not a.prepare_only:subprocess.run(value['configure'],check=True)
    print(json.dumps(value,indent=2))
if __name__=='__main__':main()
