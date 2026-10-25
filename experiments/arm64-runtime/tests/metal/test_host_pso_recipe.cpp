#include <cassert>
#include <iostream>
#include "video_core/renderer_metal/mtl_host_pso_recipe.h"
using namespace Metal::HostPso;
int main(){unsigned n=0;auto check=[&](bool x){assert(x);++n;};
 Recipe r;r.invocation.kind=Kind::InlineUpdate;r.preparation=std::string(64,'a');r.source_options=std::string(64,'b');r.descriptor=std::string(64,'c');r.entry0="inline_update";
 check(Valid(r));auto b=Encode(r);check(!b.empty());check(Decode(b)==r);
 for(size_t i=0;i<b.size();++i)check(!Decode(std::span(b).first(i)));
 auto bad=b;bad.push_back(0);check(!Decode(bad));bad=b;bad[4]=Version+1;check(!Decode(bad));
 auto x=r;x.preparation="a";check(!Valid(x));x=r;x.entry0="name/other";check(!Valid(x));x=r;x.invocation.count=15;check(!Valid(x));
 x=r;x.constants={{0,Type::Bool,2}};check(!Valid(x));x.constants={{1,Type::U32,0},{0,Type::U32,0}};check(!Valid(x));x.constants={{0,Type::U32,0},{0,Type::U32,0}};check(!Valid(x));
 x=r;x.constants={{0,Type::F32,0x7fc00023},{1,Type::I32,0xffffffff},{2,Type::F32,0x80000000}};check(Decode(Encode(x))==x);
 check(Bits(Type::Bool,17)==1&&Bits(Type::I32,0xffffffff)==0xffffffff&&Bits(Type::F32,0x80000000)==0x80000000);
 x=r;x.invocation.kind=Kind::Tiling;x.invocation.bits=32;x.invocation.count=14;check(Valid(x));x.invocation.bounded=1;x.invocation.provided[13]=1;check(!Valid(x));x.constants={{3,Type::Bool,1},{13,Type::Bool,1}};check(Valid(x));x.invocation.provided[13]=0;check(!Valid(x));
 x=r;x.invocation.kind=Kind::TextureTiling;x.invocation.bits=64;x.invocation.macro=1;x.invocation.count=12;x.constants={{12,Type::U32,1},{13,Type::Bool,0}};check(Valid(x));x.invocation.provided[12]=1;check(!Valid(x));
 x=r;x.invocation.kind=Kind::FaultParser;x.invocation.count=2;x.invocation.provided[0]=14;x.invocation.provided[1]=2048;check(Valid(x));x.invocation.provided[3]=1;check(!Valid(x));
 x=r;x.invocation.kind=Kind::MsCopy;x.invocation.samples=4;x.invocation.format=80;x.entry1="fragment";check(Valid(x));x.invocation.samples=3;check(!Valid(x));x.invocation.samples=128;check(!Valid(x));
 std::vector<std::string>names;for(int i=129;i>=0;--i)names.push_back(std::to_string(1000+i));
 std::string cursor;std::vector<bool>done(names.size());for(unsigned round=0;round<3;++round){auto order=Order(names,cursor);unsigned new_items=0;for(auto k:order){if(new_items==64)break;if(!done[k]){done[k]=true;++new_items;cursor=names[k];}}}check(std::all_of(done.begin(),done.end(),[](bool x){return x;}));check(Order({},"x").empty());check(names[Order(names,"9999").front()]=="1000");
 // Exact recipe equality rejects actual factory reconstruction drift, including unused raw data.
 x=r;x.invocation.provided[0]=1;check(x!=r);x=r;x.descriptor[0]='d';check(x!=r);x=r;x.entry0="other";check(x!=r);
 std::cout<<"recipe controls passed "<<n<<"\n";}
