"""End-to-end authored cache reuse and legacy parser migration controls."""
from pathlib import Path
import hashlib,json,os,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_launcher_source_view as source_test
import test_launcher_game_catalog as catalog_test
import launcher_source_view as view
import launcher_game_catalog as catalog

class Views(unittest.TestCase):
 setUp=source_test.SourceView.setUp
 tearDown=source_test.SourceView.tearDown
 module=source_test.SourceView.module
 actual=source_test.SourceView.actual
 clone=source_test.SourceView.clone
 prepare=source_test.SourceView.prepare
 def remove_registered_hint(self):
  # Exercise the lower incremental-hash fallback in this suite. A valid upper
  # registration legitimately need not read an unused legacy speed hint.
  for p in (self.gpu/'automatic-game-inputs/source-views').glob('selection-*.json'):p.unlink()
 def test_only_changed_selected_source_is_read(self):
  first=self.prepare();p=self.game/'eboot.bin';old=p.stat();p.write_bytes(b'Z'*old.st_size);os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns));calls=[];original=view.file_sha
  def count(path,*a,**kw):
   if Path(path).is_relative_to(self.game):calls.append(Path(path))
   return original(path,*a,**kw)
  with patch.object(view,'file_sha',side_effect=count):second=self.prepare()
  self.assertEqual(calls,[p]);self.assertNotEqual(first['game_root'],second['game_root']);self.assertEqual(second['report']['source_hashes']['hashed_files'],1)
 def test_corrupt_source_hint_falls_back_but_keeps_valid_snapshot(self):
  first=self.prepare();self.remove_registered_hint();hint=next((self.gpu/'automatic-game-inputs/source-views').glob('origin-metadata-*.json'));hint.write_text('broken');hint.chmod(0o600);second=self.prepare();self.assertEqual(first['game_root'],second['game_root']);self.assertEqual(second['report']['source_hashes']['base_receipt'],'invalid');self.assertEqual(second['report']['source_hashes']['hashed_files'],2)
 def test_corrupt_snapshot_hint_full_validates_snapshot(self):
  first=self.prepare();self.remove_registered_hint();hint=next((self.gpu/'automatic-game-inputs/source-views').glob('snapshot-metadata-*.json'));hint.write_text('broken');hint.chmod(0o600);second=self.prepare();self.assertEqual(first['game_root'],second['game_root']);self.assertEqual(second['report']['snapshot_hashes']['hashed_files'],2);self.assertEqual(second['report']['source_hashes']['hashed_files'],0)
 def test_legacy_snapshot_without_hints_enrolls_without_reclone(self):
  first=self.prepare();self.remove_registered_hint();root=first['game_root'];ready=(root.parent/'READY.json').read_bytes();count=self.clones
  for p in (self.gpu/'automatic-game-inputs/source-views').glob('*metadata-*.json'):p.unlink()
  second=self.prepare();self.assertEqual(second['game_root'],root);self.assertEqual(self.clones,count);self.assertEqual((root.parent/'READY.json').read_bytes(),ready);self.assertEqual(second['report']['source_hashes']['hashed_files'],2)
 def test_content_identical_rewrite_does_not_rebuild_snapshot(self):
  first=self.prepare();p=self.game/'eboot.bin';p.write_bytes(p.read_bytes());second=self.prepare();self.assertEqual(second['game_root'],first['game_root']);self.assertEqual(second['report']['source_hashes']['hashed_files'],1)

class Catalogues(unittest.TestCase):
 setUp=catalog_test.AutomaticCatalogue.setUp
 tearDown=catalog_test.AutomaticCatalogue.tearDown
 runner=catalog_test.AutomaticCatalogue.runner
 prepare=catalog_test.AutomaticCatalogue.prepare
 def test_warm_source_content_is_never_opened(self):
  first=self.prepare();original=catalog.file_sha
  def no_game(path,*a,**kw):
   if Path(path).is_relative_to(self.game):self.fail('warm catalogue read game content')
   return original(path,*a,**kw)
  with patch.object(catalog,'file_sha',side_effect=no_game):second=self.prepare()
  self.assertEqual(first['generation'],second['generation']);self.assertEqual(second['report']['source_hashes']['hashed_bytes'],0)
 def test_corrupt_inventory_hint_rechecks_without_reindexing(self):
  first=self.prepare();p=next((self.gpu/'automatic-game-inputs').glob('catalogue-source-metadata-*.json'));p.write_text('broken');p.chmod(0o600);second=self.prepare();self.assertEqual(first['generation'],second['generation']);self.assertEqual(len(self.calls),1);self.assertEqual(second['report']['source_hashes']['hashed_files'],1)
 def test_reading_changes_during_validation_cannot_reuse(self):
  self.prepare();p=self.game/'game.bin';original=catalog.read_ready
  def change(*a,**kw):
   r=original(*a,**kw);p.write_bytes(b'changed late');return r
  with patch.object(catalog,'read_ready',side_effect=change),self.assertRaisesRegex(ValueError,'source contents changed'):self.prepare()
 def test_historical_parser_is_not_silently_declared_equal_after_incremental_reader_changes(self):
  native=Path(catalog.__file__).parents[1]/'vendor/metal_api/shadps4'
  self.assertEqual(catalog.legacy_scanners(native),[])
 def test_authored_pinned_same_parser_migration_keeps_existing_generation(self):
  # Exercise the migration protocol with an explicitly authored pin. This does
  # NOT change the production historical pin or claim the new parser is old.
  native=Path(catalog.__file__).parents[1]/'vendor/metal_api/shadps4'
  body=catalog.scanner_body_digest(Path(catalog.__file__).read_text())
  with patch.object(catalog,'LEGACY_SCANNER_BODY',body):
   legacy=catalog.legacy_scanners(native);self.assertEqual(len(legacy),1)
   with patch.object(catalog,'scanner_identity',return_value=legacy[0]):first=catalog.prepare(project=self.project,gpu_root=self.gpu,game_root=self.game,source_root=native,cohort='a'*64,runner=self.runner)
   before=(first['generation']/'READY.json').read_bytes()
   second=catalog.prepare(project=self.project,gpu_root=self.gpu,game_root=self.game,source_root=native,cohort='a'*64,runner=self.runner)
  self.assertEqual(second['generation'],first['generation']);self.assertEqual(len(self.calls),1);self.assertEqual(second['report']['scanner_identity'],legacy[0]);self.assertNotEqual(second['report']['scheduler_identity'],legacy[0]);self.assertEqual((first['generation']/'READY.json').read_bytes(),before)
 def test_actual_parser_body_change_disables_legacy(self):
  import ast
  tree=ast.parse(Path(catalog.__file__).read_text())
  next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='build_static').body.append(ast.Expr(value=ast.Constant(value='authored changed decoder')))
  with patch.object(catalog.ast,'parse',return_value=tree):self.assertEqual(catalog.legacy_scanners(self.source),[])
 def test_changed_runtime_enum_bytes_cannot_reuse_old_index(self):
  native=Path(catalog.__file__).parents[1]/'vendor/metal_api/shadps4'
  enum=self.source/'shader_recompiler/runtime_info.h';enum.parent.mkdir();enum.write_bytes((native/'shader_recompiler/runtime_info.h').read_bytes())
  first=catalog.prepare(project=self.project,gpu_root=self.gpu,game_root=self.game,source_root=self.source,cohort='a'*64,runner=self.runner)
  enum.write_bytes(enum.read_bytes()+b'\n// authored changed enum input\n')
  second=catalog.prepare(project=self.project,gpu_root=self.gpu,game_root=self.game,source_root=self.source,cohort='a'*64,runner=self.runner)
  self.assertNotEqual(first['generation'],second['generation']);self.assertEqual(len(self.calls),2);self.assertTrue(first['index'].exists())
 def test_changed_scanner_body_cannot_authorize_legacy(self):
  with patch.object(catalog,'LEGACY_SCANNER_BODY','0'*64):self.assertEqual(catalog.legacy_scanners(self.source),[])

if __name__=='__main__':unittest.main()
