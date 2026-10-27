"""Skip unchanged anticipatory PSO work, never the renderer's final admission.

Only a previous *actual* full native verification can publish this hint. Current
code/options/manifest/toolchain identity and continuous event history must match.
Owned geometry or unknown input protocols stay on their original native path.
"""
from pathlib import Path
import hashlib,json,os,stat
from incremental_file_hashes import digest,replaceable
from startup_change_journal import query,unchanged,MAX_MESSAGE
from game_pso_owned_audit import checked_bytes
from launcher_game_catalog import directory,write_json
from game_pso_progress_identity import input_identity

class Schedule:
 def __init__(self,manifest,*,context,environment,gpu,artifact,progress,code_identity,rows,fingerprint,check):
  self.path=None;self.token=None;self.previous=None;self.roots=[];self.binding=None
  self.check=check
  try:
   gpu=directory(gpu);artifact=directory(artifact);progress=directory(progress)
   if not artifact.is_relative_to(gpu)or not progress.is_relative_to(gpu):return
   local={k:v for k,v in environment.items()if k.startswith('BB_MTL_LOCAL_AOT_')}
   if local.get('BB_MTL_LOCAL_AOT_MODE')!='load':return
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
     if unchanged(body['journal'],self.roots):self.token=body['journal'];self.previous=body
   except(OSError,ValueError,TypeError,KeyError):pass
   if self.previous is None:self.token=query(self.roots)
  except(OSError,ValueError,TypeError,KeyError):self.path=None
 def reuse(self):
  self.check()
  if self.previous is None or not unchanged(self.token,self.roots):return None
  return dict(status='unchanged-schedule-reused',completed=self.binding['recipes'],
              persistent_schedule_reused=True,previous_actual_native_verification=True,
              full_manifest_preparation_verified=False,native_verification_invoked_this_call=False,
              runtime_full_key_and_artifact_checks_retained=True,runtime_cache_import_allowed=False)
 def publish(self,result):
  if self.path is None or self.token is None or result.get('status')!='completed'or result.get('full_manifest_preparation_verified')is not True:return False
  self.check()
  body=dict(binding=self.binding,journal=self.token,actual_full_native_verification=True)
  saved=dict(body=body,sha256=digest(body))
  if len(json.dumps(saved,separators=(',',':')).encode())>MAX_MESSAGE or not replaceable(self.path):return False
  # Cursor precedes the native pass: new record/build outputs invalidate the
  # NEXT launch until they have themselves passed a stable full verification.
  write_json(self.path,saved);return True
