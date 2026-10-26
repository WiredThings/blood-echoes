// SPDX-License-Identifier: GPL-2.0-or-later
#include "launcher_patch_input.h"
#include "launcher_game_metadata.h"
#include "launcher_cli.h"
#include "launcher_thread.h"
#include "launcher_cpu_policy.h"
#include <array>
#include <cassert>
#include <unistd.h>
struct Reloc{uint64_t target,kind;};
struct Image{uint64_t size=512;std::vector<Reloc>relocs{{64,0},{80,1}};bool covered(uint64_t a,uint64_t n)const{return a<=256&&n<=256-a;}};
using Bytes=std::vector<unsigned char>;
template<class T>void put(Bytes&b,T v){auto*p=(unsigned char*)&v;b.insert(b.end(),p,p+sizeof(v));}
Bytes patch(std::vector<std::pair<uint64_t,Bytes>> edits){Bytes b{'B','B','P','A','T','C','H','2'};put<uint64_t>(b,0x400000);put<uint64_t>(b,edits.size());for(auto&e:edits){put(b,e.first);put<uint64_t>(b,e.second.size());b.insert(b.end(),e.second.begin(),e.second.end());}return b;}
int main(int argc,char**argv){
 if(argc==2){auto m=BbLauncherGame::Read(argv[1]);printf("METADATA serial=%s saves=%s version=%s attributes=%u\n",m.serial.c_str(),m.saves.c_str(),m.version.c_str(),m.attributes);return 0;}
 // The real launcher does not know FEX flags: component defaults must work without one.
 std::string previous;const char* old=std::getenv("BB_ARM_HLE_FAST");bool had=old;if(old)previous=old;
 unsetenv("BB_ARM_HLE_FAST");assert(BbLauncherCpu::ConfigurePublishedLookup());assert(std::string(std::getenv("BB_ARM_HLE_FAST"))=="1");
 setenv("BB_ARM_HLE_FAST","0",1);assert(!BbLauncherCpu::ConfigurePublishedLookup());assert(std::string(std::getenv("BB_ARM_HLE_FAST"))=="0");
 setenv("BB_ARM_HLE_FAST","1",1);assert(BbLauncherCpu::ConfigurePublishedLookup());
 setenv("BB_ARM_HLE_FAST","invalid",1);assert(!BbLauncherCpu::ConfigurePublishedLookup());
 if(had)setenv("BB_ARM_HLE_FAST",previous.c_str(),1);else unsetenv("BB_ARM_HLE_FAST");
 // Original BB_ONLINE and BB_GPU_LIFETIME selections must not need FEX-only frontend flags.
 const char* policy_keys[]={"BB_ONLINE","BB_ARM_ALLOW_NETWORK","BB_GPU_LIFETIME","BB_ARM_PACKED_VENEERS","BB_ARM_RESOURCE_HOOKS","BB_ARM_RESOURCE_FAST","BB_ARM_RESOURCE_LEAF","BB_ARM_EXTRA_HEAPS","BB_ARM_PAYLOAD_GATE","BB_LAYER_READ_TRAPS","BB_ARM_RAW_PREWRITE"};
 std::vector<std::pair<std::string,std::string>> kept;
 for(auto key:policy_keys){if(const char* v=getenv(key))kept.emplace_back(key,v);unsetenv(key);}
 assert(!BbLauncherCpu::ConfigureNetworkAdmission()&&!getenv("BB_ARM_ALLOW_NETWORK"));
 setenv("BB_ONLINE","1",1);assert(BbLauncherCpu::ConfigureNetworkAdmission());
 setenv("BB_ARM_ALLOW_NETWORK","0",1);assert(!BbLauncherCpu::ConfigureNetworkAdmission());
 setenv("BB_ARM_ALLOW_NETWORK","1",1);setenv("BB_ONLINE","0",1);assert(!BbLauncherCpu::ConfigureNetworkAdmission());
 BbLauncherCpu::ConfigureExecutionDefaults(true);assert(BbLauncherCpu::Enabled("BB_ARM_PACKED_VENEERS")&&!getenv("BB_ARM_RESOURCE_HOOKS"));
 setenv("BB_GPU_LIFETIME","1",1);BbLauncherCpu::ConfigureExecutionDefaults(true);
 for(auto key:{"BB_ARM_RESOURCE_HOOKS","BB_ARM_RESOURCE_FAST","BB_ARM_RESOURCE_LEAF","BB_ARM_EXTRA_HEAPS","BB_ARM_PAYLOAD_GATE","BB_LAYER_READ_TRAPS","BB_ARM_RAW_PREWRITE"})assert(BbLauncherCpu::Enabled(key));
 for(auto key:policy_keys)unsetenv(key);
 setenv("BB_GPU_LIFETIME","1",1);BbLauncherCpu::ConfigureExecutionDefaults(false);assert(!getenv("BB_ARM_RESOURCE_HOOKS"));
 setenv("BB_ARM_RESOURCE_HOOKS","0",1);BbLauncherCpu::ConfigureExecutionDefaults(true);assert(!BbLauncherCpu::Enabled("BB_ARM_RESOURCE_HOOKS")&&!getenv("BB_ARM_EXTRA_HEAPS"));
 for(auto key:policy_keys)unsetenv(key);
 setenv("BB_GPU_LIFETIME","0",1);BbLauncherCpu::ConfigureExecutionDefaults(true);
 for(auto key:{"BB_ARM_PAYLOAD_GATE","BB_LAYER_READ_TRAPS","BB_ARM_RAW_PREWRITE"})assert(!getenv(key));
 setenv("BB_GPU_LIFETIME","1",1);for(auto key:{"BB_ARM_PAYLOAD_GATE","BB_LAYER_READ_TRAPS","BB_ARM_RAW_PREWRITE"})setenv(key,"0",1);
 BbLauncherCpu::ConfigureExecutionDefaults(true);for(auto key:{"BB_ARM_PAYLOAD_GATE","BB_LAYER_READ_TRAPS","BB_ARM_RAW_PREWRITE"})assert(std::string(getenv(key))=="0");
 for(auto key:policy_keys)unsetenv(key);for(auto& [key,value]:kept)setenv(key.c_str(),value.c_str(),1);
 std::vector<std::string_view> args{"boot.bin","--user","user","--app0","app0","--content-profile","content.bin","--patches","patches.bin","--timeout","0"};
 auto cli=BbLauncherCli::Parse(args);assert(cli.boot=="boot.bin"&&cli.user=="user"&&cli.graphics&&cli.timeout==0);
 // Exercise the real dispatch decision, not just game_main's later parsing.
 assert(BbLauncherCli::NeedsMainThread(args));
 auto cpu=args;cpu.push_back("--cpu-only");assert(!BbLauncherCli::NeedsMainThread(cpu));
 auto strict=args;strict.push_back("--strict-imports");assert(BbLauncherCli::Parse(strict).strict_imports&&BbLauncherCli::NeedsMainThread(strict));
 for(auto bad:{std::vector<std::string_view>{"boot.bin"},std::vector<std::string_view>{"boot.bin","--user"}}) {
  bool refused=false;try{(void)BbLauncherCli::NeedsMainThread(bad);}catch(const std::runtime_error&){refused=true;}assert(refused);
 }
 assert(!BbLauncherCli::NeedsMainThread({}));
 std::array<std::string_view,3> authored{"--boot","fixture","--modules-only"};assert(!BbLauncherCli::NeedsMainThread(authored));
 std::array<std::string_view,3> graphics{"--boot","fixture","--graphics"};assert(BbLauncherCli::NeedsMainThread(graphics));
 std::array<std::string_view,2> mode_value{"--state","--graphics"};assert(!BbLauncherCli::NeedsMainThread(mode_value));
 struct ThreadResult{pthread_t main;bool worker=false;size_t stack=0;} observed{pthread_self()};
 pthread_t worker;assert(!BbLauncherThread::Start(&worker,[](void* p)->void*{
  auto& r=*static_cast<ThreadResult*>(p);r.worker=!pthread_equal(pthread_self(),r.main);
  r.stack=pthread_get_stacksize_np(pthread_self());return nullptr;
 },&observed));assert(!pthread_join(worker,nullptr));assert(observed.worker&&observed.stack>=BbLauncherThread::StackBytes);
 puts("PASS launcher dispatch: original graphical and CPU-only, strict imports, authored modes, malformed refusal, main-thread preservation and original worker stack");

 for(auto extra:std::vector<std::vector<std::string_view>>{{"--user","other"},{"--timeout","1x"},{"--timeout","-1"},{"--timeout","601"},{"--timeout","9999999999999999"},{"--unknown","0"},{"--timeout"}}){auto bad=args;if(extra[0]=="--timeout"&&extra.size()==2)bad.resize(9);bad.insert(bad.end(),extra.begin(),extra.end());bool fail=false;try{(void)BbLauncherCli::Parse(bad);}catch(const std::runtime_error&){fail=true;}assert(fail);}
 auto file=std::filesystem::temp_directory_path()/("bb-launcher-input-"+std::to_string(getpid()));
 auto write=[&](const Bytes&b){std::ofstream f(file,std::ios::binary);f.write((char*)b.data(),b.size());};
 std::array<unsigned char,512>image{};Bytes pointer;put<uint64_t>(pointer,0x400030);auto valid=patch({{8,{1,2,3}},{9,{7,8}},{64,pointer}});write(valid);BbLauncherPatch::Apply(file,image.data(),Image{});uint64_t value;memcpy(&value,image.data()+64,8);assert(value==(uintptr_t)image.data()+48&&image[8]==1&&image[9]==7&&image[10]==8);
 unsigned negatives=0;auto rejected=[&](Bytes b){write(b);auto before=image;bool fail=false;try{BbLauncherPatch::Apply(file,image.data(),Image{});}catch(const std::runtime_error&){fail=true;}assert(fail&&image==before);++negatives;};
 for(size_t i=0;i<24;++i)rejected(Bytes(valid.begin(),valid.begin()+i));
 auto b=valid;b.pop_back();rejected(b);b=valid;b.push_back(0);rejected(b);b=valid;b[0]='X';rejected(b);b=valid;uint64_t excess=65537;memcpy(b.data()+16,&excess,8);rejected(b);
 rejected(patch({{8,{1}},{UINT64_MAX,{1,2}}}));rejected(patch({{8,{1}},{255,{1,2}}}));rejected(patch({{8,{1}},{300,{1}}}));rejected(patch({{8,{1}},{65,{1}}}));rejected(patch({{8,{1}},{80,pointer}}));Bytes outside;put<uint64_t>(outside,0x400300);rejected(patch({{8,{1}},{64,outside}}));Bytes below;put<uint64_t>(below,0x3fffff);rejected(patch({{64,below}}));rejected(patch({{0,{}}}));rejected(patch({{0,Bytes(4097)}}));
 write(patch({}));auto before=image;BbLauncherPatch::Apply(file,image.data(),Image{});assert(before==image);
 // Build a small genuine SFO with a different save namespace from TITLE_ID.
 std::vector<std::pair<std::string,std::string>> fields{{"TITLE_ID","CUSA03173"},{"INSTALL_DIR_SAVEDATA","CUSA00207"},{"TITLE","Bloodborne"},{"APP_VER","01.09"}};Bytes keys,values,entries;
 for(auto&[k,v]:fields){put<uint16_t>(entries,keys.size());put<uint16_t>(entries,0x204);put<uint32_t>(entries,v.size()+1);put<uint32_t>(entries,v.size()+1);put<uint32_t>(entries,values.size());keys.insert(keys.end(),k.begin(),k.end());keys.push_back(0);values.insert(values.end(),v.begin(),v.end());values.push_back(0);}
 Bytes sfo{0,'P','S','F'};put<uint32_t>(sfo,0x101);put<uint32_t>(sfo,20+entries.size());put<uint32_t>(sfo,20+entries.size()+keys.size());put<uint32_t>(sfo,fields.size());sfo.insert(sfo.end(),entries.begin(),entries.end());sfo.insert(sfo.end(),keys.begin(),keys.end());sfo.insert(sfo.end(),values.begin(),values.end());write(sfo);auto m=BbLauncherGame::Read(file);assert(m.serial=="CUSA03173"&&m.saves=="CUSA00207"&&m.version=="01.09");
 auto badSfo=[&](Bytes b){write(b);bool fail=false;try{(void)BbLauncherGame::Read(file);}catch(const std::runtime_error&){fail=true;}assert(fail);++negatives;};
 for(size_t i=0;i<20;++i)badSfo(Bytes(sfo.begin(),sfo.begin()+i));b=sfo;b[0]=1;badSfo(b);b=sfo;b[16]=255;badSfo(b);b=sfo;b[8]=0;badSfo(b);b=sfo;memset(b.data()+12,255,4);badSfo(b);b=sfo;memset(b.data()+32,255,4);badSfo(b);b=sfo;b.back()=1;badSfo(b);b=sfo;b[23]=4;badSfo(b);b=sfo;memcpy(b.data()+36,b.data()+20,2);badSfo(b);
 std::filesystem::remove(file);printf("PASS original launcher formats: ordered overwrites, relocated image pointers, atomic refusal, mapped ranges, %u negative fixtures, separate SFO save namespace\n",negatives);
}
