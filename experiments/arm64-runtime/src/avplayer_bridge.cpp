// SPDX-License-Identifier: GPL-2.0-or-later
// Native ABI bridge only: all playback remains in the existing AvPlayer implementation.
#include "avplayer_bridge.h"
#include "runtime_exit_codes.h"
#include "core/libraries/avplayer/avplayer.h"
#include "core/libraries/avplayer/avplayer_error.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <string>
#include <unordered_map>
extern "C" {
uint64_t bb_arm_net_call(uintptr_t,const uint64_t*,size_t);
unsigned bbgpu_arm_avplayer_bridge_revision(void) __attribute__((weak_import));
void bb_arm_require_callback(const char*,uintptr_t);
void bb_arm_require_range(const char*,uintptr_t,size_t,int);
void bb_arm_require_string(const char*,const char*,size_t);
void* bb_low_malloc(size_t);
void bb_low_free(void*);
void runtime_memory_prepare_cpu_write(uintptr_t,uint64_t);
void runtime_memory_note_cpu_write(uintptr_t,uint64_t);
}
namespace BbAvPlayerBridge {
using namespace Libraries::AvPlayer;
using Core::GuestCpu::MakeHleCallAdapter;
namespace {
enum Slot {Init,PostInit,AddSource,SetLooping,Close,Start,Stop,IsActive,Pause,Resume,GetVideoDataEx,CurrentTime,StreamCount,GetStreamInfo,EnableStream,GetAudioData,Count};
constexpr std::array<const char*,Count> Names{"sceAvPlayerInit","sceAvPlayerPostInit","sceAvPlayerAddSource","sceAvPlayerSetLooping","sceAvPlayerClose","sceAvPlayerStart","sceAvPlayerStop","sceAvPlayerIsActive","sceAvPlayerPause","sceAvPlayerResume","sceAvPlayerGetVideoDataEx","sceAvPlayerCurrentTime","sceAvPlayerStreamCount","sceAvPlayerGetStreamInfo","sceAvPlayerEnableStream","sceAvPlayerGetAudioData"};
std::array<std::atomic<uintptr_t>,Count> originals{};
[[noreturn]] void Fail(const char* reason){std::fprintf(stderr,"ARM AV bridge refused: %s\n",reason);std::_Exit(BB_ARM_EXIT_HLE_FAULT);}
template<class R,class... A> R Host(Slot slot,A... args){auto at=originals[slot].load(std::memory_order_acquire);if(!at)Fail("unregistered original function");return reinterpret_cast<R(*)(A...)>(at)(args...);}
struct Context {
 AvPlayerInitData guest{};std::string language;AvPlayerHandle player{};uintptr_t token{};
 std::mutex mutex;std::condition_variable cv;size_t calls{};bool closing{};
};
std::mutex registry_mutex;
std::unordered_map<uintptr_t,std::shared_ptr<Context>> handles,callbacks;
uintptr_t next_id=1;
struct CallbackScope {Context* context;CallbackScope* previous;static thread_local CallbackScope* current;explicit CallbackScope(Context* c):context(c),previous(current){current=this;}~CallbackScope(){current=previous;}static bool Contains(Context*c){for(auto*p=current;p;p=p->previous)if(p->context==c)return true;return false;}};
thread_local CallbackScope* CallbackScope::current{};
std::shared_ptr<Context> CallbackContext(void* object){std::lock_guard lock(registry_mutex);auto i=callbacks.find(uintptr_t(object));if(i==callbacks.end())Fail("expired callback context");return i->second;}
uint64_t Guest(Context& c,uintptr_t entry,std::initializer_list<uint64_t> args){bb_arm_require_callback("avplayer.callback",entry);CallbackScope scope{&c};return bb_arm_net_call(entry,args.begin(),args.size());}
struct Scratch {void* pointer;explicit Scratch(size_t bytes):pointer(bb_low_malloc(bytes)){if(!pointer)throw std::bad_alloc();}~Scratch(){bb_low_free(pointer);}Scratch(const Scratch&)=delete;};
template<class T> T Read(uintptr_t at){bb_arm_require_range("avplayer.input",at,sizeof(T),0);T out;std::memcpy(&out,reinterpret_cast<void*>(at),sizeof(out));return out;}
template<class T> void Write(uintptr_t at,const T& value){bb_arm_require_range("avplayer.output",at,sizeof(T),1);runtime_memory_prepare_cpu_write(at,sizeof(T));std::memcpy(reinterpret_cast<void*>(at),&value,sizeof(T));runtime_memory_note_cpu_write(at,sizeof(T));}
struct Lease {
 std::shared_ptr<Context> context;
 explicit Lease(uintptr_t token){std::shared_ptr<Context> c;{std::lock_guard lock(registry_mutex);auto i=handles.find(token);if(i!=handles.end())c=i->second;}if(c){std::lock_guard lock(c->mutex);if(!c->closing){++c->calls;context=std::move(c);}}}
 ~Lease(){if(context){std::lock_guard lock(context->mutex);if(!--context->calls)context->cv.notify_all();}}
 explicit operator bool()const{return bool(context);}
 AvPlayerHandle Player()const{return context->player;}
};
void* Allocate(void* object,uint32_t alignment,uint32_t bytes,bool texture){auto c=CallbackContext(object);auto fn=texture?c->guest.memory_replacement.allocate_texture:c->guest.memory_replacement.allocate;auto address=Guest(*c,uintptr_t(fn),{uintptr_t(c->guest.memory_replacement.object_ptr),alignment,bytes});if(address){if(!alignment||(alignment&(alignment-1))||address%alignment)Fail("allocator alignment");bb_arm_require_range("avplayer.allocated",address,bytes,1);}return reinterpret_cast<void*>(address);}
void* AllocateMemory(void*object,uint32_t alignment,uint32_t bytes){return Allocate(object,alignment,bytes,false);}
void* AllocateTexture(void*object,uint32_t alignment,uint32_t bytes){return Allocate(object,alignment,bytes,true);}
void Free(void*object,void* memory,bool texture){auto c=CallbackContext(object);auto fn=texture?c->guest.memory_replacement.deallocate_texture:c->guest.memory_replacement.deallocate;Guest(*c,uintptr_t(fn),{uintptr_t(c->guest.memory_replacement.object_ptr),uintptr_t(memory)});}
void FreeMemory(void*object,void*memory){Free(object,memory,false);}void FreeTexture(void*object,void*memory){Free(object,memory,true);}
int32_t OpenFile(void*object,const char* name){auto c=CallbackContext(object);size_t bytes=strnlen(name,4096);if(bytes==4096)return -1;try{Scratch copy{bytes+1};std::memcpy(copy.pointer,name,bytes+1);return int32_t(Guest(*c,uintptr_t(c->guest.file_replacement.open),{uintptr_t(c->guest.file_replacement.object_ptr),uintptr_t(copy.pointer)}));}catch(const std::bad_alloc&){return -1;}}
int32_t CloseFile(void*object){auto c=CallbackContext(object);return int32_t(Guest(*c,uintptr_t(c->guest.file_replacement.close),{uintptr_t(c->guest.file_replacement.object_ptr)}));}
uint64_t SizeFile(void*object){auto c=CallbackContext(object);return Guest(*c,uintptr_t(c->guest.file_replacement.size),{uintptr_t(c->guest.file_replacement.object_ptr)});}
int32_t ReadFile(void*object,uint8_t*buffer,uint64_t position,uint32_t length){
 auto c=CallbackContext(object);if(!length)return 0;if(length>INT32_MAX||position>UINT64_MAX-length)return -1;
 try{Scratch copy{std::min<uint32_t>(length,65536)};uint32_t total=0;while(total<length){auto bytes=std::min<uint32_t>(length-total,65536);int32_t got=int32_t(Guest(*c,uintptr_t(c->guest.file_replacement.read_offset),{uintptr_t(c->guest.file_replacement.object_ptr),uintptr_t(copy.pointer),position+total,bytes}));if(got<0)return total?int32_t(total):got;if(uint32_t(got)>bytes)Fail("file callback returned beyond requested span");std::memcpy(buffer+total,copy.pointer,size_t(got));total+=uint32_t(got);if(uint32_t(got)<bytes)break;}return int32_t(total);}catch(const std::bad_alloc&){return -1;}
}
void Event(void*object,AvPlayerEvents event,int32_t source,void* data){auto c=CallbackContext(object);try{if(data){if(event!=AvPlayerEvents::WarningId)Fail("unimplemented non-null event payload");Scratch copy{sizeof(int32_t)};std::memcpy(copy.pointer,data,sizeof(int32_t));Guest(*c,uintptr_t(c->guest.event_replacement.event_callback),{uintptr_t(c->guest.event_replacement.object_ptr),uint32_t(event),uint64_t(int64_t(source)),uintptr_t(copy.pointer)});}else Guest(*c,uintptr_t(c->guest.event_replacement.event_callback),{uintptr_t(c->guest.event_replacement.object_ptr),uint32_t(event),uint64_t(int64_t(source)),0});}catch(const std::bad_alloc&){Fail("event staging allocation failed");}}
uintptr_t Initialize(uintptr_t address){
 if(!address)return 0;auto input=Read<AvPlayerInitData>(address);
 if(!input.memory_replacement.allocate||!input.memory_replacement.allocate_texture||!input.memory_replacement.deallocate||!input.memory_replacement.deallocate_texture)return 0;
 for(auto fn:{uintptr_t(input.memory_replacement.allocate),uintptr_t(input.memory_replacement.deallocate),uintptr_t(input.memory_replacement.allocate_texture),uintptr_t(input.memory_replacement.deallocate_texture)})bb_arm_require_callback("avplayer.allocator",fn);
 const bool files=input.file_replacement.open&&input.file_replacement.close&&input.file_replacement.read_offset&&input.file_replacement.size;
 if(files)for(auto fn:{uintptr_t(input.file_replacement.open),uintptr_t(input.file_replacement.close),uintptr_t(input.file_replacement.read_offset),uintptr_t(input.file_replacement.size)})bb_arm_require_callback("avplayer.file",fn);
 if(input.event_replacement.event_callback)bb_arm_require_callback("avplayer.event",uintptr_t(input.event_replacement.event_callback));
 if(input.default_language)bb_arm_require_string("avplayer.language",input.default_language,4096);
 auto c=std::make_shared<Context>();c->guest=input;if(input.default_language)c->language=input.default_language;
 auto native=input;native.memory_replacement={c.get(),AllocateMemory,FreeMemory,AllocateTexture,FreeTexture};native.file_replacement=files?AvPlayerFileReplacement{c.get(),OpenFile,CloseFile,ReadFile,SizeFile}:AvPlayerFileReplacement{};native.event_replacement=input.event_replacement.event_callback?AvPlayerEventReplacement{c.get(),Event}:AvPlayerEventReplacement{};native.default_language=input.default_language?c->language.c_str():nullptr;
 {std::lock_guard lock(registry_mutex);if(next_id>UINTPTR_MAX/16)return 0;c->token=next_id++*16+9;callbacks.emplace(uintptr_t(c.get()),c);}
 try{c->player=Host<AvPlayerHandle>(Init,&native);}catch(...){std::lock_guard lock(registry_mutex);callbacks.erase(uintptr_t(c.get()));throw;}
 if(!c->player){std::lock_guard lock(registry_mutex);callbacks.erase(uintptr_t(c.get()));return 0;}
 try{std::lock_guard lock(registry_mutex);handles.emplace(c->token,c);}catch(...){Host<int32_t>(Close,c->player);std::lock_guard lock(registry_mutex);callbacks.erase(uintptr_t(c.get()));throw;}return c->token;
}
int32_t ClosePlayer(uintptr_t token){
 std::shared_ptr<Context> c;{std::lock_guard lock(registry_mutex);auto i=handles.find(token);if(i==handles.end())return ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;c=i->second;}
 if(CallbackScope::Contains(c.get()))return ORBIS_AVPLAYER_ERROR_OPERATION_FAILED;
 {std::unique_lock lock(c->mutex);if(c->closing)return ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;c->closing=true;c->cv.wait(lock,[&]{return !c->calls;});}
 auto result=Host<int32_t>(Close,c->player);c->player=nullptr;{std::lock_guard lock(registry_mutex);handles.erase(token);callbacks.erase(uintptr_t(c.get()));}return result;
}
int32_t PostInitialize(uintptr_t token,uintptr_t data){Lease c{token};if(!c||!data)return ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;auto input=Read<AvPlayerPostInitData>(data);return Host<int32_t>(PostInit,c.Player(),&input);}
int32_t Add(uintptr_t token,uintptr_t name){Lease c{token};if(!c||!name)return ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;bb_arm_require_string("avplayer.source",reinterpret_cast<const char*>(name),4096);return Host<int32_t>(AddSource,c.Player(),reinterpret_cast<const char*>(name));}
int32_t Loop(uintptr_t token,bool enabled){Lease c{token};return c?Host<int32_t>(SetLooping,c.Player(),enabled):ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;}
template<Slot slot> int32_t Simple(uintptr_t token){Lease c{token};return c?Host<int32_t>(slot,c.Player()):ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;}
bool Active(uintptr_t token){Lease c{token};return c&&Host<bool>(IsActive,c.Player());}
uint64_t Time(uintptr_t token){Lease c{token};return c?Host<uint64_t>(CurrentTime,c.Player()):uint64_t(ORBIS_AVPLAYER_ERROR_INVALID_PARAMS);}
int32_t StreamInfo(uintptr_t token,uint32_t stream,uintptr_t out){Lease c{token};if(!c||!out)return ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;bb_arm_require_range("avplayer.stream",out,sizeof(AvPlayerStreamInfo),1);AvPlayerStreamInfo value{};auto result=Host<int32_t>(GetStreamInfo,c.Player(),stream,&value);if(result==0)Write(out,value);return result;}
int32_t Enable(uintptr_t token,uint32_t stream){Lease c{token};return c?Host<int32_t>(EnableStream,c.Player(),stream):ORBIS_AVPLAYER_ERROR_INVALID_PARAMS;}
template<Slot slot,class Info> bool Frame(uintptr_t token,uintptr_t out){Lease c{token};if(!c||!out)return false;bb_arm_require_range("avplayer.frame",out,sizeof(Info),1);Info value{};auto result=Host<bool>(slot,c.Player(),&value);if(result)Write(out,value);return result;}
} // namespace
std::shared_ptr<Core::GuestCpu::HleCallAdapter> Adapter(std::string_view name,uintptr_t original){
 if(!original)return {};Slot slot=Count;for(size_t i=0;i<Names.size();++i)if(name==Names[i]){slot=Slot(i);break;}if(slot==Count)return {};if(!bbgpu_arm_avplayer_bridge_revision||bbgpu_arm_avplayer_bridge_revision()!=1)Fail("host library lacks AV thread completion revision 1");auto previous=originals[slot].exchange(original,std::memory_order_acq_rel);if(previous&&previous!=original)Fail("original function identity changed");
 switch(slot){
 case Init:return MakeHleCallAdapter(Initialize);case PostInit:return MakeHleCallAdapter(PostInitialize);case AddSource:return MakeHleCallAdapter(Add);case SetLooping:return MakeHleCallAdapter(Loop);case Close:return MakeHleCallAdapter(ClosePlayer);
 case Start:return MakeHleCallAdapter(Simple<Start>);case Stop:return MakeHleCallAdapter(Simple<Stop>);case IsActive:return MakeHleCallAdapter(Active);case Pause:return MakeHleCallAdapter(Simple<Pause>);case Resume:return MakeHleCallAdapter(Simple<Resume>);
 case GetVideoDataEx:return MakeHleCallAdapter(Frame<GetVideoDataEx,AvPlayerFrameInfoEx>);case CurrentTime:return MakeHleCallAdapter(Time);case StreamCount:return MakeHleCallAdapter(Simple<StreamCount>);case GetStreamInfo:return MakeHleCallAdapter(StreamInfo);case EnableStream:return MakeHleCallAdapter(Enable);case GetAudioData:return MakeHleCallAdapter(Frame<GetAudioData,AvPlayerFrameInfo>);default:return {};
 }
}
size_t ActiveHandles(){std::lock_guard lock(registry_mutex);return handles.size();}
size_t CallbackContexts(){std::lock_guard lock(registry_mutex);return callbacks.size();}
} // namespace BbAvPlayerBridge

// Linked into the CPU main object closure, not the GPU subprocess.
extern "C" void runtime_thread_detach_host(void);
extern "C" void bb_arm_native_host_thread_detach(void) { runtime_thread_detach_host(); }
