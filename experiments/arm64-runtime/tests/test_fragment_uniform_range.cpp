// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>
#include "mtl_fragment_uniforms.h"
#include "reference.h"
static std::string source(unsigned reads) {
 std::string s="struct U { uint data[1]; };\nfragment uint pixel(const device U& ssbo_1 [[buffer(1)]])\n{\nuint value=0;\n";
 for(unsigned i=0;i<reads;++i)s+="value += ssbo_1.data["+std::to_string(i%1024)+"u];\n";
 return s+"return value;\n}\n";
}
int main(int argc,char**argv){
 std::vector<std::string> corpus;
 for(unsigned reads:{1u,8u,64u,512u,2048u}){
  auto s=source(reads);assert(Metal::RewriteFragmentUniforms(s).buffers==1);corpus.push_back(s);
  for(const auto bad:{"1024u","18446744073709551615u","uint(value)","0u + missing_offset"}){
   auto invalid=s;invalid.replace(invalid.find("0u]"),2,bad);assert(Metal::RewriteFragmentUniforms(invalid).buffers==0);corpus.push_back(invalid);
  }
  auto escaped=s;escaped.insert(escaped.find("return value;"),"uint p=ssbo_1;\n");assert(Metal::RewriteFragmentUniforms(escaped).buffers==0);corpus.push_back(escaped);
  auto rw=s;rw.replace(rw.find("const device"),12,"device");assert(Metal::RewriteFragmentUniforms(rw).buffers==0);corpus.push_back(rw);
 }
 for(int i=1;i<argc;++i){std::ifstream f(argv[i]);assert(f);std::stringstream s;s<<f.rdbuf();auto text=s.str();auto at=text.find("// ---- MSL");assert(at!=std::string::npos);at=text.find('\n',at);assert(at!=std::string::npos);corpus.push_back(text.substr(at+1));}
 unsigned eligible=0;size_t bytes=0;
 for(const auto&s:corpus){auto a=LegacyMetal::RewriteFragmentUniforms(s);auto b=Metal::RewriteFragmentUniforms(s);assert(a.source==b.source&&a.buffers==b.buffers);eligible+=b.buffers!=0;bytes+=s.size();}
 std::cout<<"cases "<<corpus.size()<<" eligible "<<eligible<<" bytes "<<bytes<<"\n";
 for(bool candidate:{false,true,true,false}){
  auto start=std::chrono::steady_clock::now();size_t checksum=0;
  for(const auto&s:corpus){if(candidate){auto r=Metal::RewriteFragmentUniforms(s);checksum+=r.source.size()+r.buffers;}else{auto r=LegacyMetal::RewriteFragmentUniforms(s);checksum+=r.source.size()+r.buffers;}}
  double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
  std::cout<<(candidate?"candidate ":"reference ")<<ms<<" checksum "<<checksum<<"\n";
 }
 puts("PASS byte-identical fragment rewriter; authored bounds/unsafe fallbacks and optional private corpus");
}
