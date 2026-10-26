"""One <=240s isolated native batch/single PSO control. Never launches a game.
Only raw .bin/.meta/.spv/.key cache files are copied, <=512 MiB, no links.
"""
from pathlib import Path
import argparse,collections,hashlib,json,os,plistlib,shutil,stat,subprocess,sys,threading,time
D=Path(__file__).resolve().parents[1]
import game_pso_batch,prepare_game_psos,game_pso_candidates
from game_pso_owned_audit import load_canonical_layout
from decode_shader_cache import semantic_profile
from warm_archive_manifest import make_manifest,resolve_gpu_identity
from native_observer import NativeObserver
from process_schedule_audit import ProcessScheduleAudit
from qa_memory_policy import MemoryPolicy
from qa_build_guard import require_no_games

def need(v,m):
 if not v:raise ValueError(m)
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,v):
 with Path(p).open('w')as f:json.dump(v,f,indent=2);f.write('\n')
def raw_inventory(root):
 need(root==root.resolve(strict=True)and root.name.startswith('CUSA03173-metal-'),'canonical raw namespace leaf required');rows={};ignored=[];total=0
 for p in sorted(root.iterdir()):
  s=p.lstat();need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and s.st_nlink==1 and not p.is_symlink(),'raw cache rejects links/nonfiles')
  if p.suffix not in('.bin','.meta','.spv','.key'):ignored.append(p.name);continue
  need(0<s.st_size<=1024*1024,'bounded raw cache input');total+=s.st_size;need(total<=512*1024*1024 and len(rows)<20000,'bounded raw-only copy');rows[p.name]=dict(sha256=sha(p),bytes=s.st_size)
 return dict(files=rows,bytes=total,ignored_nonraw=len(ignored))
def signature(row):
 w=row['worker'];need(row['exit']==0 and prepare_game_psos.preparation_ready(row)and w['source_fallback']is False,'actual native ready')
 return dict(family=w['family'],functions=sorted(w['functions'],key=lambda f:f['label']),events=sorted(({k:v for k,v in e.items()if k!='state'}for e in row['artifact_events']),key=lambda e:e['key']))

def main():
 ap=argparse.ArgumentParser();
 for k in('project','source-cache','worker','library','layout','source-root','evidence','qa-preferences','qa-outcomes'):ap.add_argument('--'+k,type=Path,required=True)
 ap.add_argument('--seconds',type=float,default=240);a=ap.parse_args();need(0<a.seconds<=240,'one bounded native test');P=a.project.resolve(strict=True);E=a.evidence.absolute();need(E.parent==P/'evidence'and not E.exists(),'fresh evidence');E.mkdir(mode=0o700);gpu=P/'state'/('native-pso-batch-control-'+str(time.time_ns()));gpu.mkdir(mode=0o700);(gpu/'cache').mkdir();started=time.monotonic();observer=NativeObserver();before=observer.system();need(not before['locked']and before['pressure']!=4,'safe unlocked preflight');policy=MemoryPolicy(before);require_no_games();stop=threading.Event();phase={'name':'setup'};samples=[];rss_errors=[];thread=None;report=dict(status='started',evidence=str(E),gpu_root=str(gpu),game_started=False,audio_started=False,formal_data_modified=False,source_cache=str(a.source_cache),before=before,cases={})
 def save():write(E/'result.json',report)
 def check():
  s=observer.system();reason=policy.observe(time.monotonic()-started,s)
  if reason:raise RuntimeError('native control protection: '+reason)
  need(time.monotonic()-started<a.seconds,'native control global deadline');require_no_games()
 def sample():
  own=NativeObserver();usage=ProcessScheduleAudit()
  try:
   while not stop.wait(.05):
    try:
     pids=[pid for pid,parent,path in own.processes(os.getpid())if Path(path).resolve()==a.worker.resolve()]
     for row in usage.sample(pids):
      value=dict(seconds=time.monotonic()-started,phase=phase['name'],pid=row['pid'],start=row['process_start_mach'],resident_bytes=row['counters']['resident_size'],footprint_bytes=row['counters']['phys_footprint'],lifetime_max_footprint_bytes=row['counters']['lifetime_max_phys_footprint']);samples.append(value)
      with(E/'rss.jsonl').open('a')as f:f.write(json.dumps(value,separators=(',',':'))+'\n')
    except Exception as error:rss_errors.append(str(error));break
  finally:own.close()
 try:
  report['actual_probe_capability']=game_pso_batch.probe_capability(a.worker,P,check);need(report['actual_probe_capability'],'actual private-state batch capability unavailable');save();print('ACTUAL_CAPABILITY_PASSED',flush=True)
  layout=load_canonical_layout(a.layout,a.source_root,sha(a.layout));identity=resolve_gpu_identity(a.worker,P);need(identity['library_sha256']==sha(a.library),'normal same-DSO edge');report['worker_identity']=identity;report['layout_sha256']=sha(a.layout);report['worker_sha256']=sha(a.worker)
  inventory=raw_inventory(a.source_cache);report['raw_copy']=dict(bytes=inventory['bytes'],files=len(inventory['files']),ignored_nonraw=inventory['ignored_nonraw'],limit=512*1024*1024,source_views_copied=False);cache=gpu/'cache'/a.source_cache.name;cache.mkdir()
  for name,row in inventory['files'].items():check();shutil.copy2(a.source_cache/name,cache/name);need(sha(cache/name)==row['sha256'],'copied raw SHA')
  manifest=E/'all-candidates.jsonl';report['candidates']=game_pso_candidates.build(gpu_root=gpu,title_id='CUSA03173',layout=layout,output=manifest,check=check)
  # Prior actual native family labels select coverage only. New worker re-derives
  # every descriptor/function and archive key; labels never authorize a recipe.
  old_family={r['recipe']:r['worker']['family']for r in map(json.loads,a.qa_outcomes.read_text().splitlines())}
  names={r['key']['reference']['name']:old_family[r['recipe_blob_identity']]for r in game_pso_candidates.cache_records(a.source_cache,layout,check)if r.get('record_type')=='offline-pso-input'and r['recipe_blob_identity']in old_family}
  eligible=[];excluded=collections.Counter()
  for r in map(json.loads,manifest.read_text().splitlines()):
   if r.get('record_type')!='offline-pso-input':continue
   if not r['execution_policy']['candidate_preparation']['policy_eligible']:excluded.update(r.get('session_rejections',[]));continue
   family=names.get(r['key']['reference']['name'],r['classification']['family']);eligible.append((family,r))
  grouped=collections.defaultdict(list)
  for family,row in eligible:grouped[family].append(row)
  selected=[]
  for family in sorted(grouped):selected.append(grouped[family].pop(0))
  while len(selected)<17 and any(grouped.values()):
   for family in sorted(grouped):
    if grouped[family]and len(selected)<17:selected.append(grouped[family].pop(0))
  need(0<len(selected)<=17,'bounded existing-family selection');chosen=E/'selected.jsonl';chosen.write_text(''.join(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n'for r in selected));report['selection']=dict(recipes=len(selected),family_hints=dict(collections.Counter(names.get(r['key']['reference']['name'],r['classification']['family'])for r in selected)),source='exact cache-local recipe/key join to prior native family labels; selection only',excluded=dict(excluded),owned_GS_admitted=False)
  prefs=plistlib.loads(a.qa_preferences.read_bytes());extra=dict(line.split('=',1)for line in prefs.get('extraEnv','').splitlines()if'='in line);env=prepare_game_psos.compile_environment(extra);game=Path(prefs['gameFolder']).resolve(strict=True);profile=semantic_profile((cache/'profile.bin').read_bytes(),layout)
  cohort,cohort_manifest=make_manifest(translator_artifacts=dict(worker=a.worker,gpu_library=a.library,layout=a.layout),game_artifacts=dict(eboot=game/'eboot.bin',sfo=game/'sce_sys/param.sfo'),options=env,profile=profile['fields']);write(E/'cohort.json',cohort_manifest);report['cohort']=cohort;artifact=gpu/'cache/CUSA03173/metal';artifact.mkdir(parents=True);thread=threading.Thread(target=sample,daemon=True);thread.start();save();print('SELECTION',report['selection'],flush=True)
  for label,legacy in [('cold-batch',False),('warm-batch',False),('warm-single',True)]:
   check();phase['name']=label;run=P/'state'/('game-pso-native-'+label+'-'+str(time.time_ns()));run.mkdir(mode=0o700);seconds=min(a.seconds-(time.monotonic()-started),180);need(seconds>10,'remaining native test budget');config=dict(project=P,worker=a.worker,manifest=chosen,layout=a.layout,source_root=a.source_root,gpu_root=gpu,run_root=run,artifact_cache_dir=artifact,cohort=cohort,library_sha256=sha(a.library),layout_sha256=sha(a.layout),environment=env,seconds=seconds,page_size=16,retain_worker_scratch=False)
   print('START',label,flush=True);saved=game_pso_batch.probe_capability
   if legacy:game_pso_batch.probe_capability=lambda *args,**kwargs:False
   try:r=prepare_game_psos.prepare(config)
   finally:game_pso_batch.probe_capability=saved
   report['cases'][label]=dict(status=r['status'],completed=r.get('completed'),seconds=r.get('wall_seconds'),native_batch=r.get('native_batch'),outcomes_jsonl=r['outcomes_jsonl'],result=str(run/'result.json'),artifact_hits=r.get('verified_artifact_hits'),protection_stops=r.get('protection_stops'),before=r['before'],after=r['after']);save();print('DONE',label,report['cases'][label],flush=True);need(r['status']=='completed'and r['completed']==len(selected),'native stage partial/rejected; stop rather than infer success')
  signatures={}
  for label,case in report['cases'].items():
   actual=[r for r in map(json.loads,Path(case['outcomes_jsonl']).read_text().splitlines())if r['phase']=='prepare'];need(len(actual)==len(selected),'one native outcome per recipe');signatures[label]={r['recipe']:signature(r)for r in actual}
  need(signatures['cold-batch']==signatures['warm-batch']==signatures['warm-single'],'native descriptor/function/all artifact identities differ');report['equivalence']=dict(passed=True,recipes=len(selected),events=sum(len(r['events'])for r in signatures['warm-batch'].values()),families=dict(collections.Counter(r['family']for r in signatures['warm-batch'].values())),compared='per recipe full native family/function/descriptor/primary/fragment/route/key/archive SHA; state built/hit kept separately');write(E/'equivalence.json',signatures)
  check();need(raw_inventory(a.source_cache)==inventory,'old raw cache changed');report['source_cache_unchanged']=True;report['status']='passed-native-batch-single-controls'
 except BaseException as error:report.update(status='stopped'if isinstance(error,KeyboardInterrupt)else'failed',error=str(error));print('CONTROL_ERROR',str(error),flush=True)
 finally:
  stop.set()
  if thread:thread.join(timeout=2)
  report.update(seconds=time.monotonic()-started,after=observer.system(),rss_errors=rss_errors,rss={label:dict(samples=sum(x['phase']==label for x in samples),max_rss_mib=max((x['resident_bytes']/2**20 for x in samples if x['phase']==label),default=0),max_footprint_mib=max((x['footprint_bytes']/2**20 for x in samples if x['phase']==label),default=0),max_lifetime_footprint_mib=max((x['lifetime_max_footprint_bytes']/2**20 for x in samples if x['phase']==label),default=0))for label in report['cases']});save();observer.close();print('RESULT',E/'result.json',report['status'],flush=True)
 return 0 if report['status']=='passed-native-batch-single-controls'else 65
if __name__=='__main__':raise SystemExit(main())
