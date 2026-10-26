"""Join verified model/material/program metadata without fabricating draw states.

An exact MTD-name reference and an exact program identity are different from a
proven multi-stage pass selection. Missing/ambiguous identities stay explicit.
"""
from pathlib import Path
import argparse,collections,hashlib,json,time
from static_program_links import canonical,digest,need,read_json,regular,rows

def base(s):return s.replace('\\','/').rsplit('/',1)[-1].casefold()

def material_records(material):
 known=collections.defaultdict(list)
 for row in material['materials']:known[base(row['material'])].append(row)
 return known

def bind_material(raw,known):
 name=base(raw);matches=known.get(name,[])
 # Never silently overwrite duplicate normalized names. A digest-equivalent
 # duplicate is still recorded as multiple sources, rather than guessed away.
 status='empty-reference'if not name else'missing-material'if not matches else'ambiguous-material'if len(matches)!=1 else'exact-material-name'
 row=dict(status=status,normalized_name=name,normalization='Windows basename/casefold; not shader/pass selection',material_candidates=[dict(material=r['material'],material_sha256=r['material_sha256'])for r in matches])
 if len(matches)==1:
  m=matches[0];row.update(material_sha256=m['material_sha256'],shader_path=m['shader_path'],shader_family_candidates=m.get('shader_entry_name_candidates',[]),shader_family_is_authoritative=False)
 return row

def static_entry_map(path):
 result=collections.defaultdict(dict)
 if path is None:return result
 for row in rows(path):
  need(row.get('schema')==1 and row.get('complete_pso')is False,'static program index shape')
  identity=row['identity'];need(identity['hardware_stage']in('fragment','vertex','geometry','export','hull','local','compute'),'native stage name')
  for variant in row['variants']:
   for ref in variant['evidence_refs']:
    if ref['entry']is not None:result[ref['package'],ref['entry']][canonical(identity)]=identity
 return result

def derive(scan,materials,catalog,output,static_program_index=None):
 scan=Path(scan).resolve(strict=True);output=Path(output)
 need(output.is_absolute()and not output.exists(),'new absolute metadata output required')
 inputs=[regular(scan/'models.jsonl'),regular(materials),regular(catalog)]
 if static_program_index is not None:inputs.append(regular(static_program_index))
 before={str(p):digest(p)for p in inputs};material=read_json(materials);source_catalog=read_json(catalog);known=material_records(material);programs=static_entry_map(static_program_index)
 output.mkdir(parents=True);counter=collections.Counter();missing=collections.Counter();binding_counts=collections.Counter();partials={};occ=collections.Counter();stage_groups=collections.defaultdict(list);model_count=0;missing_by_kind=collections.Counter();gx_refs=0
 with(output/'derived-draw-schemas.jsonl').open('x')as out,(output/'asset-reference-links.jsonl').open('x')as refs,(output/'missing-material-references.jsonl').open('x')as absent:
  for row in rows(scan/'models.jsonl'):
   model_count+=1;need(model_count<=100000,'model metadata count budget')
   for mesh_index,mesh in enumerate(row['meshes']):
    need(type(mesh['material_index'])is int and 0<=mesh['material_index']<len(row['materials']),'mesh material reference index')
    m=row['materials'][mesh['material_index']];name=base(m['mtd']);counter[name]+=1;binding=bind_material(m['mtd'],known);binding_counts[binding['status']]+=1
    if binding['status']!='exact-material-name':missing[name]+=1
    faces=sorted({(x['triangle_strip'],x['cull_backfaces'])for x in mesh['faces']})
    schema=dict(material=name,dynamic=mesh['dynamic'],vertex_streams=mesh['layouts'],topology_and_culling=[dict(triangle_strip=x[0],cull_backfaces=x[1])for x in faces])
    key=hashlib.sha256(json.dumps(schema,sort_keys=True,separators=(',',':')).encode()).hexdigest();occ[key]+=1
    ref=dict(schema=1,asset_schema_id=key,model_resource=row['resource'],model_sha256=row['sha256'],mesh_index=mesh_index,material_index=mesh['material_index'],material_name=m['name'],material_reference=m['mtd'],material_binding_status=binding['status'],material_gx_offset=m.get('gx_offset'),texture_reference_count=m.get('texture_count'),complete_pso=False)
    refs.write(canonical(ref)+'\n');gx_refs+=bool(m.get('gx_offset'))
    if binding['status']!='exact-material-name':
     kind=row['resource'].split('::',1)[0].split('/');kind=kind[1]if len(kind)>1 else'unknown';missing_by_kind[kind]+=1;absent.write(canonical(dict(**ref,missing_reason=binding['status']))+'\n')
    if key not in partials:
     need(len(partials)<65536,'distinct asset schema budget');partials[key]=schema
     out.write(canonical(dict(schema=2,id=key,example=row['resource'],asset_state=schema,material_exact_match=binding['status']=='exact-material-name',material_binding=binding,shader_family_candidates=binding.get('shader_family_candidates',[]),complete_pso=False,unresolved_compile_inputs=['authoritative-stage-pass-selection','attachment-and-sample-state','blend-depth-stencil-state','runtime-resource-specialization','fetch-or-instance-divisors'],facts_origin='actual FLVER material reference/layout/topology only'))+'\n')
 grouped=0;resolved_entry_identities=0
 with(output/'shader-stage-family-candidates.jsonl').open('x')as out:
  for package in source_catalog['packages']:
   if package['kind']!='shader'or package['debug']:continue
   for entry in package['entries']:
    stem=entry['name'].replace('\\','/').rsplit('.',1)[0];exact=list(programs.get((package['path'],entry['name']),{}).values());resolved_entry_identities+=len(exact)
    stage_groups[package['path'],stem].append(dict(entry=entry['name'],entry_sha256=entry['sha256'],stages=[dict(binary_type_raw=r['binary_type_raw'],program_hash=r['program_hash'],code_sha256=r.get('code_sha256'))for r in entry['binary_info']],verified_static_program_identities=exact,filename_extension_is_stage_authority=False))
  for(package,stem),entries in sorted(stage_groups.items()):
   if len(entries)>1:
    grouped+=1;out.write(canonical(dict(schema=2,package=package,name_family=stem,entries=entries,stage_pairing_authority=False,qualification='same-package exact stem is a candidate; checked headers establish per-entry hardware stages, never which entries a game pass selects'))+'\n')
 with(output/'material-facts.jsonl').open('x')as out:
  for record in material['materials']:out.write(canonical(dict(schema=1,material=record['material'],material_sha256=record['material_sha256'],shader_path=record['shader_path'],params=record['params'],textures=record['textures'],shader_family_candidates=record.get('shader_entry_name_candidates',[]),candidate_mapping_is_authoritative=False,parameter_to_backend_state_mapping_verified=False))+'\n')
 summary=dict(schema=2,parsed_models=model_count,model_mesh_material_references=sum(counter.values()),distinct_model_mtd_names=len(counter),references_with_exact_mtd_record=binding_counts['exact-material-name'],references_missing_mtd=sum(missing.values()),material_binding_status_counts=dict(binding_counts),missing_mtd_names=dict(missing),missing_material_references_by_asset_kind=dict(missing_by_kind),materials_unused_by_scanned_models=sorted(set(known)-set(counter)),ambiguous_normalized_material_names=[k for k,v in known.items()if len(v)>1],references_with_uninterpreted_gx_offset=gx_refs,distinct_asset_draw_schemas=len(partials),shader_stage_family_candidates=grouped,verified_static_entry_program_identities=resolved_entry_identities,full_pso_recipes_from_static_scan=0,source_files=before,formal_data_modified=False,game_started=False,qualification='FLVER/MTD references and independent static program identities; stage families remain candidates and no missing dynamic PSO field receives an invented default')
 need(all(digest(p)==h for p,h in before.items()),'input metadata changed during association')
 (output/'binding-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');return summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--scan',required=True,type=Path);ap.add_argument('--out',type=Path);ap.add_argument('--materials',required=True,type=Path);ap.add_argument('--catalog',required=True,type=Path);ap.add_argument('--static-program-index',type=Path);a=ap.parse_args();scan=a.scan.resolve(strict=True);out=a.out or scan.parent/('derived-bindings-'+str(time.time_ns()));r=derive(scan,a.materials,a.catalog,out,a.static_program_index);print('OUTPUT',out);print(json.dumps(r,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
