// SPDX-License-Identifier: GPL-2.0-or-later
#include "gpu_runtime_state.h"
#include <cassert>
#include <fstream>
#include <map>
#include <cstdio>
int main() {
    namespace fs=std::filesystem;
    auto root=fs::canonical(fs::temp_directory_path())/("bb-gpu-state-"+std::to_string(getpid()));
    assert(!fs::exists(root));fs::create_directory(root);fs::permissions(root,fs::perms::owner_all);
    const auto base=root/"original-gpu",selected=base/"arm64-native-v1",data=root/"data",work=data/"arm64-runtime-work",caps=work/"capabilities",scratch=caps/"authored-query";
    for(const auto& p:{base,selected,data,work,caps,scratch}) {fs::create_directory(p);fs::permissions(p,fs::perms::owner_all);}
    std::ofstream(base/"untouched.cache")<<"original cache";
    std::map<std::string,std::string> env{{"BB_GPU_USER_DIR",selected.string()},{"BB_ARM_GPU_BASE_DIR",base.string()},{"BB_DATA_DIR",data.string()}};
    auto observe=[&](const char* key){return env.contains(key)?env.at(key):std::string();};
    auto rejected=[&](bool worker){try{(void)BbGpuState::Formal(observe,worker);return false;}catch(const std::exception&){return true;}};
    assert(BbGpuState::Formal(observe,false)==selected);
    assert(BbGpuState::Formal(observe,true)==selected);
    env["BB_GPU_USER_DIR"]=base.string();assert(rejected(false));assert(rejected(true));
    env["BB_GPU_USER_DIR"]=(selected/"..").string();assert(rejected(false));
    env["BB_GPU_USER_DIR"]=(base/"missing").string();assert(rejected(false));
    auto link=root/"alias";fs::create_directory_symlink(selected,link);
    env["BB_GPU_USER_DIR"]=link.string();assert(rejected(false));
    env["BB_GPU_USER_DIR"]=selected.string();fs::permissions(selected,fs::perms::group_write,fs::perm_options::add);assert(rejected(false));
    fs::permissions(selected,fs::perms::owner_all);
    env.erase("BB_ARM_GPU_BASE_DIR");assert(rejected(false));
    env["BB_GPU_USER_DIR"]=env["HOME"]=env["TMPDIR"]=scratch.string();
    assert(BbGpuState::Formal(observe,true)==scratch);assert(rejected(false));
    env["HOME"]=root.string();assert(rejected(true));env["HOME"]=scratch.string();
    env["TMPDIR"]=root.string();assert(rejected(true));env["TMPDIR"]=scratch.string();
    fs::permissions(scratch,fs::perms::group_read,fs::perm_options::add);assert(rejected(true));fs::permissions(scratch,fs::perms::owner_all);
    env["BB_DATA_DIR"]=base.string();assert(rejected(true));env["BB_DATA_DIR"]=data.string();
    auto nested=scratch/"nested";fs::create_directory(nested);fs::permissions(nested,fs::perms::owner_all);
    env["BB_GPU_USER_DIR"]=env["HOME"]=env["TMPDIR"]=nested.string();assert(rejected(true));
    std::ifstream original(base/"untouched.cache");std::string value;std::getline(original,value);assert(value=="original cache");
    fs::remove_all(root);
    std::puts("GPU native namespace/scratch author controls PASS; no renderer or formal data loaded");
}
