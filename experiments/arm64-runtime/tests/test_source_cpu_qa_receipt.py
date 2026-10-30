#!/usr/bin/env python3
"""Read-only integration controls for a real original-Swift dual-CPU receipt."""
from pathlib import Path
import argparse,copy,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qa_launch_conditions import load,Reader,_source_qa_proof
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--package-result',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    package=json.loads(a.package_result.read_bytes());case=package['case'];project=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype');stage=Path(case['app'])/'Contents/Resources/bbport'
    receipt=load(Path(case['data'])/'out/launch-conditions.json',stage=stage,project=project)
    assert receipt['environment']['BB_CPU_BACKEND']=='bbcpu'
    manifest=json.loads((stage.parent/'ARM64-QA-MANIFEST.json').read_bytes());parity=json.loads((stage/'SOURCE-PARITY.json').read_bytes())
    refused=[]
    for label in('outside-source-root','missing-base-role','changed-base-identity','extra-execution-role','false-original-script'):
        m=copy.deepcopy(manifest);original=dict(receipt['original_scripts'])
        if label=='outside-source-root':m['source_app']='/Applications/Blood Echoes.app'
        elif label=='missing-base-role':m['original_current_runtime'].pop('bin/bb-probe-metal')
        elif label=='changed-base-identity':m['original_current_runtime']['bin/bb-probe-metal']='0'*64
        elif label=='extra-execution-role':m['frozen_runtime']['bin/unadmitted-extra']='0'*64
        elif label=='false-original-script':original['run.sh']='0'*64
        try:_source_qa_proof(Reader(),stage,project,m,parity,original,dict(receipt['effective_scripts']),dict(receipt['identity']))
        except ValueError:refused.append(label)
        else:raise AssertionError('unsafe source QA accepted: '+label)
    result=dict(status='passed',source_original_Swift_receipt_verified=True,CPU_backend='bbcpu',runtime_roles=len(receipt['runtime']),refused=refused,all_tests_read_only=True,game_started_by_test=False)
    assert not a.output.exists();a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
