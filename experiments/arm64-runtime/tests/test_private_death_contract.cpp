#include "private_death_contract.h"
#include <cassert>
#include <cstdio>
using namespace BbPrivateDeath;
int main(){
 auto t=Thunk(0xf001a0d470,Signature.data(),Signature.size());assert(t.size()==15&&t[0]==0x0f&&t[1]==0x57&&t[2]==0xc0&&t[13]==0xff&&t[14]==0xe0);uint64_t target{};memcpy(&target,t.data()+5,8);assert(target==0xf001a0d470);
 unsigned refused=0;for(int n=0;n<3;++n){auto s=Signature;if(n==1)s[6]^=1;try{Thunk(n==0?0:123,s.data(),n==2?10:11);assert(false);}catch(const std::runtime_error&){++refused;}}assert(refused==3);
 assert(Health(1,100,2,100)&&Health(0,100,0,100));assert(!Health(-1,100,1,100)&&!Health(101,100,1,100)&&!Health(1,0,1,0)&&!Health(1,100001,1,100001)&&!Health(1,100,1,99)&&!Health(1,100,101,100));
 Gate g;for(int i=0;i<4;++i)assert(!g.Observe(1,true,false,true,10));assert(!g.Request(1,true,false,true,10));assert(g.Observe(1,true,false,true,10));assert(!g.Request(2,true,false,true,10));assert(!g.Request(1,true,true,true,10));assert(!g.Request(1,true,false,false,10));assert(!g.Request(1,true,false,true,0));assert(g.Request(1,true,false,true,10));assert(!g.Request(1,true,false,true,10));
 for(int i=0;i<5;++i)g.Observe(1,true,false,true,10);assert(!g.Observe(2,true,false,true,10));assert(!g.Request(2,true,false,true,10));for(int i=0;i<4;++i)g.Observe(2,true,false,true,10);assert(g.Request(2,true,false,true,10));for(int i=0;i<5;++i)g.Observe(2,true,false,true,10);assert(!g.Request(3,true,false,true,10));g.Observe(0,false,true,false,0);assert(g.stable==0);
 puts("private SetHp thunk, health bounds, stable-player and two-cycle sequencing controls passed");
}
