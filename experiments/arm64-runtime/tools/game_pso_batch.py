"""Bounded phase-local transport for the unchanged per-recipe native checks.

No persisted ready shortcut. At most sixteen sequential requests, only one
owned input set in flight. Old workers without exact capability use single CLI.
"""
from pathlib import Path
import hashlib,json,os,re,signal,stat,subprocess,tempfile,time
from game_pso_queue import Deferred,Stopped
MAX_RECIPES=16;MAX_FRAME=32768;MAX_LOG=8*1024*1024
CAPABILITY=dict(schema=1,max_recipes=16,shared='device-only',per_recipe_native_validation=True)
HEX=re.compile(r'[0-9a-f]{64}')
class Refused(ValueError):pass
def need(value,why):
 if not value:raise Refused(why)
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def capability(stdout):
 values=[json.loads(line.split(' ',1)[1])for line in stdout.splitlines()if line.startswith('GAME_PSO_BATCH_CAPABILITY ')]
 return len(values)==1 and canonical(values[0])==canonical(CAPABILITY)

def probe_capability(worker,project,check,*,runner=None):
 # The selected DSO constructs UserPaths before main, including scalar CLIs.
 # Match the existing component capability contract, never inherit account or
 # launch environment secrets and never use an evidence directory as GPU data.
 if runner is None:
  from game_pso_component import _run_bounded_capability
  runner=_run_bounded_capability
 base=Path(project).resolve(strict=True)/'state';row=base.lstat()
 need(base==base.resolve(strict=True)and stat.S_ISDIR(row.st_mode)and row.st_uid==os.getuid(),'owned canonical capability state root')
 check()
 with tempfile.TemporaryDirectory(prefix='game-pso-batch-cap-',dir=base)as tmp:
  env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':tmp,'TMPDIR':tmp,'BB_GPU_USER_DIR':tmp}
  result=runner([str(worker),'--game-pso-batch-capability'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=3,env=env)
  check()
  need(type(result.returncode)is int and isinstance(result.stdout,bytes)and len(result.stdout)<=4096,'bounded scalar capability result')
  if result.returncode in(70,79):raise Stopped('batch capability protection stop')
  if result.returncode!=0:return False  # usage64 or rejected UserPaths78 is never capability.
  return capability(result.stdout.decode('utf-8'))

def scope_identity(phase,allow_compile,environment):
 need(phase in('record','prepare')and type(allow_compile)is bool,'explicit phase-local batch scope')
 # These two paths are replaced by the canonical private page roots before
 # exec. Every other inherited/effective environment value remains in scope.
 values={k:v for k,v in environment.items()if k not in('HOME','TMPDIR')}
 return phase,allow_compile,hashlib.sha256(canonical(values)).hexdigest()

def request_frame(*,phase,allow_compile,sequence,packet,packet_sha256,scratch,ordinal):
 need(phase in('record','prepare')and type(allow_compile)is bool,'explicit batch phase/policy')
 need(phase!='record'or not allow_compile,'record may not compile native PSOs')
 need(type(sequence)is int and 1<=sequence<=MAX_RECIPES,'bounded batch sequence')
 need(HEX.fullmatch(packet_sha256 or''),'exact packet SHA')
 for p in(packet,scratch):need(Path(p).is_absolute(),'absolute batch input path')
 body=dict(schema=1,phase=phase,allow_compile=allow_compile,sequence=sequence,packet=str(packet),packet_sha256=packet_sha256,scratch=str(scratch),ordinal=ordinal)
 body['request_id']=hashlib.sha256(canonical(body)).hexdigest();raw=canonical(body)+b'\n';need(len(raw)<=MAX_FRAME,'bounded batch frame');return body,raw

def parse_segment(lines,frame):
 begin=[];end=[];rows=[]
 for i,line in enumerate(lines):
  if line.startswith('GAME_PSO_BATCH_BEGIN '):begin.append((i,json.loads(line.split(' ',1)[1])))
  elif line.startswith('GAME_PSO_BATCH_END '):end.append((i,json.loads(line.split(' ',1)[1])))
  elif line.startswith('GAME_PSO_RESULT '):rows.append(i)
 need(len(begin)==len(end)==len(rows)==1,'one framed native recipe result')
 expected={k:frame[k]for k in('schema','sequence','request_id','packet_sha256')}
 need(canonical(begin[0][1])==canonical(expected)and canonical({k:v for k,v in end[0][1].items()if k!='exit'})==canonical(expected),'batch response identity')
 code=end[0][1].get('exit');need(type(code)is int and code in(0,65,70,79),'worker result exit contract')
 need(begin[0][0]<rows[0]<end[0][0],'native result must be inside its own frame')
 return code

def verify_done(lines,*,phase,processed,failed,exit_code):
 values=[json.loads(line.split(' ',1)[1])for line in lines if line.startswith('GAME_PSO_BATCH_DONE ')]
 need(len(values)==1,'one final batch summary')
 need(canonical(values[0])==canonical(dict(schema=1,processed=processed,failed=failed,phase=phase,status='partial'if failed else'completed')),'batch summary counts')
 need(exit_code==(65 if failed else 0),'batch process cannot conceal partial failure')

class Worker:
 """One private owned process; a failed recipe is never an aggregate success."""
 def __init__(self,*,executable,phase,allow_compile,environment,root,check,clock,start,seconds,wait):
  self.phase=phase;self.allow_compile=allow_compile;self.root=Path(root);self.log=self.root/'console.log';self.check=check;self.clock=clock;self.start=start;self.seconds=seconds;self.wait=wait;self.count=0;self.failed=0;self.offset=0;self.loaded=set();self.closed=False
  self.stream=self.log.open('x')
  try:self.proc=subprocess.Popen([str(executable),'--batch',phase],env=environment,cwd=root,stdin=subprocess.PIPE,stdout=self.stream,stderr=subprocess.STDOUT,start_new_session=True)
  except BaseException:self.stream.close();raise
 def request(self,*,packet,packet_sha256,scratch,ordinal):
  need(not self.closed and self.count<MAX_RECIPES,'active bounded batch')
  frame,raw=request_frame(phase=self.phase,allow_compile=self.allow_compile,sequence=self.count+1,packet=packet,packet_sha256=packet_sha256,scratch=scratch,ordinal=ordinal)
  self.check();polled=self.proc.poll()
  if polled in(70,79):raise Stopped('native worker protection/interruption')
  need(polled is None,'batch exited before request')
  try:self.proc.stdin.write(raw);self.proc.stdin.flush()
  except (BrokenPipeError,OSError)as error:raise Refused('batch input closed before request')from error
  while True:
   self.check()
   if self.clock()-self.start>=self.seconds:raise Deferred('preparation time budget')
   need(self.log.stat().st_size<=MAX_LOG,'bounded batch log')
   with self.log.open('rb')as source:source.seek(self.offset);raw=source.read(MAX_LOG+1)
   lines=raw.splitlines(keepends=True);consumed=0;complete=[];ended=False
   for line in lines:
    if not line.endswith(b'\n'):break
    consumed+=len(line);text=line.decode();complete.append(text)
    if text.startswith('GAME_PSO_BATCH_END '):ended=True;break
   if ended:
    code=parse_segment(complete,frame);self.offset+=consumed;self.count+=1;self.failed+=code!=0
    if code in(70,79):raise Stopped('native worker protection/interruption')
    return dict(exit=code,pid=self.proc.pid,lines=complete,request_id=frame['request_id'],sequence=frame['sequence'])
   code=self.proc.poll()
   if code is not None:
    if code in(70,79):raise Stopped('native worker protection/interruption')
    raise Refused('batch exited before complete recipe response')
   time.sleep(.05)
 def close(self,*,abort=False):
  if self.closed:return
  self.closed=True
  try:
   if self.proc.stdin:
    try:self.proc.stdin.close()
    except OSError:pass
   if abort:
    if self.proc.poll()is None:
     try:os.killpg(self.proc.pid,signal.SIGTERM)
     except ProcessLookupError:pass
     try:self.proc.wait(timeout=3)
     except subprocess.TimeoutExpired:
      try:os.killpg(self.proc.pid,signal.SIGKILL)
      except ProcessLookupError:pass
      self.proc.wait()
   else:
    self.wait(self.proc,self.log,self.check,self.clock,self.start,self.seconds)
    if self.proc.returncode in(70,79):raise Stopped('native worker protection/interruption')
    need(self.log.stat().st_size<=MAX_LOG,'bounded final batch log')
    verify_done(self.log.read_text().splitlines(),phase=self.phase,processed=self.count,failed=self.failed,exit_code=self.proc.returncode)
  finally:self.stream.close()
