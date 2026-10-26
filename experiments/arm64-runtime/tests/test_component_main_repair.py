"""Authored repair negatives; these do not establish native/GPU use."""
from pathlib import Path
import json,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import game_pso_component as c

class MainRepair(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.e=self.root/'evidence';self.e.mkdir()
  def file(name,data=b'authored fixture only'):
   p=self.e/name;p.write_bytes(data);return str(p)
  self.base=dict(schema=1,kind='blood-echoes-game-pso-component',coherent_renderer=True,
    diagnostic_only=False,coherent_source_identity='a'*64)
  for name in ('library','worker','layout'):
   self.base[name]=file(name);self.base[name+'_sha256']=c.sha(Path(self.base[name]))
  self.base['source_root']=str(self.e)
  result=file('base-result.json');new=file('module');main=file('main.mm');header=file('header.h')
  self.base['coherent_build_result_sha256']=c.sha(Path(result))
  self.repair=dict(schema=1,kind='coherent-game-module-main-repair',
   targeted_compiler_overrides=False,shared_abi_headers_unchanged=True,
   base_result=result,base_result_sha256=c.sha(Path(result)),source_identity='a'*64,
   library=self.base['library'],library_sha256=self.base['library_sha256'],
   module_consumer=new,module_consumer_sha256=c.sha(Path(new)),
   main_source=main,main_source_sha256=c.sha(Path(main)),
   compiled_header_sha256={header:c.sha(Path(header))})
  self.bp=self.e/'component.json';self.rp=self.e/'repair.json';self.out=self.e/'new.json'
 def bind(self,edge=None):
  self.bp.write_text(json.dumps(self.base));self.rp.write_text(json.dumps(self.repair))
  edge=edge or {k:self.base[k]for k in ('library','library_sha256')}
  with patch.object(c,'load_canonical_layout',return_value={}),patch('warm_archive_manifest.resolve_gpu_identity',return_value=edge):
   return c.bind_module_repair(self.bp,self.rp,admission_root=self.root,output=self.out)
 def test_same_dso_preserves_base(self):
  r=self.bind();self.assertEqual(r['module_consumer'],self.repair['module_consumer'])
  self.assertEqual(json.loads(self.bp.read_text()),self.base)
 def test_refuses_renderer_replacement(self):
  self.repair['library_sha256']='b'*64
  with self.assertRaisesRegex(ValueError,'replace the renderer'):self.bind()
  self.assertFalse(self.out.exists())
 def test_refuses_modified_header(self):
  Path(next(iter(self.repair['compiled_header_sha256']))).write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'header changed'):self.bind()
 def test_refuses_changed_main(self):
  Path(self.repair['main_source']).write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'main source changed'):self.bind()
 def test_refuses_actual_load_edge_mismatch(self):
  with self.assertRaisesRegex(ValueError,'actual GPU edge'):self.bind(dict(library='wrong',library_sha256='a'*64))
 def test_refuses_overwrite(self):
  self.out.write_bytes(b'keep')
  with self.assertRaisesRegex(ValueError,'fresh isolated'):self.bind()
  self.assertEqual(self.out.read_bytes(),b'keep')
 def test_refuses_targeted_compiler_override(self):
  self.repair['targeted_compiler_overrides']=True
  with self.assertRaisesRegex(ValueError,'shared-ABI'):self.bind()

if __name__=='__main__':unittest.main()
