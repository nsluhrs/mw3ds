# Save-relocated corpse is drawn at its spawn point, floating, and can't be talked to or looted

**Component:** saves / actor placement
**Severity:** gameplay-affecting (a corpse becomes permanently unreachable; the drawn body is a lie)
**Found in:** play test on Old 3DS hardware, `save_034.json` (West Gash Region, 2026-11-17)

## Symptoms

After loading `save_034.json`, a dead NPC (`hlormar wine-sot`) lies ~100 units above the
ground at his default spawn point, ~500 units from the player's start. Pressing A on the
visible body does nothing: no dialogue (he is dead, as expected) but also **no loot screen**,
even with the crosshair dead on the drawn body.

## Save evidence (`3ds/mw3ds/saves/save_034.json`, MWZ1-inflated)

Player start: `cell: ext_m3_3`, `feet: (-23568, 26428, 1257)`.

The NPC's saved reference state:

```json
{ "c": "ext_m3_3", "i": 0, "id": "hlormar wine-sot",
  "disp": -40, "talked": 1, "at": "ext_m4_3",
  "health": 0, "dead": 1, "died_at": 311.09, "bartered": 288.35, "fight": 30,
  "pos": [-25948.13, 36881.71, -34.14], "yaw": 75.66,
  "ai": { "type": 0, "start": 308.89, "repeat": 1 } }
```

He traded with the player at game time 288, turned hostile (disposition -40, `fight: 30`)
and died at 311. His saved logical position is 10.7 km north of the player, at z = -34.

## Ground truth

| Position | What's under it |
|---|---|
| Default spawn `(-23067, 26255, 1423)` (`out/cell_cache/data/ext_m3_3.json`, ref i=0) | floor at **1312** (foot floor 1329) — the ESM places him on a ledge; the drawn body at z=1423 floats ~100 units up |
| Saved pos `(-25948, 36882, -34)` | grid cell `ext_m4_4` (8192-unit squares): a **water cell** (`water_z = 0`) with **no collision floor within 6000 units below** — open sea |

Both measured with the engine's own `collisionFloor` / `collisionFootFloor` against the
converted cells (`out/data/cells/.../ext_m3_3.cel`, `ext_m4_4.cel`).

So the corpse's **logical position** (the sea) and its **drawn position** (the spawn ledge)
disagree by 10.7 km, and the drawn copy hangs ~100 units above the ground.

## Why each symptom happens

1. **No dialogue** — correct by design: `dialogueStart` (`source/dialogue.cpp:709`) returns
   false for `w.refs[ref].dead` ("Not with the dead").
2. **No loot screen either** — activating a corpse needs the crosshair on its pick box, and
   the box follows the *logical* position: `world.cpp:3538` caps a corpse's box at
   `boxMin[2] + kCorpsePickHeight` and `boxInReach` limits the range. The visible body is a
   drawn copy 10.7 km from that box, so A finds nothing.
3. **Drawn at the spawn point** — on load, `ext_m3_3`'s actors are created from the cell's
   actor list at their default positions (log: `cell: ext_m3_3.cel: ... 9 actors`).
   `World::applyCellState` (`source/save.cpp:523`) then applies the saved `pos` and the `at`
   relocation, but the redraw at the saved position only happens through `attachSpawned`,
   which runs `if (cells[ac].live` (`save.cpp:560`) — the target cell is not loaded yet at
   that moment, so the home cell's spawn-point copy keeps being drawn.
4. **Never falls onto the ledge it hovers over** — corpse gravity
   (`Session::actorGravity`, `source/combat.cpp:2292`) measures the floor under **`r.pos`**
   (the sea spot), finds none, and takes the "nothing at all below: the land isn't there,
   leave them be" branch (`combat.cpp:2353`). The drawn copy is not what gravity looks at.
5. **Nothing in `log.txt`** — the engine's monitor for exactly this desync,
   *"%s is drawn at %.0f %.0f but stands at %.0f %.0f"* (`source/session.cpp:3140`), skips
   dead actors: `Actor* a = r.dead || !r.visible() ? nullptr : w.actorOf(i)`
   (`session.cpp:3129`). A desynced corpse is invisible to the watchdog.

## Secondary findings

- **The `at` cell is wrong.** The saved position lies in grid cell `ext_m4_4`
  (x ∈ [-32768, -24576), y ∈ [32768, 40960)), but the save records `at: ext_m4_3`
  (y ∈ [24576, 32768)). Whatever recorded the relocation wrote the neighbouring cell;
  check the boundary-crossing bookkeeping that sets `r.at`.
- **The host physics probe can't read current cells.** `tools/test/physim` fails to build
  (`log.h:36` uses `threadGetCurrent()`, which the stub headers in `tools/test/physim/stub/`
  do not provide) and its `readCel` accepts only MWC1 v6–7 while the converter writes v10
  (`convert_cell.py:383`). Reproduced by compiling `collision.cpp` on Linux with a shim.
- **`hour: 959.09`** in the save root — the game hour should be in 0..24; worth a separate
  look.

## Reproduction

1. Pull `save_034.json` off the console (see README, "Pulling saves and logs off the
   console") and load it (emulator or hardware).
2. The player starts at `(-23568, 26428, 1257)` in the West Gash Region; `hlormar wine-sot`'s
   body is visible ~500 units away at his spawn point, floating ~100 units above the ground.
3. Press A with the crosshair on the body: nothing happens (no dialogue, no container).
4. The save's logical corpse position is `(-25948, 36882, -34)` in the sea, where the pick
   box actually is.

## Suggested fixes

1. `World::applyCellState` (`source/save.cpp:551-565`): when a ref takes an `at`
   relocation, detach or hide its home-cell actor copy until `attachSpawned` really runs at
   the target cell's load, so the stale spawn-point copy is never drawn.
2. `Session::monitorActors` (`source/session.cpp:3129`): include dead actors in the
   `drawn:` / `blocks:` checks so this desync is caught by the test harness and the
   watchdog.
3. Find why `at` recorded `ext_m4_3` for a position inside `ext_m4_4`.
4. Consider snapping corpses to the floor at their drawn position on load (the standing
   branch in `combat.cpp` deliberately skips the snap for `r.dead`), or at least running
   the floor check where the body is drawn.

## Artifacts

- `save_034.json` (177,246 B, MWZ1) and `log.txt` (230,013 B) from `sdmc:/3ds/mw3ds/`,
  pulled over Luma `ftpd` on `192.168.1.156:5000`.
- The log's last session shows the save loading cleanly (`save: loaded ... save_034.json,
  West Gash Region at -23568 26428 1257`, no skipped entries) and no dialogue attempts.
