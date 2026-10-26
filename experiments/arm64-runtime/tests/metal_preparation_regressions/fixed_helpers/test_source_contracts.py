# SPDX-License-Identifier: GPL-2.0-or-later
"""Source-bound invariants supplement CPU control flow, not a GPU proof."""
from pathlib import Path
import argparse,ast,sys,unittest
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1]);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left];R=a.source_root;M=R/'vendor/metal_api/shadps4/video_core/renderer_metal'
class SourceContracts(unittest.TestCase):
 def test_scalar_cli_before_arguments_storage_or_device(self):
  s=(R/'src/game_pso_preparer.mm').read_text();main=s[s.index('int main('):];q=main[main.index(' if(argc==2'):main.index(' // This executable')];self.assertIn('--fixed-helper-seed-schema',q);self.assertIn('FixedSeedSchema',q);self.assertNotIn('Metal::Device',q);self.assertNotIn('Storage::',q);self.assertLess(main.index('--fixed-helper-seed-schema'),main.index('if(argc!=4)'))
 def test_cold_route_values_not_origin_references_after_api(self):
  s=(M/'mtl_host_pso_prepare.inc').read_text()
  for begin,end in [('id<MTLComputePipelineState> HostKernels::MakeCompute','id<MTLRenderPipelineState> HostKernels::MakeRender'),('id<MTLRenderPipelineState> HostKernels::MakeRender','bool HostKernels::Replay')]:
   body=s[s.index(begin):s.index(end,s.index(begin)+1)];cold=body.index('pso_cache->CreateArtifactPipeline');self.assertLess(body.index('actual_routes[n]=origins[n].route'),cold);self.assertNotIn('origins',body[cold:]);self.assertGreater(body.index('artifact_outcome.actual_routes=actual_routes'),cold)
 def test_original_factory_replay_and_no_storage(self):
  s=(M/'mtl_host_pso_prepare.inc').read_text();body=s[s.index('HostPso::FixedSeedResult HostKernels::PrepareFixedSeeds'):s.index('HostPso::WarmResult HostKernels::FinishPreparation')];self.assertIn('Replay(*request)',body);self.assertIn('Describe(invocation,descriptor)',body);self.assertNotIn('Storage::',body);self.assertNotIn('newBuffer',body);self.assertNotIn('newTexture',body);self.assertNotIn('fixed_seed_item',body)
 def test_scalar_original_presenter_provider(self):
  s=(M/'mtl_presenter.mm').read_text();self.assertIn('PresentationFrameFormat() noexcept { return static_cast<u32>(FramePixelFormat); }',s);seed=(M/'mtl_fixed_helper_seeds.h').read_text();self.assertNotIn('BGRA8',seed);self.assertIn('out[i].format=presentation_format',seed)
 def test_frozen_tool_closure_includes_default_driver(self):
  t=ast.parse((R/'tools/prepare_local_aot_stage.py').read_text());value=next(n.value for n in t.body if isinstance(n,ast.Assign)and any(isinstance(k,ast.Name)and k.id=='TOOLS'for k in n.targets));self.assertIn('launcher_fixed_helpers.py',ast.literal_eval(value))
 def test_no_handwritten_capability_authorization(self):
  s=(R/'tools/game_pso_component.py').read_text();self.assertIn('fixed_helper_seed_schema=fixed_helper_capability(worker,admission_root)',s);self.assertNotIn("fixed_helper_seed_schema=1 if m.get",s)
 def test_original_exit_contract(self):
  s=(R/'src/game_pso_preparer.mm').read_text();self.assertIn('return critical?BB_ARM_EXIT_HOST_PROTECTION:0',s);driver=(R/'tools/launcher_fixed_helpers.py').read_text();self.assertNotIn("row['exit']==79",driver)
if __name__=='__main__':unittest.main(verbosity=2)
