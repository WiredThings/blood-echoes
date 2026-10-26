"""Packaged ABI receipts retain validation facts without developer file paths."""
from pathlib import Path
import copy,hashlib,json,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from game_pso_component import runtime_layout_bytes

class LayoutPackaging(unittest.TestCase):
 def test_no_private_fixture_paths_and_original_not_modified(self):
  row=dict(schema=1,pointer_bytes=8,source_sha256={'profile.h':'a'*64},profile_fields=[{'name':'field','type':'u32'}],authored_writer_fixtures={'plain':dict(path='/private/developer/fixture.bin',sha256='b'*64,bytes=128,canonical_decode_checked=True)})
  before=copy.deepcopy(row);raw=runtime_layout_bytes(row);out=json.loads(raw)
  self.assertNotIn(b'/private/developer',raw);self.assertEqual(row,before)
  self.assertEqual(out['source_sha256'],row['source_sha256']);self.assertEqual(out['profile_fields'],row['profile_fields']);self.assertTrue(out['authored_writer_fixtures']['plain']['canonical_decode_checked']);self.assertEqual(out['authored_writer_fixtures']['plain']['sha256'],'b'*64)
 def test_deterministic_runtime_identity(self):
  a=dict(pointer_bytes=8,schema=1);b=dict(schema=1,pointer_bytes=8)
  self.assertEqual(runtime_layout_bytes(a),runtime_layout_bytes(b));b['pointer_bytes']=4
  self.assertNotEqual(hashlib.sha256(runtime_layout_bytes(a)).digest(),hashlib.sha256(runtime_layout_bytes(b)).digest())
 def test_malformed_receipt_refused(self):
  for value in([],{'bad':'not-an-object'}):
   with self.assertRaises(ValueError):runtime_layout_bytes({'authored_writer_fixtures':value})

if __name__=='__main__':unittest.main()
