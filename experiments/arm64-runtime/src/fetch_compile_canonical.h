// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_register_snapshot.h"
namespace BbFetchCapture {
std::vector<std::byte>CanonicalRuntime(const Shader::RuntimeInfo&);
Shader::RuntimeInfo DecodeRuntime(std::span<const std::byte>);
Shader::Profile DecodeProfile(std::span<const uint8_t>);
BbFetchStore::Digest CanonicalInputs(const Shader::CompileSnapshot&,const BbFetchStore::Record&,
 const BbFetchStore::Digest& environment,const std::array<uint64_t,2>&masks,
 const BbFetchStore::Digest&runtime_before_translation);
BbFetchStore::Digest CompleteCanonicalInputs(const Shader::CompileSnapshot&,const BbFetchStore::Record&,
 const Shader::RuntimeInfo&runtime_after,const BbFetchStore::Digest&environment,
 const std::array<uint64_t,2>&masks);
}
