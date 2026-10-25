// SPDX-License-Identifier: GPL-2.0-or-later
#include "guest_process_parameters.h"
#include <array>
#include <cassert>
#include <iostream>
using namespace BbGuestProcess;
int main() {
 std::array<std::byte,256> memory{};
 auto base=reinterpret_cast<uintptr_t>(memory.data());
 auto slot=Initialize(memory,base);
 assert(slot==base+NameSlotOffset);
 uint64_t words[4];std::memcpy(words,memory.data(),sizeof(words));
 assert(words[0]==1&&words[1]==base+PathOffset&&words[2]==0&&words[3]==0);
 assert(std::string_view(reinterpret_cast<const char*>(words[1]))==Executable);
 uint64_t value;std::memcpy(&value,reinterpret_cast<const void*>(slot),8);
 assert(std::string_view(reinterpret_cast<const char*>(value))=="eboot.bin");
 // Both indirections are writable and initialization restores a new process.
 memory[value-base]=std::byte{'E'};const uint64_t other=words[1];
 std::memcpy(reinterpret_cast<void*>(slot),&other,8);
 Initialize(memory,base);std::memcpy(&value,reinterpret_cast<void*>(slot),8);
 assert(std::string_view(reinterpret_cast<const char*>(value))=="eboot.bin");
 for (auto path:{std::string_view("plain"),std::string_view("/x/last"),std::string_view("/x/")}) {
  Initialize(memory,base,path);std::memcpy(&value,reinterpret_cast<void*>(slot),8);
  auto n=path.find_last_of('/');assert(std::string_view(reinterpret_cast<const char*>(value))==path.substr(n==path.npos?0:n+1));
 }
 auto rejects=[&](std::span<std::byte> b,uintptr_t address,std::string_view path){
  auto before=memory;bool failed=false;try{Initialize(b,address,path);}catch(const std::runtime_error&){failed=true;}
  assert(failed&&before==memory);
 };
 rejects(std::span(memory).first(RequiredBytes-1),base,Executable);
 rejects(memory,0,Executable);rejects(memory,UINTPTR_MAX-RequiredBytes+1,Executable);
 rejects(memory,base,"");rejects(memory,base,std::string_view("a\0b",3));
 rejects(memory,base,std::string(64,'a'));
 assert(IsProgramName("djxxOmW6-aw#p#J",2,1));
 assert(!IsProgramName("djxxOmW6-aw#p#J",1,1));
 assert(!IsProgramName("djxxOmW6-aw#p#J",2,0));
 assert(!IsProgramName("djxxOmW6-aw#q#J",2,1));
 assert(!IsProgramName("__progname",2,1));
 std::cout<<"PASS guest process parameters: argv, data pointer, writable slot/string, reset, basename, bounds, exact NID/kind/capability\n";
}
