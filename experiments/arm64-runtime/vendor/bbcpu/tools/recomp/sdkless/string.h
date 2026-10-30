/* Stand-in for the recompiled library's build without an SDK (build_library.py): the two functions
 * the generated code calls; the game process provides them (libSystem). */
#pragma once
#include <stddef.h>
void *memcpy(void *restrict dst, const void *restrict src, size_t n);
void *memset(void *dst, int c, size_t n);
