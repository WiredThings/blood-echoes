// SPDX-License-Identifier: GPL-2.0-or-later
#include "fetch_compile_producer.h"
#include "fetch_compile_observer.h"
#include "fetch_companion_mailbox.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <set>
#include <sys/qos.h>
#include <sys/stat.h>
#include <thread>
#include <tuple>
#include <unistd.h>

extern char**environ;
namespace BbFetchCapture {namespace {
using namespace BbFetchStore;
bool Nonzero(const Digest&d){return std::any_of(d.begin(),d.end(),[](auto n){return n!=0;});}
void Need(bool v,const char*s){RequireSnapshot(v,s);}
struct File {
 int fd=-1;struct stat before{};
 File(const std::filesystem::path&p,size_t cap,bool private_file){
  Need(p.is_absolute()&&std::filesystem::canonical(p)==p,"canonical producer source");fd=open(p.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
  bool ok=fd>=0&&fstat(fd,&before)==0&&S_ISREG(before.st_mode)&&before.st_uid==getuid()&&before.st_nlink==1&&before.st_size>0&&uint64_t(before.st_size)<=cap&&(!private_file||(before.st_mode&0777)==0600);
  if(!ok){if(fd>=0)close(fd);fd=-1;throw std::runtime_error("owned bounded producer source");}
 }
 ~File(){if(fd>=0)close(fd);}File(const File&)=delete;
 void Stable(){struct stat after{};Need(fstat(fd,&after)==0&&before.st_dev==after.st_dev&&before.st_ino==after.st_ino&&before.st_size==after.st_size&&before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec&&before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec,"producer source changed");}
};
std::vector<std::byte>ReadFile(const std::filesystem::path&p,size_t cap,bool priv){File f(p,cap,priv);std::vector<std::byte>v(f.before.st_size);size_t at=0;while(at<v.size()){auto n=read(f.fd,v.data()+at,v.size()-at);if(n<0&&errno==EINTR)continue;Need(n>0,"producer source read");at+=n;}f.Stable();return v;}
Digest HashFile(const std::filesystem::path&p,size_t cap){File f(p,cap,false);CC_SHA256_CTX c;CC_SHA256_Init(&c);std::array<std::byte,65536>b;for(;;){auto n=read(f.fd,b.data(),b.size());if(n<0&&errno==EINTR)continue;Need(n>=0,"producer image read");if(!n)break;CC_SHA256_Update(&c,b.data(),n);}f.Stable();Digest out{};CC_SHA256_Final(out.data(),&c);return out;}
struct Reader {std::span<const std::byte>b;size_t at{};uint8_t U8(){Need(at<b.size(),"truncated producer receipt");return std::to_integer<uint8_t>(b[at++]);}uint32_t U32(){uint32_t n=0;for(unsigned i=0;i<4;++i)n|=uint32_t(U8())<<(i*8);return n;}uint64_t U64(){uint64_t n=0;for(unsigned i=0;i<8;++i)n|=uint64_t(U8())<<(i*8);return n;}Digest D(){Digest d;for(auto&n:d)n=U8();Need(Nonzero(d),"missing producer source identity");return d;}};
std::pair<std::string,std::string>SfoIdentity(std::span<const std::byte>b){
 auto at32=[&](size_t at){Need(at<=b.size()&&4<=b.size()-at,"SFO integer span");Reader r{b.subspan(at,4)};return r.U32();};
 auto at16=[&](size_t at){Need(at<=b.size()&&2<=b.size()-at,"SFO short span");return uint32_t(std::to_integer<uint8_t>(b[at]))|uint32_t(std::to_integer<uint8_t>(b[at+1]))<<8;};
 Need(b.size()>=20&&at32(0)==0x46535000&&at32(4)==0x101,"SFO header");auto keys=at32(8),data=at32(12),count=at32(16);Need(count<=128&&20+size_t(count)*16<=keys&&keys<=data&&data<=b.size(),"SFO tables");
 auto string=[&](size_t at,size_t end){Need(at<end&&end<=b.size(),"SFO string span");std::string out;while(at<end&&b[at]!=std::byte{}){auto c=std::to_integer<uint8_t>(b[at++]);Need(c>=32&&c<=126&&out.size()<128,"SFO ASCII identity");out+=char(c);}Need(at<end&&b[at]==std::byte{},"SFO string terminator");return out;};
 std::string title,version;for(size_t i=0;i<count;++i){const auto at=20+i*16;auto key=string(keys+at16(at),data);if(key!="TITLE_ID"&&key!="APP_VER")continue;auto length=at32(at+4),capacity=at32(at+8),offset=at32(at+12);Need(at16(at+2)==0x204&&length&&length<=capacity&&offset<=b.size()-data&&capacity<=b.size()-data-offset,"SFO identity value");auto value=string(data+offset,data+offset+length);if(key=="TITLE_ID"){Need(title.empty(),"duplicate title identity");title=value;}else{Need(version.empty(),"duplicate version identity");version=value;}}
 Need(title.size()==9&&title.starts_with("CUSA")&&std::all_of(title.begin()+4,title.end(),[](char c){return c>='0'&&c<='9';})&&version=="01.09","fixed 1.09 title identity");return{title,version};
}
auto Order(const ProgramProof&p){return std::tuple{p.stage,p.hash,p.code};}
bool ReceiptAllows(void*context,uint64_t hash,uint32_t stage,const Digest&code,size_t bytes)noexcept{auto&r=*static_cast<const Receipt*>(context);ProgramProof wanted{stage,hash,code,uint32_t(bytes)};auto it=std::lower_bound(r.programs.begin(),r.programs.end(),wanted,[](auto&a,auto&b){return Order(a)<Order(b);});return it!=r.programs.end()&&*it==wanted;}
BbFetchCompanion::SourceRequirement Requirements(Receipt&r,const Shader::Profile&profile){BbFetchCompanion::SourceRequirement source;source.game=r.environment.game;source.compiler=r.environment.compiler;source.abi=r.environment.abi;source.profile=CanonicalProfile(profile);source.require_association=true;source.allows=ReceiptAllows;source.context=&r;return source;}
Digest Abi(){std::string value="BEFCOMP1/full-native-producer-1/Runtime-before-after-vertex-export-local-1/Profile-u32x6-u64-bool-fields:";
#define MODULE_PROFILE_BOOL_FIELD(field) value+=#field ";";
#include "module_profile_bool_fields.inc"
#undef MODULE_PROFILE_BOOL_FIELD
 value+=std::to_string(sizeof(Shader::CompileSnapshot))+":"+std::to_string(sizeof(Shader::CompileSnapshot::RegisterRead))+":"+std::to_string(sizeof(AmdGpu::Buffer))+":"+std::to_string(uint32_t(Shader::HwStage::Vertex))+":"+std::to_string(uint32_t(Shader::HwStage::Export))+":"+std::to_string(uint32_t(Shader::HwStage::Local))+":"+std::to_string(uint32_t(Shader::SwStage::Vertex));
 return BbCpuCache::Hash(std::as_bytes(std::span(value)));}
Digest CompilerEnvironment(){
 std::vector<std::string>values;std::set<std::string>keys;size_t bytes=0;for(char**p=environ;*p;++p){std::string_view s=*p;if(!s.starts_with("BB_"))continue;auto equal=s.find('=');Need(equal!=s.npos&&keys.insert(std::string(s.substr(0,equal))).second,"ambiguous compile environment");Need(values.size()<256&&s.size()<=65536-bytes,"compile environment bound");bytes+=s.size();values.emplace_back(s);}std::sort(values.begin(),values.end());
 std::vector<std::byte>encoded;for(auto&s:values){for(unsigned i=0;i<4;++i)encoded.push_back(std::byte(s.size()>>(i*8)));for(char c:s)encoded.push_back(std::byte(c));}
 // Absence is meaningful, but not used as a substitute game/compiler/ABI identity.
 for(char c:std::string_view("BE-FETCH-BB-ENV-1"))encoded.push_back(std::byte(c));return BbCpuCache::Hash(encoded);
}
struct Service {
 const std::filesystem::path root_path,game_path;
 Receipt receipt;std::optional<BbFetchCompanion::Store>store;Digest environment;
 BbFetchCompanion::InflightBudget budget;BbFetchCompanion::Mailbox mailbox;
 std::atomic<uint64_t>accepted{},rejected{},written{},failed{};std::array<std::atomic<uint64_t>,static_cast<unsigned>(Refusal::Count)>refusals{};std::jthread worker;
 Service(const std::filesystem::path&root,const std::filesystem::path&game):root_path(root),game_path(game),receipt(VerifyReceipt(root,game)),environment(CompilerEnvironment()){
  std::string error;store=BbFetchCompanion::Store::Open(root,{},error);Need(bool(store),"private producer companion store unavailable");
  worker=std::jthread([this]{pthread_set_qos_class_self_np(QOS_CLASS_UTILITY,0);while(auto record=mailbox.WaitPop()){
   try{if(record->warm){auto&w=*record->warm;std::vector<uint32_t>spv;Need(w.reference.load&&w.reference.load(spv)&&spv.size()==w.reference.words&&WordDigest(spv)==w.reference.sha,"warm original SPV reference changed/unavailable");auto source=Requirements(receipt,w.input.profile);source.require_association=false;record->companion=BbFetchCompanion::MakeOwnedSpirv(std::move(w.input),w.after,w.environment,w.masks,source,std::move(spv));record->companion.association=std::move(w.association);record->warm.reset();}
   auto source=Requirements(receipt,record->companion.input.profile);std::string error;if(store->Write(record->companion,source,error))++written;else++failed;}catch(...){++failed;}
  }});
 }
 ~Service(){mailbox.Close();} // closes predicate under its wait mutex, then drains/joins
};
struct Slot {std::mutex mutex;std::unique_ptr<Service>owner;std::atomic<Service*>ready{};std::atomic<bool>environment_attempted{};};
Slot&State(){static Slot slot;return slot;}
bool Configure(const std::filesystem::path&root,const std::filesystem::path&game,std::string&error)noexcept{try{
 auto&slot=State();std::lock_guard lock(slot.mutex);if(slot.owner){Need(slot.owner->root_path==root&&slot.owner->game_path==game,"capture cohort cannot change while game process owns it");error.clear();return true;}
 auto service=std::make_unique<Service>(root,game);slot.owner=std::move(service);slot.ready.store(slot.owner.get(),std::memory_order_release);error.clear();return true;
 }catch(const std::exception&e){error=e.what();return false;}}
Service*Get()noexcept{auto&slot=State();if(auto*ready=slot.ready.load(std::memory_order_acquire))return ready;if(slot.environment_attempted.exchange(true))return nullptr;
 const char*root=getenv("BB_FETCH_CAPTURE_ROOT"),*game=getenv("BB_FETCH_CAPTURE_GAME_ROOT");if(!root||!*root||!game||!*game)return nullptr;std::string error;if(!Configure(root,game,error))std::fprintf(stderr,"BB_FETCH_CAPTURE disabled: %s\n",error.c_str());return slot.ready.load(std::memory_order_acquire);
}
} // namespace
Digest ImageDigest(){Dl_info info{};Need(dladdr(reinterpret_cast<const void*>(&ImageDigest),&info)&&info.dli_fname,"loaded backend image unavailable");return HashFile(std::filesystem::canonical(info.dli_fname),512*1024*1024);}
static Receipt VerifyReceiptAgainst(const std::filesystem::path&root,const std::filesystem::path&game,const Digest&expected_image){
 Need(root.is_absolute()&&std::filesystem::canonical(root)==root&&game.is_absolute()&&std::filesystem::canonical(game)==game,"canonical explicit QA producer roots");struct stat st{};Need(lstat(root.c_str(),&st)==0&&S_ISDIR(st.st_mode)&&st.st_uid==getuid()&&(st.st_mode&0777)==0700,"private development producer root");
 auto marker=ReadFile(root/MarkerName,sizeof(DevelopmentMarker)-1,true);Need(marker.size()==sizeof(DevelopmentMarker)-1&&memcmp(marker.data(),DevelopmentMarker,marker.size())==0,"producer development marker");
 auto bytes=ReadFile(root/"fetch-producer.receipt",160+32768*48,true);Reader r{bytes};for(char c:std::string_view("BEFPROD1"))Need(r.U8()==uint8_t(c),"producer receipt magic");Need(r.U32()==1,"producer receipt version");auto count=r.U32();Need(count&&count<=32768&&bytes.size()==160+size_t(count)*48,"producer receipt extent");
 auto sfo=r.D(),image=r.D(),index=r.D(),catalog=r.D();std::string title,version;for(unsigned n=0;n<9;++n)title+=char(r.U8());for(unsigned n=0;n<5;++n)version+=char(r.U8());Need(r.U8()==0&&r.U8()==0,"receipt identity padding");
 auto actual_sfo=ReadFile(game/"sce_sys/param.sfo",65536,false);Need(sfo==BbCpuCache::Hash(actual_sfo)&&SfoIdentity(actual_sfo)==std::pair{title,version},"current game SFO identity mismatch");Need(image==expected_image,"selected backend image identity mismatch");
 Receipt result;result.environment.compiler=image;result.environment.abi=Abi();std::array<Digest,4>game_inputs{sfo,index,catalog,BbCpuCache::Hash(bytes)};result.environment.game=BbCpuCache::Hash(std::as_bytes(std::span(game_inputs)));
 result.programs.reserve(count);for(size_t i=0;i<count;++i){ProgramProof p;p.stage=r.U32();p.hash=r.U64();p.code=r.D();p.bytes=r.U32();Need(p.stage<=6&&p.hash&&p.bytes&&p.bytes<=512*1024&&p.bytes%4==0,"static program proof domain");Need(result.programs.empty()||Order(result.programs.back())<Order(p),"duplicate or unordered static program proof");result.programs.push_back(p);}Need(r.at==bytes.size(),"producer trailing receipt bytes");return result;
}
Receipt VerifyReceipt(const std::filesystem::path&root,const std::filesystem::path&game){return VerifyReceiptAgainst(root,game,ImageDigest());}
Receipt VerifyPreparationReceipt(const std::filesystem::path&root,const std::filesystem::path&game,const std::filesystem::path&backend){Need(backend.is_absolute()&&std::filesystem::canonical(backend)==backend,"canonical selected backend file");return VerifyReceiptAgainst(root,game,HashFile(backend,512*1024*1024));}
BbFetchCompanion::SourceRequirement SourceForReceipt(Receipt&r,const Shader::Profile&p){return Requirements(r,p);}
bool RecordQueue::Push(BbFetchStore::Record&&r)noexcept{try{if(r.expected.attributes.size()>MaxAttributes||r.expected.attributes.capacity()>MaxAttributes||r.reads.size()>MaxAttributes||r.reads.capacity()>MaxAttributes)return false;std::unique_lock lock(mutex,std::try_to_lock);if(!lock||count==slots.size())return false;slots[(head+count)%slots.size()]=std::move(r);++count;available.store(count,std::memory_order_release);return true;}catch(...){return false;}}
std::optional<BbFetchStore::Record>RecordQueue::Pop()noexcept{try{std::unique_lock lock(mutex,std::try_to_lock);if(!lock||!count)return{};auto r=std::move(slots[head]);slots[head].reset();head=(head+1)%slots.size();--count;available.store(count,std::memory_order_release);return r;}catch(...){return{};}}
bool RecordMailbox::Push(BbFetchStore::Record&&r)noexcept{try{
 std::unique_lock lock(wake_mutex,std::try_to_lock);if(!lock||closed||!queue.Push(std::move(r)))return false;
 lock.unlock();wake.notify_one();return true;
 }catch(...){return false;}}
std::optional<BbFetchStore::Record>RecordMailbox::WaitPop(){
 std::unique_lock lock(wake_mutex);wake.wait(lock,[&]{bool ready=closed||queue.Available();
#if defined(BB_FETCH_CAPTURE_TESTING) && BB_FETCH_CAPTURE_TESTING
  if(!ready&&before_wait)before_wait(test_context);
#endif
  return ready;});
 return queue.Pop(); // producer cannot mutate predicate between check and wait
}
void RecordMailbox::Close()noexcept{{std::lock_guard lock(wake_mutex);closed=true;}wake.notify_all();}
bool Producer::Enabled()noexcept{return Get()!=nullptr;}
bool Producer::Configure(const std::filesystem::path&root,const std::filesystem::path&game,std::string&error)noexcept{return BbFetchCapture::Configure(root,game,error);}
bool Producer::Allows(uint64_t hash,Shader::HwStage stage,const Digest&code,size_t bytes)noexcept{auto*s=Get();if(!s)return false;ProgramProof wanted{uint32_t(stage),hash,code,uint32_t(bytes)};auto it=std::lower_bound(s->receipt.programs.begin(),s->receipt.programs.end(),wanted,[](auto&a,auto&b){return Order(a)<Order(b);});bool allowed=it!=s->receipt.programs.end()&&*it==wanted;if(!allowed)++s->rejected;return allowed;}
void Producer::Reject()noexcept{if(auto*s=Get())++s->rejected;}
void Producer::Reject(Refusal reason)noexcept{if(auto*s=Get()){++s->rejected;const auto n=static_cast<unsigned>(reason);if(n<s->refusals.size())++s->refusals[n];}}
BbFetchCompanion::Lease Producer::Acquire()noexcept{if(auto*s=Get()){auto lease=s->budget.TryAcquire();if(!lease)++s->rejected;return lease;}return{};}
std::optional<BbFetchCompanion::Pending> Producer::Build(BbFetchCompanion::Lease lease,Shader::CompileSnapshot&&snapshot,const Shader::RuntimeInfo&after,std::span<const uint32_t>spv,const std::array<uint64_t,2>&masks)noexcept{auto*s=Get();if(!s)return{};try{
 Need(bool(lease),"full companion requires prior inflight reservation");Need(Allows(snapshot.hash,snapshot.hw_stage,WordDigest(snapshot.code),snapshot.code.size()*4),"captured program source identity");Need(CompilerEnvironment()==s->environment&&RuntimeMasks()==masks,"compile cohort changed");
 auto source=Requirements(s->receipt,snapshot.profile);source.require_association=false;auto companion=BbFetchCompanion::Make(std::move(snapshot),after,s->environment,masks,source,spv);return BbFetchCompanion::Pending{std::move(companion),std::move(lease)};
 }catch(...){++s->rejected;return{};}}
bool Producer::Publish(BbFetchCompanion::Pending&&pending,const Shader::StageSpecialization&spec,uint64_t permutation,uint64_t permutation_hash)noexcept{auto*s=Get();if(!s)return false;try{
 Need(CompilerEnvironment()==s->environment&&RuntimeMasks()==pending.companion.masks,"stage sealing compile cohort changed");pending.companion.association=Associate(spec,pending.companion.input.hash,permutation,permutation_hash,1);auto source=Requirements(s->receipt,pending.companion.input.profile);BbFetchCompanion::Validate(pending.companion,source);if(!s->mailbox.Push(std::move(pending))){++s->rejected;return false;}++s->accepted;return true;
 }catch(...){++s->rejected;return false;}}
bool Producer::PublishWarm(BbFetchCompanion::Lease lease,Shader::CompileSnapshot&&snapshot,const Shader::RuntimeInfo&after,const Shader::StageSpecialization&spec,uint64_t permutation,uint64_t permutation_hash,BbFetchCompanion::Reference&&reference,const std::array<uint64_t,2>&masks)noexcept{auto*s=Get();if(!s)return false;try{
 Need(bool(lease)&&reference.words>=5&&reference.load&&BbFetchCompanion::FitsCounts(snapshot.code.size(),snapshot.flat.size(),snapshot.walker.size(),reference.words),"warm companion budget/reference");Need(Allows(snapshot.hash,snapshot.hw_stage,WordDigest(snapshot.code),snapshot.code.size()*4)&&CompilerEnvironment()==s->environment&&RuntimeMasks()==masks,"warm source/cohort changed");BbFetchCompanion::Pending pending;pending.lease=std::move(lease);pending.warm.emplace();auto&w=*pending.warm;w.association=Associate(spec,snapshot.hash,permutation,permutation_hash,2);w.input=std::move(snapshot);w.after=after;w.environment=s->environment;w.masks=masks;w.reference=std::move(reference);if(!s->mailbox.Push(std::move(pending))){++s->rejected;return false;}++s->accepted;return true;
 }catch(...){++s->rejected;return false;}}
ProducerStats Producer::Stats()noexcept{if(auto*s=Get()){ProducerStats stats{s->accepted,s->rejected,s->written,s->failed};for(size_t i=0;i<stats.refusals.size();++i)stats.refusals[i]=s->refusals[i].load(std::memory_order_relaxed);return stats;}return{};}
} // namespace BbFetchCapture
extern "C" bool bb_fetch_companion_configure_v1(const char*root,const char*game)noexcept{if(!root||!*root||!game||!*game)return false;std::string error;const bool ok=BbFetchCapture::Producer::Configure(root,game,error);if(!ok)std::fprintf(stderr,"BB_FETCH_CAPTURE configure rejected: %s\n",error.c_str());return ok;}
