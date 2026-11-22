"""Visits every cell of a converted data folder on the native (PC) build and judges each visit.

    python tools/test/visit_all.py [--data out/data] [--jobs 24] [--batch 20] [--order size|towns|name]
                                   [--filter 'regex'] [--max N] [--resume] [--wait-mult 8] [--build]

Every exterior grid cell in cells/**/ext_*.cel gets a COE:x:y hop (the test console's "set me down on the
ground in that cell"); every interior the converter recorded an entry point for (cell_entries.txt, matched
to its .cel through the converter's safe_name) gets a GOTO there. Interiors with no entry point -- cells no
door leads to -- are reported skipped, not failed.

Each case batch is one generated case (tools/test/cases/visitall/va-NNNN.txt) run by run_test.py in its own
SD slot, several at a time like run_native_all.py. The engine's own log judges every visit, attributed to
the step that made it: arrival (or "not in the level"), failed loads and out-of-memory lines, monitor
warnings, missing textures, and the linear pool's free space around the arrival.

-> build/visitall-results/<stamp>/{visit-all.csv, summary.txt, logs/va-NNNN.log}; newest also copied to
   build/visitall-results/latest/. --resume skips the cells that passed the latest run.
--order size (default) visits the biggest .cel files first: they need the most linear and heap, so the
worst-case admissions -- a big cell arriving next to a big town -- happen while memory is already tight.
--order towns groups every town grid with its eight neighbours (the "park in the town, ring it" order where
refusals live), then the rest by size. --order name is alphabetical, for diffing two runs cell by cell.
"""
import argparse
import csv
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "convert"))
import mwtools  # noqa: E402
from mwfiles import safe_name  # noqa: E402

# Lines of the engine's log that mean a visit went wrong, and what each says. The LRU evictor's
# "objects out of memory" churn and a neighbour's refused-and-retried streaming are normal under
# pressure: only loads that failed outright and allocation failures inside the player's own arrival
# are FAILs; the pool running dry mid-stream is a WARN with the number kept.
BAD = [
    (re.compile(r"world: failed to load (\S+)"), "load-failed"),
    (re.compile(r"inflate failed|out of memory inflating|out of memory reading"), "inflate-oom"),
    (re.compile(r"cell: .*: no such file"), "cel-missing"),
]
WARN = [
    (re.compile(r"monitor: (.*)"), None),
    (re.compile(r"watchdog: (.*)"), None),
    (re.compile(r"out of linear memory"), "linear-oom"),
    (re.compile(r"is not in the level"), "script-cell-missing"),
]
ARRIVE = re.compile(r"test: (going into|going to|going straight into|outside in) (.*)")
MISSING = re.compile(r"\((\d+) missing\)")
FREE = re.compile(r"linear free (\d+) KB")


def grid_of(stem):
    """ext_19_m59 -> (19, -59), ext_m7_m6 -> (-7, -6); None for interior file names."""
    parts = stem.split("_")
    if len(parts) != 3 or parts[0] != "ext":
        return None
    def coord(tok):
        return -int(tok[1:]) if tok.startswith("m") else int(tok)
    try:
        return (coord(parts[1]), coord(parts[2]))
    except ValueError:
        return None


def universe(data):
    """(cells, skipped): every visitable cell as dicts {name, kind, tok, kb}, and the no-entry interiors."""
    cel_files = {}
    for p in (data / "cells").rglob("*.cel"):
        cel_files[p.stem.lower()] = p
    cells, skipped = [], []
    for stem, p in sorted(cel_files.items()):
        g = grid_of(stem)
        if g is None:
            continue
        x, y = g
        cells.append({"name": f"ext {x},{y}", "kind": "exterior", "tok": f"COE:{x}:{y}",
                      "kb": p.stat().st_size // 1024})
    entries = data / "cell_entries.txt"
    if not entries.exists():
        sys.exit(f"no cell_entries.txt in {data}")
    claimed = set()
    for ln in entries.read_text(encoding="utf-8").splitlines():
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0]
        stem = safe_name(name).lower()
        p = cel_files.get(stem)
        if p is None or stem in claimed:
            continue  # a town street or a named exterior: its grid cell is COE'd anyway
        claimed.add(stem)
        tok = name.replace(" ", "_")
        cells.append({"name": name, "kind": "interior", "tok": f"GOTO:{tok}", "kb": p.stat().st_size // 1024})
    for stem, p in sorted(cel_files.items()):
        if grid_of(stem) is None and stem not in claimed:
            skipped.append(p.stem)
    return cells, skipped


def order_cells(cells, how):
    if how == "name":
        return sorted(cells, key=lambda c: c["name"])
    if how == "towns":
        # town grids: named exterior cells the engine knows streets for (town spawns come from the same
        # converter pass); a grid is a town's when any town-ish entry name lands inside it. Fall back to
        # grouping each named town's grid with its neighbours by entry coordinates without a comma.
        towns = [c for c in cells if c["kind"] == "exterior" and c["kb"] >= 400]  # town grids are heavy
        rest = [c for c in cells if c not in towns]
        out = []
        for t in towns:
            m = re.match(r"ext (-?\d+),(-?\d+)", t["name"])
            gx, gy = int(m.group(1)), int(m.group(2))
            out.append(t)
            out += [r for r in rest if re.match(rf"ext {gx + dx},{gy + dy}$", r["name"]) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            rest = [r for r in rest if r not in out]
        out += sorted(rest, key=lambda c: -c["kb"])
        return out
    return sorted(cells, key=lambda c: -c["kb"])


def make_cases(cells, batch, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(0, len(cells), batch):
        chunk = cells[i:i + batch]
        n = i // batch
        lines = ["3 0 0 0 0 MSG", "0.3 0 0 0 0 GOD:2"]
        for c in chunk:
            lines.append(f"0.3 0 0 0 0 {c['tok']}")
            lines.append("3 0 0 0")
        lines.append("0.3 0 0 0 0 QUIT")
        name = f"visitall/va-{n:04d}"
        mwtools.write_lines(ROOT / "tools" / "test" / "cases" / (name + ".txt"), lines)
        names.append((name, chunk))
    return names


def judge(log_text, cells_of_batch):
    """Attribute every log line to the step that wrote it, then one verdict per cell.

    The step's own 'test:' action line is logged on its first frame, just BEFORE its marker line; its
    load lines come after the marker, up to the next one; a wait step's streaming and monitor lines
    come after its marker. So: flush the gap before each marker with 'test:' lines owned by that
    marker's step and everything else owned by the step before it. Cell i hops at step 2+2i (MSG and
    GOD take 0 and 1) and waits at 3+2i, so its signals are the lines owned by those two steps.
    A 'test: <name> is not in the level' only FAILs when it names this cell's target; scripts say
    the same phrase about cells they looked for ('test: t test pc is not in the level')."""
    marker = re.compile(r"autoinput: step (\d+) .*feet")
    by, pending, last = {}, [], None
    for ln in log_text.splitlines():
        m = marker.search(ln)
        if not m:
            pending.append(ln)
            continue
        n = int(m.group(1))
        for p in pending:
            owner = n if "test: " in p else max(n - 1, 0)
            by.setdefault(owner, []).append(p)
        pending, last = [], n
    if last is not None:
        by.setdefault(last, []).extend(pending)

    arrived = re.compile(r"world: entered |world: outside in |travel: .* in \d+ ms|cellmem: .* entered")
    out = []
    for i, c in enumerate(cells_of_batch):
        j = 2 + 2 * i
        body = "\n".join(by.get(j, []) + by.get(j + 1, []))
        bad = sorted({label for rx, label in BAD if rx.search(body)})
        if c["kind"] == "interior":
            want = c["name"].lower()
        else:
            g = re.match(r"COE:(-?\d+):(-?\d+)", c["tok"])
            want = f"Exterior cell {g.group(1)}, {g.group(2)}"
        if re.search(re.escape(want) + r"\b is not in the level", body):
            bad = sorted(set(bad) | {"not-in-level"})
        elif not arrived.search(body):
            bad = sorted(set(bad) | {"no-arrival"})
        warn = []
        for rx, label in WARN:
            m2 = rx.search(body)
            if m2:
                warn.append(m2.group(1) if label is None and m2.groups() else label)
        warn = sorted(set(warn))
        miss = max((int(g) for g in MISSING.findall(body)), default=0)
        free = min((int(g) for g in FREE.findall(body)), default=None)
        verdict = "FAIL" if bad else ("WARN" if warn or miss else "PASS")
        detail = ", ".join(bad + warn + ([f"{miss} missing textures"] if miss else []))
        out.append({**c, "verdict": verdict, "detail": detail, "linear_free_kb": free})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="out/data")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--order", choices=["size", "towns", "name"], default="size")
    ap.add_argument("--filter", default="")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--wait-mult", type=int, default=12)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--exe", default="")
    ap.add_argument("--sd-root", default="build/visitall")
    a = ap.parse_args()
    if a.jobs <= 0:
        a.jobs = max(1, (int(__import__("os").cpu_count()) or 8) - 2)

    data = (ROOT / a.data) if not Path(a.data).is_absolute() else Path(a.data)
    cells, skipped = universe(data)
    if a.filter:
        rx = re.compile(a.filter)
        cells = [c for c in cells if rx.search(c["name"])]
    cells = order_cells(cells, a.order)
    if a.resume:
        latest = ROOT / "build/visitall-results/latest/visit-all.csv"
        if latest.exists():
            done = {r["cell"] for r in csv.DictReader(open(latest, encoding="utf-8")) if r["verdict"] == "PASS"}
            cells = [c for c in cells if c["name"] not in done]
    if a.max:
        cells = cells[:a.max]
    print(f"{len(cells)} cells to visit ({len(skipped)} interiors have no entry point), "
          f"{(len(cells) + a.batch - 1) // a.batch} batches, {a.jobs} at a time, data {data}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    res = ROOT / "build" / "visitall-results" / stamp
    (res / "logs").mkdir(parents=True, exist_ok=True)
    cases_dir = ROOT / "tools" / "test" / "cases" / "visitall"
    shutil.rmtree(cases_dir, ignore_errors=True)
    for f in (ROOT / "tools" / "test" / "cases").glob("va-*.txt"):
        f.unlink()
    batches = make_cases(cells, a.batch, cases_dir)

    exe = a.exe
    if a.build or not (ROOT / (exe or mwtools.NATIVE_EXE)).exists():
        subprocess.run([sys.executable, str(mwtools.TEST_DIR / "native" / "build.py")], check=True)
    exe = str((ROOT / (exe or mwtools.NATIVE_EXE)).resolve())

    run_test = str(mwtools.TEST_DIR / "run_test.py")
    queue = list(batches)
    slots = [None] * a.jobs
    started = [0.0] * a.jobs
    rows, dead = [], []
    n = 0
    out_csv = res / "visit-all.csv"
    while queue or any(slots):
        for k, slot in enumerate(slots):
            if slot and slot.poll() is not None:
                name, chunk = slot.case
                log = ROOT / a.sd_root / f"sd-{k}" / "3ds" / "mw3ds" / "log.txt"
                text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
                shutil.copy(log, res / "logs" / f"va-{slot.n}.log") if log.exists() else None
                if not text:
                    dead.append(name)
                    rows.extend({**c, "verdict": "FAIL", "detail": "no log (the run died)", "linear_free_kb": None}
                                for c in chunk)
                else:
                    rows.extend(judge(text, chunk))
                n += 1
                print(f"  {n}/{len(batches)} {name}: "
                      f"{sum(1 for r in rows[-len(chunk):] if r['verdict'] == 'PASS')} of {len(chunk)} pass")
                slots[k] = None
            if slots[k] is None and queue:
                name, chunk = queue.pop(0)
                idx = int(name.split("-")[1])
                wait = 60 + a.wait_mult * len(chunk)
                cmd = [sys.executable, run_test, name, "--native", "--sd", str(ROOT / a.sd_root / f"sd-{k}"),
                       "--data", str(data), "--wait", str(wait), "--exe", exe]
                p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                p.case, p.n = (name, chunk), idx
                slots[k] = p
                started[k] = time.time()
        for k, p in enumerate(slots):
            if p and time.time() - started[k] > 60 + a.wait_mult * len(p.case[1]) + 120:
                mwtools.kill_tree(p)
        time.sleep(1)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["cell", "kind", "tok", "cel_kb", "verdict", "detail", "linear_free_kb"])
        wr.writeheader()
        for r in rows:
            wr.writerow({"cell": r["name"], "kind": r["kind"], "tok": r["tok"], "cel_kb": r["kb"],
                         "verdict": r["verdict"], "detail": r["detail"], "linear_free_kb": r["linear_free_kb"]})
    fails = [r for r in rows if r["verdict"] == "FAIL"]
    warns = [r for r in rows if r["verdict"] == "WARN"]
    with open(res / "summary.txt", "w", encoding="utf-8") as f:
        f.write(f"visit-all {stamp} data={data}\n{len(rows)} cells: {len(rows) - len(fails) - len(warns)} PASS, "
                f"{len(warns)} WARN, {len(fails)} FAIL, {len(skipped)} skipped (no entry point)\n\n")
        for r in fails:
            f.write(f"FAIL {r['name']:60} {r['detail']}\n")
        for r in warns:
            f.write(f"WARN {r['name']:60} {r['detail']}\n")
        f.write("\nskipped (interiors no door leads to):\n  " + "\n  ".join(skipped) + "\n")
    latest = ROOT / "build/visitall-results/latest"
    shutil.rmtree(latest, ignore_errors=True)
    shutil.copytree(res, latest)
    print(f"\n{len(rows)} cells: {len(rows) - len(fails) - len(warns)} PASS, {len(warns)} WARN, {len(fails)} FAIL")
    for r in fails[:15]:
        print(f"  FAIL {r['name']:50} {r['detail'][:60]}")
    print(f"saved: {res}")


if __name__ == "__main__":
    main()
