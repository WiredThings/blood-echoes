from pathlib import Path
import sys,json,time,shutil,subprocess,argparse,hashlib
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from analyze_warm_archive_cases import analyze
ap=argparse.ArgumentParser();ap.add_argument('--cohort',type=Path);ap.add_argument('--phase',choices=['capture','A1','B1','B2','A2'],required=True);a=ap.parse_args()
require_no_games();o=NativeObserver()
try:
 before=o.system();assert not before['locked']and before['pressure']!=4
finally:o.close()
if a.cohort is None:
 assert a.phase=='capture'
 stamp=str(time.time_ns());root=P/'evidence'/('local-aot-archive-matrix-'+stamp);root.mkdir()
 cache=P/'state'/('gpu-local-aot-archive-'+stamp)
 source=P/'state/gpu-warm-persistence-1792922764597508000'
 assert source.is_dir()and not cache.exists()and not any(x.is_symlink()for x in source.rglob('*'))
 shutil.copytree(source,cache)
 pad=P/'state'/('local-aot-archive-idle-'+stamp+'.txt');pad.write_text('')
 manifest=dict(schema=1,root=str(root),cache=str(cache),source_private_cache=str(source),pad=str(pad),GPU_manifest=str(P/'evidence/gpu-candidate-app-1792928432049839000.json'),source_private_session=str(P/'state/game-1792762608095377000'),phases=[],quality='same fixed QA settings/native1920x1080; no changes',input='empty file; no movement/attack; no Cua/recording',offscreen_capture=False)
 (root/'cohort.json').write_text(json.dumps(manifest,indent=2)+'\n');(P/'evidence/local-aot-archive-matrix-current.json').write_text(json.dumps(dict(root=str(root)),indent=2)+'\n')
else:
 root=a.cohort.resolve(strict=True);assert root.parent==P/'evidence'and root.name.startswith('local-aot-archive-matrix-')
 manifest=json.loads((root/'cohort.json').read_text())
expected=['capture','A1','B1','B2','A2'];assert a.phase==expected[len(manifest['phases'])]
mode='record'if a.phase=='capture'else'load'if a.phase.startswith('B')else'off'
cmd=[sys.executable,str(P/'tools/run_game.py'),'--engine','arm64','--graphics','--boot','/Users/Shared/BloodEchoesDevelopment/assets/boot-linked.bin','--app0','/Users/Shared/BloodEchoesDevelopment/assets/CUSA03173-1.09','--content-profile','/Users/Shared/BloodEchoesDevelopment/assets/content.bin','--clone-private-session',manifest['source_private_session'],'--seed-user','/Users/Shared/BloodEchoesDevelopment/qa-seed-user','--quicksave','--native-observer','--packed-veneers','--resource-hooks','--resource-fast-dispatch','--resource-leaf-dispatch','--extra-heaps','--gpu-state',manifest['cache'],'--gpu-candidate',manifest['GPU_manifest'],'--pad-file',manifest['pad'],'--compile-audit','--frame-timing','--pipeline-archive','--local-aot',mode,'--seconds','75']
old={x for x in(P/'evidence').glob('game-*')if x.is_dir()};start=time.time_ns()
(root/(a.phase+'-command.json')).write_text(json.dumps(dict(command=cmd,started_ns=start,preflight=before),indent=2)+'\n')
with(root/(a.phase+'-console.log')).open('w')as log:
 process=subprocess.run(cmd,cwd=P,stdout=log,stderr=subprocess.STDOUT)
new={x for x in(P/'evidence').glob('game-*')if x.is_dir()}-old
assert len(new)==1,new
case=new.pop();entry=dict(phase=a.phase,local_aot=mode,case=str(case),exit=process.returncode,elapsed_seconds=(time.time_ns()-start)/1e9)
if(case/'result.json').exists():entry['analysis']=analyze(case)
manifest['phases'].append(entry);(root/'cohort.json').write_text(json.dumps(manifest,indent=2)+'\n')
summary={k:v for k,v in entry.items()if k!='analysis'}
if'analysis'in entry:
 x=entry['analysis'];summary.update({k:x[k]for k in['complete','compilation','frame_metrics','pressure_max','swap_change_mib','local_aot_events','local_aot_policy']})
print(json.dumps(dict(cohort=str(root),**summary),indent=2),flush=True)
assert process.returncode==0 and entry.get('analysis',{}).get('complete'), 'incomplete run; do not auto-advance'
