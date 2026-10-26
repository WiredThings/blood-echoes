"""CPU projection rejection controls, including one actual ambiguous source row.

The ambiguous fixture is explicitly a rejected hypothesis, never a production
generator output. It preserves real code/spec/key and constructs no V# descriptor.
"""
from pathlib import Path
import argparse,collections,copy,json,struct,time
from complete_pipeline_inputs import directory,file_sha
from game_module_inputs import need,source_code
from game_pso_owned_audit import checked_bytes
from game_pso_progress_identity import layout_abi_identity
from prepare_game_psos import new_bytes
from fetch_projection_inputs import FIELDS,verify_observed
from static_program_links import lookup_table,rows as static_rows
from run_fetch_projection_contracts import verify_packet
from run_game_module_contracts import run_child,validate_call
from run_game_module_negative_contracts import clone_packet,mutate_blob,setword,setbyte

def clone_projection(base,root):
 p=clone_packet(base,root)
 for ref in[p['source_metadata']['header']]+[k['reference']for k in p['projection_source_keys']]:
  raw,_=checked_bytes(Path(ref['path']),expected=ref['sha256']);path=root/ref['name'];path.write_bytes(raw);ref['path']=str(path)
 return p
def change_spec(p,root,change):
 mutate_blob(p,root,'spec',change);p['source_metadata']['original_spec_sha256']=p['spec']['sha256']
def vertex_specialization_start(p,layout):
 raw=Path(p['spec']['path']).read_bytes();at=layout['Backend::Bindings.size']+layout['RuntimeInfo.size']+8+128;marker=struct.unpack_from('<Q',raw,at)[0];need(marker==layout['Gcn::FetchShaderData.size'],'actual typed fetch marker');at+=8+4+2;n=struct.unpack_from('<Q',raw,at)[0];need(n<=32,'actual fetch bound');at+=8+n*layout['Gcn::VertexAttribute.size'];need(struct.unpack_from('<Q',raw,at)[0]>0,'observed vs attrs');return at+8

def cases(vs,ls,layout):
 out=[]
 def packet(name,error,fn,base=vs):out.append((name,error,fn,base))
 def attr(name,error,fn):packet(name,error,lambda p,r:fn(p['fetch_projection']['attributes'][0]))
 packet('missing-entry','complete explicit projection entries',lambda p,r:p['fetch_projection']['attributes'].pop())
 for name in('inst_offset','data_format','num_format'):
  attr('nine-fields-'+name,'all nine projected fetch fields',lambda a,name=name:a['fields'].update({name:a['fields'][name]+1}))
 packet('fetch-code-size','full original fetch scalars',lambda p,r:p['fetch_projection']['fetch'].update(code_size=p['fetch_projection']['fetch']['code_size']+4))
 attr('wrong-class','exact observed projection semantics',lambda a:a.update(number_class=2))
 attr('wrong-conversion','unique original fetch conversion proof',lambda a:a.update(read_number_conversion=1))
 attr('wrong-effective-swizzle','exact observed projection semantics',lambda a:a['effective_dst_select'].__setitem__(0,0 if a['effective_dst_select'][0]else 1))
 attr('wrong-divisor','exact observed projection semantics',lambda a:a.update(divisor=a['divisor']+1))
 attr('nonnull-false','nonnull projection needs nondefault',lambda a:a.update(nonnull=False))
 attr('nonnull-not-bool','JSON boolean expected',lambda a:a.update(nonnull=2))
 packet('raw-descriptor-marker','explicit projection policy',lambda p,r:p['fetch_projection'].update(raw_Vsharp_reconstructed=True))
 packet('conversion-proof-policy-disabled','required explicit fetch projection policy',lambda p,r:p['constraints'].update(requires_native_enum_conversion_uniqueness=False))
 packet('raw-synthesis-allowed','required explicit fetch projection policy',lambda p,r:p['constraints'].update(raw_Vsharp_synthesis_allowed=True))
 packet('metadata-header-permutation','original vertex permutation identity',lambda p,r:mutate_blob(p['source_metadata'],r,'header',lambda b:struct.pack_into('<Q',b,8,struct.unpack_from('<Q',b,8)[0]^1)))
 stage_offset=8+layout['GraphicsPipelineKey.stage_hashes']+8*3
 packet('key-vertex-permutation','exact key logical Vertex permutation',lambda p,r:mutate_blob(p['projection_source_keys'][0],r,'reference',lambda b:struct.pack_into('<Q',b,stage_offset,struct.unpack_from('<Q',b,stage_offset)[0]^1)))
 packet('key-format-JSON','raw key format proof',lambda p,r:p['projection_source_keys'][0]['vertex_formats'].__setitem__(0,41))
 attr('missing-format-proof','all same-cache formats must be proved',lambda a:a['proof'].update(backend_formats=[]))
 packet('no-key-proofs','bounded projection key proofs',lambda p,r:p.update(projection_source_keys=[]))
 packet('default-null-specialization','nonnull projection needs nondefault',lambda p,r:change_spec(p,r,lambda b:b.__setitem__(slice(vertex_specialization_start(p,layout),vertex_specialization_start(p,layout)+layout['VsAttribSpecialization.size']),bytes(layout['VsAttribSpecialization.size']))))
 packet('wrong-packet-kind','offline module fetch requires',lambda p,r:p.update(kind='owned-gcn-module-input'))
 hw=layout['Backend::Bindings.size']+layout['RuntimeInfo.hw'];sw=layout['Backend::Bindings.size']+layout['RuntimeInfo.sw_stage']
 packet('Local-stride0','projection Local stride',lambda p,r:change_spec(p,r,lambda b:setword(b,hw,0)),ls)
 packet('Local-tessellation-control','runtime plain stage discriminators',lambda p,r:change_spec(p,r,lambda b:setword(b,sw,1)),ls)
 packet('VS-motion-session','module motion VS capture unavailable',lambda p,r:change_spec(p,r,lambda b:setbyte(b,hw+layout['HwVertexRuntimeInfo.motion_vectors'],1)))
 return out

def ambiguous_fixture(row,*,layout,catalog,table,output):
 # Preserve the actual ambiguous metadata, not a doctored valid shader/spec.
 metadata,meta,sections,profile,spirv,keys,meta_ref=verify_observed(row,layout);identity=row['static_identity'];candidates=table.get((identity['program_hash'],identity['hardware_stage']),[]);need(len(candidates)==1 and candidates[0]['identity']==identity,'actual ambiguous static identity');code,source=source_code(catalog['game_root'],catalog,identity,candidates[0]['variants'][0]['evidence_refs'][0]);output.mkdir(mode=0o700)
 attributes=[]
 for i,a in enumerate(row['fetch']['attributes']):
  v=a['vertex_specialization'];attributes.append(dict(attribute_index=i,fields={k:a['fields'][k]for k in FIELDS},nonnull=True,number_class=v['num_class'],read_number_conversion=0,effective_dst_select=list(struct.pack('<I',v['dst_select'])),divisor=v['divisor'],proof=dict(backend_formats=sorted({k['vertex_formats'][i]for k in row['pipeline_key_vertex_inputs']}),unique_conversion_required=True,raw_descriptor_materialized=False)))
 for i,key in enumerate(keys):
  raw,_=checked_bytes(Path(key['reference']['path']),expected=key['reference']['sha256']);key['reference']=new_bytes(output,f'key-{i}.bin',raw)
 spec=sections['specialization'];flat=sections['flattened_table_audit_only'];walk=sections['walker_forbidden'];walker=metadata[walk['offset']:walk['offset']+walk['bytes']]
 p=dict(packet_schema=1,kind='owned-gcn-fetch-projection-module-input',candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,program_hash=identity['program_hash'],hardware_stage=identity['hardware_stage'],logical_stage='vertex',profile=new_bytes(output,'profile.bin',profile),code=new_bytes(output,'code.bin',code),spec=new_bytes(output,'spec.bin',metadata[spec['offset']:spec['offset']+spec['bytes']]),flat=new_bytes(output,'flat.bin',metadata[flat['offset']+8:flat['offset']+flat['bytes']]),user_data_words=16,flat_dwords=meta['flattened_words'],walker=new_bytes(output,'walker.bin',walker)if walker else None,walker_bytes=len(walker),expected_spirv=new_bytes(output,'expected-spirv.bin',spirv),program_base=dict(known=False,value=None,required_gate='reject-offset-by-program-base'),source_provenance=source,fetch_projection=dict(schema=1,raw_Vsharp_reconstructed=False,fetch={k:row['fetch'][k]for k in('code_size','vertex_sgpr_offset','instance_sgpr_offset')},attributes=attributes),projection_source_keys=keys,source_metadata=dict(sha256=meta_ref['sha256'],original_spec_sha256=spec['sha256'],header=new_bytes(output,'metadata-header.bin',metadata[:24]),permutation=meta['permutation'],permutation_hash=meta['permutation_hash']),layout=dict(bindings_size=layout['Backend::Bindings.size'],runtime_size=layout['RuntimeInfo.size'],profile_size=layout['Profile.size'],serialization_abi_sha256=layout_abi_identity(layout)),constraints=dict(old_walker_execution_allowed=False,guest_dereference_allowed=False,final_runtime_import_allowed=False,requires_regenerated_srt_equality=True,requires_current_profile_match=True,requires_exact_fetch_projection=True,requires_native_enum_conversion_uniqueness=True,raw_Vsharp_synthesis_allowed=False),test_expectation='must-reject-actual-conversion-ambiguity')
 (output/'packet.json').write_text(json.dumps(p));return p

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 for key in('project','build','batch','catalog','sidecar','static-index','layout','output'):ap.add_argument('--'+key,type=Path,required=True)
 a=ap.parse_args();P=directory(a.project);need(not a.output.exists()and a.output.is_relative_to(P/'evidence'),'fresh projection negative evidence');a.output.mkdir(mode=0o700);directory(a.output);b=json.loads(a.build.read_text());need(b.get('explicit_projection_callsite_TUs_compiled')and not b.get('diagnostic_only'),'current ordinary projection TU consumer');binary=Path(b['binary']);checked_bytes(binary,limit=64*1024*1024,expected=b['binary_sha256']);checked_bytes(Path(b['pinned_library']),limit=256*1024*1024,expected=b['pinned_library_sha256']);catalog=json.loads(a.catalog.read_text());layout=json.loads(a.layout.read_text());rows=[json.loads(line)for line in a.sidecar.open()];batch=json.loads(a.batch.read_text());vs=verify_packet(next(x['packet']for x in batch['packets']if x['hardware_stage']=='vertex'),catalog=catalog,rows=rows,layout=layout);ls=verify_packet(next(x['packet']for x in batch['packets']if x['hardware_stage']=='local'),catalog=catalog,rows=rows,layout=layout)
 from qa_build_guard import require_no_games
 from native_observer import NativeObserver
 from qa_memory_policy import MemoryPolicy
 require_no_games();observer=NativeObserver();before=observer.system();need(before['pressure']!=4 and not before['locked'],'safe CPU negative entry');policy=MemoryPolicy(before);started=time.monotonic();state=P/'state'/('fetch-projection-negatives-'+str(time.time_ns()));state.mkdir(mode=0o700);results=[]
 def check():
  reason=policy.observe(time.monotonic()-started,observer.system())
  if reason:raise RuntimeError(reason)
 try:
  for name,error,mutate,base in cases(vs,ls,layout):
   check();case=a.output/name;case.mkdir();child=state/name;child.mkdir();p=clone_projection(base,case/'input');mutate(p,case/'input');path=case/'input/packet.json';path.write_text(json.dumps(p));code,result,seconds=run_child(binary,path,case/'output',case,child,check);validate_call(code,result,case/'output',p,True);need(error in result['error'],'intended rejection gate: '+name);results.append(dict(name=name,error=result['error'],exit=code,seconds=seconds));print(name,'rejected',result['error'],flush=True)
  row=next(r for r in rows if r['static_identity']['program_hash']=='000000000073ecc9'and any(41 in k['vertex_formats']for k in r['pipeline_key_vertex_inputs']));case=a.output/'actual-Uint-Uscaled-ambiguity';case.mkdir();child=state/case.name;child.mkdir();p=ambiguous_fixture(row,layout=layout,catalog=catalog,table=lookup_table(list(static_rows(a.static_index))),output=case/'input');path=case/'input/packet.json';code,result,seconds=run_child(binary,path,case/'output',case,child,check);validate_call(code,result,case/'output',p,True);need('ambiguous original fetch number conversion'in result['error'],'actual enum ambiguity must reject');results.append(dict(name=case.name,error=result['error'],exit=code,seconds=seconds,actual_metadata_sha256=row['observed']['metadata']['sha256']));print(case.name,'rejected',result['error'],flush=True)
  report=dict(schema=1,status='passed',count=len(results),cases=results,build_sha256=file_sha(a.build),before=before,after=observer.system(),GPU_started=False,game_started=False,raw_Vsharp_materialized=False,formal_data_modified=False,runtime_cache_import_allowed=False);(a.output/'result.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
 finally:observer.close()
 print(json.dumps(dict(status=report['status'],count=report['count'],result=str(a.output/'result.json'))))
if __name__=='__main__':main()
