"""Authored temporary-selector controls; no game, preferences or GPU access."""
from pathlib import Path
import json,os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import texture_binding_request as m

class Request(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.tag='a'*24;self.directory=self.root/('texture-binding-'+self.tag);self.directory.mkdir(mode=0o700);self.path=self.root/m.REQUEST;self.value=dict(schema=1,id=self.tag,created=1000,expires=2000);self.write()
 def write(self):self.path.write_text(json.dumps(self.value));self.path.chmod(0o600)
 def test_only_three_diagnostic_variables_are_added(self):
  before=dict(BB_USER_DIR='/original/save',BB_CONFIG='/original/config',BB_THD_TOKEN='authored-secret',BB_GPU_USER_DIR='/original/cache',BB_LANGUAGE='11');after,report=m.apply(before,root=self.root,now=1500)
  self.assertEqual({k:after[k]for k in before},before);self.assertEqual(set(after)-set(before),{'BB_TEXTURE_BINDING_CAPTURE_DIR','BB_CAPTURE_TEXTURE_BINDINGS','BB_CAPTURE_CONSECUTIVE_COUNT'});self.assertTrue(report['trigger_required']);self.assertFalse(report['texture_contents_read']);self.assertFalse((self.directory/'trigger').exists());self.assertNotIn('authored-secret',json.dumps(report))
 def test_absent_request_is_exact_noop(self):
  self.path.unlink();env={'BB_LANGUAGE':'11'};self.assertEqual(m.apply(env,root=self.root,now=1500),(env,None))
 def test_expired_future_or_overlong_request_is_noop(self):
  for created,expires in [(1000,1400),(1600,2000),(1000,1000+m.MAX_AGE+1)]:
   self.value.update(created=created,expires=expires);self.write();self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
 def test_request_cannot_select_paths_or_other_controls(self):
  for tag in ['../elsewhere','A'*24,'a'*25,'a'*23]:
   self.value['id']=tag;self.write();self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
  self.value['id']=self.tag;self.value['BB_PAD_FILE']='x';self.write();self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
 def test_world_readable_or_oversized_request_is_refused(self):
  self.path.chmod(0o644);self.assertEqual(m.apply({},root=self.root,now=1500),({},None));self.path.chmod(0o600);self.path.write_bytes(b'x'*4097);self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
 def test_linked_or_shared_request_is_refused(self):
  other=self.root/'other.json';self.path.rename(other);self.path.symlink_to(other);self.assertEqual(m.apply({},root=self.root,now=1500),({},None));self.path.unlink();os.link(other,self.path);self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
 def test_linked_missing_or_shared_directory_is_refused(self):
  self.directory.rmdir();self.assertEqual(m.apply({},root=self.root,now=1500),({},None));elsewhere=self.root/'elsewhere';elsewhere.mkdir(mode=0o700);self.directory.symlink_to(elsewhere);self.assertEqual(m.apply({},root=self.root,now=1500),({},None));self.directory.unlink();self.directory.mkdir(mode=0o755);self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
 def test_malformed_and_nonfinite_times_fail_closed(self):
  for value in [[],None,{},dict(self.value,schema=True),dict(self.value,expires=float('nan')),dict(self.value,created='1000')]:
   self.path.write_text(json.dumps(value));self.assertEqual(m.apply({},root=self.root,now=1500),({},None))
 def test_selection_has_no_capture_or_persistent_settings_side_effect(self):
  before={str(p.relative_to(self.root)):p.read_bytes()for p in self.root.rglob('*')if p.is_file()};m.apply({},root=self.root,now=1500);after={str(p.relative_to(self.root)):p.read_bytes()for p in self.root.rglob('*')if p.is_file()};self.assertEqual(before,after)

if __name__=='__main__':unittest.main(verbosity=2)
