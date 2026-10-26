"""Authored source bytes exercise original bounded package and owned-input readers."""
from pathlib import Path
import copy,hashlib,json,sys,tempfile,unittest
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'tools')]
from test_game_shader_catalog import shader,binder,wrap,sfo
from test_pipeline_input_completion import fixture,layout
from game_shader_catalog import game_identity
from game_pso_candidates import cache_records
from game_module_inputs import source_code,freeze_module
from freeze_game_modules import freeze_batch

class ModuleInputs(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name).resolve();self.game=self.p/'game';(self.game/'sce_sys').mkdir(parents=True);(self.game/'sce_sys/param.sfo').write_bytes(sfo());self.raw=shader();package=wrap(binder(self.raw));self.rel='authored.shaderbnd.dcx';(self.game/self.rel).write_bytes(package)
  self.catalog=dict(game_root=str(self.game),game_identity=game_identity(sfo()),packages=[dict(path=self.rel,kind='shader',debug=False,sha256=hashlib.sha256(package).hexdigest())]);self.identity=dict(program_hash='0000000000001234',hardware_stage='compute',hardware_stage_value=6,code_sha256=hashlib.sha256(self.raw[:16]).hexdigest());self.ev=dict(package=self.rel,entry='Authored.vpo',source_sha256=hashlib.sha256(self.raw).hexdigest(),code_offset=0)
 def tearDown(self):self.tmp.cleanup()
 def source(self,**kw):return source_code(kw.get('root',self.game),kw.get('catalog',self.catalog),kw.get('identity',self.identity),kw.get('evidence',self.ev))
 def unit(self):
  cache=fixture(self.p/'cache');return next(u for u in cache_records(cache,layout())if u['record_type']=='offline-pso-input')
 def freeze(self,unit=None,output=None):
  u=unit or self.unit();return freeze_module(stage=u['stages'][0],profile_reference=u['profile']['reference'],layout=layout(),game_root=self.game,catalog=self.catalog,static_identity=self.identity,evidence=self.ev,output=output or self.p/'output')
 def test_exact_independently_hashed_source_and_no_source_write(self):
  before={p:p.read_bytes()for p in self.game.rglob('*')if p.is_file()};code,proof=self.source();self.assertEqual(code,self.raw[:16]);self.assertTrue(proof['code_prefix_verified']);self.assertEqual(proof['sfo_sha256'],self.catalog['game_identity']['sfo_sha256']);self.assertEqual(before,{p:p.read_bytes()for p in before})
 def test_container_digest_changed(self):
  (self.game/self.rel).write_bytes(b'changed')
  with self.assertRaises(ValueError):self.source()
 def test_entry_digest_changed(self):
  e=dict(self.ev,source_sha256='0'*64)
  with self.assertRaisesRegex(ValueError,'static entry'):self.source(evidence=e)
 def test_code_digest_or_offset_never_guessed(self):
  for e,i in [(dict(self.ev,code_offset=4),self.identity),(self.ev,dict(self.identity,code_sha256='f'*64))]:
   with self.assertRaisesRegex(ValueError,'unique independent'):self.source(evidence=e,identity=i)
 def test_game_identity_changed(self):
  c=copy.deepcopy(self.catalog);c['game_identity']['sfo_sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'fixed game identity'):self.source(catalog=c)
 def test_game_root_symlink_refused(self):
  alias=self.p/'alias';alias.symlink_to(self.game)
  with self.assertRaisesRegex(ValueError,'canonical fixed'):self.source(root=alias)
 def test_traversal_package_refused(self):
  c=copy.deepcopy(self.catalog);c['packages'][0]['path']='../authored.shaderbnd.dcx';e=dict(self.ev,package='../authored.shaderbnd.dcx')
  with self.assertRaisesRegex(ValueError,'relative package'):self.source(catalog=c,evidence=e)
 def test_frozen_input_forwards_only_owned_code_spec_flat_walker(self):
  p=self.freeze();self.assertEqual(p['code']['bytes'],16);self.assertEqual(p['flat_dwords'],16);self.assertEqual(p['source_metadata']['opaque_Info_bytes_forwarded'],0);self.assertFalse(p['native_info_srt_allowed']);self.assertFalse(p['program_base']['known']);self.assertEqual(p['program_base']['required_gate'],'reject-offset-by-program-base');self.assertEqual((self.p/'output/code.bin').read_bytes(),self.raw[:16]);self.assertEqual(p['profile']['bytes'],24)
 def test_existing_output_not_overwritten(self):
  out=self.p/'output';out.mkdir();(out/'keep').write_text('keep')
  with self.assertRaisesRegex(ValueError,'new private'):self.freeze(output=out)
  self.assertEqual((out/'keep').read_text(),'keep')
 def test_static_hardware_stage_mismatch_refused(self):
  self.identity['hardware_stage']='vertex'
  with self.assertRaisesRegex(ValueError,'static/observed'):self.freeze()
 def test_batch_refuses_outside_evidence_and_oversized_limits(self):
  (self.p/'evidence').mkdir()
  for out,limits in [(self.p/'bad',{'compute':1}),(self.p/'evidence/out',{'compute':65})]:
   with self.assertRaises(ValueError):freeze_batch(project=self.p,templates=self.p/'absent',static_index=self.p/'absent',catalog_path=self.p/'absent',layout=layout(),output=out,limits=limits)
if __name__=='__main__':unittest.main(verbosity=2)
