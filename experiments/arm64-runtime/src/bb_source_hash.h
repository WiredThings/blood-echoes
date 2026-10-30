#pragma once
// Shared bounded linked-image identity; independent of either CPU engine.
#include "bb_cpu_cache_identity.h"
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
namespace BbStaticCode {
using Digest=BbCpuCache::Digest;
inline void Require(bool ok,const char*why){if(!ok)throw std::runtime_error(why);}
inline std::string Hex(const Digest& d){constexpr char hex[]="0123456789abcdef";std::string out;for(auto b:d){out+=hex[b>>4];out+=hex[b&15];}return out;}
inline Digest SourceHash(const std::filesystem::path& path){
 std::ifstream source(path,std::ios::binary);Require(bool(source),"code source unavailable");
 CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);std::array<char,65536> buffer{};size_t total=0;
 for(;;){source.read(buffer.data(),buffer.size());auto n=source.gcount();Require(n>=0&&size_t(n)<=(544u<<20)-total,"code source too large");total+=n;if(n)CC_SHA256_Update(&ctx,buffer.data(),static_cast<CC_LONG>(n));if(source.eof())break;Require(bool(source),"code source read error");}
 Require(total!=0,"empty code source");Digest out{};CC_SHA256_Final(out.data(),&ctx);return out;
}
}
