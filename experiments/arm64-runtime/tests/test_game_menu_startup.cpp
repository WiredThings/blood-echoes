// SPDX-License-Identifier: GPL-2.0-or-later
// Actual original menu installer + actual ARM wrapper, with authored host edges.
// No FEX execution, GPU, game files, preferences or save data are used.
#include "bbport_game_menu.cpp"
#include "game_menu_detours.h"
#include <cassert>
#include <map>
#include <string>
#include <utility>

namespace BbSettings { Values& Get(){static Values v;return v;}void Save(){std::abort();} }
static std::string mode;
static unsigned allocations,publications,protect_attempts;
static std::vector<void*> allocated;
static constexpr size_t Page=16384,ImageBytes=96u<<20;
static void check(bool value,const char* reason){if(!value)throw std::runtime_error(reason);}
struct HleCallFrame{};
using HleCallResult=int;
struct HleCallAdapter{virtual ~HleCallAdapter()=default;virtual HleCallResult Invoke(HleCallFrame&)const{return 0;}};
template<class T>std::shared_ptr<HleCallAdapter> MakeHleCallAdapter(T){return std::make_shared<HleCallAdapter>();}
static std::shared_ptr<HleCallAdapter> framed(std::shared_ptr<HleCallAdapter> p){return p;}
struct Mapping{
 void*ptr{};size_t size;
 explicit Mapping(size_t n):size(n){ptr=mmap(nullptr,n,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);check(ptr!=MAP_FAILED,"authored mapping failed");}
 ~Mapping(){if(ptr&&ptr!=MAP_FAILED)munmap(ptr,size);}
 uintptr_t at()const{return reinterpret_cast<uintptr_t>(ptr);}
};
struct Boot{uint64_t size=ImageBytes;bool covered(uint64_t p,uint64_t n,bool)const{return p<=size&&n<=size-p;}};
struct Range{uintptr_t begin;size_t size;bool executable,writable;};
struct Registry{uintptr_t Register(std::shared_ptr<HleCallAdapter>,std::string){++publications;return publications;}};
struct State{uintptr_t image{};uint64_t image_size{};Registry registry;std::vector<Range> ranges;bool valid(uintptr_t,size_t,bool){return true;}};
static State state;static State*active=&state;
static uintptr_t veneer(State&s,uintptr_t n){return s.image+ImageBytes+Page+n*16;}
static uint64_t bb_arm_call(uint64_t,const uint64_t*,size_t){std::abort();}
static void*bb_low_aligned_alloc(size_t a,size_t n){++allocations;if(mode=="allocation-failure")return nullptr;void*p=std::aligned_alloc(a,n);if(p)allocated.push_back(p);return p;}
static void bb_low_free(void*p){std::free(p);for(auto&item:allocated)if(item==p)item=nullptr;}
static int authored_mprotect(void*p,size_t n,int prot){++protect_attempts;if(mode=="commit-failure")return -1;return mprotect(p,n,prot);}
#define mprotect authored_mprotect
#include "game_menu_runtime.h"
#undef mprotect

int main(int argc,char**argv){
 assert(argc==2);mode=argv[1];
 const bool disabled=mode=="disabled";
 const bool mismatch=mode=="mismatch-message"||mode=="mismatch-add"||mode=="short-image";
 const bool fatal=mode=="allocation-failure"||mode=="commit-failure";
 assert(disabled||mismatch||fatal||mode=="valid");
 setenv("BB_GAME_MENU",disabled?"0":"1",1);
 Mapping image(ImageBytes);Boot boot;std::unique_ptr<Mapping> stubs;
 state.image=image.at();state.image_size=ImageBytes;
 // Use the original installer's own synthetic test constants, never copy or
 // independently duplicate its prologue admission algorithm into production.
 memcpy(static_cast<char*>(image.ptr)+BbGameMenu::MsgLookup,BbGameMenu::MsgLookupPrologue,sizeof(BbGameMenu::MsgLookupPrologue));
 memcpy(static_cast<char*>(image.ptr)+BbGameMenu::AddPageItem,BbGameMenu::AddPageItemPrologue,sizeof(BbGameMenu::AddPageItemPrologue));
 if(mode=="mismatch-message")static_cast<char*>(image.ptr)[BbGameMenu::MsgLookup]^=1;
 if(mode=="mismatch-add")static_cast<char*>(image.ptr)[BbGameMenu::AddPageItem]^=1;
 if(mode=="short-image")boot.size=8;
 // Every non-success control has a read-only guest image. Any unintended
 // partial guest patch would fault rather than pass an incomplete byte check.
 if(mode!="valid")assert(mprotect(image.ptr,image.size,PROT_READ)==0);
 bool rejected=false;
 try{ArmGameMenu::Install(boot,image,stubs);}catch(const std::runtime_error&e){rejected=true;printf("EXPECTED refusal: %s\n",e.what());}
 assert(rejected==fatal);
 if(disabled||mismatch){
  assert(!stubs&&!allocations&&!publications&&!protect_attempts&&state.ranges.empty());
  assert(!ArmGameMenu::metadata&&!ArmGameMenu::owner&&!ArmGameMenu::sealed&&ArmGameMenu::callbacks.empty());
  assert(ArmGameMenu::startup_effects.Untouched());
  assert(BbGameMenu::image_base==0);
 }else if(fatal){
  assert(!stubs&&state.ranges.empty());
  assert(!ArmGameMenu::startup_effects.Untouched());
  if(mode=="allocation-failure")assert(allocations==1&&!publications&&!protect_attempts&&!ArmGameMenu::startup_effects.install);
  if(mode=="commit-failure")assert(allocations&&publications&&protect_attempts==1&&ArmGameMenu::startup_effects.install);
 }else{
  assert(stubs&&allocations&&publications&&protect_attempts==1&&state.ranges.size()==1);
  assert(!ArmGameMenu::metadata&&!ArmGameMenu::owner&&ArmGameMenu::sealed);
  assert(ArmGameMenu::startup_effects.allocation&&ArmGameMenu::startup_effects.callback&&ArmGameMenu::startup_effects.install);
 }
 for(auto*p:allocated)std::free(p);
 printf("PASS %s; allocations=%u callbacks=%u commits=%u; actual installer/wrapper, no game/FEX/GPU\n",mode.c_str(),allocations,publications,protect_attempts);
}
