#!/usr/bin/env python3
"""Matched CPU-engine QA through the unchanged launcher's explicit benchmark entry."""
from pathlib import Path
import argparse,csv,hashlib,json,os,secrets,shutil,signal,statistics,subprocess,sys,time
from native_observer import NativeObserver
from native_vm_observer import NativeVMObserver
from host_cpu_audit import HostCPUAudit
from process_schedule_audit import ProcessScheduleAudit
from cpu_stack_sample import CPUStackSample
from diagnostic_frame_tail import read_tail
from gpu_candidate_identity import verify as verify_gpu_candidate
from qa_memory_policy import MemoryPolicy
from launcher_environment import load as metal_environment
from launcher_language_policy import load as language_environment
from run_game import usage
from run_hle_lookup_abba import complete_rows
P=Path(__file__).resolve().parents[1]
def digest(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def main(gpu_load_identity=None):
 ap=argparse.ArgumentParser();ap.add_argument('--host-cpu-audit',action='store_true');ap.add_argument('--schedule-audit',action='store_true');ap.add_argument('--cpu-qos-order');ap.add_argument('--cpu-stack-sample',action='store_true');ap.add_argument('--producer-waits',action='store_true');ap.add_argument('--unaligned-audit',action='store_true');ap.add_argument('--shader-phases',action='store_true');ap.add_argument('--startup-shader-phases',action='store_true');ap.add_argument('--component-stage',type=Path);ap.add_argument('--post-action-hold',type=int,default=0);ap.add_argument('--diagnostic-app-cache',type=Path);ap.add_argument('--gpu-variant',choices=['baseline','first-upload','submit-diagnostic','partial-audit','present-image','layout-audit','tiler-bounds','raw-write-audit','owned-shader'],default='baseline');ap.add_argument('--tiler-bounds',choices=['0','1']);ap.add_argument('--exploration-diagnostic',action='store_true');ap.add_argument('--texture-action',choices=['none','blood','attack'],default='none');ap.add_argument('--inspection-seconds',type=int,default=0);ap.add_argument('--library-reuse',choices=['0','1']);ap.add_argument('--compile-audit',action='store_true');ap.add_argument('--producer-trace',action='store_true');ap.add_argument('--require-full-trace',action='store_true');ap.add_argument('--action-trace',action='store_true');ap.add_argument('--texture-capture',action='store_true');ap.add_argument('--game-images',action='store_true');ap.add_argument('--external-image-control',action='store_true');ap.add_argument('--lifecycle',action='store_true');ap.add_argument('--save-seed',type=Path);ap.add_argument('--order',default='arm64,rosetta,rosetta,arm64');ap.add_argument('--memory-snapshot',action='store_true');ap.add_argument('--fex-mapping-audit',action='store_true');ap.add_argument('--seconds',type=int,default=120);a=ap.parse_args();order=a.order.split(',');assert 90<=a.seconds<=(600 if a.lifecycle else 300 if a.external_image_control else 180) and order and len(order)<=4 and set(order)<={'arm64','rosetta'}
 assert (a.tiler_bounds is None) == (a.gpu_variant!='tiler-bounds'),'explicit isolated tiler switch required'
 qos_order=a.cpu_qos_order.split(',')if a.cpu_qos_order else [None]*len(order)
 assert len(qos_order)==len(order)and all(q in(None,'inherit','user-initiated')for q in qos_order),'one supported QoS mode per run required'
 assert a.cpu_qos_order is None or(set(order)=={'arm64'}and a.component_stage is None),'QoS experiment is private ARM64 only'
 assert not(a.host_cpu_audit or a.schedule_audit)or(a.action_trace and a.producer_trace),'host CPU/schedule inventory is diagnostic-only'
 assert not a.cpu_stack_sample or(a.action_trace and a.producer_trace and order==['arm64']and a.component_stage is None),'CPU stack sampling is diagnostic-only, standalone ARM64'
 assert not a.producer_waits or(a.action_trace and a.producer_trace and order==['arm64']),'producer wait scopes require the isolated producer diagnostic'
 assert not a.unaligned_audit or(a.action_trace and a.producer_trace and order==['arm64']and a.component_stage is None),'unaligned audit is isolated source-CPU diagnostic only'
 assert 0<=a.post_action_hold<=90 and (not a.post_action_hold or (a.action_trace and a.producer_trace)),'extended observation requires a producer diagnostic'
 assert not (a.texture_capture and a.gpu_variant!='baseline'),'capture and pure GPU candidate are separate cohorts'
 assert a.gpu_variant not in ('submit-diagnostic','partial-audit','present-image','layout-audit','tiler-bounds','raw-write-audit','owned-shader')or(a.action_trace and a.producer_trace),'special GPU variants require an isolated diagnostic cohort'
 assert a.component_stage is None or (order==['arm64']and a.gpu_variant=='baseline'and not a.texture_capture and not a.memory_snapshot and not a.lifecycle),'frozen component smoke requires standalone ARM64 cohort'
 assert not a.exploration_diagnostic or (a.action_trace and order==['arm64']),'exploration is isolated ARM diagnostic only'
 assert not a.game_images or(a.action_trace and a.producer_trace and order==['arm64']and a.gpu_variant=='present-image'and not a.cpu_stack_sample),'game images require their own isolated correctness cohort'
 assert a.gpu_variant!='present-image' or a.game_images,'image candidate is diagnostic only'
 assert not a.shader_phases or(a.gpu_variant=='present-image'and a.action_trace and a.producer_trace and order==['arm64']),'shader phases require their private GPU diagnostic'
 assert not a.startup_shader_phases or(a.shader_phases and a.external_image_control),'startup phase gate requires explicit external ownership'
 if a.shader_phases:assert json.loads((P/'evidence/present-image-gpu-app.json').read_text()).get('shader_phases'),'selected GPU does not contain the phase probe'
 assert not a.external_image_control or(a.game_images and a.exploration_diagnostic and not a.post_action_hold),'external image control is a bounded private correctness session'
 assert a.texture_action=='none' or a.texture_capture,'texture action requires diagnostic capture'
 assert 0<=a.inspection_seconds<=60 and (not a.inspection_seconds or a.texture_capture),'window inspection is diagnostic-only'
 assert not a.compile_audit or a.action_trace,'detailed compile logging is diagnostic-only'
 assert a.diagnostic_app_cache is None or (a.compile_audit and a.action_trace and order==['arm64']),'separate application cache is diagnostic-only'
 cache=P/'state/gpu-stutter-fresh-1792770276384734000'
 if a.diagnostic_app_cache is not None:
  requested=a.diagnostic_app_cache.absolute();cache=requested.resolve()
  assert requested==cache and cache.parent==P/'state' and cache.name.startswith('gpu-diagnostic-'),'direct private diagnostic cache required'
  cache.mkdir(exist_ok=True)
 assert cache.is_dir() and not cache.is_symlink()
 cache_before=dict(files=sum(f.is_file()for f in cache.rglob('*')),bytes=sum(f.stat().st_size for f in cache.rglob('*')if f.is_file()))
 assert not a.producer_trace or (a.action_trace and order==['arm64']),'producer scopes require an ARM64 action diagnostic cohort'
 assert not a.require_full_trace or a.texture_capture,'full texture trace requires texture capture mode'
 assert not a.action_trace or (not a.texture_capture and not a.lifecycle and not a.memory_snapshot and not a.fex_mapping_audit),'action trace requires its own diagnostic cohort'
 assert not a.texture_capture or (order==['arm64'] and not a.lifecycle and not a.memory_snapshot and not a.fex_mapping_audit),'capture is a standalone non-performance ARM64 cohort'
 assert not a.fex_mapping_audit or (a.memory_snapshot and order==['arm64']),'mapping audit requires ARM64-only memory cohort'
 assert not a.lifecycle or (order==['arm64']and not a.memory_snapshot),'lifecycle requires standalone ARM64 functional cohort'
 assert a.save_seed is None or a.lifecycle or a.texture_capture or a.action_trace,'custom QA save only permitted in isolated diagnostic/functional cohorts'
 save_seed=(a.save_seed or(P/'state/qa-launcher-community-seed')).resolve()
 assert save_seed.is_relative_to(P/'state')and save_seed.is_dir()and not(a.save_seed and a.save_seed.is_symlink())and(save_seed/'savedata').is_dir(),'private existing QA save required'
 assert not any(f.is_symlink()for f in(save_seed/'savedata').rglob('*')),'QA savedata must not link to another namespace'
 source_saves={str(f.relative_to(save_seed/'savedata')):digest(f)for f in(save_seed/'savedata').rglob('*')if f.is_file()}
 observer=NativeObserver();vm_observer=NativeVMObserver()if a.producer_trace else None;s=observer.system();assert not s['locked']and s['pressure']!=4
 table=subprocess.check_output(['/bin/ps','-axo','pid,comm'],text=True);assert not any(l.rstrip().endswith(('/bb-game-arm64','/bb-gpu','/bb-probe-metal','/bb-probe'))for l in table.splitlines())
 cua=subprocess.run(['/Users/k.d./.local/bin/cua-driver','status'],capture_output=True,text=True);assert a.texture_capture or a.action_trace or 'daemon is running'not in cua.stdout.lower(),'visual service must be stopped'
 if a.shader_phases:assert 'daemon is not running'in(cua.stdout+cua.stderr).lower(),'shader phase tests require stopped Cua, including diagnostic cohorts'
 e=P/'evidence'/('launcher-engine-abba-'+str(time.time_ns()));e.mkdir();report=dict(tiler_bounds=a.tiler_bounds,shader_phases=a.shader_phases,startup_shader_phases=a.startup_shader_phases,external_image_control=a.external_image_control,exploration_profile="straight-forward-images"if a.game_images else"original-turn-move",game_frame_capture=a.game_images,desktop_capture=False,unaligned_audit=a.unaligned_audit,producer_waits=a.producer_waits,cpu_stack_sample=a.cpu_stack_sample,qa_cpu_qos_order=qos_order,process_schedule_audit=a.schedule_audit,host_cpu_audit=a.host_cpu_audit,post_action_hold=a.post_action_hold,system_VM_counters=a.producer_trace,passed=False,runs=[],seconds=a.seconds,order=order,original_benchmark_entry=True,screen_capture=a.game_images,profiler=a.cpu_stack_sample,silent=True,formal_data_modified=False,memory_snapshot=a.memory_snapshot,performance_evidence=not(a.memory_snapshot or a.lifecycle or a.texture_capture or a.action_trace),action_trace=a.action_trace,texture_capture=a.texture_capture,lifecycle=a.lifecycle,CPU_time_schema=2,memory_policy=MemoryPolicy.MANIFEST,fex_mapping_audit=a.fex_mapping_audit,library_reuse=a.library_reuse,compile_audit=a.compile_audit,exploration_diagnostic=a.exploration_diagnostic,gpu_variant=('frozen-component'if a.component_stage is not None else a.gpu_variant),application_cache=dict(path=str(cache),initial=cache_before,separate=a.diagnostic_app_cache is not None,OS_driver_cache_policy="not cleared or directly edited by runner; driver may update naturally"))
 stage=P/'launcher-component-stage';source=json.loads((stage/'SOURCE-PARITY.json').read_text())['original_sources']
 if a.component_stage is not None:
  stage=a.component_stage.absolute();assert stage==stage.resolve()and stage.is_relative_to(P/'apps')and stage.parts[-2:]==('Resources','bbport'),'frozen QA component root required'
  frozen=json.loads((stage.parent/'ARM64-QA-MANIFEST.json').read_text())
  assert frozen['host_local_only']and frozen['formal_data_modified']is False and frozen['original_scripts']==source
  assert all(digest(stage/f)==h for f,h in frozen['frozen_runtime'].items()),'frozen QA binary identity mismatch'
  report['frozen_component']=dict(stage=str(stage),runtime=frozen['frozen_runtime'],upstream_inputs=frozen['upstream_inputs'])
  gpu_load_identity=verify_gpu_candidate(dict(target=str(stage/'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu'),library=str(stage/'bin/gpu-metal-arm64/libbbgpu.dylib'),library_sha256=frozen['frozen_runtime']['bin/gpu-metal-arm64/libbbgpu.dylib']),stage)
 assert all(digest(stage/f)==h for f,h in source.items())
 # Copy only original script bytes; x86 reference is used solely in the Rosetta control.
 ref=e/'rosetta-stage';ref.mkdir()
 for rel,h in source.items():
  dest=ref/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(stage/rel,dest);assert digest(dest)==h
 for f in('.packaged','NATIVE-METAL.json'):shutil.copy2(stage/f,ref/f)
 for f,target in [('python',stage/'python'),('deps-metal-arm64',stage/'deps-metal-arm64'),('deps-x86_64',P/'rosetta-reference/deps-x86_64'),('bin/bb-probe-metal',P/'rosetta-reference/bin/bb-probe-metal'),('bin/gpu-metal',P/'rosetta-reference/bin/gpu-metal'),('bin/gpu-metal-arm64',stage/'bin/gpu-metal-arm64')]:
  dest=ref/f;dest.parent.mkdir(parents=True,exist_ok=True);dest.symlink_to(target.resolve(),target_is_directory=target.is_dir())
 artifacts=[P/'build/fex/Bin/bb-game-arm64',P/'rosetta-reference/bin/bb-probe-metal',P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.dylib']
 if a.gpu_variant=='first-upload':artifacts.append(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.first-upload-candidate.dylib')
 if a.gpu_variant=='submit-diagnostic':artifacts.append(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.submit-diagnostic.dylib')
 if a.gpu_variant=='partial-audit':artifacts.append(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.partial-audit-candidate.dylib')
 if gpu_load_identity is None:
  assert a.gpu_variant=='baseline'and not a.texture_capture,'diagnostic GPU load identity required'
  baseline=json.loads((P/'evidence/heap-bridge-build.json').read_text())
  gpu_load_identity=verify_gpu_candidate(dict(target=str((stage/'bin/gpu-metal-arm64/bb-gpu').resolve()),library=str(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.dylib'),library_sha256=baseline['sha256']),P/'backend')
 if a.component_stage is None:assert (stage/'bin/gpu-metal-arm64/bb-gpu').resolve()==Path(gpu_load_identity['target'])
 artifacts.extend([Path(gpu_load_identity['library']),Path(gpu_load_identity['target'])]);report['gpu_load_identity']=gpu_load_identity
 if a.texture_capture:artifacts.append(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.texture-candidate.dylib')
 if a.component_stage is not None:artifacts=[stage/f for f in frozen['frozen_runtime']]
 hashes={str(f):digest(f)for f in artifacts};report['artifacts']=hashes;report['script_hashes']=source
 def save(): (e/'result.json').write_text(json.dumps(report,indent=2)+'\n')
 save();print('Evidence:',e,flush=True)
 for index,engine in enumerate(order):
  assert hashes=={str(f):digest(f)for f in artifacts}
  before=observer.system();assert not before['locked']and before['pressure']!=4
  case=e/(str(index)+'-'+engine);case.mkdir();tag=str(time.time_ns());user=P/'state'/('engine-user-'+tag);data=P/'state'/('engine-data-'+tag);user.mkdir();data.mkdir();out=data/'out';out.mkdir();pad=data/'pad.txt';pad.write_text('');nonce=secrets.token_hex(16)
  seed=P/'state/qa-launcher-community-seed';shutil.copytree(save_seed/'savedata',user/'savedata');shutil.copy2(seed/'bbport.ini',data/'bbport.ini')
  for f in list((user/'savedata/1').iterdir()):
   if f.name in('CUSA03173','CUSA03173.memory'):f.rename(f.with_name('CUSA00207'+f.name[9:]))
  game=P.parent/'assets/CUSA03173-1.09';(user/'.arm-runtime-session').write_text('ARMQA1\n'+str(out/'boot-linked.bin')+'\n'+str(game)+'\n'+str(out/'content.bin')+'\n'+nonce+'\n')
  env=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(P/'state'),TMPDIR=str(data)+'/',BB_RENDERER='metal',BB_GAME_DIR=str(game),BB_USER_DIR=str(user),BB_DATA_DIR=str(data),BB_CONFIG=str(data/'bbport.ini'),BB_GPU_USER_DIR=str(cache),BB_QUICKSAVE='0',BB_ONLINE='0',BB_SKIP_NETWORK_CHOICE='1',BB_FPS='uncap',BB_MODS_ENABLED='1',BB_METAL_ARM64='1',BB_MTL_HOT_STAGING_REUSE='1',BB_PAD_FILE=str(pad),BB_PAD_FILE_ONLY='1',BB_BENCHMARK_MUTE='1',BB_FRAME_LOG_ONLY='1',BB_FRAME_TIMING='1')
  image_dir=None;image_trigger=None;image_requests=[];external_ready=False
  if a.game_images:
   image_dir=case/'game-images';image_dir.mkdir(mode=0o700);image_trigger=image_dir/'image-request'
   env.update(BB_DUMP_DIR=str(image_dir),BB_PRESENT_DUMP_TRIGGER=str(image_trigger),BB_PRESENT_DUMP_COUNT='1',BB_PRESENT_DUMP_FINAL='1')
  capture_trigger=None
  if a.texture_capture:
   captures=case/'captures';captures.mkdir();capture_trigger=case/'capture-trigger'
   env.update(BB_CAPTURE_NATIVE='1',BB_CAPTURE_TEXTURE_BINDINGS='1',BB_CAPTURE_TRIGGER=str(capture_trigger),BB_CAPTURE_DIR=str(captures))
  audit_gate=None
  if a.action_trace:
   audit_gate=case/'stutter-control.bin';audit_gate.write_bytes((1 if a.startup_shader_phases else 0).to_bytes(4,'little')+bytes(4092))
   env.update(BB_STUTTER_AUDIT=str(case/'stutter'),BB_STUTTER_GATE=str(audit_gate),BB_STUTTER_COARSE='1')
   if a.producer_trace:env.update(BB_GUEST_FRAME_AUDIT='1',BB_GUEST_SYNC_AUDIT='1',BB_SLOW_PACKET_AUDIT='1')
   if a.shader_phases:env['BB_SHADER_PHASE_AUDIT']='1'
  if a.fex_mapping_audit:env['BB_FEX_MAPPING_AUDIT']='1'
  travel=None;travel_samples=[]
  if a.lifecycle:
   travel=user/'world-warper';travel.mkdir();(user/'.private-world-warper').write_text(nonce);env['BB_DEV_WORLD_WARPER_DIR']=str(travel)
  env.update(metal_environment());env.update(language_environment(P/'fixtures/current-launcher-language.json'))
  if a.exploration_diagnostic:env['BB_PATCHES']='Player No Dead (Read note)'
  if a.library_reuse is not None:env['BB_MTL_LIBRARY_REUSE']=a.library_reuse
  if a.compile_audit:env['BB_MTL_COMPILE_AUDIT']='1'
  if a.producer_waits:env['BB_PRODUCER_WAIT_AUDIT']='1'
  if a.unaligned_audit:env['BB_UNALIGNED_AUDIT']='1'
  if a.gpu_variant=='layout-audit':env['BB_ARM_LAYOUT_AUDIT']='1'
  if a.gpu_variant=='tiler-bounds':env['BB_ARM_TILER_BOUNDS']=a.tiler_bounds
  if a.gpu_variant=='raw-write-audit':
   env['BB_ARM_TILER_BOUNDS']='1';env['BB_ARM_RAW_WRITE_AUDIT']='1';env['BB_ARM_RAW_WRITE_AUDIT_GATE']='1'
  if qos_order[index] is not None:env.update(BB_ARM_QA_CPU_QOS=qos_order[index],BB_ARM_QA_CPU_QOS_EXPERIMENT='1')
  if engine=='arm64':env.update(BB_ARM_LAUNCHER_COMPAT='1',BB_ARM_SESSION_NONCE=nonce,BB_ARM_RESTART_NONCE=secrets.token_hex(16),BB_ARM_QA_PAD_FILE=str(pad))
  root=stage if engine=='arm64'else ref;command=['/bin/bash',str(root/'tools/macos/run_optimized.sh'),'--benchmark'];(case/'conditions.json').write_text(json.dumps(dict(engine=engine,command=command,env=env,user=str(user),data=str(data),settings_sha256=digest(data/'bbport.ini'),save_source=str(save_seed),seed_files=source_saves),indent=2)+'\n')
  tracked={};samples=[];started=time.monotonic();policy=MemoryPolicy(before);stop=None;ready=None;pressed=False;last_press=-100;proc=None;memory_child=None;memory_log=None;capture_requested=False;capture_file=None;capture_closed=None;action_step=0;action_next=None;action_cycle=0;gate_open=False;gate_closed=False;action_end=None;texture_action_sent=False;inspection_deadline=None;last_frame_t=-1;next_frame_check=0;spike_seen=None;host_cpu=HostCPUAudit()if a.host_cpu_audit else None;next_host_cpu=0;scheduler=ProcessScheduleAudit()if a.schedule_audit else None;next_schedule=0;stack_sampler=CPUStackSample(case,observer)if a.cpu_stack_sample else None
  def input_(token):
   t=pad.with_suffix('.next');t.write_text(token);os.replace(t,pad)
  def event(name,**kw):
   record=dict(event=name,mono_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC),utc_ns=time.time_ns(),**kw)
   with(case/'operator-events.jsonl').open('a')as file:file.write(json.dumps(record)+'\n')
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
  def stop_owned():
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
  with(case/'console.log').open('w')as console:
   proc=subprocess.Popen(command,cwd=root,env=env,stdout=console,stderr=subprocess.STDOUT,start_new_session=True)
   try:
    while proc.poll()is None:
     now=time.monotonic();elapsed=now-started;rows=descendants();sample=observer.system();sample.update(seconds=elapsed,processes=rows,usage=usage([f'{i} {j} 0 {path}'for i,j,path in rows]));samples.append(sample)
     if vm_observer is not None:sample['system_vm']=vm_observer.system()
     if host_cpu is not None and now>=next_host_cpu:
      host_sample=host_cpu.sample({pid for pid,ppid,path in rows})
      with(case/'host-cpu.jsonl').open('a')as file:file.write(json.dumps(host_sample)+'\n')
      next_host_cpu=now+1
     if scheduler is not None and now>=next_schedule:
      with(case/'process-schedule.jsonl').open('a')as file:file.write(json.dumps(scheduler.sample(pid for pid,ppid,path in rows))+'\n')
      next_schedule=now+1
     stop=policy.observe(elapsed,sample)
     frames=out/'frames.csv';parsed=complete_rows(frames)if ready is None and frames.exists()else[]
     if ready is None and any(f.get('cam_x')and int(f['draws'])>100 for f in parsed[-30:]):
      ready=elapsed;event('scene_camera_seen',seconds=ready,meaning='real scene camera observed; not fully-playable acceptance');print('Loaded scene:',engine,round(ready,2),flush=True)
     # Normal cohorts press Continue only until a real camera appears. The explicit
     # diagnostic action cohort additionally requests two bounded action cycles.
     if pressed and now-last_press>=.18:input_('');pressed=False
     if ready is None and parsed and now-last_press>=4 and elapsed>=12:input_('cross\n');pressed=True;last_press=now
     if a.action_trace and ready is not None and not a.external_image_control:
      if action_next is None and action_cycle==0:action_next=started+ready+10
      if not gate_open and elapsed-ready>=8:
       with audit_gate.open('r+b')as file:file.write(b'\x01\x00\x00\x00')
       gate_open=True;event('audit_gate_open')
       if a.post_action_hold:
        recent=read_tail(frames);last_frame_t=float(recent[-1]['t_s'])if recent else -1;next_frame_check=now+1
      if action_next is not None and now>=action_next:
       steps=[('l2',.18),('',2.0),('up',.18),('',2.5),('triangle',.18),('',0)]
       if a.exploration_diagnostic:steps=[('rx=64',.7),('',.3),('lx=128 ly=0',3.0),('r1',.18),('',1.2),('r2',.18),('',.8),('lx=128 ly=0',2.0),('r1',.18),('',0)]
       if a.exploration_diagnostic and a.game_images:
        steps=[('lx=128 ly=0',1.5),('',.5),('r1',.18),('',1.2),('r2',.18),('',.8),('lx=128 ly=0',1.0),('r1',.18),('',0)]
       if action_step==0:event('cycle_start',cycle=action_cycle+1)
       token,delay=steps[action_step];input_(token);event('input',cycle=action_cycle+1,token=token);action_step+=1
       if action_step==len(steps):
        action_cycle+=1;action_step=0;event('cycle_end',cycle=action_cycle)
        action_next=started+ready+25 if action_cycle<2 else None
        if action_cycle==2:action_end=now
       else:action_next=now+delay
      if a.post_action_hold and gate_open and not gate_closed and now>=next_frame_check:
       recent=read_tail(frames);new=[r for r in recent if float(r['t_s'])>last_frame_t]
       if new:last_frame_t=max(float(r['t_s'])for r in new)
       slow=next((r for r in new if float(r['frame_ms'])>=80),None)
       if slow is not None and spike_seen is None:
        spike_seen=dict(frame_ms=float(slow['frame_ms']),csv_t_s=float(slow['t_s']),observed_seconds=elapsed)
        event('long_frame_trigger',**spike_seen)
       next_frame_check=now+1
      if action_end is not None and now-action_end>=(2 if spike_seen is not None else 7+a.post_action_hold)and not gate_closed:
       with audit_gate.open('r+b')as file:file.write(bytes(4))
       gate_closed=True;event('audit_gate_close')
      if gate_closed and elapsed-ready>=45:stop=stop or 'private action trace completed'
     if a.game_images and ready is not None and not a.external_image_control:
      wanted=3 if action_cycle>=2 else 2 if action_cycle>=1 else 1 if elapsed-ready>=3 else 0
      if len(image_requests)<wanted and not image_trigger.exists():
       image_trigger.touch(mode=0o600,exist_ok=False);image_requests.append(now)
       event('game_image_requested',sequence=len(image_requests),native_completed_display_only=True,performance_evidence=False)
      if image_trigger.exists()and image_requests and now-image_requests[-1]>10:stop=stop or 'game image trigger was not acknowledged'
     if a.external_image_control and ready is not None:
      if not external_ready:
       input_('');pressed=False # complete the automatic-input handoff before publishing control
       cpus=[(pid,path)for pid,ppid,path in rows if path==str(P/'build/fex/Bin/bb-game-arm64')];assert len(cpus)==1
       pid,path=cpus[0];identity=ProcessScheduleAudit().read(pid);assert identity
       (case/'external-ready.json').write_text(json.dumps(dict(schema=1,pid=pid,path=path,process_start_mach=identity['process_start_mach'],uuid=identity['uuid'],pad=str(pad),images=str(image_dir),trigger=str(image_trigger),gate=str(audit_gate),log=str(out/'last-run.log'),offline=True,quicksave=False,max_images=8,seconds=a.seconds),indent=2)+'\n')
       external_ready=True;event('external_image_control_ready',performance_evidence=False)
      if len(list(image_dir.glob('*.raw')))>8:stop=stop or 'game image budget exceeded'
      if(case/'finish-request').exists():
       with audit_gate.open('r+b')as file:file.write(bytes(4))
       gate_closed=True;stop=stop or 'private action trace completed'
     if stack_sampler is not None:
      if stack_sampler.proc is None and action_cycle==1 and action_step>0:
       cpus=[(pid,path)for pid,ppid,path in rows if path==str(P/'build/fex/Bin/bb-game-arm64')]
       assert len(cpus)==1,'owned ARM64 CPU missing for sampler'
       pid,path=cpus[0];stack_sampler.start(pid,path,{i:p for i,j,p in rows});event('cpu_stack_sample_start',pid=pid,duration=stack_sampler.duration,interval_ms=stack_sampler.interval,performance_evidence=False)
      if stack_sampler.proc is not None and stack_sampler.poll()and not stack_sampler.report.get('passed'):stop=stop or 'CPU stack sample rejected by diagnostic budget'
     if a.texture_capture and ready is not None and a.texture_action!='none' and elapsed-ready>=2 and not texture_action_sent:
      token='up' if a.texture_action=='blood' else 'r1'
      input_(token+'\n');pressed=True;last_press=now;texture_action_sent=True;event('texture_action_requested',token=token,guest_success_independently_verified=False)
     if a.texture_capture and ready is not None and elapsed-ready>=5:
      if not capture_requested:capture_trigger.touch();capture_requested=True
      files=sorted(captures.glob('frame_*.txt'))
      if files:
       candidate=files[-1];fingerprint=(str(candidate),candidate.stat().st_size)
       if fingerprint==capture_closed and elapsed-ready>=8 and ('passes written to '+str(candidate))in(out/'last-run.log').read_text(errors='replace'):
        capture_file=candidate
        if inspection_deadline is None:inspection_deadline=now+a.inspection_seconds;print('Texture capture available:',candidate,flush=True)
        if now>=inspection_deadline:stop=stop or 'private texture capture completed'
       capture_closed=fingerprint
     if a.lifecycle and ready is not None:
      if not(travel/'armed').exists():(travel/'armed').write_text(nonce)
      status=travel/'status.json'
      if status.exists():
       st=json.loads(status.read_text());st['host_seconds']=elapsed
       last=travel_samples[-1]if travel_samples else{}
       if any(st.get(k)!=last.get(k)for k in('world','loading','map','stage')):travel_samples.append(st);(case/'lifecycle.json').write_text(json.dumps(dict(death_acceptance=False,samples=travel_samples),indent=2)+'\n')
      if(case/'finish-request').exists():stop='private test completed'
     if a.memory_snapshot and ready is not None and elapsed-ready>=45 and memory_child is None:
      cpus=[i for i,j,path in rows if path in(str(P/'build/fex/Bin/bb-game-arm64'),str(P/'rosetta-reference/bin/bb-probe-metal'))];gpu_target=Path(json.loads((P/'evidence/first-upload-gpu-app.json').read_text())['target'])if a.gpu_variant=='first-upload'else P/'backend/bbport/bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu';gpus=[i for i,j,path in rows if path==str(gpu_target)];assert len(cpus)==len(gpus)==1
      memory_log=(case/'memory-capture-console.log').open('w');memory_child=subprocess.Popen([sys.executable,str(P/'tools/capture_launcher_memory.py'),str(case),str(cpus[0]),str(gpus[0])]+(['--fex-mapping-audit']if a.fex_mapping_audit else[]),stdout=memory_log,stderr=memory_log)
     if elapsed>=a.seconds:stop='time budget'
     if stop:stop_owned();break
     time.sleep(.25)
   finally:
    input_('')
    if proc.poll()is None:stop_owned()
    if stack_sampler is not None:
     stack_sampler.poll();stack_sampler.close()
    if memory_child is not None:
     if memory_child.poll()is None:
      memory_child.terminate()
      try:memory_child.wait(timeout=3)
      except subprocess.TimeoutExpired:memory_child.kill();memory_child.wait()
     memory_log.close()
  result=dict(engine=engine,qa_cpu_qos=qos_order[index],exit=proc.returncode,stop=stop,ready=ready,seconds=time.monotonic()-started,before=before,after=observer.system(),samples=samples)
  (case/'run.json').write_text(json.dumps(result,indent=2)+'\n')
  try:
   assert stop in(('private action trace completed',)if a.action_trace else ('private texture capture completed',)if a.texture_capture else ('time budget','private test completed')if a.lifecycle else('time budget',))and proc.returncode==143 and ready is not None and (a.texture_capture or a.action_trace or a.lifecycle or a.seconds-ready>=45),'incomplete guarded scene case'
   if a.memory_snapshot:assert memory_child is not None and memory_child.returncode==0 and(case/'memory.parsed.json').is_file(),'kernel memory capture incomplete'
   text=(out/'last-run.log').read_text(errors='replace');assert 'ARM FATAL'not in text and 'ARM RESOURCE invalid metadata'not in text
   assert text.count('benchmark stream muted; original audio backend and cadence retained')>=2,'normal silent audio cadence not verified'
   if engine=='arm64':assert 'ARM LAUNCHER HLE published lookup=1;'in text,'component default did not activate'
   if engine=='arm64':
    for marker in('ARM VENEER layout=darwin-packed-guest-ro-v1','ARM RESOURCE 10/10 startup FEX detours','ARM RESOURCE leaf-dispatch=1','ARM RESOURCE dispatch-filter=1','ARM EXTRA 5/5 actual heap hooks installed'):assert marker in text,'component default missing: '+marker
   if a.cpu_stack_sample:assert stack_sampler.report and stack_sampler.report.get('passed'),'CPU stack sample incomplete'
   if a.action_trace:
    assert (external_ready if a.external_image_control else action_cycle==2)and gate_closed,'action trace incomplete'
    assert source_saves=={str(f.relative_to(save_seed/'savedata')):digest(f)for f in(save_seed/'savedata').rglob('*')if f.is_file()},'source QA save changed'
    (case/'runtime.log').symlink_to(out/'last-run.log');(case/'frames.csv').symlink_to(out/'frames.csv')
    (case/'stutter-case-conditions.json').write_text(json.dumps(dict(shader_phases=a.shader_phases,startup_shader_phases=a.startup_shader_phases,game_frame_capture=a.game_images,coarse_audit=True,original_launcher_entry=True,engine=engine,performance_evidence=False,scripted_actions_only_in_private_clone=True,generic_condition_wait_scopes_suppressed=a.producer_trace,guest_frame_audit=a.producer_trace,guest_sync_audit=a.producer_trace,slow_packet_audit=a.producer_trace,producer_waits=a.producer_waits,unaligned_audit=a.unaligned_audit,cpu_stack_sampler=a.cpu_stack_sample))+'\n')
    analysis=subprocess.run([sys.executable,str(P/'tools/analyze_stutter.py'),str(case)],capture_output=True,text=True)
    (case/'analysis-console.log').write_text(analysis.stdout+analysis.stderr);assert analysis.returncode==0,'trace analysis failed'
    summary=json.loads((case/'stutter-summary.json').read_text());assert (a.external_image_control or len(summary['windows'])==2)and summary['traces'],'missing action windows or traces'
    images=[]
    if a.game_images:
     from decode_present_image import from_log
     assert (a.external_image_control or len(image_requests)==3)and not image_trigger.exists(),'game image requests incomplete'
     images=from_log(image_dir,text,reuse_verified=a.external_image_control);assert (1<=len(images)<=8 if a.external_image_control else len(images)==3),'completed format-proven game images required'
     assert all(x['metal_format']==80 and x['flip_vertical']for x in images),'completed post-process present-view proof missing'
     (case/'game-images.json').write_text(json.dumps(dict(images=images,performance_evidence=False,desktop_capture=False,readback_disrupts_timing=True),indent=2)+'\n')
    report['runs'].append(dict(engine=engine,qa_cpu_qos=qos_order[index],case=str(case),requested_action_cycles=action_cycle,game_images=images,long_frame_trigger=spike_seen,actual_guest_action_acceptance='not instrumented; requested file inputs only',source_save_unchanged=True,trace_processes=len(summary['traces']),trace_overflow=any(t['overwritten']or t['dropped']for t in summary['traces']),trace_clock_rejections=sum(t.get('clock_rejected_rows',0)for t in summary['traces']),performance_evidence=False));save();continue
   if a.texture_capture:
    from analyze_texture_bindings import analyze
    assert capture_file and capture_file.stat().st_size<=3*1024*1024,'missing or oversized capture'
    capture=analyze(capture_file.read_text());(case/'capture-analysis.json').write_text(json.dumps(capture,indent=2)+'\n')
    assert capture['draw_count']>0 and capture['memo_binding_observations']>0,'actual memo-hit observation not exercised'
    assert not a.require_full_trace or capture['metadata_complete'],'full texture provenance incomplete'
    assert source_saves=={str(f.relative_to(save_seed/'savedata')):digest(f)for f in(save_seed/'savedata').rglob('*')if f.is_file()},'source QA save changed'
    report['runs'].append(dict(engine=engine,case=str(case),capture_file=str(capture_file),memo_hit_crash_regression_pass=True,metadata_complete=capture['metadata_complete'],warnings=capture['warnings'],memo_binding_observations=capture['memo_binding_observations'],draw_count=capture['draw_count'],source_save_unchanged=True,performance_evidence=False,render_correctness_proven=False));save();continue
   if a.lifecycle:
    assert source_saves=={str(f.relative_to(save_seed/'savedata')):digest(f)for f in(save_seed/'savedata').rglob('*')if f.is_file()},'source QA save changed'
    assert 'ARM TRAVEL BB_DEVELOPMENT_WORLD_WARPER_FEX_V1 installed'in text,'private lifecycle observation not installed'
    report['runs'].append(dict(engine=engine,case=str(case),normal_session=True,death_acceptance=False,transitions=travel_samples,source_save_unchanged=True,max_pressure=max(s['pressure']for s in samples)));save();continue
   frames=complete_rows(out/'frames.csv');end=float(frames[-1]['t_s']);tail=[f for f in frames if float(f['t_s'])>=end-30 and float(f['frame_ms'])>0];assert len(tail)>600 and statistics.median(int(f['draws'])for f in tail)>100
   assert sum(int(f['compiles'])for f in tail)==0,'late compiles contaminate steady sample'
   cameras=sorted({(f['cam_x'],f['cam_y'],f['cam_z'])for f in tail if f.get('cam_x')});assert cameras
   selected=[s for s in samples if s['seconds']>=samples[-1]['seconds']-30];roles={}
   for sample in selected:
    for u in sample['usage']:roles.setdefault((u['role'],u['pid']),[]).append((sample['seconds'],u))
   cpus={}
   for(role,pid),v in roles.items():
    if len(v)<2:continue
    role='bb-game-arm64'if a.component_stage is not None and role=='bb-probe-metal'else role
    at,x=v[0];bt,y=v[-1];cpus[role]=100*(y['cpu_user_ns']+y['cpu_system_ns']-x['cpu_user_ns']-x['cpu_system_ns'])/1e9/(bt-at)
   times=sorted(float(f['frame_ms'])for f in tail)
   row=dict(engine=engine,qa_cpu_qos=qos_order[index],case=str(case),fps=1000/statistics.mean(times),p95_ms=times[int((len(times)-1)*.95)],p99_ms=times[int((len(times)-1)*.99)],gpu_ms=statistics.mean(float(f['gpu_ms'])for f in tail),draws_median=statistics.median(int(f['draws'])for f in tail),camera=cameras,cpu_percent=cpus,combined_footprint_mib=statistics.median(sum(u['footprint_bytes']for u in s['usage'])/2**20 for s in selected),max_pressure=max(s['pressure']for s in samples),swap_change_mib=result['after']['swap_mib']-before['swap_mib'],boot_sha256=digest(out/'boot-linked.bin'),content_sha256=digest(out/'content.bin'),patch_sha256=digest(out/'patches.bin'),settings_sha256=digest(data/'bbport.ini'))
   if report['runs']:
    previous=report['runs'][0]
    for k in('boot_sha256','content_sha256','patch_sha256','settings_sha256','camera'):assert row[k]==previous[k],f'{k} differs'
   report['runs'].append(row);save();print(json.dumps(row),flush=True)
  except BaseException as error:
   report['failure']=dict(index=index,engine=engine,case=str(case),error=str(error));save();raise
  time.sleep(3)
 report['passed']=True
 for engine,mode in ([]if a.lifecycle or a.texture_capture or a.action_trace else set(zip(order,qos_order))):
  xs=[r for r in report['runs']if r['engine']==engine and r.get('qa_cpu_qos')==mode];key=engine if mode is None else engine+':'+mode
  report[key]=dict(mean_fps=statistics.mean(r['fps']for r in xs),mean_combined_footprint_mib=statistics.mean(r['combined_footprint_mib']for r in xs),mean_cpu_percent=statistics.mean(r['cpu_percent'].get('bb-game-arm64'if engine=='arm64'else'bb-probe-metal',0)for r in xs))
 report['qualification']='Two requested blood-bullet/heal cycles in a private static scene; diagnostic trace overhead and ring retention preclude FPS acceptance; guest action success is not independently verified.' if a.action_trace else 'Single static metadata capture only; memo-hit crash regression is not white-model correctness or performance acceptance.' if a.texture_capture else 'Fixed QA scene only; lightweight telemetry has overhead; aggregate footprint double-counts some shared pages. Not a deduplicated RAM comparison, long-term lifetime or network acceptance.'
 if a.exploration_diagnostic:report['qualification']='Two bounded camera/move/attack requests in a fresh private save with the original Player No Dead patch. No replay/collision determinism, combat hit or white-model acceptance is implied; diagnostics are not FPS evidence.'
 if a.game_images:report['qualification']+=' Native completed game-buffer readback disrupts timing: correctness-only; tight raw retained, RGB PNG preview, no desktop pixels.'
 if a.external_image_control:report['qualification']='Private external control through the original pad-file API; bounded to 300s and eight images. Not deterministic replay, performance or combat-hit acceptance; capture pauses may perturb races.'
 save();print('Completed:',e,flush=True)
if __name__=='__main__':
 if '--gpu-variant'in sys.argv and sys.argv[sys.argv.index('--gpu-variant')+1]in('first-upload','submit-diagnostic','partial-audit','present-image','layout-audit','tiler-bounds','raw-write-audit','owned-shader'):
  variant=sys.argv[sys.argv.index('--gpu-variant')+1]
  metadata={'first-upload':'first-upload-gpu-app.json','submit-diagnostic':'submit-gpu-app.json','partial-audit':'partial-audit-gpu-app.json','present-image':'present-image-gpu-app.json','layout-audit':'layout-audit-gpu-app.json','tiler-bounds':'tiler-bounds-gpu-app.json','raw-write-audit':'raw-write-audit-gpu-app.json','owned-shader':'owned-shader-gpu-app.json'}[variant]
  meta=json.loads((P/'evidence'/metadata).read_text())
  gpu_identity=verify_gpu_candidate(meta,P/'backend')
  alias=P/'launcher-component-stage/bin/gpu-metal-arm64/bb-gpu';original=Path(meta['original_target'])
  assert alias.is_symlink()and alias.resolve()==original
  assert digest(Path(meta['library']))==meta['library_sha256']
  alias.unlink();alias.symlink_to(meta['target'])
  try:main(gpu_identity)
  finally:
   alias.unlink();alias.symlink_to(original)
 elif '--texture-capture'not in sys.argv:main()
 else:
  # Scoped alias switch only while the diagnostic cohort owns the GPU process.
  plan=json.loads((P/'evidence/texture-binding-diagnostic-1792854382206827000/runtime-plan.json').read_text())
  alias=Path(plan['alias']);original=Path(plan['original_target']);candidate=Path(plan['candidate_target'])
  assert alias.is_symlink()and alias.resolve()==original and candidate.is_file()
  identity=Path(plan['evidence'])/'capture-binding-address-status.json'
  if not identity.is_file():identity=Path(plan['evidence'])/'memo-null-fix-status.json'
  fixed=json.loads(identity.read_text())
  assert digest(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.texture-candidate.dylib')==fixed['fixed_candidate_sha']
  gpu_identity=verify_gpu_candidate(dict(target=str(candidate),library=str(P/'backend/bbport/bin/gpu-metal-arm64/libbbgpu.texture-candidate.dylib'),library_sha256=fixed['fixed_candidate_sha']),P/'backend')
  alias.unlink();alias.symlink_to(candidate)
  try:main(gpu_identity)
  finally:
   alias.unlink();alias.symlink_to(original)

