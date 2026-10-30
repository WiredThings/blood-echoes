#!/usr/bin/env python3
"""Independent bbcpu game host, reusing actual renderer inputs and original ABI generators."""
from pathlib import Path
import argparse,json,subprocess
from configure_native_build import SOURCE,PRIVATE_RUNTIME,codegen_commands,directory,need,prepare,renderer_inputs

def plan(*,source_root,renderer_source_root,renderer_build,build_dir,generated_dir,dependency_prefixes):
    source=directory(source_root);renderer_source=directory(renderer_source_root)
    renderer=renderer_inputs(renderer_source,renderer_build)
    build=Path(build_dir).absolute();generated=Path(generated_dir).absolute()
    for p in (build,generated):
        need(p==p.resolve()and not p.is_relative_to(source),'separate ordinary build/generated locations required')
    need(build!=generated and build!=Path(renderer['build']),'CPU build, renderer build and generated locations must differ')
    prefixes=[directory(p)for p in dependency_prefixes];need(prefixes,'explicit dependency prefixes required')
    command=[renderer['build_tools']['cmake']['path'],'-S',str(source/'bbcpu'),'-B',str(build),'-G','Ninja',
      '-DCMAKE_MAKE_PROGRAM='+renderer['build_tools']['ninja']['path'],'-DCMAKE_BUILD_TYPE=Release',
      '-DCMAKE_OSX_ARCHITECTURES=arm64','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DBB_BUILD_GAME_HOST=ON',
      '-DBUILD_TESTING=OFF','-DCMAKE_PREFIX_PATH='+';'.join(map(str,prefixes)),
      '-DBB_ARM64_GENERATED_DIR='+str(generated),'-DBB_ARM64_RUNTIME_ROOT='+str(PRIVATE_RUNTIME),
      '-DBB_ARM64_SDL_LIBRARY='+renderer['sdl_library'],'-DBB_ARM64_CPU_HOST_LIBRARY='+renderer['library'],
      '-DBB_ARM64_CPU_HOST_INCLUDE_DIR='+str(Path(renderer['source'])/'shim'),
      '-DBB_ARM64_DEPENDENCY_INCLUDE_DIRS='+';'.join(renderer['includes'])]
    if renderer['deployment_target']:command+=['-DCMAKE_OSX_DEPLOYMENT_TARGET='+renderer['deployment_target']]
    if renderer['sysroot']:command+=['-DCMAKE_OSX_SYSROOT='+renderer['sysroot']]
    return dict(schema=1,cpu_backend='bbcpu',source_root=str(source),renderer_source_root=str(renderer_source),
      build_dir=str(build),generated_dir=str(generated),runtime_root=str(PRIVATE_RUNTIME),renderer=renderer,
      generators=codegen_commands(source,generated,renderer),configure=command,fex_linkage=False,
      game_started=False,formal_data_accessed=False)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root',type=Path,default=SOURCE)
    for name in('renderer-source-root','renderer-build','build-dir','generated-dir'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--dependency-prefix',type=Path,action='append',required=True);p.add_argument('--plan',action='store_true');a=p.parse_args()
    value=plan(source_root=a.source_root,renderer_source_root=a.renderer_source_root,renderer_build=a.renderer_build,
      build_dir=a.build_dir,generated_dir=a.generated_dir,dependency_prefixes=a.dependency_prefix)
    if not a.plan:
        prepare(value);subprocess.run(value['configure'],check=True)
    print(json.dumps(value,indent=2))
if __name__=='__main__':main()
