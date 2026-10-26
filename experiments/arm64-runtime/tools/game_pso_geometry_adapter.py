"""Owned GS source packets inside the existing candidate/queue/worker flow.

The trusted source controller supplies an exact CPU-validated packet reference;
this adapter never treats old cache metadata or a resolver miss as ready.
"""
from pathlib import Path
import copy,json,os
from complete_geometry_psos import require_geometry,verify_receipt,verify_stage_binding
from complete_pipeline_inputs import directory,digest
from game_pso_owned_audit import checked_bytes
from game_pso_progress_identity import input_identity
from game_module_inputs import need
from prepare_game_psos import new_bytes

MAX_PACKET=256*1024
MAX_TOTAL=8*1024*1024

def is_geometry(unit):
 return unit.get('record_type')=='offline-pso-input'and any(s.get('logical_stage')=='geometry'for s in unit.get('stages',[]))

def read_reference(ref,root,*,limit=1024*1024):
 path=Path(ref['path']);need(path.parent==root and path.name==ref['name'],'one owned frozen packet root')
 raw,_=checked_bytes(path,limit=limit,expected=ref['sha256']);need(len(raw)==ref['bytes'],'exact frozen reference extent');return raw

def packet_reference(path,cache_root):
 root=directory(Path(cache_root));path=Path(path);need(path.is_relative_to(root)and path!=root,'stable GS packet below source controller cache')
 raw,ref=checked_bytes(path,limit=MAX_PACKET);return dict(ref,name=path.name)

def freeze_recipe(unit,layout,module_results,destination,cohort,artifact_cache_dir,check=lambda:None):
 """Assemble one exact recipe AFTER controller actual native/DSO admission.

 The controller keys stable storage by the complete input/producer/cohort, not
 program_hash. This function supplies no CPU execution/producer authorization.
 It independently binds all admitted outputs back to this exact original unit.
 """
 check();require_geometry(unit,layout);need(is_geometry(unit),'GS recipe required')
 need(set(module_results)=={s['logical_stage']for s in unit['stages']},'complete exact GS module result set')
 dest=Path(destination);directory(dest.parent);need(not dest.exists(),'fresh stable GS packet directory');dest.mkdir(mode=0o700);directory(dest)
 need(isinstance(cohort,str)and len(cohort)==64 and all(c in'0123456789abcdef'for c in cohort),'current PSO cohort')
 stages=[]
 for n,s in enumerate(unit['stages']):
  check();result=module_results[s['logical_stage']]
  for key,value in dict(metadata_sha256=s['metadata']['sha256'],profile_sha256=unit['profile']['reference']['sha256'],program_hash=s['program_hash'],hardware_stage=s['hardware_stage'],logical_stage=s['logical_stage'],permutation_hash=s['permutation_hash'],spirv_sha256=s['spirv']['sha256']).items():need(result[key]==value,'exact controller result association: '+key)
  raw,_=checked_bytes(Path(result['packet']),limit=MAX_PACKET,expected=result['packet_sha256']);m=json.loads(raw);verify_stage_binding(unit,s,m)
  proof_raw,_=checked_bytes(Path(result['receipt']),limit=MAX_PACKET,expected=result['receipt_sha256']);proof=json.loads(proof_raw);verify_receipt(m,proof,result['packet_sha256'])
  spv,_=checked_bytes(Path(result['spirv']),expected=result['spirv_sha256']);need(len(spv)==s['spirv']['bytes']==m['expected_spirv']['bytes'],'actual module output SPV extent')
  spec,_=checked_bytes(Path(m['spec']['path']),expected=m['spec']['sha256']);need(len(spec)==m['spec']['bytes'],'actual module input spec extent')
  spec_ref=new_bytes(dest,f'spec-{n}.bin',spec);spec_ref.update(source_offset=24,native_spec_only_reader_allowed=True)
  r=dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],spec=spec_ref,spirv=new_bytes(dest,f'spv-{n}.bin',spv),module_input_packet_sha256=result['packet_sha256'],retranslation_receipt=new_bytes(dest,f'receipt-{n}.json',proof_raw),source_provenance=m['source_provenance'])
  if s['hardware_stage']=='geometry':
   copy=m['gs_copy'];code,_=checked_bytes(Path(copy['code']['path']),expected=copy['code']['sha256']);need(len(code)==copy['code']['bytes'],'owned GS copy extent');r.update(spec_rebinding=m['spec_rebinding'],gs_copy=dict(copy,code=new_bytes(dest,f'copy-{n}.bin',code)))
  stages.append(r)
 key,_=checked_bytes(Path(unit['key']['reference']['path']),expected=unit['key']['reference']['sha256']);profile,_=checked_bytes(Path(unit['profile']['reference']['path']),expected=unit['profile']['reference']['sha256'])
 packet=dict(packet_schema=2,manifest_schema=2,geometry_pso_schema=1,kind='owned-gcn-geometry-pso-input',candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,walker_bytes_passed=0,cache_id=unit['namespace']['cache_id'],recipe_blob_identity=unit['recipe_blob_identity'],frozen_input_set_identity=unit['execution_policy']['candidate_preparation']['frozen_input_set_identity'],original_cache=unit['namespace']['cache'],current_cohort=cohort,artifact_cache_dir=str(artifact_cache_dir),allow_compile=True,key=new_bytes(dest,'key.bin',key),retranslation_profile=new_bytes(dest,'profile.bin',profile),layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],graphics_key_size=layout['GraphicsPipelineKey.size']),stages=stages)
 new_bytes(dest,'packet.json',json.dumps(packet,sort_keys=True,separators=(',',':')).encode()+b'\n');reference=packet_reference(dest/'packet.json',dest.parent);validate_packet(unit,layout,reference,dest.parent,check);return reference

def validate_packet(unit,layout,reference,cache_root,check=lambda:None):
 check();require_geometry(unit,layout);root=directory(Path(cache_root));path=Path(reference['path'])
 need(path.is_relative_to(root)and path.parent!=root,'owned indexed GS packet directory')
 base=directory(path.parent);raw=read_reference(reference,base,limit=MAX_PACKET);p=json.loads(raw)
 need(p.get('packet_schema')==2 and type(p.get('packet_schema'))is int and p.get('manifest_schema')==2 and p.get('geometry_pso_schema')==1 and p.get('kind')=='owned-gcn-geometry-pso-input','independent GS schema2 required')
 need(p.get('candidate_only')is True and p.get('runtime_cache_import_allowed')is False and p.get('native_info_srt_allowed')is False and type(p.get('walker_bytes_passed'))is int and p['walker_bytes_passed']==0,'GS candidate-only ownership contract')
 for field,value in dict(original_cache=unit['namespace']['cache'],cache_id=unit['namespace']['cache_id'],recipe_blob_identity=unit['recipe_blob_identity'],frozen_input_set_identity=unit['execution_policy']['candidate_preparation']['frozen_input_set_identity']).items():need(p.get(field)==value,'same actual GS recipe: '+field)
 expected=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],graphics_key_size=layout['GraphicsPipelineKey.size']);need(p['layout']==expected,'current canonical GS layout')
 held={};total=0
 def take(ref):
  nonlocal total
  path=Path(ref['path']);need(path not in held,'unique packet file reference');b=read_reference(ref,base);total+=len(b);need(total<=MAX_TOTAL,'whole GS packet byte budget');held[path]=b;return b
 key=take(p['key']);profile=take(p['retranslation_profile']);need(p['key']['sha256']==unit['key']['reference']['sha256']and p['retranslation_profile']['sha256']==unit['profile']['reference']['sha256'],'same original key and profile')
 need(len(p['stages'])==len(unit['stages']),'all original GS stages required')
 actual={s['logical_stage']:s for s in p['stages']};need(len(actual)==len(p['stages']),'no duplicate frozen stages')
 for s in unit['stages']:
  check();r=actual[s['logical_stage']]
  need((r['hardware_stage'],r['program_hash'],r['permutation'],r['permutation_hash'])==(s['hardware_stage'],s['program_hash'],s['cache_local_permutation'],s['permutation_hash']),'exact GS stage/permutation association')
  spec=take(r['spec']);spv=take(r['spirv']);receipt=json.loads(take(r['retranslation_receipt']))
  need(r['spec'].get('source_offset')==24 and r['spec'].get('native_spec_only_reader_allowed')is True and r['spirv']['sha256']==s['spirv']['sha256'],'same exact spec-only/SPV input')
  proof=r['source_provenance'];need(proof['title_id']=='CUSA03173'and proof['app_ver']=='01.09'and proof['code_prefix_verified']is True,'source controller fixed game provenance')
  module=dict(program_hash=r['program_hash'],hardware_stage=r['hardware_stage'],logical_stage=r['logical_stage'],code=dict(sha256=proof['code_sha256']),spec=r['spec'],profile=p['retranslation_profile'],expected_spirv=r['spirv'],source_provenance=proof)
  if s['hardware_stage']=='geometry':
   rb=r['spec_rebinding'];at=layout['Backend::Bindings.size']+layout['RuntimeInfo.hw']+layout['HwGeometryRuntimeInfo.vs_copy']
   need(rb['original_spec_sha256']==s['serialized_spec_sha256']and rb['runtime_span_offset']==at and rb['bytes']==16 and rb['serialized_span_zeroed']is True and rb['retired_pointer_exported']is False and len(spec[at:at+16])==16 and not any(spec[at:at+16]),'exact original GS spec and zero retired span')
   c=r['gs_copy'];copy=take(c['code']);cp=c['source_provenance'];need(len(copy)==c['code']['bytes']and cp['code_sha256']==c['code']['sha256']and cp['container_sha256']==proof['container_sha256']and cp['entry_sha256']==proof['entry_sha256'],'same independently owned GS copy source');module['gs_copy']=c
  else:need(r['spec']['sha256']==s['serialized_spec_sha256'],'same exact ordinary stage specialization')
  verify_receipt(module,receipt,r['module_input_packet_sha256'])
 return p,held

def admit(unit,layout,reference,cache_root,check=lambda:None):
 need(is_geometry(unit),'only GS enters independent adapter');validate_packet(unit,layout,reference,cache_root,check)
 need(unit['execution_policy']['candidate_preparation']['policy_eligible']is False,'only previously excluded GS is upgraded')
 need(set(unit.get('session_rejections',[]))<={'geometry-session-chain-not-admitted'},'other legacy exclusions cannot be upgraded')
 result=copy.deepcopy(unit);result['execution_policy']['candidate_preparation']['policy_eligible']=True
 result['execution_policy']['candidate_preparation']['executor_approval']='owned-GCN-module-retranslation-required-and-verified'
 result['classification']['family']='geometry-owned-source-existing-native-chain'
 result['owned_geometry_pso']=dict(schema=1,packet=reference,cache_root=str(directory(Path(cache_root))),input_identity_sha256=digest(input_identity(unit)))
 return result

def augment_manifest(source,output,*,layout,resolver,cache_root,check=lambda:None):
 """Resolver is an internal trusted controller callback, never request JSON."""
 need(callable(resolver),'trusted geometry resolver required');source=Path(source);output=Path(output);need(not output.exists(),'new combined automatic candidate manifest')
 summary=dict(admitted=0,missing=0,rejected=0,records=0,bytes=0,reasons=[])
 with source.open('rb')as src,output.open('xb')as dst:
  while True:
   check();raw=src.readline(MAX_PACKET+1)
   if not raw:break
   need(len(raw)<=MAX_PACKET and raw.endswith(b'\n')and summary['records']<100000,'bounded original candidates');u=json.loads(raw);summary['records']+=1
   if is_geometry(u):
    try:
     ref=resolver(u,layout,check)
     if ref is None:summary['missing']+=1
     else:u=admit(u,layout,ref,cache_root,check);summary['admitted']+=1
    except(ValueError,KeyError,OSError)as error:
     summary['rejected']+=1
     if len(summary['reasons'])<32:summary['reasons'].append(str(error))
   encoded=json.dumps(u,sort_keys=True,separators=(',',':')).encode()+b'\n';summary['bytes']+=len(encoded);need(len(encoded)<=MAX_PACKET and summary['bytes']<=256*1024*1024,'bounded adapted candidates');dst.write(encoded)
  dst.flush();os.fsync(dst.fileno())
 return summary

def freeze(unit,layout,root,cohort,allow_compile,*,artifact_cache_dir,check=lambda:None):
 """Rebase only scratch refs/current execution fields; same native schema/key."""
 adapter=unit['owned_geometry_pso'];need(adapter['schema']==1 and adapter['input_identity_sha256']==digest(input_identity(unit)),'same indexed geometry input identity')
 packet,held=validate_packet(unit,layout,adapter['packet'],adapter['cache_root'],check);root=directory(Path(root));need(not any(root.iterdir()),'fresh worker packet root')
 p=copy.deepcopy(packet);n=0
 def clone(ref):
  nonlocal n
  n+=1;value=new_bytes(root,f'owned-{n:02d}.bin',held[Path(ref['path'])]);return dict(ref,**value)
 p['key']=clone(p['key']);p['retranslation_profile']=clone(p['retranslation_profile'])
 for s in p['stages']:
  for key in('spec','spirv','retranslation_receipt'):s[key]=clone(s[key])
  if s['hardware_stage']=='geometry':s['gs_copy']['code']=clone(s['gs_copy']['code'])
 p.update(current_cohort=cohort,artifact_cache_dir=str(artifact_cache_dir),allow_compile=bool(allow_compile));new_bytes(root,'packet.json',json.dumps(p,sort_keys=True,separators=(',',':')).encode()+b'\n')
 return p,held
