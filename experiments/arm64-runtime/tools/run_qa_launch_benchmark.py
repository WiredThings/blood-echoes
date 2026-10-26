"""Guarded scene sampling from the original Swift launch receipt, not preferences.

Only Continue is automated before a scene camera is observed. No exploration,
combat, screenshots, profiling or changes to original data are performed.
"""
from pathlib import Path
import argparse,collections,csv,hashlib,json,math,os,signal,statistics,subprocess,sys,time
from native_observer import NativeObserver
from qa_memory_policy import MemoryPolicy
from diagnostic_frame_tail import read_tail
from qa_build_guard import require_no_games
from run_game import usage
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
def sha(path):
 with Path(path).open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def stop_signal(number,frame):
 # Raising enters the existing finally block, which reaps only this run's tree.
 raise InterruptedError('benchmark supervisor received signal '+str(number))
def scene_seen(rows):
 for row in rows[-30:]:
  try:
   if int(row['draws'])>100 and all(row.get(k)and math.isfinite(float(row[k]))for k in('cam_x','cam_y','cam_z')):return True
  except(ValueError,TypeError,KeyError):pass
 return False
def deadline(elapsed,ready,startup,scene):
 if ready is None:return 'startup time budget'if elapsed>=startup else None
 return 'scene sample completed'if elapsed-ready>=scene else None

def scene_summary(frames,samples,ready,scene_seconds):
 """Bounded post-stop telemetry only; reject menu or broken clocks as results."""
 tail=collections.deque(maxlen=20000)
 previous=None
 with frames.open()as stream:
  reader=csv.DictReader(stream)
  required={'t_s','frame_ms','draws','gpu_ms','cam_x','cam_y','cam_z'}
  if not required<=set(reader.fieldnames or ()):raise ValueError('incomplete frame schema')
  for row in reader:
   try:
    t=float(row['t_s']);ms=float(row['frame_ms']);gpu=float(row['gpu_ms']);draws=int(row['draws'])
   except(ValueError,TypeError,KeyError):continue
   if not all(math.isfinite(v)for v in(t,ms,gpu))or ms<=0 or gpu<0 or draws<0:continue
   if previous is not None and t<previous:raise ValueError('frame clock moved backwards')
   previous=t;tail.append(row)
   while tail and float(tail[0]['t_s'])<t-30:tail.popleft()
 if len(tail)<100:raise ValueError('insufficient complete scene frames')
 if sum(scene_seen([r])for r in tail)<len(tail)*.9:raise ValueError('frame tail is not a stable scene camera')
 times=sorted(float(r['frame_ms'])for r in tail)
 stable=[s for s in samples if s['seconds']>=ready+scene_seconds-30]
 return dict(frames=len(tail),fps=1000/statistics.mean(times),
  p95_ms=times[int((len(times)-1)*.95)],p99_ms=times[int((len(times)-1)*.99)],
  gpu_ms=statistics.mean(float(r['gpu_ms'])for r in tail),
  draws_median=statistics.median(int(r['draws'])for r in tail),
  camera={k:tail[-1].get(k)for k in('cam_x','cam_y','cam_z')},
  combined_footprint_mib=statistics.median(sum(u['footprint_bytes']for u in s['usage'])/2**20 for s in stable)if stable else None)

def admit(conditions):
 from gpu_candidate_identity import verify
 from warm_archive_manifest import resolve_gpu_identity
 from qa_gpu_cache_seed import validate
 stage=conditions['stage']
 for rel,path in conditions['runtime'].items():
  if sha(path)!=conditions['runtime_sha256'][rel]:raise ValueError('packaged runtime changed')
 runtime=conditions['runtime'];hashes=conditions['runtime_sha256']
 gpu=verify(dict(target=str(runtime['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu']),library=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib']),library_sha256=hashes['bin/gpu-metal-arm64/libbbgpu.dylib']),stage)
 cpu=resolve_gpu_identity(runtime['bin/bb-probe-metal'],stage)
 if cpu['library']!=str(runtime['bin/gpu-metal/libbbgpu.dylib'])or cpu['library_sha256']!=hashes['bin/gpu-metal/libbbgpu.dylib']:raise ValueError('CPU actual host differs')
 manifest=stage.parent/'ARM64-QA-MANIFEST.json';m=json.loads(manifest.read_text())
 cache=validate(manifest,project=P,expected_upstream_gpu_sha256=m['upstream_inputs']['gpu_library'])
 return dict(cpu=cpu,gpu=gpu),cache

def sample(c,case,env,command,report,root,*,startup_seconds,scene_seconds,observer,policy,protection_start):
 from qa_launch_clone import inventory_savedata
 before=observer.system()
 if before['locked']or before['pressure']==4:raise RuntimeError('locked or severe pressure before run')
 write(root/'result.json',report);print('Run:',root,flush=True)
 started=time.monotonic();tracked={};samples=collections.deque(maxlen=1024);peak_pressure=before['pressure'];ready=None;last_press=-100;pressed=False;reason=None;next_frames=0;last_frame_rows=[];proc=None
 pad=case['pad'];frames=case['data_root']/'out/frames.csv'
 def input_(value):
  temporary=pad.with_name(pad.name+'.next');assert not temporary.exists();temporary.write_text(value);os.replace(temporary,pad)
 def descendants():
  pending=[proc.pid];seen=set();rows={}
  while pending:
   parent=pending.pop()
   if parent in seen:continue
   seen.add(parent);assert len(seen)<4096
   for pid,ppid,path in observer.processes(parent):
    rows[pid]=(pid,ppid,path);tracked[pid]=path
    if pid!=parent and pid not in seen:pending.append(pid)
  return list(rows.values())
 def stop():
  if proc is None:return
  if proc.poll()is None:proc.terminate()
  try:proc.wait(timeout=3)
  except subprocess.TimeoutExpired:pass
  for sig in(signal.SIGTERM,signal.SIGKILL):
   for pid,path in reversed(list(tracked.items())):
    if observer.path(pid)==path:
     try:os.kill(pid,sig)
     except ProcessLookupError:pass
   if sig==signal.SIGTERM:time.sleep(.3)
  if proc.poll()is None:proc.kill();proc.wait()
 try:
  if frames.exists():raise ValueError('old frame log was not archived before new run')
  with(root/'console.log').open('w')as log,(root/'samples.jsonl').open('w')as sample_log:
   proc=subprocess.Popen(command,cwd=c['stage'],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
   while proc.poll()is None:
    now=time.monotonic();elapsed=now-started;rows=descendants();s=observer.system();s.update(seconds=elapsed,processes=rows,usage=usage([f'{i} {j} 0 {path}'for i,j,path in rows]));samples.append(s);peak_pressure=max(peak_pressure,s['pressure']);sample_log.write(json.dumps(s,separators=(',',':'))+'\n');sample_log.flush()
    reason=policy.observe(now-protection_start,s)
    if pressed and now-last_press>=.18:input_('');pressed=False
    if ready is None and now>=next_frames:
     next_frames=now+1
     if frames.exists():
      try:last_frame_rows=read_tail(frames)
      except ValueError:last_frame_rows=[]
     if scene_seen(last_frame_rows):ready=elapsed;print('Scene camera observed:',round(ready,2),flush=True)
     elif last_frame_rows and elapsed>=12 and now-last_press>=4:
      input_('cross\n');pressed=True;last_press=now
      with(root/'actions.jsonl').open('a')as f:f.write(json.dumps(dict(action='cross',seconds=elapsed,reason='Continue before scene camera'))+'\n')
    reason=reason or deadline(elapsed,ready,startup_seconds,scene_seconds)
    if reason:break
    time.sleep(.25)
   if reason is None:reason='unexpected process exit'
 except BaseException as error:
  report['error']=str(error)[:512];reason=reason or 'driver exception';raise
 finally:
  input_('');stop();after=observer.system()
  source_same=inventory_savedata(c['user_root']/'savedata')==case['source_save_manifest']
  report.update(status='sampled'if reason=='scene sample completed'and source_same else'incomplete',stop=reason,process_exit=proc.returncode if proc else None,seconds=time.monotonic()-started,scene_ready=ready,before=before,after=after,peak_pressure=peak_pressure,swap_growth_mib=after['swap_mib']-before['swap_mib'],source_save_unchanged=source_same,memory_policy=MemoryPolicy.MANIFEST)
  if report['status']=='sampled':
   try:report['scene_sample']=scene_summary(frames,samples,ready,scene_seconds)
   except ValueError as error:report.update(status='incomplete',error=str(error))
  report['qualification']='Original Swift frozen conditions; Continue only. Scene camera is not full gameplay/visual/network/F3 acceptance. Telemetry has overhead; combined footprints may double-count shared memory. No matched old/new performance or sustained-memory conclusion.'
  if frames.exists():os.replace(frames,root/'frames.csv')
  write(root/'result.json',report);print(json.dumps({k:v for k,v in report.items()if k not in('native_identity','qualification')},indent=2),flush=True)
 return report

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--receipt',type=Path,required=True);ap.add_argument('--stage',type=Path,required=True);ap.add_argument('--startup-seconds',type=int,default=1800);ap.add_argument('--scene-seconds',type=int,default=90);ap.add_argument('--cache-mode',choices=('seeded','empty'),default='seeded');ap.add_argument('--runs',type=int,choices=(1,2),default=1);a=ap.parse_args()
 assert 60<=a.startup_seconds<=1800 and 45<=a.scene_seconds<=180
 from qa_launch_conditions import load
 from qa_launch_clone import create
 c=load(a.receipt,stage=a.stage,project=P);require_no_games()
 status=subprocess.run(['/Users/k.d./.local/bin/cua-driver','status'],capture_output=True,text=True,timeout=10)
 assert 'daemon is not running'in(status.stdout+status.stderr).lower(),'Cua must be stopped'
 native,cache_admission=admit(c)
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4
 tag=str(time.time_ns());root=P/'evidence'/('original-launch-benchmark-'+tag);root.mkdir(mode=0o700)
 case=create(c,cache_admission,project=P,tag=tag,cache_mode=a.cache_mode)
 env=dict(PATH='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(P/'state'),TMPDIR=str(case['data_root'])+'/',PYTHON=str(c['stage']/'python/bin/python3'),PYTHONHOME=str(c['stage']/'python'),PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',**case['environment'])
 # Use the original explicit benchmark entry. Normal Play intentionally strips
 # scripted input/log-only telemetry. That entry also requires quicksave off;
 # log this control difference, never silently call it identical UI behavior.
 if env.get('BB_FRAME_STATS','0')=='1' or env.get('BB_SAVE_LOG','0')=='1':raise ValueError('unprofiled benchmark requires original heavy statistics off')
 env.update(BB_PAD_FILE=str(case['pad']),BB_PAD_FILE_ONLY='1',BB_QUICKSAVE='0')
 actual_changes={k:dict(before=c['environment'].get(k),after=env.get(k))for k in sorted(set(c['environment'])|{k for k in env if k.startswith(('BB_','MTL_'))})if c['environment'].get(k)!=env.get(k)}
 allowed_changes=sorted(set(case['allowed_changes'])|{'BB_QUICKSAVE','BB_PAD_FILE_ONLY'})
 assert set(actual_changes)<=set(allowed_changes),'unapproved game setting override'
 command=[c['executable'],str(c['stage']/c['arguments'][0]),'--benchmark']
 report=dict(schema=1,status='running',receipt_sha256=sha(a.receipt),launch_id=c['launch_id'],native_identity=native,original_shell_entry_preserved=True,original_benchmark_flag_added=True,benchmark_control_overrides=dict(BB_QUICKSAVE='0',BB_PAD_FILE_ONLY='1'),cache_mode=a.cache_mode,silent=True,screen_capture=False,Cua=False,profiler=False,scripted_actions=['Continue until real scene camera'],formal_data_modified=False,source_save_unchanged=False,settings_sha256=case['config_sha256'],startup_budget_seconds=a.startup_seconds,scene_seconds=a.scene_seconds,quality_or_effects_changed=False)
 write(root/'conditions.json',dict(command=command,cwd=str(c['stage']),environment=env,source_receipt=str(a.receipt),data=str(case['data_root']),user=str(case['user_root']),cache=str(case['cache_root']),pad=str(case['pad']),allowed_changes=allowed_changes,actual_changes=actual_changes,cache_clone=case['cache_clone']))
 write(root/'result.json',report);print('Evidence:',root,flush=True)
 protection_start=time.monotonic();policy=MemoryPolicy(before)
 reports=[]
 previous_handlers={sig:signal.signal(sig,stop_signal)for sig in(signal.SIGTERM,signal.SIGINT)}
 try:
  for index in range(a.runs):
   require_no_games()
   run=root/('run-'+str(index+1));run.mkdir(mode=0o700)
   row=dict(report,run=index+1,phase='initial'if index==0 else'warm',
       same_private_cache_and_user=index>0)
   result=sample(c,case,env,command,row,run,startup_seconds=a.startup_seconds,
       scene_seconds=a.scene_seconds,observer=observer,policy=policy,protection_start=protection_start)
   reports.append(result)
   write(root/'result.json',dict(schema=2,status='sampled'if all(r['status']=='sampled'for r in reports)and len(reports)==a.runs else'incomplete',runs=reports,formal_data_modified=False,memory_policy_continuous=True))
   if result['status']!='sampled':raise SystemExit(1)
 finally:
  observer.close()
  for sig,handler in previous_handlers.items():signal.signal(sig,handler)
if __name__=='__main__':main()
