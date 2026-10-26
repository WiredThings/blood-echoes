// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_register_snapshot.h"
#include "fetch_companion_mailbox.h"
#include "fetch_capture_admission.h"
#include <array>
#include <atomic>
#include <condition_variable>
#include <mutex>

namespace BbFetchCapture {
struct ProgramProof {uint32_t stage{};uint64_t hash{};BbFetchStore::Digest code{};uint32_t bytes{};bool operator==(const ProgramProof&)const=default;};
struct Receipt {
 BbFetchStore::Environment environment{};
 std::vector<ProgramProof>programs;
};
// The identity verifier reads actual pinned regular bytes, not bare environment digests.
Receipt VerifyReceipt(const std::filesystem::path& development_root,
                      const std::filesystem::path& game_root);
BbFetchStore::Digest ImageDigest();
// Preparation executable validates the selected backend backing-file identity rather
// than mistaking its own executable for the runtime DSO. All other receipt gates identical.
Receipt VerifyPreparationReceipt(const std::filesystem::path&root,const std::filesystem::path&game,const std::filesystem::path&selected_backend);
BbFetchCompanion::SourceRequirement SourceForReceipt(Receipt&,const Shader::Profile&);
class RecordQueue {
 std::array<std::optional<BbFetchStore::Record>,2>slots;size_t count{},head{};
 std::mutex mutex;std::atomic<size_t>available{};
public:
 bool Push(BbFetchStore::Record&&)noexcept;
 std::optional<BbFetchStore::Record>Pop()noexcept;
 bool Available()const noexcept{return available.load(std::memory_order_acquire)!=0;}
};
struct MailboxTestAccess;
class RecordMailbox {
 RecordQueue queue;std::mutex wake_mutex;std::condition_variable wake;bool closed{};
#if defined(BB_FETCH_CAPTURE_TESTING) && BB_FETCH_CAPTURE_TESTING
 void(*before_wait)(void*){};void*test_context{};
#endif
 friend struct MailboxTestAccess;
public:
 bool Push(BbFetchStore::Record&&)noexcept;
 std::optional<BbFetchStore::Record>WaitPop(); // worker only; producers never wait
 void Close()noexcept;
};
struct ProducerStats {uint64_t accepted{},rejected{},written{},failed{};std::array<uint64_t,static_cast<unsigned>(Refusal::Count)>refusals{};};
class Producer {
public:
 static bool Enabled()noexcept;
 // Original launch/prepare owner calls this after automatically preparing its cohort.
 // No user-entered digest, process restart, alternate launch flow or hot reconfiguration.
 static bool Configure(const std::filesystem::path&prepared_root,const std::filesystem::path&game_root,std::string&error)noexcept;
 static bool Allows(uint64_t,Shader::HwStage,const BbFetchStore::Digest&,size_t)noexcept;
 static BbFetchCompanion::Lease Acquire()noexcept;
 static std::optional<BbFetchCompanion::Pending> Build(BbFetchCompanion::Lease,Shader::CompileSnapshot&&,
                    const Shader::RuntimeInfo&runtime_after,std::span<const uint32_t>observed_spirv,
                    const std::array<uint64_t,2>& frozen_masks)noexcept;
 static bool Publish(BbFetchCompanion::Pending&&,const Shader::StageSpecialization&,uint64_t permutation,uint64_t permutation_hash)noexcept;
 static bool PublishWarm(BbFetchCompanion::Lease,Shader::CompileSnapshot&&,const Shader::RuntimeInfo&after,
  const Shader::StageSpecialization&,uint64_t permutation,uint64_t permutation_hash,
  BbFetchCompanion::Reference&&,const std::array<uint64_t,2>&masks)noexcept;
 static void Reject()noexcept;
 static void Reject(Refusal)noexcept;
 static ProducerStats Stats()noexcept;
};
} // namespace BbFetchCapture
extern "C" __attribute__((visibility("default"))) bool bb_fetch_companion_configure_v1(const char*prepared_root,const char*game_root)noexcept;
