"""CPU-only authored material links: no fuzzy fallback or fake PSO completion."""
from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import derive_bindings as d

def material(name='A.mtd',digest='a'*64):return dict(material=name,material_sha256=digest,shader_path='A.spx',params=[],textures=[],shader_entry_name_candidates=[7])
class Bindings(unittest.TestCase):
 def test_normalized_exact_name_retains_candidate_only_shader_status(self):
  r=d.bind_material(r'N:\Materials\a.MTD',d.material_records(dict(materials=[material()])));self.assertEqual(r['status'],'exact-material-name');self.assertFalse(r['shader_family_is_authoritative'])
 def test_missing_empty_and_duplicate_names_never_choose(self):
  self.assertEqual(d.bind_material('',{})['status'],'empty-reference');self.assertEqual(d.bind_material('near.mtd',{})['status'],'missing-material')
  known=d.material_records(dict(materials=[material(),material('a.MTD','b'*64)]));r=d.bind_material('a.mtd',known);self.assertEqual(r['status'],'ambiguous-material');self.assertEqual(len(r['material_candidates']),2);self.assertNotIn('material_sha256',r)
 def test_complete_references_and_no_index_counts_in_schema_key(self):
  with tempfile.TemporaryDirectory()as temp:
   p=Path(temp).resolve();scan=p/'scan';scan.mkdir();model=dict(resource='author/model',sha256='b'*64,materials=[dict(name='A',mtd='a.mtd',gx_offset=100,texture_count=1)],meshes=[dict(material_index=0,dynamic=False,layouts=[],faces=[dict(triangle_strip=False,cull_backfaces=True,index_count=3)]),dict(material_index=0,dynamic=False,layouts=[],faces=[dict(triangle_strip=False,cull_backfaces=True,index_count=99)])]);(scan/'models.jsonl').write_text(json.dumps(model)+'\n');m=p/'materials.json';m.write_text(json.dumps(dict(materials=[material()])));c=p/'catalog.json';c.write_text(json.dumps(dict(packages=[])))
   r=d.derive(scan,m,c,p/'output');self.assertEqual(r['model_mesh_material_references'],2);self.assertEqual(r['distinct_asset_draw_schemas'],1);self.assertEqual(r['references_with_uninterpreted_gx_offset'],2);self.assertEqual(r['full_pso_recipes_from_static_scan'],0);refs=[json.loads(x)for x in(p/'output/asset-reference-links.jsonl').read_text().splitlines()];self.assertEqual([x['mesh_index']for x in refs],[0,1]);self.assertTrue(all(not x['complete_pso']for x in refs))
 def test_stage_index_never_authorizes_filename_pairing(self):
  with tempfile.TemporaryDirectory()as temp:
   p=Path(temp).resolve()/'index.jsonl';p.write_text(json.dumps(dict(schema=1,complete_pso=False,identity=dict(program_hash='h',hardware_stage='geometry',code_sha256='a'*64),variants=[dict(evidence_refs=[dict(package='p',entry='x.gpo')])]))+'\n');r=d.static_entry_map(p);self.assertEqual(next(iter(r['p','x.gpo'].values()))['hardware_stage'],'geometry')
if __name__=='__main__':unittest.main(verbosity=2)
