"""Extract the real startup control method for author CPU interfaces; no Metal.
Source root is explicit, portable tests contain no private evidence/cache path.
"""
from pathlib import Path
import argparse
p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();t=Path(__file__).resolve().parent
s=(a.source_root/'vendor/metal_api/shadps4/video_core/renderer_metal/mtl_host_pso_prepare.inc').read_text()
method=s[s.index('HostPso::FixedSeedResult HostKernels::PrepareFixedSeeds('):s.index('HostPso::WarmResult HostKernels::FinishPreparation()')]
start=s.index(' // Copy only bounded route enum VALUES');end=s.index(' id<MTLComputePipelineState> state=nil;',start);snapshot=s[start:end]
render=s[s.index('id<MTLRenderPipelineState> HostKernels::MakeRender('):];rstart=render.index(' // Copy only bounded route enum VALUES');rend=render.index(' id<MTLRenderPipelineState> state=nil;',rstart);assert render[rstart:rend]==snapshot
route='\nvoid RouteSnapshotControls(){\n std::vector<Metal::LocalLibrary::Origin> owned(2);for(auto&o:owned)o.route=Metal::LocalLibrary::Route::LocalMetallib;\n std::span<const Metal::LocalLibrary::Origin> origins=owned;\n SNAPSHOT\n // Cold callback destroys/reuses the original source storage. Everything read\n // after this point must be the captured enum/scalar, never the former span.\n owned.clear();owned.shrink_to_fit();std::vector<Metal::LocalLibrary::Origin> reuse(2);for(auto&o:reuse)o.route=Metal::LocalLibrary::Route::Source;\n ck(origin_count==2&&actual_routes[0]==Metal::LocalLibrary::Route::LocalMetallib&&actual_routes[1]==Metal::LocalLibrary::Route::LocalMetallib);\n}\n'
route=route.replace('SNAPSHOT',snapshot)
prefix=(t/'fixed_cpu_prefix.inc').read_text();suffix=(t/'fixed_cpu_suffix.inc').read_text()
# Actual @autoreleasepool and production ScopeGuard are preserved, linked only
# to libobjc; authored interfaces make Descriptor/Replay/pressure deterministic.
a.output.write_text(prefix+method+'\n} // namespace Metal\n'+route+'\nnamespace Metal {\n'+suffix)
