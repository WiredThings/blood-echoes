# SPDX-License-Identifier: GPL-2.0-or-later
"""CPU/source controls only; no C++/GPU execution."""
from pathlib import Path
import argparse,ast,os,subprocess,sys,tempfile,unittest
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,required=True);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
source=a.source_root/'tools/game_pso_component.py';tree=ast.parse(source.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='fixed_helper_capability')
def need(value,why):
 if not value:raise ValueError(why)
ns=dict(Path=Path,os=os,tempfile=tempfile,subprocess=subprocess,need=need);exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
class Delta(unittest.TestCase):
 def cap(self,code,data):
  with tempfile.TemporaryDirectory()as d:
   root=Path(d);(root/'state').mkdir();calls=[]
   def runner(command,**kwargs):calls.append((command,kwargs));return subprocess.CompletedProcess(command,code,data)
   result=ns['fixed_helper_capability']('/author/worker',root,runner=runner)
   self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],['/author/worker','--fixed-helper-seed-schema']);self.assertEqual(calls[0][1]['timeout'],10);self.assertEqual(list((root/'state').iterdir()),[])
   return result
 def test_new2_available(self):self.assertEqual(self.cap(0,b'FIXED_HELPER_CAPABILITY 2\n'),2)
 def test_old1_unavailable_not_misread(self):self.assertEqual(self.cap(0,b'FIXED_HELPER_CAPABILITY 1\n'),0)
 def test_old64_unavailable(self):self.assertEqual(self.cap(64,b''),0)
 def test_bad_schema_rejected(self):
  with self.assertRaises(ValueError):self.cap(0,b'FIXED_HELPER_CAPABILITY 3\n')
 def test_failed_query_rejected(self):
  with self.assertRaises(ValueError):self.cap(1,b'FIXED_HELPER_CAPABILITY 2\n')
 def test_extra_line_rejected(self):
  with self.assertRaises(ValueError):self.cap(0,b'FIXED_HELPER_CAPABILITY 2\nextra\n')
 def test_source_short_circuit_precedes_descriptor_or_factory(self):
  f=a.source_root/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_runtime_kernels.mm';s=f.read_text();start=s.index('id<MTLComputePipelineState> HostKernels::TextureTiling(');end=s.index('id<MTLLibrary> HostKernels::TextureTilingLibrary',start);body=s[start:end]
  call=body.index('auto library=TextureTilingLibrary(');guard=body.index('if(!library){if(!warming)texture_tiling.emplace(key,nil);return nil;}');descriptor=body.index('Descriptor(invocation)');factory=body.index('MakeCompute(')
  self.assertLess(call,guard);self.assertLess(guard,descriptor);self.assertLess(guard,factory)
  self.assertIn('auto pipeline=function?MakeCompute',body)
  shared=s[end:s.index('HostPso::TilingLibraryResult',end)];self.assertIn('if(source.empty())return nil;',shared)
 def test_supported_source_still_uses_same_shared_factory(self):
  s=(a.source_root/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_runtime_kernels.mm').read_text();self.assertIn('const auto source=TextureTilingSource(shader.msl,half);',s);self.assertIn('LocalLibrary::Build(dev.device,@(source.c_str()),CompileOptions()',s)
if __name__=='__main__':unittest.main(verbosity=2)
