/* Stand-in for the recompiled library's build without an SDK (build_library.py): asserts compile
 * out of the generated code. */
#pragma once
#define assert(x) ((void)0)
#define static_assert _Static_assert
