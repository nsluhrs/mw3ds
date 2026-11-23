// The card's fast path for the converted set: a few big files instead of 100000 small ones.
//
// An SD card pays for every file it opens: a FAT lookup, a directory walk, a chain read. The loose folder the
// staging script used to write hands the game 84000 of those per installed set, and the streaming thread
// falls behind: cells arrive late and frames hitch. The packed form (tools/build/pack_sdmc.py) lays every
// data file whole into one of a few big files, under 2 GB each because devkitARM's stdio positions are a
// 32-bit long; this reader keeps their names, so the engine asks for the same paths and gets a seek into a
// sequential file instead. It speaks at the fopen and stat level: the Makefile force-includes datastdio.hpp,
// every fopen and stat the game makes comes through here, data paths hit the archive and everything else
// (saves, the log, reports) goes to the card as it always did. The card keeps working without an archive:
// with no data.idx the load never arms and every call falls through to the real stdio.
//
// Reads come from a pool of handles opened on the parts at boot, so an entry's FILE* sits at its own offset
// while the streaming thread reads the next cell and the main thread a texture: borrowing is the only lock
// taken, and no data read ever opens the part file again. libctru's dup is not something to lean on: the pool
// opens the parts kPool times on purpose.
#pragma once
#include <cstdio>

// Try to arm the archive under dir (dir/data.idx plus dir/data-<n>.bin): true when this data folder is packed.
// Called once, where the engine picks its data folder.
bool dataArchiveTryLoad(const char* dir);

// The open, close and stat below the redirect, for platform/datastdio.hpp's macros (the console) and the
// native build's stubs: the archive answers a data path, everything else goes to the real thing.
FILE* archFopen(const char* path, const char* mode);
int archFclose(FILE* f);
struct stat;
int archStat(const char* path, struct stat* st);
