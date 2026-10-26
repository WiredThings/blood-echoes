"""Coherent build source contracts; no compiler/GPU/private fixtures."""
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import build_game_preparation_component as b
class BuilderContracts(unittest.TestCase):
 def tree(self,tmp):
  root=Path(tmp).resolve()
  for name in b.ROOTS:(root/name).mkdir(parents=True)
  return root
 def test_finder_duplicates_are_filtered_without_deletion(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=self.tree(tmp);a=root/'src/a.cpp';a.write_text('original');copy=root/'src/a 2.cpp';copy.write_text('different stale');closure=b.source_closure(root)
   self.assertIn('src/a.cpp',closure['files']);self.assertNotIn('src/a 2.cpp',closure['files']);self.assertIn('src/a 2.cpp',closure['excluded']);self.assertEqual(copy.read_text(),'different stale')
 def test_name_with_two_without_real_counterpart_is_not_discarded(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=self.tree(tmp);p=root/'src/legitimate 2.h';p.write_text('own');self.assertIn('src/legitimate 2.h',b.source_closure(root)['files'])
 def test_immutable_freeze_checks_hashes_and_preserves_relative_sources(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=self.tree(tmp);(root/'src/a.cpp').write_text('a');snapshot=root/'snapshot';plan=dict(source_root=str(root),snapshot=str(snapshot),source_closure=b.source_closure(root));b.freeze_sources(plan);self.assertEqual((snapshot/'src/a.cpp').read_text(),'a')
 def test_changed_source_after_plan_refused_before_snapshot(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=self.tree(tmp);p=root/'src/a.cpp';p.write_text('a');snapshot=root/'snapshot';plan=dict(source_root=str(root),snapshot=str(snapshot),source_closure=b.source_closure(root));p.write_text('b')
   with self.assertRaisesRegex(ValueError,'source changed after plan'):b.freeze_sources(plan)
   self.assertFalse(snapshot.exists())
 def test_symlink_sources_never_enter_owned_snapshot(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=self.tree(tmp);(root/'src/real.h').write_text('a');(root/'src/link.h').symlink_to('real.h');closure=b.source_closure(root);self.assertIn('src/link.h',closure['excluded']);self.assertNotIn('src/link.h',closure['files'])
 def test_commands_remap_source_but_preserve_prepared_dependency_edges(self):
  command=['c++','-I/old/vendor/metal_api/shim','-I/private/tmp/prepared/include','-DBB_FONT_PATH="/old/vendor/metal_api/font.ttf"','/old/vendor/metal_api/a.cpp'];result=b.rewrite(command,'/old',Path('/owned/source'));self.assertEqual(result[1],'-I/owned/source/vendor/metal_api/shim');self.assertEqual(result[2],command[2]);self.assertEqual(result[3],command[3]);self.assertEqual(result[-1],'/owned/source/vendor/metal_api/a.cpp')
if __name__=='__main__':unittest.main(verbosity=2)
