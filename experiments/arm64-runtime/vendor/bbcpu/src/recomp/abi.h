/* Shared by the actual generator, generated library and CPU loader. */
#ifndef BB_RECOMP_ABI_H
#define BB_RECOMP_ABI_H
#ifdef BBCPU_NATIVE_TSC
/* Blood Echoes uses the same hardware-counter domain as its HLE and Metal. */
#define BB_RECOMP_API_VERSION 5u
#else
#define BB_RECOMP_API_VERSION 4u
#endif
#define BB_RECOMP_INIT "bb_recomp_init"
#endif
