"""Bounded scene-only presentation attribution; never performance acceptance."""
from pathlib import Path
import json,os,stat
FLAG='BB_PRESENT_LATENCY_DIAGNOSTIC'
GATE='BB_PRESENT_LATENCY_GATE'
PHASES={'request-residence','present-dispatch','present-host','next-drawable','next-drawable-interpolated','submit-to-completion'}
def diagnostic_run(requested,index,runs):
 if not requested:return False
 if runs!=3 or index not in(0,1,2):raise ValueError('presentation attribution requires a cold/warm/diagnostic batch')
 return index==2
def environment(env,root):
 if env.get(FLAG,'0')!='0'or GATE in env:raise ValueError('inherited presentation diagnostic refused')
 path=Path(root)/'present-gate.bin'
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as f:f.write(bytes(4))
 return dict(env,**{FLAG:'1',GATE:str(path)})
def arm(env):
 path=Path(env[GATE]);fd=os.open(path,os.O_WRONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd)
  if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1 or s.st_size!=4 or s.st_mode&0o022:raise ValueError('changed presentation gate')
  if os.pwrite(fd,(1).to_bytes(4,'little'),0)!=4:raise ValueError('short presentation gate write')
 finally:os.close(fd)
def parse(lines):
 events=[];origins={};overflow=False;errors=[]
 for line in lines:
  if line.startswith('PRESENT_PHASE_ERROR '):errors.append(line.rstrip());continue
  prefix=next((p for p in('PRESENT_ORIGIN ','PRESENT_PHASE ')if line.startswith(p)),None)
  if prefix is None:continue
  try:
   row=json.loads(line[len(prefix):]);pid=row['pid']
   if type(pid)is not int or pid<=0:raise ValueError('invalid PID')
   if prefix=='PRESENT_ORIGIN ':
    if type(row['steady_ns'])is not int or row['steady_ns']<=0:raise ValueError('invalid origin')
    if pid in origins and origins[pid]!=row['steady_ns']:raise ValueError('ambiguous process origin')
    origins[pid]=row['steady_ns'];continue
   if row.get('overflow')is True:overflow=True;continue
   if row.get('phase')not in PHASES or any(type(row.get(k))is not int for k in('token','start_ns','end_ns','wall_ns','cpu_ns')):raise ValueError('invalid phase fields')
   if row['token']<0 or row['start_ns']<=0 or row['end_ns']-row['start_ns']!=row['wall_ns']or row['wall_ns']<50000000 or row['cpu_ns']< -1:raise ValueError('invalid phase interval')
   if len(events)>=1024:raise ValueError('diagnostic event bound exceeded')
   events.append(row)
  except(ValueError,KeyError,TypeError)as e:
   errors.append(str(e))
   if len(errors)>=32:overflow=True;break
 if any(row['pid']not in origins for row in events):errors.append('event without process frame origin')
 return dict(complete=bool(origins)and not overflow and not errors,performance_sample=False,profiler=False,origins=origins,events=events,overflow=overflow,errors=errors,qualification='Scene-gated slow host intervals; submission completion includes callback scheduling, not pure GPU execution. No slow records is not full-game hitch acceptance.')
def summarize(path):
 p=Path(path);s=p.lstat()
 if p.is_symlink()or not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_size>64*1024*1024:raise ValueError('invalid presentation diagnostic log')
 with p.open(errors='replace')as stream:return parse(stream)
