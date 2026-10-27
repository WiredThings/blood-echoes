import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from analyze_texture_lineage import analyze

class TestLineage(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.path=self.root/'lineage_1_2.jsonl'
 def tearDown(self):self.temp.cleanup()
 def write(self,*,gap=False,saved=True,name='lineage_1_2_1.raw'):
  raw=b'\x11'*128+b'\0'*128;(self.root/'lineage_1_2_1.raw').write_bytes(raw)
  rows=[dict(op='lineage_scope',pid=1,epoch=2),dict(op='lineage_encoded',event=1,tick=1,file=name,detail=dict(phase='input',logical_bytes=128,texture=dict(uid=7,born_us=9,generation=3),partial=True))]
  if saved:rows.append(dict(op='lineage_saved',event=1,gpu_complete=True,written=True,bytes=256,file=name))
  if gap:rows.append(dict(op='lineage_gap',reason='pending limit'))
  self.path.write_text('\n'.join(map(json.dumps,rows))+'\n')
 def test_completed_partial_never_proves_image(self):
  self.write();r=analyze(self.path);self.assertTrue(r['observations'][0]['completed']);self.assertFalse(r['whole_texture_correctness_proven']);self.assertFalse(r['root_cause_proven']);self.assertEqual(r['warnings'],[])
 def test_gap_is_preserved(self):
  self.write(gap=True);self.assertIn('explicit history gap',analyze(self.path)['warnings'])
 def test_uncompleted_not_admitted(self):
  self.write(saved=False);r=analyze(self.path);self.assertFalse(r['observations'][0]['completed']);self.assertTrue(r['warnings'])
 def test_path_escape_rejected(self):
  self.write(name='../lineage_1_2_1.raw')
  with self.assertRaises(ValueError):analyze(self.path)
 def test_replaced_payload_rejected(self):
  self.write();(self.root/'lineage_1_2_1.raw').write_bytes(b'changed')
  with self.assertRaises(ValueError):analyze(self.path)
 def test_payload_link_rejected(self):
  self.write();p=self.root/'lineage_1_2_1.raw';p.rename(self.root/'original');p.symlink_to('original')
  with self.assertRaises(OSError):analyze(self.path)
 def test_duplicate_epoch_not_hidden(self):
  self.write()
  with self.path.open('a')as f:f.write(json.dumps(dict(op='lineage_scope',pid=1,epoch=4))+'\n')
  self.assertIn('missing or ambiguous process epoch',analyze(self.path)['warnings'])
 def test_partial_tail_preserves_verified_prefix_with_warning(self):
  self.write()
  with self.path.open('a')as f:f.write('{incomplete')
  r=analyze(self.path);self.assertTrue(r['observations'][0]['completed']);self.assertIn('malformed or partial lineage row',r['warnings']);self.assertFalse(r['root_cause_proven'])

if __name__=='__main__':unittest.main()
