// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_companion_mailbox.h"
namespace BbFetchCapture {
struct BinaryProof {BbFetchStore::Digest sha{};size_t words{};bool capture_attempted{};};
struct CompileOutput {std::optional<BbFetchCompanion::Pending>pending;BinaryProof binary;};
}
