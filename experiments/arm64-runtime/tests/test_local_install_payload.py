"""Author-created packaging inputs only; no native code or installed app read."""
from pathlib import Path
import os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_local_install import check_relative_payload,disjoint_output

class PayloadTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.app=self.root/'Synthetic.app';self.app.mkdir()
  (self.app/'code').write_text('synthetic authored code')
 def tearDown(self):self.temp.cleanup()
 def test_internal_relative_alias_is_retained(self):
  (self.app/'alias').symlink_to('code')
  rows=check_relative_payload(self.app)
  self.assertEqual(len(rows),2);self.assertEqual(os.readlink(self.app/'alias'),'code')
 def test_staging_never_writes_inside_or_above_sources(self):
  sources=[self.root/'QA.app',self.root/'Baseline.app',self.root/'Products']
  for source in sources:
   with self.assertRaises(ValueError):disjoint_output(source/'nested/New.app',sources)
   with self.assertRaises(ValueError):disjoint_output(source,sources)
  with self.assertRaises(ValueError):disjoint_output(self.root,sources)
  self.assertIsNone(disjoint_output(self.root/'independent/New.app',sources))
 def test_external_or_absolute_alias_rejected(self):
  (self.root/'outside').write_text('outside')
  for target in('../outside',str(self.root/'outside')):
   link=self.app/'alias';link.symlink_to(target)
   with self.assertRaises(ValueError):check_relative_payload(self.app)
   link.unlink()
 def test_progress_and_game_data_rejected_without_changing_them(self):
  for relative in('savedata/progress','quicksaves/current.json','asset.dcx','eboot.bin'):
   path=self.app/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'author payload')
   with self.assertRaises(AssertionError):check_relative_payload(self.app)
   self.assertEqual(path.read_bytes(),b'author payload');path.unlink()
   if path.parent!=self.app:path.parent.rmdir()
 def test_private_manifests_and_automation_rejected(self):
  for relative in('ARM64-QA-MANIFEST.json','DEVELOPMENT-WORLD-WARPER.json','tools/macos/benchmark.py','Contents/Resources/bbport/tools/macos/benchmark.py'):
   path=self.app/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
   with self.assertRaises(ValueError):check_relative_payload(self.app)
   path.unlink()
 def test_development_probe_marker_across_read_boundary_is_rejected(self):
  p=self.app/'Contents/Resources/bbport/bin/bb-probe-metal';p.parent.mkdir(parents=True)
  p.write_bytes(b'x'*65530+b'BB_DEVELOPMENT_WORLD_WARPER_V1'+b'author data')
  with self.assertRaisesRegex(ValueError,'World Warper'):check_relative_payload(self.app)
  p.write_bytes(b'ordinary authored probe')
  self.assertTrue(check_relative_payload(self.app))

if __name__=='__main__':unittest.main()
