// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "bb_cpu_cache_identity.h"
#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace BbFetchStore {
using Digest=BbCpuCache::Digest;
inline constexpr size_t MaxRecordBytes=16*1024,MaxStoreBytes=64*1024*1024,MaxRecords=4096;
inline constexpr size_t MaxAttributes=32,ProfileBytes=74;
inline constexpr char DevelopmentMarker[]="Blood Echoes isolated fetch input storage v1\n";
inline constexpr char MarkerName[]="fetch-register-development.marker";
inline constexpr char DirectoryName[]="fetch-register-inputs-v1";
// None of these are paths, process addresses, a guest hash alone or a source revision alone.
// compile_inputs must identify the complete canonical compiler input set, including runtime,
// specialization and the actual captured descriptor contents, owned by the calling producer.
struct Environment {Digest game{},compiler{},abi{},compile_inputs{};bool operator==(const Environment&)const=default;};
struct Attribute {std::array<uint8_t,9> fields{};bool operator==(const Attribute&)const=default;};
struct RegisterRead {
 uint32_t pointer_index{},dword_offset{};
 // Four verbatim descriptor words, not a host pointer or a reconstructed Buffer.
 std::array<uint32_t,4> words{};
 bool operator==(const RegisterRead&)const=default;
};
struct Expected {
 Environment environment{};
 uint64_t program_hash{};
 uint32_t hardware_stage{},logical_stage{},code_bytes{},base_snapshot_bytes{};
 Digest code_sha256{},owned_context_sha256{},register_reads_sha256{};
 std::array<uint8_t,ProfileBytes> profile{};
 std::array<uint32_t,3> bindings{};
 uint32_t fetch_code_bytes{};
 int8_t vertex_offset{-1},instance_offset{-1};
 std::vector<Attribute> attributes;
 bool operator==(const Expected&)const=default;
};
struct Record {Expected expected;std::vector<RegisterRead> reads;bool operator==(const Record&)const=default;};
struct Limits {size_t bytes=MaxStoreBytes,records=MaxRecords;};
struct Cancellation {bool(*requested)(void*)noexcept{};void* context{};bool operator()()const noexcept{return requested&&requested(context);}};
// Pure codec; fixed-width little-endian fields, no C++ object dumps/Info/SRT/native pointers.
std::vector<std::byte> Encode(const Record&);
Record Decode(std::span<const std::byte>,const Expected&);
std::string Key(const Expected&);
Digest ReadsDigest(std::span<const RegisterRead>);

// Caller must explicitly prepare an owned 0700 development root and the exact owned 0600
// marker. No environment fallback, recursive creation, formal cache discovery or eviction.
class Store {
 int directory=-1;Limits limits{};
 explicit Store(int fd,Limits value):directory(fd),limits(value){}
public:
 Store()=default;~Store();Store(const Store&)=delete;Store& operator=(const Store&)=delete;
 Store(Store&&)noexcept;Store& operator=(Store&&)noexcept;
 static std::optional<Store> Open(const std::filesystem::path& development_root,Limits,std::string& error)noexcept;
 bool Write(const Record&,std::string& error,Cancellation={})const noexcept;
 std::optional<Record> Read(const Expected&,std::string& error)const noexcept;
};
} // namespace BbFetchStore
