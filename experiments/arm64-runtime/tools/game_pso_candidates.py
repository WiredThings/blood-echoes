"""Automatic cache-local candidates. Old metadata is audit-only, never runtime input."""
from pathlib import Path
import hashlib,json,os,re,stat,struct
from game_pso_owned_audit import checked_bytes
from inventory_shader_cache import inventory,combine
from decode_shader_cache import decode_key,decode_meta,semantic_profile
from game_pso_progress_identity import layout_abi_identity,cache_scope

MAX_OUTPUT=256*1024*1024
MAX_RECORDS=100000
TITLE=re.compile(r'CUSA[0-9]{5}')
def need(v,m):
 if not v:raise ValueError(m)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def directory(p):
 p=Path(p).absolute();s=p.lstat();need(p==p.resolve(strict=True)and stat.S_ISDIR(s.st_mode)and s.st_uid==os.getuid(),'owned canonical candidate directory');return p
def reference(cache,row):
 name=row['name'];need(Path(name).name==name,'cache basename');p=cache/name
 data,ref=checked_bytes(p,expected=row['sha256']);need(len(data)==row['bytes'],'cache snapshot size changed')
 return data,dict(ref,name=name)
def classify(key,stages):
 names={s['logical_stage']for s in stages}
 if key['kind']=='compute':need(names=={'compute'},'compute graph');return dict(family='compute-direct',tessellation=False)
 need('vertex'in names and 'compute'not in names,'graphics graph')
 if 'geometry'in names:return dict(family='geometry-existing-session-span-rejected',tessellation=False)
 if names&{'tessellation_control','tessellation_eval'}:
  need({'tessellation_control','tessellation_eval'}<=names,'paired tessellation stages')
  return dict(family='tessellation-existing-three-variant-chain',tessellation=True)
 need(names<={'vertex','fragment'},'graphics stage set');return dict(family='graphics-existing-plain-or-rectlist-chain',tessellation=False,rectlist='original-native-key-chain')
def cache_records(cache,layout,check=lambda:None,*,snapshot_sink=None):
 # Existing inventory joins only within this directory. No static-game catalog.
 paths=[p for p in cache.iterdir()if p.suffix in('.meta','.spv','.key','.bin')]
 need(len(paths)<=20000,'file count budget')
 for p in paths:
  check();s=p.lstat()
  need(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and 0<s.st_size<=1024*1024,'owned bounded cache file')
 inv=inventory(cache,catalog=None)
 names=[inv['profile'],*inv['pipeline_keys']]
 for pair in inv['paired_specializations']:names.extend((pair['meta'],pair['spirv']))
 inventory_sha=digest(dict(path=str(cache),files=sorted({r['name']:r for r in names}.values(),key=lambda r:r['name'])))
 profile,pref=reference(cache,inv['profile']);ps=semantic_profile(profile,layout)
 abi=layout_abi_identity(layout);cid=cache_scope(cache,ps['semantic_sha256'],abi)
 namespace=dict(cache=str(cache),cache_id=cid,scope_version=2,serialization_abi_sha256=abi)
 if snapshot_sink is not None:snapshot_sink(dict(cache=str(cache),cache_id=cid,inventory_sha256=inventory_sha,profile_semantic_sha256=ps['semantic_sha256'],serialization_abi_sha256=abi,files=len({r['name']for r in names})))
 for error in inv['errors']:
  yield dict(schema=2,record_type='rejected-input',namespace=namespace,key_name=error['name'],reason=error['reason'])
 pairs={p['meta']['permutation_hash']:p for p in inv['paired_specializations']}
 for r in inv['pipeline_keys']:
  try:
   raw,kref=reference(cache,r);key=decode_key(raw,layout)
   expected=key['stages']if key['kind']=='graphics'else{'compute':key['stage_permutation_hashes'][0]}
   expected={k:v for k,v in expected.items()if int(v,16)}
   need(bool(expected)and len(expected)<=6,'bounded complete stage set')
   stages=[];reasons=[];refs=[pref,kref]
   for logical,ph in expected.items():
    pair=pairs.get(ph);need(pair is not None,'missing cache-local stage')
    metadata,mref=reference(cache,pair['meta']);spirv,sref=reference(cache,pair['spirv']);m=decode_meta(metadata,layout)
    need((m['program_hash'],m['permutation'],m['permutation_hash'],m['logical_stage'])==(pair['program_hash'],pair['permutation'],ph,logical),'cache-local stage mismatch')
    need(ph==f"{combine(int(m['program_hash'],16),m['permutation']):016x}",'canonical stage join')
    refs.extend((mref,sref));need(sum(x['bytes']for x in refs)<=8*1024*1024,'whole candidate budget')
    if m['motion_vertex_cache_load_rejected']:reasons.append('motion-vertex-existing-preload-rejection')
    if m['session_pointer_in_geometry_runtime']or logical=='geometry':reasons.append('geometry-session-chain-not-admitted')
    stages.append(dict(logical_stage=logical,hardware_stage=m['stage'],program_hash=m['program_hash'],cache_local_permutation=m['permutation'],permutation_hash=ph,metadata=mref,spirv=sref,serialized_spec_sha256=m['serialized_spec_sha256'],metadata_role='audit-only; never native Info/SRT deserialize'))
   family=classify(key,stages);eligible=not reasons
   rid=digest(dict(profile=ps['semantic_sha256'],key=kref['sha256'],stages=stages))
   yield dict(schema=2,record_type='offline-pso-input',namespace=namespace,recipe_blob_identity=rid,
    profile=dict(reference=pref,semantic_sha256=ps['semantic_sha256'],compiler_compatibility_proof=False),key=dict(reference=kref,decoded=key),classification=family,stages=stages,
    source_provenance=dict(status='unknown',enabled=False,current_compiler_device_compatibility='not-validated'),
    execution_policy=dict(runtime_cache_import_allowed=False,candidate_preparation=dict(policy_eligible=eligible,executor_approval='not-proven',unknown_producer_tolerated_for_candidate_attempt=True,frozen_input_set_identity=digest(refs)),actual_final_read_admission=dict(allowed=False)),session_rejections=sorted(set(reasons)))
  except(ValueError,KeyError,TypeError,OSError,struct.error)as e:
   yield dict(schema=2,record_type='rejected-input',namespace=namespace,key_name=r['name'],reason=str(e))

def build(*,gpu_root,title_id,layout,output,check=lambda:None):
 gpu=directory(gpu_root);need(TITLE.fullmatch(title_id),'actual launcher TITLE_ID required')
 output=Path(output).absolute();need(not output.exists(),'new automatic manifest required');directory(output.parent)
 root=gpu/'cache';summary=dict(schema=1,records=0,eligible=0,rejected=0,excluded=0,caches=0,bytes=0,complete=True,runtime_cache_import_allowed=False,catalog_used=False,selected_gpu_root=str(gpu),title_id=title_id,families={},eligible_by_family={})
 if not root.exists():paths=[]
 else:
  directory(root);paths=sorted(p for p in root.iterdir()if p.name.startswith(title_id+'-metal-'))
  need(len(paths)<=128,'cache namespace count budget')
 with output.open('xb')as stream:
  for cache in paths:
   check();directory(cache);need(cache.parent==root,'direct selected cache only');summary['caches']+=1
   try:
    for row in cache_records(cache,layout,check,snapshot_sink=lambda receipt:summary.setdefault('cache_inventory_audits',[]).append(receipt)):
     check();encoded=json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n'
     need(len(encoded)<=256*1024 and summary['records']<MAX_RECORDS and summary['bytes']+len(encoded)<=MAX_OUTPUT,'automatic manifest bounded coverage')
     stream.write(encoded);summary['bytes']+=len(encoded);summary['records']+=1
     if row['record_type']=='rejected-input':summary['rejected']+=1
     else:
      family=row['classification']['family'];summary['families'][family]=summary['families'].get(family,0)+1
      if row['execution_policy']['candidate_preparation']['policy_eligible']:
       summary['eligible']+=1;summary['eligible_by_family'][family]=summary['eligible_by_family'].get(family,0)+1
      else:summary['excluded']+=1
   except(ValueError,KeyError,OSError)as e:
    summary.setdefault('cache_errors',[]).append(dict(cache=cache.name,error=str(e)));summary['complete']=False
  stream.flush();os.fsync(stream.fileno())
 with output.open('rb')as stream:summary['manifest_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
 return summary
