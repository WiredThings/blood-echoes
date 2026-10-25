"""Join structurally read model references to existing MTD and shader metadata.
Produces metadata candidates only; never invents complete draw/PSO recipes.
"""
from pathlib import Path
import argparse,collections,hashlib,json,time

def base(s):return s.replace('\\','/').rsplit('/',1)[-1].casefold()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--scan',required=True,type=Path);ap.add_argument('--out',type=Path);ap.add_argument('--materials',required=True,type=Path);ap.add_argument('--catalog',required=True,type=Path);a=ap.parse_args();scan=a.scan.resolve(strict=True);output=a.out or scan.parent/('derived-bindings-'+str(time.time_ns()));assert output.is_absolute()and not output.exists(),'new absolute metadata output required';output.mkdir(parents=True)
 material=json.loads(a.materials.read_text());catalog=json.loads(a.catalog.read_text());known={base(x['material']):x for x in material['materials']};entries={x['id']:x for x in material['shader_entry_candidates']}
 counter=collections.Counter();missing=collections.Counter();partials={};occ=collections.Counter();stage_groups=collections.defaultdict(list)
 with(output/'derived-draw-schemas.jsonl').open('w')as out:
  for line in(scan/'models.jsonl').open():
   row=json.loads(line)
   for mesh in row['meshes']:
    m=row['materials'][mesh['material_index']];name=base(m['mtd']);counter[name]+=1
    mat=known.get(name)
    if not mat:missing[name]+=1
    # Exclude index counts, LOD flags and asset addresses: those do not prove a
    # new compiled pipeline. Preserve actual topology/culling and vertex layout.
    faces=sorted({(x['triangle_strip'],x['cull_backfaces'])for x in mesh['faces']})
    schema=dict(material=name,dynamic=mesh['dynamic'],vertex_streams=mesh['layouts'],topology_and_culling=[dict(triangle_strip=x[0],cull_backfaces=x[1])for x in faces])
    key=hashlib.sha256(json.dumps(schema,sort_keys=True,separators=(',',':')).encode()).hexdigest();occ[key]+=1
    if key not in partials:
     partials[key]=schema
     out.write(json.dumps(dict(id=key,example=row['resource'],asset_state=schema,material_exact_match=bool(mat),shader_family_candidates=(mat or{}).get('shader_entry_name_candidates',[]),complete_pso=False),ensure_ascii=False,separators=(',',':'))+'\n')
 for p in catalog['packages']:
  if p['kind']=='shader'and not p['debug']:
   for e in p['entries']:
    stem=e['name'].replace('\\','/').rsplit('.',1)[0]
    stage_groups[(p['path'],stem)].append(dict(entry=e['name'],entry_sha256=e['sha256'],stages=[dict(binary_type_raw=r['binary_type_raw'],program_hash=r['program_hash'],code_sha256=r.get('code_sha256'))for r in e['binary_info']]))
 grouped=0
 with(output/'shader-stage-family-candidates.jsonl').open('w')as out:
  for(p,stem),es in stage_groups.items():
   if len(es)>1:
    grouped+=1;out.write(json.dumps(dict(package=p,name_family=stem,entries=es,qualification='same-package exact stem; association candidate, not proven complete stage selection or PSO'),ensure_ascii=False,separators=(',',':'))+'\n')
 summary=dict(model_mesh_material_references=sum(counter.values()),distinct_model_mtd_names=len(counter),references_with_exact_mtd_record=sum(n for k,n in counter.items()if k in known),references_missing_mtd=sum(missing.values()),missing_mtd_names=dict(missing),materials_unused_by_scanned_models=sorted(set(known)-set(counter)),distinct_asset_draw_schemas=len(partials),shader_stage_family_candidates=grouped,full_pso_recipes_from_static_scan=0)
 (output/'binding-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 print('OUTPUT',output)
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
