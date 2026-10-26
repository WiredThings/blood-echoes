"""Separate owned GS-copy input contract; never admits the old GS worker path.

Old span bytes are only bounded metadata: both words are zeroed before any
native reader. A future consumer must rebind solely to independently owned GCN.
"""
from pathlib import Path
import argparse,collections,hashlib,json,struct,time
from complete_pipeline_inputs import directory,file_sha
from decode_shader_cache import semantic_profile
from game_module_inputs import source_code,need
from game_pso_owned_audit import checked_bytes,metadata_sections,load_canonical_layout
from game_pso_progress_identity import layout_abi_identity
from inventory_shader_cache import combine
from prepare_game_psos import new_bytes
from static_program_links import rows,lookup_table,geometry_copy_provenance

MAX_RECORDS=100000

def geometry_fields(runtime,layout):
 """Canonical fixed GS prefix with compiler-extracted span/hash anchors."""
 need(layout['HwGeometryRuntimeInfo.vs_copy']==56 and layout['HwGeometryRuntimeInfo.vs_copy.bytes']==16 and layout['HwGeometryRuntimeInfo.vs_copy_hash']==72,'reviewed GS prefix layout anchors')
 need(len(runtime)==layout['RuntimeInfo.size'],'exact GS runtime interval');base=layout['RuntimeInfo.hw']
 def word(at):return struct.unpack_from('<I',runtime,base+at)[0]
 fields={k:word(off)for k,off in [('num_outputs',0),('num_invocations',16),('output_vertices',20),('in_vertex_data_size',24),('out_vertex_data_size',28),('in_primitive',32),('mode',52)]}
 fields.update(outputs=list(runtime[base+4:base+16]),out_primitive=list(struct.unpack_from('<4I',runtime,base+36)))
 need(fields['num_outputs']<=3 and all(v<=22 for v in fields['outputs']),'GS output map/count')
 need(1<=fields['num_invocations']<=32 and 1<=fields['output_vertices']<=1024,'GS invocation/output bound')
 need(fields['in_vertex_data_size']<=4096 and fields['out_vertex_data_size']<=4096,'GS vertex extent')
 need(fields['in_primitive']in(0,1,2,3,4,5,6,9,10,11,12,13,17,18,19,20,21)and fields['mode']in(0,1,2,3,4)and all(v in(0,1,2)for v in fields['out_primitive']),'GS primitive/scenario enum')
 return fields

def sanitized_spec(metadata,meta,sections,layout):
 need(meta['stage']=='geometry'and meta['logical_stage']=='geometry','exact GS/geometry pair required')
 need(meta['fetch']is None,'GS fetch register reads unavailable')
 need(meta['flattened_words']>=16 and meta['flattened_words']==meta['srt_declared_flattened_words'],'GS flat/SRT extent')
 need(meta['walker_bytes']<=256*1024 and meta['walker_bytes']%12==0,'GS portable walker data')
 s=sections['specialization'];spec=bytearray(metadata[s['offset']:s['offset']+s['bytes']]);begin=layout['Backend::Bindings.size'];runtime=bytes(spec[begin:begin+layout['RuntimeInfo.size']]);fields=geometry_fields(runtime,layout)
 at=begin+layout['RuntimeInfo.hw']+layout['HwGeometryRuntimeInfo.vs_copy'];need(at+16<=len(spec),'GS span in exact spec');_,words=struct.unpack_from('<QQ',spec,at);context=meta['context']['geometry_copy']
 need(words==context['words']and 0<words<=512*1024//4,'GS original span length bound');need(combine(int(meta['program_hash'],16),meta['permutation'])==int(meta['permutation_hash'],16),'GS canonical permutation hash')
 spec[at:at+16]=bytes(16)
 proof=dict(runtime_span_offset=at,bytes=16,serialized_span_zeroed=True,original_spec_sha256=s['sha256'],original_copy_words=words,original_copy_hash=context['hash'],retired_pointer_exported=False,original_pointer_followed=False)
 return bytes(spec),proof,fields

def freeze_geometry(*,metadata_ref,spirv_ref,profile_ref,layout,game_root,catalog,table,output):
 output=Path(output).absolute();need(not output.exists(),'new owned GS output');output.mkdir(mode=0o700);directory(output)
 metadata,_=checked_bytes(Path(metadata_ref['path']),expected=metadata_ref['sha256']);spirv,_=checked_bytes(Path(spirv_ref['path']),expected=spirv_ref['sha256']);profile,_=checked_bytes(Path(profile_ref['path']),expected=profile_ref['sha256']);semantic_profile(profile,layout)
 need(len(metadata)==metadata_ref['bytes']and len(spirv)==spirv_ref['bytes']and len(profile)==profile_ref['bytes'],'GS exact input byte counts');need(len(spirv)>=20 and len(spirv)%4==0 and struct.unpack_from('<I',spirv)[0]==0x07230203,'GS observed SPIR-V header')
 meta,sections=metadata_sections(metadata,layout);spec,rebinding,fields=sanitized_spec(metadata,meta,sections,layout)
 mains=table.get((meta['program_hash'],'geometry'),[]);need(len(mains)==1,'unique GS main static identity');main=mains[0];copy_proof=geometry_copy_provenance(main,meta['context']['geometry_copy'],table);need(copy_proof['status']=='owned-static-copy-source-identified','exact GS owned-copy provenance required')
 copy_ref=copy_proof['source_candidates'][0]['source'];main_refs=[e for v in main['variants']for e in v['evidence_refs']if(e['package'],e['entry'],e['source_sha256'],e['header_offset'])==(copy_ref['package'],copy_ref['entry'],copy_ref['source_sha256'],copy_ref['outer_header_offset'])];need(main_refs,'same source GS main/copy pair')
 code,main_source=source_code(game_root,catalog,main['identity'],main_refs[0]);copy_code,copy_source=source_code(game_root,catalog,copy_proof['identity'],copy_ref);need(len(copy_code)==copy_proof['words']*4 and len(code)+len(copy_code)<=512*1024,'owned combined GS code budget')
 flat=sections['flattened_table_audit_only'];walker=sections['walker_forbidden'];flat_data=metadata[flat['offset']+8:flat['offset']+flat['bytes']];walker_data=metadata[walker['offset']:walker['offset']+walker['bytes']]
 packet=dict(packet_schema=1,kind='owned-gcn-geometry-module-input',candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,program_hash=meta['program_hash'],hardware_stage='geometry',logical_stage='geometry',profile=new_bytes(output,'profile.bin',profile),code=new_bytes(output,'code.bin',code),spec=new_bytes(output,'spec.bin',spec),flat=new_bytes(output,'flat.bin',flat_data),user_data_words=16,flat_dwords=meta['flattened_words'],walker=new_bytes(output,'walker.bin',walker_data)if walker_data else None,walker_bytes=len(walker_data),expected_spirv=new_bytes(output,'expected-spirv.bin',spirv),program_base=dict(known=False,value=None,required_gate='reject-offset-by-program-base'),source_provenance=main_source,
  gs_copy=dict(code=new_bytes(output,'copy-code.bin',copy_code),program_hash=copy_proof['identity']['program_hash'],hardware_stage='vertex',words=copy_proof['words'],source_provenance=copy_source),spec_rebinding=rebinding,geometry_runtime=fields,
  layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],profile_size=layout['Profile.size'],serialization_abi_sha256=layout_abi_identity(layout)),constraints=dict(old_walker_execution_allowed=False,guest_dereference_allowed=False,final_runtime_import_allowed=False,requires_regenerated_srt_equality=True,requires_owned_GS_copy_rebinding=True,requires_explicit_geometry_scalar_equality=True,native_consumer_required=True,requires_current_profile_match=True),source_metadata=dict(sha256=metadata_ref['sha256'],opaque_Info_bytes_forwarded=0,original_span_bytes_forwarded=0))
 data=json.dumps(packet,sort_keys=True,separators=(',',':')).encode()+b'\n';need(len(data)<=256*1024,'GS packet budget');new_bytes(output,'packet.json',data);return packet

def scan(*,project,caches,layout,catalog,static_index,output,check=lambda:None):
 project=directory(Path(project));output=Path(output).absolute();need(not output.exists()and output.is_relative_to(project/'evidence'),'new GS private evidence directory');output.mkdir(mode=0o700);directory(output);need(0<len(caches)<=128,'bounded explicit private cache list');table=lookup_table(list(rows(Path(static_index))));seen=set();counts=collections.Counter();packets=[];ranges=collections.defaultdict(set);started=time.monotonic()
 for cache in caches:
  cache=directory(Path(cache));need(cache.is_relative_to(project/'state')and cache.name.startswith('CUSA03173-metal-'),'GS only private QA caches');check();profile,profile_ref=checked_bytes(cache/'profile.bin');semantic_profile(profile,layout)
  for path in sorted(cache.glob('*.meta')):
   check();counts['all_metadata']+=1;need(counts['all_metadata']<=MAX_RECORDS,'metadata scan bound');raw,ref=checked_bytes(path);meta,sections=metadata_sections(raw,layout)
   if meta['stage']!='geometry':continue
   counts['physical_GS_metadata']+=1;counts['physical_GS_'+meta['program_hash']]+=1;spv,spvref=checked_bytes(cache/f'0x{int(meta["program_hash"],16):016x}_{meta["permutation"]}.spv');key=(profile_ref['sha256'],ref['sha256'],spvref['sha256'])
   if key in seen:counts['exact_duplicate_GS_inputs']+=1;continue
   seen.add(key);packet=freeze_geometry(metadata_ref=ref,spirv_ref=spvref,profile_ref=profile_ref,layout=layout,game_root=catalog['game_root'],catalog=catalog,table=table,output=output/f'gs-{len(packets):03d}-{meta["program_hash"]}')
   for k,v in packet['geometry_runtime'].items():ranges[k].add(tuple(v)if isinstance(v,list)else v)
   packets.append(dict(packet=str(output/f'gs-{len(packets):03d}-{meta["program_hash"]}'/'packet.json'),program_hash=meta['program_hash'],copy_words=packet['gs_copy']['words'],original_metadata_sha256=ref['sha256'],sanitized_spec_sha256=packet['spec']['sha256'],native_compile_verified=False))
 result=dict(schema=1,status='frozen-owned-GS-inputs',counts=dict(counts),owned_GS_packets=len(packets),packets=packets,geometry_ranges={k:sorted(v)for k,v in ranges.items()},seconds=time.monotonic()-started,static_index_sha256=file_sha(static_index),actual_native_module_compilation_verified=False,complete_PSO=False,existing_worker_GS_gate_changed=False,GPU_started=False,game_started=False,formal_data_modified=False,source_cache_modified=False,original_pointer_followed=False,retired_span_forwarded=False)
 (output/'manifest.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');return result

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','cache-list','layout','layout-source','catalog','static-index','output'):ap.add_argument('--'+key,type=Path,required=True)
 a=ap.parse_args();layout=load_canonical_layout(a.layout,a.layout_source,file_sha(a.layout));catalog=json.loads(a.catalog.read_text());caches=json.loads(a.cache_list.read_text())
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe GS CPU entry');policy=MemoryPolicy(before);start=time.monotonic()
 def check():
  reason=policy.observe(time.monotonic()-start,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  result=scan(project=a.project,caches=caches,layout=layout,catalog=catalog,static_index=a.static_index,output=a.output,check=check);result.update(before=before,after=observer.system());(a.output/'manifest.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=result['status'],counts=result['counts'],owned_GS_packets=result['owned_GS_packets'],manifest=str(a.output/'manifest.json'))))
if __name__=='__main__':main()
