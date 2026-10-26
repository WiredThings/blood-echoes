"""Read-only metadata association, including non-dereferencing GS copy provenance."""
from pathlib import Path
import argparse,collections,json
from static_program_links import (canonical,digest,geometry_copy_provenance,lookup_table,
                                 need,read_json,regular,rows)

def audit(index,decoded,out):
 index=regular(index);decoded=Path(decoded).resolve(strict=True);out=Path(out);need(out.is_absolute()and not out.exists(),'new absolute audit directory required')
 source={str(index):digest(index)};records=list(rows(index));table=lookup_table(records);by_hash=collections.defaultdict(list)
 for r in records:by_hash[r['identity']['program_hash']].append(r)
 counts=collections.Counter();targets=collections.Counter();out.mkdir(parents=True)
 with(out/'observed-program-links.jsonl').open('x')as output:
  for p in sorted(decoded.glob('*/decoded.json')):
   p=regular(p,32*1024*1024);source[str(p)]=digest(p);document=read_json(p)
   for m in document.get('metadata',[]):
    counts['metadata_records']+=1;need(counts['metadata_records']<=100000,'metadata record budget')
    candidates=table.get((m['program_hash'],m['stage']),[]);identity=dict(cache=p.parent.name,metadata=m.get('name'),program_hash=m['program_hash'],hardware_stage=m['stage'],logical_stage=m['logical_stage'],serialized_spec_sha256=m['serialized_spec_sha256'])
    if len(candidates)!=1:
     counts['missing_or_ambiguous_static_code']+=1;output.write(canonical(dict(observed=identity,status='missing-or-ambiguous-static-code',candidate_count=len(candidates)))+'\n');continue
    record=candidates[0];counts['unique_static_code']+=1;row=dict(observed=identity,status='unique-static-code',static_identity=record['identity'],variant_ids=[v['id']for v in record['variants']],runtime_state_still_required=True,complete_pso=False)
    copy_context=m.get('context',{}).get('geometry_copy')
    if copy_context and(copy_context.get('serialized_pointer_nonzero')or copy_context.get('words')):
     counts['serialized_geometry_copy_records']+=1;copy=geometry_copy_provenance(record,copy_context,table);counts['geometry_copy.'+copy['status']]+=1;row['geometry_copy_source']=copy
    # Negative check: a collision's different-stage code cannot be admitted as
    # the observed actual hardware stage, even though the program hash matches.
    wrong_codes={r['identity']['code_sha256']for r in by_hash[m['program_hash']]if r['identity']['hardware_stage']!=m['stage']}-{record['identity']['code_sha256']}
    if wrong_codes:
     need(all(v not in {r['identity']['code_sha256']for r in candidates}for v in wrong_codes),'cross-stage collision was admitted');counts['cross_stage_code_negative_checks']+=len(wrong_codes)
    if m['program_hash']in('000000001635154c','000000008e3f8dc4'):targets[m['program_hash']+'.'+m['stage']]+=1
    output.write(canonical(row)+'\n')
 need(all(digest(path)==value for path,value in source.items()),'source metadata changed during audit')
 summary=dict(schema=1,counts=dict(counts),known_geometry_collision_observations=dict(targets),source_files=source,output_sha256=digest(out/'observed-program-links.jsonl'),game_started=False,native_cache_walker_executed=False,session_pointers_followed=False,formal_data_modified=False,qualification='Actual decoded metadata hardware-stage associations and exact static GS-copy source provenance. No SPIR-V provenance proof, pointer rewrite, native span reconstruction or PSO execution claimed')
 (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');return summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--index',type=Path,required=True);ap.add_argument('--decoded',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();r=audit(a.index,a.decoded,a.out);print(json.dumps({k:v for k,v in r.items()if k!='source_files'},indent=2))
if __name__=='__main__':main()
