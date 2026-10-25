"""Read-only, explicitly qualified archive/compile/frame analysis of private game cases."""
from pathlib import Path
import argparse,collections,csv,hashlib,json,re,statistics
P=Path(__file__).resolve().parents[1]
def digest(f):
 with f.open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def analyze(case):
 case=Path(case).resolve(strict=True);assert case.parent==P/'evidence'and case.name.startswith('game-')
 raw=json.loads((case/'result.json').read_text());text=(case/'runtime.log').read_text(errors='replace');q=collections.defaultdict(list)
 for m in re.finditer(r'Metal compile audit: kind=(\w+) id=([0-9a-f]+) start-ns=(\d+) elapsed-us=(\d+) ok=(\d+) cached=(-?\d+)',text):
  q[m[1]].append(dict(start_ns=int(m[3]),us=int(m[4]),ok=int(m[5]),cached=int(m[6])))
 compilation={k:dict(events=len(v),total_ms=sum(x['us']for x in v)/1000,cache_hits=sum(x['cached']==1 for x in v),max_ms=max(x['us']for x in v)/1000,failures=sum(not x['ok']for x in v))for k,v in q.items()}
 samples=raw['samples'];assert samples
 result=dict(case=str(case),exit=raw['exit'],stopped=raw['stopped'],seconds=raw['seconds'],cleanup_warnings=raw['cleanup_warnings'],complete=raw['exit']==0 and raw['stopped']=='time budget'and not raw['cleanup_warnings']and not raw['generations'] and text.count('character ready; automatic menu input released')==1,ready_markers=text.count('character ready; automatic menu input released'),compilation=compilation,compile_log_truncated='Metal compile audit: truncated=1'in text,pressure_max=max(x['pressure']for x in samples),swap_change_mib=samples[-1]['swap_mib']-samples[0]['swap_mib'],peak_aggregate_footprint_mib=max(sum(u['footprint_bytes']for u in s['usage'])/2**20 for s in samples),startup_sha256=digest(case/'startup.bin'),settings_sha256=digest(Path(raw['state'])/'bbport.ini'),GPU_identity=json.loads((case/'gpu-candidate-identity.json').read_text()),frame_metrics=None)
 result['local_aot_policy']=json.loads((case/'local-aot-policy.json').read_text())if(case/'local-aot-policy.json').exists()else dict(status='off')
 result['local_aot_events']=dict(hits=len(re.findall(r'Metal local AOT: hit key=',text)),fallbacks=len(re.findall(r'Metal local AOT: (?!hit)',text)),recorded=text.count('recorded=1'))
 if(case/'warm-archive-compatibility.json').exists():result['archive_policy']=json.loads((case/'warm-archive-compatibility.json').read_text())
 else:result['archive_policy']=dict(mode='off')
 if not result['complete']:return result
 rows=[]
 with(case/'frames.csv').open()as stream:
  for row in csv.DictReader(stream):
   if None in row or any(v is None for v in row.values()):continue
   try:float(row['t_s']);float(row['frame_ms']);float(row['gpu_ms'])
   except(ValueError,TypeError):continue
   rows.append(row)
 assert rows;end=float(rows[-1]['t_s']);tail=[x for x in rows if float(x['t_s'])>=end-30 and float(x['frame_ms'])>0]
 values=sorted(float(x['frame_ms'])for x in tail);assert sum(values)>29000
 def pct(p):return values[int((len(values)-1)*p)]
 n=max(1,int(len(values)*.01));late=[s for s in samples if s['seconds']>=raw['seconds']-30];role=collections.defaultdict(list)
 for s in late:
  for u in s['usage']:role[(u['role'],u['pid'])].append((s['seconds'],u))
 cpu={}
 for(role_name,pid),xs in role.items():
  if len(xs)<2:continue
  (at,a),(bt,b)=xs[0],xs[-1];cpu[role_name]=100*(b['cpu_user_ns']+b['cpu_system_ns']-a['cpu_user_ns']-a['cpu_system_ns'])/1e9/(bt-at)
 result['frame_metrics']=dict(window='last30s fixed scene',frames=len(tail),fps=1000/statistics.mean(values),p95_ms=pct(.95),p99_ms=pct(.99),max_frame_ms=max(values),one_percent_low_fps=1000/statistics.mean(values[-n:]),gpu_ms=statistics.mean(float(x['gpu_ms'])for x in tail),late_compiles=sum(int(x['compiles'])for x in tail),camera=sorted({(x['cam_x'],x['cam_y'],x['cam_z'])for x in tail}),draws_median=statistics.median(float(x['draws'])for x in tail),median_aggregate_footprint_mib=statistics.median(sum(u['footprint_bytes']for u in s['usage'])/2**20 for s in late),cpu_percent=cpu)
 return result
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('cases',type=Path,nargs='+');ap.add_argument('--output',type=Path);a=ap.parse_args();rows=[analyze(f)for f in a.cases];r=dict(runs=rows,qualification='Incomplete protective stops have no accepted frame metrics. Combined footprints count shared pages more than once. Compile totals are wall scope sums, not a process startup clock. System driver cache was not cleared; cold/warm variation cannot be assigned to binary archives alone. This fixed idle scene does not prove exploration/white-model correctness; per-frame logging and startup compile audit are enabled, without Cua/recording.')
 if a.output:
  out=a.output.absolute();assert out.parent.is_relative_to(P/'evidence')and not out.exists();out.write_text(json.dumps(r,indent=2)+'\n')
 print(json.dumps([dict(case=x['case'],complete=x['complete'],stopped=x['stopped'],compilation=x['compilation'],frames=x['frame_metrics'],pressure_max=x['pressure_max'],swap_change_mib=x['swap_change_mib'])for x in rows],indent=2))
