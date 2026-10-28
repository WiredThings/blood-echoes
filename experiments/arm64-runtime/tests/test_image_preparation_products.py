"""Scheduling tests using real owned files/APFS clones, authored image builder.

These do not claim authored bytes are executable images or game acceptance.
"""
from pathlib import Path
import hashlib,io,json,os,sys,tempfile,unittest
from contextlib import redirect_stdout
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import image_preparation_products as m

@unittest.skipUnless(sys.platform=='darwin','requires actual Darwin/APFS clonefile semantics')
class Products(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.stage=self.root/'code';self.game=self.root/'game';self.out=self.root/'out';self.work=self.root/'work';self.count=0
  for p in(self.stage,self.game,self.out,self.work):p.mkdir(mode=0o700)
  for name in m.INPUTS:
   p=self.game/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(('author input '+name).encode())
  for name in m.RULES:
   p=self.stage/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(('author rule '+name).encode())
  (self.game/'dvdroot_ps4/shader').mkdir(parents=True)
  self.q=patch.object(m.journal,'query',return_value={'schema':1,'complete':True});self.u=patch.object(m.journal,'unchanged',return_value=True);self.q.start();self.u.start()
 def tearDown(self):self.u.stop();self.q.stop();self.tmp.cleanup()
 def build(self,game,out):
  self.count+=1;source=b''.join((game/n).read_bytes()for n in m.INPUTS)
  for name in m.OUTPUTS:(out/name).write_bytes(hashlib.sha256(source+name.encode()).digest())
 def run_case(self,env=None):
  with redirect_stdout(io.StringIO()):return m.prepare_images(stage=self.stage,game=self.game,out=self.out,work=self.work,environment=env or {'BB_SKIP_GAME_CHECK':'1'},build=self.build)
 def test_unchanged_reuses_without_relink_or_output_copy(self):
  self.run_case();before={n:(self.out/n).stat().st_ino for n in m.OUTPUTS};row=self.run_case();self.assertEqual(self.count,1);self.assertEqual(row['status'],'reused');self.assertEqual(row['restored_outputs'],0);self.assertEqual(row['source_hashes']['hashed_bytes'],0);self.assertEqual(row['product_hashes']['hashed_bytes'],0);self.assertEqual(before,{n:(self.out/n).stat().st_ino for n in m.OUTPUTS})
 def test_unrelated_mod_and_ui_selection_preserve_product(self):
  first=self.run_case();row=self.run_case({'BB_SKIP_GAME_CHECK':'1','BB_MODS_ENABLED':'0','BB_LANGUAGE':'11','BB_LOG':'1'});self.assertEqual(self.count,1);self.assertEqual(first['identity'],row['identity'])
 def test_actual_image_change_and_back_reuse_original(self):
  self.run_case();p=self.game/'eboot.bin';original=p.read_bytes();p.write_bytes(b'changed outside UI')
  with patch.object(m.journal,'unchanged',return_value=False):self.run_case()
  self.assertEqual(self.count,2);p.write_bytes(original)
  with patch.object(m.journal,'unchanged',return_value=False):row=self.run_case()
  self.assertEqual(self.count,2);self.assertEqual(row['status'],'reused')
 def test_damage_one_generation_keeps_old_and_rebuilds_only_images(self):
  self.run_case();root=self.work/'image-products';old=[p for p in root.iterdir()if p.is_dir()and p.name!='registrations'][0];old.chmod(0o700);(old/'boot-linked.bin').chmod(0o600);(old/'boot-linked.bin').write_bytes(b'bad');old.chmod(0o500)
  with patch.object(m.journal,'unchanged',return_value=False):row=self.run_case()
  self.assertEqual(self.count,2);self.assertEqual(row['status'],'built');self.assertEqual((old/'boot-linked.bin').read_bytes(),b'bad')
 def test_output_change_is_restored_from_existing_valid_product(self):
  self.run_case();(self.out/'boot-linked.bin').write_bytes(b'changed generated output');row=self.run_case();self.assertEqual(self.count,1);self.assertEqual(row['restored_outputs'],1)
 def test_lost_history_rehashes_but_does_not_relink_valid_products(self):
  self.run_case()
  with patch.object(m.journal,'unchanged',return_value=False):row=self.run_case()
  self.assertEqual(self.count,1);self.assertGreater(row['product_hashes']['hashed_bytes'],0)
 def test_parser_change_changes_only_image_identity(self):
  self.run_case();(self.stage/'scripts/link_modules.py').write_bytes(b'changed link rules');row=self.run_case();self.assertEqual(self.count,2);self.assertEqual(row['status'],'built')
 def test_bad_output_target_and_failed_build_not_blessed(self):
  self.run_case();p=self.out/'entry.bin';p.unlink();p.symlink_to(self.out/'boot.bin')
  with self.assertRaises(ValueError):self.run_case()
 def test_shader_check_is_not_silently_skipped(self):
  from types import SimpleNamespace
  fake=SimpleNamespace(__file__=str(self.stage/'scripts/game_check.py'),problem=lambda game:('damaged',),explain=lambda *args:'original damaged-shader refusal')
  with patch.dict(sys.modules,game_check=fake),self.assertRaises(m.OriginalFailure)as result:self.run_case({'BB_SKIP_GAME_CHECK':'0'})
  self.assertEqual(result.exception.code,2)
 def test_original_game_and_link_errors_keep_fatal_statuses(self):
  from types import SimpleNamespace
  class Unsupported(Exception):pass
  def game(*args):raise Unsupported('authored unsupported game')
  def link(*args):raise ValueError('authored bad relocation')
  first=SimpleNamespace(prepare=game,GameCheckError=Unsupported);other=SimpleNamespace(link=link)
  with self.assertRaises(m.OriginalFailure)as error:m.original_algorithms(self.game,self.out,first,other,other)
  self.assertEqual(error.exception.code,2)
  first.prepare=lambda *args:None
  with self.assertRaises(m.OriginalFailure)as error:m.original_algorithms(self.game,self.out,first,other,other)
  self.assertEqual(error.exception.code,1)

if __name__=='__main__':unittest.main()
