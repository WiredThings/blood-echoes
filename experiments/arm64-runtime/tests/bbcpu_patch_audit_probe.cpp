// SPDX-License-Identifier: GPL-2.0-or-later
// Authored bytes only. Tests diagnostic ranges/ownership, not game execution.
#include "bbcpu_static_patch_audit.h"
#include <cassert>
#include <fstream>
#include <iostream>
int main(){
 char name[]="/private/tmp/bbcpu-patch-audit-XXXXXX";auto* directory=mkdtemp(name);assert(directory);
 const auto root=std::filesystem::path(directory),source=root/"input.bin",log=root/"audit.jsonl";
 std::vector<unsigned char> bytes(4096,0x90);{std::ofstream f(source,std::ios::binary);f.write(reinterpret_cast<char*>(bytes.data()),bytes.size());}
 BbCpuArtifact::Admitted admitted;admitted.input=std::make_unique<BbCpuArtifact::File>(source);admitted.effective_identity=std::string(64,'1');
 BbCpuImage::Program segment{};segment.flags=5;segment.filesz=segment.memsz=bytes.size();admitted.layout.loads.push_back(segment);
 {
  BbCpuPatchAudit::Audit audit(bytes.data(),admitted,log);audit.Step("no-change");
  std::fill(bytes.begin()+100,bytes.begin()+105,0xcc);audit.Step("known-startup-code");
  bytes[500]=0xc3;audit.Step("other-function");audit.Report(96,32,1,2);audit.Report(1024,16,3,4);
  bool refused=false;try{audit.Report(4090,32,1,2);}catch(const std::runtime_error&){refused=true;}assert(refused);
 }
 std::ifstream f(log);std::string line;size_t changed=0,unchanged=0;
 while(std::getline(f,line))if(line.find("\"event\":\"mismatch\"")!=std::string::npos){
  if(line.find("\"offset\":96,")!=std::string::npos){assert(line.find("known-startup-code")!=std::string::npos&&line.find("other-function")==std::string::npos);++changed;}
  if(line.find("\"offset\":1024,")!=std::string::npos){assert(line.find("\"writes\":[]")!=std::string::npos);++unchanged;}
 }
 assert(changed==1&&unchanged==1);
 {std::ofstream alter(source,std::ios::binary|std::ios::app);alter.put(0);}
 bool stale=false;try{BbCpuPatchAudit::Audit audit(bytes.data(),admitted,root/"stale.jsonl");audit.Report(96,32,1,2);}catch(const std::runtime_error&){stale=true;}assert(stale);
 std::cout<<"PASS authored patch/source SHA association, disjoint writes, span and stale-input refusal; evidence="<<root<<'\n';
}
