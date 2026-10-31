"""Bounded, provenance-preserving joins of verified static shader header metadata.

No game execution, native deserialization, byte extraction, or PSO fabrication.
File-stage values and BinaryInfo types are different from runtime HwStage values.
"""
from __future__ import annotations
import argparse,collections,hashlib,json,os,re,stat,time
from pathlib import Path
import gnm_shader_metadata as gnm

MAX_ROW=32*1024*1024
MAX_JSON=64*1024*1024
MAX_HEADERS=100000
MAX_VARIANTS=65536
MAX_REFERENCES=100000
HEX64=re.compile(r'[0-9a-f]{64}\Z')
HASH=re.compile(r'[0-9a-f]{16}\Z')
FACT_FIELDS=('version','file_stage','binary_type','header_bytes','shader_payload_bytes','registers','runtime_property_defaults',
 'usage_count','uses_srt','embedded_constant_bytes','scratch_dwords_per_thread',
 'input_usage','input_semantics','export_semantics','input_semantic_words',
 'uninterpreted_stage_words','unparsed_tail_bytes','aux','target_gpu_modes',
 'embedded_gs_copy','embedded_copy_header_bytes')

def need(value,message):
 if not value:raise ValueError(message)

def digest(path):
 with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()

def regular(path,limit=None):
 p=Path(path).absolute();s=p.lstat()
 need(p==p.resolve(strict=True)and stat.S_ISREG(s.st_mode),'canonical regular metadata file required')
 if limit is not None:need(s.st_size<=limit,'metadata file byte budget')
 return p

def read_json(path):return json.loads(regular(path,MAX_JSON).read_text())
def rows(path):
 with regular(path).open()as f:
  while True:
   line=f.readline(MAX_ROW+1)
   if not line:return
   need(len(line)<=MAX_ROW,'metadata row byte budget');yield json.loads(line)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def runtime_stages(path):
 text=regular(path,1024*1024).read_text();match=re.search(r'enum class HwStage\s*:\s*u32\s*\{([^}]+)\}',text)
 need(match is not None,'runtime HwStage declaration required')
 body=re.sub(r'//[^\n]*|/\*.*?\*/','',match[1],flags=re.S)
 names=[v.strip()for v in body.split(',')if v.strip()]
 need(all(re.fullmatch('[A-Za-z]+',v)for v in names),'explicit HwStage values need reader update')
 names=[v.casefold()for v in names]
 # This also binds the existing serialized metadata decoder to the native enum.
 from decode_shader_cache import HW
 need(tuple(names)==HW,'native HwStage and canonical metadata decoder disagree')
 need(set(names)==set(gnm.STAGES.values()),'GNM stage-name mapping and runtime enum disagree')
 return {name:i for i,name in enumerate(names)}

def catalog_code_spans(catalog):
 spans=collections.defaultdict(set)
 for package in catalog['packages']:
  if package.get('debug'):continue
  if package['kind']=='shader':records=(r for e in package.get('entries',[])for r in e.get('binary_info',[]))
  elif package['kind']=='embedded':records=iter(package.get('binary_info',[]))
  else:continue
  for r in records:
   if not r.get('code_prefix_verified'):continue
   key=(r['program_hash'],r['code_sha256'],r['binary_type_raw'])
   need(HASH.fullmatch(key[0])and HEX64.fullmatch(key[1]),'catalog code identity')
   size=r['code_bytes'];need(type(size)is int and 8<=size<=1024*1024 and size%4==0,'code span budget/alignment')
   spans[key].add(size)
   need(len(spans)<=MAX_VARIANTS,'catalog code identity budget')
 return spans

def header_facts(header,stages,spans):
 file_stage=header.get('file_stage');binary=header.get('binary_type');stage=header.get('stage')
 need(type(file_stage)is int and file_stage in gnm.STAGES and stage==gnm.STAGES[file_stage],'file-stage/name mismatch')
 need(type(binary)is int and binary in gnm.BINARY_TYPES[file_stage],'file-stage/BinaryInfo mismatch')
 need(stage in stages,'unknown native hardware stage')
 ph,code=header.get('program_hash'),header.get('code_sha256')
 need(isinstance(ph,str)and HASH.fullmatch(ph)and isinstance(code,str)and HEX64.fullmatch(code),'header code identity')
 sizes=spans.get((ph,code,binary),set());need(len(sizes)==1,'catalog/header code span is missing or ambiguous')
 regs=header.get('registers');need(isinstance(regs,dict)and set(regs)==set(gnm.REGISTERS[file_stage]),'complete raw register set required')
 need(all(type(v)is int and 0<=v<=0xffffffff for v in regs.values()),'raw register uint32 shape')
 need(header.get('runtime_property_defaults')==gnm.properties(regs,stage=='compute'),'file-default property decode changed')
 for field in ('usage_count','embedded_constant_bytes','scratch_dwords_per_thread','code_offset'):
  need(type(header.get(field))is int and header[field]>=0,'required static scalar '+field)
 need(type(header.get('uses_srt'))is bool,'uses_srt bool required')
 need(header['version']==[7,1],'static header version')
 need(isinstance(header.get('input_usage'),list)and len(header['input_usage'])==header['usage_count'],'input usage count mismatch')
 need(header['usage_count']<=255,'input usage budget')
 def table(field,width,maximum):
  value=header.get(field,[]);need(isinstance(value,list)and len(value)<=255,'semantic table budget '+field)
  need(all(isinstance(row,list)and len(row)==width and all(type(v)is int and 0<=v<=maximum for v in row)for row in value),'semantic table shape '+field)
 table('input_usage',4,255);table('input_semantics',4,255);table('export_semantics',2,255)
 value=header.get('input_semantic_words',[]);need(isinstance(value,list)and len(value)<=255 and all(type(v)is int and 0<=v<=65535 for v in value),'fragment semantic words')
 value=header.get('uninterpreted_stage_words',[]);need(isinstance(value,list)and len(value)<=32 and all(type(v)is int and 0<=v<=0xffffffff for v in value),'uninterpreted word budget')
 need(header['code_offset']<=128*1024*1024,'code metadata offset budget')
 if header.get('embedded_gs_copy'):
  need((file_stage,binary,stage)==(1,6,'vertex')and type(header.get('outer_header_offset'))is int,'embedded GS copy identity')
 allowed=set(FACT_FIELDS)|{'stage','header_offset','common_offset','code_offset','code_sha256','program_hash','outer_header_offset'}
 need(set(header)<=allowed,'unmodeled static header fields; explicit reader update required')
 need(type(header.get('header_bytes'))is int and 8<=header['header_bytes']<=gnm.PREFIX_BUDGET,'header extent budget')
 need(type(header.get('shader_payload_bytes'))is int and next(iter(sizes))<=header['shader_payload_bytes']<=128*1024*1024,'shader payload extent budget')
 facts={k:header[k]for k in FACT_FIELDS if k in header}
 facts['runtime_property_origin']='verified-file-defaults; fetch/draw may modify'
 facts['relocation_registers']=['program_low','program_high']
 facts['uninterpreted_fields']=[k for k in ('uninterpreted_stage_words','aux','target_gpu_modes','unparsed_tail_bytes')if k in header]
 return (ph,stage,code),facts,next(iter(sizes))

def collect(headers,catalog,runtime_header):
 stages=runtime_stages(runtime_header);spans=catalog_code_spans(catalog)
 programs={};variant_count=reference_count=estimated_bytes=0;entry_stages=collections.defaultdict(set)
 for row_number,row in enumerate(rows(headers),1):
  need(not row.get('rejected')and not row.get('unassociated_programs'),'only complete verified header rows admitted')
  package=row['package'];entry=row.get('entry');need(isinstance(package,str)and(entry is None or isinstance(entry,str)),'header provenance shape')
  source_sha=row.get('entry_sha256')if entry is not None else row.get('elf_sha256')
  need(isinstance(source_sha,str)and HEX64.fullmatch(source_sha),'header source digest required')
  for header in row['headers']:
   reference_count+=1;need(reference_count<=MAX_REFERENCES,'header reference budget')
   identity,facts,size=header_facts(header,stages,spans)
   key=hashlib.sha256(canonical(facts).encode()).hexdigest()
   program=programs.setdefault(identity,dict(identity=dict(program_hash=identity[0],hardware_stage=identity[1],hardware_stage_value=stages[identity[1]],code_sha256=identity[2]),variants={},header_count=0))
   need(len(programs)<=MAX_HEADERS,'program index budget')
   if key not in program['variants']:
    variant_count+=1;need(variant_count<=MAX_VARIANTS,'header variant budget')
    estimated_bytes+=len(canonical(facts).encode())*4;need(estimated_bytes<=256*1024*1024,'metadata aggregate estimate budget')
    program['variants'][key]=dict(id=key,facts=facts,code_bytes=size,evidence_refs=[])
   variant=program['variants'][key];need(variant['code_bytes']==size,'same facts/code identity has inconsistent length')
   ref=dict(package=package,entry=entry,source_sha256=source_sha,source_sha256_kind='entry'if entry is not None else'reconstructed-elf',header_row=row_number,header_offset=header.get('header_offset'),common_offset=header.get('common_offset'),code_offset=header['code_offset'],code_bytes=size,offset_domain='entry-bytes'if entry is not None else'reconstructed-elf-bytes',embedded_gs_copy=bool(header.get('embedded_gs_copy')),outer_header_offset=header.get('outer_header_offset'))
   estimated_bytes+=len(canonical(ref).encode())*4;need(estimated_bytes<=256*1024*1024,'metadata aggregate estimate budget')
   variant['evidence_refs'].append(ref);program['header_count']+=1
   entry_stages[package,entry].add(identity)
 records=[]
 for identity,p in sorted(programs.items()):
  records.append(dict(schema=1,**{k:v for k,v in p.items()if k!='variants'},variants=[v for _,v in sorted(p['variants'].items())],complete_pso=False,stage_selection_authority='GNM file-stage plus checked BinaryInfo/code span; not filename extension'))
 by_hash=collections.defaultdict(set);by_stage=collections.defaultdict(set)
 for row in records:
  i=row['identity'];by_hash[i['program_hash']].add(i['code_sha256']);by_stage[i['program_hash'],i['hardware_stage']].add(i['code_sha256'])
 summary=dict(schema=1,metadata_estimated_bytes=estimated_bytes,metadata_estimate_budget_bytes=256*1024*1024,header_references=reference_count,program_identities=len(records),header_variants=variant_count,hashes_with_multiple_codes=sum(len(v)>1 for v in by_hash.values()),program_stage_pairs=len(by_stage),stage_pairs_with_multiple_codes=sum(len(v)>1 for v in by_stage.values()),source_entries=len(entry_stages),native_hardware_stage_values=stages,full_pso_recipes=0,qualification='Exact static code/header identity and default facts; no complete runtime specialization or stage-pass pairing inferred')
 return records,entry_stages,summary

def compile_dependencies(record):
 """Describe missing compiler inputs; this is not a compile recipe or admission.

 Header defaults are evidence, not effective draw/dispatch state. In particular,
 an empty GNM usage table is not an instruction-level resource-read proof.
 Keep each header variant separate rather than creating a synthetic combination.
 """
 stage=record['identity']['hardware_stage']
 need(stage in gnm.STAGES.values(),'dependency hardware stage')
 variants=record['variants'];need(0<len(variants)<=MAX_VARIANTS,'dependency variant budget')
 module=['effective-runtime-info','target-profile-and-compile-options',
         'complete-resource-specialization','binding-start-state']
 if stage in ('vertex','export','local'):
  module+=['effective-logical-stage','fetch-inputs-or-proven-absence']
 elif stage=='geometry':
  module+=['owned-GS-copy-and-runtime-geometry-state']
 elif stage=='hull':
  module+=['effective-tessellation-state']
 elif stage=='fragment':
  # Fragment module generation itself uses color export/input/sample state;
  # those fields cannot all be deferred to final Metal PSO creation.
  module+=['fragment-inputs-and-color-export-state']
 else:
  module+=['effective-workgroup-and-LDS-state']
 pipeline=(['complete-compute-specialization']if stage=='compute'else
           ['complete-graphics-stage-set','complete-graphics-pipeline-state'])
 items=[]
 for variant in variants:
  facts=variant['facts']
  need(isinstance(variant['id'],str)and HEX64.fullmatch(variant['id']),
       'dependency header variant identity')
  usage=facts['input_usage'];need(facts['usage_count']==len(usage),'dependency usage count')
  srt=facts['uses_srt'];need(type(srt)is bool,'dependency SRT flag')
  items.append(dict(header_variant=variant['id'],
      resource_evidence='SRT-declared'if srt else'usage-declared'if usage else
                        'no-header-declarations; instruction-access-not-proven',
      declared_usage_count=len(usage),
      file_defaults_authorize_effective_runtime=False,
      unresolved_static_evidence=[name for name in facts.get('uninterpreted_fields',[])
          if facts.get(name)],
      evidence_authority='exact header variant in static-program-index; not merged'))
 return dict(schema=1,kind='unresolved-compile-inputs',identity=record['identity'],
     module_missing=module,pipeline_missing=pipeline,variants=items,
     static_compilation_ready=False,dynamic_requirement_proven=False,
     capture='normal-runtime-owned-input',inferred=False)

def resolve(records,program_hash,hardware_stage,code_sha256=None):
 """A lookup can disambiguate only from actual hardware stage, never SwStage."""
 values=[r for r in records if r['identity']['program_hash']==program_hash and r['identity']['hardware_stage']==hardware_stage and(code_sha256 is None or r['identity']['code_sha256']==code_sha256)]
 if len(values)==1:return dict(status='unique-static-code',identity=values[0]['identity'],variant_count=len(values[0]['variants']))
 return dict(status='missing-static-code'if not values else'ambiguous-static-code',candidates=[r['identity']for r in values])

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--headers',required=True,type=Path);ap.add_argument('--catalog',required=True,type=Path);ap.add_argument('--runtime-header',type=Path,default=Path(__file__).resolve().parents[1]/'vendor/metal_api/shadps4/shader_recompiler/runtime_info.h');ap.add_argument('--out',required=True,type=Path);a=ap.parse_args()
 need(a.out.is_absolute()and not a.out.exists(),'new absolute metadata output required');inputs={str(regular(p)):digest(p)for p in (a.headers,a.catalog,a.runtime_header)}
 records,entries,summary=collect(a.headers,read_json(a.catalog),a.runtime_header);a.out.mkdir(parents=True)
 with(a.out/'static-program-index.jsonl').open('x')as f:
  for row in records:f.write(canonical(row)+'\n')
 with(a.out/'entry-stage-identities.jsonl').open('x')as f:
  for (package,entry),identities in sorted(entries.items(),key=lambda x:canonical(x[0])):f.write(canonical(dict(schema=1,package=package,entry=entry,identities=[dict(program_hash=h,hardware_stage=s,code_sha256=c)for h,s,c in sorted(identities)],stage_pairing_authority=False))+'\n')
 need(all(digest(path)==value for path,value in inputs.items()),'source metadata changed during index construction')
 summary.update(source_files=inputs,game_started=False,formal_data_modified=False,index_sha256=digest(a.out/'static-program-index.jsonl'),entry_index_sha256=digest(a.out/'entry-stage-identities.jsonl'))
 (a.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items()if k!='source_files'},indent=2))


def lookup_table(records):
 result=collections.defaultdict(list)
 for row in records:result[row['identity']['program_hash'],row['identity']['hardware_stage']].append(row)
 return result


def geometry_copy_provenance(main,copy_context,table):
 """Join a serialized GS copy declaration to its verified embedded static source.

Only metadata is returned. No session pointer is read or passed to a renderer;
this does not authorize the old worker to deserialize unsupported spans.
"""
 need(main['identity']['hardware_stage']=='geometry','GS main hardware stage required')
 words=copy_context.get('words');ph=copy_context.get('hash')
 if type(words)is not int or not 0<words<=262144 or not isinstance(ph,str)or not HASH.fullmatch(ph):return dict(status='invalid-copy-declaration')
 candidates=table.get((ph,'vertex'),[])
 if len(candidates)!=1:return dict(status='missing-or-ambiguous-copy-code')
 copy=candidates[0];pairs=[]
 for main_variant in main['variants']:
  if main_variant['facts']['file_stage']!=3 or main_variant['facts']['binary_type']!=5:continue
  for variant in copy['variants']:
   if variant['code_bytes']!=words*4 or not variant['facts'].get('embedded_gs_copy')or variant['facts']['binary_type']!=6:continue
   for a in main_variant['evidence_refs']:
    for b in variant['evidence_refs']:
     if (a['package'],a['entry'],a['source_sha256'],a['header_offset'])!=(b['package'],b['entry'],b['source_sha256'],b['outer_header_offset']):continue
     pairs.append(dict(main_variant_id=main_variant['id'],copy_variant_id=variant['id'],source=b))
 if not pairs:return dict(status='copy-length-or-container-provenance-mismatch')
 return dict(status='owned-static-copy-source-identified',identity=copy['identity'],words=words,code_bytes=words*4,source_candidates=pairs,serialized_pointer_followed=False,native_deserialization_authorized=False,complete_pso=False,required_consumer_actions=['verify current game/package/source bytes','read owned code and verify code SHA','construct a process-owned copy span at the native compilation boundary','preserve every other runtime/spec/PSO dependency and validation'])

if __name__=='__main__':main()
