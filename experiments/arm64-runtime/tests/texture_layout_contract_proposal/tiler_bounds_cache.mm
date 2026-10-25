// SPDX-License-Identifier: GPL-2.0-or-later
// Authored same-process legacy/extended UBO and cache-separation controls.
#define BB_RAW_ROUNDTRIP_MAIN raw_roundtrip_control_main
#include "gpu_raw_roundtrip.mm"
#undef BB_RAW_ROUNDTRIP_MAIN
#include "bb_tiler_bounds.h"
int main() {
 setenv("BB_GUEST_IN_PLACE","0",1);
 void* map=MAP_FAILED;
 for(uintptr_t a=0x800000000ull;a<0x10000000000ull;a+=0x100000000ull){map=mmap((void*)a,0x800000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);if(map!=MAP_FAILED)break;}
 assert(map!=MAP_FAILED);test_base=(uintptr_t)map;
 @autoreleasepool {
  Fixture f;auto desc=Make(test_base+0x100000,13,64,60,1,1);const auto& info=desc.info;
  TilerBounds::ParamsV1 params{TileManager::MakeTilingInfo(info,1)};auto copies=Copies(info);
  assert(TilerBounds::Describe(info,copies,params));unsigned rejected=0;
  auto reject=[&](auto change){auto altered=copies;change(altered[0]);auto out=params;assert(!TilerBounds::Describe(info,altered,out));++rejected;};
  reject([](auto& c){++c.bufferOffset;});reject([](auto& c){++c.bufferRowLength;});reject([](auto& c){++c.bufferImageHeight;});
  reject([](auto& c){++c.imageOffset.x;});reject([](auto& c){++c.imageOffset.y;});reject([](auto& c){++c.imageOffset.z;});
  reject([](auto& c){++c.imageSubresource.mipLevel;});reject([](auto& c){++c.imageSubresource.baseArrayLayer;});reject([](auto& c){++c.imageSubresource.layerCount;});
  reject([](auto& c){--c.imageExtent.width;});reject([](auto& c){--c.imageExtent.height;});reject([](auto& c){++c.imageExtent.depth;});
  reject([](auto& c){c.imageSubresource.aspectMask=vk::ImageAspectFlagBits::eDepth;});
  for(int kind=0;kind<6;++kind){auto changed=info;if(kind==0)changed.props.is_block=1;if(kind==1)changed.props.is_volume=1;if(kind==2)changed.props.is_depth=1;if(kind==3)changed.props.has_stencil=1;if(kind==4)changed.num_samples=2;if(kind==5)++changed.resources.levels;auto out=params;assert(!TilerBounds::Describe(changed,copies,out));++rejected;}
  auto& stream=f.buffers.GetStreamBuffer();
  auto linear=f.runtime.StagingRequest(info.guest_size,MemoryType::HostCached,256);
  auto tiled=f.runtime.StagingRequest(info.guest_size+Guard,MemoryType::HostCached,256);
  f.runtime.FillBuffer(linear.buffer,linear.offset,info.guest_size,0x77777777);
  f.runtime.FillBuffer(linear.buffer,linear.offset,64*60*4,0x42424242);f.runtime.Finish();
  for(const auto range:{0ull,459ull,460ull,651ull,652ull,653ull,0xffffffffffffffffull}) {
   auto d=TileManager::MakeDispatch(info,true,{tiled.buffer,tiled.offset,info.guest_size},{linear.buffer,linear.offset,info.guest_size},{&stream,0,range});assert(TilerBounds::Enabled(d)==(range==652));d.key.is_tiler=false;assert(!TilerBounds::Enabled(d));
  }
  struct Default { u32 id, default_value; };
  const std::array<Default, 4> defaults{{{0,1},{4,7},{11,5},{13,0}}};
  const std::array<u32, 4> supplied{3,2,1,1};
  const auto omitted=TilerBounds::Constants(std::span(supplied),defaults,false);
  const std::array<u32, 12> explicit_values{3,2,1,1,7,0,0,0,0,0,0,5};
  const auto explicit_defaults=TilerBounds::Constants(std::span(explicit_values),defaults,false);
  assert(omitted==explicit_defaults && omitted[4]==7 && omitted[11]==5 && omitted[13]==0);
  auto changed_values=explicit_values;changed_values[4]=0;
  const auto changed=TilerBounds::Constants(std::span(changed_values),defaults,false);
  assert(changed!=omitted && changed[4]==0);
  const auto enabled=TilerBounds::Constants(std::span(supplied),defaults,true);
  assert(enabled[13]==1 && enabled[4]==7 && enabled[11]==5);
  unsigned passes=0;
  for(const bool bounded:{false,true,false,true,true,false}) {
   // Poison the unused legacy tail with zeros. Accidentally reusing a bounded
   // pipeline for the legacy prefix would skip all output, which this checks.
   auto data=params;if(!bounded)data.logical={};
   auto off=stream.Copy(&data,sizeof(data),f.runtime.Limits().uniform_min_alignment);
   f.runtime.FillBuffer(tiled.buffer,tiled.offset,info.guest_size,0x11111111);f.runtime.FillBuffer(tiled.buffer,tiled.offset+info.guest_size,Guard,0xcdcdcdcd);f.runtime.Finish();
   auto dispatch=TileManager::MakeDispatch(info,true,{tiled.buffer,tiled.offset,info.guest_size},{linear.buffer,linear.offset,info.guest_size},{&stream,off,bounded?sizeof(params):sizeof(TilingInfo)});
   f.runtime.DispatchTiling(dispatch);f.runtime.Finish();tiled.Invalidate();
   std::vector<bool> covered(info.guest_size,false);
   for(unsigned y=0;y<60;++y)for(unsigned x=0;x<64;++x){auto at=RawOffset(info,0,0,x,y);for(unsigned j=0;j<4;++j)covered[at+j]=true;}
   for(size_t i=0;i<info.guest_size;++i)assert(tiled.mapped[i]==(covered[i]?0x42:(bounded?0x11:0x77)));
   for(size_t i=info.guest_size;i<info.guest_size+Guard;++i)assert(tiled.mapped[i]==0xcd);
   ++passes;printf("UBO_CACHE phase=%u bytes=%zu logical=%d raw=PASS guard=PASS\n",passes,bounded?sizeof(params):sizeof(TilingInfo),bounded);
  }
  printf("BOUNDS_CONTROLS rejected_shapes=%u range_and_direction_cases=14 default_constant_controls=4 same_process_dispatches=%u result=PASS\n",rejected,passes);
 }
 assert(munmap(map,0x800000)==0);
}
