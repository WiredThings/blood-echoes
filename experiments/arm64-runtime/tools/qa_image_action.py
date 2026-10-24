"""Owned offline QA actions through the original pad-file API; no GUI automation.

Only live source-ARM64 correctness sessions advertise this endpoint. No new
in-game protocol or exit code. Inputs always release in finally; max 2s/action,
eight native image requests, original supervisor retains the 300s/VM guards.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,stat,sys,time,fcntl,tempfile
P=Path(__file__).resolve().parents[1]
from native_observer import NativeObserver
from process_schedule_audit import ProcessScheduleAudit
from decode_present_image import decode,proof

def regular(path,limit):
 path=Path(path).absolute()
 if path!=path.resolve()or path.is_symlink():raise ValueError('noncanonical QA file')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
 with os.fdopen(fd,'rb')as f:
  s=os.fstat(f.fileno())
  if not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_nlink!=1 or s.st_size>limit:raise ValueError('unowned/oversized QA file')
  data=f.read(limit+1)
  if len(data)>limit:raise ValueError('QA file grew beyond budget')
  return data
def tokens(text):
 parts=text.split()
 if len(parts)>8 or len(text)>128 or len(parts)!=len(set(parts)):raise ValueError('invalid pad token count')
 axes=[]
 for part in parts:
  if part in {'r1','r2','l1','l2','r3','circle','square','triangle','up','down','left','right'}:continue
  m=re.fullmatch(r'(lx|ly|rx|ry)=(\d{1,3})',part)
  if not m or int(m[2])>255:raise ValueError('unsupported QA action')
  axes.append(m[1])
 if len(axes)!=len(set(axes)):raise ValueError('duplicate axis')
 return ' '.join(parts)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('case',type=Path);ap.add_argument('--input',default='');ap.add_argument('--hold',type=float,default=.2);ap.add_argument('--capture',action='store_true');ap.add_argument('--gate',choices=['on','off']);ap.add_argument('--finish',action='store_true');a=ap.parse_args()
 case=a.case.absolute();assert case==case.resolve()and case.parent.parent==P/'evidence'and case.name=='0-arm64','private ARM case only'
 assert .03<=a.hold<=2 and not(a.finish and(a.input or a.capture))
 text=tokens(a.input);live=json.loads(regular(case/'external-ready.json',16384));conditions=json.loads(regular(case/'conditions.json',32768));env=conditions['env']
 assert live['schema']==1 and live['offline']and not live['quicksave']and env['BB_ONLINE']=='0'and env['BB_QUICKSAVE']=='0'and env['BB_PAD_FILE_ONLY']=='1'
 assert live['path']==str(P/'build/fex/Bin/bb-game-arm64')and live['pad']==env['BB_PAD_FILE']and live['trigger']==env['BB_PRESENT_DUMP_TRIGGER']
 pad=Path(live['pad']);images=Path(live['images']);trigger=Path(live['trigger']);gate=Path(live['gate']);log=Path(live['log'])
 assert pad==pad.resolve()and pad.is_relative_to(P/'state')and images==images.resolve()and images==case/'game-images'and trigger==images/'image-request'and gate==case/'stutter-control.bin'
 assert log==Path(env['BB_DATA_DIR'])/'out/last-run.log'and log.is_relative_to(P/'state')
 observer=NativeObserver();schedule=ProcessScheduleAudit()
 def current():
  if observer.path(live['pid'])!=live['path']:raise RuntimeError('owned QA CPU exited')
  row=schedule.read(live['pid'])
  if not row or row['process_start_mach']!=live['process_start_mach']or row['uuid']!=live['uuid']:raise RuntimeError('QA process identity changed')
 current();lockfd=os.open(case/'external-action.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600)
 lockstat=os.fstat(lockfd)
 if not stat.S_ISREG(lockstat.st_mode)or lockstat.st_uid!=os.getuid()or lockstat.st_nlink!=1:
  os.close(lockfd);raise ValueError('unowned QA action lock')
 fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 def event(name,**fields):
  with(case/'operator-events.jsonl').open('a')as f:f.write(json.dumps(dict(event=name,mono_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC),**fields))+'\n')
 def input_(value):
  fd,name=tempfile.mkstemp(prefix=pad.name+'.external-',dir=pad.parent);temp=Path(name)
  try:
   with os.fdopen(fd,'w')as f:f.write(value+'\n')
   os.replace(temp,pad)
  finally:
   if temp.exists():temp.unlink() # only this action's newly created temporary

 try:
  before=set(images.glob('*.raw'))
  if a.capture:assert len(before)<8 and not trigger.exists(),'capture budget/pending request'
  if a.gate is not None or a.finish:
   assert len(regular(gate,4096))==4096
   fd=os.open(gate,os.O_WRONLY|os.O_NOFOLLOW);os.write(fd,(1 if a.gate=='on'and not a.finish else 0).to_bytes(4,'little'));os.close(fd)
   event('external_gate',enabled=a.gate=='on'and not a.finish,performance_evidence=False)
  try:
   if text:input_(text);event('external_input',token=text,hold_seconds=a.hold,performance_evidence=False);time.sleep(a.hold)
  finally:input_('')
  if a.finish:(case/'finish-request').touch(mode=0o600,exist_ok=False);event('external_finish_requested');print('finish requested');return
  if a.capture:
   trigger.touch(mode=0o600,exist_ok=False);event('external_game_image_requested');deadline=time.monotonic()+10;image=None
   while time.monotonic()<deadline:
    current();fresh=set(images.glob('*.raw'))-before
    if len(fresh)>1:raise RuntimeError('concurrent capture requests')
    if fresh:
     path=next(iter(fresh));rawlog=regular(log,8*1024*1024).decode(errors='replace')
     try:fmt,flip=proof(path,rawlog)
     except ValueError:time.sleep(.1);continue
     image=decode(path,fmt,flip);image['png_sha256']=hashlib.file_digest(Path(image['png']).open('rb'),'sha256').hexdigest()
     sidecar=path.with_suffix('.preview.json');fd=os.open(sidecar,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
     with os.fdopen(fd,'w')as f:json.dump(image,f,indent=2);f.write('\n')
     break
    time.sleep(.1)
   if image is None:raise RuntimeError('native game image did not complete')
   assert image['metal_format']==80 and image['flip_vertical'];print(json.dumps(image))
  else:print('input released')
 finally:os.close(lockfd)
if __name__=='__main__':main()
