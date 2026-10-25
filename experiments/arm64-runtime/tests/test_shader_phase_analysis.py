from pathlib import Path
import sys,unittest
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from shader_phase_analysis import analyze_shader_phases,HW_STAGE_NAMES
def fixture():
 def row(seq,kind,start,end,arg=0,tid=9):return dict(pid=7,tid=tid,seq=seq,kind=kind,start=start,end=end,wall=end-start,cpu=0,arg=arg)
 return [row(1,81,100,100,(6<<32)|3),row(2,79,110,120),row(3,80,125,130),row(4,83,135,150),row(5,82,100,100,1),row(6,78,100,160,0xabcdef)]
class Analysis(unittest.TestCase):
 def test_complete_disjoint(self):
  m=analyze_shader_phases(fixture())['modules'][0];self.assertTrue(m['complete']);self.assertEqual((m['stage'],m['permutation']),(6,3));self.assertAlmostEqual(m['other_module_wall_ms'],.00003)
 def test_vertex_is_stage_one_not_fragment(self):
  rows=fixture();rows[0]['arg']=(1<<32)|3;m=analyze_shader_phases(rows)['modules'][0];self.assertTrue(m['complete']);self.assertEqual(m['stage_name'],'vertex')
 def test_names_match_actual_hw_enum(self):
  import re
  source=(P/'vendor/metal_api/shadps4/shader_recompiler/runtime_info.h').read_text();body=re.search(r'enum class HwStage : u32\s*\{([^}]+)\}',source).group(1)
  self.assertEqual(tuple(x.strip().lower()for x in body.split(',')if x.strip()),HW_STAGE_NAMES)
 def test_missing_phase_not_zero(self):
  rows=[x for x in fixture()if x['kind']!=80];m=analyze_shader_phases(rows)['modules'][0];self.assertFalse(m['complete']);self.assertEqual(m['phases']['ir_to_spirv'],[]);self.assertNotIn('other_module_wall_ms',m)
 def test_return_not_shading_success(self):
  rows=fixture();rows[-2]['arg']=0;m=analyze_shader_phases(rows)['modules'][0];self.assertTrue(m['complete']);self.assertFalse(m['nonzero_module_handle'])
 def test_wrong_thread_not_attached(self):
  rows=fixture();rows[1]['tid']=10;a=analyze_shader_phases(rows);self.assertFalse(a['modules'][0]['complete']);self.assertEqual(a['orphan_phase_sequences'],[2])
 def test_duplicate_anchor_rejected(self):
  rows=fixture();rows.append(dict(rows[-1],seq=7));self.assertTrue(all(not x['complete']for x in analyze_shader_phases(rows)['modules']))
 def test_overlapping_phases_rejected(self):
  rows=fixture();rows[2].update(start=115,wall=15);self.assertFalse(analyze_shader_phases(rows)['modules'][0]['complete'])
 def test_backend_preload_not_gcn_compile(self):
  rows=fixture();rows.append(dict(rows[2],seq=7,kind=16,start=200,end=300,wall=100));a=analyze_shader_phases(rows);self.assertEqual(len(a['backend_calls_without_gcn_module']),1)
if __name__=='__main__':unittest.main()
