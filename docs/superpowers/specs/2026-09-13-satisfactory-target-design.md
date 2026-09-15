# Satisfactory target: FactorioLab URL to stacked Satisfactory blueprints

Date: 2026-09-13
Status: design, awaiting user review
Owner: flab2bp

## 1. Goal

Turn a FactorioLab `/sfy/` URL into an ordered stack of Satisfactory blueprints
(`.sbp` + `.sbpcfg` pairs) that the player loads through the Blueprint Designer,
places one on top of the other, and connects at the edges. The stack runs the
FactorioLab flow exactly. Every emitted file decodes back to the placement that
produced it, and every placement passes a validator that is stricter than the
game.

The DSP side of this repository (`flab2bp.dsp`, `flab2bp.layout`) is the model:
FactorioLab's flow is authoritative, a refusal is a result, invalid builds
withhold the blueprint, and game constants come from the game install rather
than from hand-typed tables.

## 2. Decisions already taken

| Decision | Ruling |
|---|---|
| Where it lives | Same repository, new package `flab2bp.sfy`; `lab/`, `rates/`, `bench/` and the web shell are reused with a game parameter. The DSP layout engine is not reused: it is tile-and-sorter shaped. |
| Designer size limit | The factory is tiled into several designer-sized blueprints that stack vertically and auto-connect. Horizontal tiling is out of scope. |
| Designer size choice | Chosen in the web UI and by a CLI flag, not from the URL (FactorioLab does not carry it). Mk1 = 4x4x4 foundations = 32 m cube, Mk2 = 40 m, Mk3 = 48 m. Default Mk1. |
| Scope of logistics | Solid items on belts, splitters, mergers and lifts; fluids on pipes with junctions and pumps; power poles and wires inside every blueprint; foundation slabs under every level. |
| Legality | Extracted from the game (Docs.json plus cooked assets), never hand-typed. On top of the game's rules we forbid belts passing through belts even though the game allows it. Minor clipping within a few cm at a belt's own connection point is allowed. |
| Lifts into ports | A lift may connect directly to a machine or splitter/merger port, and its top may face a different direction than its bottom (a zero-footprint 90 or 180 degree turn). We allow this wherever the game's rules allow it, even though it is fiddly to do by hand. |
| Rates | FactorioLab's chosen flow is consumed, never re-derived (same rule as DSP). `SatisfactorySolver` is not used. |
| Strategies | Three layout strategies behind one protocol, raced like the DSP strategy race; the smallest valid stack wins. |
| Verification | The user paste-tests on a Windows machine and reports. This box cannot run the game. |

## 3. What the game install provides

`~/Satisfactory` is a Windows Epic build (build 493833 in the newest fixture).
`CommunityResources/` holds:

- `Docs/en-US.json` (UTF-16, 10.6 MB): 114 native classes, 872 `FGRecipe`,
  547 `FGBuildingDescriptor`, every buildable class with `mClearanceData`
  (500 of 539 carry boxes, typed hard or `CT_Soft`, with a relative
  transform), `mPowerConsumption`, `mManufacturingSpeed`, belt `mSpeed`
  (120..2400 items/min for Mk1..Mk6), lift `mMeshHeight`, foundation
  `mWidth/mDepth/mHeight`, designer `mDimensions` (4/5/6 foundations).
  Docs.json does NOT carry connection component positions
  (`mFactoryInputConnections` is empty).
- `Headers.zip`: the game's public C++ headers. The blueprint container,
  save custom versions and hologram limits are declared there.
- `FactoryGame.usmap`: the property mapping needed to read the cooked
  Blueprint assets in `FactoryGame/Content/Paks/*.pak|utoc|ucas`.
- `CustomVersions.json`: engine custom version GUIDs and values.

`dotnet 10` is installed, so a CUE4Parse extractor can read the paks.

## 4. Blueprint file format (decoded from headers and 49 community fixtures)

All integers are little-endian. `FString` is `int32 n` then `n` bytes
(ANSI, NUL-terminated) or, when `n < 0`, `-n` UTF-16 code units.

### 4.1 `.sbp` container (`FBlueprintSaveData`)

```
int32   header version        (FBlueprintHeader::Type, 2 = AddedUsedRecipes)
int32   SaveVersion           (FSaveCustomVersion, 52 and 60 seen)
int32   BuildVersion          (463028 .. 493833 seen)
FIntVector Dimensions         (foundations, e.g. 4,4,4)
TArray<FBlueprintItemAmount> Cost      { FObjectReferenceDisc{FString LevelName, FString PathName}; int32 Amount }
TArray<FObjectReferenceDisc> RecipeRefs
FSaveObjectVersionData        { uint32 version=0; FPackageFileVersion{int32 ue4, int32 ue5};
                                int32 licensee; FEngineVersion{u16 major,u16 minor,u16 patch,u32 changelist,FString branch};
                                FCustomVersionContainer (Optimized format) }
compressed chunk stream       (repeated until EOF)
```

Each chunk: `uint32 tag 0x9E2A83C1; int32 0x22222222; int64 max chunk 131072;
uint8 compression 3 (zlib); int64 compressed, int64 uncompressed, int64
compressed, int64 uncompressed; zlib bytes`. Concatenated decompressed payload:

```
int32 total size (payload length - 4)
int32 TOC size
int32 object count
object headers...
int32 blob size
int32 object count
per object: int32 size, bytes
```

Object header: `int32 type (0 component, 1 actor); FString class path;
FString level; FString instance path; int32 object flags;` then for actors
`int32 needTransform; FQuat (4 x float32); FVector pos (3 x float32);
FVector scale (3 x float32); int32 placedInLevel`, for components
`FString parent actor path`. Positions are cm; the biofuel fixture spans
-1200..1200 with foundations at z=50.

Object data: `FString level; FString path;` then UE tagged properties
(`StructProperty`, `ObjectProperty`, `IntProperty`, `ByteProperty`,
`FloatProperty`, `ArrayProperty`, `EnumProperty`, terminated by `None`)
followed by class-specific custom serialization. Properties seen in the
fixtures include `mSplineData` (array of `SplinePointData` with `Location`,
`ArriveTangent`, `LeaveTangent`), `mConnectedComponent` (object reference to
the peer connection component), `mCurrentRecipe`, `mBuiltWithRecipe`,
`mCustomizationData`, `mSavedDirections`, `mSortRules`, inventories.

Foundations, walls, belts, lifts, splitters, mergers, storage and machines are
all actors; connection, inventory, power and legs components are components
under them. Belts link to machine ports by object reference, so port
geometry only matters for the spline endpoints.

### 4.2 `.sbpcfg` (`FBlueprintRecord`)

`int32 ConfigVersion (6 = LatestVersion); FString BlueprintName; FString
BlueprintDescription; FPersistentGlobalIconId IconID; FLinearColor Color;
FString category; FString subcategory; int32 Priority; FPlayerInfoHandle
LastEditedBy` (exact field order and the icon/handle encodings are confirmed
byte-exactly against the fixtures during Milestone 1; the fixture is 101
bytes).

### 4.3 Guarantees

- The reader decodes every fixture in `tests/fixtures/sfy/` completely,
  including every property blob.
- The writer re-encodes every fixture byte-identically (the DSP gate).
- Every emitted blueprint decodes to the Placement that produced it.
- Save/build version numbers are writer parameters; the default is the
  newest fixture's pair, and the user's game build is confirmed at
  Milestone 1.

## 5. Game data and legality registry

### 5.1 Sources

- `Docs.json` (parsed once, cached): recipes, ingredients, products,
  duration, producers, variable power constants; per buildable: clearance
  boxes with type and transform, power, speed, belt/lift/pipe speeds, foundation
  sizes, designer dimensions, potential (overclock) limits and shard slots,
  production boost (somersloop) slots.
- `tools/sfy-extract/` (dotnet, CUE4Parse + `FactoryGame.usmap`): for every
  `Build_*_C` and its hologram: connection components (factory belt, pipe,
  power) with direction (`FCD_INPUT/OUTPUT/ANY/SNAP_ONLY`), relative
  transform, connector clearance and connection class; belt hologram
  `mBendRadius`, `mMaxSplineLength` (5600.1 cm), `mMaxIncline`; lift hologram
  `mStepHeight`, `mMinimumHeight`, `mMaximumHeight`,
  `mMinimumHeightWithVerticalConnection`, `mFirstStepYaw` semantics; pipe
  hologram `mBendRadius`, `mBendRadius2D` (199), `mMinBendRadius` (75), max
  length; wire `mMaxLength` per pole class and pole connection counts;
  hologram grid and rotation step; passthrough classes and thickness.
- Rules that the headers state only in code (which connection directions a
  lift may attach to, how the lift's top yaw is chosen) are transcribed into
  the registry with a citation to the header line, and every such rule gets an
  in-game confirmation entry in the Milestone 1 checklist.

### 5.2 Registry shape

`src/flab2bp/sfy/data/registry.json` is committed with a provenance stamp
(game build, Docs.json hash, extractor version). Python loads it into frozen
dataclasses (`Buildable`, `Port`, `ClearanceBox`, `BeltRules`, `LiftRules`,
`PipeRules`, `PowerRules`, `Designer`). No module outside `flab2bp.sfy`
imports the JSON directly.

### 5.3 Cross-checks (tests)

- Extracted port transforms are what the cooked asset states, and nothing
  accepts them against blueprint files: a blueprint says what somebody once
  built, so it can neither confirm nor refute a port position. (An earlier
  revision proposed a belt-endpoint oracle over the fixtures, on the DSP
  collider cross-validation's model; the test that did it,
  `tests/sfy/test_port_crosscheck.py`, is deleted.)
- Every recipe's producer class exists in the buildable registry.
- Every clearance box in Docs.json parses; unknown clearance types fail the
  test rather than defaulting.

## 6. FactorioLab side

- `lab/` gains a `Game` value (`dsp`, `sfy`) carried by `LabRequest`; the URL
  parser, dataset and hash URLs, `params.py`, CLI help and `web/jobs.py`
  validation use it instead of the hard-coded `dsp` id.
- FactorioLab's `sfy` dataset flags are `overclock`, `somersloop`,
  `resourcePurity`, `power`, `consumptionAsDrain`; the URL parser's
  `MachineSetting.overclock` and module settings already carry them.
- Flow capture and CSV parsing are unchanged; provenance rules are unchanged.

## 7. Rates and BuildSpec (sfy)

- Machine counts come from the flow. Fractional counts round up; the last
  machine of each recipe row is underclocked so the row's rate equals the
  flow exactly (no shards needed for underclock).
- URL overclock above 100 % is emitted as the machine's requested clock and
  reported as "N power shards to insert". URL somersloops are emitted as the
  production boost setting and reported the same way. Whether the game keeps
  these settings on paste without the items is confirmed at Milestone 2; if
  not, the report becomes the only carrier and the clock is capped at 100 %.
- Belt tier per run: floor = URL belt, ceiling = URL max belt; pipes likewise
  (300 and 600 m3/min). Runs whose demand exceeds the ceiling are split, and a
  demand above what one splitter/merger tree can carry is a refusal with the
  cause named.
- Power: every machine's consumption is summed per blueprint and reported;
  the blueprint contains poles and wires but no generators.

## 8. Layout model shared by all strategies

### 8.1 Space

Coordinates are cm. A blueprint is the designer volume: `8 m x dims` in
X, Y and Z. Each blueprint holds one or more internal levels. A level is a
concrete slab of 8 m x 1 m foundations at a pitch chosen from the tallest
object on that level plus belt clearance. Machines are placed on the
extracted hologram grid with yaws in 90 degree steps.

### 8.2 Objects

Machine (class, transform, recipe, clock, boost), belt run (polyline turned
into spline points with tangents, tier), lift (bottom transform, height,
direction, top yaw), splitter/merger, smart splitter where a mixed input must
be sorted, pipe run, pipe junction, pump, valve, power pole with wires, wall
power outlet, foundation, wall, and passthrough where a lift or pipe crosses a
slab.

### 8.3 Stacking contract

One wall of every blueprint is the bus wall. Every item that must cross from
blueprint N to N+1 owns a fixed column on that wall at a spacing of one belt
pitch. Lifts on those columns end exactly on the ceiling plane, and blueprint
N+1 places its receiving lifts or ports on the same columns at z = 0, so the
game's automatic connection joins them when N+1 is placed on N. External
inputs enter blueprint 1 on the opposite face at fixed spacings; final outputs
leave on a third face. The manifest lists every column, face and item.

Whether automatic connection accepts a lift-to-lift or lift-to-port pairing at
the boundary is confirmed in game at Milestone 3. Fallback: a short belt stub
on each side of the boundary, which automatic connection is documented to
bridge.

### 8.4 Lifts as vertical turns

Belts are used wherever a belt works: a belt run is cheaper than a lift and
the game's bend radius allows gentle turns. A lift is required where a belt
cannot make the turn within its bend radius, where the run must change level,
or where a port faces the wrong way and there is no room for the belt's
turning circle. Because lifts may attach directly to a port and their top may
face any of the four directions, such a lift changes direction or level with
zero footprint. One example pattern (user's screenshot, 2026-09-13) is a
stack of floors where lifts drop through floor passthroughs straight into the
ports of machines and splitters on the level below, with no horizontal belt at
either end; it is an example, not a rule. The router treats a lift as an edge
whose endpoints carry independent yaws, subject to the extracted rules:
minimum height with a vertical connection, step height multiples, maximum
height, and which connection directions accept a lift. Strategies may place
machines so that ports sit under bus columns or under the row above when that
removes a belt run, and may equally keep everything on one level with belts
when the turns fit.

## 9. Strategies

All strategies implement one `LayoutStrategy` protocol
(`lay_out(spec, designer, budget) -> Placement | refusal`), run under the
existing deadline rule, and are raced; the smallest valid stack wins (fewest
blueprints, then total occupied volume, then belt length).

1. **Manifold rows.** Each recipe row is N identical machines fed by a splitter
   chain and drained by a merger chain along the row. Levels are a 1D packing of
   rows; blueprints are a 1D packing of levels. Trunk belts between rows, the
   bus wall and the external faces are routed on a 1 m lattice. Rows longer than
   the wall are split. **The levels sentence is M3's, not M2's**: getting a belt
   from a row on one level to a row on another is a lift, and M2 ships no lifts,
   so what M2 lays out is a single level of rows in one designer (R10, and
   section 14 for what that cost).
2. **Grid-routed placement.** Machines are packed per level on the hologram
   grid (greedy then CP-SAT rectangle packing on integer grid cells), every belt
   is routed by a 3D orthogonal router on the lattice with lifts as vertical
   edges and lift-turns at ports.
3. **Continuous CP-SAT.** One model per blueprint: machine positions as integer
   grid multiples, 3D no-overlap on hard clearance boxes, belt length bounds,
   port-to-port distance bounds; spline routing as a post-pass with collision
   checks feeding no-good cuts back into the model. This is the experiment that
   tests whether CP-SAT packing and routing is usable at Satisfactory's scale.

Strategy 1 ships first because it is the one players build by hand and the one
most likely to pass the in-game gates early.

## 10. Validation (our gate, stricter than the game)

**Legality is what the build gun's hologram allows, and a blueprint corpus is
never evidence of it.** A `.sbp` can hold clipped geometry, a hacked save or a
build from an older game version, so a number measured out of one says only that
something once produced it — it is not a source, not a cross-check and not
evidence, for a limit or for any other game datum. What the game refuses is read
out of the validators the hologram itself runs, into
`src/flab2bp/sfy/data/hologram_rules.json`, and every limit in `registry.json`
names the rule that governs it and what that rule does with it
(`provenance.limits[key].governed_by`, `{rule, effect}` with the effect copied
from the rule — `refuse`, `clamp`, `snap`, `none` or `compute`, the last being a
function that is no validation at all: it works out a value the game then uses,
and turns no placement away) or says why no rule governs
it. Only `refuse` turns a placement away. Each rule is `extracted`, `partial` or
`unextractable`: a
`partial` rule is a bound the validator must not assume it knows, and this gate
may not invent one in its place. The first consequence is concrete —
`mBendRadius` (199 cm) is the radius the hologram lays its own arcs on, while
`AFGConveyorBeltHologram::ValidateCurvature` refuses a horizontal radius below
`mBendRadius * 1.5 - 15` = 283.5 cm, and only in the curve build mode. See
`docs/sfy-hologram-rules.md`.

- Hard clearance boxes never intersect; soft boxes may intersect only soft
  boxes.
- Belt clearance capsules (built from the spline like the game's
  `CreateClearanceData`) never intersect other belts or hard boxes, except
  within a small tolerance (5 cm) at the belt's own connection points. That
  tolerance is ours, covering float error where a belt end sits exactly on a
  port; it is not a game rule and is not derived from the corpus. Belts
  through belts are refused.
- Per belt run: length <= max spline length, incline <= max incline, and
  horizontal radius of curvature >= the `belt.curvature` floor (not
  `mBendRadius`). Per lift: height within min/max and the vertical-connection
  minimum when attached to a port; the step multiple is our own stricter rule,
  because `lift.step` is `partial` and no quantisation was found in the game.
  `lift_step_cm` is therefore *ungoverned* in the registry and says so.
- Every port is connected exactly once with matching direction; every net's
  throughput <= its tier; pipes: flow <= tier and head lift within pump limits.
- Wires <= max length and pole connection counts respected; every machine is
  on a powered circuit that reaches the wall outlet.
- Everything is inside the designer volume; a slab lies under every machine
  foot; passthroughs exist wherever a lift or pipe crosses a slab.
- The stacking contract holds: bus columns match across consecutive
  blueprints, external faces carry exactly the flow's inputs and outputs.
- Round trip: `decode(encode(placement)) == placement`.

A validation failure withholds the blueprint and lists the errors, matching
the DSP CLI and web behaviour.

## 11. Web UI and CLI

- CLI: `flab2bp <sfy url> --designer mk1|mk2|mk3 -o DIR` writes the stack and
  manifest; `--zip` writes one archive.
- Web: game selector, designer size selector, stack viewer (existing three.js
  viewer with box geometry and belt ribbons), per-blueprint and zip download,
  the manifest rendered as placement instructions.

## 12. Verification and milestones

Automated gates:

- Format: every fixture decodes; every fixture re-encodes byte-identically
  (`tests/sfy/test_codec.py::test_full_file_round_trip_is_byte_identical`, over
  every file `tests/sfy/data/fixtures` lists).
- Registry: the merge is reproducible — `test_registry.py`'s drift test
  (`test_the_committed_registry_is_what_the_merge_produces`) re-runs it into a
  temporary file and diffs — and the registry's own consistency tests in
  `tests/sfy/test_registry.py` hold every limit, port, direction, flow and asset
  path to the game source it names. There is no blueprint-corpus cross-check:
  the corpus is a format fixture and never evidence for a game fact.
- Corpus: a set of FactorioLab `sfy` URLs from early to late tiers; the gate
  passes when every URL yields a valid stack within budget or a refusal with a
  named cause.

In-game checkpoints (user):

1. A hand-built one-constructor blueprint with a belt and a pole loads and
   places.
2. A single-blueprint chain runs at the flow's rate.
3. A two-blueprint stack auto-connects when placed.

Milestones, each with its own plan:

- M1 Format and registry: codec, extractor, fixtures, checkpoint 1.
- M2 Lab game parameter, spec, rates, manifold rows in one blueprint,
  validator, checkpoint 2.
- M3 Stacking contract, lifts and passthroughs, manifest, zip, web UI,
  checkpoint 3.
- M4 Fluids, power and foundations completed across strategies.
- M5 Grid-routed and continuous CP-SAT strategies, the race, the bench.

## 13. Out of scope

Horizontal tiling, trains and drones, generators, resource extraction,
customization (paint), signs, and any mod content.

## 14. Status after M2 (2026-09-15)

What M2 actually shipped, against what this document asked for. Every number
below is a measurement out of the branch -- the corpus audit's own report, or the
strategy's own refusal text -- and not a figure written here by hand.

**R10: M2 is single-level.** Section 9.1's "levels are a 1D packing of rows"
needs vertical transport between rows on different levels, which is a lift, and
`section 8.4`'s lifts are M3. So `ManifoldRows` lays one level of rows in one
Blueprint Designer and refuses a chain that does not fit it. That ruling is what
most of the corpus refusals below are.

**How deep a row band is.** Measured over the 28 rows the corpus builds, one
row's own band is 1600 to 2400 cm deep -- the machine's hard box, its splitter
and merger chains, and the feeder belts between them, all from `registry.json`.
Add the two wall margins a belt turns in at (300 cm each) and a one-row build is
2200 cm. Add a second row and the 400 cm gap a trunk turns out of one row and
into the next and it is 4200 cm, and the designers are 3200 (Mk1), 4000 (Mk2) and
4800 (Mk3) cm deep: **only a Mk3 takes a two-row build.** Two SIBLING rows of one
split group are cheaper, because no trunk turns between them and the gap is one
grid step: `concrete*60` is 3900 cm of band and builds in a Mk2.

**The corpus, at the head this note was written against.** 36 cells (12 entries x
3 marks): 5 CLEAN, 28 refused `rows exceed the designer depth`, 3 refused `fluids
are M4`. Both gates pass and no cell is off its pin;
`docs/superpowers/evidence/sfy-m2-audit-2026-09-14.md` is the table, cell by
cell, with the centimetres each depth refusal measured.

**The refusal inventory.** `flab2bp.sfy.layout.strategy.REFUSALS` is 24 named
causes and the only ones the strategy may raise -- `_refuse` raises `ValueError`
on anything else, and the mapping tables that turn a `RowError`, a
`CorridorError` or a `PowerError` into one are pinned by a test. Exactly two of
the 24 ever fire over the corpus, which is the honest reading of the list: it is
a vocabulary for the failures this build form CAN have, not a claim that they
happen. `docs/sfy-layout-model.md` says which bound each one comes from.

**Checkpoint 2.** Three `.sbp`/`.sbpcfg` pairs, all `iron-plate*60` in a Mk3,
each asking one question a paste test can answer: the plain two-row chain (does
it run at the flow's rate), the somersloop flow (does the same rate come out of
fewer machines), and the 250 % overclock. The last one settles what section 7
left open -- a blueprint writes each machine's saved potential but puts no Power
Shard in any slot, so if a machine pasted at 250 % shows 100 % the game does not
keep an overclock nothing paid for, and the `.sbpcfg` description becomes the
only carrier of the clock. The in-game report is still outstanding.

**Untouched by M2.** The stacking contract (section 8.3) and the bus wall
(section 9.1) are as this document left them: M2 emits one blueprint, so nothing
has yet had to stack, and no build has yet needed a bus.

**Two gaps in the extractor, for M3.** `HologramLimits` reads `mHologramClass`
without walking the class's supers, so a Mk2 or Mk3 power pole comes out with no
`grid_snap_cm` of its own and the pole placer falls back on the general hologram
grid. And `flab2bp.bench.sfy_corpus` still reaches `flab2bp.rates.CandidatePolicy`
for its policy type, which drags the DSP catalog -- and `Tier` -- into a
Satisfactory-only import; moving `Tier` out is a small M3 chore.
