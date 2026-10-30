// Explicit isolated CPU-code profiling only; no cache load or code ownership.
#pragma once
#include "bb_source_hash.h"
#include <FEXCore/HLE/SourcecodeResolver.h>
#include <FEXCore/Core/CodeCache.h>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
namespace BbStaticCode {
struct Range{uint64_t begin,end;};
class Files {
 struct Page{uint64_t offset,length;Digest before;bool relocation;};
 std::vector<Page> pages;std::vector<Range> sections;std::unique_ptr<FEXCore::ExecutableFileInfo> file{new FEXCore::ExecutableFileInfo};
 Digest source{},identity{};uintptr_t base{};size_t size{},modified{},relocated{},metadata_peak{},eligible{};bool ready{};
public:
 template<class Boot>Files(const Boot& boot,uintptr_t address,std::span<const std::byte> image):base(address),size(image.size()){
  Require(base&&size&&size==boot.size&&size<=512u<<20&&base<=UINTPTR_MAX-size,"static code image bounds");
  source=SourceHash(boot.path);file->Filename=std::filesystem::canonical(boot.path).string();
  std::vector<bool> relocation_pages((size+4095)/4096);
  for(const auto& reloc:boot.relocs){Require(reloc.target<=size&&8<=size-reloc.target,"static code relocation bounds");relocation_pages[reloc.target/4096]=true;relocation_pages[(reloc.target+7)/4096]=true;}
  uint64_t previous_end=0;
  for(const auto& segment:boot.segments){
   Require(segment.size&&segment.address<=size&&segment.size<=size-segment.address&&segment.flags<=7,"static code segment bounds");
   if(!(segment.flags&1))continue;
   Require(!(segment.flags&2)&&segment.address%4096==0&&segment.address>=previous_end,"static code segment order/WX");
   previous_end=segment.address+segment.size;
   for(uint64_t at=segment.address;at<previous_end;at+=4096){
    uint64_t length=std::min<uint64_t>(4096,previous_end-at);bool relocated_page=relocation_pages[at/4096];
    pages.push_back({at,length,BbCpuCache::Hash(image.subspan(at,length)),relocated_page});
   }
  }
  Require(!pages.empty(),"static code no executable pages");metadata_peak=pages.capacity()*sizeof(Page);
 }
 Files(const Files&)=delete;Files&operator=(const Files&)=delete;
 void Finalize(std::span<const std::byte> image){
  Require(!ready&&image.size()==size,"static code finalize state");
  CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);CC_SHA256_Update(&ctx,source.data(),source.size());
  auto number=[&](uint64_t v){std::array<unsigned char,8> data{};for(unsigned i=0;i<8;++i)data[i]=v>>(8*i);CC_SHA256_Update(&ctx,data.data(),data.size());};
  number(size);
  for(const auto& page:pages){
   bool changed=page.before!=BbCpuCache::Hash(image.subspan(page.offset,page.length));modified+=changed;relocated+=page.relocation;
   if(changed||page.relocation)continue;
   number(page.offset);number(page.length);CC_SHA256_Update(&ctx,page.before.data(),page.before.size());eligible+=page.length;
   if(!sections.empty()&&sections.back().end==page.offset)sections.back().end+=page.length;
   else sections.push_back({page.offset,page.offset+page.length});
  }
  Require(eligible!=0,"static code all pages excluded");CC_SHA256_Final(identity.data(),&ctx);
  // This 64-bit hint is only for code maps. Persistent-cache acceptance must use full SHA256.
  uint64_t hint=0;for(unsigned i=0;i<8;++i)hint|=uint64_t(identity[i])<<(8*i);
  if(hint==0||hint==UINT64_MAX)hint^=0x42454350554d4150ULL;
  file->FileId=hint;std::vector<Page>().swap(pages);ready=true;
 }
 std::optional<FEXCore::ExecutableFileSectionInfo> Query(uintptr_t address)const{
  if(!ready||address<base||address-base>=size)return {};
  auto offset=address-base;auto it=std::upper_bound(sections.begin(),sections.end(),offset,[](auto x,const Range& r){return x<r.begin;});
  if(it==sections.begin())return {};--it;if(offset>=it->end)return {};
  return FEXCore::ExecutableFileSectionInfo{*file,base,base+it->begin,base+it->end};
 }
 const auto& Sections()const{return sections;}const Digest& Identity()const{return identity;}const Digest& SourceIdentity()const{return source;}
 size_t ModifiedPages()const{return modified;}size_t RelocationPages()const{return relocated;}
 size_t EligibleBytes()const{return eligible;}size_t RetainedHashBytes()const{return pages.capacity()*sizeof(Page);}
 void Report(const std::filesystem::path& out)const{
  Require(ready,"static code not finalized");std::ofstream f(out,std::ios::binary);Require(bool(f),"static code report unavailable");
  f<<"{\"schema\":1,\"source\":"<<std::quoted(std::string(file->Filename))<<",\"source_sha256\":\""<<Hex(source)<<"\",\"identity_sha256\":\""<<Hex(identity)<<"\",\"file_id\":\""<<std::hex<<file->FileId<<std::dec<<"\",\"image_bytes\":"<<size<<",\"eligible_bytes\":"<<eligible<<",\"modified_pages\":"<<modified<<",\"relocation_pages\":"<<relocated<<",\"temporary_hash_peak_bytes\":"<<metadata_peak<<",\"retained_hash_bytes\":"<<RetainedHashBytes()<<",\"sections\":[";
  for(size_t i=0;i<sections.size();++i){if(i)f<<',';f<<'['<<sections[i].begin<<','<<sections[i].end<<']';}f<<"]}\n";f.flush();Require(bool(f),"static code report write failed");
 }
};
}
