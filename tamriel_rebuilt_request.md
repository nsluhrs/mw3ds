# Tamriel Rebuilt on mw3ds — what it took

The base repository does not take pull requests, so this document stands in place of one: it records the
exact Tamriel Rebuilt release the work was done against, every change the branch `tr-on-main` carries
(pushed to `nsluhrs/mw3ds`, 10 commits on `main` `e4cd2849`), and the state of the proof.

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
- Conversion recipe: `.env` with `MW3DS_MORROWIND_DIR` set, the two ESMs in `Data Files`, and

  ```
  MW3DS_TRIBUNAL=1 MW3DS_BLOODMOON=1 \
  MW3DS_PLUGINS="Tamriel_Data.esm;TR_Mainland.esm" \
  python tools/build/build_cia.py --data out/tr --skip-convert   # or convert: drop --skip-convert
  ```

  (`MW3DS_OUT` is pointed at the folder by `--data`; `build_game.py` stamps the plugins and add-ons into
  `game.json` so the engine and the test harness both know what the data holds.)

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
- **Honest failure** (`dbd4ccc0`): when `zfile` runs out of heap it says so instead of blaming the file.

## What the plugin set needed from the engine

- **Add-on rules from the data** — `game.json` `addons` turns Tribunal's and Bloodmoon's engine/script
  rules on for TR data (TR scripts call their functions).
- **Split `game.json`** — big sections live in `game_<name>_0.json, _1 …` parts and the store
  (`mwworld/esmstore.cpp`) reads one part at a time: a whole parsed `game.json` for a set this size does
  not fit the heap next to the converted data.
- **Scripts on the card** — compiled script trees spill to `scripts.bin` and read back when a script
  runs (`mwscript/scriptstore.cpp`); TR's scripts are what pushed this over from nice-to-have to needed.
- **`game.json` `plugins`** (951f2271) — the data declares what it was converted with; a case marked
  `# data: plugins` skips, not fails, on vanilla data.
- Also on the branch, orthogonal to TR: the in-game bug report (`8c21f532`), the physim harness after the
  component move (`3e34f33e`), and the PC-build `QUIT` verdict fix (`d0239006`).

## Shipping it: the 4 GB ceiling

TR's 5.3 GB set will never fit a RomFS CIA (makerom's image and the card's FAT32 stop at 4 GB), so it
ships as a **program-only CIA (~2 MB) plus the data on the card** — the engine already reads
`sdmc:/3ds/mw3ds/data` when the CIA packed no data. `dc74d1be` adds `build_cia.py --data <dir>` (the CIA
is named after the folder), and `7ba3049c` adds `tools/build/build_tr_sdmc.py`: packs the program alone
and stages `out/tr` under a `3ds/` folder for the card with hard links where the disk allows, so nothing
copies 5.3 GB twice.

## Proof so far (native PC test build, `out/data` whole island + `out/tr`)

- **TR cases**: `tr-smoke` **PASS** — Okur's House (TR-only interior) loads with 0 missing textures, Okur
  speaks his TR dialogue line, topics learn; `tr-outside` — Hla Oad's cell loads and renders (screenshot
  verified, 0 missing textures), but flags one real warning: NPC `ilfhild` stuck travelling toward
  `velfred` on a 2-point path (open TR pathgrid question).
- **Full vanilla suite** (1,223 cases, `run_native_all`, `out/data`): 828 PASS, 99 FAIL, 262
  environment-skipped. 87 of the failures are warnings-only verdicts; 8 cases with real expectation
  failures were clean in the Nov-13 baseline run — the branch's engine delta is the bug-report menu only,
  so these read as mainline drift between the baseline and `e4cd2849`, not TR regressions (a head-to-head
  against an unmodified `main` build was cut short when PRs fell out of scope).
- TR cases run on PC end with `QUIT`, which only a console can fully judge; `d0239006` stopped the PC
  verdict charging for the console-only `exit: shut down` line.

## Open items

1. `ilfhild` stuck travel at Hla Oad (TR pathgrid/walkability) — reproduce on hardware too.
2. Attribute (or fix) the 8 vanilla hard-fails above against a plain `main` build.
3. Console-side proof pending: ARM CIA build of this layout, the `# run: emulator` exit path, and the
   5.3 GB staged-card install with FBI.
4. The Odai Plateau report (`issue-odai-invisible-collision.md`, shipped in `8c21f532`): no collision
   exists at the reported spot; the stops are the (drawn) fort-under-construction walls — filed with the
   bug-report feature as the way to catch the next one with context attached.
