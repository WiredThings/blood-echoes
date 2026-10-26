"""Freeze/verify a host-local preparation worker; no game cache or game data."""
from pathlib import Path
import hashlib,json,os,re,stat,shutil,subprocess,tempfile,select,time,signal
from game_pso_owned_audit import checked_bytes,load_canonical_layout
HEX=re.compile('[0-9a-f]{64}')
def need(v,m):
 if not v:raise ValueError(m)
def sha(p,*,owner=None):
 if owner is None:owner=os.getuid()
 p=Path(p);need(p.is_absolute()and p==p.resolve(strict=True),'canonical component input')
 fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_uid==owner and a.st_size<=512*1024*1024,'owned bounded component')
  with os.fdopen(fd,'rb',closefd=False)as f:h=hashlib.file_digest(f,'sha256').hexdigest()
  b=os.fstat(fd);need((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns),'component changed')
  return h
 finally:os.close(fd)
def relative(root,rel):
 p=Path(rel);need(not p.is_absolute()and '..'not in p.parts and str(p)==rel,'component relative path');return root/p

def runtime_layout_bytes(layout):
 """Retain ABI/proof fields, excluding developer-only Writer fixture paths.

 The source component verified those fixtures; they are not runtime inputs.
 The frozen manifest binds the serialized runtime layout's separate SHA.
 """
 row=json.loads(json.dumps(layout));fixtures=row.get('authored_writer_fixtures',{})
 need(isinstance(fixtures,dict),'layout fixture receipt domain')
 for item in fixtures.values():
  need(isinstance(item,dict),'layout fixture receipt shape');item.pop('path',None)
 return(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode()

def repair_input_sha(path):
 """Artifacts stay user-owned; only the selected SDK settings may be root-owned."""
 path=Path(path)
 if path.stat().st_uid==os.getuid():return sha(path)
 need(path.name=='SDKSettings.json'and path.stat().st_uid==0,'unowned repair input')
 sdk=Path(subprocess.check_output(['/usr/bin/xcrun','--sdk','macosx','--show-sdk-path'],
  env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin'},text=True,timeout=10).strip()).resolve(strict=True)
 need(path==sdk/'SDKSettings.json'and sdk.stat().st_uid==0,'repair SDK is not the selected root-owned SDK')
 return sha(path,owner=0)

def bind_module_repair(component_path,repair_path,*,admission_root,output):
 """Bind a tested main-only correction to the unchanged coherent GPU/ABI.
 Never rewrite the completed base result or silently bless compiler overrides.
 """
 root=Path(admission_root).resolve(strict=True);output=Path(output).absolute()
 need(output.is_relative_to(root/'evidence')and not output.exists(),'fresh isolated repaired component manifest')
 base=json.loads(checked_bytes(Path(component_path),limit=1024*1024)[0])
 repair=json.loads(checked_bytes(Path(repair_path),limit=1024*1024)[0])
 need(base['schema']==repair['schema']==1 and base['kind']=='blood-echoes-game-pso-component'
      and repair['kind']=='coherent-game-module-main-repair','exact component/repair contracts')
 need(base.get('coherent_renderer')is True and base.get('diagnostic_only')is False
      and repair.get('targeted_compiler_overrides')is False and repair.get('shared_abi_headers_unchanged')is True,
      'coherent shared-ABI main repair only')
 need(sha(Path(repair['base_result']))==repair['base_result_sha256']==base['coherent_build_result_sha256'],
      'repair must reference the exact completed base')
 need(repair['source_identity']==base['coherent_source_identity'],'same coherent GPU source identity')
 need(repair['library']==base['library']and repair['library_sha256']==base['library_sha256'],
      'repair may not replace the renderer')
 for field in('library','worker'):
  need(sha(Path(base[field]))==base[field+'_sha256'],'base artifact changed: '+field)
 need(sha(Path(repair['module_consumer']))==repair['module_consumer_sha256'],'repaired module consumer changed')
 need(sha(Path(repair['main_source']))==repair['main_source_sha256'],'repaired main source changed')
 for path,want in repair['compiled_header_sha256'].items():
  need(repair_input_sha(path)==want,'repaired consumer header changed')
 load_canonical_layout(Path(base['layout']),Path(base['source_root']),base['layout_sha256'])
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(Path(repair['module_consumer']),root)
 need(actual['library']==base['library']and actual['library_sha256']==base['library_sha256'],
      'repaired consumer actual GPU edge mismatch')
 result=dict(base,module_consumer=repair['module_consumer'],module_consumer_sha256=repair['module_consumer_sha256'],
             module_main_repair=dict(manifest_sha256=sha(Path(repair_path)),source_sha256=repair['main_source_sha256'],
                                   base_source_identity=repair['source_identity']))
 with output.open('x')as stream:json.dump(result,stream,indent=2);stream.write('\n')
 return result

class _CapabilityExitWatch:
 """Observe exit without reaping, keeping the owned PID/group reserved."""
 def __init__(self,pid):
  self.pid=pid;self.done=False;self.queue=None
  if hasattr(os,'waitid')and hasattr(os,'WNOWAIT'):return
  if not hasattr(select,'kqueue'):raise RuntimeError('non-reaping child exit observation unavailable')
  self.queue=select.kqueue()
  try:self.queue.control([select.kevent(pid,filter=select.KQ_FILTER_PROC,flags=select.KQ_EV_ADD|select.KQ_EV_ONESHOT,fflags=select.KQ_NOTE_EXIT)],0,0)
  except ProcessLookupError:self.done=True # Exited before registration, not reaped.
  except BaseException:self.queue.close();raise
 def exited(self):
  if self.done:return True
  if self.queue is None:
   self.done=os.waitid(os.P_PID,self.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)is not None
  else:
   for event in self.queue.control(None,1,0):
    if event.flags&select.KQ_EV_ERROR:raise OSError(event.data,'owned process exit watch')
    if event.fflags&select.KQ_NOTE_EXIT:self.done=True
  return self.done
 def close(self):
  if self.queue is not None:self.queue.close()

def _signal_capability_group(proc,watch,sig):
 try:os.killpg(proc.pid,sig)
 except ProcessLookupError:pass
 except PermissionError:
  # Darwin returns EPERM for a group containing only the unreaped zombie
  # leader, not ESRCH. Accept it only after a non-reaping kernel exit event.
  # The trusted scalar CLI and its same-uid children may not change session/uid.
  if watch is None or not watch.exited():raise

def _run_bounded_capability(command,*,stdin,stdout,stderr,timeout,env):
 """Owned session, <=4097 captured bytes, <=10s work, TERM/KILL+wait.
 No poll()/wait() reaps the leader before group cleanup, so its PID cannot be
 reused for an unrelated group. Only this Popen's own process group is signalled.
 Detached sessions are outside the scalar-worker contract; it must not daemonize.
 """
 need(stdin==subprocess.DEVNULL and stdout==subprocess.PIPE and stderr==subprocess.DEVNULL,'capability pipe contract')
 need(isinstance(timeout,(int,float))and 0<timeout<=10,'capability timeout bound')
 proc=None;watch=None;captured=bytearray();deadline=time.monotonic()+timeout
 try:
  proc=subprocess.Popen(command,stdin=stdin,stdout=subprocess.PIPE,stderr=stderr,
   env=env,start_new_session=True,bufsize=0)
  fd=proc.stdout.fileno();os.set_blocking(fd,False);watch=_CapabilityExitWatch(proc.pid);eof=False
  while True:
   if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(command,timeout)
   # Read at most the remaining budget+one sentinel; never communicate()/PIPE
   # an unbounded result before checking the output limit.
   while not eof:
    try:chunk=os.read(fd,min(1024,4097-len(captured)))
    except BlockingIOError:break
    if not chunk:eof=True;break
    captured.extend(chunk)
    if len(captured)>4096:raise ValueError('worker capability output bound')
   if watch.exited():
    # Exit is observed but the leader remains unreaped. Drain current pipe data
    # once more, then cleanup any same-session descendants before wait().
    while not eof:
     try:chunk=os.read(fd,min(1024,4097-len(captured)))
     except BlockingIOError:break
     if not chunk:eof=True;break
     captured.extend(chunk)
     if len(captured)>4096:raise ValueError('worker capability output bound')
    break
   remaining=deadline-time.monotonic()
   if remaining<=0:raise subprocess.TimeoutExpired(command,timeout)
   select.select([]if eof else[fd],[],[],min(.02,remaining))
 finally:
  if proc is not None:
   # Keep the leader unreaped until BOTH group signals are complete. Never
   # signal a cached/global PID or another task's process.
   interrupted=False
   try:
    try:
     _signal_capability_group(proc,watch,signal.SIGTERM)
     try:time.sleep(.05)
     except (KeyboardInterrupt,InterruptedError):interrupted=True
    finally:
     _signal_capability_group(proc,watch,signal.SIGKILL)
   finally:
    try:
     reap_deadline=time.monotonic()+2
     while True:
      try:proc.wait(timeout=max(.001,reap_deadline-time.monotonic()));break
      except (KeyboardInterrupt,InterruptedError):interrupted=True
      if time.monotonic()>=reap_deadline:raise RuntimeError('owned capability leader could not be reaped')
    finally:
     if proc.stdout is not None:proc.stdout.close()
     if watch is not None:watch.close()
   if interrupted:raise KeyboardInterrupt
 return subprocess.CompletedProcess(command,proc.returncode,bytes(captured),None)

def fixed_helper_capability(worker,admission_root,*,runner=None,runtime_profile=None):
 """Pure scalar ABI query AFTER relocation; no Metal Device or GPU work.
 Old workers return usage64. New workers must return the exact bounded schema.
 Only a fresh owned state directory is created and removed; no old cache touched.
 """
 if runner is None:runner=_run_bounded_capability
 from launcher_runtime_profile import capability_environment
 with capability_environment(admission_root,runtime_profile,'component-helper-cap-')as env:
  result=runner([str(worker),'--fixed-helper-seed-schema'],stdin=subprocess.DEVNULL,
   stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,env=env)
  need(isinstance(result.stdout,bytes)and len(result.stdout)<=4096,'worker capability output bound')
  if result.returncode==64:return 0
  # A relocated schema1 worker is a known older component, unavailable for the
  # stronger schema2 library contract. Never silently interpret it as schema2.
  if result.returncode==0 and result.stdout==b'FIXED_HELPER_CAPABILITY 1\n':return 0
  need(result.returncode==0 and len(result.stdout)<=4096 and result.stdout==b'FIXED_HELPER_CAPABILITY 2\n','worker fixed helper ABI query failed')
  return 2

def prepare_inputs(stage,component,library,*,rewrite,admission_root):
 """Copy/relocate verified inputs only; never query or publish an enabled component."""
 stage=Path(stage).resolve(strict=True);library=Path(library).resolve(strict=True)
 need(not os.path.lexists(stage/'GAME-PSO-COMPONENT.json'),'component must not be published before input preparation')
 raw,_=checked_bytes(Path(component),limit=1024*1024);m=json.loads(raw)
 need(m['schema']==1 and m['kind']=='blood-echoes-game-pso-component'and m['production']is False,'admitted private component required')
 source=Path(m['worker']);need(sha(source)==m['worker_sha256'],'worker source identity')
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(source,Path(admission_root).resolve(strict=True))
 need(actual['library']==str(Path(m['library']).resolve(strict=True))and
      actual['library_sha256']==m['library_sha256'],'worker actual GPU edge/source library identity mismatch')
 # The input DSO identity is the admitted coherent pre-packaging library. The
 # caller verifies it against its GPU build before rewriting copied load edges.
 layout=Path(m['layout']);source_root=Path(m['source_root'])
 l=load_canonical_layout(layout,source_root,m['layout_sha256'])
 root=stage/'native-runtime-inputs';root.mkdir(mode=0o700)
 dst=root/'layout.json';dst.write_bytes(runtime_layout_bytes(l));runtime_layout_sha=sha(dst)
 headers=root/'layout-source';headers.mkdir()
 for name,want in l['source_sha256'].items():
  src=relative(source_root,name);data,_=checked_bytes(src,limit=4*1024*1024,expected=want)
  out=relative(headers,name);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
 load_canonical_layout(dst,headers,runtime_layout_sha)
 worker=stage/'bin/game-pso-preparer';shutil.copy2(source,worker)
 need(sha(worker)==m['worker_sha256'],'copied worker changed before relocation')
 rewrite(worker,Path(m['library']),library)
 # No development-source paths survive in the installed runtime manifest.
 frozen=dict(schema=1,enabled=False,kind=m['kind'],production=False,
  worker=str(worker.relative_to(stage)),worker_sha256=sha(worker),
  library=str(library.relative_to(stage)),library_sha256=sha(library),
  layout=str(dst.relative_to(stage)),layout_sha256=runtime_layout_sha,source_layout_sha256=m['layout_sha256'],
  source_root=str(headers.relative_to(stage)))
 if m.get('module_consumer')is not None:
  # Coherent module input conversion is optional only for historical component
  # admission. New automatic builds provide it and the actual scalar capability.
  module=Path(m['module_consumer']);need(m.get('coherent_renderer')is True and m.get('diagnostic_only')is False,
      'module component must be a coherent non-diagnostic build')
  need(sha(module)==m['module_consumer_sha256'],'module consumer source identity')
  edge=resolve_gpu_identity(module,Path(admission_root).resolve(strict=True))
  need(edge['library']==str(Path(m['library']).resolve(strict=True))and edge['library_sha256']==m['library_sha256'],
      'module consumer actual GPU edge/source library identity mismatch')
  target=stage/'bin/game-module-preparer';shutil.copy2(module,target)
  rewrite(target,Path(m['library']),library)
  frozen.update(module_consumer=str(target.relative_to(stage)),module_consumer_sha256=sha(target),
                coherent_renderer=True,diagnostic_only=False)
 if m.get('automatic_capture_schema')is not None:
  need(m['automatic_capture_schema']==1,'unknown automatic capture schema')
  frozen['automatic_capture_schema']=1
 identities={str(path.relative_to(stage)):sha(path)for path in [worker,library,dst,*([stage/frozen['module_consumer']]if'module_consumer'in frozen else[]),*(path for path in headers.rglob('*')if path.is_file())]}
 return dict(candidate_schema=1,kind='blood-echoes-prepared-component-inputs',stage=str(stage),component=frozen,
             copied_sha256=identities,capability_queried=False,component_published=False)


def _prepared_candidate(stage,candidate):
 need(candidate.get('candidate_schema')==1 and candidate.get('kind')=='blood-echoes-prepared-component-inputs'and candidate.get('stage')==str(stage)and candidate.get('capability_queried')is False and candidate.get('component_published')is False,'unpublished prepared component input contract')
 frozen=dict(candidate['component'])
 need(frozen.get('schema')==1 and frozen.get('kind')=='blood-echoes-game-pso-component'and frozen.get('enabled')is False and frozen.get('production')is False and'fixed_helper_seed_schema'not in frozen,'component candidate cannot claim capability or enablement')
 expected={frozen[name]for name in('worker','library','layout')}
 if'module_consumer'in frozen:expected.add(frozen['module_consumer'])
 headers=relative(stage,frozen['source_root']);expected.update(str(path.relative_to(stage))for path in headers.rglob('*')if path.is_file())
 need(set(candidate['copied_sha256'])==expected,'prepared component copied file closure changed')
 for name,want in candidate['copied_sha256'].items():
  need(HEX.fullmatch(want)and sha(relative(stage,name))==want,'prepared component bytes changed: '+name)
 for name in('worker','library','layout','module_consumer'):
  if name in frozen:need(candidate['copied_sha256'][frozen[name]]==frozen[name+'_sha256'],'candidate field/file identity mismatch')
 load_canonical_layout(relative(stage,frozen['layout']),headers,frozen['layout_sha256'])
 return frozen


def finalize_inputs(stage,candidate,*,admission_root,runtime_profile=None,production=False,capability_runner=None):
 """Publish only after a real scalar query on final copied, stable code."""
 stage=Path(stage).resolve(strict=True);need(type(production)is bool,'explicit component profile')
 need(not os.path.lexists(stage/'GAME-PSO-COMPONENT.json'),'refuse overwriting a published component')
 frozen=_prepared_candidate(stage,candidate)
 if production:
  from launcher_runtime_profile import PackagingCapabilityProfile
  need(isinstance(runtime_profile,PackagingCapabilityProfile),'formal finalization requires a packaging-only capability profile')
  admitted=runtime_profile.revalidate();need(admitted.stage==stage,'packaging capability belongs to another bundle')
  for name,want in dict(worker='bin/game-pso-preparer',module_consumer='bin/game-module-preparer',library='bin/gpu-metal-arm64/libbbgpu.dylib',layout='native-runtime-inputs/layout.json',source_root='native-runtime-inputs/layout-source').items():
   need(frozen.get(name)==want,'formal final component role mismatch: '+name)
  for name in('worker','library','module_consumer'):
   need(admitted.native['frozen_runtime'][frozen[name]]==frozen[name+'_sha256'],'final component differs from actual frozen bundle')
 else:need(runtime_profile is None,'old QA finalization retains its original Shared scalar path')
 from warm_archive_manifest import resolve_gpu_identity
 code_args={'code_owners':(0,os.getuid())}if production else{}
 for name in('worker','module_consumer'):
  if name not in frozen:continue
  actual=resolve_gpu_identity(relative(stage,frozen[name]),stage,**code_args)
  need(actual['library']==str(relative(stage,frozen['library']))and actual['library_sha256']==frozen['library_sha256'],'final copied consumer actual DSO edge differs')
 capability=fixed_helper_capability(relative(stage,frozen['worker']),admission_root,runner=capability_runner,runtime_profile=runtime_profile)
 need(not production or capability==2,'formal component requires the actual fixed helper schema2 query')
 _prepared_candidate(stage,candidate)
 if production:runtime_profile.revalidate()
 frozen.update(enabled=True,production=production,fixed_helper_seed_schema=capability)
 # The candidate was never usable. Only this completed scalar query publishes
 # the final manifest, without replacing an earlier successful component.
 with(stage/'GAME-PSO-COMPONENT.json').open('x')as stream:json.dump(frozen,stream,indent=2);stream.write('\n')
 return frozen


def freeze_inputs(stage,component,library,*,rewrite,admission_root):
 """Historical QA API: the same verified copy stage then original scalar path."""
 candidate=prepare_inputs(stage,component,library,rewrite=rewrite,admission_root=admission_root)
 return finalize_inputs(stage,candidate,admission_root=admission_root)

def load(stage,*,runtime_profile=None):
 stage=Path(stage).resolve(strict=True);path=stage/'GAME-PSO-COMPONENT.json'
 if not path.exists():return None
 code_args={'code_owners':(0,os.getuid())}if runtime_profile is not None else{}
 if runtime_profile is not None:need(runtime_profile.bundle.stage==stage,'formal component scope stage')
 raw,_=checked_bytes(path,**code_args);m=json.loads(raw)
 need(m['schema']==1 and m['kind']=='blood-echoes-game-pso-component'and m['enabled']is True and m['production']is (runtime_profile is not None),'invalid preparation component')
 if runtime_profile is not None:
  for key,value in dict(worker='bin/game-pso-preparer',module_consumer='bin/game-module-preparer',library='bin/gpu-metal-arm64/libbbgpu.dylib',layout='native-runtime-inputs/layout.json',source_root='native-runtime-inputs/layout-source').items():
   need(m.get(key)==value,'formal component role mismatch: '+key)
 def code_sha(p):return sha(p,owner=0 if runtime_profile is not None and p.stat().st_uid==0 else os.getuid())
 result=dict(m)
 for field in('worker','library','layout','source_root'):
  p=relative(stage,m[field]);need(p==p.resolve(strict=True)and p.is_relative_to(stage),'linked/outside component')
  result[field]=p
  if field!='source_root':need(HEX.fullmatch(m[field+'_sha256'])and code_sha(p)==m[field+'_sha256'],'packaged component identity changed: '+field)
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(result['worker'],stage,**code_args)
 need(actual['library']==str(result['library'])and actual['library_sha256']==result['library_sha256'],
      'packaged worker actual GPU edge/library mismatch')
 result['native_layout']=load_canonical_layout(result['layout'],result['source_root'],result['layout_sha256'],**code_args)
 if m.get('module_consumer')is not None:
  module=relative(stage,m['module_consumer'])
  need(module==module.resolve(strict=True)and module.is_relative_to(stage),'linked/outside module consumer')
  need(HEX.fullmatch(m['module_consumer_sha256'])and code_sha(module)==m['module_consumer_sha256'],
       'packaged module consumer changed')
  edge=resolve_gpu_identity(module,stage,**code_args)
  need(edge['library']==str(result['library'])and edge['library_sha256']==result['library_sha256'],
       'packaged module consumer actual GPU mismatch')
  need(m.get('coherent_renderer')is True and m.get('diagnostic_only')is False,'invalid module producer type')
  result.update(module_consumer=module,backend_root=stage)
 if m.get('automatic_capture_schema')is not None:
  need(m['automatic_capture_schema']==1,'unknown packaged capture schema')
  exports=subprocess.check_output(['/usr/bin/nm','-gjU',str(result['library'])],text=True,timeout=10).splitlines()
  need('_bb_fetch_companion_configure_v1'in exports,'packaged capture capability is not compiled in')
  need(code_sha(result['library'])==result['library_sha256'],'capture library changed during capability check')
 return result
