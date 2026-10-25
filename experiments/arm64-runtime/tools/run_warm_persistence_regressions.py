"""Serial authored persistence regressions; invoke through run_authored_controls_guard.py."""
from pathlib import Path
import argparse, json, subprocess, sys, time

P=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,default=P/'evidence/warm-stage-candidate-current.json');args=ap.parse_args()
assert args.candidate.resolve(strict=True).is_relative_to(P/'evidence')
candidate=json.loads(args.candidate.read_text())
library=candidate['candidate']
e=P/'evidence'/('warm-persistence-checkpoint-'+str(time.time_ns()));e.mkdir()
steps=[]
def run(name,args):
    with (e/(name+'.log')).open('w') as log:
        proc=subprocess.run([sys.executable,*args],cwd=P,stdout=log,stderr=subprocess.STDOUT)
    steps.append(dict(name=name,exit=proc.returncode))
    (e/'steps.json').write_text(json.dumps(steps,indent=2)+'\n')
    assert proc.returncode==0,(name,str(e))
    print('PASS '+name,flush=True)
run('owned-shader',['tests/test_owned_shader.py','--library',library])
run('phase-build',['tools/build_first_upload_test.py','--suite','shader-phases','--library',library])
run('phase-controls',['tests/test_actual_shader_phases.py'])
run('preservation',['tests/test_texture_layout_preservation.py','--full','--bounds','--candidate',str(P/'evidence/warm-stage-candidate-current.json')])
run('archive-build',['tools/build_first_upload_test.py','--suite','warm-archive','--library',library])
fixture=json.loads((P/'evidence/first-upload-build-current.json').read_text())
run('archive-controls',['tools/run_warm_archive_process_controls.py','--binary',fixture['binary'],'--library',library])
run('stage-build',['tools/build_first_upload_test.py','--suite','warm-stage','--library',library])
fixture=json.loads((P/'evidence/first-upload-build-current.json').read_text())
run('stage-controls',['tools/run_warm_stage_storage_controls.py','--binary',fixture['binary'],'--library',library])
run('producer-branches',['tools/test_warm_archive_manifest.py'])
(e/'result.json').write_text(json.dumps(dict(passed=True,candidate=candidate,steps=steps,game_started=False,performance_claim=False),indent=2)+'\n')
print(e,flush=True)
