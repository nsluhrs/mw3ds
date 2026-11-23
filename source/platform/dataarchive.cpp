// The body for platform/dataarchive.hpp: the fopen- and stat-level redirect to the card's data archive.
//
// The force-included datastdio.hpp has swapped fopen, fclose and stat for the arch* functions below in every
// engine file, this one included; the push_macro dance is how this reader still reaches the real stdio for the
// parts, the index and every path that isn't the data folder.
//
// An open of a packed name hands back a cookie stream, the same trick zfile.cpp uses for compressed files: the
// callbacks below get the entry's own coordinates and translate them to the part file. So a caller's
// fseek(f, 0, SEEK_SET), its fseek(f, -4, SEEK_END) and its ftell see the file it asked for rather than the
// gigabyte image holding it, and no reader anywhere in the engine had to change. The stream is unbuffered and
// the part handle it reads through is buffered wide: the caller's bytes come off the card 64 KB at a time,
// which is what an SD card wants.
//
// Each part is opened kPool times at load and an open borrows one of those handles exclusively for as long as
// its stream stays open: the streaming thread can walk the next cell while the main thread pulls a texture, and
// no data read ever opens the part file again. When all of a part's handles are borrowed, an open takes one of
// its own and gives it back on fclose, so a burst of readers costs an open, not a stall.
#include "platform/dataarchive.hpp"
#include "components/debug/debuglog.hpp"
#include <3ds.h>
#include <sys/stat.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#pragma push_macro("fopen")
#pragma push_macro("fclose")
#pragma push_macro("stat")
#undef fopen
#undef fclose
#undef stat

// The opens below the redirect: the console's libc, and on the PC build the stub's sdmc:/ mapper (which must
// still run for the part files, the index and everything the archive doesn't hold). Declared at file scope:
// the mapper lives in another object (tools/test/native/native_stubs.cpp).
#ifdef __3DS__
static FILE* realOpen(const char* p, const char* m) { return fopen(p, m); }
static int realStat(const char* p, struct stat* st) { return stat(p, st); }
#else
FILE* native_realOpen(const char* p, const char* m);
int native_realStat(const char* p, struct stat* st);
static FILE* realOpen(const char* p, const char* m) { return native_realOpen(p, m); }
static int realStat(const char* p, struct stat* st) { return native_realStat(p, st); }
#endif
static int realClose(FILE* f) { return fclose(f); }

// A packed entry reaches its reader through a cookie stream. The console's newlib and the Linux test build's
// libc both have fopencookie (zfile.cpp:194 has used it on the console all along); MSYS2's doesn't, and a PC
// checkout reads a loose folder, so that build answers every data path with the plain card.
#if defined(__3DS__) || (defined(__linux__) && !defined(_WIN32))
#define MW3DS_ARCHIVE 1
// The seek callback's offset must be exactly the type this libc's cookie_io_functions_t wants: the console's
// newlib says a 32-bit long, a Linux glibc a 64-bit one, and the feature macros that decide it differ per
// toolchain. Read it off the struct itself instead of guessing it from an ifdef.
template <class F> struct CookieSeekOff { typedef long type; };
template <class O> struct CookieSeekOff<int (*)(void *, O *, int)> { typedef O type; };
typedef CookieSeekOff<decltype(cookie_io_functions_t::seek)>::type ArchOff;
#else
#define MW3DS_ARCHIVE 0
#endif

#if MW3DS_ARCHIVE

namespace
{
constexpr const char* kIdxName = "data.idx";
constexpr int kPool = 6;                     // handles opened on each part at load
constexpr size_t kPartBuffer = 1 << 16;      // the card reads behind a borrowed handle come in 64 KB

struct Cookie                                // one open entry, handed to the callbacks by stdio
{
    FILE* part;                              // the borrowed handle, its position kept in step with pos
    char* buf;                               // ... and the buffer we gave it, if this open made one
    uint64_t base, size, pos;
    int slot;                                // the pool slot to hand back, -1 for a handle of our own
};

std::vector<char> g_names;                   // the table's names, back to back and NUL-terminated, sorted
std::vector<uint32_t> g_nameAt;              // each name's offset into g_names
std::vector<uint32_t> g_part;
std::vector<uint64_t> g_off;
std::vector<uint32_t> g_size;
std::string g_prefix;                        // the data folder these names hang off, empty until armed
std::vector<std::vector<FILE*>> g_pool;      // the borrowed handles, kPool a part
std::vector<char> g_busy;                    // ... and which are taken
LightLock g_lock = 1;                        // unlocked (LightLock_Init sets 1)

bool find(const std::string& name, size_t* out)
{
    size_t lo = 0, hi = g_nameAt.size();
    while (lo < hi)
    {
        size_t mid = (lo + hi) / 2;
        int c = strcmp(g_names.data() + g_nameAt[mid], name.c_str());
        if (c == 0) { *out = mid; return true; }
        if (c < 0) lo = mid + 1;
        else hi = mid;
    }
    return false;
}

bool underPrefix(const char* path, const char** relOut)
{
    if (g_prefix.empty())
        return false;
    size_t n = g_prefix.size();
    if (strncmp(path, g_prefix.c_str(), n) != 0 || path[n] != '/')
        return false;
    *relOut = path + n + 1;
    return true;
}

// The card's FAT matches names without regard to case, and so does the table: the packer wrote them lower case.
void lowerName(const char* rel, char* out, size_t cap)
{
    size_t i = 0;
    for (; rel[i] && i + 1 < cap; i++)
        out[i] = rel[i] >= 'A' && rel[i] <= 'Z' ? rel[i] - 'A' + 'a' : rel[i];
    out[i] = 0;
}

// The callbacks of one entry's stream: stdio calls them with the entry's coordinates, and the part handle is
// moved only when the stream's position moves, so a sequential read is one buffered read after another.
bool cookieSeekTo(Cookie* k, int64_t to)
{
    if (to < 0)
        to = 0;
    if ((uint64_t)to > k->size)
        to = (int64_t)k->size;
    k->pos = (uint64_t)to;
    return fseek(k->part, (long)(k->base + k->pos), SEEK_SET) == 0;
}

ssize_t cookieRead(void* c, char* buf, size_t n)
{
    Cookie* k = (Cookie*)c;
    uint64_t left = k->size > k->pos ? k->size - k->pos : 0;
    if (!n || !left)
        return 0;
    if (n > left)
        n = (size_t)left;
    size_t got = fread(buf, 1, n, k->part);
    k->pos += got;
    return (ssize_t)got;
}

int cookieSeek(void* c, ArchOff* off, int whence)
{
    Cookie* k = (Cookie*)c;
    int64_t to = whence == SEEK_SET ? (int64_t)*off
               : whence == SEEK_CUR ? (int64_t)k->pos + (int64_t)*off
                                    : (int64_t)k->size + (int64_t)*off;
    if (!cookieSeekTo(k, to))
        return -1;
    *off = (ArchOff)k->pos;
    return 0;
}

int cookieClose(void* c)
{
    Cookie* k = (Cookie*)c;
    LightLock_Lock(&g_lock);
    if (k->slot >= 0)
        g_busy[k->slot] = 0;
    LightLock_Unlock(&g_lock);
    if (k->slot < 0)
        realClose(k->part);                  // a handle this open took for itself, given back now
    free(k->buf);
    free(k);
    return 0;
}

// A borrowed handle for one entry of part p, or nullptr: the caller's lock is held here. The slot is kept as
// its absolute index in g_busy, which is what cookieClose and release hand back.
Cookie* borrow(unsigned p, uint64_t base, uint32_t size)
{
    size_t first = (size_t)p * kPool, slot = kPool;
    for (size_t j = 0; j < (size_t)kPool; j++)
        if (!g_busy[first + j]) { slot = j; break; }
    Cookie* k = (Cookie*)calloc(1, sizeof(Cookie));
    if (!k)
        return nullptr;
    k->slot = (int)(first + slot);
    if (slot < (size_t)kPool)
    {
        g_busy[first + slot] = 1;
        k->part = g_pool[p][slot];
    }
    else
    {
        // all of this part's handles borrowed already: one of our own, closed when the file is
        char partPath[256];
        snprintf(partPath, sizeof partPath, "%s/data-%u.bin", g_prefix.c_str(), p + 1);
        k->part = realOpen(partPath, "rb");
        k->slot = -1;
        if (k->part)
        {
            k->buf = (char*)malloc(kPartBuffer);
            setvbuf(k->part, k->buf, _IOFBF, kPartBuffer);
        }
    }
    if (!k->part)
    {
        if (k->slot >= 0)
            g_busy[k->slot] = 0;
        free(k->buf);
        free(k);
        return nullptr;
    }
    k->base = base;
    k->size = size;
    k->pos = 0;
    fseek(k->part, (long)base, SEEK_SET);
    return k;
}

// An open that never became a stream: the handle goes back, lock and all.
void release(Cookie* k)
{
    LightLock_Lock(&g_lock);
    if (k->slot >= 0)
        g_busy[k->slot] = 0;
    LightLock_Unlock(&g_lock);
    if (k->slot < 0)
        realClose(k->part);
    free(k->buf);
    free(k);
}
}

bool dataArchiveTryLoad(const char* dir)
{
    std::string idx = std::string(dir) + "/" + kIdxName;
    FILE* f = realOpen(idx.c_str(), "rb");
    if (!f)
    {
        struct stat st;      // (loose sets never have an index: only say something when a packed one can't be read)
        std::string one = std::string(dir) + "/data-1.bin";
        if (realStat(one.c_str(), &st) == 0)
            logf("data archive: %s is not readable, using the loose files", idx.c_str());
        return false;
    }
    fseek(f, 0, SEEK_END);
    long total = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (total < 16) { realClose(f); return false; }
    std::vector<char> raw(total);
    bool whole = fread(raw.data(), 1, total, f) == (size_t)total;
    realClose(f);
    if (!whole || memcmp(raw.data(), "MWDA", 4) != 0)
    {
        logf("data archive: %s is not a packed set", idx.c_str());
        return false;
    }
    uint32_t ver, parts, n;
    memcpy(&ver, raw.data() + 4, 4);
    memcpy(&parts, raw.data() + 8, 4);
    memcpy(&n, raw.data() + 12, 4);
    if (ver != 1 || !parts || !n)
    {
        logf("data archive: %s wants a reader newer than this (version %u)", idx.c_str(), ver);
        return false;
    }
    size_t pos = 16;
    g_nameAt.reserve(n); g_part.reserve(n); g_off.reserve(n); g_size.reserve(n);
    std::string prev;
    for (uint32_t i = 0; i < n; i++)
    {
        uint16_t len; uint32_t part; uint64_t off; uint32_t size;
        memcpy(&len, raw.data() + pos, 2);
        memcpy(&part, raw.data() + pos + 2, 4);          // the packer's table: name length, part, offset, size
        memcpy(&off, raw.data() + pos + 6, 8);
        memcpy(&size, raw.data() + pos + 14, 4);
        pos += 18 + len;
        if (pos > raw.size() || part >= parts)
        {
            logf("data archive: %s is cut short", idx.c_str());
            return false;
        }
        std::string name(raw.data() + pos - len, len);
        if (name < prev)
        {
            logf("data archive: %s is not sorted", idx.c_str());
            return false;
        }
        prev = name;
        g_nameAt.push_back((uint32_t)g_names.size());
        g_names.insert(g_names.end(), name.begin(), name.end());
        g_names.push_back(0);
        g_part.push_back(part);
        g_off.push_back(off);
        g_size.push_back(size);
    }
    raw.clear();
    // The parts, opened kPool times each: the handles every data read borrows from, each buffered wide.
    g_pool.resize(parts);
    g_busy.assign((size_t)parts * kPool, 0);
    for (uint32_t p = 0; p < parts; p++)
    {
        char partPath[256];
        snprintf(partPath, sizeof partPath, "%s/data-%u.bin", dir, p + 1);
        for (int k = 0; k < kPool; k++)
        {
            FILE* h = realOpen(partPath, "rb");
            if (!h)
            {
                logf("data archive: %s missing, using the loose files", partPath);
                for (auto& v : g_pool)
                    for (FILE* open : v)
                        realClose(open);
                g_pool.clear();
                return false;
            }
            char* buf = (char*)malloc(kPartBuffer);
            if (buf)
                setvbuf(h, buf, _IOFBF, kPartBuffer);
            g_pool[p].push_back(h);
        }
    }
    g_prefix = dir;
    logf("data archive: %u files in %u parts under %s", n, parts, dir);
    return true;
}

int archStat(const char* path, struct stat* st)
{
    const char* rel;
    if (underPrefix(path, &rel))
    {
        char lower[512];
        lowerName(rel, lower, sizeof lower);
        LightLock_Lock(&g_lock);
        size_t row;
        bool hit = find(lower, &row);
        uint32_t size = hit ? g_size[row] : 0;
        LightLock_Unlock(&g_lock);
        if (hit)
        {
            memset(st, 0, sizeof(*st));
            st->st_mode = S_IFREG | 0444;
            st->st_nlink = 1;
            st->st_size = (decltype(st->st_size))size;
            return 0;
        }
    }
    return realStat(path, st);        // (a mixed card, or no archive at all: the card answers)
}

FILE* archFopen(const char* path, const char* mode)
{
    const char* rel;
    if (!g_prefix.empty() && underPrefix(path, &rel) && !strpbrk(mode, "wa+"))
    {
        char lower[512];
        lowerName(rel, lower, sizeof lower);
        LightLock_Lock(&g_lock);
        size_t row;
        Cookie* k = find(lower, &row) ? borrow(g_part[row], g_off[row], g_size[row]) : nullptr;
        LightLock_Unlock(&g_lock);
        if (k)
        {
            cookie_io_functions_t io = {cookieRead, nullptr, cookieSeek, cookieClose};
            FILE* f = fopencookie(k, "rb", io);
            if (f)
            {
                setvbuf(f, nullptr, _IONBF, 0);   // the caller's buffer straight from the card's 64 KB reads
                return f;
            }
            release(k);
        }
        // a name the archive doesn't hold, or no stream: the card's loose files answer for it (a mixed card
        // keeps working, and a name in neither place comes back missing exactly as a plain card's would)
    }
    return realOpen(path, mode);
}

int archFclose(FILE* f)
{
    // stdio runs cookieClose, which hands the borrowed handle back, as part of closing the stream: both sorts
    // of file close the same way here.
    return realClose(f);
}

#else                                                  // the PC build on MSYS2: no cookie streams

bool dataArchiveTryLoad(const char* dir)
{
    logf("data archive: %s is packed for the card, and this build reads loose folders", dir);
    return false;
}

int archStat(const char* path, struct stat* st) { return realStat(path, st); }
FILE* archFopen(const char* path, const char* mode) { return realOpen(path, mode); }
int archFclose(FILE* f) { return realClose(f); }

#endif

#pragma pop_macro("fopen")
#pragma pop_macro("fclose")
#pragma pop_macro("stat")
