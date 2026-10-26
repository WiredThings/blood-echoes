#!/usr/bin/env python3
"""Configure-only controls for normal preparation targets; never builds/execs them.

Uses declared existing dependency prefixes for CMake discovery. This tests the
source target graph, not clean dependency construction or a runnable package.
"""
from pathlib import Path
import argparse
import json
import os
import re
import shlex
import subprocess
import time

D=Path(__file__).resolve().parents[1]
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')

def need(value,reason):
    if not value:raise AssertionError(reason)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-prefix',type=Path,required=True)
    parser.add_argument('--header-prefix',type=Path,required=True)
    parser.add_argument('--homebrew-prefix',type=Path,default=Path('/opt/homebrew'))
    args=parser.parse_args()
    prefix=args.dependency_prefix.resolve(strict=True)
    headers=args.header_prefix.resolve(strict=True)
    brew=args.homebrew_prefix.resolve(strict=True)
    base=P/'evidence'
    need(base==base.resolve(strict=True)and base.stat().st_uid==os.getuid(),'canonical owned evidence root')
    root=base/('game-preparation-cmake-'+str(time.time_ns()));root.mkdir(mode=0o700)
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
    env=dict(os.environ,PKG_CONFIG_LIBDIR=':'.join(str(p)for p in(prefix/'lib/pkgconfig',prefix/'share/pkgconfig',brew/'lib/pkgconfig',brew/'share/pkgconfig')))
    env.pop('PKG_CONFIG_PATH',None)
    common=['cmake','-S',str(D/'vendor/metal_api'),'-G','Ninja',
            '-DCMAKE_BUILD_TYPE=Release','-DCMAKE_OSX_ARCHITECTURES=arm64',
            '-DCMAKE_OSX_DEPLOYMENT_TARGET=26.0','-DCMAKE_OSX_SYSROOT='+sdk,
            '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DCMAKE_DISABLE_SOURCE_CHANGES=ON',
            '-DCMAKE_PREFIX_PATH='+';'.join(map(str,(prefix,headers,brew))),
            '-DSPIRV_CROSS_INCLUDE_DIR='+str(prefix/'include'),'-DBB_NET=OFF','-DBB_LTO=OFF','-DBB_PGO=off']
    for name in('MSL','GLSL','CORE'):
        common+=['-DSPIRV_CROSS_'+name+'_LIBRARY='+str(prefix/('lib/libspirv-cross-'+name.lower()+'.a'))]
    cases=[]
    variants=(
        ('enabled',['-DBB_BUILD_GAME_PREPARATION=ON','-DBB_GAME_PREPARATION_SOURCE_ROOT='+str(D)],None),
        ('missing-root',['-DBB_BUILD_GAME_PREPARATION=ON'],'Set BB_GAME_PREPARATION_SOURCE_ROOT'),
        ('wrong-root',['-DBB_BUILD_GAME_PREPARATION=ON','-DBB_GAME_PREPARATION_SOURCE_ROOT='+str(D.parent)],'this exact renderer'),
        ('missing-producer',['-DBB_BUILD_GAME_PREPARATION=ON','-DBB_GAME_PREPARATION_SOURCE_ROOT='+str(D),'-DBB_DEV_FETCH_CAPTURE=OFF'],'same renderer'),
        ('wrong-architecture',['-DBB_BUILD_GAME_PREPARATION=ON','-DBB_GAME_PREPARATION_SOURCE_ROOT='+str(D),'-DCMAKE_OSX_ARCHITECTURES=x86_64'],'native arm64'),
        ('standalone',[],None))
    for name,options,error in variants:
        out=root/name;out.mkdir()
        query=out/'.cmake/api/v1/query';query.mkdir(parents=True)
        (query/'codemodel-v2').touch()
        command=common+['-B',str(out)]+options
        (out/'command.json').write_text(json.dumps(command,indent=2)+'\n')
        result=subprocess.run(command,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (out/'configure.log').write_text(result.stdout)
        need((result.returncode!=0)==bool(error),'unexpected configure status: '+name)
        if error:need(error in result.stdout,'wrong rejection reason: '+name)
        cases.append(dict(name=name,returncode=result.returncode,expected_failure=bool(error)))
    out=root/'enabled'
    reply=out/'.cmake/api/v1/reply'
    index=json.loads(next(reply.glob('index-*.json')).read_text())
    model=json.loads((reply/index['reply']['codemodel-v2']['jsonFile']).read_text())
    targets={t['name']:json.loads((reply/t['jsonFile']).read_text())for t in model['configurations'][0]['targets']}
    for name in('game-pso-preparer','game-module-preparer','metadata-layout','game-preparation-host-services'):
        need(name in targets,'normal target missing: '+name)
    gpu_id=targets['bbgpu']['id'];host_id=targets['game-preparation-host-services']['id']
    for name in('game-pso-preparer','game-module-preparer'):
        target=targets[name];dependencies={item['id']for item in target['dependencies']}
        need(gpu_id in dependencies and host_id in dependencies,'same normal renderer/production host services: '+name)
        source_files=[item['path']for item in target['sources']if item['path'].endswith(('.cpp','.mm'))]
        need(len(source_files)==1 and Path(source_files[0]).parent==D/'src','helper must not compile override/runtime TUs')
        link=' '.join(item['fragment']for item in target['link']['commandFragments'])
        need('libbbgpu.dylib'in link and '-export_dynamic'in link,'real renderer load edge and host export required')
        external_link=link.replace(str(out),'<fresh-build>')
        need('evidence/'not in external_link and '/private/tmp/blood-echoes-arm64-metal-20261023/build'not in external_link,'historical dependency edge imported')
    layout=targets['metadata-layout']
    need(gpu_id not in {item['id']for item in layout.get('dependencies',[])},'metadata may not depend on renderer')
    link=' '.join(item['fragment']for item in layout['link']['commandFragments'])
    need(not any(item in link for item in('libbbgpu','libfmt','libsirit','-framework','-lav','-lSDL')),'metadata must remain system-only')
    profile=(D/'vendor/metal_api/shadps4/shader_recompiler/profile.h').read_text()
    expected=re.findall(r'^\s*(?:u32|u64|bool)\s+(\w+)\s*(?:\{[^{}]*\})?;',profile,re.M)
    actual=re.findall(r'^PROFILE_FIELD\((\w+)\)$',(out/'game-preparation/profile_fields.inc').read_text(),re.M)
    need(actual==expected and len(actual)>40,'full same-source profile member order')
    commands=json.loads((out/'compile_commands.json').read_text())
    for filename in('game_pso_host_services.cpp','game_pso_preparer.mm','game_module_preparer.mm','metadata_layout.cpp'):
        unit=next(item for item in commands if Path(item['file']).name==filename)
        command=shlex.split(unit['command'])
        need('-DBB_DEV_FETCH_CAPTURE=1'in command and '-std=gnu++23'in command,'explicit native/header ABI fields: '+filename)
        need(str(D/'vendor/metal_api/shim/bbport_platform.h')in command,'same platform preinclude')
        if filename.endswith('.mm'):need('-fobjc-arc'in command,'Objective-C++ ARC')
        if filename=='game_module_preparer.mm':need('-DBB_WITH_OWNED_COMPANION_CONSUMER=1'in command,'owned companion reader absent')
    standalone=(root/'standalone/build.ninja').read_text()
    need('build game-pso-preparer:'not in standalone and 'build metadata-layout:'not in standalone,'standalone opt-in changed')
    report=dict(status='configure-controls-passed',cases=cases,profile_fields=len(actual),same_renderer_target=True,
                production_host_services=True,metadata_renderer_link=False,targets_built=False,
                prepared_dependencies_reused=True,clean_checkout_build_proven=False,game_started=False,GPU_started=False)
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(evidence=str(root),**report),indent=2))

if __name__=='__main__':main()
