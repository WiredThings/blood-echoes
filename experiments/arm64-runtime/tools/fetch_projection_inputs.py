"""Explicit fetch compilation projections, never synthetic Buffer descriptors.

Only a proven unique conversion class is projected. Actual C++ consumers must
independently enumerate the original enum helpers and reproduce observed SPIR-V.
"""
from pathlib import Path
import argparse,collections,hashlib,json,struct,time
from complete_pipeline_inputs import directory,file_sha,digest
from game_module_inputs import source_code,need
from game_pso_owned_audit import checked_bytes,metadata_sections,load_canonical_layout
from game_pso_progress_identity import layout_abi_identity
from decode_shader_cache import semantic_profile,decode_key
from prepare_game_psos import new_bytes
from static_program_links import rows,lookup_table

FIELDS=('semantic','dest_vgpr','num_elements','sgpr_base','dword_offset','instance_data','inst_offset','data_format','num_format')
# Exact backend equivalence classes for the physical V# enum domain (dfmt4b,
# nfmt3b), after the original RemapDataFormat/RemapNumberFormat operations.
# Values are VkFormat wire numbers already stored in canonical pipeline keys.
NONE_FORMATS={37:(10,0),91:(12,0),100:(4,7),103:(11,7),106:(13,7),109:(14,7)}
AMBIGUOUS_FORMATS={41,96,82,14}

def input_identity(row,*,profile_ref,spirv_ref,layout,game_identity):
 """Stable byte/proof identity, not producer compatibility or artifact authority.

 Paths and timestamps are provenance only. Keep distinct raw metadata, profile,
 reference SPIR-V and all key proofs; never collapse at program hash granularity.
 The existing preparation queue separately supplies the compiler/device cohort.
 """
 value=dict(schema=1,recipe='explicit-fetch-projection-v1',game_identity=game_identity,
  program=row['static_identity'],metadata_sha256=row['observed']['metadata']['sha256'],
  metadata_bytes=row['observed']['metadata']['bytes'],
  profile=dict(sha256=profile_ref['sha256'],bytes=profile_ref['bytes']),
  observed_spirv=dict(sha256=spirv_ref['sha256'],bytes=spirv_ref['bytes']),
  key_proofs=sorted({(k['source_reference']['sha256'],k['source_reference']['bytes'])for k in row['pipeline_key_vertex_inputs']}),
  projection=project(row),serialization_abi_sha256=layout_abi_identity(layout))
 return dict(source_input_id=digest(value),identity=value)

def iter_candidates(sidecar,*,check=lambda:None):
 """Exhaust the existing observed directory automatically, yielding every gap.
 This is suitable for the existing startup preparation queue's incremental index.
 No material/FLVER defaults are manufactured for unknown states.
 """
 with Path(sidecar).open()as source:
  for seen,line in enumerate(source,1):
   check();need(seen<=100000 and len(line)<=256*1024,'bounded fetch sidecar');row=json.loads(line)
   try:project(row)
   except ValueError as error:
    yield dict(status='deferred',reason=str(error),source=row);continue
   yield dict(status='eligible',source=row)

def project(row):
 attributes=row['fetch']['attributes'];keys=row['pipeline_key_vertex_inputs'];need(0<len(attributes)<=32 and keys,'bounded fetch and exact key associations')
 result=[]
 for i,a in enumerate(attributes):
  f=a['fields'];need(all(type(f[k])is int and 0<=f[k]<=255 for k in FIELDS),'fetch nine u8 fields');need(f['data_format']==0,'typed overrides need separate proof')
  need(f['semantic']<32 and f['dest_vgpr']<=252 and 1<=f['num_elements']<=4 and f['sgpr_base']<=14 and f['instance_data']<=3,'fetch attribute range')
  v=a['vertex_specialization'];need(v is not None,'exact vertex specialization required');need(v['divisor']or v['num_class']or v['dst_select'],'nonnull V# cannot be inferred from default specialization')
  formats=sorted({k['vertex_formats'][i]for k in keys});need(all(fmt in NONE_FORMATS for fmt in formats),'number-conversion-ambiguous-or-unproven')
  need(v['num_class']==0,'unique known backend class must match observed specialization');swizzle=list(struct.pack('<I',v['dst_select']));need(all(s in(0,1,4,5,6,7)for s in swizzle),'observed effective component enum')
  if f['instance_data']in(1,2):need(isinstance(row['step_rates'],dict),'step-rate scalar requires canonical runtime projection')
  expected_divisor=0 if f['instance_data']==0 else row['step_rates']['step_rate_0']if f['instance_data']==1 else row['step_rates']['step_rate_1']if f['instance_data']==2 else 1
  need(v['divisor']==expected_divisor,'observed divisor and runtime step-rate must agree')
  result.append(dict(attribute_index=i,fields={k:f[k]for k in FIELDS},nonnull=True,number_class=0,read_number_conversion=0,effective_dst_select=swizzle,divisor=v['divisor'],proof=dict(backend_formats=formats,unique_conversion_required=True,raw_descriptor_materialized=False)))
 return dict(schema=1,raw_Vsharp_reconstructed=False,fetch={k:row['fetch'][k]for k in('code_size','vertex_sgpr_offset','instance_sgpr_offset')},attributes=result)

def verify_observed(row,layout):
 r=row['observed'];metadata,meta_ref=checked_bytes(Path(r['metadata']['path']),expected=r['metadata']['sha256']);need(len(metadata)==r['metadata']['bytes'],'observed meta extent');meta,sections=metadata_sections(metadata,layout);identity=row['static_identity']
 need((meta['program_hash'],meta['stage'],meta['logical_stage'],meta['permutation'],meta['permutation_hash'],meta['serialized_spec_sha256'])==(identity['program_hash'],identity['hardware_stage'],'vertex',r['permutation'],r['permutation_hash'],r['serialized_spec_sha256']),'observed fetch stage/spec identity')
 need(meta['stage']in('vertex','local')and meta['fetch']is not None and not meta['motion_vertex_cache_load_rejected'],'admitted fetch stage/session subset');need(layout['Gcn::VertexAttribute.size']==9,'exact nine-byte canonical fetch layout');need(len(meta['fetch']['attributes'])==len(row['fetch']['attributes'])==len(meta['vertex_attributes']),'exact attribute specialization order')
 need(all(meta['fetch'][k]==row['fetch'][k]for k in('code_size','vertex_sgpr_offset','instance_sgpr_offset')),'fetch parser scalars')
 for a,b,v in zip(meta['fetch']['attributes'],row['fetch']['attributes'],meta['vertex_attributes']):
  raw=bytes.fromhex(a['raw_hex']);need(len(raw)==9 and dict(zip(FIELDS,raw))=={k:b['fields'][k]for k in FIELDS},'all nine observed fetch fields');need(all(v[k]==b['vertex_specialization'][k]for k in('divisor','num_class','dst_select')),'observed vertex specialization')
 runtime=bytes.fromhex(meta['runtime_raw_hex']);sw=layout['RuntimeInfo.sw']
 actual_steps={field:struct.unpack_from('<I',runtime,sw+layout['SwVertexRuntimeInfo.'+field])[0]for field in('step_rate_0','step_rate_1')}
 if row['step_rates']is not None:need(actual_steps==row['step_rates'],'actual runtime step rates')
 for a in row['fetch']['attributes']:
  mode=a['fields']['instance_data'];expected=0 if mode==0 else actual_steps['step_rate_0']if mode==1 else actual_steps['step_rate_1']if mode==2 else 1
  need(a['vertex_specialization']['divisor']==expected,'divisor from actual serialized runtime')
 refs=[]
 for key in row['pipeline_key_vertex_inputs']:
  ref=key['source_reference'];raw,current=checked_bytes(Path(ref['path']),expected=ref['sha256']);need(len(raw)==ref['bytes'],'observed key extent');decoded=decode_key(raw,layout)
  need(decoded['kind']=='graphics'and decoded['stages']['vertex']==meta['permutation_hash']and decoded['vertex_formats']==key['vertex_formats']and decoded['vertex_attributes']==key['vertex_attributes']and decoded['raw_key_sha256']==key['raw_key_sha256'],'exact same-cache key vertex inputs');need(Path(ref['path']).parent==Path(meta_ref['path']).parent,'same original QA cache')
  refs.append(dict(reference=dict(current,name=Path(ref['path']).name),vertex_permutation_hash=meta['permutation_hash'],vertex_formats=decoded['vertex_formats'],vertex_attributes=decoded['vertex_attributes']))
 cache=Path(meta_ref['path']).parent;profile,profile_ref=checked_bytes(cache/'profile.bin');semantic_profile(profile,layout);spirv,spv_ref=checked_bytes(cache/f'0x{int(meta["program_hash"],16):016x}_{meta["permutation"]}.spv');need(len(spirv)>=20 and len(spirv)%4==0 and struct.unpack_from('<I',spirv)[0]==0x07230203,'observed SPIR-V header');need(meta['flattened_words']>=16 and meta['flattened_words']==meta['srt_declared_flattened_words']and meta['walker_bytes']<=256*1024 and meta['walker_bytes']%12==0,'owned flat/walker bounds')
 return metadata,meta,sections,profile,spirv,refs,meta_ref

def freeze_projection(row,*,layout,catalog,table,output):
 projection=project(row);metadata,meta,sections,profile,spirv,keys,meta_ref=verify_observed(row,layout);identity=row['static_identity'];candidates=table.get((identity['program_hash'],identity['hardware_stage']),[]);need(len(candidates)==1 and candidates[0]['identity']==identity,'unique exact static code source');reference=candidates[0]['variants'][0]['evidence_refs'][0];code,source=source_code(catalog['game_root'],catalog,identity,reference)
 output=Path(output).absolute();need(not output.exists(),'fresh owned fetch projection directory');output.mkdir(mode=0o700);directory(output);spec=sections['specialization'];flat=sections['flattened_table_audit_only'];walk=sections['walker_forbidden'];walker=metadata[walk['offset']:walk['offset']+walk['bytes']]
 for i,key in enumerate(keys):
  data,_=checked_bytes(Path(key['reference']['path']),expected=key['reference']['sha256']);key['reference']=new_bytes(output,f'key-{i}.bin',data)
 packet=dict(packet_schema=1,kind='owned-gcn-fetch-projection-module-input',candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,program_hash=identity['program_hash'],hardware_stage=identity['hardware_stage'],logical_stage='vertex',profile=new_bytes(output,'profile.bin',profile),code=new_bytes(output,'code.bin',code),spec=new_bytes(output,'spec.bin',metadata[spec['offset']:spec['offset']+spec['bytes']]),flat=new_bytes(output,'flat.bin',metadata[flat['offset']+8:flat['offset']+flat['bytes']]),user_data_words=16,flat_dwords=meta['flattened_words'],walker=new_bytes(output,'walker.bin',walker)if walker else None,walker_bytes=len(walker),expected_spirv=new_bytes(output,'expected-spirv.bin',spirv),program_base=dict(known=False,value=None,required_gate='reject-offset-by-program-base'),source_provenance=source,fetch_projection=projection,projection_source_keys=keys,source_metadata=dict(sha256=meta_ref['sha256'],original_spec_sha256=spec['sha256'],opaque_Info_bytes_forwarded=0,raw_Vsharp_captured=False),layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],profile_size=layout['Profile.size'],serialization_abi_sha256=layout_abi_identity(layout)),constraints=dict(old_walker_execution_allowed=False,guest_dereference_allowed=False,final_runtime_import_allowed=False,requires_regenerated_srt_equality=True,requires_current_profile_match=True,requires_exact_fetch_projection=True,requires_native_enum_conversion_uniqueness=True,raw_Vsharp_synthesis_allowed=False))
 packet['source_metadata'].update(header=new_bytes(output,'metadata-header.bin',metadata[:24]),permutation_hash=meta['permutation_hash'],permutation=meta['permutation'],original_path=meta_ref['path'])
 raw=json.dumps(packet,sort_keys=True,separators=(',',':')).encode()+b'\n';need(len(raw)<=256*1024,'projection packet bound');new_bytes(output,'packet.json',raw);return packet

def scan(*,sidecar,layout,catalog,table,output,limits=None,check=lambda:None):
 """Generic automatic converter. Default covers every provable source state.
 Optional limits exist for authored/representative controls, not launcher policy;
 even with limits every entry is classified, and unvisited states are explicit.
 Output manifests are only candidate input and carry no runtime readiness claim.
 """
 need(not output.exists(),'fresh projection evidence');output.mkdir(mode=0o700);directory(output);counts=collections.Counter();reasons=collections.Counter();packets=[];selected=set();eligible=collections.Counter()
 if limits is not None:need(all(k in('vertex','local')and type(v)is int and 0<=v<=128 for k,v in limits.items()),'bounded test-only limits')
 for item in iter_candidates(sidecar,check=check):
   row=item['source'];counts['records']+=1
   if item['status']=='deferred':reasons[item['reason']]+=1;continue
   stage=row['static_identity']['hardware_stage'];eligible[stage]+=1;cache=Path(row['observed']['metadata']['path']).parent
   # Validate aliases before deduplication. A matching digest in a sidecar is
   # not permission to skip rereading a changed or missing original source.
   verify_observed(row,layout)
   _,profile_ref=checked_bytes(cache/'profile.bin');_,spirv_ref=checked_bytes(cache/f'0x{int(row["static_identity"]["program_hash"],16):016x}_{row["observed"]["permutation"]}.spv')
   identity=input_identity(row,profile_ref=profile_ref,spirv_ref=spirv_ref,layout=layout,game_identity=catalog['game_identity']);uid=identity['source_input_id']
   if uid in selected:counts['exact_duplicate_inputs']+=1;continue
   if limits is not None and(stage not in limits or counts['frozen_'+stage]>=limits[stage]):counts['representative_only_unvisited']+=1;continue
   name=f'{stage}-{uid}';packet=freeze_projection(row,layout=layout,catalog=catalog,table=table,output=output/name);selected.add(uid);counts['frozen_'+stage]+=1;packets.append(dict(packet=str(output/name/'packet.json'),source_input_id=uid,input_identity=identity['identity'],program_hash=packet['program_hash'],hardware_stage=stage,native_compile_verified=False))
 report=dict(schema=1,status='frozen-unique-fetch-projection-inputs',counts=dict(counts),field_projection_eligible=dict(eligible),rejection_reasons=dict(reasons),packets=packets,sidecar_sha256=file_sha(sidecar),actual_native_compilation_verified=False,raw_Vsharp_synthesized=False,GPU_started=False,game_started=False,formal_data_modified=False,source_cache_modified=False,runtime_cache_import_allowed=False)
 (output/'manifest.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');return report

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','sidecar','layout','layout-source','catalog','static-index','output'):ap.add_argument('--'+key,type=Path,required=True)
 a=ap.parse_args();P=directory(a.project);need(a.output.is_relative_to(P/'evidence'),'private new evidence only');layout=load_canonical_layout(a.layout,a.layout_source,file_sha(a.layout));catalog=json.loads(a.catalog.read_text());table=lookup_table(list(rows(a.static_index)))
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe projection CPU entry');policy=MemoryPolicy(before);started=time.monotonic()
 def check():
  reason=policy.observe(time.monotonic()-started,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  report=scan(sidecar=a.sidecar,layout=layout,catalog=catalog,table=table,output=a.output,check=check);report.update(before=before,after=observer.system());(a.output/'manifest.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps({k:v for k,v in report.items()if k!='packets'}))
if __name__=='__main__':main()
