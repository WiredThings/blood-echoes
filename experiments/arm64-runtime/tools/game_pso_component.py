"""Freeze/verify a host-local preparation worker; no game cache or game data."""
from pathlib import Path
import hashlib,json,os,re,stat,shutil,subprocess,tempfile,select,time,signal
from game_pso_owned_audit import checked_bytes,load_canonical_layout
HEX=re.compile('[0-9a-f]{64}')
def need(v,m):
 if not v:raise ValueError(m)
def sha(p):
 p=Path(p);need(p.is_absolute()and p==p.resolve(strict=True),'canonical component input')
 fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_uid==os.getuid()and a.st_size<=512*1024*1024,'owned bounded component')
  with os.fdopen(fd,'rb',closefd=False)as f:h=hashlib.file_digest(f,'sha256').hexdigest()
  b=os.fstat(fd);need((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns),'component changed')
  return h
 finally:os.close(fd)
def relative(root,rel):
 p=Path(rel);need(not p.is_absolute()and '..'not in p.parts and str(p)==rel,'component relative path');return root/p

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

def fixed_helper_capability(worker,admission_root,*,runner=None):
 """Pure scalar ABI query AFTER relocation; no Metal Device or GPU work.
 Old workers return usage64. New workers must return the exact bounded schema.
 Only a fresh owned state directory is created and removed; no old cache touched.
 """
 if runner is None:runner=_run_bounded_capability
 base=Path(admission_root).resolve(strict=True)/'state'
 need(base==base.resolve(strict=True)and base.is_dir()and base.stat().st_uid==os.getuid(),'owned helper capability data root')
 with tempfile.TemporaryDirectory(prefix='component-helper-cap-',dir=base)as tmp:
  env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':tmp,'TMPDIR':tmp,'BB_GPU_USER_DIR':tmp}
  result=runner([str(worker),'--fixed-helper-seed-schema'],stdin=subprocess.DEVNULL,
   stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,env=env)
  need(isinstance(result.stdout,bytes)and len(result.stdout)<=4096,'worker capability output bound')
  if result.returncode==64:return 0
  # A relocated schema1 worker is a known older component, unavailable for the
  # stronger schema2 library contract. Never silently interpret it as schema2.
  if result.returncode==0 and result.stdout==b'FIXED_HELPER_CAPABILITY 1\n':return 0
  need(result.returncode==0 and len(result.stdout)<=4096 and result.stdout==b'FIXED_HELPER_CAPABILITY 2\n','worker fixed helper ABI query failed')
  return 2

def freeze_inputs(stage,component,library,*,rewrite,admission_root):
 """Call AFTER dependency relocation/signing; bind final copied GPU and worker."""
 stage=Path(stage).resolve(strict=True);library=Path(library).resolve(strict=True)
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
 dst=root/'layout.json';shutil.copy2(layout,dst);need(sha(dst)==m['layout_sha256'],'copied native layout changed')
 headers=root/'layout-source';headers.mkdir()
 for name,want in l['source_sha256'].items():
  src=relative(source_root,name);data,_=checked_bytes(src,limit=4*1024*1024,expected=want)
  out=relative(headers,name);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
 worker=stage/'bin/game-pso-preparer';shutil.copy2(source,worker)
 need(sha(worker)==m['worker_sha256'],'copied worker changed before relocation')
 rewrite(worker,Path(m['library']),library)
 # No development-source paths survive in the installed runtime manifest.
 frozen=dict(schema=1,enabled=True,kind=m['kind'],production=False,
  worker=str(worker.relative_to(stage)),worker_sha256=sha(worker),
  library=str(library.relative_to(stage)),library_sha256=sha(library),
  layout=str(dst.relative_to(stage)),layout_sha256=sha(dst),
  source_root=str(headers.relative_to(stage)),
  fixed_helper_seed_schema=fixed_helper_capability(worker,admission_root))
 (stage/'GAME-PSO-COMPONENT.json').write_text(json.dumps(frozen,indent=2)+'\n')
 return frozen

def load(stage):
 stage=Path(stage).resolve(strict=True);path=stage/'GAME-PSO-COMPONENT.json'
 if not path.exists():return None
 raw,_=checked_bytes(path);m=json.loads(raw)
 need(m['schema']==1 and m['kind']=='blood-echoes-game-pso-component'and m['enabled']is True and m['production']is False,'invalid preparation component')
 result=dict(m)
 for field in('worker','library','layout','source_root'):
  p=relative(stage,m[field]);need(p==p.resolve(strict=True)and p.is_relative_to(stage),'linked/outside component')
  result[field]=p
  if field!='source_root':need(HEX.fullmatch(m[field+'_sha256'])and sha(p)==m[field+'_sha256'],'packaged component identity changed: '+field)
 from warm_archive_manifest import resolve_gpu_identity
 actual=resolve_gpu_identity(result['worker'],stage)
 need(actual['library']==str(result['library'])and actual['library_sha256']==result['library_sha256'],
      'packaged worker actual GPU edge/library mismatch')
 result['native_layout']=load_canonical_layout(result['layout'],result['source_root'],result['layout_sha256'])
 return result
