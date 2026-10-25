"""Progress identity only. Never an authority for native artifact/cache import."""
from pathlib import Path
import copy,hashlib,json,re
HEX=re.compile(r'[0-9a-f]{64}')
class IdentityRejected(ValueError):pass
def need(ok,why):
 if not ok:raise IdentityRejected(why)
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def layout_abi_identity(layout):
 # Only compiler-witnessed layout values and declared Profile fields; source
 # timestamps, tool paths, fixture paths and whole inventories are not ABI.
 scalars={k:v for k,v in layout.items()if type(v)in(int,bool)and k not in('renderer_loaded','cached_code_registered')}
 need(scalars.get('pointer_bytes')==8 and scalars.get('little_endian')is True,'supported numeric ABI')
 return digest(dict(schema=1,serialization_versions=[10,7,5],scalars=scalars,profile_fields=layout['profile_fields']))
def cache_scope(cache,profile_semantic,abi):
 path=Path(cache);need(path.is_absolute()and HEX.fullmatch(profile_semantic)and HEX.fullmatch(abi),'cache progress scope')
 fields=dict(scope_version=2,cache=str(path),profile_semantic_sha256=profile_semantic,serialization_abi_sha256=abi)
 return digest(fields)
def ref(r,cache):
 p=Path(r['path']);need(p.is_absolute()and p.parent==cache and p.name==r['name'],'cache-local input path')
 need(type(r['bytes'])is int and 0<r['bytes']<=1024*1024 and HEX.fullmatch(r['sha256']),'exact bounded input reference')
 return {k:r[k]for k in('path','name','bytes','sha256')}
def input_identity(u):
 cache=Path(u['namespace']['cache']);need(cache.is_absolute(),'absolute cache scope')
 namespace=u['namespace'];scope=namespace['cache_id'];need(HEX.fullmatch(scope),'cache identity')
 if namespace.get('scope_version')==2:
  need(scope==cache_scope(cache,u['profile']['semantic_sha256'],namespace['serialization_abi_sha256']),'stable scope mismatch')
 stages=[];refs=[ref(u['profile']['reference'],cache),ref(u['key']['reference'],cache)]
 for s in u['stages']:
  mr,sr=ref(s['metadata'],cache),ref(s['spirv'],cache);refs.extend((mr,sr))
  stages.append(dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],
    cache_local_permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],
    metadata=mr,spirv=sr,serialized_spec_sha256=s['serialized_spec_sha256']))
 need(len(refs)<=14 and sum(x['bytes']for x in refs)<=8*1024*1024,'whole input identity budget')
 frozen=digest(refs);need(frozen==u['execution_policy']['candidate_preparation']['frozen_input_set_identity'],'exact frozen input identity')
 return dict(schema=2,cache=str(cache),cache_id=scope,profile=refs[0],profile_semantic_sha256=u['profile']['semantic_sha256'],
             key=refs[1],stages=stages,frozen_input_set_identity=frozen)
def execution_identity(u):
 # No payload field is projected away: inventory audits belong only in summary.
 return digest(u)
