#include "bb_stutter_audit.h"
// SPDX-License-Identifier: GPL-2.0-or-later
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <chrono>
#include <cstdio>
#include <dlfcn.h>
#include <mach/mach.h>
#include <mutex>
#include <memory>
#include <unordered_map>
#include <vector>
#include "bbport_resource_lifetime.h"
#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include "video_core/renderer_metal/mtl_guest_memory.h"
namespace Metal::MemoryStats {
namespace {
using VideoCore::FrameCapture;
std::uint64_t NowUs() {return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
struct Entry { std::uint64_t bytes; Kind kind; bool retired = false, discarded = false; BbResourceLifetime::Tag guest{};
    std::uint64_t uid=0, born_us=0, retired_us=0;
    std::array<std::uint64_t,unsigned(Content::Count)> content{};
    std::uint64_t content_us=0; };
struct Destroyed { std::uint64_t native=0; Entry entry{}; std::uint64_t destroyed_us=0; };
struct LifetimeAudit { std::uint64_t next_uid=0, destroyed=0; std::array<Destroyed,256> recent{}; };
struct Ledger {
    std::mutex mutex;
    std::unordered_map<const void*, Entry> entries;
    Snapshot totals{};
    std::unique_ptr<LifetimeAudit> audit;
};
// Metal deallocations may run during global teardown. This small ledger outlives them.
Ledger& Get() { static auto* value = new Ledger; return *value; }
void Remove(const void* key) {
    auto& l = Get(); std::scoped_lock lock{l.mutex};
    auto it = l.entries.find(key); if (it == l.entries.end()) return;
    auto e = it->second; auto& c = l.totals[unsigned(e.kind)];
    c.bytes -= e.bytes; --c.objects; c.released += e.bytes;
    if (e.retired) c.retired -= e.bytes;
    if (e.discarded) c.discarded -= e.bytes;
    if(l.audit) {
        auto& a=*l.audit;
        a.recent[a.destroyed++%a.recent.size()]={std::uint64_t(reinterpret_cast<uintptr_t>(key)),e,NowUs()};
    }
    l.entries.erase(it);
}
}
}
@interface BBMetalMemoryLifetime : NSObject { @public const void* key; }
@end
@implementation BBMetalMemoryLifetime
- (void)dealloc { Metal::MemoryStats::Remove(key); }
@end
namespace Metal::MemoryStats {
namespace { char lifetime_key; }
void Track(const void* pointer, Kind kind) {
    if (!pointer) return;
    id<MTLResource> resource = (__bridge id<MTLResource>)pointer;
    if (objc_getAssociatedObject(resource, &lifetime_key)) return;
    {
        auto& l = Get(); std::scoped_lock lock{l.mutex};
        auto [it, added] = l.entries.emplace(pointer, Entry{resource.allocatedSize, kind});
        if (!added) return;
        if(FrameCapture::NativeMode()) {
            if(!l.audit)l.audit=std::make_unique<LifetimeAudit>();
            it->second.uid=++l.audit->next_uid;it->second.born_us=NowUs();
        }
        auto& c = l.totals[unsigned(kind)]; c.bytes += it->second.bytes; ++c.objects;
        c.peak = std::max(c.peak, c.bytes);
    }
    auto token = [BBMetalMemoryLifetime new]; token->key = pointer;
    objc_setAssociatedObject(resource, &lifetime_key, token, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
void Reclassify(const void* pointer, Kind kind) {
    auto& l = Get(); std::scoped_lock lock{l.mutex};
    auto it = l.entries.find(pointer); if (it == l.entries.end() || it->second.kind == kind) return;
    auto& e = it->second; auto& old = l.totals[unsigned(e.kind)]; auto& now = l.totals[unsigned(kind)];
    old.bytes -= e.bytes; --old.objects; now.bytes += e.bytes; ++now.objects;
    if (e.retired) { old.retired -= e.bytes; now.retired += e.bytes; }
    if (e.discarded) { old.discarded -= e.bytes; now.discarded += e.bytes; }
    now.peak = std::max(now.peak, now.bytes); e.kind = kind;
}
void Associate(const void* pointer, std::uint64_t address, std::uint64_t size) {
    if (!BbResourceLifetime::Enabled()) return;
    const auto tag=BbResourceLifetime::Get().Owner(address,size);
    auto& l=Get(); std::scoped_lock lock{l.mutex};
    if(auto it=l.entries.find(pointer);it!=l.entries.end())it->second.guest=tag;
}
std::array<Association, static_cast<unsigned>(Kind::Count)> ReadAssociations() {
    std::vector<Entry> entries;
    {auto& l=Get();std::scoped_lock lock{l.mutex};entries.reserve(l.entries.size());
     for(const auto& [key,e]:l.entries)entries.push_back(e);}
    // Never acquire the guest allocation lock while holding the ledger lock: a cache
    // retirement can enter this ledger while keeping the guest generation stable.
    std::array<Association, static_cast<unsigned>(Kind::Count)> result{};
    for(const auto& e:entries) {
        auto& r=result[unsigned(e.kind)];
        switch(BbResourceLifetime::Get().Status(e.guest)) {
        case BbResourceLifetime::State::Live:r.live+=e.bytes;break;
        case BbResourceLifetime::State::Released:r.released+=e.bytes;break;
        case BbResourceLifetime::State::Reused:r.reused+=e.bytes;break;
        default:r.unknown+=e.bytes;break;
        }
    }
    return result;
}
void Retire(const void* pointer) {
    auto& l = Get(); std::scoped_lock lock{l.mutex};
    auto it = l.entries.find(pointer); if (it == l.entries.end() || it->second.retired) return;
    if(l.audit)it->second.retired_us=NowUs();
    it->second.retired = true; l.totals[unsigned(it->second.kind)].retired += it->second.bytes;
}
bool IsDiscarded(const void* pointer) {
    auto& l = Get(); std::scoped_lock lock{l.mutex}; auto it = l.entries.find(pointer);
    return it != l.entries.end() && it->second.discarded;
}
void Discarded(const void* pointer, bool value) {
    auto& l = Get(); std::scoped_lock lock{l.mutex};
    auto it = l.entries.find(pointer); if (it == l.entries.end() || it->second.discarded == value) return;
    auto& c = l.totals[unsigned(it->second.kind)];
    if (value) c.discarded += it->second.bytes; else c.discarded -= it->second.bytes;
    it->second.discarded = value;
}
Snapshot Read() { auto& l = Get(); std::scoped_lock lock{l.mutex}; return l.totals; }
bool ContentEnabled() {
    static const bool audit=[] {const auto* v=std::getenv("BB_TEXTURE_AUDIT_DIR");return v && *v;}();
    return audit || FrameCapture::NativeMode();
}
ContentSnapshot ReadContent(const void* pointer) {
    if(!ContentEnabled() || !pointer)return {};
    id<MTLTexture> texture=(__bridge id<MTLTexture>)pointer;
    for(unsigned depth=0;depth<8 && texture.parentTexture;++depth)texture=texture.parentTexture;
    auto& l=Get();std::scoped_lock lock{l.mutex};
    if(auto i=l.entries.find((__bridge const void*)texture);i!=l.entries.end())
        return {i->second.content,i->second.content_us};
    return {};
}
ContentIdentity ReadIdentity(const void* pointer) {
    if(!ContentEnabled() || !pointer)return {};
    id<MTLTexture> texture=(__bridge id<MTLTexture>)pointer;
    for(unsigned depth=0;depth<8 && texture.parentTexture;++depth)texture=texture.parentTexture;
    auto& l=Get();std::scoped_lock lock{l.mutex};
    if(auto i=l.entries.find((__bridge const void*)texture);i!=l.entries.end())
        return {i->second.uid,i->second.born_us,i->second.guest.address,i->second.guest.generation};
    return {};
}
void NoteContent(const void* pointer, Content event) {
    if(!ContentEnabled() || !pointer || unsigned(event)>=unsigned(Content::Count))return;
    // Native textures/views only; no pixels, command-buffer wait or retained history.
    id<MTLTexture> texture=(__bridge id<MTLTexture>)pointer;
    for(unsigned depth=0;depth<8 && texture.parentTexture;++depth)texture=texture.parentTexture;
    auto& l=Get();std::scoped_lock lock{l.mutex};
    if(auto i=l.entries.find((__bridge const void*)texture);i!=l.entries.end()) {
        ++i->second.content[unsigned(event)];i->second.content_us=NowUs();
    }
}
void CaptureLifetimes() {
    if(!FrameCapture::NativeRecording())return;
    // Copy integers only, then release the ledger lock before emitting. The fixed ring
    // reports overwritten history explicitly; absence never proves no destruction.
    std::vector<std::pair<std::uint64_t,Entry>> entries;
    LifetimeAudit history{};bool overflow=false;
    {auto& l=Get();std::scoped_lock lock{l.mutex};
     if(!l.audit)return;
     history=*l.audit;entries.reserve(std::min<size_t>(l.entries.size(),2048));
     for(const auto& [pointer,e]:l.entries) {
         if(entries.size()==2048){overflow=true;break;}
         entries.emplace_back(reinterpret_cast<uintptr_t>(pointer),e);
     }}
    const auto now=NowUs();
    FrameCapture::Native(std::format("{{\"op\":\"memory_lifetimes\",\"now_us\":{},\"live_count\":{},\"live_overflow\":{},\"destroyed_total\":{},\"destroyed_ring\":256}}",now,entries.size(),overflow,history.destroyed));
    for(const auto& [native,e]:entries)
        FrameCapture::Native(std::format("{{\"op\":\"memory_live\",\"uid\":{},\"native\":{},\"kind\":{},\"bytes\":{},\"retired\":{},\"discarded\":{},\"born_us\":{},\"retired_us\":{},\"guest\":{},\"generation\":{},\"content_us\":{},\"uploads\":{},\"clears\":{},\"copies\":{},\"draws\":{},\"storage_binds\":{},\"unknown_writes\":{}}}",e.uid,native,unsigned(e.kind),e.bytes,e.retired,e.discarded,e.born_us,e.retired_us,e.guest.address,e.guest.generation,e.content_us,e.content[0],e.content[1],e.content[2],e.content[3],e.content[4],e.content[5]));
    for(const auto& d:history.recent)if(d.destroyed_us)
        FrameCapture::Native(std::format("{{\"op\":\"memory_destroyed\",\"uid\":{},\"native\":{},\"kind\":{},\"bytes\":{},\"born_us\":{},\"retired_us\":{},\"destroyed_us\":{}}}",d.entry.uid,d.native,unsigned(d.entry.kind),d.entry.bytes,d.entry.born_us,d.entry.retired_us,d.destroyed_us));
}
void Report(const void* pointer, bool force) {
    using Clock = std::chrono::steady_clock;
    static auto last = Clock::time_point{};
    const auto now = Clock::now();
    if (!force && now - last < std::chrono::seconds(5)) return;
    last = now;
    BbAudit::Scope report_audit{BbAudit::MemoryReport,0,1000000};
    const auto snapshot = Read();
    constexpr const char* names[] = {"buffers", "staging", "sampled-textures", "render-targets", "scene-proxies", "velocity", "upscaler-owned", "occlusion"};
    std::uint64_t owned = 0;
    for (unsigned i = 0; i < snapshot.size(); ++i) {
        const auto& c = snapshot[i]; owned += c.bytes;
        std::printf("Metal memory category %s: allocated=%llu bytes, objects=%llu, retired=%llu bytes, discarded-capacity=%llu bytes, released=%llu bytes, peak=%llu bytes\n", names[i], (unsigned long long)c.bytes, (unsigned long long)c.objects, (unsigned long long)c.retired, (unsigned long long)c.discarded, (unsigned long long)c.released, (unsigned long long)c.peak);
    }
    if (BbResourceLifetime::Enabled()) {
        const auto correlated=ReadAssociations();
        for(unsigned i=0;i<correlated.size();++i) {
            const auto& c=correlated[i];
            std::printf("Metal guest association %s: live=%llu bytes, released=%llu bytes, reused=%llu bytes, unknown=%llu bytes; allocation capacity, not additional resident memory\n",names[i],(unsigned long long)c.live,(unsigned long long)c.released,(unsigned long long)c.reused,(unsigned long long)c.unknown);
        }
        BbResourceLifetime::Report();
    }
    task_vm_info_data_t vm{}; mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    const bool valid = task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vm, &count) == KERN_SUCCESS && count >= TASK_VM_INFO_REV3_COUNT;
    id<MTLDevice> device = (__bridge id<MTLDevice>)pointer;
    // Opaque allocations include MetalFX internals, pipelines, drawables and driver metadata.
    // This is reconciliation, not a fabricated exact total for any of those components.
    const auto aliases = GuestMemory::ImportedBytes();
    const auto allocated = std::uint64_t(device.currentAllocatedSize);
    const auto accounted = aliases + owned;
    std::printf("Metal memory totals: footprint=%llu bytes, graphics-ledger=%llu bytes, metal-allocated=%llu bytes, owned=%llu bytes, guest-alias-capacity=%llu bytes, unclassified-metal=%llu bytes, ledger-valid=%d; aliases overlap guest CPU pages, discarded capacity is not resident usage\n", (unsigned long long)vm.phys_footprint, (unsigned long long)(vm.ledger_tag_graphics_footprint > 0 ? vm.ledger_tag_graphics_footprint : 0), (unsigned long long)allocated, (unsigned long long)owned, (unsigned long long)aliases, (unsigned long long)(allocated > accounted ? allocated - accounted : 0), valid);
    // Optional ABI: old probes still work, but cannot supply direct-allocation liveness.
    using GuestStats = int (*)(std::uint64_t*, std::uint64_t);
    static const auto guest = reinterpret_cast<GuestStats>(dlsym(RTLD_DEFAULT, "runtime_memory_snapshot_v1"));
    std::uint64_t g[16]{};
    if (guest && guest(g, 16))
        std::printf("Guest memory categories: direct-live=%llu bytes (type0=%llu, type3=%llu, other=%llu), flexible-live=%llu bytes, imported-capacity=%llu bytes, imported-live=%llu bytes, imported-free-retained=%llu bytes, direct-release-calls=%llu, direct-released-total=%llu bytes, gpu-release-zeroed-total=%llu bytes, mapped-direct-alias-bytes=%llu, free-chunks=%llu; allocation capacity is not resident usage\n", (unsigned long long)g[0], (unsigned long long)g[1], (unsigned long long)g[2], (unsigned long long)g[3], (unsigned long long)g[4], (unsigned long long)g[5], (unsigned long long)g[6], (unsigned long long)g[7], (unsigned long long)g[8], (unsigned long long)g[9], (unsigned long long)g[10], (unsigned long long)g[11], (unsigned long long)g[12]);
    else std::printf("Guest memory categories: snapshot ABI unavailable; imported liveness unknown\n");
}
}
