"""Skip unchanged anticipatory PSO work, never the renderer's final admission.

Only a previous *actual* full native verification can publish this hint. Current
code/options/manifest/toolchain identity and continuous event history must match.
Owned geometry or unknown input protocols stay on their original native path.
"""
from pathlib import Path
import hashlib,json,os,stat,re
from incremental_file_hashes import digest,replaceable
from startup_change_journal import query,unchanged,MAX_MESSAGE
from game_pso_owned_audit import checked_bytes
from launcher_game_catalog import directory,write_json,file_sha
from game_pso_progress_identity import input_identity

MAX_PROOF_BYTES=512*1024*1024
MAX_PROOF_FILES=100000

def startup_preference(path,check):
 """Canonical scheduling preference, never an input or admission certificate.

 Native RememberPreparedArtifact may select any successfully prepared recipe.
 Prove the selected receipt AND archive; the enclosing proof still hashes every
 receipt/archive byte. Changing that preference cannot change a factory's key.
 Invalid, missing, foreign or damaged targets refuse the scheduling shortcut.
 """
 raw,_=checked_bytes(path,limit=4096);entry=json.loads(raw)
 if set(entry)!={'schema','preparation','key'} or type(entry['schema'])is not int or entry['schema']!=1:return None
 prep=entry['preparation'];key=entry['key']
 if not all(isinstance(x,str)and re.fullmatch('[0-9a-f]{64}',x)for x in(prep,key)):return None
 if path.parent.name!='native-pipeline-artifacts-v1-'+prep:return None
 receipt=json.loads(checked_bytes(path.parent/(key+'.json'),limit=4096)[0])
 if set(receipt)!={'schema','recipe_key','preparation','archive','archive_bytes','archive_sha256'}:return None
 if type(receipt['schema'])is not int or receipt['schema']!=1 or receipt['recipe_key']!=key or receipt['preparation']!=prep:return None
 name=receipt['archive'];size=receipt['archive_bytes'];expected=receipt['archive_sha256']
 if not isinstance(name,str)or len(name)>240 or '/'in name or '\\'in name or '..'in name or not name.startswith(key+'-')or not name.endswith('.binaryarchive'):return None
 if type(size)is not int or not 0<size<=MAX_PROOF_BYTES or not isinstance(expected,str)or not re.fullmatch('[0-9a-f]{64}',expected):return None
 actual,actual_size=file_sha(path.parent/name,check)
 if actual!=expected or actual_size!=size:return None
 return dict(schema=1,preparation=prep,verified_artifact_preference=True)

def content_proof(roots,check):
 """Bounded streaming proof of logical bytes and security-relevant metadata.

 Timestamp/xattr-only changes are not byte changes. Enumerate the exact set,
 reject links/foreign nodes, and do not keep libraries in Python memory. The
 BEFORE and AFTER proofs must agree around an ACTUAL full native validation.
 """
 value=hashlib.sha256();total=0;count=0;folders=0
 def append(row):value.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n')
 def security(s):return [s.st_dev,s.st_mode,s.st_uid,s.st_gid,s.st_nlink]
 for raw in sorted(roots):
  root=directory(raw);s=root.stat();append(['root',str(root),s.st_dev,s.st_ino,security(s)])
  for base,dirs,names in os.walk(root,followlinks=False):
   check();folders+=1
   if folders>MAX_PROOF_FILES or len(Path(base).relative_to(root).parts)>16:return None
   dirs.sort();names.sort();folder=directory(base);append(['dir',str(folder.relative_to(root)),security(folder.stat())])
   for name in dirs:directory(folder/name)
   for name in names:
    check();p=folder/name;s=p.lstat();count+=1
    if count>MAX_PROOF_FILES or not stat.S_ISREG(s.st_mode)or s.st_uid!=os.getuid()or s.st_size<0 or total+s.st_size>MAX_PROOF_BYTES:return None
    sha,size=file_sha(p,check)
    preference=None
    if p.name=='startup-entry.json':
     preference=startup_preference(p,check)
     if preference is None:return None
    after=p.lstat()
    stamp=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
    if stamp(s)!=stamp(after)or size!=s.st_size:return None
    total+=size;append(['file',str(p.relative_to(root)),security(s),size,preference if preference is not None else sha])
 return dict(schema=1,sha256=value.hexdigest(),files=count,bytes=total)

class Schedule:
 def __init__(self,manifest,*,context,environment,gpu,artifact,progress,code_identity,rows,fingerprint,check):
  self.path=None;self.token=None;self.previous=None;self.roots=[];self.binding=None
  self.check=check;self.before_proof=None;self.reuse_via=None
  try:
   gpu=directory(gpu);artifact=directory(artifact);progress=directory(progress)
   if not artifact.is_relative_to(gpu)or not progress.is_relative_to(gpu):return
   local={k:v for k,v in environment.items()if k.startswith('BB_MTL_LOCAL_AOT_')}
   if local.get('BB_MTL_LOCAL_AOT_MODE')!='load':return
   import re
   if not re.fullmatch('[0-9a-f]{64}',local.get('BB_MTL_LOCAL_AOT_ENVIRONMENT_ID','')):return
   root=directory(local['BB_MTL_LOCAL_AOT_ROOT']);cohort=local['BB_MTL_LOCAL_AOT_COHORT'];build=local['BB_MTL_LOCAL_AOT_BUILD_ID']
   import re
   if root!=gpu/'local-metal'or not all(isinstance(x,str)and re.fullmatch('[0-9a-f]{64}',x)for x in(cohort,build)):return
   roots={artifact,directory(root/cohort/'requests'),directory(root/cohort/'artifacts'/build)}
   n=0
   for unit in rows(manifest):
    check();n+=1
    if n>100000 or unit.get('record_type')!='offline-pso-input'or 'owned_geometry_pso'in unit:return
    inp=input_identity(unit);cache=directory(inp['cache'])
    if not cache.is_relative_to(gpu/'cache'):return
    roots.add(cache)
    if len(roots)>16:return
   if not n:return
   self.roots=sorted(roots)
   self.binding=dict(schema=1,kind='anticipatory-pso-schedule-only',context=context,
                     manifest=fingerprint(manifest,check),local=local,code_identity=code_identity,recipes=n)
   self.path=progress/('unchanged-schedule-'+digest(self.binding)+'.json')
   try:
    raw,_=checked_bytes(self.path,limit=MAX_MESSAGE);saved=json.loads(raw);body=saved['body']
    if set(saved)=={'body','sha256'}and digest(body)==saved['sha256']and body['binding']==self.binding and body.get('actual_full_native_verification')is True:
     if unchanged(body.get('journal'),self.roots):
      self.token=body['journal'];self.previous=body;self.reuse_via='journal'
     elif body.get('content_proof') is not None:
      current=content_proof(self.roots,check)
      if current is not None and current==body['content_proof']:
       self.previous=body;self.before_proof=current;self.reuse_via='unchanged-logical-bytes'

   except(OSError,ValueError,TypeError,KeyError):pass
   if self.previous is None:
    self.token=query(self.roots)
    self.before_proof=content_proof(self.roots,check)
  except(OSError,ValueError,TypeError,KeyError):self.path=None
 def reuse(self):
  self.check()
  if self.previous is None:return None
  try:
   if self.reuse_via=='journal':
    if not unchanged(self.token,self.roots):return None
   elif self.reuse_via=='unchanged-logical-bytes':
    if content_proof(self.roots,self.check)!=self.before_proof:return None
   else:return None
  except(OSError,ValueError,TypeError):return None
  return dict(status='unchanged-schedule-reused',completed=self.binding['recipes'],
              persistent_schedule_reused=True,schedule_reuse_proof=self.reuse_via,previous_actual_native_verification=True,
              full_manifest_preparation_verified=False,native_verification_invoked_this_call=False,
              runtime_full_key_and_artifact_checks_retained=True,runtime_cache_import_allowed=False)
 def publish(self,result):
  if self.path is None or result.get('status')!='completed'or result.get('full_manifest_preparation_verified')is not True:return False
  self.check()
  after=content_proof(self.roots,self.check)
  stable=(self.before_proof is not None and after==self.before_proof)
  if not stable and self.token is None:return False
  body=dict(binding=self.binding,journal=self.token,actual_full_native_verification=True)
  if stable:body['content_proof']=after
  saved=dict(body=body,sha256=digest(body))
  if len(json.dumps(saved,separators=(',',':')).encode())>MAX_MESSAGE or not replaceable(self.path):return False
  # Cursor precedes the native pass: new record/build outputs invalidate the
  # NEXT launch until they have themselves passed a stable full verification.
  write_json(self.path,saved);return True
