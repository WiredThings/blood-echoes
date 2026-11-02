#include "fex_launcher_paths.h"
#include <cassert>
#include <fstream>
#include <functional>
using namespace BbFexLauncher;
int main(int argc,char**argv){
 assert(argc==2);auto base=fs::canonical(argv[1]);auto data=base/"data",user=base/"progress",game=base/"game";for(auto p:{data,user,game,data/"out",data/"fex-preparation",data/"fex-preparation/effective",data/"fex-preparation/products"})fs::create_directory(p);
 std::ofstream(data/"out/eboot.elf")<<"authored ELF";Paths paths(data,user,game);
 const auto generation=data/"fex-preparation/products"/std::string(64,'a');fs::create_directory(generation);
 assert(paths.Directory(generation,"products",64)==generation);paths.Export(data/"fex-preparation/effective"/std::string(32,'b'),data/"out/eboot.elf");
 auto plan=generation/("plan-"+std::string(64,'c')+".bin");std::ofstream(plan)<<"authored plan";paths.Plan(plan,generation);
 auto rejects=[](auto call){bool no=false;try{call();}catch(...){no=true;}assert(no);};
 rejects([&]{paths.Directory(user,"products",64);});rejects([&]{paths.Plan(data/"out/eboot.elf",generation);});rejects([&]{Paths bad(data,data/"fex-preparation",game);});
 auto link=data/"fex-preparation/products"/std::string(64,'d');fs::create_directory_symlink(generation,link);rejects([&]{paths.Directory(link,"products",64);});
 auto dangling=data/"fex-preparation/effective"/std::string(32,'e');fs::create_symlink(base/"missing",dangling);rejects([&]{paths.Export(dangling,data/"out/eboot.elf");});
 chmod(generation.c_str(),0777);rejects([&]{paths.Directory(generation,"products",64);});chmod(generation.c_str(),0700);
 puts("FEX formal path tests passed");
}
