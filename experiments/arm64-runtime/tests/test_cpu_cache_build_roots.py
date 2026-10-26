"""Bounded synthetic build-input/path controls, no compiled artifacts or game."""
from pathlib import Path
import hashlib,json,subprocess,sys,tempfile,time,unittest
D=Path(__file__).resolve().parents[1];sys.path.insert(0,str(D/'tools'))
from make_cpu_cache_build import digest_file,write_output
P=Path('/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype')
class Roots(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.evidence=P/'evidence'/('cpu-cache-build-roots-'+str(time.time_ns()));cls.evidence.mkdir(mode=0o700);print('evidence:',cls.evidence)
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=self.evidence);self.root=Path(self.temp.name);self.build=self.root/'build';self.renderer=self.root/'renderer';self.build.mkdir();self.renderer.mkdir();self.inside=self.build/'owned.a';self.inside.write_bytes(b'authored archive identity fixture\n');self.outside=self.root/'outside';self.outside.write_text('preserve')
 def tearDown(self):self.temp.cleanup()
 def test_declared_artifact_roots(self):
  self.assertEqual(digest_file(self.inside,(D,self.build,self.renderer)),hashlib.sha256(self.inside.read_bytes()).hexdigest())
  gpu=self.renderer/'lib.dylib';gpu.write_text('authored library identity fixture')
  self.assertEqual(digest_file(gpu,(D,self.build,self.renderer)),hashlib.sha256(gpu.read_bytes()).hexdigest())
 def test_input_escape(self):
  with self.assertRaisesRegex(ValueError,'escaped'):digest_file(self.outside,(D,self.build,self.renderer))
 def test_input_symlink(self):
  link=self.build/'linked.a';link.symlink_to(self.inside)
  with self.assertRaisesRegex(ValueError,'linked'):digest_file(link,(self.build,))
 def test_linked_parent(self):
  link=self.build/'linked';link.symlink_to(self.root,target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'linked'):digest_file(link/'outside',(self.build,))
  with self.assertRaisesRegex(ValueError,'inside'):write_output(link/'escape.h','bad',self.build)
  self.assertFalse((self.root/'escape.h').exists())
 def test_output_inside_build(self):
  output=self.build/'generated/key.h';write_output(output,'authored output',self.build);self.assertEqual(output.read_text(),'authored output')
  write_output(output,'replacement',self.build);self.assertEqual(output.read_text(),'replacement');self.assertFalse(list(output.parent.glob('*.new')))
 def test_output_escape(self):
  with self.assertRaisesRegex(ValueError,'inside'):write_output(self.outside,'bad',self.build)
  self.assertEqual(self.outside.read_text(),'preserve')
 def test_output_symlink(self):
  link=self.build/'key.h';link.symlink_to(self.outside)
  with self.assertRaisesRegex(ValueError,'linked'):write_output(link,'bad',self.build)
  self.assertEqual(self.outside.read_text(),'preserve')
 def test_cli_independent_roots_and_bad_output(self):
  header=self.build/'generated/key.h';manifest=self.build/'generated/key.json'
  base=[sys.executable,str(D/'tools/make_cpu_cache_build.py'),'--source-root',str(D),'--build-root',str(self.build),'--renderer-root',str(self.renderer),'--runtime-part','authored='+str(self.inside),'--bridge-part','authored='+str(self.inside),'--header',str(header)]
  failed=subprocess.run(base+['--manifest',str(self.outside)],text=True,capture_output=True,timeout=5)
  self.assertNotEqual(failed.returncode,0);self.assertFalse(header.exists());self.assertEqual(self.outside.read_text(),'preserve')
  result=subprocess.run(base+['--manifest',str(manifest)],text=True,capture_output=True,timeout=5)
  self.assertEqual(result.returncode,0,result.stderr);data=json.loads(manifest.read_text());self.assertEqual(data['build_root'],str(self.build));self.assertEqual(data['renderer_root'],str(self.renderer));self.assertIn('BbCacheBuild',header.read_text())
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Roots))
 (Roots.evidence/'result.json').write_text(json.dumps(dict(tests=result.testsRun,passed=result.wasSuccessful(),synthetic_inputs=True,actual_linked_identity_validated=False,game_started=False),indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful()else 1)
