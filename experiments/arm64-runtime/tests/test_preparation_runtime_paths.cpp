// SPDX-License-Identifier: GPL-2.0-or-later
// Authored path fixtures only; actual code admission is separately tested in an app.
#include "preparation_runtime_paths.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <map>
namespace BbBundledRuntime {
std::optional<Admission> fixture_code;
const std::optional<Admission>& Actual(){return fixture_code;}
}
int main(){
 namespace fs=std::filesystem;
 const auto root=fs::canonical(fs::temp_directory_path())/("preparation-paths-"+std::to_string(getpid()));
 fs::create_directory(root);fs::permissions(root,fs::perms::owner_all,fs::perm_options::replace);
 auto dir=[&](fs::path p){fs::create_directories(p);fs::permissions(p,fs::perms::owner_all,fs::perm_options::replace);return p;};
 const auto data=dir(root/"Library/Application Support/bbport/optimized-launcher"),base=dir(data/"gpu-cache/metal1");
 const auto gpu=dir(base/"arm64-native-v1"),work=dir(data/"arm64-runtime-work"),job=dir(work/"fixed-1/probe"),scratch=dir(job/"scratch");
 const auto automatic=dir(gpu/"automatic-game-inputs"),input=dir(automatic/"modules/one/input"),output=dir(automatic/"modules/one/output");
 const auto game=dir(automatic/"source-views/version"),store=dir(automatic/"capture");
 std::map<std::string,std::string> values={{"BB_DATA_DIR",data},{"BB_ARM_GPU_BASE_DIR",base},{"BB_GPU_USER_DIR",gpu}};
 auto env=[&](const char*key){return values[key];};unsigned count=0;
 auto reject=[&](auto fn){bool stopped=false;try{fn();}catch(const std::exception&){stopped=true;}assert(stopped);++count;};
 auto paths=BbPreparationPaths::Roots::Formal(env);paths.Pso(job/"packet.json",scratch);++count;
 paths.Module(input/"packet.json",output);++count;paths.Companion(store,game);++count;
 reject([&]{paths.Pso(root/"packet.json",scratch);});
 reject([&]{paths.Pso(job/"packet.json",work);});
 std::ofstream(scratch/"occupied")<<"keep";reject([&]{paths.Pso(job/"packet.json",scratch);});fs::remove(scratch/"occupied");
 reject([&]{paths.Module(job/"packet.json",output);});reject([&]{paths.Module(input/"packet.json",scratch);});
 reject([&]{paths.Companion(root,game);});reject([&]{paths.Companion(store,root);});
 const auto alias=root/"alias";fs::create_directory_symlink(scratch,alias);reject([&]{paths.Pso(job/"packet.json",alias);});
 values["BB_GPU_USER_DIR"]=base.string();reject([&]{BbPreparationPaths::Roots::Formal(env);});values["BB_GPU_USER_DIR"]=gpu.string();
 values["BB_ARM_GPU_BASE_DIR"]="";reject([&]{BbPreparationPaths::Roots::Formal(env);});values["BB_ARM_GPU_BASE_DIR"]=base.string();
 fs::permissions(work,fs::perms::group_read,fs::perm_options::add);reject([&]{BbPreparationPaths::Roots::Formal(env);});fs::permissions(work,fs::perms::owner_all,fs::perm_options::replace);
 // Environment fields alone never choose the new formal branch.
 assert(!BbPreparationPaths::Actual(BbBundledRuntime::Role::PsoWorker));++count;
 BbBundledRuntime::Admission code;code.role=BbBundledRuntime::Role::CpuTop;BbBundledRuntime::fixture_code=code;
 reject([&]{BbPreparationPaths::Actual(BbBundledRuntime::Role::PsoWorker);});
 code.role=BbBundledRuntime::Role::ModuleWorker;BbBundledRuntime::fixture_code=code;
 reject([&]{BbPreparationPaths::Actual(BbBundledRuntime::Role::PsoWorker);});
 fs::remove_all(root);std::cout<<"PREPARATION_PATHS "<<count<<" authored controls passed\n";
}
