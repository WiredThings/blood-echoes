"""Fixed-scene A/B/B/A: immutable private inputs, silent, no Cua or character actions."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,sys,time
from qa_build_guard import require_no_games
from native_observer import NativeObserver
from analyze_warm_archive_cases import analyze
P=Path(__file__).resolve().parents[1]
a=argparse.ArgumentParser();a.add_argument('--cohort',type=Path);a.add_argument('--phase',choices=['A1','B1','B2','A2','B3','A3'],required=True);a.add_argument('--candidate-app',type=Path);args=a.parse_args();require_no_games();o=NativeObserver()
try:before=o.system();assert not before['locked']and before['pressure']!=4
finally:o.close()
if args.cohort is None:
 assert args.phase=='A1'and args.candidate_app
 app=args.candidate_app.resolve(strict=True);assert app.is_relative_to(P/'evidence')
 stamp=str(time.time_ns());root=P/'evidence'/('runtime-key-game-matrix-'+stamp);root.mkdir();source=P/'state/gpu-warm-persistence-1792922764597508000';assert source.is_dir()and not any(x.is_symlink()for x in source.rglob('*'))
 caches={}
 for label in ['baseline','fixed']:
  cache=P/'state'/('gpu-runtime-key-'+label+'-'+stamp);shutil.copytree(source,cache);caches[label]=str(cache)
 common=json.loads((P/'evidence/local-aot-archive-matrix-1792929066449607000/A2-command.json').read_text())['command'];common.remove('--pipeline-archive')
 meta=dict(root=str(root),source_private_cache=str(source),caches=caches,baseline_GPU_manifest=str(P/'evidence/gpu-candidate-app-1792930120787560000.json'),fixed_GPU_manifest=str(app),common_command=common,phases=[],quality='same private native1920x1080 settings and effects',input='empty file; no character actions; zero-gain SDL; no Cua/recording',library_AOT='off',pipeline_archive='off in both')
 (root/'cohort.json').write_text(json.dumps(meta,indent=2)+'\n');(P/'evidence/runtime-key-game-matrix-current.json').write_text(json.dumps(dict(root=str(root)),indent=2)+'\n')
else:
 root=args.cohort.resolve(strict=True);assert root.parent==P/'evidence'and root.name.startswith('runtime-key-game-matrix-');meta=json.loads((root/'cohort.json').read_text())
assert args.phase==['A1','B1','B2','A2','B3','A3'][len(meta['phases'])]
variant='baseline'if args.phase.startswith('A')else'fixed';cmd=list(meta['common_command']);cmd[cmd.index('--gpu-candidate')+1]=meta[variant+'_GPU_manifest'];cmd[cmd.index('--gpu-state')+1]=meta['caches'][variant]
old={x for x in(P/'evidence').glob('game-*')if x.is_dir()};start=time.time_ns();(root/(args.phase+'-command.json')).write_text(json.dumps(dict(command=cmd,started_ns=start,preflight=before),indent=2)+'\n')
with(root/(args.phase+'-console.log')).open('w')as log:r=subprocess.run(cmd,cwd=P,stdout=log,stderr=subprocess.STDOUT)
new={x for x in(P/'evidence').glob('game-*')if x.is_dir()}-old;assert len(new)==1;case=new.pop();entry=dict(phase=args.phase,variant=variant,case=str(case),process_exit=r.returncode,elapsed_seconds=(time.time_ns()-start)/1e9)
if(case/'result.json').exists():entry['analysis']=analyze(case)
meta['phases'].append(entry);(root/'cohort.json').write_text(json.dumps(meta,indent=2)+'\n');short={k:v for k,v in entry.items()if k!='analysis'}
if'analysis'in entry:
 x=entry['analysis'];short.update({k:x[k]for k in['complete','compilation','frame_metrics','pressure_max','swap_change_mib']})
print(json.dumps(dict(cohort=str(root),**short),indent=2),flush=True);assert r.returncode==0 and entry.get('analysis',{}).get('complete'),'incomplete; do not auto-advance'
