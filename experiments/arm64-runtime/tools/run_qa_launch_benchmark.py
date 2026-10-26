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

CPU_ROLES=('bin/bb-probe-metal','bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal')
FORMAL_ROLES=frozenset((*CPU_ROLES,'bin/gpu-metal/libbbgpu.dylib',
 'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu','bin/gpu-metal-arm64/libbbgpu.dylib',
 'bin/gpu-net/libbbnet.dylib','bin/game-pso-preparer','bin/game-module-preparer'))

def cpu_paths(conditions):
 # Exact independently admitted roles. The shell chooses nested when present;
 # do not pretend the top executable was the observed nested process.
 return {str(conditions['runtime'][role])for role in CPU_ROLES if role in conditions['runtime']}

def app_seal(app,*,runner=None):
 """Whole resource envelope, not just executable signatures. No launch."""
 command=['/usr/bin/codesign','--verify','--deep','--strict',str(app)]
 try:
  result=(runner or subprocess.run)(command,capture_output=True,text=True,timeout=30)
  output=(result.stdout or'')+(result.stderr or'')
  if type(result.returncode)is not int or result.returncode or len(output)>65536:
   return dict(status='failed',command=command,exit=result.returncode,error=output[:2048]or'whole app resource seal rejected')
  return dict(status='verified',command=command,exit=0)
 except (OSError,subprocess.SubprocessError)as error:
  return dict(status='failed',command=command,error=str(error)[:2048])

def finish_app_seal(report,formal,*,runner=None):
 """Call only AFTER owned child teardown. Preserve the independent raw result."""
 result=dict(report,raw_run_status=report['status'])
 post=app_seal(formal['app'],runner=runner)
 result['whole_app_seal']=dict(pre=formal['seal'],post=post,post_after_owned_process_cleanup=True)
 if formal['seal'].get('status')!='verified'or post['status']!='verified':
  result.update(status='incomplete',seal_error='whole staged application resource seal failed')
 return result

def admit_formal(app,source,*,project=P,bundle_loader=None,gpu_verifier=None,identity_resolver=None,seal_runner=None):
 """Extra code-only admission. Original QA selection/save reader is unchanged.

 Default loaders perform real bundle byte, Mach-O ARM64, signature and declared
 load-edge checks. Test injections exercise contracts without native processes.
 """
 from qa_launch_conditions import canonical,SCRIPTS
 from launcher_runtime_profile import bundle,code_bytes
 from gpu_candidate_identity import verify
 from warm_archive_manifest import resolve_gpu_identity
 project=canonical(project,directory=True)
 if project!=P:raise ValueError('formal benchmark uses only the fixed Shared development project')
 app=canonical(app,directory=True,within=project/'evidence')
 if app.suffix!='.app':raise ValueError('formal-stage must be a new staged .app under development evidence')
 seal=app_seal(app,runner=seal_runner)
 if seal['status']!='verified':raise ValueError('whole application seal failed before run: '+seal.get('error','rejected'))
 stage=canonical(app/'Contents/Resources/bbport',directory=True,within=app)
 admitted=(bundle_loader or bundle)(stage)
 if admitted is None or admitted.stage!=stage or admitted.app!=app:raise ValueError('actual outer formal bundle admission missing')
 hashes=dict(admitted.native['frozen_runtime'])
 if set(hashes)!=FORMAL_ROLES:raise ValueError('formal stage requires exactly eight frozen actual runtime roles')
 runtime={role:canonical(stage/role,within=stage)for role in hashes}
 parity=json.loads(code_bytes(stage/'SOURCE-PARITY.json')[0])['effective_sources']
 if set(parity)!=set(SCRIPTS)or dict(source['effective_scripts'])!=parity:raise ValueError('formal stage differs from original QA receipt sixteen scripts')
 owners={'code_owners':(0,os.getuid())};cpus={}
 for role in CPU_ROLES:
  value=(identity_resolver or resolve_gpu_identity)(runtime[role],stage,**owners)
  if value.get('target')!=str(runtime[role])or value.get('target_sha256')!=hashes[role]or value.get('library')!=str(runtime['bin/gpu-metal/libbbgpu.dylib'])or value.get('library_sha256')!=hashes['bin/gpu-metal/libbbgpu.dylib']or value.get('load_edge_verified')is not True or value.get('strict_signatures')is not True or value.get('architecture')!='arm64':
   raise ValueError('formal actual CPU/host role identity differs: '+role)
  cpus[role]=value
 gpu_role='bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'
 gpu=(gpu_verifier or verify)(dict(target=str(runtime[gpu_role]),library=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib']),library_sha256=hashes['bin/gpu-metal-arm64/libbbgpu.dylib']),stage,**owners)
 if gpu.get('target')!=str(runtime[gpu_role])or gpu.get('target_sha256')!=hashes[gpu_role]or gpu.get('library')!=str(runtime['bin/gpu-metal-arm64/libbbgpu.dylib'])or gpu.get('library_sha256')!=hashes['bin/gpu-metal-arm64/libbbgpu.dylib']or gpu.get('load_edge_verified')is not True or gpu.get('strict_signatures')is not True or gpu.get('architecture')!='arm64':
  raise ValueError('formal actual GPU/DSO role identity differs')
 return dict(app=app,stage=stage,runtime=runtime,runtime_sha256=hashes,seal=seal,
  identity=dict(profile='staged-formal-bundle-isolated-CLI',app=str(app),stage=str(stage),
   native_manifest_sha256=sha(stage/'NATIVE-METAL.json'),runtime_sha256=hashes,
   effective_scripts=parity,cpu=cpus,gpu=gpu,whole_app_pre_seal=seal,formal_GUI_started=False,formal_data_read=False))

def execution_conditions(source,formal):
 if formal is None:return source
 return dict(source,stage=formal['stage'],app=formal['app'],runtime=formal['runtime'],runtime_sha256=formal['runtime_sha256'],formal_code_admission=formal)

def execution_environment(source,case,formal=None,*,functional_f3=False):
 """The already cloned original conditions, with code path changes only."""
 stage=formal['stage']if formal is not None else source['stage']
 env=dict(PATH='/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(P/'state'),TMPDIR=str(case['data_root'])+'/',PYTHON=str(stage/'python/bin/python3'),PYTHONHOME=str(stage/'python'),PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',**case['environment'])
 if formal is not None:env['BB_GPU_PROCESS']=str(formal['runtime']['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'])
 if env.get('BB_FRAME_STATS','0')=='1'or env.get('BB_SAVE_LOG','0')=='1':raise ValueError('unprofiled benchmark requires original heavy statistics off')
 if env.get('BB_AUDIO')!='none':raise ValueError('night QA requires original GUI audio=none')
 env.update(BB_PAD_FILE=str(case['pad']),BB_PAD_FILE_ONLY='1',BB_QUICKSAVE='0')
 if functional_f3:
  if source['environment'].get('BB_QUICKSAVE')!='1':raise ValueError('original GUI quicksave must already be enabled')
  env.update(BB_QUICKSAVE='1',BB_FRAME_STATS='1')
 return env

def benchmark_changes(source,case,env,*,formal=None,functional_f3=False):
 changes={k:dict(before=source['environment'].get(k),after=env.get(k))for k in sorted(set(source['environment'])|{k for k in env if k.startswith(('BB_','MTL_'))})if source['environment'].get(k)!=env.get(k)}
 allowed=set(case['allowed_changes'])|{'BB_QUICKSAVE','BB_PAD_FILE_ONLY'}|({'BB_FRAME_STATS'}if functional_f3 else set())
 if formal is not None:allowed.add('BB_GPU_PROCESS')
 if set(changes)-allowed:raise ValueError('unapproved game setting override')
 return changes,sorted(allowed)

def selected_cache_mode(requested,formal_stage):
 if formal_stage is not None and requested not in(None,'empty'):raise ValueError('formal-stage requires empty cache mode; QA shader cache import is forbidden')
 return 'empty'if formal_stage is not None else requested or'seeded'

def sample(c,case,env,command,report,root,*,startup_seconds,scene_seconds,observer,policy,protection_start,functional_f3=False):
 from qa_launch_clone import inventory_savedata
 from qa_f3_contract import F3Check,NewLogLines
 f3=F3Check()if functional_f3 else None
 fresh_logs=NewLogLines()if f3 is not None else None
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
    if (ready is None or f3 is not None)and now>=next_frames:
     next_frames=now+1
     if frames.exists():
      try:last_frame_rows=read_tail(frames)
      except ValueError:last_frame_rows=[]
     in_scene=scene_seen(last_frame_rows)
     if in_scene and ready is None:ready=elapsed;print('Scene camera observed:',round(ready,2),flush=True)
     if f3 is not None:
      log_text,drained=fresh_logs.read(root/'console.log')
      token=None
      # Consume all pre-save logs before requesting a new checkpoint. An old
      # startup confirmation must never authorize restoring that old checkpoint.
      if drained or f3.phase not in('entering','settling'):
       token=f3.step(elapsed,scene=in_scene,cpus={pid for pid,ppid,path in rows if path in cpu_paths(c)},gpus={pid for pid,ppid,path in rows if path==str(c['runtime']['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'])},log=log_text)
      if token:
       input_(token+'\n');pressed=True;last_press=now
       with(root/'actions.jsonl').open('a')as f:f.write(json.dumps(dict(action=token,seconds=elapsed,phase=f3.phase,utc_ns=time.time_ns()))+'\n')
     if ready is None and(f3 is None or f3.phase=='entering')and last_frame_rows and elapsed>=12 and now-last_press>=4:
      input_('cross\n');pressed=True;last_press=now
      with(root/'actions.jsonl').open('a')as f:f.write(json.dumps(dict(action='cross',seconds=elapsed,reason='Continue before scene camera'))+'\n')
    reason=reason or(('functional F3 completed'if f3.phase=='completed'else 'functional F3 time budget'if elapsed>=startup_seconds else None)if f3 is not None else deadline(elapsed,ready,startup_seconds,scene_seconds))
    if reason:break
    time.sleep(.25)
   if reason is None:reason='unexpected process exit'
 except BaseException as error:
  report['error']=str(error)[:512];reason=reason or 'driver exception';raise
 finally:
  input_('');stop();after=observer.system()
  source_same=inventory_savedata(c['user_root']/'savedata')==case['source_save_manifest']
  report.update(status=('functional-checked'if f3 is not None else'sampled')if reason in('scene sample completed','functional F3 completed')and source_same else'incomplete',stop=reason,process_exit=proc.returncode if proc else None,seconds=time.monotonic()-started,scene_ready=ready,before=before,after=after,peak_pressure=peak_pressure,swap_growth_mib=after['swap_mib']-before['swap_mib'],source_save_unchanged=source_same,memory_policy=MemoryPolicy.MANIFEST)
  if f3 is not None:report['functional_f3']={**vars(f3),'scripted_api_not_physical_key_test':True}
  if report['status']=='sampled':
   try:report['scene_sample']=scene_summary(frames,samples,ready,scene_seconds)
   except ValueError as error:report.update(status='incomplete',error=str(error))
  report['qualification']=('Original normal Play and original offline F3 supervisor; isolated scripted quicksave/quickload API, not a physical keyboard test. No FPS/performance or full visual/network conclusion.'if f3 is not None else'Original Swift frozen conditions; Continue only. Scene camera is not full gameplay/visual/network/F3 acceptance. Telemetry has overhead; combined footprints may double-count shared memory. No matched old/new performance or sustained-memory conclusion.')
  if c.get('formal_code_admission')is not None:
   sealed=finish_app_seal(report,c['formal_code_admission']);report.clear();report.update(sealed)
  if frames.exists():os.replace(frames,root/'frames.csv')
  write(root/'result.json',report);print(json.dumps({k:v for k,v in report.items()if k not in('native_identity','qualification')},indent=2),flush=True)
 return report

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--receipt',type=Path,required=True);ap.add_argument('--stage',type=Path,required=True);ap.add_argument('--startup-seconds',type=int,default=1800);ap.add_argument('--scene-seconds',type=int,default=90);ap.add_argument('--cache-mode',choices=('seeded','empty'));ap.add_argument('--formal-stage',type=Path);ap.add_argument('--runs',type=int,choices=(1,2),default=1);ap.add_argument('--functional-f3',action='store_true');a=ap.parse_args()
 assert 60<=a.startup_seconds<=1800 and 45<=a.scene_seconds<=180
 assert not a.functional_f3 or a.runs==1,'functional F3 is one supervised session'
 from qa_launch_conditions import load
 from qa_launch_clone import create
 c=load(a.receipt,stage=a.stage,project=P);require_no_games()
 a.cache_mode=selected_cache_mode(a.cache_mode,a.formal_stage)
 status=subprocess.run(['/Users/k.d./.local/bin/cua-driver','status'],capture_output=True,text=True,timeout=10)
 assert 'daemon is not running'in(status.stdout+status.stderr).lower(),'Cua must be stopped'
 native,cache_admission=admit(c)
 formal=admit_formal(a.formal_stage,c)if a.formal_stage is not None else None
 execution=execution_conditions(c,formal)
 observer=NativeObserver();before=observer.system();assert not before['locked']and before['pressure']!=4
 tag=str(time.time_ns());root=P/'evidence'/('original-launch-benchmark-'+tag);root.mkdir(mode=0o700)
 case=create(c,cache_admission,project=P,tag=tag,cache_mode=a.cache_mode)
 env=execution_environment(c,case,formal,functional_f3=a.functional_f3)
 actual_changes,allowed_changes=benchmark_changes(c,case,env,formal=formal,functional_f3=a.functional_f3)
 command=[c['executable'],str(execution['stage']/c['arguments'][0])]+([]if a.functional_f3 else['--benchmark'])
 report=dict(schema=1,status='running',receipt_sha256=sha(a.receipt),launch_id=c['launch_id'],native_identity=native,original_shell_entry_preserved=True,original_benchmark_flag_added=True,benchmark_control_overrides=dict(BB_QUICKSAVE='0',BB_PAD_FILE_ONLY='1'),cache_mode=a.cache_mode,silent=True,screen_capture=False,Cua=False,profiler=False,scripted_actions=['Continue until real scene camera'],formal_data_modified=False,source_save_unchanged=False,settings_sha256=case['config_sha256'],startup_budget_seconds=a.startup_seconds,scene_seconds=a.scene_seconds,quality_or_effects_changed=False)
 if formal is not None:report.update(formal_native_identity=formal['identity'],execution_profile='staged-formal-bundle-isolated-CLI',source_QA_only_for_clone=True,cache_namespace_policy='empty-base-to-new-namespace-by-original-bootstrap')
 if a.functional_f3:report.update(original_benchmark_flag_added=False,benchmark_control_overrides={'BB_FRAME_STATS':'1','BB_ARM_QA_PAD_FILE':'isolated existing native QA API'},performance_sample=False,functional_f3_requested=True,incremental_log_events=True,scripted_actions=['Continue before first scene','quicksave API','quickload API','automatic continue observed; no scripted Continue after F3'])
 write(root/'conditions.json',dict(command=command,cwd=str(execution['stage']),environment=env,source_receipt=str(a.receipt),data=str(case['data_root']),user=str(case['user_root']),cache=str(case['cache_root']),pad=str(case['pad']),allowed_changes=allowed_changes,actual_changes=actual_changes,cache_clone=case['cache_clone']))
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
   result=sample(execution,case,env,command,row,run,startup_seconds=a.startup_seconds,
       scene_seconds=a.scene_seconds,observer=observer,policy=policy,protection_start=protection_start,functional_f3=a.functional_f3)
   reports.append(result)
   write(root/'result.json',dict(schema=2,status=('functional-checked'if a.functional_f3 else'sampled')if all(r['status']in('sampled','functional-checked')for r in reports)and len(reports)==a.runs else'incomplete',runs=reports,formal_data_modified=False,memory_policy_continuous=True))
   if result['status']not in('sampled','functional-checked'):raise SystemExit(1)
 finally:
  observer.close()
  for sig,handler in previous_handlers.items():signal.signal(sig,handler)
if __name__=='__main__':main()
