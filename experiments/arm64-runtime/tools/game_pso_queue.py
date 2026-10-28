"""Disk-backed scheduling, not an artifact cache or an authority to skip native checks."""
from pathlib import Path
import fcntl,hashlib,json,os,re,sqlite3,stat,time,uuid
from game_pso_progress_identity import input_identity,execution_identity
MAX_LINE=256*1024
PAGE_MAX=64
PREVIEW_MAX=64
class Deferred(RuntimeError):pass
class Stopped(RuntimeError):pass
class Pending(RuntimeError):pass
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

def local_inputs_ready(row):
 """Current Local function/descriptor graph; not PSO success or old-cache trust."""
 w=row.get('worker',{});c=w.get('artifact_counts',{});fs=w.get('functions',[]);events=row.get('artifact_events',[])
 if type(w.get('allow_compile'))is not bool:return False
 if not isinstance(c,dict)or any(type(c.get(k,0))is not int or c.get(k,0)<0 for k in('attempts','hits','built','deferred','failed','critical','unavailable','compile_attempts')):return False
 n=c.get('attempts',0)
 if n<=0 or not isinstance(events,list)or len(events)!=n or sum(c.get(k,0)for k in('hits','built','deferred'))!=n:return False
 if w.get('allow_compile')is False and(c.get('built',0)or c.get('compile_attempts',0)):return False
 if (w.get('source_fallback')is not False or not isinstance(fs,list)or not 0<len(fs)<=6 or
     not isinstance(events,list)or not events or not isinstance(c,dict)or
     any(c.get(k,0)for k in('failed','critical','unavailable'))):return False
 def h(v):return isinstance(v,str)and re.fullmatch('[0-9a-f]{64}',v)
 if not all(isinstance(f,dict)and f.get('actual_route')=='local-metallib'and h(f.get('identity'))for f in fs):return False
 return all(isinstance(e,dict)and type(e.get('state'))is int and e['state']in(1,2,3)and (h(e.get('archive_sha256'))if e['state']in(1,2)else e.get('archive_sha256','')=='')and h(e.get('key'))and h(e.get('descriptor'))and h(e.get('primary'))and
    e.get('primary_route')==2 and ((e.get('fragment_route')==0 and e.get('fragment')=='')or(e.get('fragment_route')==2 and h(e.get('fragment'))))for e in events)

def local_preparation_ready(row,ready):
 try:return bool(ready(row))and local_inputs_ready(row)
 except (KeyError,TypeError,ValueError,AttributeError):return False

def strict_lookup_ready(row,ready):
 if not local_preparation_ready(row,ready):return False
 w=row['worker'];c=w['artifact_counts'];n=c.get('attempts',0)
 return (w.get('allow_compile')is False and type(n)is int and n>0 and
   all(type(c.get(k))is int for k in('hits','built','compile_attempts'))and c['hits']==n and c['built']==c['compile_attempts']==0 and
   len(row['artifact_events'])==n and all(e['state']==1 for e in row['artifact_events']))

def verified_completion_schedule(report):
 """A completed current queue, including newly built native recipes.

 This is scheduling evidence, never artifact import authority. In particular a
 native build is not counted as a cache hit, nor a missing GS recipe receipt
 fabricated. The producer commits each outcome only after its final real
 environment verification, binding exact unit/payload/outcome identities.
 """
 try:
  document=report['completion_coverage'];body=document['body']
  if set(document)!={'body','sha256'}or digest(body)!=document['sha256']:return False
  if set(body)!={'schema','kind','manifest_sha256','job_id','cycle_id','context_sha256','environment_identity','required','scheduled_reused','native_hits','native_built','bindings_sha256','artifact_authority'}:return False
  if type(body['schema'])is not int or body['schema']!=1 or body['kind']!='current-verified-required-queue'or body['artifact_authority']is not False:return False
  if not all(isinstance(body[k],str)and re.fullmatch('[0-9a-f]{64}',body[k])for k in('manifest_sha256','job_id','context_sha256','environment_identity','bindings_sha256')):return False
  if not isinstance(body['cycle_id'],str)or not re.fullmatch('[0-9a-f]{32}',body['cycle_id']):return False
  counts=('required','scheduled_reused','native_hits','native_built')
  if not all(type(body[k])is int and body[k]>=0 for k in counts)or body['required']<=0:return False
  if report.get('status')!='completed'or report.get('required_preparation_complete')is not True or report.get('index_complete')is not True or report.get('cycle_stage')!='done':return False
  for k in('deferred','rejected','protection_stops','index_rejected','source_late_deferred','provisional_native','not_visited'):
   if type(report.get(k))is not int or report[k]!=0:return False
  for k in('unique_units','completed','visited'):
   if type(report.get(k))is not int or report[k]!=body['required']:return False
  for field,key in(('scheduled_reused','scheduled_reused'),('verified_artifact_hits','native_hits')):
   if type(report.get(field))is not int or report[field]!=body[key]:return False
  if sum(body[k]for k in('scheduled_reused','native_hits','native_built'))!=body['required']:return False
  if any(report.get(k)is True for k in('budget_deferred','pending_preparation','state_is_artifact_authority')):return False
  if any(report.get(k)!=body['manifest_sha256']for k in('manifest_sha256','active_manifest_sha256','incoming_manifest_sha256')):return False
  if report.get('job_id')!=body['job_id']or report.get('cycle_id')!=body['cycle_id']or report.get('current_context_sha256')!=body['context_sha256']:return False
  if 'current_context'in report and digest(report['current_context'])!=body['context_sha256']:return False
  proof=report.get('warm_environment_verification',{})if report.get('warm_fast_path')is True else report.get('final_environment_verification',{})
  return proof.get('status')=='environment-verified'and proof.get('environment_identity')==body['environment_identity']
 except(KeyError,TypeError,ValueError,AttributeError):return False

class Queue:
 """Versioned scheduling hints. Only a fully indexed current snapshot is executable.

 A changed manifest stages membership/payloads separately, so interruption leaves
 the prior snapshot intact but never executable under the new incoming SHA.
 """
 def __init__(self,manifest,progress_root,context,check):
  self.manifest=Path(manifest).resolve(strict=True);self.check=check;self.db=None;self.lock=None;self.interrupted=None
  self.manifest_sha=fingerprint(self.manifest,check)
  self.job=digest(dict(progress_schema=2,current_context=context))
  root=canonical_dir(progress_root);domain=root/'incremental-v2'
  if not domain.exists():canonical_dir(domain,True)
  domain=canonical_dir(domain);folder=domain/self.job
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
   self._sql("""CREATE TABLE IF NOT EXISTS units(id TEXT PRIMARY KEY,input_json TEXT,input_sha TEXT,
       active INTEGER DEFAULT 0,ordinal INTEGER,payload TEXT,payload_sha TEXT,
       pending_scan TEXT DEFAULT '',pending_ordinal INTEGER,pending_payload TEXT,pending_payload_sha TEXT,
       pending_execution_sha TEXT,pending_conflict INTEGER DEFAULT 0,
       ready_hint INTEGER DEFAULT 0,recorded_hint INTEGER DEFAULT 0,last_visit INTEGER DEFAULT 0,run_mark TEXT DEFAULT '')""")
   self._sql('CREATE INDEX IF NOT EXISTS units_visit ON units(active,last_visit,ordinal)')
   self._sql('CREATE TABLE IF NOT EXISTS phase_units(id TEXT PRIMARY KEY,cycle TEXT,state TEXT,outcome_sha TEXT,final_run TEXT DEFAULT \'\',final_state TEXT DEFAULT \'\')');self.db.commit()
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
  if key=='indexed':return self.get('active_manifest',None)==self.manifest_sha
  row=self._sql('SELECT v FROM meta WHERE k=?',(key,)).fetchone();return json.loads(row[0]) if row else default
 def put(self,key,value):self._sql('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value)))
 def _start_scan(self):
  if self.get('pending_manifest',None)==self.manifest_sha:return
  # Clearing only pending columns cannot disturb old active membership or hints.
  self._sql("UPDATE units SET pending_scan='',pending_ordinal=NULL,pending_payload=NULL,pending_payload_sha=NULL,pending_execution_sha=NULL,pending_conflict=0")
  self.put('pending_manifest',self.manifest_sha)
  for key in ('records','eligible','rejected','excluded','duplicates','conflicts','offset'):self.put(key,0)
  self.db.commit()
 def index(self):
  if self.get('indexed',False):return
  self._start_scan()
  count=self.get('records');eligible=self.get('eligible');rejected=self.get('rejected');excluded=self.get('excluded');duplicates=self.get('duplicates');conflicts=self.get('conflicts')
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
       identity=input_identity(u);uid=digest(identity);identity_json=canonical(identity).decode()
       payload=canonical(u).decode();ph=hashlib.sha256(payload.encode()).hexdigest();eh=execution_identity(u)
       old=self._sql('SELECT input_json,pending_scan,pending_execution_sha,pending_conflict FROM units WHERE id=?',(uid,)).fetchone()
       if old:
        need(old[0]==identity_json,'input identity collision')
        if old[1]==self.manifest_sha:
         if old[2]!=eh:
          if not old[3]:conflicts+=1
          self._sql('UPDATE units SET pending_conflict=1 WHERE id=?',(uid,));rejected+=1
         else:duplicates+=1
        else:
         self._sql('UPDATE units SET pending_scan=?,pending_ordinal=?,pending_payload=?,pending_payload_sha=?,pending_execution_sha=?,pending_conflict=0 WHERE id=?',(self.manifest_sha,count,payload,ph,eh,uid))
       else:
        self._sql('INSERT INTO units(id,input_json,input_sha,pending_scan,pending_ordinal,pending_payload,pending_payload_sha,pending_execution_sha) VALUES(?,?,?,?,?,?,?,?)',(uid,identity_json,uid,self.manifest_sha,count,payload,ph,eh))
       eligible+=1
     except (ValueError,KeyError,TypeError):rejected+=1
     offset=stream.tell()
     if count%PAGE_MAX==0:self._checkpoint(count,eligible,rejected,excluded,duplicates,conflicts,offset)
   finally:self._checkpoint(count,eligible,rejected,excluded,duplicates,conflicts,offset)
  if finished:
   need(fingerprint(self.manifest,self.check)==self.manifest_sha,'manifest changed while indexing')
   try:
    self._sql('UPDATE units SET active=(pending_scan=? AND pending_conflict=0)',(self.manifest_sha,))
    self._sql('UPDATE units SET ordinal=pending_ordinal,payload=pending_payload,payload_sha=pending_payload_sha WHERE active=1')
    self.put('active_manifest',self.manifest_sha);self.db.commit()
   except BaseException:self.db.rollback();raise
 def _checkpoint(self,count,eligible,rejected,excluded,duplicates,conflicts,offset):
  for k,v in dict(records=count,eligible=eligible,rejected=rejected,excluded=excluded,duplicates=duplicates,conflicts=conflicts,offset=offset).items():self.put(k,v)
  self.db.commit()
 def page(self,run,page_size):
  self.check();need(self.get('indexed',False),'incoming manifest not fully indexed/activated')
  rows=self._sql("""SELECT id,payload,payload_sha,ready_hint,recorded_hint FROM units WHERE active=1 AND run_mark!=?
        ORDER BY last_visit,ordinal LIMIT ?""",(run,page_size)).fetchall()
  result=[]
  for uid,payload,expected,ready,recorded in rows:
   need(hashlib.sha256(payload.encode()).hexdigest()==expected,'progress payload changed')
   u=json.loads(payload);need(digest(input_identity(u))==uid,'progress unit identity')
   result.append(dict(id=uid,unit=u,ready_hint=bool(ready),recorded_hint=bool(recorded)))
  return result
 def mark(self,item,run,*,ready=False,recorded=False):
  need(self.get('indexed',False),'cannot mark an unactivated manifest')
  seq=self.get('visit_sequence')+1;self.put('visit_sequence',seq)
  self._sql('UPDATE units SET ready_hint=?,recorded_hint=?,last_visit=?,run_mark=? WHERE id=? AND active=1',(int(ready),int(recorded),seq,run,item['id']));self.db.commit()
 def begin_cycle(self,phase,run):
  old=self.get('phase_cycle',None)
  fresh=not(isinstance(old,dict)and old.get('schema')==1 and old.get('manifest_sha')==self.manifest_sha and old.get('phase')==phase and old.get('stage')in('collect','build','prepare'))
  if fresh:
   old=dict(schema=1,id=uuid.uuid4().hex,manifest_sha=self.manifest_sha,phase=phase,stage='prepare'if phase=='prepare'else'collect')
   self.put('phase_cycle',old)
   if phase=='prepare':self._sql("INSERT OR REPLACE INTO phase_units(id,cycle,state,outcome_sha) SELECT id,?,'pending','' FROM units WHERE active=1",(old['id'],))
   self.db.commit()
  return old,fresh
 def set_stage(self,cycle,stage):
  cycle['stage']=stage;self.put('phase_cycle',cycle);self.db.commit()
 def phase_count(self,cycle,state=None):
  sql='SELECT COUNT(*) FROM phase_units p JOIN units u ON u.id=p.id WHERE u.active=1 AND p.cycle=?';args=[cycle['id']]
  if state is not None:sql+=' AND p.state=?';args.append(state)
  return self._sql(sql,args).fetchone()[0]
 def phase_page(self,cycle,run,size,kind):
  self.check();need(self.get('indexed',False),'incoming manifest not fully indexed/activated')
  if kind=='collect':
   where='(p.cycle IS NULL OR p.cycle!=?)';args=[cycle['id']]
  elif kind=='prepare':
   where="p.cycle=? AND p.state NOT IN('rejected','scheduled') AND p.final_run!=?";args=[cycle['id'],run]
  elif kind=='commit':
   where="p.cycle=? AND p.final_run=? AND p.final_state IN('ready-hit','ready-built') AND u.run_mark!=?";args=[cycle['id'],run,run]
  elif kind=='warm':
   where="p.cycle=? AND p.state='provisional' AND u.run_mark!=?";args=[cycle['id'],run]
  else:raise Rejected('unknown internal pass')
  rows=self._sql("SELECT u.id,u.payload,u.payload_sha,u.ready_hint,u.recorded_hint,p.state,p.outcome_sha,p.final_state FROM units u LEFT JOIN phase_units p ON p.id=u.id WHERE u.active=1 AND "+where+' ORDER BY u.last_visit,u.ordinal LIMIT ?',(*args,size)).fetchall()
  result=[]
  for uid,payload,ph,rh,rec,state,outcome,final_state in rows:
   need(hashlib.sha256(payload.encode()).hexdigest()==ph,'progress payload changed');u=json.loads(payload);need(digest(input_identity(u))==uid,'progress unit identity')
   result.append(dict(id=uid,unit=u,ready_hint=bool(rh),recorded_hint=bool(rec),collection_state=state,collection_outcome_sha=outcome,native_hit=final_state=='ready-hit'))
  return result
 def collect(self,item,cycle,state,row=None):
  need(state in('provisional','local-inputs','recorded','unavailable','rejected','scheduled'),'collection state')
  self._sql('INSERT OR REPLACE INTO phase_units(id,cycle,state,outcome_sha) VALUES(?,?,?,?)',(item['id'],cycle['id'],state,digest(row)if row else''))
  seq=self.get('visit_sequence')+1;self.put('visit_sequence',seq)
  self._sql('UPDATE units SET last_visit=?,recorded_hint=? WHERE id=?',(seq,int(state in('provisional','local-inputs','recorded','scheduled')),item['id']));self.db.commit()
 def provisional_final(self,item,cycle,run,row):
  self._sql("UPDATE phase_units SET final_run=?,final_state=?,outcome_sha=? WHERE id=? AND cycle=?",(run,'ready-hit'if row['worker']['artifact_counts'].get('built',0)==0 else'ready-built',digest(row),item['id'],cycle['id']))
  seq=self.get('visit_sequence')+1;self.put('visit_sequence',seq);self._sql('UPDATE units SET last_visit=? WHERE id=?',(seq,item['id']));self.db.commit()
 def terminal_final(self,item,cycle,run,state):
  self._sql('UPDATE phase_units SET final_run=?,final_state=? WHERE id=? AND cycle=?',(run,state,item['id'],cycle['id']));self.db.commit()
 def total(self):return self._sql('SELECT COUNT(*) FROM units WHERE active=1').fetchone()[0]
 def hint_count(self):return self._sql('SELECT COUNT(*) FROM units WHERE active=1 AND ready_hint=1').fetchone()[0]
 def close(self):
  if self.db is not None:self.db.close();self.db=None
  if self.lock is not None:os.close(self.lock);self.lock=None

def run_manifest(manifest,*,progress_root,context,execute,prepare_phase,check,ready,phase='all',page_size=16,max_recipes=None,
                 seconds=600,clock=time.monotonic,event_sink=None,reuse_unit=None,commit_unit=None):
 """Disk-backed collect -> one final build -> native prepare. No module batches.

 Internal prepare_phase also supports snapshot/verify for a real same-session
 source/tool/environment anchor. These are not CLI phases or artifact authority.
 """
 need(phase in('all','record','prepare')and 0<page_size<=PAGE_MAX and seconds>0,'bounded page/phase/time budget')
 need(max_recipes is None or type(max_recipes)is int and max_recipes>0,'optional recipe work budget')
 # Preserve the accepted CLI range while limiting the live metadata window to
 # the native page contract. This is never a cap on full-manifest coverage.
 page_size=min(page_size,16)
 start=clock();run=uuid.uuid4().hex;queue=None;cycle=None;active=None;ordinal=0;fresh=False
 coverage=dict(scheduled_reused=0,native_hits=0,native_built=0);bindings=hashlib.sha256()
 report=dict(schema=1,progress_schema=2,orchestration_schema=1,status='partial',completed=0,deferred=0,rejected=0,protection_stops=0,recorded=0,current_context_sha256=digest(context),
   verified_artifact_hits=0,collection_strict_hits=0,strict_lookup_attempts=0,visited=0,work_units=0,collected=0,provisional_native=0,provisional_artifact_hits=0,source_late_deferred=0,
   events_preview=[],event_count=0,max_page_items=0,initial_local_calls=0,final_local_calls=0,warm_fast_path=False,
   full_manifest_preparation_verified=False,required_preparation_complete=False,state_is_artifact_authority=False,scheduled_reused=0)
 def guard():
  check()
  if clock()-start>=seconds:raise Deferred('preparation time budget')
 def work_guard():
  guard()
  if max_recipes is not None and report['work_units']>=max_recipes:raise Deferred('recipe phase work budget')
 def event(item,state,**extra):
  v=dict(unit_id=item['id'],recipe=item['unit']['recipe_blob_identity'],state=state,**extra);report['event_count']+=1
  if len(report['events_preview'])<PREVIEW_MAX:report['events_preview'].append(v)
  if event_sink:event_sink(v)
 def finish(item,state,*,recorded=False,row=None):
  report['visited']+=1;report[state]+=1;queue.mark(item,run,ready=state=='completed',recorded=recorded or state=='completed');event(item,state,worker_phase=row.get('phase')if row else None)
 def covered(item,kind,outcome):
  need(kind in coverage and isinstance(outcome,str)and re.fullmatch('[0-9a-f]{64}',outcome),'current verified outcome binding')
  coverage[kind]+=1
  bindings.update(canonical([item['id'],execution_identity(item['unit']),kind,outcome])+b'\n')
 def call(item,p,allow):
  nonlocal ordinal,active
  guard();active=item;ordinal+=1;row=execute(item['unit'],p,allow,ordinal)
  if row.get('exit')in(70,79):raise Stopped('native worker protection/interruption')
  w=row.get('worker')
  if row.get('exit')!=0 or not isinstance(w,dict):raise Rejected('native worker rejected or malformed result')
  if p=='record'and w.get('status')!='source-requests-recorded':raise Rejected('worker completion did not record validated source requests')
  if p=='prepare'and not allow:need(w.get('allow_compile')is False,'native lookup allow flag mismatch')
  need(isinstance(w.get('artifact_counts',{}),dict),'worker artifact count shape')
  if w.get('artifact_counts',{}).get('critical',0):raise Stopped('native critical memory pressure')
  return row
 def local(p):
  guard();r=prepare_phase(p,guard);guard()
  if r.get('status')=='stopped':raise Stopped(r.get('error','local library protection stop'))
  return r
 def commit(kind):
  while True:
   guard();page=queue.phase_page(cycle,run,page_size,kind)
   if not page:break
   for item in page:
    finish(item,'completed',recorded=True)
    covered(item,'native_hits'if kind=='warm'or item['native_hit']else'native_built',item['collection_outcome_sha'])
    if commit_unit is not None:commit_unit(item['unit'])
    if kind=='commit':
     report['provisional_native']-=1
     if item['native_hit']:report['verified_artifact_hits']+=1;report['provisional_artifact_hits']-=1
 try:
  queue=Queue(manifest,progress_root,context,guard);queue.index();cycle,fresh=queue.begin_cycle(phase,run)
  report.update(job_id=queue.job,manifest_sha256=queue.manifest_sha,index_complete=True,manifest_records=queue.get('records'),policy_eligible_records=queue.get('eligible'),index_rejected=queue.get('rejected'),policy_excluded=queue.get('excluded'),exact_duplicates=queue.get('duplicates'),conflicting_uids=queue.get('conflicts'),cycle_id=cycle['id'],resumed_collection=not fresh)
  initial={};initial_anchor={};record_open=False
  if queue.total()==0:queue.set_stage(cycle,'done')
  if cycle['stage']=='collect':
   initial_started=False
   def collected(item,state,row=None):
    queue.collect(item,cycle,state,row);report['collected']+=1
    if state=='provisional':report['collection_strict_hits']+=1
    event(item,'collected-'+state)
    if state=='rejected':finish(item,'rejected')
    elif phase=='record':finish(item,'deferred',recorded=True)
   while True:
    guard()
    # Admit only work-budgeted items. A pending record is the second half of
    # the same admitted item, not a new work unit or a second payload window.
    remaining=page_size if max_recipes is None else max(1,min(page_size,max_recipes-report['work_units']))
    page=queue.phase_page(cycle,run,remaining,'collect')
    if not page:break
    report['max_page_items']=max(report['max_page_items'],len(page))
    pending_record=[]  # References to this <=16-item page only, never owned bytes.
    for item in page:
     if phase=='all'and reuse_unit is not None:
      guard();reused=reuse_unit(item['unit'])
      if isinstance(reused,dict)and reused.get('status')=='required-recipe-schedule-reused'and reused.get('previous_native_verification')is True and reused.get('native_worker_invoked')is False and reused.get('runtime_artifact_authority')is False:
       queue.collect(item,cycle,'scheduled',reused);report['scheduled_reused']+=1
       finish(item,'completed',recorded=True);event(item,'previous-native-schedule-reused')
       covered(item,'scheduled_reused',digest(reused))
       continue
     if phase!='record'and not initial_started:
      report['initial_local_calls']+=1;initial=local('prepare');report['initial_local']=initial;initial_started=True
      if initial.get('status')=='prepared':initial_anchor=local('snapshot')
     work_guard();report['work_units']+=1;active=item;state=None;row=None
     if phase!='record'and initial.get('status')=='prepared':
      try:
       report['strict_lookup_attempts']+=1;row=call(item,'prepare',False)
       if strict_lookup_ready(row,ready):state='provisional'
       elif local_inputs_ready(row):state='local-inputs'
      except (Rejected,ValueError,OSError)as error:event(item,'collection-lookup-not-admitted',error=str(error)[:512])
     if state is None and phase in('all','record'):
      pending_record.append(item)
     else:collected(item,state or'unavailable',row)
    # Finish this bounded page's actual strict lookups before switching the
    # phase-local worker. Each record still independently freezes/audits its
    # complete original read-set; no lookup miss or ready hint is authority.
    for item in pending_record:
     guard();active=item;row=None
     if not record_open:local('record');record_open=True
     try:
      # Only explicit record CLI retains its original scheduling-only hint.
      if phase!='record'or not item['recorded_hint']:
       row=call(item,'record',False);report['recorded']+=1
      state='recorded'
     except (Rejected,ValueError,OSError)as error:state='rejected';event(item,'record-rejection',error=str(error)[:512])
     collected(item,state,row)
   if phase=='record':queue.set_stage(cycle,'done')
   else:
    scheduled=queue.phase_count(cycle,'scheduled')
    if scheduled==queue.total()and scheduled>0:queue.set_stage(cycle,'done')
    all_strict=queue.phase_count(cycle,'provisional')+scheduled==queue.total()and queue.total()>0
    if fresh and all_strict and not record_open and initial_anchor.get('status')=='environment-captured':
     proof=local('verify');report['warm_environment_verification']=proof
     if proof.get('status')=='environment-verified'and proof.get('environment_identity')==initial_anchor.get('environment_identity'):
      report['warm_fast_path']=True;commit('warm');report['verified_artifact_hits']=report['completed']-report['scheduled_reused'];queue.set_stage(cycle,'done')
    if cycle['stage']!='done':queue.set_stage(cycle,'build')
  if cycle['stage']in('build','prepare'):
   # Exactly one post-collection builder call per invocation, never per page.
   work_guard();report['final_local_calls']+=1;final=local('prepare');report['final_local']=final
   if final.get('status')!='prepared':
    while True:
     guard();unready=queue.phase_page(cycle,run,page_size,'prepare')
     if not unready:break
     for item in unready:queue.terminal_final(item,cycle,run,'deferred');finish(item,'deferred')
    raise Pending('final local libraries unavailable; disk collection retained')
   anchor=local('snapshot');queue.set_stage(cycle,'prepare')
   while True:
    guard();page=queue.phase_page(cycle,run,page_size,'prepare')
    if not page:break
    report['max_page_items']=max(report['max_page_items'],len(page))
    for item in page:
     work_guard();report['work_units']+=1;active=item
     try:
      row=call(item,'prepare',True)
      if local_preparation_ready(row,ready):
       queue.provisional_final(item,cycle,run,row);report['provisional_native']+=1
       if row['worker']['artifact_counts'].get('built',0)==0:report['provisional_artifact_hits']+=1
       event(item,'native-ready-provisional')
      else:
       state='rejected'if row['worker'].get('artifact_counts',{}).get('failed',0)else'deferred'
       if not local_inputs_ready(row):report['source_late_deferred']+=1
       queue.terminal_final(item,cycle,run,state);finish(item,state,row=row)
     except (Rejected,ValueError,OSError)as error:
      queue.terminal_final(item,cycle,run,'rejected');finish(item,'rejected');event(item,'prepare-rejection',error=str(error)[:512])
   proof=local('verify')if anchor.get('status')=='environment-captured'else dict(status='unavailable')
   report['final_environment_verification']=proof
   if proof.get('status')=='environment-verified'and proof.get('environment_identity')==anchor.get('environment_identity'):
    commit('commit');queue.set_stage(cycle,'done')
   else:raise Pending('final environment changed/unverified; native outcomes remain provisional')
  report['status']='no-candidates'if queue.total()==0 else'completed'if phase!='record'and report['visited']==queue.total()and report['deferred']==report['rejected']==0 and queue.phase_count(cycle,'rejected')==0 else'partial'
  report['required_preparation_complete']=report['status']=='completed'and report.get('index_rejected',0)==0
  report['full_manifest_preparation_verified']=report['required_preparation_complete']and report['scheduled_reused']==0
 except Pending as error:report.update(status='partial',pending_preparation=True,error=str(error))
 except Deferred as error:report.update(status='partial',budget_deferred=True,error=str(error))
 except (Stopped,KeyboardInterrupt)as error:
  report.update(status='stopped',error=str(error)or'interrupted');report['protection_stops']+=1
  if active:event(active,'protection-stopped')
 except Exception as error:report.update(status='failed',error=str(error)[:1024])
 finally:
  report['seconds']=clock()-start
  if queue is not None:
   queue.db.set_progress_handler(None,0)
   report.update(unique_units=queue.total(),observed_ready_hint_count=queue.hint_count(),index_complete=queue.get('indexed',False),active_manifest_sha256=queue.get('active_manifest',None),incoming_manifest_sha256=queue.manifest_sha)
   if cycle is not None:report.update(cycle_stage=cycle['stage'],cycle_collected=queue.phase_count(cycle)if phase!='prepare'else 0,cycle_first_pass_strict=queue.phase_count(cycle,'provisional'),cycle_rejected=queue.phase_count(cycle,'rejected'))
   report['not_visited']=max(0,report['unique_units']-report['visited']);report['budget_deferred_remaining']=report['not_visited']if report['index_complete']else None
   report['rejected_total']=report['rejected']+queue.get('rejected');report['deferred_total_known']=report['deferred']+report['not_visited']
   # Emit only after actual per-unit commits, full membership and the final
   # environment proof. No worker-ready boolean or cache-hit arithmetic alone
   # can manufacture this record; incomplete/interrupted runs have no coverage.
   proof=report.get('warm_environment_verification',{})if report['warm_fast_path']else report.get('final_environment_verification',{})
   if report['required_preparation_complete']and proof.get('status')=='environment-verified':
    body=dict(schema=1,kind='current-verified-required-queue',manifest_sha256=queue.manifest_sha,
        job_id=queue.job,cycle_id=cycle['id'],context_sha256=digest(context),environment_identity=proof.get('environment_identity'),
        required=queue.total(),**coverage,bindings_sha256=bindings.hexdigest(),artifact_authority=False)
    report['completion_coverage']=dict(body=body,sha256=digest(body))
    if not verified_completion_schedule(report):report.pop('completion_coverage')
   queue.close()
 return report
