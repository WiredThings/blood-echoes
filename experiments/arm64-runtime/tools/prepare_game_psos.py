#!/usr/bin/env python3
"""Serial candidate-only game PSO worker supervisor, preserving old caches.

Phases: record exact successful effective-source requests -> existing local
builder -> prepare using the resulting local libraries. Never starts a game.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,signal,stat,subprocess,sys,time
from game_pso_queue import Deferred,Stopped

MAX_BLOB=1024*1024;MAX_TOTAL=8*MAX_BLOB;MAX_LINE=256*1024
HEX=re.compile(r'[0-9a-f]{64}')
VALIDATION_ENV=('MTL_SHADER_VALIDATION','MTL_SHADER_VALIDATION_ENABLE_PIPELINES',
                'MTL_SHADER_VALIDATION_DISABLE_PIPELINES','MTL_DEBUG_LAYER')
class Refused(ValueError):pass
def need(v,why):
 if not v:raise Refused(why)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(v):return hashlib.sha256(v).hexdigest()
def wait_owned_worker(proc,log,check,clock,start,seconds):
 """Wake on exit; preserve the original supervision/owned-group cleanup."""
 try:
  while proc.poll()is None:
   # Keep the same whole-preparation deadline, not a new per-worker budget.
   check()
   if clock()-start>=seconds:raise Deferred('preparation time budget')
   if log.stat().st_size>8*MAX_BLOB:raise Stopped('bounded worker log budget')
   try:proc.wait(timeout=.1)
   except subprocess.TimeoutExpired:pass
 finally:
  if proc.poll()is None:
   os.killpg(proc.pid,signal.SIGTERM)
   try:proc.wait(timeout=3)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
def directory(p):
 p=Path(p);s=p.lstat();need(p.is_absolute()and p==p.resolve(strict=True)and stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned canonical directory');return p
def owned(p,maximum,expected=None):
 p=Path(p);need(p.is_absolute()and p==p.resolve(strict=True),'canonical nonlinked file')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_uid==os.getuid()and 0<a.st_size<=maximum,'owned bounded regular input')
  data=bytearray()
  while len(data)<a.st_size:
   b=os.read(fd,a.st_size-len(data));need(bool(b),'short owned input');data.extend(b)
  b=os.fstat(fd);identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
  need(identity(a)==identity(b),'input changed while reading');data=bytes(data)
  if expected is not None:need(HEX.fullmatch(expected)and sha(data)==expected,'owned input SHA')
  return data
 finally:os.close(fd)
def fresh(parent,name):
 parent=directory(parent);p=parent/name;p.mkdir(mode=0o700);return directory(p)
def new_bytes(root,name,data):
 p=root/name;fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  at=0
  while at<len(data):n=os.write(fd,data[at:]);need(n>0,'short snapshot write');at+=n
  os.fsync(fd)
 finally:os.close(fd)
 return dict(path=str(p),name=name,bytes=len(data),sha256=sha(data))

def freeze(unit,layout,root,cohort,allow_compile,*,cache_root,artifact_cache_dir,audit_stage,audit_key_profile):
 """One owned read-set; caller retains returned immutable bytes until child exits."""
 need(unit['schema']==2 and unit['record_type']=='offline-pso-input','v2 full recipe')
 policy=unit['execution_policy'];need(policy['runtime_cache_import_allowed']is False and
  policy['actual_final_read_admission']['allowed']is False and
  policy['candidate_preparation']['policy_eligible']is True,'candidate-only policy')
 need(unit['source_provenance']['enabled']is False and unit['profile']['compiler_compatibility_proof']is False,'old compatibility not upgraded')
 cache=directory(unit['namespace']['cache']);need(cache.is_relative_to(cache_root),'source outside private Shared state')
 refs=[unit['profile']['reference'],unit['key']['reference']]
 for s in unit['stages']:refs.extend((s['metadata'],s['spirv']))
 need(len(refs)<=14 and sum(r['bytes']for r in refs)<=MAX_TOTAL,'whole read-set budget')
 held={};canonical=[]
 for r in refs:
  p=Path(r['path']);need(p.parent==cache and p.name==r['name'],'same cache-local filename')
  need(p not in held,'duplicate source path in whole recipe')
  b=owned(p,MAX_BLOB,r['sha256']);need(len(b)==r['bytes'],'source byte count');held[p]=b
  canonical.append({k:r[k]for k in('path','name','bytes','sha256')})
 need(digest(canonical)==policy['candidate_preparation']['frozen_input_set_identity'],'frozen read-set identity')
 profile=held[Path(unit['profile']['reference']['path'])];key=held[Path(unit['key']['reference']['path'])]
 audit_key_profile(unit,key,profile,layout)
 packet=dict(packet_schema=1,manifest_schema=2,candidate_only=True,runtime_cache_import_allowed=False,
  native_info_srt_allowed=False,walker_bytes_passed=0,current_cohort=cohort,
  cache_id=unit['namespace']['cache_id'],recipe_blob_identity=unit['recipe_blob_identity'],
  original_cache=str(cache),frozen_input_set_identity=policy['candidate_preparation']['frozen_input_set_identity'],
  allow_compile=bool(allow_compile),artifact_cache_dir=str(artifact_cache_dir),key=new_bytes(root,'key.bin',key),stages=[],
  layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],graphics_key_size=layout['GraphicsPipelineKey.size']))
 for i,s in enumerate(unit['stages']):
  meta=held[Path(s['metadata']['path'])];spv=held[Path(s['spirv']['path'])]
  audit=audit_stage(s,meta,spv,layout);region=audit['sections']['specialization']
  need(region['offset']==24 and region['native_spec_only_reader_allowed']is True,'audited exact spec interval')
  spec=meta[24:24+region['bytes']];need(sha(spec)==region['sha256'],'frozen exact spec SHA')
  row=dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],
   permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],
   spec=new_bytes(root,f'spec-{i}.bin',spec),spirv=new_bytes(root,f'spirv-{i}.bin',spv))
  row['spec'].update(source_offset=24,native_spec_only_reader_allowed=True)
  packet['stages'].append(row)
 encoded=json.dumps(packet,sort_keys=True,separators=(',',':')).encode()+b'\n';need(len(encoded)<=MAX_LINE,'native packet budget')
 new_bytes(root,'packet.json',encoded)
 return packet,held

def rows(path):
 with Path(path).open('rb')as f:
  while True:
   b=f.readline(MAX_LINE+1)
   if not b:return
   need(len(b)<=MAX_LINE and b.endswith(b'\n'),'manifest line budget')
   yield json.loads(b)

def preparation_ready(row):
 """A finished worker is insufficient: require actual native ready outcomes/events."""
 w=row.get('worker');events=row.get('artifact_events',[])
 if not isinstance(w,dict)or not isinstance(events,list):return False
 c=w.get('artifact_counts',{})
 if not isinstance(c,dict):return False
 n=c.get('attempts',0)
 if type(n)is not int:return False
 return (w.get('native_pipeline_ready')is True and w.get('artifact_publication_observed')is True
  and n>0 and len(events)==n and c.get('hits',0)+c.get('built',0)==n
  and c.get('deferred',0)+c.get('failed',0)+c.get('critical',0)+c.get('unavailable',0)==0
  and all(e.get('state')in(1,2)and HEX.fullmatch(e.get('archive_sha256',''))for e in events))

def compile_environment(provided):
 """Preserve actual game validation selection; absence is an actual selection."""
 return {k:v for k,v in provided.items()if k in VALIDATION_ENV or k=='BB_GS_DEBUG' or
         (k.startswith(('BB_MTL_','BB_LAYER_','BB_PC_','BB_GUEST_'))and
          not k.startswith('BB_MTL_LOCAL_AOT_')and k!='BB_MTL_ARCHIVE_COMPATIBILITY_ID')}

DYLD_PATH_MAX=16*1024
DYLD_LIBRARY_MAX=256*MAX_BLOB
_DYLD_IMAGE=re.compile(r'\Adyld(?:\[(?P<pid>[0-9]{1,20})\])?: (?:loaded: )?'
 r'(?:<(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})> )?'
 r'(?P<path>/[^\x00\r\n]+)\Z')

def dyld_gpu_image(line,expected_pid):
 """Read a complete dyld image record, preserving internal path whitespace."""
 need(isinstance(line,str)and len(line)<=MAX_LINE,'bounded dyld record')
 match=_DYLD_IMAGE.fullmatch(line.rstrip('\r\n'))
 if match is None:return None
 text=match['path'];need(len(os.fsencode(text))<=DYLD_PATH_MAX,'dyld path budget')
 path=Path(text)
 if not path.name.startswith('libbbgpu'):return None
 # Supported native hosts emit dyld[pid]. Never let another process or an
 # unbound/forged legacy line authorize this worker's loaded GPU library.
 need(match['pid']is not None and int(match['pid'])==expected_pid,'dyld GPU image PID mismatch')
 need(path.is_absolute(),'absolute dyld GPU image path')
 return str(path.resolve(strict=True))

def verify_loaded_gpu(loaded,identity):
 """Only one actual GPU DSO, bound to its expected path AND owned bytes SHA."""
 expected=Path(identity['library']).resolve(strict=True)
 need(set(loaded)=={str(expected)},'actual runtime GPU load edge changed')
 need(HEX.fullmatch(identity['library_sha256']),'expected GPU library SHA')
 fd=os.open(expected,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  before=os.fstat(fd)
  need(stat.S_ISREG(before.st_mode)and before.st_uid==os.getuid()and before.st_nlink==1 and
       0<before.st_size<=DYLD_LIBRARY_MAX,'owned bounded loaded GPU library')
  fingerprint=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_nlink,
                            value.st_size,value.st_mtime_ns,value.st_ctime_ns)
  digest=hashlib.sha256();total=0
  while True:
   block=os.read(fd,MAX_BLOB)
   if not block:break
   total+=len(block);need(total<=DYLD_LIBRARY_MAX,'loaded GPU library byte budget');digest.update(block)
  after=os.fstat(fd);need(fingerprint(before)==fingerprint(after)and total==before.st_size,'loaded GPU library changed during hash')
  value=digest.hexdigest();need(value==identity['library_sha256'],'actual loaded GPU library SHA mismatch')
  return dict(library=str(expected),library_sha256=value,library_bytes=total,single_gpu_load_edge=True,
              owned_post_worker_sha_verified=True)
 finally:os.close(fd)

def prepare(config,*,services=None):
 """Startup callable; current cache manifest and native identities are explicit.

 services is a trusted internal test seam, never supplied by CLI/request JSON.
 Completion always requires preparation_ready(actual native outcome).
 """
 from types import SimpleNamespace
 import shutil
 from game_pso_queue import run_manifest,Deferred,Stopped
 a=SimpleNamespace(**config) if isinstance(config,dict) else config
 for name,value in dict(phase='all',allow_compile=True,max_recipes=None,seconds=600,page_size=16,
                        progress_root=None,environment=None,retain_worker_scratch=False).items():
  if not hasattr(a,name):setattr(a,name,value)
 P=directory(a.project)  # Data root; imports remain in the loaded tools closure.
 if services is None:
  from game_pso_owned_audit import audit_owned_stage,audit_owned_key_profile,load_canonical_layout
  from qa_build_guard import require_no_games
  from native_observer import NativeObserver
  from qa_memory_policy import MemoryPolicy
  from warm_archive_manifest import resolve_gpu_identity
  # Data globals are explicit; code imports stay in this loaded closure.
  import local_aot_policy,local_metal_builder
  local_aot_policy.P=P
  local_metal_builder.PROJECT=P
  from local_aot_policy import prepare as local_prepare
  from local_metal_builder import build as local_build,NativeToolchain,FixedRunner,Stopped as BuilderStopped,PreparationSession
  services=dict(audit_stage=audit_owned_stage,audit_key_profile=audit_owned_key_profile,load_layout=load_canonical_layout,
    require_no_games=require_no_games,observer_factory=NativeObserver,memory_policy=MemoryPolicy,resolve_identity=resolve_gpu_identity,
    local_prepare=local_prepare,local_build=local_build,toolchain=NativeToolchain,runner=FixedRunner,builder_stopped=BuilderStopped,builder_session_factory=PreparationSession)
 observer=None;require_no_games=services['require_no_games'];policy_type=services['memory_policy']
 need(HEX.fullmatch(a.cohort)and HEX.fullmatch(a.library_sha256)and HEX.fullmatch(a.layout_sha256),'current identities required')
 need((a.max_recipes is None or type(a.max_recipes)is int and a.max_recipes>0)and 0<a.seconds<=7200 and 0<a.page_size<=64,'explicit bounded work budget')
 run=directory(a.run_root);gpu=directory(a.gpu_root);artifact=directory(a.artifact_cache_dir)
 need(artifact.is_relative_to(gpu)and artifact!=gpu,'exact original selected PsoCache artifact/cache_dir required')
 need(run.parent==P/'state'and gpu.is_relative_to(P/'state')and run!=gpu and not any(run.iterdir()),'fresh Shared run and selected artifact roots')
 need(Path(a.manifest).resolve(strict=True).is_relative_to(P)and Path(a.layout).resolve(strict=True).is_relative_to(P),'actual runtime inputs must be Shared')
 progress=Path(a.progress_root)if a.progress_root else gpu/'game-pso-progress'
 need(progress.is_absolute()and progress.is_relative_to(gpu)and progress!=gpu,'progress must stay below selected GPU root')
 if not progress.exists():progress.mkdir(mode=0o700)
 directory(progress)
 require_no_games();identity=services['resolve_identity'](a.worker,P);need(identity['library_sha256']==a.library_sha256,'actual selected worker library SHA')
 layout=services['load_layout'](a.layout,a.source_root,a.layout_sha256)
 worker_sha=sha(owned(Path(a.worker).resolve(strict=True),32*MAX_BLOB))
 provided=dict(os.environ if a.environment is None else a.environment)
 options=compile_environment(provided)
 effective_options=dict(options,BB_MTL_PERSIST_WARM_ARCHIVE='0',BB_MTL_RELEASE_WARM_ARCHIVE='1')
 context=dict(worker_sha256=worker_sha,library_sha256=a.library_sha256,layout_sha256=a.layout_sha256,
              cohort=a.cohort,options_identity=digest(effective_options),artifact_cache_dir=str(artifact),
              runner_sha256=sha(Path(__file__).read_bytes()))
 if getattr(a,'options_identity',None):need(HEX.fullmatch(a.options_identity),'declared options identity');context['declared_options_identity']=a.options_identity
 observer=services.get('observer') or services['observer_factory']()
 before=observer.system()
 if before['locked']or before['pressure']==4:
  result=dict(status='stopped',error='locked or severe pressure at preflight',completed=0,protection_stops=1,game_started=False)
  (run/'result.json').write_text(json.dumps(result,indent=2)+'\n');observer.close();return result
 clock=services.get('clock',time.monotonic)
 policy=policy_type(before);start=clock();samples=[];sample_count=0;outcomes=[];outcome_count=0;phase_env={};stop_error=None
 samples_path=run/'samples.jsonl';outcomes_path=run/'outcomes.jsonl';events_path=run/'events.jsonl'
 builder_tmp=fresh(run,'local-builder-tmp');result={}
 session_factory=services.get('builder_session_factory');builder_session=session_factory(builder_tmp)if session_factory else None
 validation_anchor=None;validation_environment=None
 def check():
  nonlocal sample_count
  elapsed=clock()-start
  try:s=observer.system()
  except Exception as error:raise Stopped('cannot observe protection state: '+str(error))from error
  sample=dict(seconds=elapsed,**s);sample_count+=1
  if len(samples)<64:samples.append(sample)
  with samples_path.open('a')as stream:stream.write(json.dumps(sample,separators=(',',':'))+'\n')
  reason=policy.observe(elapsed,s)
  if reason:raise Stopped(reason)
 def prep_phase(phase,guard):
  nonlocal stop_error,validation_anchor,validation_environment
  if phase=='record'and builder_session is not None:builder_session.invalidate('record-phase')
  class BuilderGuard:
   def check(self,**kwargs):
    nonlocal stop_error
    try:
     guard()
     if kwargs.get('preflight'):require_no_games()
    except (Deferred,Stopped,KeyboardInterrupt)as error:
     stop_error=error;raise services['builder_stopped'](str(error))
  if phase in('snapshot','verify'):
   try:
    guard();current=digest(phase_env.get('prepare',{}))
    if phase=='snapshot':
     if validation_anchor is not None:validation_anchor.close()
     validation_anchor=builder_session.begin_validation(gpu/'local-metal',a.cohort,BuilderGuard())if builder_session is not None else None
     validation_environment=current
     return dict(status='environment-captured'if validation_anchor is not None else'unavailable',environment_identity=current)
    valid=validation_anchor is not None and validation_environment==current and validation_anchor.unchanged(BuilderGuard())
    return dict(status='environment-verified'if valid else'changed',environment_identity=current,read_only_verification=True)
   except services['builder_stopped']as error:
    if stop_error is not None:raise stop_error
    raise Stopped('environment verification safety stop: '+str(error))from error
  def builder(root,cohort,**kwargs):
   if builder_session is not None:
    return builder_session.build(root,cohort,guard=BuilderGuard(),toolchain_factory=lambda:services['toolchain'](services['runner'](builder_tmp)),**kwargs)
   return services['local_build'](root,cohort,guard=BuilderGuard(),
     toolchain=services['toolchain'](services['runner'](builder_tmp)),**kwargs)
  updates,report=services['local_prepare'](mode='record'if phase=='record'else'auto',gpu_root=gpu,cohort=a.cohort,
       engine='arm64',graphics=True,builder=builder)
  if stop_error is not None:raise stop_error
  if report['status']=='stopped':raise Stopped(report.get('error','local library protection stop'))
  phase_env[phase]=updates;return report
 def execute(unit,phase,allow_compile,ordinal):
  nonlocal outcome_count
  check();require_no_games();case=fresh(run,f'{phase}-{ordinal:06d}');packetdir=fresh(case,'packet');scratch=fresh(case,'source-cache');tmp=fresh(case,'tmp')
  if 'owned_geometry_pso'in unit:
   from game_pso_geometry_adapter import freeze as geometry_freeze
   packet,held=geometry_freeze(unit,layout,packetdir,a.cohort,bool(allow_compile and a.allow_compile),artifact_cache_dir=artifact,check=check)
  else:
   packet,held=freeze(unit,layout,packetdir,a.cohort,bool(allow_compile and a.allow_compile),cache_root=P/'state',artifact_cache_dir=artifact,
         audit_stage=services['audit_stage'],audit_key_profile=services['audit_key_profile'])
  env={k:v for k,v in os.environ.items()if not k.startswith(('BB_','DYLD_'))and k not in VALIDATION_ENV};env.update(options);env.update(phase_env[phase])
  env.update(BB_GPU_USER_DIR=str(gpu),BB_MTL_ARCHIVE_COMPATIBILITY_ID=a.cohort,BB_MTL_PERSIST_WARM_ARCHIVE='0',
             BB_MTL_RELEASE_WARM_ARCHIVE='1',HOME=str(case),TMPDIR=str(tmp),DYLD_PRINT_LIBRARIES='1')
  log=case/'console.log';command=[str(Path(a.worker).resolve()),phase,str(packetdir/'packet.json'),str(scratch)]
  with log.open('w')as stream:
   proc=subprocess.Popen(command,env=env,cwd=case,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
   wait_owned_worker(proc,log,check,clock,start,a.seconds)
  need(sum(map(len,held.values()))<=MAX_TOTAL,'retained original read-set budget');del held
  events=[];workers=[];loaded=set()
  with log.open()as stream:
   while True:
    line=stream.readline(MAX_LINE+1)
    if not line:break
    need(len(line)<=MAX_LINE,'bounded worker result line')
    if line.startswith('GAME_PSO_RESULT '):workers.append(json.loads(line.split(' ',1)[1]));need(len(workers)<=1,'one actual worker result')
    if line.startswith('Metal game PSO artifact event: '):events.append(json.loads(line.split(': ',1)[1]));need(len(events)<=64,'bounded actual artifact events')
    image=dyld_gpu_image(line,proc.pid)
    if image is not None:loaded.add(image);need(len(loaded)<=1,'multiple actual GPU libraries')
  loaded_identity=verify_loaded_gpu(loaded,identity)
  row=dict(phase=phase,ordinal=ordinal,pid=proc.pid,exit=proc.returncode,recipe=unit['recipe_blob_identity'],worker=workers[0]if len(workers)==1 else None,
           artifact_events=events,log=str(log),loaded_gpu_identity=loaded_identity,runtime_cache_import_allowed=False,actual_final_read_admission=False)
  (case/'result.json').write_text(json.dumps(row,indent=2)+'\n');outcome_count+=1
  if len(outcomes)<64:outcomes.append(row)
  with outcomes_path.open('a')as stream:stream.write(json.dumps(row,separators=(',',':'))+'\n')
  if not a.retain_worker_scratch:
   # These are this worker's brand-new temporary inputs/outputs, never old caches.
   for child in (packetdir,scratch,tmp):shutil.rmtree(child)
  return row
 def event(value):
  with events_path.open('a')as stream:stream.write(json.dumps(value,separators=(',',':'))+'\n')
 try:
  result=run_manifest(a.manifest,progress_root=progress,context=context,execute=execute,prepare_phase=prep_phase,check=check,
       ready=preparation_ready,phase=a.phase,page_size=a.page_size,max_recipes=a.max_recipes,seconds=a.seconds,clock=clock,event_sink=event)
 finally:
  if validation_anchor is not None:validation_anchor.close()
  if builder_session is not None:
   builder_session.close();result['builder_session']=dict(builder_session.stats)
  result.update(before=before,after=observer.system(),samples_preview=samples,sample_count=sample_count,samples_jsonl=str(samples_path),
    memory_policy=policy_type.MANIFEST,wall_seconds=clock()-start,outcomes_preview=outcomes,outcome_count=outcome_count,
    outcomes_jsonl=str(outcomes_path),events_jsonl=str(events_path),worker_identity=identity,current_context=context,
    runtime_cache_import_allowed=False,actual_final_read_admission=False,game_started=False)
  (run/'result.json').write_text(json.dumps(result,indent=2)+'\n');observer.close()
 return result

def main(argv=None):
 ap=argparse.ArgumentParser()
 for field in ('project','worker','manifest','layout','source-root','gpu-root','run-root','artifact-cache-dir'):
  ap.add_argument('--'+field,required=True,type=Path)
 for field in ('library-sha256','layout-sha256','cohort'):ap.add_argument('--'+field,required=True)
 ap.add_argument('--progress-root',type=Path);ap.add_argument('--phase',choices=['record','prepare','all'],default='all')
 ap.add_argument('--max-recipes',type=int,default=None);ap.add_argument('--page-size',type=int,default=16);ap.add_argument('--seconds',type=int,default=600)
 ap.add_argument('--allow-compile',action=argparse.BooleanOptionalAction,default=True)
 ap.add_argument('--retain-worker-scratch',action='store_true')
 result=prepare(vars(ap.parse_args(argv)))
 print(json.dumps({k:v for k,v in result.items()if k not in('samples_preview','outcomes_preview','events_preview')},indent=2))
 return 0 if result['status']in('completed','no-candidates')else 70 if result['status']=='stopped'else 65
if __name__=='__main__':raise SystemExit(main())
