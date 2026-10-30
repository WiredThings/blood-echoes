from pathlib import Path
import copy,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from bbcpu_static_library import snapshot,digest,rebind_identical_import_source

class ImportReuse(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir='/private/tmp');self.root=Path(self.temp.name)
  self.source=self.root/'real-import-source';self.tool=self.root/'generator';self.source.write_bytes(b'actual A');self.tool.write_bytes(b'trusted tool')
  rows=[snapshot(self.source),snapshot(self.tool)];self.saved=dict(selected_inputs=rows,pins=rows)
  self.input=dict(path=str(self.source),sha256=digest(self.source))
 def tearDown(self):self.temp.cleanup()
 def now(self):return[snapshot(self.source),snapshot(self.tool)]
 def test_restore_equal_bytes_rebinds_only_source_file_identity(self):
  self.source.write_bytes(b'actual B');self.source.write_bytes(b'actual A')
  new=rebind_identical_import_source(self.saved,self.now(),self.input)
  self.assertIsNotNone(new);self.assertEqual(new['pins'],self.now());self.assertNotEqual(self.saved['pins'],new['pins'])
 def test_changed_bytes_refused(self):
  self.source.write_bytes(b'actual B');self.assertIsNone(rebind_identical_import_source(self.saved,self.now(),self.input))
 def test_changed_generator_even_with_equal_source_refused(self):
  self.tool.write_bytes(b'changed');self.assertIsNone(rebind_identical_import_source(self.saved,self.now(),self.input))
 def test_changed_dependency_outside_selected_set_refused(self):
  dependency=self.root/'abi.h';dependency.write_bytes(b'old');self.saved['pins']=[*self.saved['pins'],snapshot(dependency)];dependency.write_bytes(b'new')
  self.assertIsNone(rebind_identical_import_source(self.saved,self.now(),self.input))
 def test_path_or_expected_digest_changes_refused(self):
  other=self.root/'other';other.write_bytes(b'actual A')
  self.assertIsNone(rebind_identical_import_source(self.saved,[snapshot(other),snapshot(self.tool)],self.input))
  self.assertIsNone(rebind_identical_import_source(self.saved,self.now(),dict(self.input,sha256='0'*64)))
if __name__=='__main__':unittest.main()
