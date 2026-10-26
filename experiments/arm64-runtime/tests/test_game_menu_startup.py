#!/usr/bin/env python3
"""Short native source-boundary controls; outputs only in fresh Shared evidence."""
from pathlib import Path
import json
import os
import subprocess
import time

D=Path(__file__).resolve().parents[1]
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')

def main():
    base=P/'evidence'
    if base!=base.resolve(strict=True) or base.stat().st_uid!=os.getuid():
        raise ValueError('canonical owned private evidence root required')
    root=base/('game-menu-startup-'+str(time.time_ns()))
    root.mkdir(mode=0o700)
    binary=root/'test-game-menu-startup'
    command=['clang++','-arch','arm64','-std=c++20','-O1','-UNDEBUG',
             '-include',str(D/'vendor/metal_api/shim/bbport_platform.h'),
             '-I'+str(D/'src'),'-I'+str(D/'vendor/bb_runtime/src'),
             '-I'+str(D/'vendor/metal_api/shim'),str(D/'tests/test_game_menu_startup.cpp'),'-o',str(binary)]
    (root/'command.json').write_text(json.dumps(command,indent=2)+'\n')
    result=subprocess.run(command,text=True,capture_output=True,timeout=45)
    (root/'compile.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError('compile failed: '+str(root/'compile.log')+'\n'+result.stderr[-4000:])
    cases=[]
    for mode in ('disabled','mismatch-message','mismatch-add','short-image','allocation-failure','commit-failure','valid'):
        result=subprocess.run([str(binary),mode],env={'PATH':'/usr/bin:/bin'},text=True,capture_output=True,timeout=5)
        (root/(mode+'.log')).write_text(result.stdout+result.stderr)
        cases.append(dict(mode=mode,returncode=result.returncode))
    report=dict(cases=cases,passed=all(c['returncode']==0 for c in cases),actual_original_installer=True,
                actual_arm_wrapper=True,authored_host_edges=True,game_started=False,FEX_executed=False,GPU_started=False)
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(evidence=str(root),**report),indent=2))
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
