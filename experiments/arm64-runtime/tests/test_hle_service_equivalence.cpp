// SPDX-License-Identifier: MIT
// Host dispatch contract only: neither FEX nor bbcpu execution is linked.
#include "core/guest_cpu/fex_hle_bridge.h"
#include <cassert>
using namespace Core::GuestCpu;
namespace {
uint64_t host_value=73;
uint64_t Add(uint64_t a,uint64_t b){return a+b;}
uint64_t Read(const uint64_t* p){return *p;}
uint64_t* Publish(){assert(PublishHostRange(&host_value,sizeof(host_value),true));return &host_value;}
void Revoke(){assert(RevokeHostRange(&host_value));}
bool Deny(void*,uintptr_t,size_t,bool){return false;}
}
int main(){
 HleCallRegistry registry;
 auto add=registry.Register(MakeHleCallAdapter(Add),"contract.add");
 auto read=registry.Register(MakeHleCallAdapter(Read),"contract.read");
 auto publish=registry.Register(MakeHleCallAdapter(Publish),"contract.publish");
 auto revoke=registry.Register(MakeHleCallAdapter(Revoke),"contract.revoke");
 HleGuestBridge fex(registry,Deny,nullptr);
 HleCallService neutral(registry,Deny,nullptr);
 auto failure=[](auto result){
  if(auto* e=std::get_if<int>(&result))return *e;
  return 0;
 };
 auto invoke_fex=[&](HleCallFrame& frame)->HleDispatchResult{
  auto result=fex.Invoke(frame);
  if(auto* e=std::get_if<AetherPS4::Fex::EngineFailure>(&result)){
   assert(e->Stage==AetherPS4::Fex::EngineStage::Bridge);return e->Error;
  }
  return std::get<bool>(result);
 };
 auto exercise=[&](auto invoke){
  HleCallFrame frame;frame.operation=add->Operation();frame.gpr[7]=11;frame.gpr[6]=23;
  assert(std::get<bool>(invoke(frame))&&frame.gpr[0]==34);
  frame.operation=read->Operation();frame.gpr[7]=reinterpret_cast<uintptr_t>(&host_value);
  assert(failure(invoke(frame))==EFAULT);
  frame.operation=publish->Operation();assert(std::get<bool>(invoke(frame))&&frame.gpr[0]==reinterpret_cast<uintptr_t>(&host_value));
  frame.operation=read->Operation();assert(std::get<bool>(invoke(frame))&&frame.gpr[0]==73);
  frame.operation=revoke->Operation();assert(std::get<bool>(invoke(frame)));
  frame.operation=read->Operation();assert(failure(invoke(frame))==EFAULT);
  frame.operation=UINT64_MAX;assert(failure(invoke(frame))==ENOSYS);
 };
 exercise(invoke_fex);exercise([&](HleCallFrame& frame){return neutral.Invoke(frame);});
 assert(!PublishHostRange(&host_value,sizeof(host_value),true));
 assert(!RevokeHostRange(&host_value));
 puts("FEX bridge and CPU-neutral HLE agree: scalar, denied pointer, scoped publication, revocation, unknown operation");
}
