"""Declared main-executable RPATH resolution; no native process is executed."""
from pathlib import Path
from unittest.mock import patch
import tempfile,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import gpu_candidate_identity as tool

class Rpaths(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.target=self.root/'main';self.target.write_bytes(b'author executable');self.lib=self.root/'libbbgpu.dylib';self.lib.write_bytes(b'author library')
 def test_loader_and_absolute_unchanged(self):
  for edge in('@loader_path/libbbgpu.dylib','@executable_path/libbbgpu.dylib',str(self.lib)):
   self.assertEqual(tool.resolve_gpu_edge(self.target,edge),self.lib)
 def test_declared_normal_target_and_duplicate_same_path(self):
  self.assertEqual(tool.resolve_gpu_edge(self.target,'@rpath/libbbgpu.dylib',rpaths=[str(self.root),'@loader_path']),self.lib)
 def test_no_implicit_current_directory_or_environment_search(self):
  with self.assertRaisesRegex(ValueError,'unambiguous'):tool.resolve_gpu_edge(self.target,'@rpath/libbbgpu.dylib',rpaths=[])
 def test_two_actual_libraries_rejected(self):
  other=self.root/'other';other.mkdir();(other/self.lib.name).write_bytes(b'other library')
  with self.assertRaisesRegex(ValueError,'unambiguous'):tool.resolve_gpu_edge(self.target,'@rpath/libbbgpu.dylib',rpaths=[str(self.root),str(other)])
 def test_nested_unknown_rpath_rejected(self):
  with self.assertRaisesRegex(ValueError,'unresolved native'):tool.resolve_gpu_edge(self.target,'@rpath/libbbgpu.dylib',rpaths=['@rpath/other',str(self.root)])
 def test_parent_traversal_rejected(self):
  with self.assertRaisesRegex(ValueError,'suffix'):tool.resolve_gpu_edge(self.target,'@rpath/../libbbgpu.dylib',rpaths=[str(self.root)])
 def test_current_command_parser_and_bad_extent(self):
  with patch.object(tool.subprocess,'check_output',return_value='Load command 2\n cmd LC_RPATH\n cmdsize 32\n path @loader_path (offset 12)\n'):
   self.assertEqual(tool.load_rpaths(self.target),['@loader_path'])
  with patch.object(tool.subprocess,'check_output',return_value='cmd LC_RPATH\ncmdsize 32\ncmd LC_LOAD_DYLIB\n'):
   with self.assertRaisesRegex(ValueError,'incomplete'):tool.load_rpaths(self.target)
 def test_rpath_does_not_bypass_private_root(self):
  outside=self.root/'outside';outside.mkdir();target=outside/'main';target.write_bytes(b'fake')
  with self.assertRaisesRegex(ValueError,'outside private backend'):tool.verify(dict(target=str(target),library=str(self.lib),library_sha256=tool.sha(self.lib)),self.root/'private')

if __name__=='__main__':unittest.main()
