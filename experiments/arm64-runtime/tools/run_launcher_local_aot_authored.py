"""Actual default policy + actual toolchain + original final-exec shell hook.

Only authored boot/content/patch/config and MSL. No game or existing cache use.
"""
from pathlib import Path
import hashlib, json, os, subprocess, sys, time

P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tools'))
from native_observer import NativeObserver
from qa_build_guard import require_no_games
from local_metal_builder import derive_key, canonical, digest


def main():
    require_no_games()
    observer=NativeObserver()
    before=observer.system()
    assert not before['locked'] and before['pressure']!=4, before
    label=str(time.time_ns())
    state=P/'state'/('launcher-aot-authored-'+label);state.mkdir()
    evidence=P/'evidence'/('launcher-aot-authored-'+label);evidence.mkdir()
    gpu=state/'gpu';gpu.mkdir()
    for name in ['boot','content','patches','settings']:
        (state/name).write_text('authored '+name+'\n')
    probe=state/'authored-probe'
    probe.write_text('#!/usr/bin/python3\nimport json,os,sys\nprint("AUTHORED_EXEC "+json.dumps({"args":sys.argv[1:],"mode":os.environ.get("BB_MTL_LOCAL_AOT_MODE"),"cohort":os.environ.get("BB_MTL_LOCAL_AOT_COHORT"),"build":os.environ.get("BB_MTL_LOCAL_AOT_BUILD_ID"),"original_env":os.environ.get("BB_ARM_AUTHORED_EXEC_SENTINEL")}))\n')
    probe.chmod(0o700)
    # Use the same real selected native GPU executable verification as gameplay,
    # but only hash/inspect it; it is never launched by this authored control.
    selected=json.loads((P/'evidence/owned-shader-gpu-app.json').read_text())
    gpu_executable=selected['target']
    args=[str(state/'boot'),'--content-profile',str(state/'content'),
          '--patches',str(state/'patches'),'--app0',str(state/'authored merged view'),
          '--user',str(state/'untouched save path'),'--timeout','0']
    launcher=Path(__file__).resolve().parents[1]/'fixtures/original-run-with-local-aot.sh'
    source=launcher.read_text()
    start=source.index('# Native-runtime component preparation')
    end=source.index('if [[ -n ${mod_game:-} ]]',start)
    hook=source[start:end]
    stage=state/'stage';(stage/'tools/macos').mkdir(parents=True)
    (stage/'tools/macos/local_metal_startup.py').symlink_to(P/'tools/launcher_local_aot.py')
    shell='set -euo pipefail\nprobe=$1; shift\nprobe_args=("$@")\n'+hook+'exec "$probe" "${probe_args[@]}"\n'
    runner=state/'original-final-exec.sh';runner.write_text(shell)
    env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',PYTHON=sys.executable,
        BB_ARM_LAUNCHER_COMPAT='1',BB_RENDERER='metal',BB_CONFIG=str(state/'settings'),
        BB_GPU_USER_DIR=str(gpu),BB_GPU_PROCESS=gpu_executable,
        BB_ARM_AUTHORED_EXEC_SENTINEL='retained-original-value')
    outcomes=[]
    def launch(name):
        result=subprocess.run(['/bin/bash',str(runner),str(probe),*args],cwd=stage,
                              env=env,capture_output=True,text=True,timeout=120)
        (evidence/(name+'.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,(name,result.returncode,result.stderr[-1500:])
        observed=json.loads(next(s[len('AUTHORED_EXEC '):] for s in result.stdout.splitlines()
                                 if s.startswith('AUTHORED_EXEC ')))
        assert observed['args']==args and observed['original_env']=='retained-original-value'
        assert not (state/'untouched save path').exists()
        outcomes.append(dict(name=name,observed=observed,exit=result.returncode))
        return observed
    cold=launch('first-default-record')
    assert cold['mode']=='record' and cold['build'] is None
    cohort=cold['cohort'];assert cohort and len(cohort)==64
    requests=gpu/'local-metal'/cohort/'requests';requests.mkdir(parents=True)
    source=b'#include <metal_stdlib>\nusing namespace metal;kernel void authored_default(device uint* x[[buffer(0)]],uint i[[thread_position_in_grid]]){x[i]=i+7u;}\n'
    request=dict(schema=1,recipe='bb-metal-local-aot-v1',cohort=cohort,
        source_sha256=digest(source),entry='authored_default',language='metal3.1',
        math_mode='safe',math_fp32='fast')
    request['key']=derive_key(request)
    (requests/(request['key']+'.metal')).write_bytes(source)
    (requests/(request['key']+'.json')).write_bytes(canonical(request))
    prepared=launch('second-default-build-and-load')
    assert prepared['cohort']==cohort and prepared['mode']=='load' and prepared['build']
    artifact_dir=gpu/'local-metal'/cohort/'artifacts'/prepared['build']
    library=artifact_dir/(request['key']+'.metallib')
    assert library.is_file()
    original=hashlib.sha256(library.read_bytes()).hexdigest();mtime=library.stat().st_mtime_ns
    warm=launch('third-default-verified-skip')
    assert warm==prepared
    assert hashlib.sha256(library.read_bytes()).hexdigest()==original and library.stat().st_mtime_ns==mtime
    result=dict(passed=True,outcomes=outcomes,library_sha256=original,
                before=before,after=observer.system(),state=str(state),game_started=False,
                actual_toolchain=True,original_shell_hook=True,existing_cache_modified=False)
    (evidence/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    observer.close();print(evidence)


if __name__=='__main__':main()
