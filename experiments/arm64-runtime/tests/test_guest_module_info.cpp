// SPDX-License-Identifier: GPL-2.0-or-later
#include "guest_module_info.h"
#include "prepare_guest_module_info.h"
#include "boot_image.h"
#include <CommonCrypto/CommonDigest.h>
#include <cassert>
#include <iostream>
#include <sys/mman.h>
using namespace BbGuestModules;
std::array<uint8_t,32> Hash(const std::filesystem::path&p){CC_SHA256_CTX c;CC_SHA256_Init(&c);std::ifstream f(p,std::ios::binary);std::array<char,65536>b;while(f){f.read(b.data(),b.size());if(f.gcount())CC_SHA256_Update(&c,b.data(),f.gcount());}assert(f.eof());std::array<uint8_t,32>d;CC_SHA256_Final(d.data(),&c);return d;}
int main(int argc,char**argv){
 assert(argc==3||argc==4);BbBoot::Boot boot(argv[1]);auto metadata=argc==4?Prepare(argv[1],argv[3],argv[2]):std::filesystem::path(argv[2]);
 auto mapped=Round(boot.size);auto*image=static_cast<uint8_t*>(mmap(nullptr,mapped+16384,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0));assert(image!=MAP_FAILED);assert(!mprotect(image+mapped,16384,PROT_NONE));boot.load(image);
 const auto base=reinterpret_cast<uintptr_t>(image);auto digest=Hash(argv[1]);Registry reg;reg.Load(metadata,digest,boot,base);assert(reg.Size()==boot.modules.size()+1);
 for(const auto&s:boot.segments){const auto*r=reg.Find(base+s.address);assert(r);assert(Match(*r,base+s.address)==bool(s.flags&1));}
 assert(!reg.Find(base-1)&&!reg.Find(base+mapped));
 if(argc==4){std::cout<<"PASS actual original-loader process bridge, boot identity, all modules/TLS/EH/segments; metadata bytes="<<reg.Size()*sizeof(Info)<<"\n";return 0;}
 assert(bb_low_allocation_alignment(0)==16&&bb_low_allocation_alignment(17)==32&&bb_low_allocation_alignment(1184)==2048&&bb_low_allocation_alignment(524289)==1048576);
 Info copy=*reg.Find(base+0x8000);assert(Match(copy,base+0xbfff)&&!Match(copy,base+0xc000));copy.segment_count=5;assert(!Match(copy,base+0x8000));copy=*reg.Find(base+0x8000);copy.segments[1].address=UINT64_MAX;assert(!Match(copy,base+0x8000));
 std::ifstream input(metadata,std::ios::binary);std::vector<char> original((std::istreambuf_iterator<char>(input)),{});auto bad=metadata.string()+".negative";unsigned negatives=0;
 auto reject=[&](std::vector<char>b){std::ofstream f(bad,std::ios::binary);f.write(b.data(),b.size());f.close();bool failed=false;try{Registry r;r.Load(bad,digest,boot,base);}catch(const std::runtime_error&){failed=true;}assert(failed);++negatives;};
 for(size_t i=0;i<56;++i)reject({original.begin(),original.begin()+i});
 auto put=[&](size_t off,uint64_t value,size_t size){auto b=original;memcpy(b.data()+off,&value,size);reject(b);};
 put(0,0,1);put(8,0,1);put(40,1,8);put(48,9,8);
 auto b=original;b.push_back(0);reject(b);b=original;b.pop_back();reject(b);
 // Power-of-two alignments must still match the actual static/dynamic allocator.
 b=original;uint32_t align=32,offset=32;memcpy(b.data()+56+292,&align,4);memcpy(b.data()+56+288,&offset,4);reject(b);put(56+sizeof(Info)+292,64,4);
 for(auto off:{size_t(56+264),size_t(56+272),size_t(56+288),size_t(56+292),size_t(56+352+8),size_t(56+352+12),size_t(56+416)})put(off,UINT64_MAX,4);
 b=original;memset(b.data()+56+8,'x',256);reject(b);
 // An invalid EH header ends at a PROT_NONE page: rejection must precede any read.
 const size_t row=56+sizeof(Info);b=original;uint64_t hdr=boot.size-4,frame=boot.size-260;uint32_t hs=4,fs=256;
 memcpy(b.data()+row+328,&hdr,8);memcpy(b.data()+row+336,&frame,8);memcpy(b.data()+row+344,&hs,4);memcpy(b.data()+row+348,&fs,4);reject(b);
 std::filesystem::remove(bad);munmap(image,mapped+16384);std::cout<<"PASS module ABI/identity/TLS/EH/ranges, executable boundaries, whole-table matcher validation, negatives="<<negatives<<"\n";
}
