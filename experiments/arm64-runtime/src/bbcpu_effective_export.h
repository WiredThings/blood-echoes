// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "bbcpu_effective_image.h"
#include <fstream>
#include <iomanip>
namespace BbCpuImage {
struct ResumeSite {u64 site,covered;std::string phase;};
inline void WriteBytes(std::ofstream& out,u64 at,const void* data,size_t size){out.seekp(std::streamoff(at));out.write(static_cast<const char*>(data),std::streamsize(size));Require(bool(out),"effective ELF write failed");}
// The CPU loader and the small authored-input tool share the exact serializer
// and normalization algorithm. Inputs are real mapped bytes, never fabricated
// descriptors or assumed patch destinations.
inline Summary Export(const std::filesystem::path& output,const BbBoot::Boot& boot,const Layout& layout,
 const void* image,std::span<const unsigned char> source_elf,std::string_view boot_hash,std::string_view elf_hash,
 std::span<const ResumeSite> resumes={},std::string_view phase="original-startup-patches",std::string_view import_source={},std::string_view analysis_hash={}){
 namespace fs=std::filesystem;auto out=fs::absolute(output).lexically_normal();
 Require(fs::canonical(out.parent_path())==out.parent_path()&&fs::create_directory(out),"effective-code output must be a fresh non-linked directory");fs::permissions(out,fs::perms::owner_all,fs::perm_options::replace);
 ElfHeader h{};Require(source_elf.size()>=sizeof(h),"effective source ELF header absent");memcpy(&h,source_elf.data(),sizeof(h));h.phoff=sizeof(h);h.phnum=uint16_t(layout.loads.size()+1);h.phentsize=sizeof(Program);h.shoff=0;h.shnum=0;h.shentsize=0;h.shstrndx=0;
 std::vector<Program> programs;u64 at=4096;
 for(auto p:layout.loads){p.offset=at;p.paddr=p.address;p.filesz=p.memsz;p.align=4096;Require(p.memsz<=(544u<<20)-at,"effective ELF too large");programs.push_back(p);at=(at+p.memsz+4095)&~u64{4095};}
 auto eh=layout.eh;bool found=false;
 for(auto p:programs)if(eh.address>=p.address&&eh.address-p.address<=p.filesz&&eh.filesz<=p.filesz-(eh.address-p.address)){eh.offset=p.offset+eh.address-p.address;found=true;break;}
 Require(found,"effective ELF lost original unwind metadata");programs.push_back(eh);
 std::ofstream ready(out/"effective.elf",std::ios::binary);Require(bool(ready),"effective ELF unavailable");WriteBytes(ready,0,&h,sizeof(h));WriteBytes(ready,h.phoff,programs.data(),programs.size()*sizeof(Program));
 auto result=Normalize(boot,layout,image,[&](Program p,u64 offset,std::span<const unsigned char> bytes){
  auto match=std::find_if(programs.begin(),programs.end(),[&](auto row){return row.type==1&&row.address==p.address;});Require(match!=programs.end(),"effective output lost a load");WriteBytes(ready,match->offset+offset,bytes.data(),bytes.size());
 });ready.flush();Require(bool(ready),"effective ELF flush failed");ready.close();
 std::ofstream pointers(out/"pointers.txt");for(auto pointer:result.pointers)pointers<<"0x"<<std::hex<<pointer<<'\n';pointers.flush();Require(bool(pointers),"effective pointer list write failed");
 auto output_hash=BbStaticCode::Hex(BbStaticCode::SourceHash(out/"effective.elf"));std::ofstream report(out/"effective-input.json");
 report<<"{\"schema\":1,\"effective_identity\":"<<std::quoted(result.identity)<<",\"effective_elf_sha256\":"<<std::quoted(output_hash)<<",\"source_boot_sha256\":"<<std::quoted(std::string(boot_hash))<<",\"source_elf_sha256\":"<<std::quoted(std::string(elf_hash))<<",\"normalized_bytes\":"<<result.bytes<<",\"source_fields_are_provenance_only\":true,\"phase\":"<<std::quoted(std::string(phase))<<",\"loads\":[";
 for(size_t i=0;i<layout.loads.size();++i){auto p=layout.loads[i];if(i)report<<',';report<<"{\"address\":"<<p.address<<",\"size\":"<<p.memsz<<",\"flags\":"<<p.flags<<'}';}
 report<<"],\"unwind\":{\"address\":"<<eh.address<<",\"size\":"<<eh.filesz<<"},\"excluded_code_slots\":[";
 for(size_t i=0;i<result.excluded.size();++i){if(i)report<<',';report<<'['<<result.excluded[i].first<<','<<result.excluded[i].second<<']';}
 report<<"],\"resume_entries\":[";
 for(size_t i=0;i<resumes.size();++i){const auto& r=resumes[i];Require(r.covered&&r.site<=UINT64_MAX-r.covered&&boot.covered(r.site,r.covered,true)&&boot.covered(r.site+r.covered,1,true),"runtime resume outside actual guest code");if(i)report<<',';report<<"{\"site\":"<<r.site<<",\"covered\":"<<r.covered<<",\"resume\":"<<r.site+r.covered<<",\"phase\":"<<std::quoted(r.phase)<<'}';}
 report<<']';if(!import_source.empty())report<<",\"import_input\":{\"path\":"<<std::quoted(std::string(import_source))<<",\"sha256\":"<<std::quoted(std::string(elf_hash))<<'}';
 if(!analysis_hash.empty())report<<",\"analysis_elf_sha256\":"<<std::quoted(std::string(analysis_hash));
 report<<"}\n";report.flush();Require(bool(report),"effective descriptor write failed");return result;
}
}
