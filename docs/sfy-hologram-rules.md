# What the Satisfactory hologram allows

Legality is what the build gun's hologram does or allows, and nothing else. A
community blueprint is not evidence of it: a `.sbp` can hold clipped geometry, a
hacked save or a build from an older game version, so a number taken out of one
says only that *something* once produced it. Every rule in
`src/flab2bp/sfy/data/hologram_rules.json` is read out of the machine code of the
validator the hologram itself runs, by `scripts/sfy_native_rules.py` driving
`tools/sfy-native`'s `disasm` mode.

This file is the map that extraction was made from: which function decides what,
which members it uses, and — for a belt — where the spline that gets validated
comes from. Line numbers are into `CommunityResources/Headers.zip`, under
`Source/FactoryGame/Public/`.

## How a placement is refused

Nothing returns an error. `AFGHologram::CheckValidPlacement` and its overrides
add a *construct disqualifier* — a `UFGConstructDisqualifier` subclass — to
`mConstructDisqualifiers` (`Hologram/FGHologram.h:394`), and the build gun
refuses while that list is non-empty. So a rule is found by looking for the
`AddConstructDisqualifier` call and reading the branch that skips it.

Two rules turn out not to work that way at all, which matters more than it
sounds: the grid snap **moves** a hologram rather than refusing it, and a
conveyor lift's height is **clamped** into its legal range rather than checked.
Neither can produce an illegal value, and neither has a disqualifier.

## Conveyor belts — `Hologram/FGConveyorBeltHologram.h`

| Declaration | Line | Reads | Rule |
| --- | --- | --- | --- |
| `CheckValidPlacement` | 67 | — | entry point; tail-jumps to `ValidateConveyorBelt` |
| `ValidateConveyorBelt` | 72 | `mMaxSplineLength`, `mSplineComponent`, `mBuildModeCurve`, `mSnappedConnectionComponents`, `mUpgradedConveyorBelt` | `belt.max_length` |
| `ValidateIncline` | 103 | `mMaxIncline`, `mSplineData` | `belt.incline` |
| `ValidateMinLength` | 104 | `mMeshLength`, `mSplineData` | `belt.min_length` |
| `ValidateCurvature` | 105 | `mBendRadius`, `mSplineComponent` | `belt.curvature` |
| `UpdateClearanceData` | 84 | `mMaxSplineLength`, `mSplineComponent`, `mSplineData` | `belt.clearance` |
| `SetupSnappedConnectionDirections` | 79 | `mConnectionComponents`, `mSnappedWallPassthrough` | `belt.snap_directions` |
| `GenerateAndUpdateSpline` | 87 | `mUpgradedConveyorBelt` | construction |
| `AutoRouteSpline` | 97 | `mBendRadius`, `mMaxSplineLength`, `mSplineData` | construction |
| `UpdateSplineComponent` | 83 | `mConnectionComponents` | construction |

The members the rules turn on are `mBendRadius` (169), `mMaxSplineLength` (173,
`= 5600.1f` in the header), `mMaxIncline` (177) and `mMeshLength` (207). The
first three are `EditDefaultsOnly` properties whose values `tools/sfy-native`
read out of the constructor; `mMeshLength` has no initialiser at all and is
cached from the default buildable's mesh in `BeginPlay`, so it is not in
`native.json` and the minimum-length floor follows the belt mark.

`ValidateConveyorBelt` runs the four in this order, each with its own
disqualifier:

1. `GetSplineLength() > mMaxSplineLength` → `UFGCDConveyorTooLong`
2. `!ValidateMinLength()` → `UFGCDConveyorTooShort`
3. `!ValidateIncline()` → `UFGCDConveyorTooSteep`
4. `IsCurrentBuildMode(mBuildModeCurve) && !ValidateCurvature()` →
   `UFGCDConveyorInvalidShape`

and then, for each of the two snapped connections, refuses with
`UFGCDInvalidPlacement` if the connection already has something on it.

### Where the spline comes from, and where `mBendRadius` enters

`mSplineData` (`Hologram/FGSplineHologram.h:68`) is the list of
`FSplinePointData` the hologram replicates; `mSplineComponent` (63) is the
`USplineComponent` built from it. Two rules read the point list directly
(`ValidateIncline`, `ValidateMinLength`) and one reads the curve
(`ValidateCurvature`), which is why a belt can pass the incline test on its
chords and still fail on curvature.

`GenerateAndUpdateSpline` → `AutoRouteSpline` is what fills `mSplineData` when
the game routes the belt itself, and `mBendRadius` enters **there** as the radius
of the arcs it lays: it is handed to `FSplineUtils::Build90DegreeSpline2D` and
`FSplineUtils::BuildBendStraightBendSpline2D`, and a route those cannot build
adds `UFGCDConveyorInvalidShape` directly. `AutoRouteSpline` also reads
`mMaxSplineLength`, but only to clamp its leg lengths with a `minsd` — not as a
bound it refuses.

So `mBendRadius` is two things, and neither is "the tightest legal turn":

- **construction** — the radius the auto-router builds its bends on;
- **validation** — the input to `ValidateCurvature`'s actual floor,
  `mBendRadius * 1.5 - 15`, which is **283.5 cm** at the shipped 199.0.

Validation is the binding one, and it binds only in the curve build mode. See
the `belt.curvature` rule for the instructions.

## Conveyor lifts — `Hologram/FGConveyorLiftHologram.h`

| Declaration | Line | Reads | Rule |
| --- | --- | --- | --- |
| `CheckValidPlacement` | 62 | `mActivePointIdx`, `mSnappedConnectionComponents`, `mUpgradedConveyorLift` | `lift.placement` |
| `UpdateTopTransform` | 65 | `mStepHeight`, `mMinimumHeight`, `mMaximumHeight`, `mMinimumHeightWithVerticalConnection` | `lift.height_range`, `lift.step` |
| `UpdateClearance` | 70 | `mMeshHeight`, `mSnappedPassthroughs` | `lift.clearance` |
| `GetClearanceData` | 38 | `mClearance` | hands out the one box |
| `GetRotationStep` | 54 | `mActivePointIdx`, `mSnappedConnectionComponents`, `mSnappedBuilding` | corroborates `buildable.rotation_step` |
| `IsValidHitResult` | 24 | `mActivePointIdx` | — |
| `BeginPlay` | 20 | all four heights, `mMeshHeight` | where the heights come from |

The four height members (107–110) have no initialisers: `BeginPlay` computes
them from the lift buildable's mesh height H, which `tools/sfy-native` read as
`mMinimumHeight = H*2`, `mMaximumHeight = H*24` and
`mMinimumHeightWithVerticalConnection = H - 50` (`native.json`, and
`LIFT_HEIGHT_FORMULAS` in `scripts/sfy_registry.py`). `mStepHeight` is the one
stored constant, 100.0.

`UpdateTopTransform` then *clamps* the wanted height between the minimum — the
vertical-connection one when the lift meets a passthrough, the ordinary one
otherwise — and `mMaximumHeight`. There is no disqualifier because there is
nothing to refuse. Note what is **not** there: no instruction in that function
rounds a free height to a multiple of `mStepHeight`, so "every legal height is a
whole number of steps" is arithmetic on the `BeginPlay` values rather than a rule
read from the binary. `lift.step` says so and stays `partial`.

## Pipelines — `Hologram/FGPipelineHologram.h`

| Declaration | Line | Reads | Rule |
| --- | --- | --- | --- |
| `CheckValidPlacement` | 66 | — | entry point; tail-jumps to `ValidatePipeline` |
| `ValidatePipeline` | 70 | `mMaxSplineLength`, `mSplineComponent`, `mBuildStep`, `mUpgradedPipeline` | `pipe.max_length` |
| `ValidateMinLength` | 137 | `mMeshLength`, `mSplineData` | `pipe.min_length` |
| `ValidateCurvatureAndReturnFaultyPosition` | 145 | `mMinBendRadius`, `mSplineComponent` | `pipe.curvature` |
| `ValidateFluidRequirements` | 151 | `mSnappedConnectionComponents` | `pipe.fluid_requirements` |
| `UpdateClearanceData` | 82 | `mMaxSplineLength`, `mSplineComponent`, `mSplineData` | — |
| `AutoRouteSpline` and friends | 90–134 | `mBendRadius`, `mBendRadius2D`, `mMinBendRadius` | construction |

The header states three of the four limits outright: `mBendRadius2D = 199.0`
(198), `mMinBendRadius = 75` (202) and `mMaxSplineLength = 5600.1f` (206).
`mBendRadius` (194) has no initialiser and comes from the hologram Blueprint.

The split matters the same way it does for belts. `mBendRadius` and
`mBendRadius2D` are what `AutoRouteSpline`, `Auto2DRouteSpline` and the rest
build their bends on; **`mMinBendRadius` is the only one anything compares
against**, and the floor is `mMinBendRadius * 1.05`, 78.75 cm. Unlike the belt,
the pipe normalises its tangents in 3D, so a climb counts as curvature and there
is no separate incline rule for pipes.

`ValidatePipeline` runs length, minimum length, fluid requirements and curvature,
with `UFGCDPipeTooLong`, `UFGCDPipeTooShort`, `UFGCDPipeFluidTypeMismatch` and
`UFGCDPipeInvalidShape`; the curvature check only once `mBuildStep` is non-zero.
`ValidateFluidRequirements` is now read to its end: it compares the two ends'
`GetFluidDescriptor()` classes for **identity** and returns "legal" whenever
either end has no fluid committed, is not a `UFGPipeConnectionComponent`, or is
`FCD_SNAP_ONLY`. So a pipe from a carrying network into an empty one is legal;
two different fluids are not. The header also declares two constants nothing else
mentions: `MINIMUM_PIPE_CLEARANCE` and `MINIMUM_HOLOGRAM_LENGTH` (154–155), both
off `FHologramPathingGrid::PATH_GRID_CELL_SIZE`, which is 100
(`Hologram/HologramHelpers.h:464`).

## Every hologram — `Hologram/FGBuildableHologram.h`, `Hologram/FGHologram.h`

| Declaration | Line | Reads | Rule |
| --- | --- | --- | --- |
| `AFGHologram::CheckClearance` | — | `mConstructDisqualifiers` | `buildable.clearance` |
| `AFGHologram::TestClearanceOverlap` | FGHologram.h:545 | — | where the box test lives |
| `AFGHologram::GetClearanceData` | FGHologram.h:365 | `mClearanceData` (674) | — |
| `AFGBuildableHologram::CheckValidPlacement` | 262 | `mLegs` | base entry point |
| `AFGBuildableHologram::GetRotationStep` | 263 | `mDidSnapDuetoClearance`, `mSnappedAttachmentPoint`, `mSnapToGuideLines`, `mSnappedBuilding`, `mUseGradualFoundationRotations` | `buildable.rotation_step` |
| `AFGBuildableHologram::CheckValidFloor` | 291 | `mMaxPlacementFloorAngle`, `mNeedsValidFloor` | — |
| `AFGBuildableHologram::SnapToFloor` / `SnapToWall` / `SnapToFoundationSide` | — | `mGridSnapSize` (445) | `buildable.grid_snap` |

`mGridSnapSize` is 100.0 by default and three classes override it to 50.0 in
their Blueprints (both free-standing power poles and the street light);
`registry.json` carries the overrides per buildable. All three snap functions do
the same thing with it — hand it to `FHologramHelpers::SnapToFloor` and let the
callee round — so the grid is a *snap*, not a refusal.

The rotation step is not one number, and the ladder reads the opposite way
round from what the entry branch alone suggested. `AFGBuildableHologram::
GetRotationStep` is a leaf with no `.pdata` entry, so it is bounded by the
PDB's procedure record (77 bytes); all four returns then decode:

| Condition | Step |
| --- | --- |
| `mDidSnapDuetoClearance`, or the unnamed byte at `+5D8h` | 90° |
| on an attachment point with `mSnapToGuideLines` | 10° |
| `mSnappedBuilding` is **null** — not snapped to a building | **0°** (free) |
| snapped, with `mUseGradualFoundationRotations` | 45° |
| snapped, without it | 90° |

0° for the unsnapped case is the base class's own answer too:
`AFGHologram::GetRotationStep` at `0x13b010` is `xor eax, eax; ret`. Subclasses
override freely — `AFGConveyorLiftHologram::GetRotationStep` repeats the same 90
(`0xa7c1a2`) and 0 (`0xa7c19a`), while others return 180, 15, 5 or 1 — so 90 is
the *snapped buildable* default rather than a universal constant.
`registry.json`'s `hologram_rotation_step_deg` is still tagged a project
constant; `buildable.rotation_step` is the evidence that 90 is the game's own
default for that case, and re-sourcing that limit is a separate change.

`FGFactoryHologram.h` adds nothing (it is a two-line subclass);
`FGFactoryBuildingHologram.h` overrides `CheckValidPlacement` and
`CheckValidFloor` for foundations and walls, which the placer does not use yet.

## What each rule does with the number: `effect`

A rule's `status` says how well it was read; its `effect` says what the hologram
*does*, and that is the field a validator has to read before it refuses
anything. Four values:

| `effect` | what the instructions show | what a placer does |
| --- | --- | --- |
| `refuse` | a validation disqualifies the hologram | stay inside the bound, or the build gun says no |
| `clamp` | the value is forced into range | any value is buildable; the game moves it |
| `snap` | the value is quantised or aligned | any value is buildable; the game moves it |
| `none` | nothing in the instructions read enforces it | treat the number as known-good practice, not a bound |

`none` is only allowed beside a `partial` or `unextractable` status —
`flab2bp.sfy.rules.load_rules` refuses it on an `extracted` rule, because a
branch that was read says what it does. The shipped seventeen:

| `effect` | rules |
| --- | --- |
| `refuse` | `belt.curvature`, `belt.incline`, `belt.min_length`, `belt.max_length`, `pipe.min_length`, `pipe.curvature`, `pipe.max_length`, `pipe.fluid_requirements`, `lift.placement`, `buildable.clearance` |
| `clamp` | `lift.height_range` |
| `snap` | `belt.snap_directions`, `buildable.grid_snap`, `buildable.rotation_step` |
| `none` | `belt.clearance`, `lift.step`, `lift.clearance` |

Two of those deserve their own sentence. `buildable.clearance` is `partial` —
the box-against-box test is in `AFGHologram::TestClearanceOverlap`, which was
not read — but what the function that *was* read does with the answer is
`AddUnique` into `mConstructDisqualifiers`, so the effect is a refusal even
though the geometry is not known. `buildable.grid_snap` is the mirror image: the
rounding is in `FHologramHelpers::SnapToFloor` and the extraction only sees the
member handed to it, so the amount is unknown while the effect — a snap, not a
refusal — is not.

`registry.json` copies this field: `provenance.limits[key].governed_by` is
`{"rule": <id>, "effect": <effect>}`, and the merge refuses to write a copy that
disagrees with the rule. That replaced an `enforced_by` field which claimed the
grid, the rotation step, the lift heights and the lift step were all *enforced*,
when the rules behind them clamp and snap.

`lift.step` governs nothing: a rule whose effect is `none` does nothing to the
number, so `lift_step_cm` is *ungoverned* in `registry.json` and carries the
reason "AFGConveyorLiftHologram compares mStepHeight (lift.step evidence) but
never quantises a height to it; the multiple is this project's own stricter rule
(spec section 10)". Its source stays `binary` — the constructor value is real
game data; what is not game data is the claim that a lift's height has to be a
multiple of it.

## What was extracted, and what was not

`hologram_rules.json` carries seventeen rules; twelve are `extracted` and five
`partial`. A `partial` rule is a **bound the placer must not assume it knows** —
its `comparison` names where the comparison actually is, and its
`interpretation` is a lead for the next extraction, not a constraint.

The two reasons a rule is only `partial`:

- **the comparison is in a callee** — `belt.clearance`
  (`AFGBuildableConveyorBelt::CreateClearanceData`), `buildable.clearance`
  (`AFGHologram::TestClearanceOverlap`), `buildable.grid_snap`
  (`FHologramHelpers::SnapToFloor`), `lift.clearance`;
- **the rule may not exist** — `lift.step`, where nothing in
  `UpdateTopTransform` quantises a height to `mStepHeight`.

**Where a function ends is now game data in every case** (Task 5). Three
sources, in that order:

1. **`.pdata`**, including chained chunks. MSVC splits a function into several
   `RUNTIME_FUNCTION` entries and chains each chunk's `UNWIND_INFO` back to the
   primary; `sfy-native disasm` follows those chains, so `ValidateConveyorBelt`
   comes back as all 1065 bytes across three chunks instead of the 27 of its
   entry, `ValidatePipeline` as 1147 across five and
   `ValidateFluidRequirements` as 518 across four. That moved
   `belt.max_length`, `pipe.max_length` and `pipe.fluid_requirements` to
   `extracted`.
2. **The PDB's procedure record.** MSVC emits no `.pdata` entry for a leaf, and
   cutting at the first `ret` loses every later `return`:
   `AFGBuildableHologram::GetRotationStep` showed 43 of its 77 bytes and one of
   its four answers. `S_GPROC32`'s `len` states the length, which is game data
   of the same kind as the member offsets, so the leaf is bounded rather than
   guessed. That moved `buildable.rotation_step` to `extracted`.
3. **The first `ret`**, only when neither of those knows the function — and the
   rule says so.

The tool also resolves `call qword ptr [rip+K]` through the import directory
now, so the evidence lines name `USplineComponent::GetSplineLength` and
`GetTangentAtDistanceAlongSpline` where they used to show only an IAT address —
the interpretations that already used those names are reproducible from the
tool's own output.
