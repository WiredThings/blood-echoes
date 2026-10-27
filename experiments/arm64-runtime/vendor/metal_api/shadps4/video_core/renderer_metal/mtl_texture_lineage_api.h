// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
namespace VideoCore {struct Image;class Buffer;}
namespace Metal {
// No-op without an explicitly armed private lineage session. No guest reads.
void TraceTextureUploadBuffer(const VideoCore::Image&,const VideoCore::Buffer*,
                             std::uint64_t offset,const char* phase);
void TraceTextureRefreshDecision(const VideoCore::Image&);
}
