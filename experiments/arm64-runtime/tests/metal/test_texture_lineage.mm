// SPDX-License-Identifier: GPL-2.0-or-later
// Real cache/upload/promotion/copy path, authored guest bytes, no game data.
#define main texture_expansion_previous_main
#include "test_texture_expansion.mm"
#undef main
#include "video_core/renderer_metal/mtl_texture_lineage.h"
#include <filesystem>
#include <fstream>

int main(int argc,char**argv) {
 assert(argc==3);const std::string kind=argv[2];const std::filesystem::path root=argv[1];
 setenv("BB_GUEST_IN_PLACE","0",1);
 void* backing=MAP_FAILED;
 for(uintptr_t a=0x800000000ull;a<0x10000000000ull;a+=0x100000000ull){backing=mmap((void*)a,0x800000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);if(backing!=MAP_FAILED)break;}
 assert(backing!=MAP_FAILED);test_base=(uintptr_t)backing;
 @autoreleasepool {
  Fixture f;auto desc=Desc(test_base+0x100000,256,AmdGpu::ImageType::Color2D,256);
  desc.info.resources={9,1};desc.info.pixel_format=vk::Format::eR8G8B8A8Uint;desc.info.UpdateSize();
  desc.type=TextureCache::BindingType::Storage;
  desc.view_info.format=vk::Format::eR8G8B8A8Uint;desc.view_info.range.extent={9,1};
  const unsigned char initial=kind=="bad-input"?0xbc:0x11;
  memset((void*)desc.info.guest_address,initial,desc.info.guest_size);
  auto image_id=f.textures.FindImage(desc);(void)f.textures.FindTexture(image_id,desc);f.runtime.Finish();
  auto& image=f.textures.GetImage(image_id);const auto old=image.backing->image.native;
  __weak id<MTLTexture> old_weak=(__bridge id<MTLTexture>)old;
  const auto identity=Metal::MemoryStats::ReadIdentity(old);assert(identity.uid);
  f.runtime.PromoteImageUsage(&image,ImageUsagePromotion::MutableFormat);f.runtime.Finish();
  assert(image.backing->image.native!=old);
  const auto fresh=Metal::MemoryStats::ReadIdentity(image.backing->image.native);assert(fresh.uid&&fresh.uid!=identity.uid);
  if(kind=="log-full")for(const auto& entry:std::filesystem::directory_iterator(root))
      if(entry.path().extension()==".jsonl")std::filesystem::resize_file(entry.path(),8*1024*1024);
  vk::ClearValue zero{};f.runtime.ClearImage(&image,{.base={0,0},.extent={9,1}},zero);f.runtime.Finish();
  image.flags|=ImageFlagBits::CpuDirty;desc.type=TextureCache::BindingType::Texture;
  (void)f.textures.FindTexture(image_id,desc);f.runtime.Finish();
  auto target=desc;target.info.guest_address=test_base+0x200000;
  memset((void*)target.info.guest_address,0x55,target.info.guest_size);
  auto dst=f.textures.FindImage(target);(void)f.textures.FindTexture(dst,target);f.runtime.Finish();
  f.runtime.CopyImage(&image,&f.textures.GetImage(dst));f.runtime.Finish();
  if(kind=="pending-cap") {
   for(unsigned n=0;n<70;++n)Metal::TextureLineage::Get().Texture("pending-test",f.scheduler,
      f.device.GetImpl().device,(__bridge id<MTLTexture>)image.backing->image.native);
   f.runtime.Finish();
  }
  // Finish waits for the shared-event signal. Deferred resource callbacks wait
  // for command-buffer retirement too; preserve that stronger lifetime boundary.
  for(unsigned n=0;n<1000&&Metal::TextureLineage::Get().PendingCount();++n){
   f.scheduler.PopPendingOperations();std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  assert(!Metal::TextureLineage::Get().PendingCount());
  for(unsigned n=0;n<100&&old_weak;++n){f.scheduler.PopPendingOperations();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
  assert(!old_weak); // The lineage keeps scalar identities, not old backing objects.
  if(kind=="log-full") {
   assert(std::filesystem::exists(root/"lineage-incomplete"));
   assert(!Metal::TextureLineage::Get().Armed());
   assert(Pixels(f,f.textures.GetImage(dst),0,0));
   printf("lineage log-full PASS; original clear/copy survives diagnostic failure\n");return 0;
  }
  std::vector<std::filesystem::path> logs;
  for(const auto& entry:std::filesystem::directory_iterator(root))if(entry.path().extension()==".jsonl")logs.push_back(entry.path());
  if(kind=="disabled"||kind=="linked-marker"){assert(logs.empty());printf("lineage %s PASS\n",kind.c_str());return 0;}
  assert(logs.size()==1);const auto logfile=logs.front();
  std::ifstream stream(logfile);assert(stream.good());std::string line;std::vector<NSDictionary*> encoded;std::vector<NSDictionary*> saved;bool gap=false;
  unsigned decisions=0;
  while(std::getline(stream,line)) {
   auto data=[NSData dataWithBytes:line.data() length:line.size()];
   NSDictionary* row=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];assert(row);
   if([row[@"op"] isEqualToString:@"lineage_encoded"])encoded.push_back(row);
   if([row[@"op"] isEqualToString:@"lineage_saved"])saved.push_back(row);
   if([row[@"op"] isEqualToString:@"lineage_gap"])gap=true;
   if([row[@"op"] isEqualToString:@"lineage_decision"])++decisions;
  }
  assert(encoded.size()==saved.size());std::set<std::string> phases;bool link=false;
  for(auto row:encoded) {
   const auto phase=std::string([row[@"detail"][@"phase"] UTF8String]);phases.insert(phase);
   auto event=[row[@"event"] unsignedIntValue];NSDictionary* done=nil;
   for(auto candidate:saved)if([candidate[@"event"] unsignedIntValue]==event){done=candidate;break;}
   assert(done&&[done[@"gpu_complete"] boolValue]&&[done[@"written"] boolValue]);
   NSData* bytes=[NSData dataWithContentsOfFile:[NSString stringWithFormat:@"%s/%@",root.c_str(),done[@"file"]]];assert(bytes&&bytes.length==[done[@"bytes"] unsignedIntValue]);
   const auto* raw=static_cast<const unsigned char*>(bytes.bytes);
   if(phase=="upload-input-before-detile"||phase=="upload-input-after-detile") {
    const auto guest=[row[@"detail"][@"context"][@"guest"] unsignedLongLongValue];const unsigned char want=guest==desc.info.guest_address?initial:0x55;
    for(unsigned i=0;i<128;++i)assert(raw[i]==want);
   } else if(phase=="promotion-source"||phase=="promotion-result"||phase=="clear-return"||phase=="copy-image-return") {
    const unsigned char want=(phase=="promotion-source"||phase=="promotion-result")?initial:0;
    for(NSDictionary* region in row[@"detail"][@"regions"]) {
     auto offset=[region[@"offset"] unsignedIntValue];
     for(unsigned y=0;y<8;++y)for(unsigned x=0;x<32;++x)assert(raw[offset+y*256+x]==want);
    }
   }
   if(phase=="promotion-result") {
    assert([row[@"detail"][@"predecessor"][@"uid"] unsignedLongLongValue]==identity.uid);
    assert([row[@"detail"][@"texture"][@"uid"] unsignedLongLongValue]==fresh.uid);link=true;
   }
  }
  assert(link&&decisions&&phases.contains("upload-input-before-detile")&&phases.contains("upload-input-after-detile")&&phases.contains("upload-result")&&phases.contains("clear-return")&&phases.contains("copy-image-return"));
  if(kind=="pending-cap")assert(gap);
  printf("lineage %s PASS events=%zu\n",kind.c_str(),encoded.size());
 }
 munmap(backing,0x800000);
}
