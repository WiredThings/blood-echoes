// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/frame_capture.h"
#include "bbport_texture_trace_batch.h"

#include <algorithm>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <format>
#include <mutex>
#include <memory>
#include <cstring>
#include <limits>

#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/texture_cache/image_info.h"

namespace VideoCore {

std::atomic<u64> g_bb_flip_count{0};

namespace {

// Private component-side diagnostic admission. Normal Play clears general capture
// variables; this explicit Shared-only selector does not enable scripted input or
// change the launcher's language/save/network/graphics policies.
const std::string& TextureDiagnosticDir() {
    static const std::string path=[]()->std::string {
        const char* dir=std::getenv("BB_TEXTURE_BINDING_CAPTURE_DIR");
        const char* on=std::getenv("BB_CAPTURE_TEXTURE_BINDINGS");
        if(!dir || !*dir || !on || std::strcmp(on,"1"))return {};
        const std::filesystem::path root="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state";
        const auto requested=std::filesystem::path(dir).lexically_normal();
        const auto lexical=requested.lexically_relative(root);
        if(lexical.empty()||lexical=="."||*lexical.begin()=="..")return {};
        std::error_code ec;const auto canonical=std::filesystem::canonical(requested,ec);
        if(ec||std::filesystem::is_symlink(requested)||!std::filesystem::is_directory(canonical))return {};
        const auto relative=canonical.lexically_relative(root);
        if(relative.empty()||relative=="."||*relative.begin()=="..")return {};
        return canonical.string();
    }();
    return path;
}

struct Target {
    VAddr address{};
    vk::Format format{};
    u32 width{}, height{};
    bool operator==(const Target&) const = default;
};

struct Entry {
    bool compute{};
    std::vector<Target> colors;
    Target depth;
    u32 draws{};
    u64 indices{};
    std::vector<u64> shaders; ///< vs/ps pairs (graphics) or cs (compute), first few
    std::vector<std::pair<Target, bool>> sampled; ///< textures (false) and storage images (true)
    std::string note;
    std::vector<std::string> buffers; ///< dumped constants, per draw
};

// GPU thread only.
std::vector<Entry> entries;
std::vector<std::pair<Target, bool>> pending_sampled;
std::vector<std::string> pending_buffers;

void AddBuffers(Entry& entry) {
    // Constants of full-screen passes (post-processing: camera matrices) and of the first draw
    // of each geometry pass.
    if (entry.draws <= 4) {
        for (auto& b : pending_buffers) {
            entry.buffers.push_back(std::move(b));
        }
    }
    pending_buffers.clear();
}
bool pass_open = false;
std::mutex native_mutex;
std::vector<std::string> native_lines;
BbTextureTraceBatch texture_trace_batch;
size_t native_bytes=0;
bool native_truncated=false;
u64 native_frame=0;
u32 native_remaining=1,native_series_count=1;
std::atomic<u64> native_capture_sequence{0};
// A bounded watcher for the observed small command-write range. The payload is compared
// only on CPU, never exported or read back from GPU; reset at both capture boundaries.
struct DependencyAudit {
    struct Watch { u64 address=0, resource=0, native=0, offset=0, generation=0, version=0;
        bool active=false, has_payload=false;
        std::array<unsigned char,192> payload{}; };
    std::array<Watch,8> watches{};
    std::array<u64,7> command_read_sources{};
    u64 sequence=0, uses=0;
    u64 srt_calls=0, srt_covered=0, srt_encoded_reads=0;
    bool overflow=false;
    static bool Overlap(u64 a,u64 size,u64 b,u64 bytes) {
        return size && bytes && (a<=b ? b-a<size : a-b<bytes);
    }
};
constexpr std::array<const char*,7> command_read_names={"cp_vo_wait","cp_graphics_wait",
    "cp_compute_wait","cp_cond_exec","indirect_geometry_args","indirect_draw_args",
    "indirect_dispatch_args"};
std::mutex dependency_mutex;
std::unique_ptr<DependencyAudit> dependency_audit;
void ResetDependencyAudit(bool allocate) {
    std::scoped_lock lock{dependency_mutex};
    if(allocate && !dependency_audit)dependency_audit=std::make_unique<DependencyAudit>();
    if(dependency_audit)*dependency_audit={};
    if(allocate && dependency_audit) {
        // A requested fixed range is observed before its first command write too. No
        // contents or GPU allocation; malformed/unbounded watches remain disabled.
        const char* input=std::getenv("BB_WATCH");char* end=nullptr;
        const u64 address=input?std::strtoull(input,&end,0):0;
        const u64 size=end && *end==','?std::strtoull(end+1,&end,0):0;
        if(end && !*end && address && address<(u64{1}<<40) && size==192 && size<=(u64{1}<<40)-address) {
            auto& w=dependency_audit->watches[0];w.address=address;w.active=true;
        }
    }
}
void RecordCommandReadCounts() {
    std::scoped_lock lock{dependency_mutex};
    if(!dependency_audit)return;
    const auto& n=dependency_audit->command_read_sources;
    FrameCapture::Native(std::format("{{\"op\":\"command_read_source_counts\",\"cp_vo_wait\":{},\"cp_graphics_wait\":{},\"cp_compute_wait\":{},\"cp_cond_exec\":{},\"indirect_geometry_args\":{},\"indirect_draw_args\":{},\"indirect_dispatch_args\":{}}}",
        n[0],n[1],n[2],n[3],n[4],n[5],n[6]));
    const auto& a=*dependency_audit;
    FrameCapture::Native(std::format("{{\"op\":\"srt_walk_coverage\",\"calls\":{},\"covered\":{},\"uncovered\":{},\"encoded_read_sites\":{}}}",
        a.srt_calls,a.srt_covered,a.srt_calls-a.srt_covered,a.srt_encoded_reads));
}

void* native_boundary_owner=nullptr;
void (*native_boundary_drain)(void*)=nullptr;
std::mutex display_mutex;
std::vector<VAddr> display_buffers;
// bbport: read on every draw; the game registers a handful of display buffers once.
std::array<std::atomic<VAddr>, 16> display_list{};
std::atomic<u32> display_count{0};

Target ToTarget(const VideoCore::ImageInfo& info) {
    return {info.guest_address, info.pixel_format, info.size.width, info.size.height};
}

std::string Describe(const Target& t) {
    if (!t.address) {
        return "-";
    }
    return std::format("{:#x} {} {}x{}", t.address, vk::to_string(t.format), t.width, t.height);
}

void AddSampled(Entry& entry) {
    for (const auto& s : pending_sampled) {
        if (entry.sampled.size() < 32 &&
            std::ranges::find(entry.sampled, s) == entry.sampled.end()) {
            entry.sampled.push_back(s);
        }
    }
    pending_sampled.clear();
}

void AddShader(Entry& entry, u64 hash) {
    if (entry.shaders.size() < 8 && std::ranges::find(entry.shaders, hash) == entry.shaders.end()) {
        entry.shaders.push_back(hash);
    }
}

void Write(VAddr presented) {
    const char* dir = std::getenv("BB_CAPTURE_DIR");
    if(!dir && !TextureDiagnosticDir().empty())dir=TextureDiagnosticDir().c_str();
    const std::string path = FrameCapture::NativeMode()
        ? std::format("{}/frame_{}_{}.txt", dir ? dir : ".",
                      static_cast<long long>(std::time(nullptr)), native_frame)
        : std::format("{}/frame_{}.txt", dir ? dir : ".", static_cast<long long>(std::time(nullptr)));
    FILE* f = std::fopen(path.c_str(), "w");
    if (!f) {
        std::printf("Frame capture: cannot write %s\n", path.c_str());
        return;
    }
    std::fprintf(f, "presented buffer %#llx\n", static_cast<unsigned long long>(presented));
    u32 index = 0;
    for (const auto& e : entries) {
        if (e.compute) {
            std::fprintf(f, "\n#%u COMPUTE dispatches %u, cs", index++, e.draws);
        } else {
            std::fprintf(f, "\n#%u PASS draws %u (indices %llu), vs/ps", index++, e.draws,
                         static_cast<unsigned long long>(e.indices));
        }
        for (const u64 h : e.shaders) {
            std::fprintf(f, " %016llx", static_cast<unsigned long long>(h));
        }
        std::fprintf(f, "\n");
        for (size_t i = 0; i < e.colors.size(); ++i) {
            std::fprintf(f, "  color%zu %s\n", i, Describe(e.colors[i]).c_str());
        }
        if (!e.compute) {
            std::fprintf(f, "  depth  %s\n", Describe(e.depth).c_str());
        }
        for (const auto& [t, storage] : e.sampled) {
            std::fprintf(f, "  %s %s\n", storage ? "writes " : "samples", Describe(t).c_str());
        }
        if (!e.note.empty()) {
            std::fprintf(f, "  note: %s\n", e.note.c_str());
        }
        for (const auto& b : e.buffers) {
            std::fprintf(f, "%s\n", b.c_str());
        }
    }
    {
        std::scoped_lock lock{native_mutex};
        texture_trace_batch.Flush(native_lines,native_bytes,native_truncated);
        std::fprintf(f,"NATIVE {\"op\":\"frame\",\"frame\":%llu,\"truncated\":%s,\"bytes\":%zu,\"series_count\":%u,\"series_member\":%u}\n",
            (unsigned long long)native_frame,native_truncated?"true":"false",native_bytes,
            native_series_count,native_series_count-native_remaining+1);
        for(const auto& line:native_lines)std::fprintf(f,"NATIVE %s\n",line.c_str());
    }
    std::fclose(f);
    if (FrameCapture::NativeMode()) std::printf("Frame capture: native series member %llu\n", (unsigned long long)native_frame);
    std::printf("Frame capture: %zu passes written to %s\n", entries.size(), path.c_str());
}

} // namespace

void FrameCapture::OnFlip(VAddr presented_address) {
    last_presented.store(presented_address, std::memory_order_relaxed);
    flips.fetch_add(1, std::memory_order_release);
    static const std::string trigger_path=[] {
        if(const char* env=std::getenv("BB_CAPTURE_TRIGGER"))return std::string(env);
        return TextureDiagnosticDir().empty()?std::string{}:TextureDiagnosticDir()+"/trigger";
    }();
    const char* trigger=trigger_path.empty()?nullptr:trigger_path.c_str();
    if (trigger && state.load(std::memory_order_relaxed) == Idle &&
        std::filesystem::exists(trigger)) {
        std::error_code ec;
        std::filesystem::remove(trigger, ec);
        state.store(Armed, std::memory_order_release);
        std::printf("Frame capture: armed\n");
    }
}

void FrameCapture::Poll() {}

bool FrameCapture::NativeMode() {
    static const bool enabled=[] {const char* v=std::getenv("BB_CAPTURE_NATIVE");return (v&&v[0]=='1')||!TextureDiagnosticDir().empty();}();
    return enabled;
}
bool FrameCapture::NativeRecording() {return NativeMode()&&state.load(std::memory_order_relaxed)==Recording;}
const std::string& FrameCapture::TextureDiagnosticDirectory() {return TextureDiagnosticDir();}
bool FrameCapture::TextureBindingsEnabled() {
    static const bool enabled=[] {const char* v=std::getenv("BB_CAPTURE_TEXTURE_BINDINGS");return v&&std::strcmp(v,"1")==0;}();
    return NativeMode() && enabled;
}
void FrameCapture::Native(std::string line) {
    if(!NativeRecording())return;
    // Texture diagnostics need selected bindings, sets and draw association only.
    // Keep the dependency/attachment audit streams for normal native captures.
    if(TextureBindingsEnabled()&&(!line.starts_with("{\"op\":\"texture_")||line.starts_with("{\"op\":\"texture_lifetime\"")))return;
    std::scoped_lock lock{native_mutex};
    if(!NativeRecording())return; // a concurrent CPU trap may cross the closing boundary
    if(TextureBindingsEnabled())texture_trace_batch.Add(native_lines,native_bytes,native_truncated,std::move(line));
    else BbTextureTraceBatch::Append(native_lines,native_bytes,native_truncated,std::move(line));
}
void FrameCapture::NativeLabel(const char* text, u64 queue) {
    if(!NativeRecording())return;
    std::string escaped;
    for(unsigned char c:std::string_view(text?text:"")) {
        if(c=='"'||c=='\\'){escaped+='\\';escaped+=char(c);}
        else if(c<32){char out[7];std::snprintf(out,sizeof(out),"\\u%04x",c);escaped+=out;}
        else escaped+=char(c);
        if(escaped.size()>512)break;
    }
    Native(std::format("{{\"op\":\"label\",\"queue\":{},\"text\":\"{}\"}}",queue,escaped));
}
u64 FrameCapture::NativeCaptureSequence() {
    return NativeRecording()?native_capture_sequence.load(std::memory_order_relaxed):0;
}
void FrameCapture::NativeWriteOrigin(VAddr address, u64 size, WriteOrigin origin,
                                    u32 constant_offset, const void* packet_address) {
    if(!NativeRecording() || size!=192)return;
    const char* packet=nullptr;
    switch(origin) {
    case WriteOrigin::GraphicsWriteData:packet="graphics_write_data";break;
    case WriteOrigin::ComputeWriteData:packet="compute_write_data";break;
    case WriteOrigin::ConstantRam:packet="dump_const_ram";break;
    default:return;
    }
    const char* phase=origin==WriteOrigin::GraphicsWriteData ? "graphics_write_execution" :
        origin==WriteOrigin::ComputeWriteData ? "compute_decode" : "constant_engine_decode";
    Native(std::format("{{\"op\":\"command_write_origin\",\"order\":\"{}\",\"address\":{},\"size\":192,\"packet\":\"{}\",\"constant_offset\":{},\"packet_address\":{}}}",
        phase,address,packet,origin==WriteOrigin::ConstantRam ? std::to_string(constant_offset) : "null",
        packet_address ? std::to_string(reinterpret_cast<uintptr_t>(packet_address)) : "null"));
}
void FrameCapture::NativeTaskPacket(bool copied, u64 queue, u64 position,
                                   const void* source, const void* payload, u32 size) {
    if(!NativeRecording() || size!=208)return;
    Native(std::format("{{\"op\":\"ordered_task_{}\",\"queue\":{},\"position\":{},\"source\":{},\"payload\":{},\"bytes\":208}}",
        copied ? "copy" : "execute",queue,position,
        source ? std::to_string(reinterpret_cast<uintptr_t>(source)) : "null",
        reinterpret_cast<uintptr_t>(payload)));
}
void FrameCapture::NativeCommandSnapshot(u64 snapshot, u64 bytes, u64 guest) {
    if (!NativeRecording() || !bytes) return;
    constexpr u64 Max = std::numeric_limits<u64>::max();
    const bool valid = snapshot && guest && bytes <= Max - snapshot && bytes <= Max - guest;
    static std::atomic<u64> sequence{};
    Native(std::format("{{\"op\":\"command_snapshot\",\"sequence\":{},\"snapshot\":{},\"bytes\":{},\"guest\":{},\"valid\":{}}}",
        ++sequence, snapshot, bytes, guest, valid));
}
void FrameCapture::NativeSubmittedPacket(bool copied, u64 packet, u32 bytes, u64 begin,
                                        u64 range_bytes, u64 guest_begin, u64 submission) {
    if(!NativeRecording() || bytes!=208)return;
    constexpr u64 Max=std::numeric_limits<u64>::max();
    const bool valid=packet && begin && guest_begin && range_bytes>=bytes &&
        range_bytes<=Max-begin && packet>=begin && packet-begin<=range_bytes-bytes &&
        packet-begin<=Max-guest_begin && bytes<=Max-(guest_begin+(packet-begin));
    Native(std::format("{{\"op\":\"submitted_packet\",\"decoder_packet\":{},\"bytes\":208,\"copied\":{},\"submission\":{},\"mapping_valid\":{},\"guest_packet\":{},\"snapshot_begin\":{},\"range_bytes\":{}}}",
        packet,copied,submission,valid,valid ? std::to_string(guest_begin+(packet-begin)) : "null",begin,range_bytes));
}
void FrameCapture::NativeCommandWrite(VAddr address, const void* data, u64 size,
                                      u64 resource, u64 native, u64 offset, u64 generation) {
    if(!NativeRecording() || size!=192 || !data)return;
    std::scoped_lock lock{dependency_mutex};
    if(!NativeRecording() || !dependency_audit)return;
    auto& a=*dependency_audit;
    auto* slot=static_cast<DependencyAudit::Watch*>(nullptr);
    for(auto& w:a.watches)if(w.active && w.address==address){slot=&w;break;}
    if(!slot)for(auto& w:a.watches)if(!w.active){slot=&w;break;}
    if(!slot) {
        if(!a.overflow)Native("{\"op\":\"dependency_audit_overflow\",\"kind\":\"watch_slots\"}");
        a.overflow=true;return;
    }
    auto& w=*slot;const bool previous=w.has_payload;
    const bool same=previous && !std::memcmp(w.payload.data(),data,size);
    const bool same_generation=previous && generation && generation==w.generation;
    w.address=address;w.resource=resource;w.native=native;w.offset=offset;w.generation=generation;
    std::memcpy(w.payload.data(),data,size);++w.version;w.active=w.has_payload=true;
    unsigned zero_words=0, float_one_words=0;
    for(unsigned i=0;i<48;++i){u32 word;std::memcpy(&word,w.payload.data()+4*i,4);zero_words+=word==0;float_one_words+=word==0x3f800000u;}
    Native(std::format("{{\"op\":\"command_write\",\"seq\":{},\"address\":{},\"size\":192,\"resource\":{},\"native\":{},\"offset\":{},\"generation\":{},\"version\":{},\"previous\":{},\"same_payload\":{},\"same_generation\":{},\"zero_words\":{},\"float_one_words\":{},\"payload_address\":{}}}",
        ++a.sequence,address,resource,native,offset,generation,w.version,previous,same,same_generation,zero_words,float_one_words,reinterpret_cast<uintptr_t>(data)));
}
void FrameCapture::NativeBufferUse(u64 resource, u64 native, u64 offset, u64 size,
                                   VAddr address, u32 stage, u64 shader, bool write,
                                   const char* source, u64 caller_stage) {
    if(!NativeRecording())return;
    std::scoped_lock lock{dependency_mutex};
    if(!NativeRecording() || !dependency_audit)return;
    auto& a=*dependency_audit;const auto seq=++a.sequence;
    if(!write)for(size_t i=0;i<command_read_names.size();++i)
        if(std::string_view(source)==command_read_names[i]){++a.command_read_sources[i];break;}
    // Record the precise caller stage before shared-core AllGraphics aggregation. This is
    // a logical stage, not proof of how geometry/tessellation was lowered in Metal.
    if(++a.uses<=1024)
        Native(std::format("{{\"op\":\"buffer_use\",\"seq\":{},\"resource\":{},\"native\":{},\"offset\":{},\"size\":{},\"address\":{},\"logical_stage\":{},\"shader\":{},\"write\":{},\"source\":\"{}\",\"caller_stage\":{}}}",seq,resource,native,offset,size,address,stage,shader,write,source,caller_stage));
    else if(a.uses==1025)Native("{\"op\":\"dependency_audit_overflow\",\"kind\":\"general_uses\"}");
    for(const auto& w:a.watches) {
        if(!w.active)continue;
        const bool unknown=!resource && !address;
        const bool hit=unknown || (address && DependencyAudit::Overlap(address,size,w.address,192)) ||
            (native && native==w.native && DependencyAudit::Overlap(offset,size,w.offset,192));
        if(hit)Native(std::format("{{\"op\":\"watch_access\",\"seq\":{},\"watch\":{},\"version\":{},\"logical_stage\":{},\"shader\":{},\"write\":{},\"unknown\":{},\"source\":\"{}\",\"caller_stage\":{}}}",seq,w.address,w.version,stage,shader,write,unknown,source,caller_stage));
    }
}
void FrameCapture::NativeCpuAccess(VAddr address, u64 size, bool write) {
    if(!NativeRecording())return;
    std::scoped_lock lock{dependency_mutex};
    if(!NativeRecording() || !dependency_audit)return;
    auto& a=*dependency_audit;
    for(const auto& w:a.watches)if(w.active && DependencyAudit::Overlap(address,size,w.address,192))
        Native(std::format("{{\"op\":\"watch_cpu\",\"seq\":{},\"watch\":{},\"version\":{},\"address\":{},\"size\":{},\"write\":{}}}",++a.sequence,w.address,w.version,address,size,write));
}
void FrameCapture::NativeSrtWalk(bool covered,u32 encoded_reads) {
    if(!NativeRecording())return;
    std::scoped_lock lock{dependency_mutex};
    if(!NativeRecording() || !dependency_audit)return;
    auto& a=*dependency_audit;++a.srt_calls;
    if(covered){++a.srt_covered;a.srt_encoded_reads+=encoded_reads;}
}

void FrameCapture::SetNativeBoundaryDrain(void* owner, void (*drain)(void*)) {
    native_boundary_owner=owner;native_boundary_drain=drain;
}
void FrameCapture::ClearNativeBoundaryDrain(void* owner) {
    if(native_boundary_owner==owner){native_boundary_owner=nullptr;native_boundary_drain=nullptr;}
}

void FrameCapture::FrameBoundary(u64 frame) {
    if(!NativeMode())return;
    const auto current=state.load(std::memory_order_relaxed);
    if(current!=Idle && native_boundary_drain) {
        if(current==Recording) Native(std::format("{{\"op\":\"capture_drain_begin\",\"boundary_frame\":{}}}",frame));
        native_boundary_drain(native_boundary_owner);
        if(current==Recording) Native(std::format("{{\"op\":\"capture_drain_end\",\"boundary_frame\":{}}}",frame));
    }
    if(current==Recording){
        RecordCommandReadCounts();
        state.store(Idle,std::memory_order_release);
        Write(last_presented.load(std::memory_order_relaxed));
        ResetDependencyAudit(false);
        entries.clear();pending_sampled.clear();pending_buffers.clear();pass_open=false;
        {std::scoped_lock lock{native_mutex};native_lines.clear();native_lines.shrink_to_fit();texture_trace_batch.Reset();}
        if(native_remaining<=1)return;
        --native_remaining;
        // Re-arm on this same encoding boundary, before the next pass starts. There
        // is no host trigger/poll gap. Metadata remains bounded separately per frame.
        ResetDependencyAudit(true);
        {std::scoped_lock lock{native_mutex};texture_trace_batch.Reset();native_bytes=0;native_truncated=false;native_frame=frame;}
        native_capture_sequence.fetch_add(1,std::memory_order_relaxed);
        state.store(Recording,std::memory_order_release);
        if(TextureBindingsEnabled())Native("{\"op\":\"texture_capture\",\"schema\":1,\"keep_set_memo\":true,\"keep_render_memo\":true,\"keep_vertex_memo\":true,\"keep_bind_helper\":true,\"contents_read\":false}");
        return;
    }
    if(current==Armed){
        const char* count=std::getenv("BB_CAPTURE_CONSECUTIVE_COUNT");char* end=nullptr;
        const unsigned long requested=count?std::strtoul(count,&end,10):1;
        native_series_count=native_remaining=count && end && !*end && requested>=1 && requested<=3
            ? u32(requested) : 1;
        if(native_series_count>1)std::printf("Frame capture: consecutive chain %u\n",native_series_count);
        ResetDependencyAudit(true);
        entries.clear();pending_sampled.clear();pending_buffers.clear();pass_open=false;
        {std::scoped_lock lock{native_mutex};native_lines.clear();texture_trace_batch.Reset();native_bytes=0;native_truncated=false;native_frame=frame;}
        native_capture_sequence.fetch_add(1,std::memory_order_relaxed);
        state.store(Recording,std::memory_order_release);
        if(TextureBindingsEnabled())Native("{\"op\":\"texture_capture\",\"schema\":1,\"keep_set_memo\":true,\"keep_render_memo\":true,\"keep_vertex_memo\":true,\"keep_bind_helper\":true,\"contents_read\":false}");
    }
}

bool FrameCapture::IsDisplayBuffer(VAddr address) {
    const u32 count = display_count.load(std::memory_order_acquire);
    for (u32 i = 0; i < count; ++i) {
        if (display_list[i].load(std::memory_order_relaxed) == address) {
            return true;
        }
    }
    if (count < display_list.size()) {
        return false;
    }
    std::scoped_lock lk{display_mutex};
    return std::ranges::find(display_buffers, address) != display_buffers.end();
}

void FrameCapture::AddDisplayBuffer(VAddr address) {
    std::scoped_lock lk{display_mutex};
    if (std::ranges::find(display_buffers, address) == display_buffers.end()) {
        display_buffers.push_back(address);
        const u32 count = display_count.load(std::memory_order_relaxed);
        if (count < display_list.size()) {
            display_list[count].store(address, std::memory_order_relaxed);
            display_count.store(count + 1, std::memory_order_release);
        }
    }
}

void FrameCapture::BeginPass(const VideoCore::ImageInfo* const* colors, u32 num_colors,
                             const VideoCore::ImageInfo* depth) {
    std::vector<Target> targets;
    bool display = false;
    {
        std::scoped_lock lk{display_mutex};
        for (u32 i = 0; i < num_colors; ++i) {
            targets.push_back(colors[i] ? ToTarget(*colors[i]) : Target{});
            display |= colors[i] && std::ranges::find(display_buffers, colors[i]->guest_address) !=
                                        display_buffers.end();
        }
    }
    // Frame boundaries in the command stream: the pass that writes a display buffer.
    const bool new_pass = !(pass_open && !entries.empty() && !entries.back().compute &&
                            entries.back().colors == targets);
    if (!NativeMode() && display && new_pass) {
        if (state.load(std::memory_order_relaxed) == Recording) {
            Write(last_presented.load(std::memory_order_relaxed));
            entries.clear();
            pending_sampled.clear();
            pending_buffers.clear();
            state.store(Idle, std::memory_order_release);
            return;
        }
        entries.clear();
        pending_sampled.clear();
        pending_buffers.clear();
        pass_open = false;
        state.store(Recording, std::memory_order_release);
    }
    if (state.load(std::memory_order_relaxed) != Recording) {
        pending_sampled.clear();
        pending_buffers.clear();
        return;
    }
    const Target d = depth ? ToTarget(*depth) : Target{};
    if (pass_open && !entries.empty() && !entries.back().compute &&
        entries.back().colors == targets && entries.back().depth == d) {
        return;
    }
    entries.push_back({.compute = false, .colors = std::move(targets), .depth = d});
    pass_open = true;
}

void FrameCapture::Draw(u64 vs_hash, u64 ps_hash, u32 num_indices, u32 num_instances) {
    if (state.load(std::memory_order_relaxed) != Recording || entries.empty() ||
        entries.back().compute) {
        return;
    }
    auto& e = entries.back();
    ++e.draws;
    e.indices += u64(num_indices) * std::max(num_instances, 1u);
    AddShader(e, vs_hash);
    AddShader(e, ps_hash);
    AddSampled(e);
    AddBuffers(e);
}

void FrameCapture::Dispatch(u64 cs_hash, u32 x, u32 y, u32 z) {
    if (state.load(std::memory_order_relaxed) != Recording) {
        pending_sampled.clear();
        pending_buffers.clear();
        return;
    }
    if (!entries.empty() && entries.back().compute && !entries.back().shaders.empty() &&
        entries.back().shaders[0] == cs_hash) {
        ++entries.back().draws;
    } else {
        entries.push_back({.compute = true, .draws = 1, .shaders = {cs_hash}});
        entries.back().note = std::format("groups {}x{}x{}", x, y, z);
    }
    AddSampled(entries.back());
    AddBuffers(entries.back());
    pass_open = false;
}

void FrameCapture::Buffer(u64 stage_hash, u32 slot, VAddr address, const void* data, u64 size) {
    if(NativeMode())return; // metadata mode never reads buffer contents
    const u64 bytes = std::min<u64>(size, 1024) & ~u64(3);
    std::string out = std::format("  buffer stage {:016x} slot {} at {:#x} size {}:", stage_hash,
                                  slot, address, size);
    const auto* words = static_cast<const float*>(data);
    for (u64 i = 0; i < bytes / 4; ++i) {
        if (i % 8 == 0) {
            out += std::format("\n    [{:3}]", i);
        }
        out += std::format(" {:12.6g}", words[i]);
    }
    pending_buffers.push_back(std::move(out));
}

void FrameCapture::Sampled(const VideoCore::ImageInfo& info, bool storage) {
    if (pending_sampled.size() < 64) {
        pending_sampled.emplace_back(ToTarget(info), storage);
    }
}

void FrameCapture::Note(const char* text) {
    if (!entries.empty()) {
        entries.back().note += text;
    }
}

} // namespace VideoCore
