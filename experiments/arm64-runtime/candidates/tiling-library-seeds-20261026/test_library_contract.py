# SPDX-License-Identifier: GPL-2.0-or-later
"""CPU-only author response controls. No Metal/native factories executed."""
from pathlib import Path
import argparse,ast,copy,importlib.util,json,sys,unittest
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,required=True);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
f=a.source_root/'tools/launcher_fixed_helpers.py';spec=importlib.util.spec_from_file_location('helpers',f);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def result():
 items=[dict(kind=k,outcome=1,request_sha256='a'*64,descriptor_sha256='b'*64,inputs_verified=True,native_ready=True,archive_sha256='c'*64,actual_routes=[2,2]if k==10 else[2])for k in(4,5,6,9,10,11,12,13)]
 libraries=[dict(bits=b,macro=macro,texture=False,entry_ready=True,actual_route=2,source_sha256='d'*64,request_key='e'*64,library_sha256='f'*64)for b in(8,16,32,64,96,128)for macro in(False,True)]+[dict(bits=b,macro=True,texture=True,entry_ready=True,actual_route=2,source_sha256='d'*64,request_key='e'*64,library_sha256='f'*64)for b in(32,64)]
 return dict(exit=0,result=dict(status='completed',fixed_helper_seed_schema=2,known=8,ready=8,all_fixed_native_ready=True,items=items,artifact_counts={'compile_attempts':0},tiling_libraries=dict(schema=1,known=14,processed=14,local_ready=14,complete=True,final_psos_prepared=False,items=libraries)))
class Contract(unittest.TestCase):
 def validate(self,r):return m.validate_result(r,'helpers-probe')[1]
 def test_complete22_inputs_not22psos(self):self.assertTrue(self.validate(result()))
 def test_schema1_cannot_silently_succeed(self):
  r=result();r['result']['fixed_helper_seed_schema']=1
  with self.assertRaises(ValueError):self.validate(r)
 def test_library_not_final_pso(self):
  r=result();r['result']['tiling_libraries']['final_psos_prepared']=True
  with self.assertRaises(ValueError):self.validate(r)
 def test_missing_library_refuses_complete(self):
  r=result();l=r['result']['tiling_libraries'];l['items'].pop();l.update(processed=13,local_ready=13)
  with self.assertRaises(ValueError):self.validate(r)
 def test_source_is_collected_not_complete(self):
  r=result();l=r['result']['tiling_libraries'];l['items'][0]['actual_route']=1;l.update(local_ready=13,complete=False)
  self.assertFalse(self.validate(r))
 def test_source_cannot_be_claimed_local_ready(self):
  r=result();r['result']['tiling_libraries']['items'][0]['actual_route']=1
  with self.assertRaises(ValueError):self.validate(r)
 def test_local_requires_actual_library_sha(self):
  r=result();r['result']['tiling_libraries']['items'][0]['library_sha256']=''
  with self.assertRaises(ValueError):self.validate(r)
 def test_duplicate_request_rejected(self):
  r=result();l=r['result']['tiling_libraries'];l['items'][1]=copy.deepcopy(l['items'][0])
  with self.assertRaises(ValueError):self.validate(r)
 def test_texture16_outside_original_domain(self):
  r=result();r['result']['tiling_libraries']['items'][-1]['bits']=16
  with self.assertRaises(ValueError):self.validate(r)
 def test_partial_is_deferred(self):
  r=result();l=r['result']['tiling_libraries'];l['items']=l['items'][:2];l.update(processed=2,local_ready=2,complete=False)
  self.assertFalse(self.validate(r))
 def test_missing_library_contract_rejected(self):
  r=result();del r['result']['tiling_libraries']
  with self.assertRaises(ValueError):self.validate(r)
 def test_source_factory_and_hotpath_controls(self):
  base=a.source_root/'vendor/metal_api/shadps4/video_core/renderer_metal';s=(base/'mtl_runtime_kernels.mm').read_text();collect=s[s.index('HostPso::TilingLibraryResult HostKernels::PrepareTilingLibraries'):s.index('void HostKernels::ConfigureFaultParser')]
  self.assertIn('TilingShader(bits,macro)',collect);self.assertIn('Library(shader,values,false,&function,&item.origin)',collect);self.assertIn('TextureTilingLibrary(shader,bits==64',collect);self.assertNotIn('MakeCompute',collect);self.assertNotIn('MakeRender',collect);self.assertNotIn('CreateTexture',collect);self.assertNotIn('texture_tiling.emplace',collect);self.assertIn('SCOPE_EXIT{warming=old_warming;}',collect);self.assertIn('HostSeverePressure()',collect)
 def test_python_source_syntax(self):
  for n in('launcher_fixed_helpers.py','game_pso_component.py'):ast.parse((a.source_root/'tools'/n).read_text())
if __name__=='__main__':unittest.main(verbosity=2)
