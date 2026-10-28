"""Actual owned code files, bounded digest memo, no game or native subprocess."""
from pathlib import Path
import hashlib,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launcher_runtime_profile as p

class Session(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.file=self.root/'code';self.file.write_bytes(b'original code');self.file.chmod(0o600)
  with p._code_memo_lock:p._code_memo.clear()
 def tearDown(self):self.tmp.cleanup()
 def read(self,**kw):return p.code_bytes(self.file,content=False,**kw)
 def test_same_file_no_second_content_open(self):
  self.read();before=p.code_validation_work()
  with patch.object(p.os,'open',side_effect=AssertionError('repeated content read')):_,row=self.read()
  self.assertEqual(row['sha256'],hashlib.sha256(b'original code').hexdigest());after=p.code_validation_work();self.assertEqual(after['hash_bytes'],before['hash_bytes']);self.assertGreater(after['metadata_digest_hits'],before['metadata_digest_hits'])
 def test_required_content_still_returns_actual_bytes(self):
  self.read();data,_=p.code_bytes(self.file);self.assertEqual(data,b'original code')
 def test_current_expected_digest_still_checked_on_hit(self):
  self.read()
  with self.assertRaises(ValueError):self.read(expected='0'*64)
 def test_smaller_limit_still_rejected_on_hit(self):
  self.read()
  with self.assertRaises(ValueError):self.read(limit=1)
 def test_rewrite_restored_mtime_requires_rehash(self):
  self.read();before=self.file.stat();self.file.write_bytes(b'changed! code');os.utime(self.file,ns=(before.st_atime_ns,before.st_mtime_ns));_,row=self.read();self.assertEqual(row['sha256'],hashlib.sha256(b'changed! code').hexdigest())
 def test_same_bytes_inode_replacement_is_revalidated(self):
  self.read();other=self.root/'replacement';other.write_bytes(self.file.read_bytes());other.chmod(0o600);os.replace(other,self.file);before=p.code_validation_work()['hash_reads'];self.read();self.assertEqual(p.code_validation_work()['hash_reads'],before+1)
 def test_permissions_links_symlink_and_missing_refused(self):
  self.read();self.file.chmod(0o622)
  with self.assertRaises(ValueError):self.read()
  self.file.chmod(0o600);link=self.root/'link';os.link(self.file,link)
  with self.assertRaises(ValueError):self.read()
  link.unlink();self.file.unlink();self.file.symlink_to('missing')
  with self.assertRaises((ValueError,OSError)):self.read()
 def test_memo_is_bounded_and_keeps_no_content(self):
  with patch.object(p,'_CODE_MEMO_MAX',2):
   for n in range(4):f=self.root/str(n);f.write_bytes(b'code');p.code_bytes(f,content=False)
   self.assertEqual(p.code_validation_work()['memo_entries'],2)
   self.assertTrue(all(isinstance(v[1],str)and len(v)==2 for v in p._code_memo.values()))

if __name__=='__main__':unittest.main()
