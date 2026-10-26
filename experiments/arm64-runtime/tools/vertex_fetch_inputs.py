"""Provenance-only vertex inputs from existing metadata, never a guest read.

Keep three namespaces separate: FLVER semantic IDs, GNM raw input semantics,
and the fetch parser's sequential semantic index. There is no guessed join.
"""
from pathlib import Path
import argparse,collections,hashlib,json,re,struct,time
from static_program_links import canonical,digest,lookup_table,need,read_json,regular,rows
from game_pso_owned_audit import checked_bytes,metadata_sections,load_canonical_layout
from decode_shader_cache import decode_key

FIELDS=('semantic','dest_vgpr','num_elements','sgpr_base','dword_offset',
        'instance_data','inst_offset','data_format','num_format')
MAX_METADATA=100000

def fetch_layout(header,layout):
 raw,ref=checked_bytes(Path(header),limit=65536)
 declared=tuple(re.findall(r'^\s*u8\s+(\w+)(?:\{\})?\s*;',raw.decode(),re.M))
 need(declared==FIELDS,'fetch declaration order changed')
 # Nine u8 members and sizeof==9 leave no unaccounted member/interstitial padding.
 need(layout['Gcn::VertexAttribute.size']==len(FIELDS) and layout['pointer_bytes']==8
      and layout['little_endian'],'fetch canonical byte layout')
 return dict(header=ref,member_type='u8',size=len(FIELDS),offsets={f:i for i,f in enumerate(FIELDS)},
             authority='checked C++ declaration order plus compiler-derived sizeof')

def decode_attribute(attribute,index):
 raw=bytes.fromhex(attribute['raw_hex']);need(len(raw)==9,'fetch attribute byte extent')
 value=dict(zip(FIELDS,raw));need(value['semantic']==index,'fetch semantic must be parser sequence')
 need(1<=value['num_elements']<=4 and value['sgpr_base']<=14 and value['sgpr_base']%2==0
      and value['instance_data']<=3,'fetch attribute domain')
 return dict(value,raw_hex=raw.hex(),semantic_namespace='fetch-parser-sequential-index')

def flat_origins(walker,flat):
 """Symbolic addresses only: never execute pointer loads or follow captured UD.

 This deliberately admits only the constant-address subset actually present in
 the audited fetch records. Unsupported bytecode stays an explicit gap. No final
 flat value is used to guess an earlier dynamic offset or overwritten destination.
 """
 need(isinstance(walker,bytes) and len(walker)<=256*1024 and len(walker)%12==0,'portable walker extent')
 need(16<=len(flat)<=16384 and all(type(x)is int and 0<=x<2**32 for x in flat),'flat uint32 extent')
 ops=list(struct.iter_unpack('<3I',walker));need(len(ops)<=21845,'walker operation budget')
 if not ops:return dict(status='empty-walker',origins={},roots=[],op_counts={})
 counts=collections.Counter(op for op,_,_ in ops)
 if set(counts)-{0,1,3,4}:return dict(status='dynamic-or-unsupported-walker',origins={},roots=[],op_counts=dict(counts))
 path=();stack=[];written=set();origins=collections.defaultdict(list);roots=set();ended=False
 for i,(op,a,b)in enumerate(ops):
  need(not ended,'walker data after End')
  if op==0:
   need(not a and not b and not stack and i==len(ops)-1,'walker End/stack');ended=True
  elif op==1:
   need(not b and len(stack)<64,'pointer stack or operand')
   if not path:need(a<=14,'UD pointer must fit 16 words');roots.add(a)
   stack.append(path);path=path+(a,)
  elif op==3:
   need(not a and not b and bool(stack),'pointer Pop underflow');path=stack.pop()
  else:
   need(bool(path) and 16<=b<len(flat),'copied word must be guest-origin and in flat extent')
   need(b not in written,'overwritten flat destination is not provenance-safe');written.add(b)
   origins[path,a].append(dict(flat_word=b,value=flat[b]))
 need(ended,'walker missing End')
 return dict(status='constant-origin-map',origins=dict(origins),roots=sorted(roots),op_counts=dict(counts))

def descriptor_provenance(attribute,mapping):
 target=dict(pointer_index=attribute['sgpr_base'],dword_offset=attribute['dword_offset'],num_dwords=4)
 result=dict(target=target,guest_pointer_followed=False,native_consumer_authorized=False)
 if mapping['status']!='constant-origin-map':return dict(result,status=mapping['status'],missing_words=list(range(4)))
 words=[];proof=[];missing=[]
 for i in range(4):
  refs=mapping['origins'].get(((attribute['sgpr_base'],),attribute['dword_offset']+i),[])
  if not refs:missing.append(i);continue
  values={r['value']for r in refs}
  if len(values)!=1:return dict(result,status='conflicting-captured-word',word=i)
  words.append(next(iter(values)));proof.append(dict(descriptor_word=i,flat_words=[r['flat_word']for r in refs]))
 if missing:return dict(result,status='fetch-Vsharp-not-captured',missing_words=missing)
 return dict(result,status='exact-flat-descriptor-provenance',descriptor_words=words,word_proof=proof)

def key_links(meta,keys):
 result=[]
 for k in keys:
  if k['kind']!='graphics'or k['stages'].get('vertex')!=meta['permutation_hash']:continue
  need(meta['logical_stage']=='vertex','key vertex stage mismatch')
  result.append(dict(name=k['name'],raw_key_sha256=k['raw_key_sha256'],
   vertex_formats=k['vertex_formats'],vertex_attributes=k['vertex_attributes'],
   authority='exact same-cache serialized vertex-stage permutation; not program hash',
   backend_formats_not_raw_Vsharp=True,source_reference=k.get('source_reference')))
 return result

def asset_layouts(schemas):
 """Deduplicate only exact FLVER byte layouts, retaining asset-schema references."""
 layouts={}
 for row in rows(schemas):
  need(row['complete_pso']is False,'asset schema must not claim PSO')
  streams=row['asset_state']['vertex_streams'];encoded=canonical(streams);key=hashlib.sha256(encoded.encode()).hexdigest()
  if key not in layouts:layouts[key]=dict(schema=1,id=key,streams=streams,asset_schema_ids=[],
   source_kind='FLVER2 mesh vertex-buffer layout metadata',backend_vertex_formats_known=False,
   fetch_shader_link_known=False,semantic_namespace='FLVER-layout-member-semantic-enum',complete_pso=False)
  layouts[key]['asset_schema_ids'].append(row['id'])
 need(len(layouts)<=65536,'asset layout count')
 return sorted(layouts.values(),key=lambda r:r['id'])

def analyze(*,index,decoded,layout_path,source,asset_schemas,output):
 started=time.monotonic();output=Path(output);need(output.is_absolute()and not output.exists(),'new absolute provenance output')
 source=Path(source).resolve(strict=True);layout_path=regular(layout_path);layout_raw,layout_ref=checked_bytes(layout_path,limit=4*1024*1024)
 layout=load_canonical_layout(layout_path,source,layout_ref['sha256'])
 declaration=fetch_layout(source/'shader_recompiler/frontend/fetch_shader.h',layout)
 records=list(rows(index));table=lookup_table(records);counts=collections.Counter();statuses=collections.Counter();roots=collections.Counter();fetch_roots=collections.Counter();fetch_identities=set();sources={str(regular(index)):digest(index),str(regular(asset_schemas)):digest(asset_schemas),str(layout_path):layout_ref['sha256']}
 output.mkdir(parents=True);output_bytes=0
 def emit(f,row):
  nonlocal output_bytes
  encoded=canonical(row)+'\n';output_bytes+=len(encoded.encode());need(output_bytes<=128*1024*1024,'provenance output budget');f.write(encoded)
 with(output/'static-vertex-program-inputs.jsonl').open('x')as stream:
  for record in records:
   if not any(v['facts'].get('input_semantics') for v in record['variants']):continue
   counts['static_programs_with_input_semantics']+=1
   emit(stream,dict(schema=1,identity=record['identity'],variants=[dict(variant_id=v['id'],
    input_semantics_raw_u8x4=v['facts'].get('input_semantics',[]),input_usage=v['facts']['input_usage'],
    source_refs=v['evidence_refs'])for v in record['variants']],
    input_semantics_fields_interpreted=False,fetch_shader_generation_reproduced=False,
    backend_vertex_layout_known=False,complete_pso=False))
 with(output/'asset-vertex-layouts.jsonl').open('x')as stream:
  layouts=asset_layouts(asset_schemas);counts['distinct_asset_vertex_layouts']=len(layouts)
  for row in layouts:emit(stream,row)
 with(output/'observed-vertex-fetch-inputs.jsonl').open('x')as stream:
  for p in sorted(Path(decoded).glob('*/decoded.json')):
   p=regular(p,32*1024*1024);sources[str(p)]=digest(p);document=read_json(p);inv_path=regular(p.parent/'inventory.json',32*1024*1024);sources[str(inv_path)]=digest(inv_path);inv=read_json(inv_path);cache=Path(inv['cache']);pairs={pair['meta']['name']:pair for pair in inv['paired_specializations']}
   need(len(pairs)==len(inv['paired_specializations']),'cache metadata name ambiguity')
   needed={m['permutation_hash']for m in document.get('metadata',[])if m['fetch']is not None}
   key_refs={k['name']:k for k in inv['pipeline_keys']};verified_keys=[]
   for key in document['pipeline_keys']:
    if key['kind']!='graphics'or key['stages'].get('vertex')not in needed:continue
    original=key_refs[key['name']];need(Path(original['name']).name==original['name'],'cache key basename')
    key_raw,key_ref=checked_bytes(cache/original['name'],expected=original['sha256']);decoded_key=decode_key(key_raw,layout)
    need(all(key[k]==v for k,v in decoded_key.items()),'decoded key no longer matches owned bytes')
    verified_keys.append(dict(key,source_reference=key_ref))
   for m in document.get('metadata',[]):
    counts['metadata_records']+=1;need(counts['metadata_records']<=MAX_METADATA,'metadata count budget')
    if m['fetch']is None:continue
    counts['fetch_metadata_records']+=1;pair=pairs[m['name']];need(Path(m['name']).name==m['name'],'cache metadata basename');raw,ref=checked_bytes(cache/m['name'],expected=pair['meta']['sha256']);canonical_meta,sections=metadata_sections(raw,layout)
    need({k:v for k,v in m.items()if k!='name'}==canonical_meta,'decoded metadata no longer matches owned bytes')
    static=table.get((m['program_hash'],m['stage']),[]);need(len(static)==1,'unambiguous exact static stage required');fetch_identities.add(canonical(static[0]['identity']));counts['fetch_hardware_stage.'+m['stage']]+=1
    flat=sections['flattened_table_audit_only'];values=list(struct.unpack('<'+'I'*m['flattened_words'],raw[flat['offset']+8:flat['offset']+flat['bytes']]))
    walker=sections['walker_forbidden'];mapping=flat_origins(raw[walker['offset']:walker['offset']+walker['bytes']],values);statuses['walker.'+mapping['status']]+=1;roots.update(mapping['roots'])
    attributes=[];need(len(m['fetch']['attributes'])<=32,'CompileSnapshot fetch budget')
    need(len(m['fetch']['attributes'])==len(m['vertex_attributes']),'fetch and vertex specialization cardinality')
    for i,a in enumerate(m['fetch']['attributes']):
     a=decode_attribute(a,i);proof=descriptor_provenance(a,mapping);statuses[proof['status']]+=1;counts['fetch_attributes']+=1;fetch_roots[a['sgpr_base']]+=1
     attributes.append(dict(fields=a,vertex_specialization=m['vertex_attributes'][i],
      specialization_authority='same metadata, StageSpecialization ForEachSharp attribute order',descriptor=proof))
    links=key_links(m,verified_keys);counts['exact_key_vertex_links']+=len(links)
    emit(stream,dict(schema=1,static_identity=static[0]['identity'],observed=dict(cache=str(cache),metadata=ref,
     permutation=m['permutation'],permutation_hash=m['permutation_hash'],serialized_spec_sha256=m['serialized_spec_sha256']),
     fetch=dict(code_size=m['fetch']['code_size'],vertex_sgpr_offset=m['fetch']['vertex_sgpr_offset'],instance_sgpr_offset=m['fetch']['instance_sgpr_offset'],attributes=attributes),
     step_rates=m.get('context',{}).get('step_rates'),pipeline_key_vertex_inputs=links,
     flat_provenance=dict(status=mapping['status'],pointer_roots=mapping['roots'],op_counts=mapping['op_counts'],
      captured_word_origins=len(mapping['origins']),walker_sha256=m['walker_sha256']),
     module_fetch_gate_unchanged=True,asset_layout_join_authoritative=False,complete_pso=False))
 need(all(digest(path)==sha for path,sha in sources.items()),'provenance inputs changed')
 counts['unique_static_programs_with_observed_fetch']=len(fetch_identities)
 summary=dict(schema=1,counts=dict(counts),statuses=dict(statuses),walker_root_occurrences=dict(roots),fetch_attribute_pointer_roots=dict(fetch_roots),
  source_files=sources,fetch_layout_proof=declaration,outputs={p.name:digest(p)for p in output.glob('*.jsonl')},
  seconds=time.monotonic()-started,output_bytes=output_bytes,game_started=False,GPU_started=False,
  guest_pointers_followed=False,walker_executed=False,formal_data_modified=False,
  qualification='Metadata and exact constant-address walker origin audit only; no FLVER-to-fetch semantic inference, no raw descriptor synthesis from Vulkan format, no pipeline/module admission changes.')
 (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');return summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--index',required=True,type=Path);ap.add_argument('--decoded',required=True,type=Path);ap.add_argument('--layout',required=True,type=Path);ap.add_argument('--source',required=True,type=Path);ap.add_argument('--asset-schemas',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
 result=analyze(index=a.index,decoded=a.decoded,layout_path=a.layout,source=a.source,asset_schemas=a.asset_schemas,output=a.output);print(json.dumps({k:v for k,v in result.items()if k not in('source_files','fetch_layout_proof')},indent=2))
if __name__=='__main__':main()
