"""CPU-only ABBA through immutable private QA bundles; source/runtime manifest checks.

No production app, data or settings. Both GPU and all original entry scripts must
match. Does not provide death/online/white-model or long-term leak acceptance.
"""
from pathlib import Path
import argparse,hashlib,json,subprocess,time,sys,re,statistics
P=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P/'tools'))
from qa_build_guard import require_no_games
parser=argparse.ArgumentParser();parser.add_argument('--baseline',required=True,type=Path);parser.add_argument('--candidate',required=True,type=Path);parser.add_argument('--seconds',type=int,choices=[90,120],default=90);args=parser.parse_args()
require_no_games()
apps={'old':args.baseline.absolute(),'new':args.candidate.absolute()}
assert all(a==a.resolve()and a.is_relative_to(P/'apps')and a.suffix=='.app'for a in apps.values()),'direct private frozen QA apps only'

stages={k:v/'Contents/Resources/bbport'for k,v in apps.items()}
identity={}
for k,stage in stages.items():
 m=json.loads((stage.parent/'ARM64-QA-MANIFEST.json').read_text());assert m['formal_data_modified']is False
 assert all(not(stage/n).is_symlink()and(stage/n).resolve().is_relative_to(apps[k])for n in m['frozen_runtime'])
 assert all(hashlib.file_digest((stage/n).open('rb'),'sha256').hexdigest()==h for n,h in m['frozen_runtime'].items())
 identity[k]=m
assert identity['old']['original_scripts']==identity['new']['original_scripts']
assert all(identity['old']['frozen_runtime'][k]==identity['new']['frozen_runtime'][k]for k in identity['old']['frozen_runtime']if k!='bin/bb-probe-metal')
e=P/'evidence'/('frozen-cpu-memory-abba-'+str(time.time_ns()));e.mkdir();print(e,flush=True)
r=dict(passed=False,order=['old','new','new','old'],runtime_identity={k:v['upstream_inputs']for k,v in identity.items()},runs=[],formal_data_modified=False,quality_changed=False,screen_capture=False)
for i,key in enumerate(r['order']):
 log=e/(str(i)+'-'+key+'.log');cmd=[sys.executable,str(P/'tools/run_launcher_engine_abba.py'),'--order','arm64','--seconds',str(args.seconds),'--component-stage',str(stages[key])]
 with log.open('w')as f:result=subprocess.run(cmd,cwd=P,stdout=f,stderr=subprocess.STDOUT)
 if result.returncode:
  r['error']=dict(run=i,status=result.returncode,log=str(log));(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');raise SystemExit(1)
 text=log.read_text();match=re.search(r'^Evidence: (.+)$',text,re.M);assert match;case=Path(match[1]);report=json.loads((case/'result.json').read_text());assert report['passed']and len(report['runs'])==1
 row=report['runs'][0];row.update(cohort=key,source_case=str(case),index=i)
 categories={}
 for line in (case/'0-arm64/console.log').read_text(errors='replace').splitlines():
  m=re.match(r'Metal memory category ([^:]+): allocated=(\d+) bytes, objects=(\d+)',line)
  if m:categories[m[1]]=dict(allocated_bytes=int(m[2]),objects=int(m[3]))
 raw=json.loads((case/'0-arm64/run.json').read_text());row['critical_samples']=sum(x['pressure']==4 for x in raw['samples']);row['categories_last_report']=categories
 r['runs'].append(row);(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(i,key,round(row['fps'],2),round(row['combined_footprint_mib'],2),row['critical_samples'],flush=True)
keys=['boot_sha256','content_sha256','patch_sha256','settings_sha256','camera']
assert all(all(row[k]==r['runs'][0][k]for k in keys)for row in r['runs'])
r['matching_input_fields']=keys;r['summary']={}
for key in apps:
 rows=[x for x in r['runs']if x['cohort']==key];r['summary'][key]=dict(fps_mean=statistics.mean(x['fps']for x in rows),aggregate_footprint_mib_mean=statistics.mean(x['combined_footprint_mib']for x in rows),footprint_runs=[x['combined_footprint_mib']for x in rows],critical_samples=sum(x['critical_samples']for x in rows))
r['passed']=True;r['qualification']='Matched frozen CPU-only ABBA, no diagnostic captures. Aggregate footprint duplicates shared pages; four static runs cannot prove long-term leak freedom or gameplay white-model correctness.'
(e/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r['summary'],indent=2),flush=True)
