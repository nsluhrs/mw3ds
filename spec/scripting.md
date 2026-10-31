# Scripting and the console

How a script line becomes something the engine runs, the console's rules, and how a new game starts its scripts.
OpenMW's code: `components/compiler` (scanner, parsers, `extensions0.cpp`), `apps/openmw/mwgui/console.cpp`,
`apps/openmw/mwscript` (`interpretercontext.cpp`, `globalscripts.cpp`, `*extensions.cpp`), `mwworld/worldimp.cpp`.

## Two compilers, one tree

| Where | What | Code |
|---|---|---|
| PC, at conversion | every script and dialogue result of the load order | `tools/convert/mwscript.py` |
| device, at run time | one console line | `source/scriptc.cpp` (a port of `mwscript.py`) |

Both build the same JSON tree (`mwscript.py`'s docstring has the shapes), which `game.cpp` turns into the interpreter's
nodes. The C++ function tables (`include/scriptc_tables.h`) are generated from `mwscript.py`'s, and
`tools/test/script_compile_check.py` compiles every script and dialogue result with both and compares the trees
(32,911 compiles with the expansions, 0 differences, 2026-10-31). Run it after changing either compiler.

`tools/test/script_gap.py` lists OpenMW's functions the engine does not run, and which master file uses each. None
is missing: every one Morrowind.esm uses, and the 21 Tribunal.esm and 23 Bloodmoon.esm uses that were the expansion
work list (script_gap, 2026-10-31).

## Tribunal and Bloodmoon

What the expansions' scripts ask for, and where this engine differs from OpenMW. The engine runs all of them
whatever the data is: `MW3DS_EXPANSIONS` decides what is converted, not what is compiled in. Tests `exp-script`
(the console running each family) and `exp-mournhold` (Mournhold's water in a real cell).

| Function | Does | Differs |
|---|---|---|
| `GetSquareRoot n` | the square root | |
| `GetInterior` | the cell the player is in is an interior | |
| `GetStat "Name"` | one stat by name, the same numbers `GetLevel`, `GetStrength` ... give | vanilla also writes it bare (`GetStat Level`), which our compiler reads as a variable: the variable's name is the stat |
| `GetPCSneaking` | the player sneaks | |
| `GetWeaponDrawn` / `GetWeaponType` | the weapon out; what the right hand holds, out or not (0 short blade .. 9 staff, -2 a lockpick, -3 a probe, -1 nothing or not a weapon) | |
| `GetPCinJail` / `GetPCTraveling` | the frame the session jailed the player, or moved them with a travel service | OpenMW resets both at the start of its frame, ours after that frame's scripts: the same one frame to notice |
| `GetChameleon` / `GetParalysis` | one magic effect's magnitude on them, whole: Chameleon's strength, how hard paralysed | OpenMW counts that one effect only, so a player made invisible outright still has a Chameleon of 0 (`GaenorScript` asks `GetChameleon == 1` and only ever fires on a magnitude-1 chameleon) |
| `EnableLevitation` / `DisableLevitation` | the Levitate effect lifts nothing while off | |
| `ModResistFire / Frost / Shock / Magicka / Poison / Paralysis n` | the resistance, with no duration | OpenMW's stays until cured; ours ticks its effects down, so these get a duration no playthrough runs out |
| `GetCollidingPC` / `GetCollidingActor` / `HurtCollidingActor n` | whoever's body overlaps this object's box, and health a second off them | OpenMW asks its physics system; ours takes the object's box and a body of the size projectiles aim at (34 x 135) |
| `GetStandingActor` | a living actor stands on this object | as `GetStandingPC` already did |
| `SetDelete 1 / 0` | the object is gone for good (or back) | OpenMW takes it out of the world; ours keeps its slot, hidden and inert, and remembers it so the cell does not give it back |
| `SetScale n` | its size | the collision box grows with it; the model is baked into the cell's geometry and keeps its converted size |
| `GetWaterLevel` / `SetWaterLevel n` / `ModWaterLevel n` | an interior's water (Mournhold's cranks) | the level a script set stays with the cell and comes back when it is read again, as it does in OpenMW |
| `PlaceItem "id" x y z zrot` / `PlaceItemCell "id", "cell" x y z zrot` | the object at that spot | a cell that is out of memory gets it when it is read, as a dropped item does |
| `ExplodeSpell "id"` | the spell lands at once, as when a bolt hits: what has an area reaches everyone in it | |
| `TurnMoonRed` / `TurnMoonWhite` | Masser's colour (Bloodmoon's omen) | OpenMW changes its picture; ours tints the one it has |
| `Face race facegen hair` | an NPC's new head | the record's race changes, so dialogue conditions and the next read of the cell follow; the model it stands in keeps its old head. One script asks, `"Tienius Delitian"->Face 0 0 -116` |
| `IsWerewolf` / `BecomeWerewolf` / `UndoWerewolf` | the werewolf form | the session's flag: what the form refuses, and the timer scripts that run it; not a creature's body |
| `GetWerewolfKills` | kills made in the form since the last change of form | |
| `SetWerewolfAcrobatics n` | the human's Acrobatics back | nothing to put back: ours never takes the skill away |

What these need out of the world is saved with it: `levitationDisabled`, `moonRed`, `waterLevels` (by cell),
`werewolfKills`, and the `pcJail` / `pcTraveling` flags the session sets (`no_levitation`, `moon_red`,
`water_levels`, `werewolf_kills` in the save). A deleted reference is saved as `deleted` next to its other changes,
and `Enable` does not bring it back.

## The console (Options > Console)

OpenMW's `Console::execute` and its `LineParser` with expressions allowed:

- One line at a time. It is compiled with the selected object's script locals, then run in that object's context: a
  call without `X->` acts on it, its locals are the line's variables.
- A line that starts with a number, `(`, `-`, `+`, a variable, `X.var`, or a function that returns a value is an
  expression: its value is printed, `%d` for a whole number and `%f` otherwise (`50`, `3.500000`). `X.var` prints by
  the type that object's script declares. Anything else is one statement (an instruction, `set ... to`).
- Block keywords (`if`, `while`, `begin`, `short` ...) are refused.
- Nothing selected: a call without `X->` to a function that needs an object is refused, "no implicit reference" (OpenMW
  throws the same). The functions that need none are those OpenMW registers without an explicit form, or whose opcode
  takes the reference as optional (Journal, StartScript, GetItemCount, PCJoinFaction ...): `NO_REF_NEEDED` in
  `mwscript.py`.
- Refused before anything runs: a name no table knows, and a function OpenMW has that the engine does not run
  ("not supported here"). OpenMW runs everything it compiles; this engine says so instead of half running a line.
- The object selected is the one under the crosshair when the menu was opened (OpenMW: a click on it).

Console commands the engine runs (OpenMW registers them for scripts too):

| Command | Does | Differs |
|---|---|---|
| `coc` / `CenterOnCell` | to a named exterior (a town's street) or an interior (where a door into it lands); "Cell X is not found" | |
| `coe` / `CenterOnExterior` | to the middle of that exterior cell, on the ground | |
| `tgm` / `ToggleGodMode` | "God Mode -> On / Off" | the player is kept alive; spells and fatigue still cost |
| `tai` / `ToggleAI` | actors stand still, packages and fights held; "AI -> On / Off" | |
| `sv` / `ShowVars` | the object's script variables; nothing selected: the globals, sorted, and running global scripts' | |
| `ori` / `OutputRefInfo` / `bc` | RefID, cell, coordinates, rotation, script | no content file, RefNum or model |
| `help` | the functions the engine runs | OpenMW lists every keyword |
| `FillMap` | every named exterior place on the map | |
| `FillJournal` | every quest's entries, every topic known | topic pages are not filled with every answer |

Not run (refused "not supported here"): collision, render and debug toggles (`tcl`, `tw`, `ts`, `tb`, `twf`, `tpg`,
`tcb`, `tcg`, `tm`, `tfow`, navmesh), `ssg`, `testcells`, `ra`, `fixme`, `user1`-`user4`, and the functions in
`script_gap.py`'s missing list.

The console screen itself is the 3DS's (bottom screen, the system keyboard): "differs (3DS UI, kept)".

## A new game

OpenMW's `World::startNewGame` (not skipping the menu): CharGenState 1, `main` and the SSCR start scripts added
(`GlobalScripts::addStartup`; Morrowind.esm has none), then the global scripts run five times before the first frame.
Main starts Startup, VampireCheck and CharGen; CharGen disables the controls, jumping, view switch, vanity mode,
fighting and magic, moves the player into the Imperial Prison Ship, sets the Bitter Coast cloudy
(`ChangeWeather "Bitter Coast Region" 1`), sets CharGenState to 10 and stops. The engine does the same
(`Session` new game); it used to set CharGen's results by hand, without the weather.

## Findings

- 2026-10-15: CharGen's weather never ran (the engine copied CharGen's other effects by hand). Test
  `chargen-start-script` (failed before, passes after).
- 2026-10-15: both compilers type `X.var` as a float, so `X.var / 2` divides as floats; OpenMW asks the remote
  script's declared type (a short divides as whole numbers). Not fixed: no vanilla script found depending on it yet.
- 2026-10-15: `Equip` on an NPC only gives the item; OpenMW equips it. No vanilla script calls `Equip`; an NPC's
  clothes and armour are baked into its meshes at conversion and its weapon is a record field, so wearing a new item
  at run time is a larger change. Not fixed.
- 2026-10-31: `GetPCinJail` and `GetPCTraveling` are not a state. OpenMW sets them when it jails the player or moves
  them with a travel service and resets both at the top of its next `World::update`, so a script has one frame to
  notice (the werewolf scripts use them to stand still while a journey or a cell change is happening). Reading them
  as "the player is in jail" would keep the scripts frozen for good.
- 2026-10-31: which console calls need `X->` comes out of OpenMW's registrations, not out of what the function is
  about: `extensions0.cpp` registers `getWaterLevel`, `getPCinJail`, `getSquareRoot`, `placeItem`, `turnMoonRed` ...
  with no explicit form at all (so they are only ever bare), and `isWerewolf`, `getWeaponDrawn`, `getStat`,
  `setDelete`, `setScale`, `getChameleon`, `getParalysis` and the rest with one (so bare needs an object in context,
  which the console has only when something is selected). `NO_REF_NEEDED` in `mwscript.py` is that first list.
