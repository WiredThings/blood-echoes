// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_companion_store.h"
#include <condition_variable>
#include <mutex>
#include <functional>
namespace BbFetchCompanion {
struct Reference {Digest sha{};size_t words{};std::function<bool(std::vector<uint32_t>&)>load;};
struct WarmInput {Shader::CompileSnapshot input;Shader::RuntimeInfo after{};Digest environment{};std::array<uint64_t,2>masks{};BbFetchCapture::StageAssociation association;Reference reference;};
struct Pending {Companion companion;Lease lease;std::optional<WarmInput>warm;};
struct MailboxTestAccess;
class Mailbox {
 std::array<std::optional<Pending>,2>slots;size_t head{},count{};
 std::mutex wake_mutex;std::condition_variable wake;bool closed{};
#if defined(BB_FETCH_CAPTURE_TESTING) && BB_FETCH_CAPTURE_TESTING
 void(*before_wait)(void*){};void*test_context{};
#endif
 friend struct MailboxTestAccess;
public:
 bool Push(Pending&&value)noexcept{try{
  if(!value.lease)return false;if(value.warm){auto&w=*value.warm;if(!FitsCounts(w.input.code.size(),w.input.flat.size(),w.input.walker.size(),w.reference.words))return false;}else if(MemoryBytes(value.companion)>MaxItemBytes)return false;
  std::unique_lock lock(wake_mutex,std::try_to_lock);if(!lock||closed||count==2)return false;
  slots[(head+count)%2].emplace(std::move(value));++count;lock.unlock();wake.notify_one();return true;
 }catch(...){return false;}}
 std::optional<Pending>WaitPop(){std::unique_lock lock(wake_mutex);wake.wait(lock,[&]{bool ready=closed||count;
#if defined(BB_FETCH_CAPTURE_TESTING) && BB_FETCH_CAPTURE_TESTING
  if(!ready&&before_wait)before_wait(test_context);
#endif
  return ready;});if(!count)return{};auto value=std::move(slots[head]);slots[head].reset();head=(head+1)%2;--count;return value;}
 void Close()noexcept{{std::lock_guard lock(wake_mutex);closed=true;}wake.notify_all();}
};
}
