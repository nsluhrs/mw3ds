"""Packs a converted set into the big-file archive the console's SD card reads fast.
    python tools/build/pack_sdmc.py out/tr                 -> build/tr-pack/data-1.bin ... data-N.bin data.idx
    python tools/build/pack_sdmc.py out/tr --out DIR       another staging folder
    python tools/build/pack_sdmc.py out/tr --parts 3       smaller parts (the 4 GB FAT32 file's cap says how many)

An SD card wears down on 100000 small files: each cell, texture, sound and book page is a fresh FAT open, and
the staging copy lands wherever the card's free gaps are. The archive form keeps the same names and the same
bytes but lays every file whole, padded to the card's 512-byte sectors, into a handful of big files. The
engine then reads a name's bytes with one seek into one open file, which is the pattern SD cards are quick
at. Two ceilings pick how many files: FAT32's 4 GB on a single one, and under it newlib's 32-bit file
positions (off_t and ftell are a 32-bit long on devkitARM), which stop stdio from seeking past 2 GB. The
engine reads through stdio, so parts stay under that and a 5.3 GB set spreads over three.

data.idx holds the table: every name (lower case, as the card's FAT would match it), its part, its offset
and its length. Sorted by name, so the engine finds any of them by searching the sorted table, and
tools/build/pack_sdmc.py --check DIR re-reads samples to prove the bytes.
"""
import argparse
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mwtools

SECTOR = 512
# Two ceilings pick the part size: FAT32's 4 GB per file, and the looser one under it, newlib's 32-bit file
# positions (off_t and ftell are a 32-bit long on devkitARM), which stop stdio from seeking past 2 GB. The
# engine reads with stdio, so a part stays under that; the set's bytes simply spread over more of them.
PART_MAX = 1_900_000_000
MAGIC = b"MWDA"


def parts_for(total: int) -> int:
    return max(1, -(-total // PART_MAX))     # 5.43 GB of set becomes three parts; 1.4 GB stays one



def pack(data: Path, out: Path, parts: int):
    files = sorted((p for p in data.rglob("*") if p.is_file()), key=lambda p: str(p.relative_to(data)).lower())
    entries, cur, part = [], 0, 0
    plans = [out / f"data-{i + 1}.bin" for i in range(parts)]
    with open(out / "data.idx", "wb") as idx:
        idx.write(MAGIC + struct.pack("<III", 1, parts, len(files)))
        for f in files:
            name = str(f.relative_to(data)).replace("\\", "/").lower().encode("utf-8")
            size = f.stat().st_size
            if cur + size > PART_MAX:
                if part + 1 >= parts:
                    raise SystemExit(f"{f}: no room left in {parts} parts; pass a bigger --parts")
                part, cur = part + 1, 0          # a part ends where it ends; every file stays whole in one
            idx.write(struct.pack("<HIQI", len(name), part, cur, size) + name)   # length, part, offset, size: 18 bytes
            entries.append((part, cur, size, f))
            cur = (cur + size + SECTOR - 1) // SECTOR * SECTOR
    total = 0
    for p in range(parts):
        w = 0
        with open(plans[p], "wb") as o:
            for part, off, size, f in entries:
                if part != p:
                    continue
                assert o.tell() == off, f"{f}: at {o.tell()}, planned {off}"
                with open(f, "rb") as i:
                    while chunk := i.read(1 << 20):
                        o.write(chunk)
                        w += len(chunk)
                pad = (-size) % SECTOR
                o.write(b"\0" * pad)
        o_size = plans[p].stat().st_size
        total += w
        print(f"{plans[p]}: {o_size / 1e9:.2f} GB ({w / 1e9:.2f} used)")
    print(f"{len(files)} files, {total / 1e9:.2f} GB of bytes; {out}")
    # game.json beside the parts, for the tools and the card: which set this is, without opening the archive.
    # (the engine still reads its own copy inside: the archive answers data paths first, and the bytes match)
    shutil.copyfile(data / "game.json", out / "game.json")
    check(data, out)


def check(data: Path, out: Path):
    """Re-reads the head, middle and tail of 200 sampled files and the whole of 20 small ones."""
    idx = open(out / "data.idx", "rb").read()
    assert idx[:4] == MAGIC
    ver, parts, n = struct.unpack_from("<III", idx, 4)
    pos, reclen = 16, struct.calcsize("<HIQI")
    rows = []
    for _ in range(n):
        ln, part, off, size = struct.unpack_from("<HIQI", idx, pos)
        pos += reclen + ln
        rows.append((idx[pos - ln:pos].decode("utf-8"), part, off, size))
    assert all(r[0] == r[0].lower() for r in rows), "a name is not lower case"
    assert rows == sorted(rows), "the table is not sorted by name"
    import random
    random.seed(7)
    parts_f = [open(out / f"data-{i + 1}.bin", "rb") for i in range(parts)]
    sample = random.sample(rows, min(200, len(rows))) + [r for r in rows if 0 < r[3] <= 4096][:20]
    for name, part, off, size in sample:
        src = (data / name)
        p = parts_f[part]
        with open(src, "rb") as i:
            if size <= 4096:
                p.seek(off)
                assert p.read(size) == i.read(size), f"{name}: bytes differ"
                continue
            i.seek(0)
            p.seek(off)
            assert p.read(1024) == i.read(1024), f"{name}: head differs"
            i.seek(size // 2)
            p.seek(off + size // 2)
            assert p.read(1024) == i.read(1024), f"{name}: middle differs"
            i.seek(max(0, size - 1024))
            p.seek(off + max(0, size - 1024))
            assert p.read(1024) == i.read(1024), f"{name}: tail differs"
    empties = [r for r in rows if r[3] == 0]
    print(f"--check: {len(sample)} reads match" + (f" ({len(empties)} empty files, kept)" if empties else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", nargs="?", default="out/tr", help="the converted set (default out/tr)")
    ap.add_argument("--out", metavar="DIR", help="where to write the archive (default build/<set>-pack)")
    ap.add_argument("--parts", type=int, help="part files to spread over (default: enough to keep each under 2 GB)")
    ap.add_argument("--check", metavar="DIR", help="only verify build/<set>-pack against the set DIR")
    a = ap.parse_args()
    data = Path(a.data)
    if not (data / "game.json").is_file():
        raise SystemExit(f"{data} has no game.json: convert it first")
    parts = a.parts or parts_for(sum(f.stat().st_size for f in data.rglob("*") if f.is_file()))
    out = Path(a.out) if a.out else ROOT / "build" / f"{data.name}-pack"
    out.mkdir(parents=True, exist_ok=True)
    if a.check:
        check(Path(a.check), out)
        return
    pack(data, out, parts)


ROOT = mwtools.ROOT
mwtools.load_env()

if __name__ == "__main__":
    main()
