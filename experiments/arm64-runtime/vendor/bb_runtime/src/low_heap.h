/* macOS: the runtime's C heap calls go to the low heap (src/low_heap.c), so pointers the
 * guest receives stay below 1 TiB. Included by runtime.h for C files only. */
#ifndef BB_LOW_HEAP_H
#define BB_LOW_HEAP_H
#include <stddef.h>
/* Shared description of this allocator, not the host C++ max_align_t. Small
 * blocks have power-of-two size/alignment; large runs are chunk-aligned. */
enum { BB_LOW_HEAP_MIN_SHIFT=4, BB_LOW_HEAP_CHUNK_SHIFT=20 };
static inline size_t bb_low_allocation_alignment(size_t size) {
    size_t alignment=(size_t)1<<BB_LOW_HEAP_MIN_SHIFT;
    const size_t chunk=(size_t)1<<BB_LOW_HEAP_CHUNK_SHIFT;
    while (alignment<size && alignment<chunk) alignment<<=1;
    return alignment;
}
#if defined(__APPLE__) && !defined(__cplusplus) && !defined(BB_LOW_HEAP_IMPL)
#include <stddef.h>
void *bb_low_malloc(size_t size);
void *bb_low_calloc(size_t count, size_t size);
void *bb_low_realloc(void *p, size_t size);
void *bb_low_aligned_alloc(size_t alignment, size_t size);
void bb_low_free(void *p);
char *bb_low_strdup(const char *s);
int bb_low_posix_memalign(void **out, size_t alignment, size_t size);
/* Function-like, so attribute spellings such as __attribute__((malloc)) in system and SDL
 * headers stay untouched; nothing in the runtime passes these functions as values. */
#define malloc(size) bb_low_malloc(size)
#define calloc(count, size) bb_low_calloc(count, size)
#define realloc(p, size) bb_low_realloc(p, size)
#define aligned_alloc(alignment, size) bb_low_aligned_alloc(alignment, size)
#define free(p) bb_low_free(p)
#define strdup(s) bb_low_strdup(s)
#define posix_memalign(out, alignment, size) bb_low_posix_memalign(out, alignment, size)
#endif
#endif
