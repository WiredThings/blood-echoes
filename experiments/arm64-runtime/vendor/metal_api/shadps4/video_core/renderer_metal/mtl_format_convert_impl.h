// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#ifndef __OBJC__
#error "mtl_format_convert_impl.h is for Objective-C++ (.mm) files only"
#endif

#import <Metal/Metal.h>

#include "video_core/renderer_metal/mtl_format_convert.h"

namespace Metal { class HostKernels; }

namespace Metal::FormatConvert {

/// The kernels that rewrite texel bytes between a guest layout and the layout of a Metal blit
/// (see mtl_format_convert.h), one dispatch per copy. Pipelines are made on first use and live as
/// long as the converter: nothing retains a pipeline for the GPU (unretained command buffers), so
/// the owner releases it only after the GPU work is finished (Runtime's destructor finishes).
class Converter {
public:
    // Runtime::Impl owns kernels before converter, and finishes the scheduler
    // before either is destroyed. Keep the existing two pipeline owner slots.
    explicit Converter(HostKernels& kernels) : kernels{kernels} {}

    /// One copy's worth of texels: `width` x `height` texels in each of `slices` slices (array
    /// layers or depth slices).
    struct Region {
        u32 width, height, slices;
    };
    /// The guest side: the bytes at `offset` of `buffer`, rows and slices `row_pitch` and
    /// `slice_pitch` bytes apart (bufferRowLength and bufferImageHeight of the copy).
    struct GuestSide {
        id<MTLBuffer> buffer;
        u64 offset;
        u32 row_pitch, slice_pitch;
    };
    /// The host side: tightly packed rows and slices of `Conversion::host_texel_bytes` texels at
    /// `offset` of `buffer`.
    struct HostSide {
        id<MTLBuffer> buffer;
        u64 offset;
    };

    /// Records a dispatch on the open compute encoder `compute`. `to_host`: guest bytes -> host
    /// layout (an upload), else host layout -> guest bytes (a download; only the bytes of the
    /// texels are written, not the padding of rows and slices).
    void Encode(id<MTLComputeCommandEncoder> compute, const Conversion& conversion, bool to_host,
                const GuestSide& guest, const HostSide& host, const Region& region);

    /// Bytes the host side of `conversion` takes for `region`.
    static u64 HostBytes(const Conversion& conversion, const Region& region);

private:
    id<MTLComputePipelineState> Pipeline(Kind kind);

    HostKernels& kernels;
    id<MTLComputePipelineState> rgb = nil;
    id<MTLComputePipelineState> depth = nil;
};

} // namespace Metal::FormatConvert
