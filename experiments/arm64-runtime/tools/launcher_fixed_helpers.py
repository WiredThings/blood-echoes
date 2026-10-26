"""Default-on fixed helper seeds in the existing headless component/cohort.
No game cache walker, Storage/profile mutation, renderer owner or new cache root.
"""
from pathlib import Path
import hashlib,json,os,re,signal,subprocess,time
HEX=re.compile('[0-9a-f]{64}')
MAX_LINE=64*1024
class ProtectionStopped(RuntimeError):pass

def validate_result(row,phase):
 """Pure response boundaries, available to self-contained CPU controls."""
 if phase not in ('helpers-probe','helpers-prepare'):raise ValueError('fixed helper response phase')
 r=row['result']
 if r.get('status')=='stopped':raise ProtectionStopped('fixed helper protected stop')
 if type(row['exit'])is not int or row['exit']!=0:raise RuntimeError('fixed helper worker failed')
 items=r.get('items',[])
 if type(r.get('fixed_helper_seed_schema'))is not int or r['fixed_helper_seed_schema']!=1 or type(r.get('known'))is not int or r['known']!=8 or not isinstance(items,list)or len(items)>8:raise ValueError('fixed helper response shape')
 if type(r.get('ready'))is not int or not 0<=r['ready']<=8 or type(r.get('all_fixed_native_ready'))is not bool:raise ValueError('fixed helper ready count')
 kinds=[i['kind']for i in items]
 if len(set(kinds))!=len(kinds)or any(type(k)is not int or k not in (4,5,6,9,10,11,12,13)for k in kinds):raise ValueError('fixed helper response kinds')
 for i in items:
  if type(i['inputs_verified'])is not bool or type(i['native_ready'])is not bool or type(i.get('outcome'))is not int or not 0<=i['outcome']<=5:raise ValueError('fixed helper response types')
  if i['outcome']==5:raise ProtectionStopped('fixed helper protected stop')
  if i['inputs_verified']or i['native_ready']:
   if not isinstance(i['request_sha256'],str)or not isinstance(i['descriptor_sha256'],str)or not HEX.fullmatch(i['request_sha256'])or not HEX.fullmatch(i['descriptor_sha256']):raise ValueError('fixed helper input identity')
  routes=i.get('actual_routes',[])
  if i['inputs_verified']and(not isinstance(routes,list)or len(routes)!=(2 if i['kind']==10 else 1)or any(type(v)is not int or v not in(1,2)for v in routes)):raise ValueError('fixed helper actual origin routes')
  if i['native_ready']and(not i['inputs_verified']or i['outcome']not in(1,2)or not isinstance(i['archive_sha256'],str)or not HEX.fullmatch(i['archive_sha256'])):raise ValueError('fixed helper ready receipt identity')
  if phase=='helpers-probe'and i['outcome']==2:raise ValueError('fixed helper probe cannot publish a built pipeline')
 if r['ready']!=sum(i['native_ready']for i in items):raise ValueError('fixed helper ready/item mismatch')
 complete=r['all_fixed_native_ready']
 if complete and(set(kinds)!={4,5,6,9,10,11,12,13}or r['ready']!=8 or not all(i['native_ready']and i['actual_routes']in([2],[2,2])for i in items)):raise ValueError('fixed helper incomplete success')
 count=r.get('artifact_counts',{}).get('compile_attempts')
 if type(count)is not int or not 0<=count<=64:raise ValueError('fixed helper compile count')
 if phase=='helpers-probe'and count!=0:raise ValueError('fixed helper probe ordinary compile forbidden')
 return r,complete

def prepare(*,project,execution_root,arguments,environment,cohort,options,observe,
            mode='auto',local_prepare=None,component_loader=None,toolchain_probe=None,
            protection_baseline=None,runner=None):
 project=Path(project).resolve(strict=True)
 if mode in ('off','record'):return {},dict(status='inactive',reason='explicit-local-mode')
 if mode not in ('auto','load')or not HEX.fullmatch(cohort):raise ValueError('fixed helper mode/cohort')
 if execution_root is None:return {},dict(status='unavailable',reason='packaged component unavailable')
 stage=Path(execution_root).resolve(strict=True)
 if component_loader is None:
  from game_pso_component import load as component_loader
 component=component_loader(stage)
 if component is None or component.get('fixed_helper_seed_schema')!=1:
  return {},dict(status='unavailable',reason='fixed helper worker ABI unavailable')
 if toolchain_probe is None:
  from local_aot_policy import available_toolchain as toolchain_probe
 if not toolchain_probe():return {},dict(status='source-fallback',reason='toolchain-unavailable')
 from warm_archive_manifest import resolve_gpu_identity
 selected=resolve_gpu_identity(Path(environment['BB_GPU_PROCESS']),stage)
 if selected['library_sha256']!=component['library_sha256']:raise ValueError('fixed helper worker/renderer DSO mismatch')
 from launcher_game_pso import title_id
 title=title_id(arguments,stage,project);gpu=Path(environment['BB_GPU_USER_DIR']).resolve(strict=True)
 if gpu==project/'state'or not gpu.is_relative_to(project/'state'):raise ValueError('fixed helper isolated GPU root required')
 artifact=gpu/'cache'/title/'metal';artifact.mkdir(parents=True,exist_ok=True)
 if artifact!=artifact.resolve(strict=True):raise ValueError('linked renderer artifact root')
 from qa_memory_policy import MemoryPolicy
 from qa_build_guard import require_no_games
 baseline=observe();policy=MemoryPolicy(baseline)
 if protection_baseline is not None:policy.baseline=min(policy.baseline,protection_baseline['swap_mib'])
 start=time.monotonic();job=project/'state'/('fixed-helper-seeds-'+str(time.time_ns()));job.mkdir(mode=0o700)
 calls=[];report=dict(schema=1,status='deferred',known=8,calls=calls,artifact_cache_dir=str(artifact),cohort=cohort,
  original_arguments_preserved=True,storage_opened=False,renderer_owners_constructed=False,formal_data_modified=False)
 def check():
  sample=observe();reason=policy.observe(time.monotonic()-start,sample)
  if reason:raise ProtectionStopped('fixed helper protection stop: '+reason)
  if time.monotonic()-start>120:raise TimeoutError('bounded fixed helper preparation interval')
 def execute(phase,env,ordinal):
  check();require_no_games();case=job/(phase+'-'+str(ordinal));case.mkdir(mode=0o700)
  scratch=case/'scratch';scratch.mkdir();tmp=case/'tmp';tmp.mkdir();packet=case/'packet.json'
  packet.write_text(json.dumps(dict(packet_schema=1,fixed_helper_seed_schema=1,candidate_only=True,
   runtime_cache_import_allowed=False,current_cohort=cohort,artifact_cache_dir=str(artifact)),sort_keys=True)+'\n')
  child={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))}
  child.update({k:v for k,v in env.items()if k=='BB_GS_DEBUG'or k.startswith(('BB_MTL_','BB_LAYER_','BB_PC_','BB_GUEST_'))or k in ('MTL_SHADER_VALIDATION','MTL_SHADER_VALIDATION_ENABLE_PIPELINES','MTL_SHADER_VALIDATION_DISABLE_PIPELINES','MTL_DEBUG_LAYER')})
  child.update(BB_GPU_USER_DIR=str(gpu),BB_MTL_ARCHIVE_COMPATIBILITY_ID=cohort,BB_MTL_PERSIST_WARM_ARCHIVE='0',
   BB_MTL_RELEASE_WARM_ARCHIVE='1',BB_MTL_REPLAY_THREAD='0',HOME=str(case),TMPDIR=str(tmp),DYLD_PRINT_LIBRARIES='1')
  log=case/'console.log';command=[str(component['worker']),phase,str(packet),str(scratch)]
  if runner is not None:row=runner(command,child,case,check)
  else:
   with log.open('w')as out:
    proc=subprocess.Popen(command,env=child,cwd=case,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    try:
     while proc.poll()is None:
      check()
      if log.stat().st_size>8*1024*1024:raise RuntimeError('fixed helper bounded log')
      time.sleep(.1)
    finally:
     if proc.poll()is None:
      os.killpg(proc.pid,signal.SIGTERM)
      try:proc.wait(timeout=3)
      except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
   # Share the established bounded PID/path/SHA admission, including app paths
   # with spaces. Splitting a dyld line on whitespace truncates packaged paths.
   from prepare_game_psos import dyld_gpu_image,verify_loaded_gpu
   rows=[];loaded=set()
   with log.open()as stream:
    while True:
     line=stream.readline(MAX_LINE+1)
     if not line:break
     if len(line)>MAX_LINE:raise ValueError('fixed helper result bound')
     if line.startswith('FIXED_HELPER_RESULT '):rows.append(json.loads(line.split(' ',1)[1]))
     image=dyld_gpu_image(line,proc.pid)
     if image:loaded.add(image)
   if len(rows)!=1:raise RuntimeError('fixed helper result record count')
   loaded_identity=verify_loaded_gpu(loaded,component)
   row=dict(exit=proc.returncode,pid=proc.pid,result=rows[0],log=str(log),loaded_gpu_identity=loaded_identity)
  calls.append(row)
  r,complete=validate_result(row,phase)
  check();return r,complete
 updates={};primary_error=False
 try:
  probe,complete=execute('helpers-probe',environment,0)
  if complete:report.update(status='completed',ready=8,first_probe_ready=True);return updates,report
  if local_prepare is None:
   from local_aot_policy import prepare as local_prepare
  # Probe emitted original LocalLibrary requests. Reuse the SAME local policy,
  # root and cohort; never publish or fabricate a library/function origin here.
  updates,local=local_prepare(mode=mode,gpu_root=gpu,cohort=cohort,engine='arm64',graphics=True)
  report['local']=local;nextenv={**environment,**updates}
  if local.get('status')in('stopped','failed-protection'):raise ProtectionStopped('fixed helper local preparation safety stop')
  if nextenv.get('BB_MTL_LOCAL_AOT_MODE')!='load':report.update(status='source-fallback',reason='local-library-build-unavailable');return updates,report
  previous=-1
  # At most8 fixed entries, same10-second/64-attempt HostKernels limits; each
  # attempt resumes through strict hits, never discards successful artifacts.
  for ordinal in range(1,9):
   prepared,complete=execute('helpers-prepare',nextenv,ordinal)
   if complete:report.update(status='completed',ready=8);return updates,report
   ready=prepared.get('ready',0)
   if ready<=previous:break
   previous=ready
  report.update(status='deferred',ready=max(previous,0),reason='bounded-fixed-preparation-incomplete');return updates,report
 except TimeoutError:
  report.update(status='deferred',reason='fixed-helper-time-budget');return updates,report
 except BaseException as error:
  primary_error=True
  report.update(status='stopped'if isinstance(error,(ProtectionStopped,KeyboardInterrupt,SystemExit))else'failed',error=str(error))
  raise
 finally:
  report.update(seconds=time.monotonic()-start);observation_error=None
  try:report['after']=observe()
  except Exception as error:
   report['after_error']=str(error)
   if not primary_error:
    report.update(status='failed',error='final protection observation unavailable')
    observation_error=error
  try:(job/'result.json').write_text(json.dumps(report,indent=2)+'\n')
  except OSError:pass # Never replace the primary cancellation/protection error.
  if observation_error is not None:raise observation_error
