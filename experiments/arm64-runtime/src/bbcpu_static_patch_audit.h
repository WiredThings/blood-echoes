// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "bbcpu_static_admission.h"
#include <iomanip>
extern "C" void bbcpu_recomp_set_mismatch_reporter(void(*)(void*,uint64_t,uint64_t,uint64_t,uint64_t),void*);
namespace BbCpuPatchAudit {
inline std::string Hash(const unsigned char* bytes,size_t count){
 CC_SHA256_CTX c;CC_SHA256_Init(&c);CC_SHA256_Update(&c,bytes,static_cast<CC_LONG>(count));BbCpuCache::Digest h{};CC_SHA256_Final(h.data(),&c);return BbStaticCode::Hex(h);
}
struct Write {std::string stage;uint64_t offset,size;std::string before,after;};
class Audit {
 struct Range {uint64_t offset;std::vector<unsigned char> before;};
 const unsigned char* image_;const BbCpuArtifact::Candidate& admitted_;
 std::vector<Range> ranges_;std::vector<Write>writes_;std::ofstream output_;
public:
 Audit(const void* image,const BbCpuArtifact::Candidate& admitted,const std::filesystem::path& output):
  image_(static_cast<const unsigned char*>(image)),admitted_(admitted),output_(output){
  BbCpuImage::Require(bool(output_),"static patch audit output unavailable");
  for(auto p:admitted.layout.loads)if(p.flags&1){BbCpuImage::Require(p.memsz<=(128ull<<20),"static patch audit text bound");ranges_.push_back({p.address,{image_+p.address,image_+p.address+p.memsz}});}
  output_<<"{\"event\":\"baseline\",\"phase\":\"after-original-startup-patches-before-runtime-hooks\",\"effective_identity\":"<<std::quoted(admitted.effective_identity)<<"}\n";
 }
 void Step(const char* stage){
  for(auto& range:ranges_){auto* actual=image_+range.offset;size_t at=0;
   while(at<range.before.size()){
    if(range.before[at]==actual[at]){++at;continue;}
    const auto start=at++;while(at<range.before.size()&&range.before[at]!=actual[at])++at;
    BbCpuImage::Require(writes_.size()<4096,"static patch audit write bound");
    Write w{stage,range.offset+start,at-start,Hash(range.before.data()+start,at-start),Hash(actual+start,at-start)};writes_.push_back(w);
    output_<<"{\"event\":\"write\",\"stage\":"<<std::quoted(w.stage)<<",\"offset\":"<<w.offset<<",\"size\":"<<w.size<<",\"before_sha256\":"<<std::quoted(w.before)<<",\"after_sha256\":"<<std::quoted(w.after)<<"}\n";
   }
   memcpy(range.before.data(),actual,range.before.size());
  }output_.flush();
 }
 void Report(uint64_t offset,uint64_t size,uint64_t compiled,uint64_t actual){
  BbCpuImage::Require(size&&size<=(16ull<<20),"static mismatch function bound");
  const BbCpuImage::Program* source=nullptr;
  for(const auto& p:admitted_.layout.loads)if((p.flags&1)&&offset>=p.address&&offset-p.address<=p.filesz&&size<=p.filesz-(offset-p.address)){source=&p;break;}
  BbCpuImage::Require(source,"static mismatch source span unavailable");
  std::vector<unsigned char> bytes(size);admitted_.input->Unchanged();admitted_.input->Read(source->offset+offset-source->address,bytes.data(),bytes.size());admitted_.input->Unchanged();
  output_<<"{\"event\":\"mismatch\",\"offset\":"<<offset<<",\"address\":"<<reinterpret_cast<uintptr_t>(image_+offset)<<",\"size\":"<<size<<",\"compiled_fnv64\":"<<compiled<<",\"runtime_fnv64\":"<<actual<<",\"compiled_sha256\":"<<std::quoted(Hash(bytes.data(),bytes.size()))<<",\"runtime_sha256\":"<<std::quoted(Hash(image_+offset,size))<<",\"writes\":[";
  bool first=true;for(const auto& w:writes_)if(w.offset<offset+size&&offset<w.offset+w.size){if(!first)output_<<',';first=false;output_<<"{\"stage\":"<<std::quoted(w.stage)<<",\"offset\":"<<w.offset<<",\"size\":"<<w.size<<'}';}
  output_<<"]}\n";output_.flush();
 }
 static void Callback(void* context,uint64_t offset,uint64_t size,uint64_t expected,uint64_t actual)noexcept{
  try{static_cast<Audit*>(context)->Report(offset,size,expected,actual);}catch(const std::exception& e){fprintf(stderr,"BBCPU STATIC audit incomplete: %s\n",e.what());}
 }
};
}
