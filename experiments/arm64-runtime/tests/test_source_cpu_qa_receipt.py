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
    from run_qa_launch_benchmark import admit
    native,cache=admit(receipt)
    assert native['cpu_backend']=='bbcpu'and native['cpu']['target']==str(stage/'bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal')
    assert cache['source']==str(receipt['cache_root'])
    backend_refused=[]
    for label in('unknown-CPU','FEX-role-instead-of-selected-bbcpu'):
        bad=dict(receipt)
        if label=='unknown-CPU':bad['environment']={**receipt['environment'],'BB_CPU_BACKEND':'unknown'}
        else:
            role='bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal'
            bad['runtime']={**receipt['runtime'],role:receipt['runtime']['bin/bb-probe-metal']}
            bad['runtime_sha256']={**receipt['runtime_sha256'],role:receipt['runtime_sha256']['bin/bb-probe-metal']}
        try:admit(bad)
        except ValueError:backend_refused.append(label)
        else:raise AssertionError('benchmark silently admitted wrong CPU: '+label)
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
    result=dict(status='passed',source_original_Swift_receipt_verified=True,CPU_backend='bbcpu',runtime_roles=len(receipt['runtime']),actual_selected_CPU_and_Metal_edges_verified=True,refused=refused,wrong_backend_refused=backend_refused,all_tests_read_only=True,game_started_by_test=False)
    assert not a.output.exists();a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
