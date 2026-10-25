// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: the native GPU process split (docs/macos-native-gpu.md), as the rest of the GPU library
// sees it. With BB_NATIVE_GPU=1 the game process (bb-probe, x86-64) keeps the HLE libraries
// (GnmDriver, VideoOut, event queues) and forwards what reaches the GPU core to bb-gpu (arm64),
// which runs that core: Liverpool, the caches, the shader recompiler, the presenter and the window.
// In-process (the default) both flags stay false and none of this runs.
#pragma once

#include <cstdint>
#include <span>
#include <functional>
#include "remote/bb_live_payload.h"
#include "../../../bb_runtime/src/runtime_read_arm.h"

#include "../../bbgpu.h"
#include "common/types.h"

namespace Libraries::VideoOut {
struct VideoOutPort;
struct BufferAttribute;
struct FlipStatus;
struct SceVideoOutVblankStatus;
} // namespace Libraries::VideoOut

namespace BbRemote {

inline bool front_active = false; ///< this is the game process and the GPU core is in bb-gpu
inline bool back_active = false;  ///< this is bb-gpu

inline bool FrontActive() {
    return front_active;
}
inline bool BackActive() {
    return back_active;
}

/// BB_NATIVE_GPU=1 in an x86-64 build: the game process should start bb-gpu.
bool Requested();

namespace Front {
/// Starts bb-gpu and initializes the GPU core there. False (reason printed) on failure.
bool Init(const BbGpuConfig& config);
void BeforeRestart();

void SubmitGfx(std::span<const u32> dcb, std::span<const u32> ccb);
void SubmitAsc(u32 gnm_vqid, std::span<const u32> acb);
void SubmitDone(u64 frame);
bool IsGpuIdle();

struct AscQueue {
    u64 map_addr;
    u32 ring_size_dw;
};
/// Slot index of the new compute queue, or -1.
s32 MapComputeQueue(u64 ring_base, u32* read_ptr, u32 ring_size_dw, u32 pipe_id);
void UnmapComputeQueue(u32 index);
AscQueue GetAscQueue(u32 index);

/// The game process's VideoOut port (the driver's main_port): kept in step with bb-gpu's.
void SetVideoOutPort(Libraries::VideoOut::VideoOutPort* port);
u64* VideoOutLabels();
int VoOpen();
void VoClose(s32 handle);
int VoRegisterBuffers(s32 start, void* const* addresses, s32 count,
                      const Libraries::VideoOut::BufferAttribute* attribute);
int VoUnregisterBuffers(s32 attribute_index);
int VoChangeBufferAttribute(s32 attribute_index,
                            const Libraries::VideoOut::BufferAttribute* attribute);
bool VoSubmitFlip(s32 index, s64 flip_arg);
s32 VoSubmitEopFlip(s32 handle, u32 buf_id, u32 mode, s64 flip_arg);
void VoSetFlipRate(s32 handle, s32 rate);
void VoSetHdr(s32 handle, bool hdr);
bool VoIsHdrSupported();
void VoSetGamma(float gamma);

/// The loader's SIGSEGV/SIGBUS handler: 1 when the fault was GPU page tracking (handled).
int HandleFault(void* context, void* address);
/// CPU wrote guest memory outside page tracking (decoded video frames).
void InvalidateMemory(u64 address, u64 size);
/// Moves [address, address + size) (page-aligned, mapped) into shared memory with protection
/// `prot` (PROT_*), so bb-gpu sees it at the same address. 0 on success.
int ShareRange(void* address, u64 size, int prot);

int OverlayCapturesInput();
/// bb-gpu's window keyboard (bbgpu_keyboard_state).
const bool* KeyboardState();
void MouseTake(double*,double*,int*,int*);
int MouseCaptured();
void MouseEnable(int);
int TextInputBegin(const char* initial, const char* prompt);
int TextInputPoll(char* out, u64 size);
} // namespace Front

namespace Back {
/// bb-gpu's entry (bb_gpu_main.cpp): never returns.
int Main(int argc, char** argv);

// Calls from the GPU core that the game process must hear of.
void FramesRetired(u64 frames);
void ReleaseSubmissionLock();
void Irq(u32 irq);
/// Called with the port's port_mutex held, after flip_status changed; `flip_event`: a flip
/// completed (the game process triggers its flip events with `flip_arg`).
void VoFlipStatus(const Libraries::VideoOut::FlipStatus& status, bool flip_event, s64 flip_arg);
/// Called with vo_mutex held after a vblank: `status` after it, `event_count` the events' count.
void VoVblank(const Libraries::VideoOut::SceVideoOutVblankStatus& status, u64 event_count);
u64* VideoOutLabels();
/// From the window loop: state the game process reads directly (settings menu open...).
void PublishWindowState();
void RequestRestart();
/// ARM requests x86 guest hooks, keeping executable patches in the game process.
void InstallGuestHooks(void (*fresh)(u64,u64));
int MemorySnapshot(u64* out,u64 count);
// Explicit experiment only. The synchronous callback is metadata/encoding-only,
// with real frontend + mirror read ownership; it cannot wait/compile/allocate/RPC.
// A caller-owned cache lock may already be held: all local table/Tracker grants
// use try-lock and the independent frontend worker never enters cache/driver code.
// Validate requires an original identity; empty origins cannot request capture.
bool WithLivePayload(BbPayload::Origin expected,u64 address,u64 size,
                     const std::function<bool(const LivePayloadReceipt&)>&);
// Production raw commit requires this form after LayerArmReadTraps completed
// outside the lease. The frontend verifies and freezes actual VramData protection.
bool WithProtectedLivePayload(BbPayload::Origin expected,u64 address,u64 size,
                     const std::function<bool(const LivePayloadReceipt&)>&);
// Successful committed slices only. Performs conditional frontend retirement and
// synchronizes the backend trap copy locally; never forwards a trap-clear RPC.
bool RetireReadProtection(BbPayload::Origin expected,u64 address,u64 size,
 const std::function<bool(const LivePayloadReceipt&)>&,LivePayloadRetirement* out=nullptr);
// Metadata-only pre-capture before first native construction; caller must then
// validate the same Origin after cold CreateImage. Unknown remains unknown.
bool CaptureLivePayload(u64 address,u64 size,
                        const std::function<bool(const LivePayloadReceipt&)>&);
void SetGuestChunkWhole(int whole);
void SetGuestChunkAllocator(int (*allocate)(u64,u64));
/// After the frame stats: the game process prints its threads' CPU time and the guest's waits.
void GameStats(double window_s, double frames);

// The runtime's GPU interface (runtime_memory_*, clocks), served from bb-gpu's own view.
u64 MemoryClamp(uintptr_t address, u64 size);
int MemoryRegion(uintptr_t address, uintptr_t* start, uintptr_t* end, int* mapped);
const u64* MemoryGeneration();
int MemoryVmaInfo(uintptr_t address, int* prot, int* type, uintptr_t* end);
int MemoryDirectPhys(uintptr_t address, u64* phys, uintptr_t* end);
/// Stable alias of the shared pool, for native Metal host-memory import.
void* MemoryBackingPointer(u64 phys);
u64 MemoryBackingSize();
int MemoryIsMapped(uintptr_t address, u64 size);
int MemoryWriteBacking(uintptr_t address, const void* data, u64 size);
void MemoryReadBacking(uintptr_t address, void* data, u64 size);
void MemoryGpuProtect(uintptr_t address, u64 size, int read, int write);
void MemoryTrap(uintptr_t address, u64 size, unsigned reason, int on);
unsigned MemoryTrapReasons(uintptr_t address);
bool ArmRead(const RuntimeReadArmArgs&,RuntimeReadArmReceipt&);
bool FinishReadArm(uint64_t token,bool commit,RuntimeReadArmReceipt&);
using GpuRange = void (*)(uintptr_t address, u64 size);
void SetGpuHooks(GpuRange map, GpuRange unmap, GpuRange invalidate);
void SetNoteWriteHook(GpuRange hook);
void SetCpuWriteHook(GpuRange hook);
u64 ProcessTimeUs();
u64 ProcessTimeCounter();
u64 TscFrequency();
u64 ReadTsc();
} // namespace Back

} // namespace BbRemote
