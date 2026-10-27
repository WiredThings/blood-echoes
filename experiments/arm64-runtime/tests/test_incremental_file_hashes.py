"""Authored content changes and hostile cache nodes; no game or native process."""
from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from incremental_file_hashes import Hashes,digest
from launcher_game_catalog import file_sha

class Incremental(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.source=self.root/'source';self.source.write_bytes(b'authored content');self.cache=self.root/'metadata.json';self.calls=[]
 def hash(self,path,check):self.calls.append(str(path));return file_sha(path,check)
 def index(self,**kw):return Hashes(self.cache,kw.pop('binding','authored-binding'),hasher=kw.pop('hasher',self.hash),**kw)
 def seed(self):
  h=self.index();value=h.get(self.source);self.assertTrue(h.publish());self.calls.clear();return value
 def test_warm_opens_no_content_and_keeps_digest(self):
  want=self.seed();h=self.index(hasher=lambda *_:self.fail('unchanged source was opened'));self.assertEqual(h.get(self.source),want);h.verify();h.publish();self.assertEqual(h.report()['hashed_bytes'],0);self.assertEqual(h.report()['reused_files'],1)
 def test_same_size_and_restored_mtime_rewrite_is_detected(self):
  old=self.seed();s=self.source.stat();self.source.write_bytes(b'X'*s.st_size);os.utime(self.source,ns=(s.st_atime_ns,s.st_mtime_ns));h=self.index();self.assertNotEqual(h.get(self.source),old);self.assertEqual(len(self.calls),1)
 def test_replacement_inode_with_restored_mtime_is_detected(self):
  old=self.seed();s=self.source.stat();temp=self.root/'replacement';temp.write_bytes(b'Y'*s.st_size);os.utime(temp,ns=(s.st_atime_ns,s.st_mtime_ns));temp.replace(self.source);h=self.index();self.assertNotEqual(h.get(self.source),old);self.assertEqual(len(self.calls),1)
 def test_touch_same_content_reads_once_then_reuses(self):
  old=self.seed();self.source.write_bytes(self.source.read_bytes());h=self.index();self.assertEqual(h.get(self.source),old);h.publish();self.assertEqual(len(self.calls),1);self.calls.clear();h=self.index();h.get(self.source);self.assertEqual(self.calls,[])
 def test_binding_change_is_full_check(self):
  self.seed();h=self.index(binding='other');self.assertEqual(h.receipt_status,'invalid');h.get(self.source);self.assertEqual(len(self.calls),1)
 def test_corrupt_digest_is_full_check(self):
  self.seed();r=json.loads(self.cache.read_text());r['body']['files'][str(self.source)]['sha256']='a'*64;self.cache.write_text(json.dumps(r));h=self.index();self.assertEqual(h.receipt_status,'invalid');value=h.get(self.source);self.assertEqual(value[0],hashlib.sha256(self.source.read_bytes()).hexdigest());self.assertEqual(len(self.calls),1)
 def test_malformed_and_duplicate_receipt_fields_are_full_check(self):
  for raw in ('invalid','[]','{"body":{},"body":{},"sha256":"x"}'):
   self.cache.write_text(raw);self.cache.chmod(0o600);h=self.index();self.assertEqual(h.receipt_status,'invalid');h.get(self.source)
  self.assertEqual(len(self.calls),3)
 def test_bad_stamp_is_full_check(self):
  self.seed();r=json.loads(self.cache.read_text());r['body']['files'][str(self.source)]['stamp']=[0];r['sha256']=digest(r['body']);self.cache.write_text(json.dumps(r));h=self.index();self.assertEqual(h.receipt_status,'invalid');h.get(self.source);self.assertEqual(len(self.calls),1)
 def test_receipt_symlink_never_followed_or_replaced(self):
  other=self.root/'unrelated';other.write_bytes(b'keep unrelated');self.cache.symlink_to(other);h=self.index();self.assertEqual(h.receipt_status,'invalid');h.get(self.source);self.assertFalse(h.publish());self.assertTrue(self.cache.is_symlink());self.assertEqual(other.read_bytes(),b'keep unrelated')
 def test_receipt_hardlink_never_replaced(self):
  self.seed();other=self.root/'linked';os.link(self.cache,other);before=other.read_bytes();h=self.index();self.assertEqual(h.receipt_status,'invalid');h.get(self.source);self.assertFalse(h.publish());self.assertEqual(other.read_bytes(),before);self.assertEqual(self.cache.stat().st_nlink,2)
 def test_nonprivate_receipt_mode_is_full_check(self):
  self.seed();self.cache.chmod(0o644);h=self.index();self.assertEqual(h.receipt_status,'invalid');h.get(self.source);self.assertEqual(len(self.calls),1)
 def test_source_symlink_cannot_use_cached_digest(self):
  self.seed();other=self.root/'other';self.source.rename(other);self.source.symlink_to(other)
  with self.assertRaisesRegex(ValueError,'canonical'):self.index().get(self.source)
 def test_changed_during_hash_cannot_publish(self):
  def mutate(p,check):
   result=file_sha(p,check);p.write_bytes(b'changed while reading');return result
  h=self.index(hasher=mutate)
  with self.assertRaisesRegex(ValueError,'changed during'):h.get(self.source)
  self.assertFalse(self.cache.exists())
 def test_changed_after_cached_lookup_cannot_publish(self):
  self.seed();before=self.cache.read_bytes();h=self.index();h.get(self.source);self.source.write_bytes(b'changed afterwards')
  with self.assertRaisesRegex(ValueError,'changed during'):h.publish()
  self.assertEqual(self.cache.read_bytes(),before)
 def test_new_file_read_and_unselected_old_entries_pruned(self):
  self.seed();second=self.root/'new';second.write_bytes(b'new');h=self.index();h.get(second);h.publish();rows=json.loads(self.cache.read_text())['body']['files'];self.assertEqual(set(rows),{str(second)})
 def test_oversize_hint_not_read_as_content(self):
  self.cache.write_bytes(b'x'*9);self.cache.chmod(0o600)
  with patch('incremental_file_hashes.MAX_BYTES',8):h=self.index()
  self.assertEqual(h.receipt_status,'invalid')
 def test_file_count_bound(self):
  h=self.index();h.get(self.source);second=self.root/'second';second.write_bytes(b'new')
  with patch('incremental_file_hashes.MAX_FILES',1),self.assertRaisesRegex(ValueError,'file-count'):h.get(second)

if __name__=='__main__':unittest.main()
