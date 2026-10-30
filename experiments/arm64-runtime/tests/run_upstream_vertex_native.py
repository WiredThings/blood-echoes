#!/usr/bin/env python3
"""Real bounded authored Metal: record -> actual builder -> local depth pixels.

Run from an authorized host shell with Metal access. No game, window, capture,
production input, synthetic receipt or toolchain override is used.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import local_metal_builder as builder
from qa_build_guard import require_no_games


def main():
    require_no_games()
    case=Path(tempfile.mkdtemp(prefix='upstream-vertex-native-',dir=ROOT/'state'))
    for name in ('gpu','record','load','warm','home','tmp'):(case/name).mkdir(mode=0o700)
    local=case/'gpu/local-metal';local.mkdir(mode=0o700)
    command=['/usr/bin/clang++','-std=c++20','-fobjc-arc','-Wno-deprecated-declarations',
             '-I',str(ROOT/'vendor/metal_api/shadps4'),
             str(ROOT/'tests/metal/test_upstream_vertex_invariance.mm'),
             '-framework','Foundation','-framework','Metal','-o',str(case/'probe')]
    built=subprocess.run(command,capture_output=True,text=True,timeout=90)
    (case/'compile.log').write_text(built.stdout+built.stderr)
    if built.returncode:raise RuntimeError('native probe build failed: '+str(case/'compile.log'))
    env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
    env.update(BB_GPU_USER_DIR=str(case/'gpu'),BB_MTL_LOCAL_AOT_ROOT=str(local),
               BB_MTL_LOCAL_AOT_COHORT='a'*64,HOME=str(case/'home'),TMPDIR=str(case/'tmp'))
    def run(name,mode,build_id=None):
        child=dict(env,BB_MTL_LOCAL_AOT_MODE=mode)
        if build_id:child['BB_MTL_LOCAL_AOT_BUILD_ID']=build_id
        result=subprocess.run([str(case/'probe'),str(case/name),'load'if mode=='load'else'record'],
                              capture_output=True,text=True,timeout=45,env=child)
        (case/(name+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('native probe failed: '+str(case/(name+'.log')))
        return json.loads((case/name/'result.json').read_text())
    recorded=run('record','record')
    first=builder.build(local,'a'*64,enabled=True)
    (case/'builder-first.json').write_bytes(builder.canonical(first)+b'\n')
    if first['status']!='passed' or first['built']!=2:raise RuntimeError('real builder did not compile two libraries: '+str(case/'builder-first.json'))
    loaded=run('load','load',first['build_id'])
    second=builder.build(local,'a'*64,enabled=True)
    (case/'builder-warm.json').write_bytes(builder.canonical(second)+b'\n')
    if second['status']!='passed' or second['built']!=0:raise RuntimeError('warm builder repeated compilation')
    warm=run('warm','load',second['build_id'])
    result=dict(passed=True,case=str(case),game_started=False,production_data_touched=False,
                recorded=recorded,loaded=loaded,warm=warm,
                first=dict(built=first['built'],build_id=first['build_id']),
                second=dict(built=second['built'],build_id=second['build_id']))
    out=ROOT/'evidence/upstream-vertex-native-current.json'
    out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
