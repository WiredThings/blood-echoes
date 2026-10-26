"""Exact edge relocation controls; no native editing tools are run."""
from pathlib import Path
from unittest.mock import patch
import os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import package_local_arm64_qa as tool

class Relocation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  p=Path(self.tmp.name).resolve();self.exe=p/'copy/main';self.old=p/'original/libbbgpu.dylib';self.new=p/'copy/library/libbbgpu.dylib'
  for f in(self.exe,self.old,self.new):f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'author fixture')
 def relocate(self,edge,*,resolved_old=None,extra=()):
  target='@executable_path/'+os.path.relpath(self.new,self.exe.parent);commands=[];links=[edge,*extra]
  def run(command,**kwargs):
   commands.append(command)
   if '-change'in command:self.assertEqual(command[command.index('-change')+1],edge);links[:]=[target]
  with patch.object(tool,'deps',side_effect=lambda p:list(links)),patch.object(tool.subprocess,'run',side_effect=run),patch('gpu_candidate_identity.load_rpaths',return_value=[str((resolved_old or self.old).parent)]):
   tool.freeze_consumer(self.exe,self.old,self.new)
  return commands,links,target
 def test_legacy_absolute_exact_edge(self):
  commands,links,target=self.relocate(str(self.old));self.assertEqual(links,[target]);self.assertEqual(len(commands),2)
 def test_normal_rpath_exact_edge(self):
  commands,links,target=self.relocate('@rpath/libbbgpu.dylib');self.assertEqual(commands[0][1:4],['-change','@rpath/libbbgpu.dylib',target]);self.assertEqual(links,[target])
 def test_other_actual_library_not_rewritten_by_basename(self):
  wrong=self.old.parent.parent/'wrong/libbbgpu.dylib';wrong.parent.mkdir();wrong.write_bytes(b'wrong')
  with self.assertRaisesRegex(AssertionError,'admitted'):self.relocate('@rpath/libbbgpu.dylib',resolved_old=wrong)
 def test_multiple_gpu_edges_rejected_before_edit(self):
  with self.assertRaisesRegex(AssertionError,'exactly one'):self.relocate(str(self.old),extra=['/unknown/libbbgpu-other.dylib'])

if __name__=='__main__':unittest.main()
