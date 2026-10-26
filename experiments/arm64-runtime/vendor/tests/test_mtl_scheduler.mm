// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_scheduler: ticks, waits, deferred and priority operations,
// the fence chain across encoders, memory barriers on device-address accesses and the error
// path. Needs a Metal device, no window or game.
//
//   mtl-scheduler-test
//
// Exit 0 when every check passed. The error path is tested with a synthetic failure: a kernel
// that never ends was not stopped by any GPU timeout on the M4 (5+ minutes), so no real
// command buffer error can be provoked reliably.
#import <Metal/Metal.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <filesystem>
#include <fstream>
#include <iterator>
#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_pass_continuation.h"

#include "video_core/renderer_metal/mtl_scheduler_impl.h"

// The runtime's toggle bits the scheduler reads (bbport_toggles.h); this test has no guest process.
extern "C" {
uint64_t runtime_experiment_bits = 0;
uint64_t runtime_disabled_optimizations = 0;
}

// common/assert.cpp pulls in the emulator; a failed ASSERT here just aborts.
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}

using Clock = std::chrono::steady_clock;
using Metal::Scheduler;

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                          \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

static double MsSince(Clock::time_point t) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t).count();
}

static NSString* const kernel_source = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "kernel void spin(device float* out [[buffer(0)]], constant uint& n [[buffer(1)]]) {\n"
    "    float x = 1.0f;\n"
    "    for (uint i = 0; i < n; ++i) { x = x * 0.999f + sin(x); }\n"
    "    out[0] = x;\n"
    "}\n"
    "kernel void write_word(constant ulong& address [[buffer(0)]], constant uint& value [[buffer(1)]]) {\n"
    "    *reinterpret_cast<device uint*>(address) = value;\n"
    "}\n"
    "kernel void read_word(constant ulong& address [[buffer(0)]], constant ulong& index [[buffer(1)]],\n"
    "                      device uint* out [[buffer(2)]]) {\n"
    "    out[0] = reinterpret_cast<device const uint*>(address)[index];\n"
    "}\n";

struct Kernels {
    id<MTLComputePipelineState> spin, write_word, read_word;
};

static id<MTLComputePipelineState> Pipeline(id<MTLDevice> device, id<MTLLibrary> library,
                                            NSString* name) {
    NSError* error = nil;
    id<MTLComputePipelineState> pipeline = [device
        newComputePipelineStateWithFunction:[library newFunctionWithName:name]
                                      error:&error];
    if (!pipeline) {
        std::printf("pipeline %s: %s\n", name.UTF8String,
                    error ? error.localizedDescription.UTF8String : "?");
        std::exit(2);
    }
    return pipeline;
}

static void DispatchSpin(Scheduler& scheduler, const Kernels& k, id<MTLBuffer> out, uint32_t n) {
    scheduler.ComputeEncoder();
    auto& compute = scheduler.GetImpl().compute;
    [compute setComputePipelineState:k.spin];
    [compute setBuffer:out offset:0 atIndex:0];
    [compute setBytes:&n length:4 atIndex:1];
    [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
}

static void TestTicks(Metal::Device& device, const Kernels& k) {
    std::printf("ticks\n");
    Scheduler scheduler{device, "test-ticks"};
    CHECK(scheduler.CurrentTick() == 1);
    CHECK(scheduler.IsFree(0));
    CHECK(!scheduler.IsFreeKnown(1));
    scheduler.Flush(); // nothing recorded: the tick still advances (and is done at once)
    CHECK(scheduler.CurrentTick() == 2);
    CHECK(scheduler.IsFree(1));

    id<MTLBuffer> out = [device.GetImpl().device newBufferWithLength:64
                                                             options:MTLResourceStorageModeShared];
    DispatchSpin(scheduler, k, out, 1000);
    CHECK(scheduler.HasOpenCommandBuffer());
    CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::Compute);
    scheduler.Flush();
    CHECK(scheduler.CurrentTick() == 3);
    CHECK(!scheduler.HasOpenCommandBuffer());
    CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::None);
    scheduler.Wait(2);
    CHECK(scheduler.IsFreeKnown(2));
    CHECK(scheduler.IsFree(2));
    CHECK(!scheduler.IsFree(3));

    // Wait on the open tick flushes it.
    const u64 open = scheduler.CurrentTick();
    DispatchSpin(scheduler, k, out, 1000);
    scheduler.Wait(open);
    CHECK(scheduler.CurrentTick() == open + 1);
    CHECK(scheduler.IsFree(open));

    scheduler.Finish();
    CHECK(scheduler.IsFree(scheduler.CurrentTick() - 1));
    CHECK(!scheduler.HasFailed());

    // Encoders of different kinds replace one another.
    scheduler.BlitEncoder();
    CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::Blit);
    scheduler.ComputeEncoder();
    CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::Compute);
    CHECK(!scheduler.IsRendering());
    scheduler.EndRendering(); // not rendering: leaves the compute encoder alone
    CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::Compute);
    scheduler.EndEncoder();
    CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::None);
    scheduler.Finish();
}

/// Iterations of the spin kernel that take about `ms`.
static uint32_t Calibrate(Metal::Device& device, const Kernels& k, double ms) {
    id<MTLBuffer> out = [device.GetImpl().device newBufferWithLength:64
                                                             options:MTLResourceStorageModeShared];
    Scheduler scheduler{device, "test-calibrate"}; // (after the buffer: its destructor retires)
    DispatchSpin(scheduler, k, out, 1000);
    scheduler.Finish(); // warm up
    uint32_t n = 200000;
    double elapsed = 0;
    for (int i = 0; i < 4; ++i) {
        const auto start = Clock::now();
        DispatchSpin(scheduler, k, out, n);
        scheduler.Finish();
        elapsed = MsSince(start);
        if (elapsed > 5.0) {
            break;
        }
        n *= 8;
    }
    const double scaled = n * (ms / std::max(elapsed, 0.1));
    n = static_cast<uint32_t>(std::min(scaled, 4.0e9));
    std::printf("  spin kernel: %u iterations for ~%.0f ms (calibration run %.1f ms)\n", n, ms,
                elapsed);
    return n;
}

static void TestDeferred(Metal::Device& device, const Kernels& k, uint32_t spin_n) {
    std::printf("deferred operations\n");
    Scheduler scheduler{device, "test-deferred"};
    id<MTLBuffer> out = [device.GetImpl().device newBufferWithLength:64
                                                             options:MTLResourceStorageModeShared];
    std::atomic<int> order{0};
    std::atomic<int> ran_first{0}, ran_second{0};
    const u64 tick = scheduler.CurrentTick();
    DispatchSpin(scheduler, k, out, spin_n);
    scheduler.DeferOperation([&] { ran_first = ++order; });
    // Before the flush the tick is open: nothing runs.
    for (int i = 0; i < 100; ++i) {
        scheduler.PopPendingOperations();
    }
    CHECK(ran_first == 0);
    scheduler.Flush();
    // The kernel runs ~20 ms; the checks below take microseconds.
    CHECK(!scheduler.IsFree(tick));
    for (int i = 0; i < 200; ++i) { // more than the poll interval
        scheduler.PopPendingOperations();
    }
    CHECK(ran_first == 0);
    // An operation deferred to the next open tick waits for that one too.
    scheduler.DeferOperation([&] { ran_second = ++order; });
    CHECK(!scheduler.WaitTick(tick, 1000000)); // 1 ms: the kernel is still running
    scheduler.Wait(tick);
    CHECK(scheduler.IsFree(tick));
    // Deferred operations wait for the command buffer to retire (its completed handler), which
    // comes a moment after the event signal Wait saw.
    for (int i = 0; i < 1000 && ran_first == 0; ++i) {
        scheduler.PopPendingOperations();
        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    CHECK(ran_first == 1);
    CHECK(ran_second == 0); // its tick (open) was not flushed
    scheduler.Flush();      // submits the open tick and pops what is free
    scheduler.Finish();
    for (int i = 0; i < 1000 && ran_second == 0; ++i) {
        scheduler.PopPendingOperations();
        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    CHECK(ran_second == 2);

    // Priority operation: runs on another thread once the GPU is done, no one calls Pop.
    std::atomic<bool> prio_ran{false};
    std::atomic<bool> prio_was_free{false};
    std::thread::id prio_thread;
    const u64 prio_tick = scheduler.CurrentTick();
    DispatchSpin(scheduler, k, out, spin_n);
    scheduler.DeferPriorityOperation([&] {
        prio_was_free = scheduler.IsFree(prio_tick);
        prio_thread = std::this_thread::get_id();
        prio_ran = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    CHECK(!prio_ran); // the tick is open, not even submitted
    scheduler.Flush();
    const auto start = Clock::now();
    while (!prio_ran && MsSince(start) < 3000) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    CHECK(prio_ran);
    CHECK(prio_was_free);
    CHECK(prio_thread != std::this_thread::get_id());

    // Destruction runs what is left (the priority thread is waiting on a tick that was never
    // flushed) and joins the priority thread.
    std::atomic<bool> rest_ran{false}, prio_rest_ran{false};
    {
        Scheduler last{device, "test-deferred-last"};
        DispatchSpin(last, k, out, spin_n);
        last.DeferOperation([&] { rest_ran = true; });
        last.DeferPriorityOperation([&] { prio_rest_ran = true; });
    }
    CHECK(rest_ran);
    CHECK(prio_rest_ran);
}

static id<MTLBuffer> UntrackedBuffer(Metal::Device& device, size_t size) {
    id<MTLBuffer> buffer = [device.GetImpl().device
        newBufferWithLength:size
                    options:MTLResourceStorageModeShared | MTLResourceHazardTrackingModeUntracked];
    // Device addresses read and write nothing for a buffer that is not in the residency set.
    device.GetImpl().AddResident(buffer);
    return buffer;
}

static void ReadWord(Scheduler& scheduler, const Kernels& k, id<MTLBuffer> source, uint64_t index,
                     id<MTLBuffer> out) {
    scheduler.ComputeEncoder();
    auto& compute = scheduler.GetImpl().compute;
    const uint64_t address = source.gpuAddress;
    [compute setComputePipelineState:k.read_word];
    [compute setBytes:&address length:8 atIndex:0];
    [compute setBytes:&index length:8 atIndex:1];
    [compute setBuffer:out offset:0 atIndex:2];
    [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
}

static void TestFenceChain(Metal::Device& device, const Kernels& k) {
    std::printf("fence chain and barriers\n");
    // Declared before the scheduler: release only after its destructor drains completions.
    std::vector<id> lifetime;
    Scheduler scheduler{device, "test-fences"};
    constexpr size_t Size = 64 << 20; // a long fill: an unordered reader would see it half done
    constexpr uint64_t Last = Size / 4 - 1;
    id<MTLBuffer> big = UntrackedBuffer(device, Size);
    id<MTLBuffer> out = [device.GetImpl().device newBufferWithLength:64
                                                             options:MTLResourceStorageModeShared];
    auto* words = static_cast<uint32_t*>(out.contents);

    // Blit fill, barrier, compute read in another encoder (same and later command buffers).
    for (int round = 1; round <= 24; ++round) {
        const uint8_t value = static_cast<uint8_t>(round * 7);
        scheduler.BlitEncoder();
        [scheduler.GetImpl().blit fillBuffer:big range:NSMakeRange(0, Size) value:value];
        scheduler.MemoryBarrier(); // blit: ends the encoder
        CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::None);
        CHECK(scheduler.NeedFenceWait());
        words[0] = 0;
        ReadWord(scheduler, k, big, Last, out);
        CHECK(!scheduler.NeedFenceWait()); // the new encoder took the wait
        if (round % 3 == 0) {
            scheduler.Flush(); // the chain spans command buffers
        }
        scheduler.Finish();
        CHECK(words[0] == value * 0x01010101u);
    }

    // More encoders than fence slots between two barriers.
    for (int i = 0; i < 20; ++i) {
        scheduler.BlitEncoder();
        // (own region each: encoders without a barrier between them are unordered)
        [scheduler.GetImpl().blit fillBuffer:big
                                       range:NSMakeRange(Size - 65536 + i * 256, 256)
                                       value:static_cast<uint8_t>(i)];
        scheduler.EndEncoder();
    }
    scheduler.BlitEncoder();
    [scheduler.GetImpl().blit fillBuffer:big range:NSMakeRange(0, Size - 65536) value:0xC3];
    scheduler.MemoryBarrier();
    ReadWord(scheduler, k, big, 7, out);
    scheduler.Finish();
    CHECK(words[0] == 0xC3C3C3C3u);

    // A compute write through gpuAddress, MemoryBarrier, a second dispatch reading it, all in
    // the same encoder (the residency set, no useResource).
    id<MTLBuffer> cell = UntrackedBuffer(device, 4096);
    for (uint32_t round = 1; round <= 40; ++round) {
        scheduler.ComputeEncoder();
        auto& compute = scheduler.GetImpl().compute;
        const uint64_t address = cell.gpuAddress;
        const uint32_t value = 0xA5000000u + round;
        [compute setComputePipelineState:k.write_word];
        [compute setBytes:&address length:8 atIndex:0];
        [compute setBytes:&value length:4 atIndex:1];
        [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
        scheduler.MemoryBarrier();
        CHECK(scheduler.OpenEncoder() == Scheduler::EncoderKind::Compute);
        words[0] = 0;
        ReadWord(scheduler, k, cell, 0, out);
        scheduler.Finish();
        CHECK(words[0] == value);
    }

    // The same across a compute and a blit encoder (barrier with an open compute encoder, the
    // reader in the next encoder).
    for (uint32_t round = 1; round <= 10; ++round) {
        scheduler.ComputeEncoder();
        auto& compute = scheduler.GetImpl().compute;
        const uint64_t address = cell.gpuAddress;
        const uint32_t value = 0x5A000000u + round;
        [compute setComputePipelineState:k.write_word];
        [compute setBytes:&address length:8 atIndex:0];
        [compute setBytes:&value length:4 atIndex:1];
        [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
        scheduler.EndEncoder();
        scheduler.MemoryBarrier(); // no encoder open
        CHECK(scheduler.NeedFenceWait());
        scheduler.BlitEncoder();
        CHECK(!scheduler.NeedFenceWait());
        [scheduler.GetImpl().blit copyFromBuffer:cell sourceOffset:0 toBuffer:out destinationOffset:0 size:4];
        scheduler.Finish();
        CHECK(words[0] == value);
    }

    // A render encoder: barrier inside it, and an empty pass behind a compute encoder.
    MTLTextureDescriptor* texture_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:8
                                                          height:8
                                                       mipmapped:NO];
    texture_descriptor.usage = MTLTextureUsageRenderTarget;
    texture_descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> target = [device.GetImpl().device newTextureWithDescriptor:texture_descriptor];
    lifetime.push_back(target);
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    scheduler.ComputeEncoder(); // an empty encoder in front of the pass
    scheduler.EndEncoder();
    scheduler.RenderEncoder((__bridge const void*)pass);
    CHECK(scheduler.IsRendering());
    scheduler.MemoryBarrier();
    CHECK(scheduler.IsRendering());
    scheduler.EndRendering();
    CHECK(!scheduler.IsRendering());
    scheduler.RenderEncoder((__bridge const void*)pass); // after another encoder kind
    scheduler.ComputeEncoder();                           // ends the pass
    CHECK(!scheduler.IsRendering());
    scheduler.Finish();
    CHECK(!scheduler.HasFailed());
}

/// P6.2: a held pass takes no encoder; hoisted compute goes before it, after what came before.
static void TestHeldPass(Metal::Device& device, const Kernels& k) {
    std::printf("held pass\n");
    // Declared before the scheduler: release only after its destructor drains completions.
    std::vector<id> lifetime;
    Scheduler scheduler{device, "test-held"};
    VideoCore::GpuQueue& queue = scheduler;
    constexpr size_t Size = 64 << 20; // a long fill: a hoist that overtook it would be overwritten
    id<MTLBuffer> big = UntrackedBuffer(device, Size);
    id<MTLBuffer> out = [device.GetImpl().device newBufferWithLength:64
                                                             options:MTLResourceStorageModeShared];
    auto* words = static_cast<uint32_t*>(out.contents);
    MTLTextureDescriptor* texture_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:64
                                                          height:64
                                                       mipmapped:NO];
    texture_descriptor.usage = MTLTextureUsageRenderTarget;
    texture_descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> target = [device.GetImpl().device newTextureWithDescriptor:texture_descriptor];
    lifetime.push_back(target);
    const VideoCore::RenderState state{};

    for (int round = 1; round <= 12; ++round) {
        const uint8_t fill = static_cast<uint8_t>(round * 5);
        const uint32_t hoisted = 0xabc00000u + round;
        scheduler.BlitEncoder();
        [scheduler.GetImpl().blit fillBuffer:big range:NSMakeRange(0, Size) value:fill];
        scheduler.MemoryBarrier();

        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        const u64 serial = queue.HeldPassSerial();
        scheduler.RenderEncoder((__bridge const void*)pass, state);
        CHECK(queue.HoldingPass() && queue.HeldPassSerial() == serial + 1);
        CHECK(scheduler.IsRenderingWith(state));
        CHECK(scheduler.GetImpl().render == nil); // no encoder while held
        CHECK(scheduler.Commands().Recording());
        scheduler.MemoryBarrier(); // recorded in the pass
        CHECK(scheduler.Commands().HasBarrier());

        // Hoisted: before the pass, after the fill (it waits for the blit's fence).
        queue.BeginHoist();
        scheduler.ComputeEncoder();
        CHECK(scheduler.GetImpl().compute != nil && scheduler.IsRendering());
        {
            auto& compute = scheduler.GetImpl().compute;
            const uint64_t address = big.gpuAddress;
            [compute setComputePipelineState:k.write_word];
            [compute setBytes:&address length:8 atIndex:0];
            [compute setBytes:&hoisted length:4 atIndex:1];
            [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1)
                    threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
        }
        queue.EndHoist();
        CHECK(queue.HoldingPass());

        // Anything else releases the pass: it gets its encoder, then ends.
        words[0] = words[1] = 0;
        ReadWord(scheduler, k, big, 0, out);
        CHECK(!queue.HoldingPass() && !scheduler.IsRendering());
        if (round % 2 == 0) {
            scheduler.Flush();
        }
        scheduler.Finish();
        if(words[0]!=hoisted)std::printf("  held result round=%d actual=%#x expected=%#x\n",round,words[0],hoisted);
        CHECK(words[0] == hoisted);
    }
    // A pass held and released by Flush with nothing in it.
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    scheduler.RenderEncoder((__bridge const void*)pass, state);
    scheduler.Finish();
    CHECK(!queue.HoldingPass() && !scheduler.HasFailed());
}

static void TestGpuQueue(Metal::Device& device, const Kernels& k) {
    std::printf("gpu queue\n");
    // Declared before the scheduler: release only after its destructor drains completions.
    std::vector<id> lifetime;
    Scheduler scheduler{device, "test-queue"};
    VideoCore::GpuQueue& queue = scheduler;
    CHECK(!queue.HoldingPass() && queue.HoistEnabled()); // held passes are the default (P6.2)

    // The submit callback runs before every submission, then what it put in the submit info.
    int callbacks = 0, before_submit = 0, tick_in_callback = 0;
    queue.SetSubmitCallback([&](VideoCore::SubmitInfo& info) {
        ++callbacks;
        tick_in_callback = static_cast<int>(scheduler.CurrentTick());
        info.before_submit.emplace_back([&] { ++before_submit; });
        scheduler.Flush(); // a callback that flushes again is not called back
    });
    const u64 first = scheduler.CurrentTick();
    queue.Flush();
    CHECK(callbacks == 1 && before_submit == 1);
    CHECK(tick_in_callback == static_cast<int>(first));
    CHECK(scheduler.CurrentTick() == first + 2); // the nested Flush took one tick too
    queue.WaitSubmitted(first); // already submitted
    scheduler.Wait(first + 1);
    CHECK(queue.KnownGpuTick() >= first + 1);

    // WaitSubmitted of the open tick blocks until another thread flushes it.
    std::atomic<bool> waited{false};
    const u64 open = scheduler.CurrentTick();
    std::thread waiter([&] {
        queue.WaitSubmitted(open);
        waited = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    CHECK(!waited);
    queue.Flush();
    waiter.join();
    CHECK(waited);
    queue.SetSubmitCallback({});

    // Recording order and host copies are immediate.
    queue.KickRecording(true);
    queue.SyncRecording();
    queue.WaitHostCopies();
    queue.WaitHostCopiesFor(0, 16);
    int signalled = 0;
    queue.SignalAfterHostCopies([&] { ++signalled; });
    CHECK(signalled == 1);
    queue.WaitDeferredSignals();
    queue.BeginHoist();
    queue.EndHoist();
    queue.ReleaseHeldPass();
    CHECK(!queue.HoldingPass());

    // The render pass is compared with the state it was begun with.
    MTLTextureDescriptor* texture_descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:8
                                                          height:8
                                                       mipmapped:NO];
    texture_descriptor.usage = MTLTextureUsageRenderTarget;
    texture_descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> target = [device.GetImpl().device newTextureWithDescriptor:texture_descriptor];
    lifetime.push_back(target);
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    VideoCore::RenderState state{};
    state.width = state.height = 8;
    state.num_layers = 1;
    state.num_color_attachments = 1;
    state.color_attachments[0].image_view = 0x1234;
    VideoCore::RenderState other = state;
    other.color_attachments[0].image_view = 0x5678;
    CHECK(!queue.IsRenderingWith(state));
    CHECK(queue.WillBeginRendering(state));
    scheduler.RenderEncoder((__bridge const void*)pass, state);
    CHECK(scheduler.IsRendering());
    CHECK(queue.IsRenderingWith(state));
    CHECK(!queue.WillBeginRendering(state));
    CHECK(!queue.IsRenderingWith(other));
    CHECK(queue.WillBeginRendering(other));
    scheduler.EndRendering();
    CHECK(!queue.IsRenderingWith(state));
    scheduler.RenderEncoder((__bridge const void*)pass); // without a state: never matches
    CHECK(!queue.IsRenderingWith(state));
    scheduler.ComputeEncoder();
    CHECK(!queue.IsRenderingWith(state));
    scheduler.Finish();
    CHECK(!scheduler.HasFailed());
}

static void TestLabels(Metal::Device& device, const Kernels& k) {
    std::printf("labels\n");
    setenv("BB_MTL_LABELS", "1", 1);
    {
        id<MTLBuffer> out = [device.GetImpl().device newBufferWithLength:64
                                                                 options:MTLResourceStorageModeShared];
        Scheduler scheduler{device, "test-labels"};
        scheduler.PushDebugGroup("outer"); // no encoder yet: applies to the next one
        DispatchSpin(scheduler, k, out, 100);
        scheduler.PushDebugGroup("inner");
        scheduler.InsertDebugSignpost("signpost");
        scheduler.PopDebugGroup();
        scheduler.EndEncoder(); // pops "outer" too
        scheduler.BlitEncoder(); // "outer" again
        scheduler.PopDebugGroup();
        scheduler.PopDebugGroup(); // more pops than pushes: ignored
        scheduler.Finish();
        CHECK(!scheduler.HasFailed());
    }
    unsetenv("BB_MTL_LABELS");
}

static void TestErrors(Metal::Device& device, const Kernels& k) {
    std::printf("errors\n");
    Scheduler scheduler{device, "test-errors"};
    std::string report;
    scheduler.SetErrorHandler([&](const std::string& text) { report = text; });
    CHECK(!scheduler.HasFailed());
    // What the completed handler does for a command buffer in error status.
    NSError* error = [NSError errorWithDomain:MTLCommandBufferErrorDomain
                                         code:MTLCommandBufferErrorTimeout
                                     userInfo:@{NSLocalizedDescriptionKey : @"synthetic"}];
    scheduler.ReportFailure(7, "test-errors #7", error.localizedDescription.UTF8String);
    CHECK(scheduler.HasFailed());
    CHECK(report.find("test-errors #7") != std::string::npos);
    CHECK(report.find("synthetic") != std::string::npos);
    // Waiting on a tick that can no longer be signaled must not hang.
    const auto start = Clock::now();
    scheduler.Wait(scheduler.CurrentTick() + 3);
    CHECK(MsSince(start) < 2000);
}

static void TestGpuTiming(Metal::Device& device, const Kernels& k) {
    const char* timing=std::getenv("BB_MTL_GPU_TIMING");
    if (!timing || timing[0]!='1') return;
    std::printf("GPU hardware stage timing (held/replay, opaque work, overflow)\n");
    // Declared before the scheduler: release only after its destructor drains completions.
    std::vector<id> lifetime;
    Scheduler scheduler{device, "test-gpu-timing"};
    auto profile = scheduler.GetImpl().profile;
    CHECK(profile->Enabled());
    if (!profile->Enabled()) return;
    auto d=device.GetImpl().device;
    id<MTLBuffer> out=[d newBufferWithLength:64 options:MTLResourceStorageModeShared];
    DispatchSpin(scheduler,k,out,10000);
    scheduler.LabelEncoder("Camera motion");
    scheduler.EndWithBarrier();
    scheduler.BlitEncoder("Copy scene without motion blur");
    [scheduler.GetImpl().blit fillBuffer:out range:NSMakeRange(0,64) value:0x37];
    scheduler.EndWithBarrier();
    auto cb=scheduler.GetImpl().EnsureCommandBuffer();
    profile->Opaque(cb,true);
    auto encoder=[cb computeCommandEncoder];
    [encoder waitForFence:profile->OpaqueFence()];
    const uint32_t value=0x12345678; const uint64_t address=out.gpuAddress;
    [encoder setComputePipelineState:k.write_word];
    [encoder setBytes:&address length:8 atIndex:0];
    [encoder setBytes:&value length:4 atIndex:1];
    [encoder useResource:out usage:MTLResourceUsageWrite];
    [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];
    [encoder updateFence:profile->OpaqueFence()];
    [encoder endEncoding];
    profile->Opaque(cb,false);
    NSError* error=nil;
    auto lib=[d newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint v [[vertex_id]]) { float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[v],0,1); }\nfragment float4 fs(){return float4(.5,0,0,1);}\n" options:nil error:&error];
    CHECK(lib!=nil);
    auto* desc=[MTLRenderPipelineDescriptor new];
    desc.vertexFunction=[lib newFunctionWithName:@"vs"];desc.fragmentFunction=[lib newFunctionWithName:@"fs"];
    desc.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    auto pipeline=[d newRenderPipelineStateWithDescriptor:desc error:&error];CHECK(pipeline!=nil);
    auto* td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
    td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
    auto texture=[d newTextureWithDescriptor:td];
    lifetime.push_back(texture); lifetime.push_back(pipeline); lifetime.push_back(out);
    auto* pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    VideoCore::RenderState state{};state.width=state.height=8;state.num_layers=1;state.num_color_attachments=1;
    state.color_attachments[0].image_view=1;
    for(unsigned i=0;i<2;++i) {
        state.color_attachments[0].image_view=i+1;
        scheduler.RenderEncoder((__bridge const void*)pass,state);
        scheduler.LabelEncoder("Batched game velocity mirror");
        auto& list=scheduler.Commands();
        list.SetRenderPipelineState((__bridge void*)pipeline);
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
    }
    for(unsigned i=0;i<2;++i) {
        state.color_attachments[0].image_view=i+3;
        scheduler.RenderEncoder((__bridge const void*)pass,state);
        scheduler.LabelEncoder(i ? "P2 8x8 C1 vs a ps b" : "P1 8x8 C1 vs a ps b");
        auto& list=scheduler.Commands();list.SetRenderPipelineState((__bridge void*)pipeline);
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
    }
    scheduler.Finish();
    for(unsigned i=0;i<1000 && profile->Stats().completed<1;++i) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    const auto stats=profile->Stats();
    CHECK(stats.completed>=1);CHECK(stats.invalid==0);
    for(auto stage:{Metal::GpuProfile::CameraMotion,Metal::GpuProfile::BlurCopy,Metal::GpuProfile::MetalFX,Metal::GpuProfile::VelocityDraw}) {
        CHECK(stats.stages[stage].count>0);CHECK(stats.stages[stage].ns>0);
    }
    CHECK(stats.stages[Metal::GpuProfile::VelocityDraw].count==2);
    const auto& render=stats.render[Metal::GpuProfile::GuestRender];
    CHECK(render.vertex.count==2);CHECK(render.vertex.ns>0);
    CHECK(render.fragment.count>0);CHECK(render.fragment.ns>0);
    CHECK(render.paired<=render.vertex.count && render.paired<=render.fragment.count);
    CHECK(render.overlap_ns>=0 && render.overlap_ns<=std::min(render.vertex.ns,render.fragment.ns));
    CHECK(render.gap_ns>=0);
    CHECK(std::abs(stats.stages[Metal::GpuProfile::GuestRender].ns-(render.vertex.ns+render.fragment.ns-render.overlap_ns+render.gap_ns))<1);
    CHECK(*static_cast<const uint32_t*>(out.contents)==value);
    uint8_t pixel[4]{};[texture getBytes:pixel bytesPerRow:4 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
    CHECK(pixel[0]>=127 && pixel[0]<=128);CHECK(pixel[3]==255);
    // The original descriptor must not retain or reuse sample attachments across passes.
    CHECK(pass.sampleBufferAttachments[0].sampleBuffer==nil);
    // Capacity overflow must continue encoding normally and never wrap timestamp indices.
    for(unsigned i=0;i<Metal::GpuProfile::MaxEntries+2;++i) {
        scheduler.BlitEncoder("Copy scene without motion blur");
        [scheduler.GetImpl().blit fillBuffer:out range:NSMakeRange(4,4) value:0x5a];
        scheduler.EndEncoder();
    }
    scheduler.Finish();
    CHECK(static_cast<const uint32_t*>(out.contents)[1]==0x5a5a5a5a);
    for(unsigned i=0;i<1000 && profile->Stats().completed<2;++i) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    CHECK(!scheduler.HasFailed());profile->Report();
}

static void TestColorClearBatch(Metal::Device& device) {
    Scheduler scheduler(device,"clear batch test");auto gpu=device.GetImpl().device;
    auto make=[&](unsigned size,MTLStorageMode mode) {
        auto* d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:size height:size mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=mode;
        return [gpu newTextureWithDescriptor:d];
    };
    id<MTLTexture> a=make(4,MTLStorageModePrivate),b=make(4,MTLStorageModePrivate);
    CHECK(scheduler.QueueColorClear((__bridge void*)a,{1,0,0,1}));scheduler.MemoryBarrier();
    CHECK(scheduler.QueueColorClear((__bridge void*)b,{0,1,0,1}));scheduler.MemoryBarrier();
    CHECK(scheduler.QueueColorClear((__bridge void*)a,{0,0,1,1}));scheduler.MemoryBarrier();
    CHECK(scheduler.GetImpl().pending_clear_count==2);
    auto read=[&](id<MTLTexture> texture,unsigned red,unsigned green,unsigned blue) {
        id<MTLBuffer> out=[gpu newBufferWithLength:1024 options:MTLResourceStorageModeShared];
        const bool barrier=scheduler.GetImpl().pending_clear_barrier;
        scheduler.BlitEncoder("clear batch pixel read");if(barrier)CHECK(scheduler.GetImpl().outstanding_fences==0);
        [scheduler.GetImpl().blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(1,1,1) toBuffer:out destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];
        scheduler.Finish();const auto* p=static_cast<const unsigned char*>(out.contents);
        CHECK(p[0]==red&&p[1]==green&&p[2]==blue&&p[3]==255);
    };
    read(a,0,0,255);read(b,0,255,0);CHECK(scheduler.GetImpl().clear_passes==1);
    CHECK(!scheduler.QueueColorClear((__bridge void*)make(4,MTLStorageModeShared),{0,0,0,1}));
    auto* depth_desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float width:4 height:4 mipmapped:NO];
    depth_desc.usage=MTLTextureUsageRenderTarget;depth_desc.storageMode=MTLStorageModePrivate;
    CHECK(!scheduler.QueueColorClear((__bridge void*)[gpu newTextureWithDescriptor:depth_desc],{0,0,0,1}));
    id<MTLTexture> small=make(2,MTLStorageModePrivate);
    CHECK(scheduler.QueueColorClear((__bridge void*)small,{1,0,0,1}));scheduler.MemoryBarrier();
    CHECK(scheduler.QueueColorClear((__bridge void*)a,{0,1,0,1}));scheduler.MemoryBarrier();
    read(small,255,0,0);read(a,0,255,0);CHECK(scheduler.GetImpl().clear_passes==3);
    // Nine distinct targets must flush at the eight-attachment limit.
    for(unsigned i=0;i<9;++i)CHECK(scheduler.QueueColorClear((__bridge void*)make(4,MTLStorageModePrivate),{1,0,0,1}));
    scheduler.Finish();CHECK(scheduler.GetImpl().clear_passes==5);
    puts("clear batching: exact pixels, repeated target, dimensions, format/storage rejection, fences and 8-target bound passed");
}

static void TestNativeMetadata(Metal::Device& device) {
    char directory[]="/private/tmp/bb-native-gpu-test-XXXXXX";CHECK(mkdtemp(directory));
    std::filesystem::path root=directory,trigger=root/"trigger";
    setenv("BB_CAPTURE_NATIVE","1",1);setenv("BB_CAPTURE_DIR",directory,1);setenv("BB_CAPTURE_TRIGGER",trigger.c_str(),1);
    using VideoCore::FrameCapture;
    Scheduler scheduler(device,"native metadata test");
    auto* d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:4 height:4 mipmapped:NO];
    d.usage=MTLTextureUsageRenderTarget;d.storageMode=MTLStorageModeShared;
    id<MTLTexture> texture=[device.GetImpl().device newTextureWithDescriptor:d];
    auto* pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;
    pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
    std::ofstream(trigger)<<"1";FrameCapture::OnFlip(123);FrameCapture::FrameBoundary(101);
    scheduler.RenderEncoder((__bridge void*)pass);scheduler.Flush();scheduler.Finish();
    FrameCapture::FrameBoundary(102);
    std::string text;for(auto& entry:std::filesystem::directory_iterator(root))if(entry.path().extension()==".txt") {std::ifstream f(entry.path());text.assign(std::istreambuf_iterator<char>(f),{});}
    CHECK(text.find("\"frame\":101")!=std::string::npos);CHECK(text.find("\"load\":2")!=std::string::npos);CHECK(text.find("\"store\":1")!=std::string::npos);
    CHECK(text.find("\"w\":4")!=std::string::npos);CHECK(text.find("\"truncated\":false")!=std::string::npos);
    unsigned char pixels[64]{};[texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,4,4) mipmapLevel:0];
    CHECK(pixels[0]==255&&pixels[1]==0&&pixels[2]==0&&pixels[3]==255);
    std::filesystem::remove_all(root);
}

static void TestPreciseHostResidency(Metal::Device& device,const Kernels& k) {
    const bool enabled=[] {const char* v=std::getenv("BB_MTL_PRECISE_RESIDENCY");return v&&v[0]=='1';}();
    auto gpu=device.GetImpl().device;
    id<MTLBuffer> guest=[gpu newBufferWithLength:4096 options:MTLResourceStorageModeShared];
    id<MTLBuffer> out=[gpu newBufferWithLength:64 options:MTLResourceStorageModeShared];
    device.GetImpl().AddGuestBuffer(guest); // deliberately absent from the residency set
    *static_cast<u32*>(guest.contents)=0x1937cafe;
    auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:4 height:4 mipmapped:NO];
    td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
    auto texture=[gpu newTextureWithDescriptor:td];
    std::vector<id> lifetime; // retained through scheduler destruction / command-buffer retirement
    Scheduler scheduler{device,"precise host residency"};
    auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;
    pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
    for(unsigned mode=0;mode<3;++mode) {
        bool hoisted=mode==2 && !(std::getenv("BB_MTL_HOLD") && std::getenv("BB_MTL_HOLD")[0]=='0');
        if(hoisted) {VideoCore::RenderState state{};scheduler.RenderEncoder((__bridge void*)pass,state);scheduler.BeginHoist();}
        scheduler.ComputeEncoder("direct bindings only",true);
        auto encoder=scheduler.GetImpl().compute;
        CHECK(scheduler.GetImpl().compute_guest_declared==!enabled);
        const u32 n=1;
        [encoder setComputePipelineState:k.spin];[encoder setBuffer:out offset:0 atIndex:0];[encoder setBytes:&n length:4 atIndex:1];
        [encoder dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];
        // A guest raw pointer read in the SAME physical encoder must add guest residency first.
        ReadWord(scheduler,k,guest,0,out);
        CHECK(scheduler.GetImpl().compute==encoder);CHECK(scheduler.GetImpl().compute_guest_declared);
        scheduler.ComputeEncoder("host after indirect",true);
        CHECK(scheduler.GetImpl().compute==encoder);CHECK(scheduler.GetImpl().compute_guest_declared); // never downgrade a mixed pass
        if(hoisted)scheduler.EndHoist();
        scheduler.Finish();CHECK(*static_cast<u32*>(out.contents)==0x1937cafe);
        scheduler.HostRenderEncoder((__bridge void*)pass);scheduler.Finish();
        unsigned char pixels[64]{};[texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,4,4) mipmapLevel:0];
        for(unsigned i=0;i<16;++i)CHECK(pixels[i*4]==255 && pixels[i*4+1]==0 && pixels[i*4+2]==0 && pixels[i*4+3]==255);
    }
    NSError* error=nil;
    const char* graphics=R"(
#include <metal_stdlib>
using namespace metal;
vertex float4 vp(uint v [[vertex_id]]) {float2 p=float2((v<<1u)&2u,v&2u);return float4(p*2-1,0,1);}
fragment float4 fp(constant ulong& address [[buffer(0)]]) {
 uint w=*reinterpret_cast<device const uint*>(address);
 return float4(w&255u,(w>>8u)&255u,(w>>16u)&255u,w>>24u)/255.0f;
}
)";
    auto library=[gpu newLibraryWithSource:[NSString stringWithUTF8String:graphics] options:nil error:&error];CHECK(library!=nil);
    auto pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[library newFunctionWithName:@"vp"];pd.fragmentFunction=[library newFunctionWithName:@"fp"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    auto pipeline=[gpu newRenderPipelineStateWithDescriptor:pd error:&error];CHECK(pipeline!=nil);
    lifetime.push_back(library);lifetime.push_back(pipeline);
    for(unsigned mode=0;mode<3;++mode) {
        VideoCore::RenderState state{};
        scheduler.RenderEncoder((__bridge void*)pass,state,false,true);
        if(!scheduler.HoldingPass())CHECK(scheduler.GetImpl().render_guest_declared==!enabled);
        scheduler.RequireGuestRender(); // union before draws, also carried through replay jobs
        if(mode==0) {scheduler.ReleaseHeldPass();CHECK(scheduler.GetImpl().render_guest_declared);}
        auto& list=scheduler.Commands();const uint64_t address=guest.gpuAddress;
        list.SetRenderPipelineState((__bridge void*)pipeline);
        list.SetBytes(Metal::RenderList::Stage::Fragment,&address,8,0);
        const Metal::RenderList::Viewport viewport{0,0,4,4,0,1};list.SetViewports(&viewport,1);
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        if(mode==1) {
            MTLRenderPassDescriptor* load=[pass copy];load.colorAttachments[0].loadAction=MTLLoadActionLoad;
            scheduler.RenderEncoder((__bridge void*)load,state,false,true); // posts preceding held pass to replay
        }
        scheduler.Finish();
        unsigned char pixels[64]{};[texture getBytes:pixels bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,4,4) mipmapLevel:0];
        for(unsigned i=0;i<16;++i)CHECK(pixels[i*4]==254 && pixels[i*4+1]==202 && pixels[i*4+2]==55 && pixels[i*4+3]==25);
    }
    CHECK(!scheduler.HasFailed());
    puts("precise residency: host/guest compute upgrade, mixed/hoisted access, held/direct/replayed guest-pointer draws and exact pixels passed");
}


// Real Metal pixels and scheduler boundaries, not just descriptor shape. The clear values
// must remain visible to hoisted indirect work, render tails and later native readers.
static void TestClearFolding(Metal::Device& device) {
    auto gpu=device.GetImpl().device;
    const bool enabled=std::getenv("BB_MTL_FOLD_CLEARS") && std::getenv("BB_MTL_FOLD_CLEARS")[0]=='1';
    const bool held=!(std::getenv("BB_MTL_HOLD") && std::getenv("BB_MTL_HOLD")[0]=='0');
    std::vector<id> retained_textures; // command buffers deliberately do not retain resources
    auto make=[&](unsigned n=4) {
        auto d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:n height:n mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
        d.storageMode=MTLStorageModePrivate;
        auto texture=[gpu newTextureWithDescriptor:d];retained_textures.push_back(texture);return texture;
    };
    auto descriptor=[&](id<MTLTexture> t) {
        auto d=[MTLRenderPassDescriptor renderPassDescriptor];
        d.colorAttachments[0].texture=t;d.colorAttachments[0].loadAction=MTLLoadActionLoad;
        d.colorAttachments[0].storeAction=MTLStoreActionStore;return d;
    };
    auto read=[&](Scheduler& q,id<MTLTexture> t,unsigned r,unsigned g,unsigned b) {
        id<MTLBuffer> out=[gpu newBufferWithLength:1024 options:MTLResourceStorageModeShared];
        q.BlitEncoder("clear fold exact pixel read");
        [q.GetImpl().blit copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
          sourceSize:MTLSizeMake(t.width,t.height,1) toBuffer:out destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:1024];
        q.Finish();
        auto bytes=static_cast<const unsigned char*>(out.contents);
        for(unsigned y=0;y<t.height;++y)for(unsigned x=0;x<t.width;++x) {
            auto p=bytes+y*256+x*4;CHECK(p[0]==r&&p[1]==g&&p[2]==b&&p[3]==255);
        }
        CHECK(!q.HasFailed());
    };
    // Both direct-host and held/posted guest passes consume the exact same queued clears.
    for(bool guest:{false,true})for(bool own_clear:{false,true}) {
        Scheduler q(device,"fold exact clear");auto t=make();auto pass=descriptor(t);
        if(own_clear) {pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].clearColor=MTLClearColorMake(0,1,0,1);}
        CHECK(q.QueueColorClear((__bridge void*)t,{1,0,0,1}));q.MemoryBarrier();
        VideoCore::RenderState state{};
        if(guest)q.RenderEncoder((__bridge void*)pass,state);else q.HostRenderEncoder((__bridge void*)pass);
        CHECK(pass.colorAttachments[0].loadAction==(own_clear?MTLLoadActionClear:MTLLoadActionLoad));
        if(guest && held) {
            // No-dispatch BeginHoist/EndHoist is harmless. ReplayDepth path commits once.
            q.BeginHoist();q.EndHoist();
            auto next=descriptor(make());next.colorAttachments[0].loadAction=MTLLoadActionClear;
            q.RenderEncoder((__bridge void*)next,state);
        }
        read(q,t,own_clear?0:255,own_clear?255:0,0);
        CHECK(q.GetImpl().clear_folded==unsigned(enabled));
        CHECK(q.GetImpl().clear_passes==unsigned(!enabled));
        CHECK(!q.GetImpl().folded_clears);
    }
    // Several MRT clears, including a repeated target, remain exact with one folded batch.
    {
        Scheduler q(device,"fold MRT");auto a=make(),b=make();auto pass=descriptor(a);
        pass.colorAttachments[1].texture=b;pass.colorAttachments[1].loadAction=MTLLoadActionLoad;pass.colorAttachments[1].storeAction=MTLStoreActionStore;
        CHECK(q.QueueColorClear((__bridge void*)a,{1,0,0,1}));CHECK(q.QueueColorClear((__bridge void*)b,{0,1,0,1}));
        CHECK(q.QueueColorClear((__bridge void*)a,{0,0,1,1}));q.MemoryBarrier();
        q.RenderEncoder((__bridge void*)pass,VideoCore::RenderState{});
        read(q,a,0,0,255);read(q,b,0,255,0);CHECK(q.GetImpl().clear_folded_targets==(enabled?2:0));
    }
    // A real hoisted kernel must see red, then write blue; the subsequent Load must retain blue.
    if(held) {
        NSError* error=nil;
        NSString* src=@"#include <metal_stdlib>\nusing namespace metal;\nkernel void test(texture2d<float,access::read_write> t [[texture(0)]],device uint* out [[buffer(0)]],uint2 p [[thread_position_in_grid]]){float4 x=t.read(p);out[p.y*4+p.x]=uint(x.r*255.0f);t.write(float4(0,0,1,1),p);}";
        auto lib=[gpu newLibraryWithSource:src options:nil error:&error];CHECK(lib);
        auto kernel=Pipeline(gpu,lib,@"test");
        Scheduler q(device,"fold hoist fallback");auto t=make();auto pass=descriptor(t);
        auto out=[gpu newBufferWithLength:64 options:MTLResourceStorageModeShared];
        CHECK(q.QueueColorClear((__bridge void*)t,{1,0,0,1}));q.MemoryBarrier();
        q.RenderEncoder((__bridge void*)pass,VideoCore::RenderState{});
        q.BeginHoist();q.ComputeEncoder("clear must precede hoist",true);
        CHECK(!q.GetImpl().folded_clears);
        auto c=q.GetImpl().compute;[c setComputePipelineState:kernel];[c setTexture:t atIndex:0];[c setBuffer:out offset:0 atIndex:0];
        [c dispatchThreads:MTLSizeMake(4,4,1) threadsPerThreadgroup:MTLSizeMake(4,4,1)];q.EndHoist();
        read(q,t,0,0,255);for(unsigned i=0;i<16;++i)CHECK(static_cast<const u32*>(out.contents)[i]==255);
        CHECK(q.GetImpl().clear_folded==0);CHECK(q.GetImpl().clear_fold_fallbacks==unsigned(enabled));
    }
    for(unsigned reason=0;reason<6;++reason) {
        Scheduler q(device,"fold rejection preserves content");auto t=make(),other=make();auto pass=descriptor(t);
        CHECK(q.QueueColorClear((__bridge void*)t,{1,0,0,1}));
        switch(reason) {
        case 0: CHECK(q.QueueColorClear((__bridge void*)other,{0,1,0,1}));break; // unmatched target
        case 1: pass.renderTargetWidth=2;pass.renderTargetHeight=2;break; // partial extent
        case 2: pass.colorAttachments[1].texture=make(2);pass.colorAttachments[1].loadAction=MTLLoadActionClear;pass.colorAttachments[1].storeAction=MTLStoreActionStore;break;
        case 3: pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;break;
        case 4: q.SetRenderTail([&]{});q.RequestRenderTail();break;
        case 5: q.BlitEncoder("intervening read boundary");q.EndEncoder();break;
        }
        q.HostRenderEncoder((__bridge void*)pass);q.EndRendering();q.Finish();
        CHECK(q.GetImpl().clear_folded==0);CHECK(!q.GetImpl().folded_clears);
        if(reason!=3)read(q,t,255,0,0);
        if(reason==0)read(q,other,0,255,0);
    }
    puts("clear folding: exact host/guest/replayed/MRT pixels, hoisted read/write fallback, render-tail, extent and unmatched-resource rejection passed");
}


static void TestClearFoldBlending(Metal::Device& device) {
    auto gpu=device.GetImpl().device;NSError* error=nil;
    NSString* src=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 v(uint id [[vertex_id]]){return float4(id==1?3.0f:-1.0f,id==2?3.0f:-1.0f,0,1);}\nfragment float4 f(){return float4(0.123f,0.234f,0.345f,0.37f);}";
    auto options=[MTLCompileOptions new];options.mathMode=MTLMathModeSafe;
    auto lib=[gpu newLibraryWithSource:src options:options error:&error];CHECK(lib);
    for(auto format:{MTLPixelFormatRGBA8Unorm,MTLPixelFormatRGBA8Unorm_sRGB,MTLPixelFormatRGBA16Float,MTLPixelFormatRG11B10Float,MTLPixelFormatRGB10A2Unorm}) {
        auto pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"v"];pd.fragmentFunction=[lib newFunctionWithName:@"f"];
        auto a=pd.colorAttachments[0];a.pixelFormat=format;a.blendingEnabled=YES;
        a.sourceRGBBlendFactor=MTLBlendFactorSourceAlpha;a.destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
        a.sourceAlphaBlendFactor=MTLBlendFactorOne;a.destinationAlphaBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
        auto pso=[gpu newRenderPipelineStateWithDescriptor:pd error:&error];CHECK(pso);
        for(auto color:{MTLClearColorMake(0,0,0,0),MTLClearColorMake(1,1,1,1),MTLClearColorMake(.1,.2,.3,.4),MTLClearColorMake(.5001,.9999,.0001,.3333)}) {
            std::array<id<MTLBuffer>,2> outputs;std::array<id<MTLTexture>,2> textures;
            for(unsigned variant=0;variant<2;++variant) {
                auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:4 height:4 mipmapped:NO];td.storageMode=MTLStorageModePrivate;td.usage=MTLTextureUsageRenderTarget;
                textures[variant]=[gpu newTextureWithDescriptor:td];auto texture=textures[variant];
                outputs[variant]=[gpu newBufferWithLength:1024 options:MTLResourceStorageModeShared];
                Scheduler q(device,"clear fold blend equivalence");
                auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].clearColor=color;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
                if(variant==0) {q.HostRenderEncoder((__bridge void*)pass);q.EndRendering();}
                else CHECK(q.QueueColorClear((__bridge void*)texture,{color.red,color.green,color.blue,color.alpha}));
                pass.colorAttachments[0].loadAction=MTLLoadActionLoad;q.HostRenderEncoder((__bridge void*)pass);
                auto r=q.GetImpl().render;[r setRenderPipelineState:pso];[r setViewport:MTLViewport{0,0,4,4,0,1}];[r drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                q.BlitEncoder();[q.GetImpl().blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(4,4,1) toBuffer:outputs[variant] destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:1024];q.Finish();CHECK(!q.HasFailed());
            }
            const unsigned row_bytes=format==MTLPixelFormatRGBA16Float?32:16;bool equal=true;
            for(unsigned y=0;y<4;++y)if(std::memcmp(static_cast<const char*>(outputs[0].contents)+y*256,static_cast<const char*>(outputs[1].contents)+y*256,row_bytes)!=0)equal=false;
            if(!equal)printf("clear fold blend mismatch: format=%u clear=(%.8f,%.8f,%.8f,%.8f)\n",unsigned(format),color.red,color.green,color.blue,color.alpha);
            CHECK(equal);
        }
    }
    puts("clear folding: full-surface blended color matches separate clear/store/load at original pixel precision");
}


static void TestConsumedClearContinuation(Metal::Device& device) {
    using namespace Metal::PassContinuation;
    auto gpu = device.GetImpl().device;
    const auto make = [&](MTLPixelFormat format) {
        auto d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:4 height:4 mipmapped:NO];
        d.storageMode = MTLStorageModePrivate;
        d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead | MTLTextureUsagePixelFormatView;
        return [gpu newTextureWithDescriptor:d];
    };
    auto color = make(MTLPixelFormatRGBA8Unorm);
    auto depth = make(MTLPixelFormatDepth32Float_Stencil8);
    auto sampled = make(MTLPixelFormatRGBA8Unorm);
    auto alias = [color newTextureViewWithPixelFormat:MTLPixelFormatRGBA8Unorm];
    CHECK(color && depth && sampled && alias);
    VideoCore::RenderState clear{};
    clear.width=clear.height=4; clear.num_layers=1; clear.num_color_attachments=1;
    clear.color_attachments[0].image_view=(uintptr_t)(__bridge void*)color;
    clear.color_attachments[0].image_layout=vk::ImageLayout::eColorAttachmentOptimal;
    clear.color_attachments[0].is_clear=1;
    clear.depth_stencil_attachment.image_view=(uintptr_t)(__bridge void*)depth;
    clear.depth_stencil_attachment.image_layout=vk::ImageLayout::eDepthAttachmentOptimal;
    clear.depth_stencil_attachment.has_depth=true;
    clear.depth_stencil_attachment.depth_clear=true;
    clear.depth_stencil_attachment.has_stencil=true;
    clear.depth_stencil_attachment.stencil_clear=true;
    clear.depth_stencil_attachment.clear_value[1]=7;
    clear.depth_stencil_attachment.clear_value[0]=0x3f800000;
    auto load=clear;
    load.color_attachments[0].is_clear=0;
    load.depth_stencil_attachment.depth_clear=false;
    load.depth_stencil_attachment.stencil_clear=false;
    load.depth_stencil_attachment.clear_value={};
    const auto descriptor = [&] {
        auto p=[MTLRenderPassDescriptor renderPassDescriptor];
        p.colorAttachments[0].texture=color;
        p.colorAttachments[0].loadAction=MTLLoadActionClear;
        p.colorAttachments[0].storeAction=MTLStoreActionStore;
        p.depthAttachment.texture=depth;
        p.depthAttachment.loadAction=MTLLoadActionClear;
        p.depthAttachment.storeAction=MTLStoreActionStore;
        p.depthAttachment.clearDepth=1;
        p.stencilAttachment.texture=depth;
        p.stencilAttachment.loadAction=MTLLoadActionClear;
        p.stencilAttachment.storeAction=MTLStoreActionStore;
        p.stencilAttachment.clearStencil=7;
        return p;
    };
    void* unrelated=(__bridge void*)sampled;
    void* feedback=(__bridge void*)alias;
    CHECK(TexturesExcludeAttachments(load, std::span<void* const>(&unrelated,1)));
    CHECK(!TexturesExcludeAttachments(load, std::span<void* const>(&feedback,1)));
    std::vector<id> retained; // outlive scheduler completion handlers (unretained command buffers)
    Scheduler q{device,"consumed clear test"};
    // Exercise every scheduler exclusion with an actual held/direct encoder.
    for (unsigned kind=0;kind<6;++kind) {
        auto p=descriptor();
        q.RenderEncoder((__bridge void*)p,clear,true,true);
        CHECK(q.Commands().CanContinueConsumedClear());
        if(kind==0) q.Commands().SetTexture(Metal::RenderList::Stage::Fragment,feedback,0);
        if(kind==1) q.RequireGuestRender();
        if(kind==2) q.RequestRenderTail();
        if(kind==3) { q.BeginHoist(); q.ComputeEncoder(); }
        if(kind==4) q.Commands().MemoryBarrier();
        if(kind==5) load.depth_stencil_attachment.image_layout=vk::ImageLayout::eDepthReadOnlyOptimal;
        const bool continued=q.ContinueConsumedClear(load);
        if(continued) std::printf("unexpected continuation guard case %u\n",kind);
        CHECK(!continued);
        if(kind==3) q.EndHoist();
        load.depth_stencil_attachment.image_layout=vk::ImageLayout::eDepthAttachmentOptimal;
        q.Finish(); CHECK(!q.HasFailed());
    }
    NSError* error=nil;
    auto lib=[gpu newLibraryWithSource:@R"(
#include <metal_stdlib>
using namespace metal;
struct V {float4 p [[position]];};
vertex V vs(uint id [[vertex_id]], constant float& z [[buffer(0)]]) {
 float2 p=float2((id<<1u)&2u,id&2u);return {float4(p*2-1,z,1)};
}
fragment float4 fs(constant float4& c [[buffer(0)]]) {return c;}
)" options:nil error:&error];
    CHECK(lib); retained.push_back(lib);
    auto pd=[MTLRenderPipelineDescriptor new];
    pd.vertexFunction=[lib newFunctionWithName:@"vs"];pd.fragmentFunction=[lib newFunctionWithName:@"fs"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;pd.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float_Stencil8;pd.stencilAttachmentPixelFormat=MTLPixelFormatDepth32Float_Stencil8;
    auto pso=[gpu newRenderPipelineStateWithDescriptor:pd error:&error];CHECK(pso);
    auto dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLess;dd.depthWriteEnabled=YES;
    dd.frontFaceStencil.stencilCompareFunction=MTLCompareFunctionEqual;
    dd.backFaceStencil.stencilCompareFunction=MTLCompareFunctionEqual;
    auto ds=[gpu newDepthStencilStateWithDescriptor:dd];CHECK(ds);
    auto out=[gpu newBufferWithLength:2048 options:MTLResourceStorageModeShared];
    retained.push_back(pso); retained.push_back(ds); retained.push_back(out);
    auto queries=[gpu newBufferWithLength:16 options:MTLResourceStorageModeShared];
    retained.push_back(queries); std::memset(queries.contents,0,16);
    unsigned query_count=0;
    q.SetVisibilityQuery([&](const void*) -> std::pair<void*,u64> {
        CHECK(query_count<2);return {(__bridge void*)queries,(query_count++)*8};
    });
    const auto draw = [&](float z, std::array<float,4> c) {
        auto& list=q.Commands();
        list.SetRenderPipelineState((__bridge void*)pso);list.SetDepthStencilState((__bridge void*)ds);
        list.SetStencilReference(7,7);
        Metal::RenderList::Viewport viewport{0,0,4,4,0,1};list.SetViewports(&viewport,1);
        list.SetBytes(Metal::RenderList::Stage::Vertex,&z,sizeof(z),0);
        list.SetBytes(Metal::RenderList::Stage::Fragment,c.data(),sizeof(c),0);
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
    };
    auto p=descriptor();q.RenderEncoder((__bridge void*)p,clear,true,true);
    draw(.5f,{1,0,0,1});
    CHECK(q.ContinueConsumedClear(load));
    CHECK(q.IsRenderingWith(load));CHECK(!q.IsRenderingWith(clear));
    CHECK(!q.ContinueConsumedClear(clear));
    draw(.75f,{0,1,0,1}); // must fail depth: preserve the first draw's depth
    q.BlitEncoder();
    [q.GetImpl().blit copyFromTexture:color sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(4,4,1) toBuffer:out destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:1024];
    p=descriptor();q.RenderEncoder((__bridge void*)p,clear,true,true); // fresh identical clear must execute
    draw(.75f,{0,1,0,1});
    q.BlitEncoder();
    [q.GetImpl().blit copyFromTexture:color sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(4,4,1) toBuffer:out destinationOffset:1024 destinationBytesPerRow:256 destinationBytesPerImage:1024];
    q.Finish();CHECK(!q.HasFailed());
    CHECK(query_count==2);
    const auto* query_values=static_cast<const u64*>(queries.contents);
    CHECK(query_values[0]==16 && query_values[1]==16);
    const auto* bytes=static_cast<const unsigned char*>(out.contents);
    for(unsigned y=0;y<4;++y)for(unsigned x=0;x<4;++x) {
        auto a=bytes+y*256+x*4;auto b=bytes+1024+y*256+x*4;
        CHECK(a[0]==255 && a[1]==0 && a[2]==0 && a[3]==255);
        CHECK(b[0]==0 && b[1]==255 && b[2]==0 && b[3]==255);
    }
    puts("consumed clear: depth continuity, identical fresh clear, alias feedback, shader metadata, tail, hoist, barrier and layout exclusions passed");
}


static void TestDepthLayoutContinuation(Metal::Device& device) {
    const bool enabled=Metal::PassContinuation::DepthLayoutEnabled();
    auto gpu=device.GetImpl().device;
    auto make=[&](MTLPixelFormat format) {
        auto d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:4 height:4 mipmapped:NO];
        d.storageMode=MTLStorageModePrivate;
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;
        return [gpu newTextureWithDescriptor:d];
    };
    auto color=make(MTLPixelFormatRGBA8Unorm), depth=make(MTLPixelFormatDepth32Float_Stencil8);
    CHECK(color && depth);
    VideoCore::RenderState state{};state.width=state.height=4;state.num_layers=1;state.num_color_attachments=1;
    state.color_attachments[0].image_view=(uintptr_t)(__bridge void*)color;
    state.color_attachments[0].image_layout=vk::ImageLayout::eColorAttachmentOptimal;
    state.depth_stencil_attachment.image_view=(uintptr_t)(__bridge void*)depth;
    state.depth_stencil_attachment.image_layout=vk::ImageLayout::eDepthStencilAttachmentOptimal;
    state.depth_stencil_attachment.has_depth=state.depth_stencil_attachment.has_stencil=true;
    auto next=state;next.depth_stencil_attachment.image_layout=vk::ImageLayout::eDepthStencilReadOnlyOptimal;
    auto descriptor=[&](bool clear) {
        auto p=[MTLRenderPassDescriptor renderPassDescriptor];
        p.colorAttachments[0].texture=color;p.depthAttachment.texture=p.stencilAttachment.texture=depth;
        p.colorAttachments[0].loadAction=p.depthAttachment.loadAction=p.stencilAttachment.loadAction=clear?MTLLoadActionClear:MTLLoadActionLoad;
        p.colorAttachments[0].storeAction=p.depthAttachment.storeAction=p.stencilAttachment.storeAction=MTLStoreActionStore;
        p.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);p.depthAttachment.clearDepth=1;p.stencilAttachment.clearStencil=0;
        return p;
    };
    NSError* error=nil;
    auto lib=[gpu newLibraryWithSource:@R"(
#include <metal_stdlib>
using namespace metal;
struct V {float4 p [[position]];};
vertex V vs(uint id [[vertex_id]], constant float& z [[buffer(0)]]) {
 float2 p=float2((id<<1u)&2u,id&2u);return {float4(p*2-1,z,1)};
}
fragment float4 fs(constant float4& c [[buffer(0)]]) {return c;}
)" options:nil error:&error];CHECK(lib);
    auto pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"vs"];pd.fragmentFunction=[lib newFunctionWithName:@"fs"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    pd.depthAttachmentPixelFormat=pd.stencilAttachmentPixelFormat=MTLPixelFormatDepth32Float_Stencil8;
    auto pso=[gpu newRenderPipelineStateWithDescriptor:pd error:&error];CHECK(pso);
    auto dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLess;dd.depthWriteEnabled=YES;
    dd.frontFaceStencil.stencilCompareFunction=dd.backFaceStencil.stencilCompareFunction=MTLCompareFunctionAlways;
    dd.frontFaceStencil.depthStencilPassOperation=dd.backFaceStencil.depthStencilPassOperation=MTLStencilOperationReplace;
    auto write=[gpu newDepthStencilStateWithDescriptor:dd];CHECK(write);
    dd.depthWriteEnabled=NO;
    dd.frontFaceStencil.stencilCompareFunction=dd.backFaceStencil.stencilCompareFunction=MTLCompareFunctionEqual;
    dd.frontFaceStencil.depthStencilPassOperation=dd.backFaceStencil.depthStencilPassOperation=MTLStencilOperationKeep;
    auto read=[gpu newDepthStencilStateWithDescriptor:dd];CHECK(read);
    auto pixels=[gpu newBufferWithLength:1024 options:MTLResourceStorageModeShared];
    auto queries=[gpu newBufferWithLength:32 options:MTLResourceStorageModeShared];
    // Keep all GPU objects alive until the scheduler and its deferred operations are gone.
    std::vector<id> retained{color,depth,lib,pso,write,read,pixels,queries};
    Scheduler q{device,"depth layout pixel and visibility equivalence"};
    auto p=descriptor(true);q.HostRenderEncoder((__bridge void*)p);q.Finish();
    unsigned count=0;std::memset(queries.contents,0,32);
    q.SetVisibilityQuery([&](const void*) -> std::pair<void*,u64> {CHECK(count<4);return {(__bridge void*)queries,(count++)*8};},Scheduler::VisibilityMergePolicy::Additive);
    auto draw=[&](float z,std::array<float,4> c,bool writes,unsigned stencil=7) {
        auto& list=q.Commands();list.SetRenderPipelineState((__bridge void*)pso);
        list.SetDepthStencilState((__bridge void*)(writes?write:read));list.SetStencilReference(stencil,stencil);
        Metal::RenderList::Viewport v{0,0,4,4,0,1};list.SetViewports(&v,1);
        list.SetBytes(Metal::RenderList::Stage::Vertex,&z,sizeof(z),0);
        list.SetBytes(Metal::RenderList::Stage::Fragment,c.data(),sizeof(c),0);
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
    };
    p=descriptor(false);q.RenderEncoder((__bridge void*)p,state,true,true);draw(.5f,{1,0,0,1},true);
    const auto change=[&](const auto& target) {
        const bool allowed=Metal::PassContinuation::DepthLayoutWritePromotion(*q.OpenRenderState(),target);
        bool merged=q.ContinueDepthLayout(target);CHECK(merged==(enabled&&allowed));
        if(!merged){auto load=descriptor(false);q.RenderEncoder((__bridge void*)load,target,true,true);}
        CHECK(q.IsRenderingWith(target));
    };
    change(next);draw(.25f,{0,0,1,1},false); // reads depth, writes no depth/stencil
    change(state);draw(.75f,{0,1,0,1},true); // must fail depth
    change(next);draw(.4f,{1,1,0,1},false); // succeeds only if readonly .25 did not change depth
    draw(.1f,{1,0,1,1},false,3); // must fail stencil and retain yellow
    q.BlitEncoder();[q.GetImpl().blit copyFromTexture:color sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(4,4,1) toBuffer:pixels destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:1024];
    q.Finish();CHECK(!q.HasFailed());CHECK(count==(enabled?3u:4u));
    u64 total=0;for(unsigned i=0;i<count;++i)total+=static_cast<const u64*>(queries.contents)[i];CHECK(total==48);
    auto bytes=static_cast<const unsigned char*>(pixels.contents);
    for(unsigned y=0;y<4;++y)for(unsigned x=0;x<4;++x){auto c=bytes+y*256+x*4;CHECK(c[0]==255&&c[1]==255&&c[2]==0&&c[3]==255);}
    q.SetVisibilityQuery({});
    for(unsigned mode=0;mode<11;++mode) {
        if(mode>=9) q.SetVisibilityQuery([&](const void*) -> std::pair<void*,u64> {
            return {(__bridge void*)queries,0};
        }, mode==9?Scheduler::VisibilityMergePolicy::Forbidden:Scheduler::VisibilityMergePolicy::NativeDensity);
        auto load=descriptor(false);q.RenderEncoder((__bridge void*)load,next,true,mode!=1);
        if(mode==2)q.RequireGuestRender();
        if(mode==3)q.Commands().SetTexture(Metal::RenderList::Stage::Fragment,(__bridge void*)depth,0);
        if(mode==4)q.MemoryBarrier();
        if(mode==5)q.RequestRenderTail();
        if(mode==6) {
            q.Commands().SetTexture(Metal::RenderList::Stage::Fragment,(__bridge void*)depth,0);
            q.Commands().SetTexture(Metal::RenderList::Stage::Fragment,nullptr,0);
        }
        if(mode==7) {
            auto view=[color newTextureViewWithPixelFormat:MTLPixelFormatRGBA8Unorm];CHECK(view);
            retained.push_back(view);
            q.Commands().SetTexture(Metal::RenderList::Stage::Vertex,(__bridge void*)view,0);
            q.Commands().SetTexture(Metal::RenderList::Stage::Vertex,nullptr,0);
        }
        if(mode==8)q.EndWithBarrier();

        CHECK(q.ContinueDepthLayout(state)==(enabled&&mode==0));
        q.Finish();CHECK(!q.HasFailed());
        q.SetVisibilityQuery({});
    }
    puts("depth-layout pixels, depth/stencil decisions, visibility sum and shader/feedback/barrier/tail guards passed");
}

int main() {
    @autoreleasepool {
        Metal::Device device;
        if(std::getenv("BB_TEST_DEPTH_LAYOUT_ONLY")){TestDepthLayoutContinuation(device);return failures?1:0;}
        if(std::getenv("BB_TEST_CONSUMED_CLEAR_ONLY")){TestConsumedClearContinuation(device);return failures?1:0;}
        if(std::getenv("BB_TEST_CLEAR_BLEND_ONLY")){TestClearFoldBlending(device);return failures?1:0;}
        if(std::getenv("BB_TEST_CLEAR_FOLD_ONLY")){TestClearFolding(device);return failures?1:0;}
        if(std::getenv("BB_TEST_COLOR_CLEAR_BATCH_ONLY")){TestColorClearBatch(device);return failures?1:0;}
        if(std::getenv("BB_TEST_NATIVE_CAPTURE_ONLY")){TestNativeMetadata(device);return failures?1:0;}
        NSError* error = nil;
        id<MTLLibrary> library = [device.GetImpl().device newLibraryWithSource:kernel_source
                                                                       options:nil
                                                                         error:&error];
        if (!library) {
            std::printf("kernels: %s\n", error.localizedDescription.UTF8String);
            return 2;
        }
        Kernels k;
        k.spin = Pipeline(device.GetImpl().device, library, @"spin");
        k.write_word = Pipeline(device.GetImpl().device, library, @"write_word");
        k.read_word = Pipeline(device.GetImpl().device, library, @"read_word");

        if(std::getenv("BB_TEST_PRECISE_RESIDENCY_ONLY")) {TestPreciseHostResidency(device,k);return failures?1:0;}
        if (std::getenv("BB_TEST_GPU_TIMING_ONLY")) {
            TestGpuTiming(device,k);
            std::printf(failures ? "FAILED: %d check(s)\n" : "all checks passed\n", failures);
            return failures ? 1 : 0;
        }
        TestTicks(device, k);
        const uint32_t spin_n = Calibrate(device, k, 20.0);
        TestDeferred(device, k, spin_n);
        TestFenceChain(device, k);
        TestHeldPass(device, k);
        TestGpuQueue(device, k);
        TestLabels(device, k);
        TestGpuTiming(device, k);
        TestErrors(device, k);
    }
    std::printf(failures ? "FAILED: %d check(s)\n" : "all checks passed\n", failures);
    return failures ? 1 : 0;
}
