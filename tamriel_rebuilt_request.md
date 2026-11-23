# Tamriel Rebuilt on mw3ds — what it took

The base repository does not take pull requests, so this document stands in place of one: it records the
exact Tamriel Rebuilt release the work was done against, every change branch `tr-on-main` made for it
(pushed to `nsluhrs/mw3ds`), and how to reproduce the build.

## The Tamriel Rebuilt release used (recorded)

| Piece | Version | Where from |
|---|---|---|
| **Tamriel Rebuilt (TR_Mainland.esm)** | **26.08a** (`TR_Mainland-metadata.toml: version = "26.08a"`) | Nexus mods/42145, archive `downloads/TamrielRebuilt_26-08a.7z` (65 MB) |
| **Tamriel_Data (Tamriel_Data.esm)** | **12** (`Tamriel_Data-metadata.toml: version = "12"`; TR_Mainland requires `>=12`) | Nexus mods/44537, archive `downloads/Tamriel_Data.7z` (845 MB) |

- Load order the data was converted with: `Morrowind.esm`, `Tribunal.esm`, `Bloodmoon.esm`,
  `Tamriel_Data.esm`, `TR_Mainland.esm` — TR needs **both** add-ons (`game.json` records
  `"addons": ["tribunal", "bloodmoon"]`).
- The ESMs and loose assets (meshes, textures, sounds, books) sit in the Morrowind `Data Files` like any
  mod install; the 7z releases and unpacked trees stay out of git (`mods/tamriel-rebuilt/`, `downloads/`,
  added to `.gitignore`).
- Converted set: `out/tr`, **5.3 GB**, **12,683 cells** (the 3,290 vanilla grid squares plus TR's mainland),
  `game.json` declaring `"plugins": ["tamriel_data.esm", "tr_mainland.esm"]`.

## Converting it

`.env` with `MW3DS_MORROWIND_DIR` set and the two TR ESMs in `Data Files`, then:

```
MW3DS_TRIBUNAL=1 MW3DS_BLOODMOON=1 \
MW3DS_PLUGINS="Tamriel_Data.esm;TR_Mainland.esm" \
python tools/build/build_cia.py --data out/tr          # drop nothing: converts and packs
```

`--data` points `MW3DS_OUT` at the folder and names the CIA after it; `build_game.py` stamps the plugins
and add-ons into `game.json` so the engine and the test harness both know what the data holds.

## What the plugin set needed from the converter

- **Merge the way a plugin set uses it** — the ESM master chain is checked (`MW3DS_PLUGINS` refuses
  masters that are missing or are add-ons that are off), records replace by (type, id) with `DELE`
  removing, cell references key on (file, number), and dialogue INFO chains follow OpenMW's `InfoOrder`
  splice (an INFO sits after the one its PNAM names, earlier siblings trail, a re-inserted INFO with an
  empty PNAM keeps its place — TR rebuilds vanilla topics this way). `tools/test/plugincheck.py` checks
  all of it rule by rule; `c83da49b` fixed its master-alone check to clear the add-on flags, which TR's
  load order otherwise breaks.
- **Meshes and ids TR brings** (`eff179ec`): the Oathbound armor's `NiSortAdjustNode` and its
  `NiAlphaAccumulator` (read as OpenMW does, empty records on a GPU with no dynamic sorting);
  `NiStringExtraData` children skipped in `walk_nif` (MOP's `w_wakizashi.nif`, worn by a TR weapon, hung
  one on a node and killed the walk); punctuation-only id collisions like `TR_m1_S_Ka'Minhir` vs
  `TR_m1_S_Ka_Minhir` — `safe_name` folded them together, so ambiguous ids now get a hash-suffixed `.aml`.
- **Conversion survivability on Linux** (`f71db0a0`): the cell-worker count reads the free memory on Linux
  too — TR's conversion (the biggest set by far) was picking worker counts that OOM-killed runs.

## What the plugin set needed from the engine

- **Add-on rules from the data** — `game.json` `addons` turns Tribunal's and Bloodmoon's engine and script
  rules on for TR data (TR scripts call their functions).
- **Split `game.json`** — big sections live in `game_<name>_0.json, _1 …` parts and the store
  (`mwworld/esmstore.cpp`) reads one part at a time: a whole parsed `game.json` for a set this size does
  not fit the heap next to the converted data.
- **Scripts on the card** — compiled script trees spill to `scripts.bin` and read back when a script runs
  (`mwscript/scriptstore.cpp`); TR's scripts are what pushed this from nice-to-have to needed.
- **`game.json` `plugins`** (`951f2271`) — the data declares what it was converted with; a case marked
  `# data: plugins` skips, not fails, on vanilla data.

## Shipping it: the 4 GB ceiling

TR's 5.3 GB set will never fit a RomFS CIA (makerom's image and the card's FAT32 stop at 4 GB), so it
ships as a **program-only CIA (~2 MB) plus the data on the card** — the engine already reads
`sdmc:/3ds/mw3ds/data` when the CIA packed no data. `dc74d1be` adds `build_cia.py --data <dir>`, and
`7ba3049c` adds `tools/build/build_tr_sdmc.py`: packs the program alone and stages `out/tr` under a
`3ds/` folder for the card with hard links where the disk allows, so nothing copies 5.3 GB twice.

    python tools/build/build_tr_sdmc.py            # build/mw3ds-tr-sdmc.cia + build/tr-sdmc/3ds to put on the card

### Reading it fast: the packed set

Staged file by file, that folder is why the card install lags where a RomFS CIA does not: 84,000 cells,
textures, sounds and book pages are a fresh FAT open each, on a card whose free space is fragmented.
`tools/build/pack_sdmc.py` packs the same names and the same bytes into three big files plus an index —
a part stays under 2 GB because devkitARM's stdio positions are a 32-bit `long` — and
`source/platform/dataarchive.cpp` answers `fopen` and `stat` for a packed folder out of that index:
each entry reaches its reader as a cookie stream (the trick `zfile.cpp` uses for compressed files), so a
caller's `fseek(f, 0, SEEK_SET)`, `fseek(f, -4, SEEK_END)` and `ftell(f)` see the entry rather than the
image holding it and no reader in the engine had to change. Reads go through handles opened on the parts
once at boot, buffered 64 KB. `build_tr_sdmc.py` stages that form by default (`--loose` stages the tree,
and a folder without an index is still read the old way), so the copy to the card is minutes instead of an
hour and lands unfragmented by construction.

## Proof so far (native PC test build, data `out/tr`)

- **All-cells sweep** (`tools/test/visit_all.py`, 12,528 cells — every grid square and every reachable
  interior, biggest cells first): **0 FAIL** — nothing crashes, nothing fails to load, no cell is
  unreachable; 92% fully clean. The 7.9% warnings are dominated by the teleport-arrival settling
  artifact (4.4%, identical on vanilla data) and actor placement nags (below/above floor — vanilla
  carries the same rate below floor, so it's an engine-wide floor-probe strictness, not TR sloppiness).
  TR-specific only: 88 items "can't be aimed at" (collision boxes), 39 scripts calling
  `getwindspeed`/`getarmortype` (unsupported), and the emulator-side linear refusals around the biggest
  grid cells. Tightest linear spots: the far-south water grids `ext 6-8, m49..m52` — first targets for
  an emulator spot pass.
- The seven script functions the sweep's warnings named (`getwindspeed`, `getarmortype`, `skipanim`,
  `getpcjumping`, `getpcrunning`, `samefaction`, `gethello`) now run, with OpenMW's readings (`c9d0757c`):
  the re-run of those cells logs zero unsupported-function warnings, and `probe-fns` asserts the values —
  wind 0 indoors / blowing outside, armor slot −1 bare and 2 heavy in an iron cuirass, faction and
  movement modes. The vanilla suite re-ran clean: 830/1191 against the 828/1189 record, no case lost
  (the two new passes are `probe-fns` and `tr-cities`).
- `tr-smoke`: **PASS** in the emulator — Okur's House (a TR-only interior) loads with 0 missing textures,
  Okur speaks his TR dialogue line, its topics learn, the run saves clean, and the log closes with the
  console-only `exit: shut down`.
- `tr-cities`: Hlerynhul, Akamora and Hlan Oek walked (the mainland's three biggest settlements by cell
  count), PC and Azahar, no crashes; one real measurement: neighbour grid cell `ext_0_m45` (68 batches,
  960 KB) fills the emulator's linear pool, is refused, retried and recovered under the memory-low
  handshake — graceful, but the first hard number for "towns are bigger than the pool".
- `tr-outside`: Hla Oad's cell loads and renders; its one open warning (NPC `ilfhild` stuck travelling)
  led to the engine fix that gives a walker stuck on one grid hop a route without it — re-run on the
  emulator with that build, `tr-smoke`, `tr-outside` and the town walk are warning-free, `ilfhild` walks.
- `d0239006`: the PC verdict stopped charging cases for the console-only `exit: shut down` line, which the
  TR smoke tests (ending in `QUIT`) could otherwise never show.

## Open items

1. Emulator spot pass over `ext 6-8, m49..m52` and the towns, where the linear pool actually refuses
   cells: decide the partial-commit question (keep the batches that fit, stream the rest later).
   Measured (`tr-southwaters`, the seven tightest grids from the sweep, Azahar --fast): every hop landed,
   the run shut down clean; two actor-pass `out of linear memory` refusals left `ext_7_m50` and `ext_6_m51`
   unloaded for the whole visit (the retry reloads the cell only on the next arrival); the memory-low
   handshake brought free linear back from 0 to 22-26 MB between hops; and under the same pressure 21
   texture *reads* failed — files present, allocation refused; the cache's retry (up to 3, or once 2 MB
   frees) is the recovery path. Watchdog frames: 4-21 s main-thread stalls "at texture read".
2. Offline repair pass for actor placement (floor-snap sunk/floating actors, pathgrid links a body
   can't fit through) — worth doing for vanilla data at the same rates, not TR alone.
3. Console-side proof: the ARM CIA from this data, the staged 5.3 GB card install, and the two TR cases
   on hardware.
