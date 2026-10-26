"""Short authored staging/refusal controls; no Mach-O or helper execution."""
from pathlib import Path
import hashlib,json,sys,tempfile,time,unittest
from unittest.mock import patch
D=Path(__file__).resolve().parents[1];sys.path.insert(0,str(D/'tools'))
import stage_normal_runtime as tool
class Stage(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.evidence=tool.P/'evidence'/('normal-stage-controls-'+str(time.time_ns()));cls.evidence.mkdir(mode=0o700);print('evidence:',cls.evidence)
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=self.evidence);self.root=Path(self.temp.name);self.file=self.root/'owned';self.file.write_text('authored input');self.library=self.root/'libbbgpu.dylib';self.library.write_text('authored identity path, not executable')
 def tearDown(self):self.temp.cleanup()
 def test_exact_fresh_output_root(self):
  path=tool.output_root();self.assertEqual(path.parent,tool.P/'evidence');self.assertTrue(path.name.removeprefix('resource-cpu-execution-').isdigit());self.assertFalse(path.exists())
 def test_wrong_output_name(self):
  with self.assertRaisesRegex(ValueError,'exact private'):tool.output_root(tool.P/'evidence'/'normal-new-root')
 def test_existing_output_preserved(self):
  path=tool.output_root();path.mkdir()
  try:
   with self.assertRaisesRegex(ValueError,'fresh'):tool.output_root(path)
  finally:path.rmdir()
 def test_linked_output_refused(self):
  path=tool.output_root();path.symlink_to(self.root,target_is_directory=True)
  try:
   with self.assertRaisesRegex(ValueError,'fresh'):tool.output_root(path)
  finally:path.unlink()
 def test_regular_non_evidence_refused(self):
  with self.assertRaisesRegex(ValueError,'evidence'):tool.regular(D/'tools/stage_normal_runtime.py')
 def test_regular_link_refused(self):
  link=self.root/'link';link.symlink_to(self.file)
  with self.assertRaisesRegex(ValueError,'canonical'):tool.regular(link)
 def test_receipt_sha_checked(self):
  with self.assertRaisesRegex(ValueError,'SHA mismatch'):tool.checked_hash(self.file,'0'*64,{})
  inputs={};tool.checked_hash(self.file,hashlib.sha256(self.file.read_bytes()).hexdigest(),inputs);self.file.write_text('changed')
  with self.assertRaisesRegex(ValueError,'input changed'):tool.check_inputs(inputs)
 def test_main_compile_output_only_normalization(self):
  a=['clang','-DMODE=1','-o','normal.o','-c','normal.mm'];b=['clang','-DMODE=1','-o','repair.o','-c','repair.mm','-MMD','-MF','repair.d']
  self.assertEqual(tool.normalize_compile(a),tool.normalize_compile(b));b[1]='-DMODE=0';self.assertNotEqual(tool.normalize_compile(a),tool.normalize_compile(b))
 def test_link_only_main_object_may_change(self):
  a=['clang','host.o','normal.o','-o','normal','libbbgpu.dylib'];b=['clang','host.o','repair.o','-o','repair','libbbgpu.dylib']
  self.assertEqual(tool.normalize_link(a,'normal.o'),tool.normalize_link(b,'repair.o'));b.insert(1,'compiler-override.o');self.assertNotEqual(tool.normalize_link(a,'normal.o'),tool.normalize_link(b,'repair.o'))
 def test_unique_actual_gpu_edge(self):
  with patch.object(tool,'dependencies',return_value=[str(self.library),'/usr/lib/libSystem.B.dylib']):self.assertEqual(tool.gpu_edge(self.file,self.library),str(self.library))
  with patch.object(tool,'dependencies',return_value=[str(self.library),str(self.library)]):
   with self.assertRaisesRegex(ValueError,'exactly'):tool.gpu_edge(self.file,self.library)
 def test_wrong_resolved_library_refused(self):
  other=self.root/'libbbgpu.other.dylib';other.write_text('authored')
  with patch.object(tool,'dependencies',return_value=[str(other)]):
   with self.assertRaisesRegex(ValueError,'selected normal'):tool.gpu_edge(self.file,self.library)
 def test_rewrite_only_owned_copy_exact_edge(self):
  with patch.object(tool,'gpu_edge',side_effect=['@rpath/libbbgpu.dylib',str(self.library)]),patch.object(tool.subprocess,'run')as run:
   tool.relocate_copy(self.file,self.library,self.library)
   self.assertEqual(run.call_args_list[0].args[0],['/usr/bin/install_name_tool','-change','@rpath/libbbgpu.dylib',str(self.library),str(self.file)])
   self.assertEqual(run.call_args_list[1].args[0],['/usr/bin/codesign','--force','--sign','-',str(self.file)])
if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stage))
 (Stage.evidence/'result.json').write_text(json.dumps(dict(tests=result.testsRun,passed=result.wasSuccessful(),author_controls=True,native_processes_executed=False,game_started=False,GPU_started=False),indent=2)+'\n')
 raise SystemExit(0 if result.wasSuccessful()else 1)
