// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <span>
#include <utility>

#include "common/types.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/cache_backend.h"

namespace VideoCore {

struct ImageInfo;
struct Image;

/// A mip as the tiling kernel (host_shaders/tiling.comp) gets it. The linear image of the mip is
/// `slices` slices of `height` rows of `pitch` texels (blocks), one after the other from `offset`
/// (the logical size of the image: the rows the linear copies of the texture cache hold). The
/// tiled surface in the guest memory (`size` bytes from `offset`) is laid out padded: slices of
/// `padded_height` rows of `padded_pitch` texels, the pitch a multiple of the macro tile pitch and
/// the height of the macro tile height (micro tiled mips: 8). The padding holds no image data:
/// the kernel never reads or writes the tiled bytes of texels outside the logical size. `slices`
/// is the number of whole padded slices in `size` (a depth buffer of 720 rows in 768: 1 slice).
struct TilingMip {
    u32 size;
    u32 pitch;
    u32 height;
    u32 offset;
    u32 padded_pitch;
    u32 padded_height;
    u32 slices;
};

/// The uniform buffer of the tiling kernel.
struct TilingInfo {
    u32 bank_swizzle;
    u32 micro_tiled_mips;
    u32 num_mips;
    std::array<TilingMip, 16> mips;
};

class TileManager {
public:
    explicit TileManager(CacheBackend& backend, StreamBuffer& stream_buffer);
    ~TileManager();

    void TileImage(Image& in_image, std::span<vk::BufferImageCopy> buffer_copies,
                   const VideoCore::Buffer* out_buffer, u64 out_offset);

    std::pair<const Buffer*, u64> DetileImage(const VideoCore::Buffer* in_buffer, u64 in_offset,
                                              const ImageInfo& info);

    // No raw write until token.Record(). At most one attempt per scheduler tick.
    std::unique_ptr<PreparedExactImageExport> PrepareExactImageExport(
        Image& image, const Buffer* existing_raw, u64 raw_offset,
        const std::function<void()>& release_input_lease = {});

    /// The first `num_mips` mips of `info` as the kernel gets them.
    static TilingInfo MakeTilingInfo(const ImageInfo& info, u32 num_mips);

    /// The dispatch of the kernel for `info` over the given buffers (the backend runs it).
    static TilingDispatch MakeDispatch(const ImageInfo& info, bool is_tiler,
                                       const KernelBuffer& tiled, const KernelBuffer& linear,
                                       const KernelBuffer& params);

private:
    CacheBackend& backend;
    StreamBuffer& stream_buffer;
};

} // namespace VideoCore
