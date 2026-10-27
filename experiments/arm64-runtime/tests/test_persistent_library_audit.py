"""Real journal invalidation with authored compiler bytes; no Metal/GPU/game."""
from pathlib import Path
import json,os,sys,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_local_metal_builder as authored
import startup_change_journal as journal

@unittest.skipUnless(sys.platform=='darwin','Darwin journal')
class PersistentAudit(unittest.TestCase):
 @classmethod
 def setUpClass(cls):authored.BuilderTests.setUpClass()
 def setUp(self):
  self.f=authored.BuilderTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.key,self.request=self.f.export()
 def settle(self):
  for _ in range(40):
   r=self.f.run_builder();self.assertEqual(r['status'],'passed',r)
   if r.get('persistent_audit_reused'):return r
   time.sleep(.05) # Native history batches may still include our cold publishes.
  self.fail('real journal did not settle on authored unchanged inputs')
 def test_unchanged_files_skip_request_and_library_byte_reads(self):
  self.settle()
  with patch.object(authored.builder,'load_request',side_effect=AssertionError('unchanged request reread')),patch.object(authored.builder,'already_succeeded',side_effect=AssertionError('unchanged library reread')):
   r=self.f.run_builder();self.assertEqual(r['status'],'passed',r);self.assertTrue(r['persistent_audit_reused']);self.assertEqual(r['built'],0)
 def test_bad_library_is_rejected_without_replacement(self):
  r=self.settle();lib=self.f.artifact(r,self.key,'.metallib');lib.write_bytes(b'CORRUPTED');before=lib.read_bytes();r=self.f.run_builder();self.assertEqual(r['status'],'refused',r);self.assertEqual(lib.read_bytes(),before)
 def test_same_size_source_rewrite_with_restored_mtime_not_reused(self):
  self.settle();p=self.f.requests/(self.key+'.metal');s=p.stat();p.write_bytes(b'X'*s.st_size);os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns));r=self.f.run_builder();self.assertEqual(r['status'],'refused',r);self.assertNotIn('persistent_audit_reused',r)
 def test_added_request_and_sdk_change_reenter_full_audit(self):
  self.settle();self.f.export(source=b'kernel void added() {}\n');r=self.f.run_builder();self.assertEqual(r['status'],'passed',r);self.assertEqual(r['built'],1);self.assertNotIn('persistent_audit_reused',r);self.settle();(self.f.runner.sdk/'SDKSettings.json').write_text('{"changed":true}');r=self.f.run_builder();self.assertEqual(r['status'],'passed',r);self.assertNotIn('persistent_audit_reused',r)
 def test_missing_history_and_corrupt_hint_fall_back(self):
  r=self.settle();path=self.f.folder/'artifacts'/('.verified-'+r['build_id']+'.json');path.write_bytes(b'bad hint');r=self.f.run_builder();self.assertEqual(r['status'],'passed',r);self.assertNotIn('persistent_audit_reused',r);self.settle()
  with patch.object(journal,'query',return_value=None):r=self.f.run_builder()
  self.assertEqual(r['status'],'passed',r);self.assertNotIn('persistent_audit_reused',r)

if __name__=='__main__':unittest.main()
