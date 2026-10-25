// bbport: host threads that may call guest code (AvPlayer allocator callbacks).
// Each thread gets a guest TCB (GS base, TLS) from the C runtime before running.
#pragma once
#include <atomic>
#include <functional>
#include <mutex>
#include <stop_token>
#include <thread>
#include "common/types.h"

extern "C" void runtime_thread_attach_host(const char* name);
// Only the ARM64 CPU bridge provides this optional completion hook. Other hosts
// retain their existing lifetime. It runs before native/FEX thread-local teardown.
#ifdef __APPLE__
extern "C" void bb_arm_native_host_thread_detach(void) __attribute__((weak_import));
#else
extern "C" void bb_arm_native_host_thread_detach(void) __attribute__((weak));
#endif

namespace Libraries::Kernel {
// Several threads may end one Thread at once: when a stream ends, AvPlayer's demuxer joins the
// decoders while the game's sceAvPlayerStop stops them, and each decoder "joins" itself on its
// way out. Joins take turns (two pthread_joins of one thread never return on macOS), a thread's
// own join does nothing (whoever stops it, or the destructor, joins it), and a stop request
// never waits for a join in progress (the joined thread may be waiting for that request).
class Thread {
public:
    Thread() = default;
    ~Thread() {
        Stop();
        std::scoped_lock lock(join_mutex);
        if (thread.joinable()) thread.detach(); // destroyed by its own thread
    }
    void Run(std::function<void(std::stop_token)>&& func) {
        std::scoped_lock lock(join_mutex);
        thread = std::jthread([this, func = std::move(func)](std::stop_token token) {
            runtime_thread_attach_host("bb:hle");
            struct GuestScope {
                ~GuestScope() {
                    if (bb_arm_native_host_thread_detach) bb_arm_native_host_thread_detach();
                    Current() = nullptr;
                }
            } guest_scope;
            Current() = this;
            func(token);
        });
        {
            std::scoped_lock stop_lock(stop_mutex);
            stop = thread.get_stop_source();
        }
        running = true;
    }
    void Join() {
        if (Current() == this) return;
        std::scoped_lock lock(join_mutex);
        if (thread.joinable()) thread.join();
        running = false;
    }
    bool Joinable() const { return running; }
    void Stop() {
        {
            std::scoped_lock lock(stop_mutex);
            stop.request_stop();
        }
        Join();
    }

private:
    static Thread*& Current() {
        static thread_local Thread* current = nullptr;
        return current;
    }

    std::jthread thread;
    std::stop_source stop;
    std::mutex join_mutex, stop_mutex;
    std::atomic<bool> running{false};
};
} // namespace Libraries::Kernel
