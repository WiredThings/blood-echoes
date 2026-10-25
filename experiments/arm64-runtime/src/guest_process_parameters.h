// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <stdexcept>
#include <string_view>

namespace BbGuestProcess {
// Existing guest parameter page: argc/argv/env, then argv[0], then the DATA
// symbol __progname (a writable guest pointer, never a native char**).
inline constexpr std::string_view Executable = "/app0/eboot.bin";
inline constexpr size_t PathOffset = 64, NameSlotOffset = 128;
inline constexpr size_t RequiredBytes = NameSlotOffset + sizeof(uint64_t);
inline bool IsProgramName(std::string_view nid, unsigned kind, uint64_t caps) {
 return kind == 2 && (caps & 1) && nid == "djxxOmW6-aw#p#J";
}
inline uintptr_t Initialize(std::span<std::byte> bytes, uintptr_t guest_base,
                            std::string_view path = Executable) {
 if (!guest_base || bytes.size() < RequiredBytes || path.empty() || path.size() >= NameSlotOffset-PathOffset ||
     path.find('\0') != path.npos || guest_base > std::numeric_limits<uintptr_t>::max()-RequiredBytes)
  throw std::runtime_error("invalid guest process parameters");
 const uint64_t args[] = {1, guest_base+PathOffset, 0, 0};
 const uint64_t name = guest_base+PathOffset+
     (path.find_last_of('/') == path.npos ? 0 : path.find_last_of('/')+1);
 std::memcpy(bytes.data(), args, sizeof(args));
 std::memcpy(bytes.data()+PathOffset, path.data(), path.size());
 bytes[PathOffset+path.size()] = std::byte{0};
 std::memcpy(bytes.data()+NameSlotOffset, &name, sizeof(name));
 return guest_base+NameSlotOffset;
}
}
