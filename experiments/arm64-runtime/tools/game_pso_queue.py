"""Disk-backed scheduling, not an artifact cache or an authority to skip native checks."""
from pathlib import Path
import fcntl,hashlib,json,os,sqlite3,stat,time,uuid
MAX_LINE=256*1024
PAGE_MAX=64
PREVIEW_MAX=64
class Deferred(RuntimeError):pass
class Stopped(RuntimeError):pass
class Rejected(ValueError):pass

def need(value,why):
 if not value:raise Rejected(why)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def canonical_dir(p,create=False):
 p=Path(p)
 if create:p.mkdir(mode=0o700)
 s=p.lstat();need(p.is_absolute() and p==p.resolve(strict=True) and stat.S_ISDIR(s.st_mode) and s.st_uid==os.getuid(),'owned canonical progress directory')
 return p

def fingerprint(path,check):
 path=Path(path);need(path==path.resolve(strict=True),'linked manifest')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  a=os.fstat(fd);need(stat.S_ISREG(a.st_mode) and a.st_uid==os.getuid(),'owned manifest')
  h=hashlib.sha256()
  while True:
   check();data=os.read(fd,1024*1024)
   if not data:break
   h.update(data)
  b=os.fstat(fd);need((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns),'manifest changed while fingerprinting')
  return h.hexdigest()
 finally:os.close(fd)

class Queue:
 def __init__(self,manifest,progress_root,context,check):
  self.manifest=Path(manifest).resolve(strict=True);self.check=check;self.db=None;self.lock=None;self.interrupted=None
  self.manifest_sha=fingerprint(self.manifest,check)
  self.job=digest(dict(manifest_sha256=self.manifest_sha,current_context=context))
  root=canonical_dir(progress_root);folder=root/self.job
  if not folder.exists():canonical_dir(folder,True)
  self.folder=canonical_dir(folder);self.db_path=folder/'queue.sqlite'
  try:
   self.lock=os.open(folder/'queue.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
   s=os.fstat(self.lock);need(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1,'progress lock identity')
   try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError as error:raise Rejected('another preparation owns progress queue') from error
   if self.db_path.exists():
    s=self.db_path.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1,'owned nonlinked progress DB')
   else:
    fd=os.open(self.db_path,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600);os.close(fd)
   self.db=sqlite3.connect(self.db_path);self.db.set_progress_handler(self._progress,50000)
   self._sql('PRAGMA temp_store=FILE');self._sql('PRAGMA mmap_size=0');self._sql('PRAGMA cache_size=-2048')
   self._sql('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT)')
   self._sql('''CREATE TABLE IF NOT EXISTS units(id TEXT PRIMARY KEY,ordinal INTEGER,payload TEXT,payload_sha TEXT,
                ready_hint INTEGER DEFAULT 0,recorded_hint INTEGER DEFAULT 0,last_visit INTEGER DEFAULT 0,run_mark TEXT DEFAULT '')''')
   self._sql('CREATE INDEX IF NOT EXISTS units_visit ON units(last_visit,ordinal)')
   self.db.commit()
  except BaseException:self.close();raise
 def _progress(self):
  try:self.check()
  except BaseException as error:self.interrupted=error;return 1
  return 0
 def _sql(self,*args):
  try:return self.db.execute(*args)
  except sqlite3.Error:
   if self.interrupted is not None:raise self.interrupted
   raise
 def get(self,key,default=0):
  row=self._sql('SELECT v FROM meta WHERE k=?',(key,)).fetchone();return json.loads(row[0]) if row else default
 def put(self,key,value):self._sql('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value)))
 def index(self):
  if self.get('indexed',False):return
  count=self.get('records');eligible=self.get('eligible');rejected=self.get('rejected');excluded=self.get('excluded');duplicates=self.get('duplicates')
  offset=self.get('offset');self.check();finished=False
  with self.manifest.open('rb') as stream:
   stream.seek(offset)
   try:
    while True:
     self.check();raw=stream.readline(MAX_LINE+1)
     if not raw:finished=True;break
     count+=1
     if len(raw)>MAX_LINE or not raw.endswith(b'\n'):raise Rejected('manifest line budget; indexing incomplete')
     try:
      u=json.loads(raw)
      if u.get('record_type')=='rejected-input':rejected+=1
      elif u.get('record_type')!='offline-pso-input' or u.get('execution_policy',{}).get('candidate_preparation',{}).get('policy_eligible') is not True:excluded+=1
      else:
       need(u['schema']==2 and u['execution_policy']['runtime_cache_import_allowed'] is False,'v2 candidate-only input')
       key=dict(cache_id=u['namespace']['cache_id'],key=u['key']['reference'],recipe_blob_identity=u['recipe_blob_identity'])
       uid=digest(key);payload=canonical(u).decode();ph=hashlib.sha256(payload.encode()).hexdigest()
       old=self._sql('SELECT payload_sha FROM units WHERE id=?',(uid,)).fetchone()
       if old:
        need(old[0]==ph,'conflicting duplicate exact namespace');duplicates+=1
       else:self._sql('INSERT INTO units(id,ordinal,payload,payload_sha) VALUES(?,?,?,?)',(uid,count,payload,ph))
       eligible+=1
     except (ValueError,KeyError,TypeError):rejected+=1
     offset=stream.tell()
     if count%PAGE_MAX==0:self._checkpoint(count,eligible,rejected,excluded,duplicates,offset)
   finally:self._checkpoint(count,eligible,rejected,excluded,duplicates,offset)
  if finished:
   need(fingerprint(self.manifest,self.check)==self.manifest_sha,'manifest changed while indexing')
   self.put('indexed',True);self.db.commit()
 def _checkpoint(self,count,eligible,rejected,excluded,duplicates,offset):
  for k,v in dict(records=count,eligible=eligible,rejected=rejected,excluded=excluded,duplicates=duplicates,offset=offset).items():self.put(k,v)
  self.db.commit()
 def page(self,run,page_size):
  self.check();rows=self._sql('''SELECT id,payload,payload_sha,ready_hint,recorded_hint FROM units WHERE run_mark!=?
        ORDER BY last_visit,ordinal LIMIT ?''',(run,page_size)).fetchall()
  result=[]
  for uid,payload,expected,ready,recorded in rows:
   need(hashlib.sha256(payload.encode()).hexdigest()==expected,'progress payload changed')
   u=json.loads(payload);key=dict(cache_id=u['namespace']['cache_id'],key=u['key']['reference'],recipe_blob_identity=u['recipe_blob_identity'])
   need(digest(key)==uid,'progress unit identity')
   result.append(dict(id=uid,unit=u,ready_hint=bool(ready),recorded_hint=bool(recorded)))
  return result
 def mark(self,item,run,*,ready=False,recorded=False):
  seq=self.get('visit_sequence')+1;self.put('visit_sequence',seq)
  self._sql('UPDATE units SET ready_hint=?,recorded_hint=?,last_visit=?,run_mark=? WHERE id=?',(int(ready),int(recorded),seq,run,item['id']));self.db.commit()
 def total(self):return self._sql('SELECT COUNT(*) FROM units').fetchone()[0]
 def hint_count(self):return self._sql('SELECT COUNT(*) FROM units WHERE ready_hint=1').fetchone()[0]
 def close(self):
  if self.db is not None:self.db.close();self.db=None
  if self.lock is not None:os.close(self.lock);self.lock=None

def run_manifest(manifest,*,progress_root,context,execute,prepare_phase,check,ready,phase='all',page_size=16,max_recipes=None,
                 seconds=600,clock=time.monotonic,event_sink=None):
 """Startup API. Callbacks are native executor services; tests use authors only.

 execute(unit,phase,allow_compile,ordinal)->actual row. A hint never counts as
 success: ready(row) must validate the current native strict artifact outcome.
 """
 need(phase in ('all','record','prepare') and 0<page_size<=PAGE_MAX and seconds>0,'bounded page/phase/time budget')
 need(max_recipes is None or type(max_recipes) is int and max_recipes>0,'optional recipe work budget')
 start=clock();run=uuid.uuid4().hex;queue=None;active=None;ordinal=0
 report=dict(schema=1,status='partial',completed=0,deferred=0,rejected=0,protection_stops=0,recorded=0,
             verified_artifact_hits=0,visited=0,events_preview=[],event_count=0,max_page_items=0,
             full_manifest_preparation_verified=False,state_is_artifact_authority=False)
 def guard():
  check()
  if clock()-start>=seconds:raise Deferred('preparation time budget')
 def event(item,state,**extra):
  value=dict(unit_id=item['id'],recipe=item['unit']['recipe_blob_identity'],state=state,**extra)
  report['event_count']+=1
  if len(report['events_preview'])<PREVIEW_MAX:report['events_preview'].append(value)
  if event_sink:event_sink(value)
 def finish(item,state,*,recorded=False,row=None):
  report['visited']+=1;report[state]+=1
  queue.mark(item,run,ready=state=='completed',recorded=recorded or state=='completed')
  event(item,state,worker_phase=row.get('phase') if row else None)
 def call(item,p,allow):
  nonlocal ordinal,active
  guard();active=item;ordinal+=1
  row=execute(item['unit'],p,allow,ordinal)
  if row.get('exit') in (70,79):raise Stopped('native worker protection/interruption')
  worker=row.get('worker')
  if row.get('exit')!=0 or not isinstance(worker,dict):raise Rejected('native worker rejected or malformed result')
  if p=='record' and worker.get('status')!='source-requests-recorded':raise Rejected('worker completion did not record validated source requests')
  need(isinstance(worker.get('artifact_counts',{}),dict),'worker artifact count shape')
  if worker.get('artifact_counts',{}).get('critical',0):raise Stopped('native critical memory pressure')
  return row
 def local(p):
  guard();result=prepare_phase(p,guard);guard()
  if result.get('status')=='stopped':raise Stopped(result.get('error','local library protection stop'))
  return result
 try:
  queue=Queue(manifest,progress_root,context,guard);queue.index()
  report.update(job_id=queue.job,manifest_sha256=queue.manifest_sha,index_complete=True,
      manifest_records=queue.get('records'),policy_eligible_records=queue.get('eligible'),
      index_rejected=queue.get('rejected'),policy_excluded=queue.get('excluded'),exact_duplicates=queue.get('duplicates'))
  while True:
   guard()
   if max_recipes is not None and report['visited']>=max_recipes:raise Deferred('recipe work budget')
   page=queue.page(run,min(page_size,max_recipes-report['visited']) if max_recipes is not None else page_size)
   if not page:break
   report['max_page_items']=max(report['max_page_items'],len(page));pending=[]
   # Prior success schedules a real current lookup-only native verification.
   hints=[item for item in page if item['ready_hint'] and phase!='record']
   if hints:
    env=local('prepare')
    for item in hints:
     try:
      if env.get('status')=='prepared':
       row=call(item,'prepare',False)
       if ready(row)and row['worker']['artifact_counts'].get('built',0)==0:
        report['verified_artifact_hits']+=1;finish(item,'completed',recorded=True,row=row);continue
      item['recorded_hint']=False;pending.append(item)
     except (Rejected,ValueError,OSError) as error:
      item['recorded_hint']=False;pending.append(item);event(item,'previous-hint-not-admitted',error=str(error)[:512])
   pending.extend(item for item in page if item not in hints)
   recorded=[]
   if phase in ('all','record') and pending:
    local('record')
    for item in pending:
     try:
      if not item['recorded_hint']:
       row=call(item,'record',False);report['recorded']+=1
       # Intermediate scheduling hint cannot approve artifact reads or success.
       queue.mark(item,'',ready=False,recorded=True);item['recorded_hint']=True
      recorded.append(item)
      if phase=='record':finish(item,'deferred',recorded=True)
     except (Rejected,ValueError,OSError) as error:finish(item,'rejected');event(item,'rejection-detail',error=str(error)[:512])
   else:recorded=pending
   if phase!='record' and recorded:
    env=local('prepare')
    for item in recorded:
     try:
      if env.get('status')!='prepared':finish(item,'deferred',recorded=False);continue
      row=call(item,'prepare',True)
      if ready(row):finish(item,'completed',recorded=True,row=row)
      elif row['worker'].get('artifact_counts',{}).get('failed',0):finish(item,'rejected',row=row)
      else:finish(item,'deferred',recorded=False,row=row)
     except (Rejected,ValueError,OSError) as error:finish(item,'rejected');event(item,'rejection-detail',error=str(error)[:512])
  report['status']=('partial'if queue.get('rejected')else'no-candidates') if queue.total()==0 else 'completed' if phase!='record' and report['deferred']==report['rejected']==0 else 'partial'
  report['full_manifest_preparation_verified']=report['status']=='completed' and report.get('index_rejected',0)==0
 except Deferred as error:report.update(status='partial',budget_deferred=True,error=str(error))
 except (Stopped,KeyboardInterrupt) as error:
  report.update(status='stopped',error=str(error) or 'interrupted');report['protection_stops']+=1
  if active:event(active,'protection-stopped')
 except Exception as error:report.update(status='failed',error=str(error)[:1024])
 finally:
  report['seconds']=clock()-start
  if queue is not None:
   queue.db.set_progress_handler(None,0)
   report.update(unique_units=queue.total(),observed_ready_hint_count=queue.hint_count(),index_complete=queue.get('indexed',False))
   report['not_visited']=max(0,report['unique_units']-report['visited'])
   report['budget_deferred_remaining']=report['not_visited'] if report['index_complete'] else None
   report['rejected_total']=report['rejected']+queue.get('rejected')
   report['deferred_total_known']=report['deferred']+report['not_visited']
   queue.close()
 return report
