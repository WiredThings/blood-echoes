// SPDX-License-Identifier: GPL-2.0-or-later
// CPU-only authored data, original native CompileSnapshot type, no GPU/library/guest.
#include "fetch_register_snapshot.h"
#include <fstream>
#include <iostream>
#include <sys/stat.h>
#include <unistd.h>
using namespace BbFetchStore;
namespace {
int passed{};
void Check(bool ok,const char*name){if(!ok)throw std::runtime_error(name);++passed;}
template<class F>void Reject(F f,const char*name){bool threw=false;try{f();}catch(const std::exception&){threw=true;}Check(threw,name);}
Shader::CompileSnapshot Snapshot(){
 Shader::CompileSnapshot s;s.hw_stage=Shader::HwStage::Vertex;s.sw_stage=Shader::SwStage::Vertex;s.runtime.hw_stage=s.hw_stage;s.runtime.sw_stage=s.sw_stage;s.hash=0x1234;s.program_base=0x10000;
 s.code={0xBEEB03FF,0xBF810000};s.user_data.resize(16,0xDEADBEEF);s.flat.resize(16,0xABCD);s.flat_dwords=16;s.profile.two_level_bda=true;s.profile.relaxed_fragment_compute_float=true;
 s.fetch.emplace();s.fetch->size=32;s.fetch->attributes.push_back({0,4,3,2,0,0,7,11,4});
 Shader::CompileSnapshot::RegisterRead r{};r.pointer_index=2;r.dword_offset=0;std::array<uint32_t,4>raw{0x01234567,0x89ABCDEF,0x10203040,0x50607080};std::memcpy(&r.buffer,raw.data(),16);s.register_reads.push_back(r);return s;
}
Environment Env(){Environment e;for(auto*d:{&e.game,&e.compiler,&e.abi,&e.compile_inputs})d->fill(11);return e;}
void File(const std::filesystem::path&p,std::string_view text,mode_t mode=0600){std::ofstream f(p,std::ios::binary);f<<text;f.close();chmod(p.c_str(),mode);}
std::vector<std::byte>Bytes(const std::filesystem::path&p){std::ifstream f(p,std::ios::binary);std::string s((std::istreambuf_iterator<char>(f)),{});std::vector<std::byte>b(s.size());std::memcpy(b.data(),s.data(),s.size());return b;}
bool CancelAfterWrite(void*p)noexcept{return ++*static_cast<int*>(p)>=2;}
template<class Snapshot>void ProjectionBoundary(Snapshot s,const Environment&e){if constexpr(requires{s.fetch_projection;}){s.fetch_projection.emplace();Reject([&]{FromSnapshot(s,e);},"raw store refuses a semantic projection instead of losing its dynamic budget");}}
}
int main(int argc,char**argv){try{
 Check(argc==2,"private authored root argument");std::filesystem::path root=std::filesystem::canonical(argv[1]);chmod(root.c_str(),0700);auto s=Snapshot();auto environment=Env();auto r=FromSnapshot(s,environment);auto bytes=Encode(r);
 Check(Decode(bytes,r.expected)==r,"verbatim native capture codec roundtrip");
 Check(r.reads[0].words==std::array<uint32_t,4>{0x01234567,0x89ABCDEF,0x10203040,0x50607080},"all16 original Buffer bytes retained");
 Check(r.expected.profile[72]==1&&r.expected.profile[73]==1,"current relaxed float and two-level Profile fields");
 Check(r.expected.attributes[0].fields==std::array<uint8_t,9>{0,4,3,2,0,0,7,11,4},"all9 fetch fields retained");
 ProjectionBoundary(s,environment);
 auto restored=s;restored.register_reads.clear();RestoreSnapshotReads(restored,r,r.expected);Check(restored.register_reads.size()==1&&std::memcmp(&restored.register_reads[0].buffer,&s.register_reads[0].buffer,16)==0,"reader restores exact owned descriptor");
 {auto bad=s;bad.register_reads.clear();Reject([&]{FromSnapshot(bad,environment);},"missing read refused");}
 {auto bad=s;bad.register_reads[0].dword_offset=4;Reject([&]{FromSnapshot(bad,environment);},"extraneous read refused");}
 {auto bad=s;bad.register_reads.push_back(bad.register_reads[0]);reinterpret_cast<uint8_t*>(&bad.register_reads.back().buffer)[0]^=1;Reject([&]{FromSnapshot(bad,environment);},"conflicting duplicate read refused");}
 {auto good=s;auto a=good.fetch->attributes[0];a.semantic=1;a.dest_vgpr=8;good.fetch->attributes.push_back(a);good.register_reads.push_back(good.register_reads[0]);Check(FromSnapshot(good,environment).reads.size()==1,"identical repeated read is canonicalized");}
 {auto bad=r;bad.reads.push_back(bad.reads[0]);Reject([&]{Encode(bad);},"noncanonical disk duplicate refused");}
 {auto bad=r;bad.expected.base_snapshot_bytes=2*1024*1024;Reject([&]{Encode(bad);},"snapshot total bound refused");}
 {auto bad=s;bad.flat.resize(Shader::CompileSnapshot::MaxFlatBytes/4+1);bad.flat_dwords=bad.flat.size();Reject([&]{FromSnapshot(bad,environment);},"owned vector bound refused before copy");}
 {auto bad=bytes;bad.back()^=std::byte{1};Reject([&]{Decode(bad,r.expected);},"tampered descriptor digest refused");}
 {auto bad=bytes;bad.push_back(std::byte{});Reject([&]{Decode(bad,r.expected);},"trailing payload refused");}
 {auto changed=r.expected;changed.profile[73]=0;Reject([&]{Decode(bytes,changed);},"old Profile cannot hit current capture");}
 {auto changed=r.expected;changed.environment.compiler[0]^=1;Reject([&]{Decode(bytes,changed);},"compiler build mismatch refused");}
 {auto changed=s;changed.program_base++;Check(DescribeSnapshot(changed,environment).owned_context_sha256!=r.expected.owned_context_sha256,"program-base scalar contributes to context identity");}
 {auto changed=s;changed.user_data[0]++;auto before=changed.register_reads;Reject([&]{RestoreSnapshotReads(changed,r,r.expected);},"companion snapshot mismatch refused");Check(std::memcmp(&changed.register_reads[0].buffer,&before[0].buffer,16)==0,"failed restore preserves existing reads");}
 {auto changed=r;changed.reads[0].words[0]++;Reject([&]{Encode(changed);},"descriptor edits cannot retain prior identity");changed.expected.register_reads_sha256=ReadsDigest(changed.reads);Check(Key(changed.expected)!=Key(r.expected),"actual descriptor bytes participate in storage key");}
 std::string error;Check(!Store::Open(root,{},error),"no store without development marker");File(root/MarkerName,DevelopmentMarker);auto store=Store::Open(root,{},error);Check(bool(store),"explicit development store opens");
 Check(store->Write(r,error),"atomic writer publishes");auto path=root/DirectoryName/(Key(r.expected)+".befetch");Check(store->Read(r.expected,error)==std::optional<Record>(r),"owned disk reader exact input identity");auto previous=Bytes(path);
 auto hard=root/"authored-hardlink";std::filesystem::create_hard_link(path,hard);Check(!store->Read(r.expected,error)&&!store->Write(r,error),"multiply linked record refused");std::filesystem::remove(hard);
 auto one=Store::Open(root,{MaxStoreBytes,1},error);Check(bool(one)&&!one->Write(r,error),"file count budget refuses replacement transient slot");Check(Bytes(path)==previous,"file count refusal preserves old entry");
 auto occupied=root/DirectoryName/(".pending-"+std::to_string(getpid())+"-1");File(occupied,"authored older pending");auto older=Bytes(occupied);Check(!store->Write(r,error),"exclusive pending collision refused");Check(Bytes(occupied)==older,"failed exclusive creation cannot delete somebody else's pending");Check(Bytes(path)==previous,"pending collision preserves committed record");std::filesystem::remove(occupied);
 int cancels=0;Check(!store->Write(r,error,{CancelAfterWrite,&cancels}),"cancellation after fsync refuses publication");Check(Bytes(path)==previous,"failed publication preserves old bytes");
 size_t pending=0;for(auto&e:std::filesystem::directory_iterator(root/DirectoryName))pending+=e.path().filename().string().starts_with(".pending-");Check(pending==0,"failed writer removes its own temp");
 auto small=Store::Open(root,{previous.size(),10},error);Check(bool(small)&&!small->Write(r,error),"total disk budget refuses transient growth");Check(Bytes(path)==previous,"budget refusal never evicts prior record");
 std::filesystem::remove(path);std::filesystem::create_symlink(root/MarkerName,path);Check(!store->Read(r.expected,error)&&!store->Write(r,error),"symlink target refused on read and write");Check(std::filesystem::is_symlink(path),"symlink target never followed or replaced on refusal");std::filesystem::remove(path);Check(store->Write(r,error),"valid writer recovers after authored obstruction");
 auto bad=Bytes(path);bad.back()^=std::byte{1};{std::ofstream f(path,std::ios::binary);f.write(reinterpret_cast<const char*>(bad.data()),bad.size());}Check(!store->Read(r.expected,error),"disk corruption refused");
 auto link=root.parent_path()/(root.filename().string()+"-link");std::filesystem::create_directory_symlink(root,link);Check(!Store::Open(link,{},error),"linked development root refused");std::filesystem::remove(link);
 chmod((root/MarkerName).c_str(),0644);Check(!Store::Open(root,{},error),"nonprivate marker refused");
 std::cout<<"FETCH_STORE_RESULT {\"passed\":"<<passed<<",\"game_started\":false,\"gpu_started\":false,\"guest_reads\":false}\n";return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
