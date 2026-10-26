// SPDX-License-Identifier: GPL-2.0-or-later
#include "fetch_register_store.h"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <set>
#include <stdexcept>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>

namespace BbFetchStore {namespace {
void Need(bool value,const char* message){if(!value)throw std::runtime_error(message);}
bool Nonzero(const Digest& d){return std::any_of(d.begin(),d.end(),[](auto b){return b!=0;});}
struct Fd {int n=-1;explicit Fd(int v=-1):n(v){}~Fd(){if(n>=0)close(n);}Fd(const Fd&)=delete;Fd&operator=(const Fd&)=delete;int Release(){return std::exchange(n,-1);}};
bool Owned(const struct stat&s,bool dir){return (dir?S_ISDIR(s.st_mode):S_ISREG(s.st_mode))&&s.st_uid==getuid()&&(s.st_mode&0777)==(dir?0700:0600)&&(dir||s.st_nlink==1);}
struct Writer {
 std::vector<std::byte> b;
 void U8(uint8_t n){Need(b.size()<MaxRecordBytes,"record byte budget");b.push_back(std::byte(n));}
 void U32(uint32_t n){for(unsigned i=0;i<4;++i)U8(n>>(8*i));}
 void U64(uint64_t n){for(unsigned i=0;i<8;++i)U8(n>>(8*i));}
 template<class T>void Bytes(const T& a){for(auto n:a)U8(uint8_t(n));}
};
struct Reader {
 std::span<const std::byte> b;size_t at{};
 uint8_t U8(){Need(at<b.size(),"truncated record");return std::to_integer<uint8_t>(b[at++]);}
 uint32_t U32(){uint32_t n=0;for(unsigned i=0;i<4;++i)n|=uint32_t(U8())<<(8*i);return n;}
 uint64_t U64(){uint64_t n=0;for(unsigned i=0;i<8;++i)n|=uint64_t(U8())<<(8*i);return n;}
 template<class T>void Bytes(T&a){for(auto&n:a)n=U8();}
};
void ValidateExpected(const Expected&e){
 for(const auto*d:{&e.environment.game,&e.environment.compiler,&e.environment.abi,&e.environment.compile_inputs,&e.code_sha256,&e.owned_context_sha256,&e.register_reads_sha256})Need(Nonzero(*d),"missing complete identity digest");
 Need((e.hardware_stage==1||e.hardware_stage==3||e.hardware_stage==5)&&e.logical_stage==3,"fetch vertex/export/local stage only");
 Need(e.program_hash&&e.code_bytes&&e.code_bytes<=512*1024&&e.code_bytes%4==0,"owned code extent");
 Need(e.base_snapshot_bytes&&e.base_snapshot_bytes<=2*1024*1024&&e.attributes.size()<=MaxAttributes&&!e.attributes.empty(),"snapshot/attribute budget");
 Need(e.fetch_code_bytes&&e.fetch_code_bytes<=32*1024&&e.fetch_code_bytes%4==0,"fetch code extent");
 Need(e.vertex_offset>=-1&&e.vertex_offset<104&&e.instance_offset>=-1&&e.instance_offset<104,"fetch SGPR offsets");
 for(size_t i=0;i<e.attributes.size();++i){const auto&a=e.attributes[i].fields;
  Need(a[0]==i&&a[2]>=1&&a[2]<=4&&a[3]<=14&&a[3]%2==0&&a[5]<=3,"fetch attribute fields");
 }
 // Canonical Profile: six u32 fields, one u64, then every current boolean field.
 for(size_t i=32;i<e.profile.size();++i)Need(e.profile[i]<=1,"profile boolean representation");
}
void PutExpected(Writer&w,const Expected&e){
 ValidateExpected(e);for(auto*d:{&e.environment.game,&e.environment.compiler,&e.environment.abi,&e.environment.compile_inputs})w.Bytes(*d);
 w.U64(e.program_hash);w.U32(e.hardware_stage);w.U32(e.logical_stage);w.U32(e.code_bytes);w.U32(e.base_snapshot_bytes);w.Bytes(e.code_sha256);w.Bytes(e.owned_context_sha256);w.Bytes(e.register_reads_sha256);w.Bytes(e.profile);
 for(auto n:e.bindings)w.U32(n);w.U32(e.fetch_code_bytes);w.U8(uint8_t(e.vertex_offset));w.U8(uint8_t(e.instance_offset));w.U32(e.attributes.size());for(auto&a:e.attributes)w.Bytes(a.fields);
}
Expected GetExpected(Reader&r){
 Expected e;for(auto*d:{&e.environment.game,&e.environment.compiler,&e.environment.abi,&e.environment.compile_inputs})r.Bytes(*d);
 e.program_hash=r.U64();e.hardware_stage=r.U32();e.logical_stage=r.U32();e.code_bytes=r.U32();e.base_snapshot_bytes=r.U32();r.Bytes(e.code_sha256);r.Bytes(e.owned_context_sha256);r.Bytes(e.register_reads_sha256);r.Bytes(e.profile);
 for(auto&n:e.bindings)n=r.U32();e.fetch_code_bytes=r.U32();e.vertex_offset=int8_t(r.U8());e.instance_offset=int8_t(r.U8());auto count=r.U32();Need(count<=MaxAttributes,"attribute count budget");e.attributes.resize(count);for(auto&a:e.attributes)r.Bytes(a.fields);ValidateExpected(e);return e;
}
void ValidateReads(const Record&r){
 ValidateExpected(r.expected);Need(r.reads.size()<=MaxAttributes&&!r.reads.empty(),"read count budget");
 std::set<std::pair<uint32_t,uint32_t>> wanted,actual;for(auto&a:r.expected.attributes)wanted.emplace(a.fields[3],a.fields[4]);
 std::pair<uint32_t,uint32_t> previous{};bool first=true;
 for(auto&read:r.reads){const auto key=std::pair{read.pointer_index,read.dword_offset};Need(first||previous<key,"duplicate/unsorted register read");first=false;previous=key;actual.insert(key);}
 Need(actual==wanted,"missing or extraneous captured fetch read");
 Need(ReadsDigest(r.reads)==r.expected.register_reads_sha256,"captured descriptor digest mismatch");
 // Native RegisterRead is two u32 plus the original 16-byte Buffer. No padding in this ABI.
 Need(r.expected.base_snapshot_bytes<=2*1024*1024-r.reads.size()*24,"snapshot total budget");
}
std::vector<std::byte> ReadAt(int dir,const std::string& name,size_t max){
 Fd fd(openat(dir,name.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC));Need(fd.n>=0,"record open");struct stat before{},after{};Need(fstat(fd.n,&before)==0&&Owned(before,false)&&before.st_size>0&&uint64_t(before.st_size)<=max,"owned bounded regular file");
 std::vector<std::byte>b(before.st_size);size_t at=0;while(at<b.size()){auto n=read(fd.n,b.data()+at,b.size()-at);if(n<0&&errno==EINTR)continue;Need(n>0,"record read");at+=n;}
 Need(fstat(fd.n,&after)==0&&before.st_dev==after.st_dev&&before.st_ino==after.st_ino&&before.st_size==after.st_size&&before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec&&before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec,"record changed during read");return b;
}
Fd Lock(int dir){Fd lock(openat(dir,".lock",O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600));struct stat st{};Need(lock.n>=0&&fstat(lock.n,&st)==0&&Owned(st,false),"owned store lock");Need(flock(lock.n,LOCK_EX|LOCK_NB)==0,"store busy");return Fd(lock.Release());}
void Budget(int dir,const Limits&limits,size_t pending){
 // Independent directory offset: dup would share the original descriptor's offset.
 int scan=openat(dir,".",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);Need(scan>=0,"budget directory open");DIR*d=fdopendir(scan);if(!d){close(scan);throw std::runtime_error("budget directory stream");}
 size_t files=0,total=0;bool ok=true;errno=0;
 while(auto*entry=readdir(d)){
  std::string name=entry->d_name;if(name=="."||name==".."||name==".lock")continue;struct stat st{};
  if(++files>limits.records||fstatat(dir,name.c_str(),&st,AT_SYMLINK_NOFOLLOW)||!Owned(st,false)||st.st_size<0||uint64_t(st.st_size)>MaxRecordBytes||size_t(st.st_size)>limits.bytes-total){ok=false;break;}
  total+=st.st_size;
 }
 if(errno)ok=false;closedir(d);Need(ok&&files<limits.records&&pending<=limits.bytes-total,"development store budget exhausted (no eviction)");
}
} // namespace
Digest ReadsDigest(std::span<const RegisterRead>reads){Need(reads.size()<=MaxAttributes,"read digest count bound");Writer w;w.U32(reads.size());for(auto&r:reads){w.U32(r.pointer_index);w.U32(r.dword_offset);for(auto n:r.words)w.U32(n);}return BbCpuCache::Hash(w.b);}
std::string Key(const Expected&e){Writer w;for(char c:std::string_view("BE-FETCH-IDENTITY-1"))w.U8(c);PutExpected(w,e);auto d=BbCpuCache::Hash(w.b);constexpr char hex[]="0123456789abcdef";std::string s;for(auto b:d){s+=hex[b>>4];s+=hex[b&15];}return s;}
std::vector<std::byte> Encode(const Record&r){
 ValidateReads(r);Writer payload;PutExpected(payload,r.expected);payload.U32(r.reads.size());for(auto&read:r.reads){payload.U32(read.pointer_index);payload.U32(read.dword_offset);for(auto n:read.words)payload.U32(n);}
 Writer output;for(char c:std::string_view("BEFETCH1"))output.U8(c);output.U32(1);output.U32(payload.b.size());output.Bytes(BbCpuCache::Hash(payload.b));for(auto b:payload.b)output.U8(std::to_integer<uint8_t>(b));return output.b;
}
Record Decode(std::span<const std::byte>b,const Expected&expected){
 Need(b.size()>=48&&b.size()<=MaxRecordBytes,"record file extent");Reader h{b};for(char c:std::string_view("BEFETCH1"))Need(h.U8()==uint8_t(c),"record magic");Need(h.U32()==1,"record version");auto bytes=h.U32();Digest sha{};h.Bytes(sha);Need(bytes==b.size()-h.at,"record payload extent");auto payload=b.subspan(h.at);Need(BbCpuCache::Hash(payload)==sha,"record content digest");Reader p{payload};Record r;r.expected=GetExpected(p);Need(r.expected==expected,"complete compiler input identity mismatch");auto count=p.U32();Need(count<=MaxAttributes,"read vector budget");r.reads.resize(count);for(auto&read:r.reads){read.pointer_index=p.U32();read.dword_offset=p.U32();for(auto&n:read.words)n=p.U32();}Need(p.at==payload.size(),"record trailing bytes");ValidateReads(r);return r;
}
Store::~Store(){if(directory>=0)close(directory);}
Store::Store(Store&&s)noexcept:directory(std::exchange(s.directory,-1)),limits(s.limits){}
Store&Store::operator=(Store&&s)noexcept{if(this!=&s){if(directory>=0)close(directory);directory=std::exchange(s.directory,-1);limits=s.limits;}return*this;}
std::optional<Store> Store::Open(const std::filesystem::path&root,Limits limits,std::string&error)noexcept{try{
 error.clear();Need(limits.bytes&&limits.bytes<=MaxStoreBytes&&limits.records&&limits.records<=MaxRecords,"store limit domain");Need(root.is_absolute()&&root.lexically_normal()==root&&std::filesystem::canonical(root)==root,"canonical development root");Fd parent(open(root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));struct stat st{};Need(parent.n>=0&&fstat(parent.n,&st)==0&&Owned(st,true),"owned private development root");
 const auto marker=ReadAt(parent.n,MarkerName,sizeof(DevelopmentMarker)-1);Need(marker.size()==sizeof(DevelopmentMarker)-1&&memcmp(marker.data(),DevelopmentMarker,marker.size())==0,"explicit development marker required");
 Need(mkdirat(parent.n,DirectoryName,0700)==0||errno==EEXIST,"development child directory creation");Fd child(openat(parent.n,DirectoryName,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));Need(child.n>=0&&fstat(child.n,&st)==0&&Owned(st,true),"owned private store directory");return Store(child.Release(),limits);
 }catch(const std::exception&e){error=e.what();return{};}}
bool Store::Write(const Record&r,std::string&error,Cancellation cancelled)const noexcept{
 std::string temp;bool owns_temp=false;try{
  error.clear();Need(directory>=0,"store is closed");auto bytes=Encode(r);auto lock=Lock(directory);Need(!cancelled(),"publication cancelled");Budget(directory,limits,bytes.size());const auto name=Key(r.expected)+".befetch";
  struct stat old{};if(fstatat(directory,name.c_str(),&old,AT_SYMLINK_NOFOLLOW)==0)Need(Owned(old,false)&&old.st_size<=MaxRecordBytes,"old target must be an owned regular file");else Need(errno==ENOENT,"old target stat");
  static std::atomic<uint64_t>sequence{};temp=".pending-"+std::to_string(getpid())+"-"+std::to_string(sequence++);Fd fd(openat(directory,temp.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW|O_CLOEXEC,0600));Need(fd.n>=0,"exclusive pending output");owns_temp=true;size_t at=0;while(at<bytes.size()){auto n=write(fd.n,bytes.data()+at,bytes.size()-at);if(n<0&&errno==EINTR)continue;Need(n>0,"pending write");at+=n;}
  Need(fsync(fd.n)==0,"pending fsync");Need(close(fd.Release())==0,"pending close");Need(ReadAt(directory,temp,MaxRecordBytes)==bytes,"pending readback equality");Decode(bytes,r.expected);Need(!cancelled(),"publication cancelled");
  // Same validated, pinned directory; all failures before this commit retain the old file.
  Need(renameat(directory,temp.c_str(),directory,name.c_str())==0,"atomic publication");temp.clear();return true;
 }catch(const std::exception&e){error=e.what();if(owns_temp&&!temp.empty())unlinkat(directory,temp.c_str(),0);return false;}}
std::optional<Record>Store::Read(const Expected&e,std::string&error)const noexcept{try{error.clear();Need(directory>=0,"store is closed");auto lock=Lock(directory);return Decode(ReadAt(directory,Key(e)+".befetch",MaxRecordBytes),e);}catch(const std::exception&ex){error=ex.what();return{};}}
} // namespace BbFetchStore
