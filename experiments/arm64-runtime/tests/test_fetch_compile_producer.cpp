// SPDX-License-Identifier: GPL-2.0-or-later
// Authored normal GetSharp + observer/store path, no game or renderer linked.
#include "fetch_compile_observer.h"
#include "fetch_compile_producer.h"
#include "bbport_toggles.h"
#include <fstream>
#include <future>
#include <iostream>
#include <semaphore>
#include <sys/mman.h>
#include <unistd.h>

// Link-only negative seams: neither owned-reader nor speculative reader may be used here.
extern "C" {__thread sigjmp_buf*runtime_fault_recover=nullptr;uint64_t runtime_disabled_optimizations=0,runtime_experiment_bits=0;}
namespace Shader {
u64(*srt_guest_translate)(u64)=nullptr;
void ReadCompileSnapshotReg(const CompileSnapshot&,u32,u32,void*,size_t){throw std::runtime_error("unexpected owned-reader seam");}
}
using namespace BbFetchCapture;using namespace BbFetchStore;
namespace BbFetchCapture {
struct MailboxTestAccess {static void BeforeWait(RecordMailbox&m,void(*hook)(void*),void*context){m.before_wait=hook;m.test_context=context;}};
}
namespace {
int passed{};void Check(bool b,const char*s){if(!b)throw std::runtime_error(s);++passed;}
template<class F>void Reject(F f,const char*s){bool rejected=false;try{f();}catch(...){rejected=true;}Check(rejected,s);}
std::vector<std::byte>Bytes(const std::filesystem::path&p){std::ifstream f(p,std::ios::binary);std::string s((std::istreambuf_iterator<char>(f)),{});std::vector<std::byte>b(s.size());memcpy(b.data(),s.data(),s.size());return b;}
void Write(const std::filesystem::path&p,const std::vector<std::byte>&b){std::ofstream f(p,std::ios::binary);f.write(reinterpret_cast<const char*>(b.data()),b.size());}
struct Fixture {
 Shader::Info info{};Shader::RuntimeInfo runtime{};Shader::Profile profile{};
 std::array<uint32_t,16>ud{};std::array<uint32_t,2>code{0xBEEB03FF,0xBF810000};
 std::array<uint32_t,6>spv{0x07230203,0x10000,7,12,0,0x00010000};
 Shader::Gcn::FetchShaderData fetch;AmdGpu::Buffer*table{};size_t page{};
 Fixture(){
  page=sysconf(_SC_PAGESIZE);table=static_cast<AmdGpu::Buffer*>(mmap(nullptr,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0));Check(table!=MAP_FAILED,"authored descriptor page");std::array<uint32_t,4>raw{0x01234567,0x000C0000,8,0x00000000};memcpy(table,raw.data(),16);uint64_t address=reinterpret_cast<uint64_t>(table);memcpy(ud.data()+2,&address,8);
  info.hw_stage=Shader::HwStage::Vertex;info.sw_stage=Shader::SwStage::Vertex;info.pgm_hash=0x1234;info.pgm_base=0x10000;info.user_data=ud;info.flattened_ud_buf.assign(ud.begin(),ud.end());info.has_fetch_shader=true;
  runtime.Initialize(info.hw_stage,info.sw_stage);profile.two_level_bda=true;profile.relaxed_fragment_compute_float=true;fetch.size=32;fetch.attributes.push_back({0,4,3,2,0,0,7,11,4});
 }
 ~Fixture(){mprotect(table,page,PROT_READ|PROT_WRITE);munmap(table,page);}
};
struct Pause {std::binary_semaphore entered{0},release{0};std::atomic<bool>once{true};static void Hook(void*p){auto&s=*static_cast<Pause*>(p);if(s.once.exchange(false)){s.entered.release();s.release.acquire();}}};
}
int main(int argc,char**argv){try{
 Check(argc==3,"authored private roots");std::filesystem::path root=std::filesystem::canonical(argv[1]),game=std::filesystem::canonical(argv[2]);auto receipt_path=root/"fetch-producer.receipt";auto original=Bytes(receipt_path);auto receipt=VerifyReceipt(root,game);Check(receipt.programs.size()==1,"verified actual loaded image and SFO");
 {auto bad=original;bad[48]^=std::byte{1};Write(receipt_path,bad);Reject([&]{VerifyReceipt(root,game);},"wrong actual image rejected");Write(receipt_path,original);}
 {auto bad=original;std::fill(bad.begin()+80,bad.begin()+112,std::byte{});Write(receipt_path,bad);Reject([&]{VerifyReceipt(root,game);},"absent index proof rejected");Write(receipt_path,original);}
 {auto bad=original;bad[12]=std::byte{1};bad[13]=std::byte{128};Write(receipt_path,bad);Reject([&]{VerifyReceipt(root,game);},"oversized proof count rejected");Write(receipt_path,original);}
 {auto bad=original;bad[12]=std::byte{2};bad.insert(bad.end(),original.begin()+160,original.end());Write(receipt_path,bad);Reject([&]{VerifyReceipt(root,game);},"duplicate exact source proof rejected");Write(receipt_path,original);}
 auto sfo_path=game/"sce_sys/param.sfo";auto sfo=Bytes(sfo_path);{auto bad=sfo;bad.back()=std::byte{'x'};Write(sfo_path,bad);Reject([&]{VerifyReceipt(root,game);},"changed source SFO rejected");Write(sfo_path,sfo);}
 unsetenv("BB_FETCH_CAPTURE_ROOT");unsetenv("BB_FETCH_CAPTURE_GAME_ROOT");std::string configured;
 Check(!bb_fetch_companion_configure_v1(nullptr,game.c_str()),"automatic API rejects absent cohort");Check(Producer::Configure(root,game,configured),"original launch flow configures prepared cohort without environment digests");Check(Producer::Configure(root,game,configured),"same prepared cohort configure is idempotent");Check(!Producer::Configure(root/"other",game,configured),"live cohort cannot be silently changed");Check(Producer::Enabled(),"explicit real-source producer gate enabled");
 Fixture f;Observation observed(f.info);observed.Parsed(f.info,f.fetch);observed.Sharp(f.info,f.fetch.attributes[0],*f.table);Check(observed.Complete(),"fixed-array complete original read");auto reads=observed.Reads();Check(memcmp(&reads[0].buffer,f.table,16)==0,"observer preserves16 raw bytes");
 {Observation missing(f.info);missing.Parsed(f.info,f.fetch);Check(!missing.Complete(),"missing raw read refused");}
 {Observation conflict(f.info);conflict.Parsed(f.info,f.fetch);conflict.Sharp(f.info,f.fetch.attributes[0],*f.table);auto changed=*f.table;changed.num_records++;conflict.Sharp(f.info,f.fetch.attributes[0],changed);Check(conflict.Failed()&&!conflict.Complete(),"conflicting live reads refused");}
 {Observation changed(f.info);changed.Parsed(f.info,f.fetch);auto other=f.fetch;other.attributes[0].inst_offset++;changed.Parsed(f.info,other);Check(changed.Failed(),"all nine parsed fields are compared");}
 {Observation foreign(f.info);Shader::Info other;foreign.Parsed(other,f.fetch);foreign.Sharp(other,f.fetch.attributes[0],*f.table);Check(!foreign.Complete(),"unrelated Info callbacks cannot fill owner");}
 {Observation too_many(f.info);auto huge=f.fetch;huge.attributes.resize(33);too_many.Parsed(f.info,huge);Check(too_many.Failed(),"fixed32 attribute bound");}
 {auto a=CanonicalRuntime(f.runtime);auto changed=f.runtime;changed.sw.vs.step_rate_1++;Check(a!=CanonicalRuntime(changed),"step-rate compiler input identity");changed=f.runtime;changed.hw.vs.clip_disable=true;Check(a!=CanonicalRuntime(changed),"active vertex runtime identity");}
 {auto boundary=f.runtime;boundary.hw.vs.num_outputs=3;Check(!CanonicalRuntime(boundary).empty(),"MapOutputs three-vector boundary accepted");boundary.hw.vs.num_outputs=4;Reject([&]{CanonicalRuntime(boundary);},"four extra vectors exceed MapOutputs array");}
 {auto bad=f.runtime;bad.sw_stage=Shader::SwStage::TessellationControl;Reject([&]{CanonicalRuntime(bad);},"uncovered runtime does not get an empty identity");}
 {
  Shader::CompileSnapshot owned;owned.hw_stage=f.info.hw_stage;owned.sw_stage=f.info.sw_stage;owned.hash=f.info.pgm_hash;owned.program_base=f.info.pgm_base;owned.runtime=f.runtime;owned.profile=f.profile;owned.code.assign(f.code.begin(),f.code.end());owned.user_data.assign(f.ud.begin(),f.ud.end());owned.flat=owned.user_data;owned.flat_dwords=16;owned.fetch=f.fetch;owned.register_reads=observed.Reads();
  Record facts;facts.expected=DescribeSnapshot(owned,receipt.environment);for(auto&read:owned.register_reads){RegisterRead r;r.pointer_index=read.pointer_index;r.dword_offset=read.dword_offset;memcpy(r.words.data(),&read.buffer,16);facts.reads.push_back(r);}facts.expected.register_reads_sha256=ReadsDigest(facts.reads);auto environment=ImageDigest();auto before=BbCpuCache::Hash(CanonicalRuntime(owned.runtime));auto zero=CanonicalInputs(owned,facts,environment,{0,0},before);Check(zero!=CanonicalInputs(owned,facts,environment,{1ull<<63,0},before),"complete effective disabled mask enters canonical digest");Check(zero!=CanonicalInputs(owned,facts,environment,{0,1ull<<63},before),"complete effective experiment mask enters canonical digest");
 }
 {
  Check(!ObserveWarm(true,false,true,true),"seen last module skips scope despite unvisited old variant");Check(ObserveWarm(true,false,true,false),"unseen last module requests original-reader observation");Check(!ObserveWarm(false,true,false,false),"completed program skips even stale retry flag");
  bool retry=false;Check(!ObserveWarm(true,retry,true,true),"A seen skips observation before B first selection");retry=RetryWarm(retry,false,false,false);Check(retry&&ObserveWarm(true,retry,true,false),"B unseen selection arms next normal lookup");retry=RetryWarm(retry,true,true,true);Check(retry,"observed seen A does not starve alternating unseen B");retry=RetryWarm(retry,false,true,true);Check(!retry&&!ObserveWarm(true,retry,true,true),"next observed B seals then returns to stable seen path");retry=RetryWarm(false,false,false,true);Check(retry,"queue Busy leaves observed target retryable");
  auto before_reason=Producer::Stats();Producer::Reject(Refusal::ReferenceBudget);auto reasons=Producer::Stats();Check(reasons.refusals[static_cast<unsigned>(Refusal::ReferenceBudget)]==before_reason.refusals[static_cast<unsigned>(Refusal::ReferenceBudget)]+1&&reasons.written==before_reason.written,"permanent budget refusal is explicit rejection, never success");
 }
 // The original GetSharp makes the only V# read. Protect it before Finish: a second
 // live V# read by the collector would terminate this authored test with SIGSEGV.
 auto before=Producer::Stats();{
  CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);auto modified=f.fetch.attributes[0].GetSharp(f.info);Check(modified.base_address==f.table->base_address+7,"ordinary GetSharp applies original override");
  Check(mprotect(f.table,f.page,PROT_NONE)==0,"protect original Vsharp before collection");auto pending=scope.Finish(f.spv);Check(pending.has_value(),"complete capture retained until actual stage sealing");Shader::StageSpecialization actual;actual.info=&f.info;actual.runtime_info=f.runtime;actual.fetch_shader_data=f.fetch;auto labels=std::pair<uint64_t,uint64_t>{3,HashCombine(f.info.pgm_hash,uint64_t(3))};Producer::Publish(std::move(*pending),actual,labels.first,labels.second);Check(mprotect(f.table,f.page,PROT_READ|PROT_WRITE)==0,"collector never rereads protected Vsharp");
 }
 for(unsigned i=0;i<100&&Producer::Stats().written==before.written;++i)usleep(10000);
 auto after=Producer::Stats();Check(after.accepted==before.accepted+1&&after.written==before.written+1&&after.failed==before.failed,"actual new-compile observation reaches atomic disk writer");
 size_t records=0;for(auto&e:std::filesystem::directory_iterator(root/BbFetchCompanion::DirectoryName))records+=e.path().extension()==".befetchc";Check(records==1,"one real observed record published");
 {
  auto baseline=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);
  Shader::StageSpecialization actual;actual.info=&f.info;actual.runtime_info=f.runtime;actual.fetch_shader_data=f.fetch;auto reference=f.spv;auto sha=WordDigest(reference);BbFetchCompanion::Reference ref{sha,reference.size(),[reference](std::vector<uint32_t>&out){out.assign(reference.begin(),reference.end());return true;}};
  Check(mprotect(f.table,f.page,PROT_NONE)==0,"protect Vsharp before warm sealing");Check(scope.FinishWarm(actual,3,HashCombine(f.info.pgm_hash,uint64_t(3)),std::move(ref)),"warm existing-parser observation queued");Check(mprotect(f.table,f.page,PROT_READ|PROT_WRITE)==0,"warm capture performs no second guest read");
  for(unsigned n=0;n<100&&Producer::Stats().written==baseline.written;++n)usleep(10000);Check(Producer::Stats().written==baseline.written+1&&Producer::Stats().failed==baseline.failed,"warm actual SPV reference persisted");
  std::string error;auto store=BbFetchCompanion::Store::Open(root,{},error);auto keys=store->Keys(error);Check(keys.size()==2&&error.empty(),"automatic bounded companion enumeration");auto source=SourceForReceipt(receipt,f.profile);bool warm=false;for(auto&key:keys){auto c=store->Read(key,source,error);Check(bool(c),"preparer strict typed consumer reads sealed companion");warm|=c->association->reference_origin==2&&c->association->permutation_hash==HashCombine(f.info.pgm_hash,uint64_t(3));}Check(warm,"warm source provenance and actual permutation association retained");
 }
 {
  auto baseline=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);Shader::StageSpecialization actual;actual.info=&f.info;actual.runtime_info=f.runtime;actual.fetch_shader_data=f.fetch;auto sha=WordDigest(f.spv);auto reference=f.spv;reference.back()^=1;BbFetchCompanion::Reference bad{sha,reference.size(),[reference](std::vector<uint32_t>&out){out.assign(reference.begin(),reference.end());return true;}};
  Check(scope.FinishWarm(actual,9,HashCombine(f.info.pgm_hash,uint64_t(9)),std::move(bad)),"warm changed-source work admitted for validation");for(unsigned n=0;n<100&&Producer::Stats().failed==baseline.failed;++n)usleep(10000);Check(Producer::Stats().failed==baseline.failed+1&&Producer::Stats().written==baseline.written,"changed warm SPV SHA rejected without publication");
 }
 {auto base=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);scope.Finish(f.spv);Check(Producer::Stats().accepted==base.accepted&&Producer::Stats().rejected>base.rejected,"incomplete compilation observation refuses queue admission");}
 {auto base=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);f.ud[0]++;scope.Finish(f.spv);f.ud[0]--;Check(Producer::Stats().accepted==base.accepted,"changed owned userdata refuses capture");}
 {auto base=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);f.profile.two_level_bda=false;scope.Finish(f.spv);f.profile.two_level_bda=true;Check(Producer::Stats().accepted==base.accepted&&Producer::Stats().rejected>base.rejected,"changed Profile refuses capture");}
 {auto base=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);f.code[1]^=1;scope.Finish(f.spv);f.code[1]^=1;Check(Producer::Stats().accepted==base.accepted&&Producer::Stats().rejected>base.rejected,"changed original code refuses capture");}
 {auto base=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);f.table->num_records++;f.fetch.attributes[0].GetSharp(f.info);f.table->num_records--;scope.Finish(f.spv);Check(Producer::Stats().accepted==base.accepted&&Producer::Stats().rejected>base.rejected,"conflicting original GetSharp values never reach writer");}
 for(auto*mask:{&runtime_disabled_optimizations,&runtime_experiment_bits}){auto base=Producer::Stats();CompileScope scope(f.info,f.runtime,f.profile,{},f.code);Shader::Gcn::ObserveCapturedFetch(f.info,f.fetch);f.fetch.attributes[0].GetSharp(f.info);__atomic_store_n(mask,1ull<<63,__ATOMIC_RELAXED);scope.Finish(f.spv);__atomic_store_n(mask,0,__ATOMIC_RELAXED);Check(Producer::Stats().accepted==base.accepted&&Producer::Stats().rejected>base.rejected,"changed effective mask refuses capture");}
 Check(!Producer::Allows(0x9999,Shader::HwStage::Vertex,WordDigest(f.code),8),"unknown program hash rejected");Check(!Producer::Allows(0x1234,Shader::HwStage::Local,WordDigest(f.code),8),"same hash/code wrong hardware stage rejected");
 {RecordQueue q;Record a,b,c;a.expected.program_hash=1;b.expected.program_hash=2;c.expected.program_hash=3;Check(q.Push(std::move(a))&&q.Push(std::move(b))&&!q.Push(std::move(c)),"two-slot queue rejects full without blocking");auto first=q.Pop(),second=q.Pop();Check(first&&second&&first->expected.program_hash==1&&second->expected.program_hash==2&&!q.Pop(),"full queue did not evict accepted records");Record huge;huge.reads.reserve(64);Check(!q.Push(std::move(huge)),"queue rejects oversized retained vector capacity");}
 {
  RecordMailbox mailbox;Pause pause;MailboxTestAccess::BeforeWait(mailbox,Pause::Hook,&pause);
  auto waiter=std::async(std::launch::async,[&]{return mailbox.WaitPop();});Check(pause.entered.try_acquire_for(std::chrono::seconds(1)),"waiter paused after false predicate with wake mutex held");
  Record rejected;rejected.expected.program_hash=41;Check(!mailbox.Push(std::move(rejected))&&rejected.expected.program_hash==41,"producer rejects wake-lock contention without moving its item");pause.release.release();
  Record last;last.expected.program_hash=99;bool pushed=false;for(unsigned n=0;n<100&&!pushed;++n){pushed=mailbox.Push(std::move(last));if(!pushed)usleep(1000);}Check(pushed,"last isolated record admitted");Check(waiter.wait_for(std::chrono::seconds(1))==std::future_status::ready&&waiter.get()->expected.program_hash==99,"last notification cannot be lost in false-predicate gap");
  auto empty=std::async(std::launch::async,[&]{return mailbox.WaitPop();});mailbox.Close();Check(empty.wait_for(std::chrono::seconds(1))==std::future_status::ready&&!empty.get(),"close wakes idle writer");
 }
 {
  RecordMailbox mailbox;Pause pause;MailboxTestAccess::BeforeWait(mailbox,Pause::Hook,&pause);auto waiter=std::async(std::launch::async,[&]{return mailbox.WaitPop();});Check(pause.entered.try_acquire_for(std::chrono::seconds(1)),"close test holds false-predicate gap");auto close=std::async(std::launch::async,[&]{mailbox.Close();});Check(close.wait_for(std::chrono::milliseconds(10))==std::future_status::timeout,"close uses the same predicate mutex");pause.release.release();Check(close.wait_for(std::chrono::seconds(1))==std::future_status::ready,"close completes after predicate unlock");Check(waiter.wait_for(std::chrono::seconds(1))==std::future_status::ready&&!waiter.get(),"shutdown notification cannot be lost");
 }
 std::cout<<"FETCH_PRODUCER_RESULT {\"passed\":"<<passed<<",\"original_Vsharp_reads\":true,\"Vsharp_protected_before_finish\":true,\"disk_record_written\":true,\"game_started\":false,\"GPU_started\":false}\n";return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
