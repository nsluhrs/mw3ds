// Force-included into every engine source (the Makefile's -include): every fopen and stat the game makes on a
// data path is weighed against the card's data archive (platform/dataarchive.cpp), and everything that isn't
// an archive hit keeps the real open or the real card. The macros are function like (fopen(p, m)) so the
// <cstdio> and <sys/stat.h> declarations other headers pull in keep their own name; dataarchive.cpp itself
// pushes the macros aside to reach the real stdio. C and asm units (extern/cjson, startup) keep the plain
// stdio: they never open data files, and this header is C++ only (a gcc unit has no <cstdio> on its path).
#pragma once
#ifdef __cplusplus
#ifndef _GNU_SOURCE
#define _GNU_SOURCE        // (before the first libc header: engine files such as zfile.cpp ask for newlib's
#endif                     //  fopencookie and friends, and a header included ahead of their own define hides it)
#include <cstdio>
#include <sys/stat.h>

FILE* archFopen(const char* path, const char* mode);
int archFclose(FILE* f);
int archStat(const char* path, struct stat* st);

#define fopen(p, m) archFopen(p, m)
#define fclose(f) archFclose(f)
#define stat(p, s) archStat(p, s)
#endif
