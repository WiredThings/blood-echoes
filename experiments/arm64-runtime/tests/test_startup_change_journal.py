from pathlib import Path
import json,os,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import startup_change_journal as journal
from incremental_file_hashes import Hashes
from launcher_game_catalog import file_sha

class QueryBoundary(unittest.TestCase):
 def test_failure_timeout_incomplete_and_oversize_are_not_clean(self):
  for result in [subprocess.CompletedProcess([],65,b''),subprocess.CompletedProcess([],0,b'{}'),subprocess.CompletedProcess([],0,b'X'),subprocess.CompletedProcess([],0,b'X'*(journal.MAX_MESSAGE+1))]:
   with patch.object(journal.subprocess,'run',return_value=result):self.assertIsNone(journal.query(['/private/tmp']))
  with patch.object(journal.subprocess,'run',side_effect=subprocess.TimeoutExpired('native',5)):self.assertIsNone(journal.query(['/private/tmp']))
  self.assertFalse(journal.unchanged(None,['/private/tmp']))
 def test_no_clean_claim_without_explicit_native_result(self):
  with patch.object(journal,'query',return_value=dict(schema=1,complete=True,unchanged=False)):
   self.assertFalse(journal.unchanged({},['/private/tmp']))
 def test_token_must_not_bypass_hint_digest(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();src=root/'data';src.write_bytes(b'content');hint=root/'h.json'
   h=Hashes(hint,'scope',hasher=file_sha);h.get(src);h.publish()
   row=json.loads(hint.read_text());row['body']['journal']={'tampered':True};hint.write_text(json.dumps(row))
   with patch.object(journal,'unchanged',return_value=True):h=Hashes(hint,'scope',hasher=file_sha,watch_roots=[root])
   self.assertFalse(h.journal_valid);self.assertEqual(h.receipt_status,'invalid')

@unittest.skipUnless(sys.platform=='darwin','Darwin FSEvents integration')
class NativeChanges(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()/'source';self.root.mkdir();self.source=self.root/'file';self.source.write_bytes(b'content')
 def token(self):
  result=journal.query([self.root]);self.assertIsNotNone(result,'native history/volume must be available in this test');return result
 def test_stable_and_immediate_same_size_rewrite_with_restored_mtime(self):
  token=self.token();self.assertTrue(journal.unchanged(token,[self.root]));before=self.source.stat();self.source.write_bytes(b'CONTENT');os.utime(self.source,ns=(before.st_atime_ns,before.st_mtime_ns));self.assertFalse(journal.unchanged(token,[self.root]))
 def test_nested_addition_and_removal(self):
  folder=self.root/'nested';folder.mkdir();token=self.token();(folder/'new').write_bytes(b'new');self.assertFalse(journal.unchanged(token,[self.root]));token=self.token();(folder/'new').unlink();self.assertFalse(journal.unchanged(token,[self.root]))
 def test_root_inode_replacement_and_symlink_are_not_clean(self):
  token=self.token();other=self.root/'elsewhere';other.mkdir();link=self.root/'alias';link.symlink_to(other,target_is_directory=True);self.assertFalse(journal.unchanged(token,[link]))
  original=self.root/'original';original.mkdir();token=journal.query([original]);self.assertIsNotNone(token);original.rename(self.root/'old');original.mkdir();self.assertFalse(journal.unchanged(token,[original]))
 def test_multiple_roots_detect_changes_in_each(self):
  other=self.root.parent/'other';other.mkdir();roots=[self.root,other];token=journal.query(roots);self.assertIsNotNone(token);self.assertTrue(journal.unchanged(token,roots));(other/'new').write_bytes(b'new');self.assertFalse(journal.unchanged(token,roots));token=journal.query(roots);self.assertIsNotNone(token);self.source.write_bytes(b'changed');self.assertFalse(journal.unchanged(token,roots))
 def test_warm_get_and_verify_do_not_stat_each_source(self):
  hint=self.root.parent/(self.root.name+'-hint.json');self.addCleanup(lambda:hint.unlink(missing_ok=True))
  h=Hashes(hint,'scope',hasher=file_sha,watch_roots=[self.root]);want=h.get(self.source);h.publish();h=Hashes(hint,'scope',hasher=file_sha,watch_roots=[self.root]);self.assertTrue(h.journal_valid)
  with patch('incremental_file_hashes.stamp',side_effect=AssertionError('per-file stat on unchanged journal')):
   self.assertEqual(h.get(self.source),want);h.verify()
 def test_change_between_lookup_and_use_forces_original_stamp_check(self):
  hint=self.root.parent/(self.root.name+'-hint.json');self.addCleanup(lambda:hint.unlink(missing_ok=True))
  h=Hashes(hint,'scope',hasher=file_sha,watch_roots=[self.root]);h.get(self.source);h.publish();h=Hashes(hint,'scope',hasher=file_sha,watch_roots=[self.root]);self.assertTrue(h.journal_valid);h.get(self.source);self.source.write_bytes(b'CHANGED')
  with self.assertRaisesRegex(ValueError,'changed during'):h.verify()

if __name__=='__main__':unittest.main()
