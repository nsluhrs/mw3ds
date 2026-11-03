"""Checks the converter's plugin merge (tools/convert/mwfiles.py read_game) rule by rule, against the plugins in
your own Data Files (Bethesda's official ones work: adamantiumarmor.esp, AreaEffectArrows.esp, bcsounds.esp,
EBQ_Artifact.esp, entertainers.esp, LeFemmArmor.esp, master_index.esp, Siege at Firemoth.esp). The rules are in
spec/plugins-and-translations.md.

  python tools/test/plugincheck.py                        every .esp in Data Files whose masters are loaded
  python tools/test/plugincheck.py --plugins "a.esp;b.esp"

Prints one line per check and "RESULT: PASS" or "RESULT: FAIL (n failed)"; exit status 1 on a failure.
"""
import argparse
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "convert"))
import mwfiles  # noqa: E402
from mwfiles import DATA_FILES, MASTER, cell_refs, read_records  # noqa: E402

failures = []


def check(ok, what):
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        failures.append(what)


def cell_key(r):
    d = r.get("DATA")
    if struct.unpack_from("<I", d)[0] & 1:
        return r.id.lower()
    return struct.unpack_from("<ii", d, 4)


def refnum_key(fi, sub, masters):
    num = struct.unpack("<I", sub[:4])[0]
    local = num >> 24
    return (masters[local - 1], num & 0xFFFFFF) if 0 < local <= len(masters) else (fi, num)


def frmr_groups(r):
    """[(FRMR bytes, ref subrecords)] of a CELL record, MVRF / NAM0 aside."""
    out = []
    for t, d in r.subs:
        if t == "FRMR":
            out.append((d, [(t, d)]))
        elif out and t not in ("NAM0", "MVRF", "CNDT"):
            out[-1][1].append((t, d))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plugins", help="';'-separated plugin names (default: every .esp needing only Morrowind.esm)")
    args = ap.parse_args()
    if args.plugins:
        plugins = [p for p in args.plugins.split(";") if p]
    else:
        plugins = sorted(p.name for p in DATA_FILES.glob("*.esp")
                         if all(m.lower() == MASTER.lower() for m in mwfiles.masters(p)))
    if not plugins:
        sys.exit("plugincheck: no plugins to check in " + str(DATA_FILES))
    print("plugins:", "; ".join(plugins))

    # The master alone: the merge gives back exactly what the file holds (the expansions and plugins stay out)
    mwfiles.PLUGINS, mwfiles.EXPANSIONS, mwfiles._merged = [], [], None
    raw = [r for r in read_records(DATA_FILES / MASTER) if r.tag != "TES3"]
    alone = mwfiles._merge()
    same = len(raw) == len(alone) and sorted(map(repr, ((r.tag, r.id) for r in raw))) == \
        sorted(map(repr, ((r.tag, r.id) for r in alone)))
    check(same, f"{MASTER} alone: the merge holds its {len(raw)} records")
    mwfiles.EXPANSIONS = [DATA_FILES / n for n in ("Tribunal.esm", "Bloodmoon.esm")
                          if os.environ.get("MW3DS_EXPANSIONS", "").strip() not in ("", "0") and (DATA_FILES / n).is_file()]

    mwfiles.PLUGINS, mwfiles._merged = plugins, None
    order = mwfiles.load_order()
    merged = mwfiles._merge()
    db = mwfiles.load_db()
    index = {p.name.lower(): i for i, p in enumerate(order)}

    # 1. A later record of the same type and ID replaces the whole record; DELE removes it
    last, deleted = {}, set()
    for fi, path in enumerate(order):
        for r in read_records(path):
            if r.tag in ("TES3", "CELL", "DIAL", "INFO", "LAND", "LTEX", "PGRD"):
                continue
            key = mwfiles._record_key(r)
            if r.get("DELE") is not None:
                last.pop(key, None)
                deleted.add(key)
            else:
                last[key] = (fi, r)
                deleted.discard(key)
    got = {mwfiles._record_key(r): r for r in merged
           if r.tag not in ("CELL", "DIAL", "INFO", "LAND", "LTEX", "PGRD")}
    overridden = [k for k, (fi, r) in last.items() if fi > 0]
    bad = [k for k, (fi, r) in last.items() if got.get(k) is None or got[k].subs != r.subs]
    check(not bad, f"records: the last file's version wins ({len(overridden)} from plugins){' ' + str(bad[:3]) if bad else ''}")
    check(not any(k in got for k in deleted), f"records: DELE removes ({len(deleted)})")

    # 2. Cells: references by (file, number); a plugin's own replaces in place, DELE removes, new ones join
    plugin_cells = {}
    for fi, path in enumerate(order[1:], 1):
        masters = [index[m.lower()] for m in mwfiles.masters(path)]
        for r in read_records(path, {"CELL"}):
            for frmr, subs in frmr_groups(r):
                plugin_cells.setdefault(cell_key(r), []).append((refnum_key(fi, frmr, masters), subs, fi))
    merged_cells = {cell_key(r): r for r in merged if r.tag == "CELL"}
    master_cells = {cell_key(r): r for r in read_records(DATA_FILES / MASTER, {"CELL"})}
    changed = deleted_refs = added = 0
    bad_cells = []
    for ck, refs in plugin_cells.items():
        m = merged_cells.get(ck)
        if m is None:
            bad_cells.append(ck)
            continue
        groups = frmr_groups(m)
        keys = [refnum_key(0, f, []) for f, _ in groups]       # merged keys: (0, n) master / (fi, n) own ...
        final = {}
        for key, subs, fi in refs:
            final[key] = subs
        for key, subs in final.items():
            dele = any(t == "DELE" for t, _ in subs)
            present = [g for g in groups if g[1] == subs]
            if dele:
                deleted_refs += 1
                if present:
                    bad_cells.append((ck, "deleted ref kept"))
            else:
                if key[0] == 0:
                    changed += 1
                else:
                    added += 1
                if not present:
                    bad_cells.append((ck, "ref missing", key))
        del keys
        # As many references as the master's, plus the new ones, less the deleted ones (none twice)
        base = master_cells.get(ck)
        base_refs = {struct.unpack("<I", f[:4])[0] for f, s in frmr_groups(base) if not any(t == "DELE" for t, _ in s)} \
            if base else set()
        want = len(base_refs) + sum(1 for k, s in final.items() if k[0] != 0 and not any(t == "DELE" for t, _ in s)) \
            - sum(1 for k, s in final.items() if k[0] == 0 and k[1] in base_refs and any(t == "DELE" for t, _ in s))
        if len(cell_refs(m)) != want:
            bad_cells.append((ck, f"{len(cell_refs(m))} references, {want} wanted"))
    check(not bad_cells, f"cells: plugin references in place ({changed} changed, {added} new, {deleted_refs} deleted)"
          f"{' ' + str(bad_cells[:3]) if bad_cells else ''}")
    # A master reference a plugin changed keeps its place among the cell's references
    moved_place = 0
    for ck, refs in plugin_cells.items():
        base = next((r for r in read_records(DATA_FILES / MASTER, {"CELL"}) if cell_key(r) == ck), None) \
            if any(k[0] == 0 for k, _, _ in refs) else None
        if base is None:
            continue
        order_base = [struct.unpack("<I", f[:4])[0] for f, _ in frmr_groups(base)]
        order_merged = [struct.unpack("<I", f[:4])[0] & 0xFFFFFF for f, _ in frmr_groups(merged_cells[ck])]
        kept = [n for n in order_merged if n in order_base]
        expect = [n for n in order_base if n in kept]
        moved_place += kept != expect
    check(moved_place == 0, "cells: master references keep their order")

    # 3. Dialogue: in the merged chain every response comes after the one its PNAM names, and one naming an INFO
    # that is gone sits last (OpenMW's InfoOrder). Not necessarily right after: a later INFO naming the same PNAM
    # splices in first, its earlier siblings staying behind it (std::list::splice after the named one). An INFO
    # with no PNAM goes first only when it is new; an existing one re-inserted (TR rebuilds vanilla topics with
    # chains of its own) keeps its place, so no rule. Deleted INFOs are out of the chain.
    misplaced, placed = [], 0
    master_info = set()
    topic = None
    for r in read_records(DATA_FILES / MASTER, {"DIAL", "INFO"}):
        if r.tag == "DIAL":
            topic = (r.id or "").lower()
        elif topic:
            master_info.add((topic, r.zstr("INAM")))
    for d in db["dialogue"]:
        chain = [i.zstr("INAM") for i in d["infos"]]
        pos = {n: k for k, n in enumerate(chain)}
        for i in d["infos"]:
            inam, prev = i.zstr("INAM"), i.zstr("PNAM") or ""
            if (d["name"].lower(), inam) not in master_info:
                placed += 1
            if i.get("DELE") is not None and inam in pos:
                misplaced.append((d["name"], inam, "deleted kept"))
            elif prev and prev in pos and pos[prev] >= pos[inam]:
                misplaced.append((d["name"], inam, "not after its PNAM"))
            elif prev and prev not in pos and pos[inam] != len(chain) - 1:
                misplaced.append((d["name"], inam, "unknown PNAM not last"))
    check(not misplaced, f"dialogue: {placed} plugin responses after their PNAM"
          f"{' ' + str(misplaced[:3]) if misplaced else ''}")

    # 4. Land: a plugin's LAND names its own LTEX records by index; the merged one names the same textures
    bad_land, lands = [], 0
    for fi, path in enumerate(order[1:], 1):
        own = {struct.unpack("<i", r.get("INTV"))[0]: r.zstr("DATA") for r in read_records(path, {"LTEX"})}
        for r in read_records(path, {"LAND"}):
            vtex = r.get("VTEX")
            grid = struct.unpack("<ii", r.get("INTV")[:8])
            if vtex is None or db["LAND"].get(grid) is not r and db["LAND"].get(grid) is None:
                continue
            lands += 1
            want = [own.get(v - 1) if v else None for v in struct.unpack(f"<{len(vtex) // 2}H", vtex)]
            mv = db["LAND"][grid].get("VTEX")
            have = [db["LTEX"].get(v - 1) if v else None for v in struct.unpack(f"<{len(mv) // 2}H", mv)]
            if want != have:
                bad_land.append(grid)
    check(not bad_land, f"land: {lands} plugin LAND records keep their textures{' ' + str(bad_land[:3]) if bad_land else ''}")

    # 5. Load order: a plugin that isn't there is refused; the expansions load when MW3DS_EXPANSIONS is set
    for names, why in ((["no such plugin.esp"], "missing plugin refused"),):
        mwfiles.PLUGINS = names
        try:
            mwfiles.load_order()
            check(False, why)
        except SystemExit:
            check(True, why)
    mwfiles.PLUGINS, saved = [], mwfiles.EXPANSIONS
    mwfiles.EXPANSIONS = [DATA_FILES / "Tribunal.esm"]
    check([p.name for p in mwfiles.load_order()] == [MASTER, "Tribunal.esm"], "expansions: loaded when the flag is set")
    mwfiles.EXPANSIONS = saved

    # 6. Text: the code page setting, file names for letters outside ASCII
    check(mwfiles.text(b"wulfharth\x92s cups\0") == "wulfharth’s cups" or mwfiles.ENCODING != "win1252",
          "text: win1252 0x92 is a right single quote")
    a, b = mwfiles.safe_name("Балмора"), mwfiles.safe_name("Вивек")
    check(a != b and mwfiles.safe_name("Seyda Neen, Arrille's Tradehouse") == "seyda_neen__arrille_s_tradehouse",
          "file names: ASCII names as before, others told apart by a hash")

    print(f"RESULT: {'PASS' if not failures else f'FAIL ({len(failures)} failed)'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
