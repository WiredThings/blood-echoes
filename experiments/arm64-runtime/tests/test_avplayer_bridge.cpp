// SPDX-License-Identifier: GPL-2.0-or-later
// Native transport/control test; reverse callbacks here are authored native functions, not x86.
#include "avplayer_bridge.h"
#include "core/libraries/avplayer/avplayer.h"
#include "core/libraries/avplayer/avplayer_error.h"
#include <cassert>
#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <set>
#include <thread>
#include <unordered_map>
#include <vector>
#define runtime_thread_attach_host test_unused_attach
#include "metal/test_gpu_runtime_stubs.cpp"
#undef runtime_thread_attach_host
using namespace Libraries::AvPlayer;using namespace Core::GuestCpu;
extern "C" void bbgpu_register_kernel();extern "C" uintptr_t bbgpu_resolve(const char*);
namespace {
struct Range{uintptr_t at;size_t size;bool write;};std::mutex ranges_mutex;std::vector<Range> ranges;std::set<uintptr_t> blocks;std::atomic<unsigned> allocated{},freed{},opened{},closed{},read_calls{},event_calls{},attached{},detached{};
std::atomic<uintptr_t> callback_close_handle{};std::atomic<unsigned> callback_close_rejected{};
uint64_t Call(const char*,std::initializer_list<uint64_t>);
std::vector<uint8_t> movie;std::unordered_map<std::string,std::shared_ptr<HleCallAdapter>> adapters;
void Track(void*p,size_t n,bool w=true){std::lock_guard lock(ranges_mutex);ranges.push_back({uintptr_t(p),n,w});}
void Untrack(void*p){std::lock_guard lock(ranges_mutex);std::erase_if(ranges,[&](auto x){return x.at==uintptr_t(p);});}
bool Valid(uintptr_t a,size_t n,bool w){std::lock_guard lock(ranges_mutex);for(auto x:ranges)if(a>=x.at&&a-x.at<=x.size&&n<=x.size-(a-x.at)&&(!w||x.write))return true;return n==0;}
constexpr uintptr_t Cookie=0xaabbccddeeff0011ull;
void* Alloc(void*cookie,uint32_t align,uint32_t bytes){assert(uintptr_t(cookie)==Cookie);void*p{};assert(posix_memalign(&p,std::max<size_t>(align,sizeof(void*)),std::max<uint32_t>(bytes,1))==0);Track(p,std::max<uint32_t>(bytes,1));{std::lock_guard lock(ranges_mutex);assert(blocks.insert(uintptr_t(p)).second);}++allocated;return p;}
void Free(void*cookie,void*p){assert(uintptr_t(cookie)==Cookie);{std::lock_guard lock(ranges_mutex);assert(blocks.erase(uintptr_t(p))==1);}Untrack(p);std::free(p);++freed;}
int32_t Open(void*cookie,const char*name){assert(uintptr_t(cookie)==Cookie);assert(Valid(uintptr_t(name),std::strlen(name)+1,false));assert(std::string(name)=="authored.mp4");++opened;return 7;}
int32_t Close(void*cookie){assert(uintptr_t(cookie)==Cookie);++closed;return 0;}
uint64_t Size(void*cookie){assert(uintptr_t(cookie)==Cookie);return movie.size();}
int32_t Read(void*cookie,uint8_t*dst,uint64_t pos,uint32_t size){assert(uintptr_t(cookie)==Cookie&&Valid(uintptr_t(dst),size,true));++read_calls;auto n=pos>movie.size()?0:std::min<uint64_t>(size,movie.size()-pos);std::memcpy(dst,movie.data()+std::min<uint64_t>(pos,movie.size()),n);return int32_t(n);}
void Event(void*cookie,AvPlayerEvents,int32_t,void*payload){assert(uintptr_t(cookie)==Cookie);if(payload)assert(Valid(uintptr_t(payload),4,false));if(auto h=callback_close_handle.load()){assert(int32_t(Call("sceAvPlayerClose",{h}))==ORBIS_AVPLAYER_ERROR_OPERATION_FAILED);++callback_close_rejected;}++event_calls;}
bool Known(uintptr_t f){return f==uintptr_t(Alloc)||f==uintptr_t(Free)||f==uintptr_t(Open)||f==uintptr_t(Close)||f==uintptr_t(Size)||f==uintptr_t(Read)||f==uintptr_t(Event);}
uint64_t Call(const char*name,std::initializer_list<uint64_t>args){HleCallFrame frame{};alignas(16)uint64_t stack[4]{};frame.rsp=uintptr_t(stack)+8;const size_t regs[]{7,6,2,1,8,9};assert(args.size()<=6);size_t i=0;for(auto x:args)frame.gpr[regs[i++]]=x;frame.validate_range=[](void*,uintptr_t a,size_t n,bool w){return Valid(a,n,w);};auto result=adapters.at(name)->Invoke(frame);assert(std::holds_alternative<bool>(result)&&std::get<bool>(result));return frame.gpr[0];}
}
extern "C" void bb_arm_require_callback(const char*,uintptr_t a){assert(Known(a));}
extern "C" void bb_arm_require_range(const char*,uintptr_t a,size_t n,int w){assert(Valid(a,n,w));}
extern "C" void bb_arm_require_string(const char*,const char*s,size_t limit){size_t n=strnlen(s,limit);assert(n<limit&&Valid(uintptr_t(s),n+1,false));}
extern "C" uint64_t bb_arm_net_call(uintptr_t f,const uint64_t*a,size_t n){
 assert(Known(f));if(f==uintptr_t(Alloc)){assert(n==3);return uintptr_t(Alloc((void*)a[0],uint32_t(a[1]),uint32_t(a[2])));}if(f==uintptr_t(Free)){assert(n==2);Free((void*)a[0],(void*)a[1]);return 0;}if(f==uintptr_t(Open)){assert(n==2);return uint64_t(Open((void*)a[0],(const char*)a[1]));}if(f==uintptr_t(Close)){assert(n==1);return uint64_t(Close((void*)a[0]));}if(f==uintptr_t(Size)){assert(n==1);return Size((void*)a[0]);}if(f==uintptr_t(Read)){assert(n==4);return uint64_t(Read((void*)a[0],(uint8_t*)a[1],a[2],uint32_t(a[3])));}assert(n==4);Event((void*)a[0],AvPlayerEvents(a[1]),int32_t(a[2]),(void*)a[3]);return 0;
}
extern "C" void* bb_low_malloc(size_t n){auto*p=std::malloc(n);if(p)Track(p,n);return p;}
extern "C" void bb_low_free(void*p){Untrack(p);std::free(p);}
extern "C" void runtime_memory_prepare_cpu_write(uintptr_t a,uint64_t n){assert(Valid(a,n,true));}
extern "C" void runtime_memory_note_cpu_write(uintptr_t a,uint64_t n){assert(Valid(a,n,true));}
extern "C" void runtime_thread_attach_host(const char*){++attached;}
extern "C" void runtime_thread_detach_host(){++detached;}
int main(int argc,char**argv){assert(argc==2);std::ifstream in(argv[1],std::ios::binary);movie.assign(std::istreambuf_iterator<char>(in),{});assert(!movie.empty());bbgpu_register_kernel();
 const std::pair<const char*,const char*> list[]={{"sceAvPlayerInit","aS66RI0gGgo#m#n"},{"sceAvPlayerPostInit","HD1YKVU26-M#m#n"},{"sceAvPlayerAddSource","KMcEa+rHsIo#m#n"},{"sceAvPlayerSetLooping","OVths0xGfho#m#n"},{"sceAvPlayerClose","NkJwDzKmIlw#m#n"},{"sceAvPlayerStart","ET4Gr-Uu07s#m#n"},{"sceAvPlayerStop","ZC17w3vB5Lo#m#n"},{"sceAvPlayerIsActive","UbQoYawOsfY#m#n"},{"sceAvPlayerPause","9y5v+fGN4Wk#m#n"},{"sceAvPlayerResume","w5moABNwnRY#m#n"},{"sceAvPlayerGetVideoDataEx","JdksQu8pNdQ#m#n"},{"sceAvPlayerCurrentTime","wwM99gjFf1Y#m#n"},{"sceAvPlayerStreamCount","hdTyRzCXQeQ#m#n"},{"sceAvPlayerGetStreamInfo","d8FcbzfAdQw#m#n"},{"sceAvPlayerEnableStream","ODJK2sn9w4A#m#n"},{"sceAvPlayerGetAudioData","Wnp1OVcrZgk#m#n"}};
 for(auto[name,nid]:list){auto a=BbAvPlayerBridge::Adapter(name,bbgpu_resolve(nid));assert(a);adapters.emplace(name,std::move(a));}
 assert(!BbAvPlayerBridge::Adapter("unknown",1));assert(!Call("sceAvPlayerInit",{0}));assert(!Call("sceAvPlayerIsActive",{123}));assert(int32_t(Call("sceAvPlayerClose",{123}))==ORBIS_AVPLAYER_ERROR_INVALID_PARAMS);
 AvPlayerInitData config{};config.memory_replacement={(void*)Cookie,Alloc,Free,Alloc,Free};config.file_replacement={(void*)Cookie,Open,Close,Read,Size};config.event_replacement={(void*)Cookie,Event};config.num_output_video_framebuffers=2;config.auto_start=true;config.default_language="eng";Track(&config,sizeof(config));Track((void*)config.default_language,4,false);
 auto handle=Call("sceAvPlayerInit",{uintptr_t(&config)});assert(handle&&BbAvPlayerBridge::ActiveHandles()==1);AvPlayerPostInitData post{};Track(&post,sizeof(post));assert(int32_t(Call("sceAvPlayerPostInit",{handle,uintptr_t(&post)}))==0);assert(int32_t(Call("sceAvPlayerSetLooping",{handle,0}))==ORBIS_AVPLAYER_ERROR_OPERATION_FAILED);
 callback_close_handle=handle;char name[]="authored.mp4";Track(name,sizeof(name),false);assert(int32_t(Call("sceAvPlayerAddSource",{handle,uintptr_t(name)}))==0);
 AvPlayerFrameInfoEx frame{};Track(&frame,sizeof(frame));bool got=false;for(unsigned i=0;i<500;++i){if(Call("sceAvPlayerGetVideoDataEx",{handle,uintptr_t(&frame)})){got=true;break;}std::this_thread::sleep_for(std::chrono::milliseconds(5));}assert(got&&frame.details.video.width==16&&frame.details.video.height==16&&frame.p_data&&Valid(uintptr_t(frame.p_data),1,false));
 assert(int32_t(Call("sceAvPlayerSetLooping",{handle,0}))==0);assert(int32_t(Call("sceAvPlayerStreamCount",{handle}))>=1);AvPlayerStreamInfo stream{};Track(&stream,sizeof(stream));assert(int32_t(Call("sceAvPlayerGetStreamInfo",{handle,0,uintptr_t(&stream)}))==0);Call("sceAvPlayerCurrentTime",{handle});Call("sceAvPlayerPause",{handle});Call("sceAvPlayerResume",{handle});Call("sceAvPlayerStop",{handle});callback_close_handle=0;assert(int32_t(Call("sceAvPlayerClose",{handle}))==0);
 assert(!BbAvPlayerBridge::ActiveHandles()&&!BbAvPlayerBridge::CallbackContexts());assert(callback_close_rejected>0);assert(allocated==freed&&allocated>0&&opened==closed&&opened>0&&read_calls>0&&event_calls>0);assert(int32_t(Call("sceAvPlayerClose",{handle}))==ORBIS_AVPLAYER_ERROR_INVALID_PARAMS);
 auto next=Call("sceAvPlayerInit",{uintptr_t(&config)});assert(next&&next!=handle);assert(!Call("sceAvPlayerIsActive",{handle}));assert(int32_t(Call("sceAvPlayerClose",{next}))==0);assert(!BbAvPlayerBridge::ActiveHandles()&&!BbAvPlayerBridge::CallbackContexts());
 printf("PASS authored 16x16 video through original decoder and native bridge; callbacks alloc=%u/free=%u file=%u/%u events=%u host-threads=%u/%u\n",allocated.load(),freed.load(),opened.load(),closed.load(),event_calls.load(),attached.load(),detached.load());
 if(getenv("BB_AV_EXPECT_THREAD_CLEANUP"))assert(attached==detached);
}
