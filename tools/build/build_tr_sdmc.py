"""Packs a big converted set the way the console can take it: a program-only CIA plus the data read from the SD card.
    python tools/build/build_tr_sdmc.py                 Tamriel Rebuilt: build/mw3ds-tr-sdmc.cia + out/tr staged for the card
    python tools/build/build_tr_sdmc.py --build         build the program first (needs devkitPro; else the README's container)
    python tools/build/build_tr_sdmc.py --data out/exp  another converted set; the CIA and the staging folder are named after it
    python tools/build/build_tr_sdmc.py --stage DIR     stage the card layout elsewhere (default build/<set>-sdmc)
    python tools/build/build_tr_sdmc.py --no-stage      the CIA only: the data already on the card stays as it is

Tamriel Rebuilt's data is 5.4 GB, past the 4 GB a CIA can carry (makerom's RomFS image, and the card's FAT32, stop
there), so it never fits in the RomFS. The engine reads sdmc:/3ds/mw3ds/data when the CIA packed no data
(engine.cpp), so this CIA ships the program alone (~2 MB) and the script stages the set under a 3ds folder to put
on the card. Convert the set first with the TR ESMs (which sit in Data Files) in MW3DS_PLUGINS: the .env.example
line explains it, and build_cia.py --data out/tr converts and packs the folder this script then ships.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mwtools
from make_cia import pack

ROOT = mwtools.ROOT


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="build the program instead of packing the mw3ds.elf already in the repo root")
    ap.add_argument("--data", default="out/tr", metavar="DIR", help="the converted set to ship (default out/tr)")
    ap.add_argument("--stage", metavar="DIR", help="where to stage the card layout (default build/<set>-sdmc)")
    ap.add_argument("--no-stage", action="store_true", help="pack the CIA and skip staging the data")
    a = ap.parse_args()

    mwtools.load_env()
    data = Path(a.data)
    if not (ROOT / data / "game.json").is_file():
        raise SystemExit(f"{data} has no game.json: convert it first (build_cia.py --data {data})")
    name = data.name
    cia = f"build/mw3ds-{name}-sdmc.cia"
    stage = Path(a.stage) if a.stage else ROOT / "build" / f"{name}-sdmc"

    if a.build and mwtools.make([]).returncode != 0:
        raise SystemExit("make failed: see its output above (devkitPro, or the container recipe in the README)")

    # The program alone in the CIA (no_data): the set would push the RomFS past the 4 GB ceiling
    if pack(str(data), cia, no_data=True) != 0:
        raise SystemExit("CIA build failed: see the makerom output just above this error")

    if not a.no_stage:
        # The card's layout: the engine reads sdmc:/3ds/mw3ds/data, so the set keeps that name whatever it is called here
        dest = stage / "3ds" / "mw3ds" / "data"
        print(f"== staging {data} at {dest}")
        # (hard links where the disk allows: a staging copy of 5.4 GB for nothing, until it goes to the card)
        mwtools.copy_tree_linked(ROOT / data, dest)
        print(f"put {stage / '3ds'} on the root of the SD card (it merges with the 3ds folder there), then install {cia} with FBI.")
    print(f"Built {ROOT / cia}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
