"""Complete authored package closures plus exact invalidation counterexamples."""
from pathlib import Path
import json,os,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_formal_runtime_profile import Formal
import launcher_runtime_profile as p
import startup_change_journal as j

class Registration(Formal):
 def setUp(self):
  super().setUp();self.reg=self.data/'registration';self.q=patch.object(j,'query',return_value={'schema':1,'complete':True,'code_roots':True});self.u=patch.object(j,'unchanged',return_value=True);self.q.start();self.u.start()
 def tearDown(self):self.u.stop();self.q.stop();super().tearDown()
 def admit(self):return p.bundle(self.stage,registration_root=self.reg)
 def test_fresh_interpreter_digest_state_reuses_full_registered_closure(self):
  first=self.admit();self.assertGreater(first.validation_work['hashed_files'],0)
  with p._code_memo_lock:p._code_memo.clear()
  before=p.code_validation_work();second=self.admit();after=p.code_validation_work()
  self.assertTrue(second.validation_work['journal_reused']);self.assertEqual(second.validation_work['hashed_files'],0)
  self.assertEqual(after['hash_reads']-before['hash_reads'],4) # actual small manifests only
  self.assertEqual(after['hash_bytes']-before['hash_bytes'],sum(x.stat().st_size for x in [self.app/'Contents/Info.plist',self.stage/'NATIVE-METAL.json',self.stage/'ARM64-LOCAL-PREPARATION.json',self.stage/'SOURCE-PARITY.json']))
 def test_changed_code_without_ui_is_rejected_after_change_event(self):
  self.admit();(self.stage/self.roles[0]).write_bytes(b'changed outside frontend')
  with patch.object(j,'unchanged',return_value=False),self.assertRaisesRegex(ValueError,'identity changed'):self.admit()
 def test_history_lost_rechecks_actual_bytes(self):
  self.admit()
  with p._code_memo_lock:p._code_memo.clear()
  with patch.object(j,'unchanged',return_value=False):result=self.admit()
  self.assertFalse(result.validation_work['journal_reused']);self.assertGreater(result.validation_work['hashed_files'],0)
 def test_updated_manifest_rechecks_instead_of_blessing_code(self):
  self.admit();self.native['frozen_runtime'][self.roles[0]]='0'*64;self.write_manifests()
  with self.assertRaisesRegex(ValueError,'identity changed'):self.admit()
 def test_corrupt_registration_is_rebuilt_without_deleting_old_artifacts(self):
  self.admit();receipt=next(self.reg.glob('*.json'));receipt.write_text('{}');result=self.admit();self.assertFalse(result.validation_work['journal_reused']);self.assertTrue(receipt.exists())
 def test_symlink_and_permission_change_rejected_after_event(self):
  self.admit();file=self.stage/self.roles[0];file.chmod(0o622)
  with patch.object(j,'unchanged',return_value=False),self.assertRaises(ValueError):self.admit()
 def test_selection_and_ui_env_are_not_package_registration_identity(self):
  first=self.admit();self.env['BB_LANGUAGE']='11';self.env['BB_FAKE_UNRELATED_UI']='changed';second=self.admit();self.assertTrue(second.validation_work['journal_reused']);self.assertEqual(first.native,second.native)
 def test_final_launch_manifest_reuses_image_digest_but_detects_real_edits(self):
  scope=self.scope();from warm_archive_manifest import make_manifest
  values=dict(translator_artifacts={'gpu_library':self.stage/'bin/gpu-metal-arm64/libbbgpu.dylib'},game_artifacts=dict(boot=self.args[0],content_profile=self.data/'out/content.bin',patches=self.data/'out/patches.bin',settings=self.data/'bbport.ini'),options={},profile={'author':'fixed-full-key'})
  original=make_manifest(**values);self.assertEqual(scope.archive_manifest(**values),original)
  with patch('launcher_game_catalog.file_sha',side_effect=AssertionError('unchanged final input rehashed')):self.assertEqual(scope.archive_manifest(**values),original)
  path=self.data/'out/patches.bin';stamp=path.stat();path.write_bytes(b'changed patch input');os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns));self.assertNotEqual(scope.archive_manifest(**values),original)

if __name__=='__main__':unittest.main()
