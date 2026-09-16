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

Most of what is here is a *validation*: code that turns a placement away, or
moves it. A good half of the rules are the other kind — code that **works out** a
value the game then writes, which this project has to reproduce rather than
enforce when it authors a blueprint. Those carry the effect `compute`:
`belt.cost`, `belt.straight_tangents`, `manufacturer.inventory_filters`,
`buildable.rotation_step`, the two clearance builders `belt.clearance` and
`lift.clearance`, the two that say where a conveyor lift's ends go —
`lift.connectors` and `lift.top_yaw` — and the two overclocking rules
`factory.potential` and `manufacturer.production_boost`.

Not every function here is a hologram's. Four of the rules are read out of the
**buildable** instead — a belt's clearance boxes, a lift's, where a lift's two
connections end up, and what a power shard or a somersloop does to a machine —
because that is where the game computes them; the file's name is about what it
is for, which is deciding what this project may author.

## How a placement is refused

Nothing returns an error. `AFGHologram::CheckValidPlacement` and its overrides
add a *construct disqualifier* — a `UFGConstructDisqualifier` subclass — to
`mConstructDisqualifiers` (`Hologram/FGHologram.h:394`), and the build gun
refuses while that list is non-empty. So a rule is found by looking for the
`AddConstructDisqualifier` call and reading the branch that skips it.

Some rules turn out not to work that way at all, which matters more than it
sounds: the grid snap **moves** a hologram rather than refusing it, and a
conveyor lift's height is **snapped** onto a multiple of the step and then
**clamped** into its legal range rather than checked. None of them can produce
an illegal value, and none has a disqualifier.

## Conveyor belts — `Hologram/FGConveyorBeltHologram.h`

| Declaration | Line | Reads | Rule |
| --- | --- | --- | --- |
| `CheckValidPlacement` | 67 | — | entry point; tail-jumps to `ValidateConveyorBelt` |
| `ValidateConveyorBelt` | 72 | `mMaxSplineLength`, `mSplineComponent`, `mBuildModeCurve`, `mSnappedConnectionComponents`, `mUpgradedConveyorBelt` | `belt.max_length` |
| `ValidateIncline` | 103 | `mMaxIncline`, `mSplineData` | `belt.incline` |
| `ValidateMinLength` | 104 | `mMeshLength`, `mSplineData` | `belt.min_length` |
| `ValidateCurvature` | 105 | `mBendRadius`, `mSplineComponent` | `belt.curvature` |
| `UpdateClearanceData` | 84 | `mMaxSplineLength`, `mSplineComponent`, `mSplineData` | `belt.clearance`, with `AFGBuildableConveyorBelt::CreateClearanceData` |
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

### Four details of `ValidateCurvature` that have to be copied exactly

The rule's `interpretation` now states them, because reproducing the loop from
the prose alone gets three of the four wrong:

- **The numerator is `step`.** `0xaa5300` sets `xmm14` to `length / n` and
  `0xaa54da` reloads it before `0xaa54de divsd xmm1, xmm0` — so the radius is the
  sample *spacing* over the angle, not the distance between the two sampled
  points.
- **`RoundToInt` is the doubling trick, and the doubling is load-bearing.**
  `addss xmm2,xmm2; addss 0.5; cvtss2si; sar esi,1` is UE's SSE form.
  `cvtss2si` rounds half to *even* under the default `MXCSR`, which alone would
  send 0.5 to 0; doubling first puts the tie on `2n + 0.5` instead of on `n`, so
  the shift back recovers `floor(n + 0.5)` — half **up**. A ceiling or a C cast
  gives a different sample count on exactly the lengths where it matters.
- **A straight pair passes by arithmetic, not by a guard.** `theta == 0` makes
  `divsd` yield `+inf`, and `0xaa54f9 jb` is not taken. There is no
  "skip a straight segment" branch to reproduce.
- **A vertical tangent is refused by this rule.** `0xaa53a4` compares the
  squared 2-D length against `1e-8` and, under it, `0xaa53ab` loads
  `FVector::ZeroVector` through the global at `0xee7288` (`movups` +
  `movsd [rax+10h]`, the three doubles) and uses it as the normalised tangent.
  The dot product is then 0, `acos(0)` is `PI/2`, and the radius comes out
  `step / (PI/2)` — about 32 cm at the 50 cm sampling, far inside the 283.5 cm
  floor. So `GetSafeNormal2D` zeroing `Z` means a climb does not *count* as
  curvature, **not** that a climbing sample is passed over: a belt going
  straight up is refused here, and `belt.incline` refuses it separately on the
  chord.

### What a straight run's tangents are — `belt.straight_tangents`

`AutoRouteSpline` (97) builds `mSplineData` through an `FSplineBuilder`:
`FSplineBuilder::Start(location, tangent)` for the first point, then one
`FSplineUtils::Build*` call per leg, each ending in `FSplineBuilder::AddSegment`.
For a straight run that is one `BuildStraightSpline2D`.

- **`Start`** (`0xb220b0`) normalises the tangent it is given and stores that
  **unit** vector into *both* of point 0's tangents — `ArriveTangent` at `+18h`
  and `LeaveTangent` at `+30h` of the 72-byte `FSplinePointData`.
- **`BuildStraightSpline2D`** (`0xafcac0`) takes the 3D distance to the new
  point, halves it, clamps it to **[50, 600] cm** (`0xafcf0d` `mulsd 0.5`,
  `0xafcf15` `minsd 600.0`, `0xafcf1d` `maxsd 50.0`), and multiplies the unit
  run direction by it. `BuildStraightSpline3D` uses the same three constants.
- **`AddSegment`** (`0xaf1e80`) rescales the **previous** point's `LeaveTangent`
  to the new tangent's length, keeping its direction, and writes the new point:
  `Location` from the caller, `ArriveTangent` the tangent it was given,
  `LeaveTangent` that tangent normalised.

So a straight run of length L along a unit `d` is exactly:

| Point | Location | ArriveTangent | LeaveTangent |
| --- | --- | --- | --- |
| 0 | start | `d` | `d * T` |
| 1 | end | `d * T` | `d` |

with `T = clamp(L / 2, 50, 600)`. A 400 cm belt is `(1, 200, 200, 1)`.
`flab2bp.sfy.templates.straight_spline` is that, and it is the only place the
shape is written.

**What this does not settle** is which way a belt leaves a port. The declaration
is `AutoRouteSpline(startConnectionPos, startConnectionNormal, endConnectionPos,
endConnectionNormal)`, so the router does take the connection's facing, but the
vector it hands `Start` is built by inlined vector code (`0xa632f2`–`0xa63346`)
this extraction did not unpick. Nothing refuses a spline for leaving off-facing
either — `ValidateConveyorBelt`'s four checks are length, minimum length,
incline and curvature. So "a belt leaves a port along the port's facing" is this
project's own authoring rule, and `flab2bp.sfy.geometry.port_forward` says so.

### What a belt's clearance is — `belt.clearance`

The hologram only supplies the inputs: `UpdateClearanceData` empties
`mClearanceData` and hands the spline component, `mSplineData`, the root
component's transform and `mMaxSplineLength` to
`AFGBuildableConveyorBelt::CreateClearanceData` (`0x4d78c0`, four chained
`.pdata` chunks, 1894 bytes). That function is where the boxes are, and it is
now read whole, so the rule is `extracted` with the effect `compute` rather than
the `partial`/`none` it used to be.

It walks the spline, one segment per call to
`UFGSplineMeshGenerationLibrary::GetNextDistanceExceedingTolerance` with the
tolerances 0.5, 20.0 and 50.0, and stops once the distance reaches
`mMaxSplineLength`. Each segment gets one `FFGClearanceData` (0xC0 bytes),
appended to the array the hologram passed in:

| | X | Y | Z |
| --- | --- | --- | --- |
| `Min` | `-length/2` | **-79** | **-15** |
| `Max` | `+length/2` | **+79** | **+15** |

in the segment's own frame, placed by that segment's transform relative to the
belt's root (`TTransform<double>::GetRelativeTransform`). So a belt's clearance
is **158 cm wide and 30 cm tall**, and it is *narrower* than the belt's own mesh
— `registry.json`'s `mesh_bounds_cm` gives the Mk1 mesh 178.25 cm across. The
two are different facts and the registry carries both; a placer that used the
mesh box as the clearance would refuse placements the game accepts.

What is still unread: the two flag bytes of `FFGClearanceData` (where a
`CT_Soft` marking would live) and the tolerance arithmetic inside
`GetNextDistanceExceedingTolerance`, so the *number* of boxes on a given spline
cannot be reproduced from this rule.

### What a belt costs — `belt.cost`

Not a hologram rule at all, and the only reason it is in this file is that it is
the same kind of fact read the same way: the machine code of the shipped game.
`AFGBuildable::GetCostMultiplierForLength(totalLength, costSegmentLength)`
(`Buildables/FGBuildable.h:475`, `0x4a6bb0`) is thirteen instructions:

```
comiss xmm1,[0FD7934h]   ; costSegmentLength vs 1e-4
jbe                      ;   -> return 1
divss  xmm0,xmm1         ; r = totalLength / costSegmentLength
addss  xmm0,xmm0 ; addss xmm0,[0F6DEE8h] ; cvtss2si ; sar eax,1   ; RoundToInt(r)
cmp/cmovl                ; max(1, that)
```

So a spline buildable is charged its build recipe `max(1, round(length /
segment))` times — a **round**, not a ceiling. Which segment is the mark's own
mesh: `AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier`
(`0x4edf70`) passes `mMeshLength` and its `mLength`, the lift's (`0x4edf90`)
passes `mMeshHeight` and its height, and `AFGBuildable`'s (`0x2434e0`) returns 1
for everything else. Both mesh properties are Docs.json class defaults (200 cm
for all twelve marks in 1.2.0), so `registry.json` carries the number per
buildable as `length_per_cost_cm` with the source `docs`.

`AFGBuildable::GetDismantleRefundReturns` (`0x4a7720`) reads that multiplier
through the primary vtable at `+8C8h` and merges one stack per ingredient of
`multiplier * FItemAmount::Amount` (`0x4a781d imul`), and
`AFGBlueprintSubsystem::CalculateBlueprintCost` (`0x6738d0`) sums the refund of
every buildable in the designer, which is the `Cost` array in the `.sbp` header.

The one hop that is not an instruction is the vtable slot: the belt's
constructor stores its primary vtable `0xF79290` (`0x1b9ccf`), and the qword at
`0xF79290 + 8C8h` is `0x4edf70` — the belt's own override, whose RVA the rule's
`also_read` reports. That is `.rdata`, checkable with a hex dump, not something
`sfy-native disasm` can quote.

## Conveyor lifts — `Hologram/FGConveyorLiftHologram.h`

| Declaration | Line | Reads | Rule |
| --- | --- | --- | --- |
| `CheckValidPlacement` | 62 | `mActivePointIdx`, `mSnappedConnectionComponents`, `mUpgradedConveyorLift` | `lift.placement` |
| `UpdateTopTransform` | 65 | `mStepHeight`, `mMinimumHeight`, `mMaximumHeight`, `mMinimumHeightWithVerticalConnection` | `lift.height_range`, `lift.step`, and the `mTopTransform` half of `lift.top_yaw` |
| `SetHologramLocationAndRotation` | 25 | `mActivePointIdx`, `mFirstStepYaw`, `mConnectionComponents` | `lift.top_yaw` |
| `mFirstStepYaw` | 157 | — | the bottom's yaw, kept for the top |
| `UpdateClearance` | 70 | `mMeshHeight`, `mSnappedPassthroughs` | `lift.clearance`, with `AFGBuildableConveyorLift::FitClearance` |
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

`UpdateTopTransform` *snaps* the wanted height onto a multiple of `mStepHeight`
and then *clamps* it between the minimum — the vertical-connection one when the
lift meets a passthrough, the ordinary one otherwise — and `mMaximumHeight`.
There is no disqualifier for either, because neither refuses anything.

`mMinimumHeight` itself is not always the `BeginPlay` value while that call
runs. When the connection the top snapped to has a **vertical normal**, the
function overwrites it with 2.5 or 3.5 times `mStepHeight` — 250 or 350 cm
rather than 400 — and puts the saved value back on the way out (`0xaa4871` the
snapped connection, `0xaa4885` `GetConnectorNormal`, `0xaa4896`/`0xaa489d` the
`|Z| > 0.5` test, `0xaa48b2`/`0xaa48a8` the two constants at `0x12bd258` and
`0x12d6478`, `0xaa48ba`/`0xaa48c2` the multiply and the store, `0xaa4a6f` the
restore). A validator that keeps to 400 is therefore *stricter* than the game
for a lift that meets a vertical connection, which is the safe direction and is
said out loud in the `lift.height` check.

The snap is `lift.step`, `extracted`/`snap`:
`FHologramHelpers::CalcPoleHeight` hands the raw height back (`0xaa474f`) and
`0xaa4769`–`0xaa477c` compute `floor(raw / mStepHeight + 0.5) * mStepHeight` —
`divss` by the step, `addss` the 0.5 at `0xf6dee8`, `roundps ..., 1` (floor) and
`mulss` back. That rounded value in `xmm6` is what the zero test (`0xaa48ca`),
the sign agreement (`0xaa48db`) and both ends of the clamp (`0xaa4965`,
`0xaa4979`, `0xaa497d`) read. So a height that is not a whole number of steps is
one the game **moves**, not one it refuses: a validator that turns such a lift
away is stating this project's own rule, with this snap as its reason. The one
exception is a lift snapped to a passthrough, which carries that passthrough's
thickness modulo 100 through the clamp (`0xaa4830`…`0xaa4867`, back in at
`0xaa4981` / out at `0xaa49a3`) and so lands off the lattice by that remainder.

This rule was `partial`/`none` until Task 8e: the earlier reading looked at the
three `comiss`/`ucomiss` uses of `mStepHeight` and concluded nothing quantised
the height, which was an absence claim over a function that contains the
quantisation twelve bytes away.

### Where a lift's two connections sit — `lift.connectors`

A lift's two ports are both at the actor origin with **no rotation** in
`registry.json`, because that is what its cooked class default object holds. The
geometry is set at runtime, by `AFGBuildableConveyorLift::SetupConnections`
(`Buildables/FGBuildableConveyorLift.h:148`, `0x505a90`, 1950 bytes over four
chained `.pdata` chunks — read whole).

It starts by assigning the directions outright, neither store behind a branch:

```
0x505b0d  mov byte ptr [rax+258h],0   ; mConnection0 = FCD_INPUT
0x505b1b  mov byte ptr [rax+258h],1   ; mConnection1 = FCD_OUTPUT
```

`+258h` is `mDirection` because that is the byte
`UFGFactoryConnectionComponent::SetDirection` writes (`0x197e50`, in the rule's
`also_read`), and 0 and 1 are the first two of `EFactoryConnectionDirection`.

Then it places them. The unnamed helper at `0x4f9850` — the PDB gives it only a
templated mangling, which is why the rule asks for it by address — copies a
transform whole and adds its third argument times the rotation's forward row to
the translation. `SetupConnections` calls it twice, with `FTransform::Identity`
(`0x505b22`, a data import the module's own import table names) and with
`mTopTransform` (`0x505ba5`), passing a zeroed `xmm2` both times (`0x505b03`,
`0x505b9b`) — `AFGBuildableConveyorLift::CONNECTION_RELATIVE_FORWARD`, which the
header declares `static constexpr float ... = 0.f` (line 212). So both copies are
their inputs unchanged, and:

| End | Member | Direction | Relative transform |
| --- | --- | --- | --- |
| bottom | `mConnection0` | `FCD_INPUT` | the identity — the actor transform, facing `+X` |
| top | `mConnection1` | `FCD_OUTPUT` | `mTopTransform` — `(0, 0, height)` at the top's yaw |

`SetRelativeTransform` at `0x505e7e` and `0x506155`, each behind a test that the
matching `mSnappedPassthroughs` entry is null. Where one is **not** null the end
is turned to face straight up or straight down instead: `0x505bdf` compares the
two transforms' Z and picks `FVector::UpVector` (`0x505bad`) or
`FVector::DownVector` (`0x505bfb`), and `mConnection1` gets that vector negated
first (`0x505f4f` loads `-1.0`). All three globals are named by the DLL's import
table, not guessed.

**Reversal does not swap which end items enter by.** `mIsReversed` is still a
`SaveGame` bool, and the header marks it `DEPRECATED 2023-01-30 / Instead build
lifts where mConnector0 is always input, and the other always output`
(lines 269–272); `GetIsReversed()` is documented `LEGACY` and returns
`IsFlowUpwards()` (line 134). `SetupConnections`, read whole, never touches it.
What reversal means is the *sign of the height*:
`AFGBuildableConveyorLift::GetConveyorLiftFlowDirection` (`0x4ed5e0`, 56 bytes)
reads nothing but `mTopTransform`'s translation Z at `+7E0h` and returns
`LD_Upwards` for `>= 0`, `LD_Downwards` for `< 0`. Items still come in through
`mConnection0`, which `AFGBuildableConveyorBase::Factory_Tick` — a lift does not
override it — grabs through at `0x4e1611`, pushing out through `mConnection1` at
`0x4e1743`.

A placer that wants a lift to carry items upward puts the actor at the bottom;
one that wants it to carry them downward puts the actor at the top and gives
`mTopTransform` a negative Z. Either way the input is `mConnection0` at the
actor.

`registry.json` carries all of this as `Buildable.lift`, a `LiftGeometry` on each
of the six marks, with a source per field.

### How the top's yaw is chosen — `lift.top_yaw`

A lift is placed in two clicks, counted by `mActivePointIdx`.
`AFGConveyorLiftHologram::SetHologramLocationAndRotation` (`0xa87800`) branches on
it at `0xa87932` and calls `UpdateTopTransform` from either arm:

- **first point** (`0xa880a3`): with the **zero rotator** (`0xa8808e`,
  `0xa88095`). The bottom's own yaw is the hologram actor's, and
  `DoMultiStepPlacement` saves it into `mFirstStepYaw` at the end of that step
  (`0xa72f72`) before incrementing the counter (`0xa72f7a`).
- **second point** (`0xa88733`): with `FRotator(0, yaw, 0)` where
  `yaw = AFGHologram::ApplyScrollRotationTo(mFirstStepYaw)` (`0xa886e8` reads
  the member, `0xa886fa` calls).

`ApplyScrollRotationTo` (`0xaafe10`) asks the hologram for its rotation step
through the vtable (`0xaafe2c`), floors it at 1 (`0xaafe4f`), splits the base
yaw into whole steps and a residue (`0xaafe5e`/`0xaafe64`/`0xaafe68`), adds the
player's `mScrollRotation` (`0xaafe3d`, `0xaafe71`) and rounds the sum back onto
the lattice (`0xaafe85` `addss 0.5`, `0xaafe8d` `roundps`, `0xaafe93` `mulss`,
`0xaafe97` adds the residue back).

And `AFGConveyorLiftHologram::GetRotationStep` (`0xa7c140`) returns **0 only
while the first point is still live** — `cmp dword ptr [rcx+984h], 0; jg` at
`0xa7c15b`/`0xa7c162` — and **90** otherwise (`0xa7c1a2` `mov eax,5Ah`). So the
top's yaw is the bottom's plus whatever multiple of 90 the player has scrolled
to: four directions, chosen independently of the bottom.

`UpdateTopTransform` then writes what it was handed:

```
mTopTransform.Rotation    = rotation.Quaternion()        ; 0xaa49d1, stored 0xaa49ee/0xaa4a01
mTopTransform.Translation = clamped height * UpVector    ; 0xaa49a7 names the import,
                                                         ;   0xaa4a0c..0xaa4a14 multiply
mTopTransform.Scale3D     = (1, 1, 1)                    ; 0xaa4a36/0xaa4a3d
```

— so the top end is directly above or below the bottom, never offset sideways,
and the height is `lift.height_range`'s business rather than this rule's.

### What a lift's clearance is — `lift.clearance`

`UpdateClearance` builds the three doubles `(-5, -5, -5)`, tests both
`mSnappedPassthroughs` and hands the lift's height, `mMeshHeight`, a module
global and that `-5` vector to `AFGBuildableConveyorLift::FitClearance`
(`0x4e5dd0`, 375 bytes, `.pdata`). Both functions are read whole, and the
**span** between the lift's two ends comes out of them:

```
bottom = (|height| + mMeshHeight + 100 - P) / 2 - 30
top    = (height  + mMeshHeight - 100 - P) / 2 + Q
```

with `P = 200` when the first passthrough flag is set and `Q = 50` when the
second is. The box is then `centre ± extent`, where the centre is `top` scaled
by a vector and the extent is that module global shrunk by 5 cm per axis.

**The half-extent is read, through the symbol rather than the operand.** The
first global is the one static the class declares,
`AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D`
(`Buildables/FGBuildableConveyorLift.h:211`), and it lives in `.data`, which
`sfy-native`'s operand annotation refuses to quote because a mutable global is
not a constant. So the rule reads it the other way — `sfy-native data` resolves
the PDB symbol, refuses any RVA outside `.data`/`.rdata`, and prints the bytes —
and the rule carries the result under `data_reads`: 16 bytes at `0x19b8118` in
`.data`, `(100.0, 100.0)`, which the `-5` leaves at `(95.0, 95.0)`. That is
`registry.json`'s `lift_clearance_half_extent_cm`, so a lift's footprint is a
2 m square column and a placer may take it from here. The number is an
**initialiser**, what the image holds before the game runs; the rule says so
once, beside the bytes.

**The rule is still `partial`, and not because of a callee.** What is left is
the box's *centre*: `FitClearance` scales it by a third vector reached through
the pointer at `0xEE7CC0`, and a pointer's target is an address the loader
filled in rather than a symbol anything names, so where along its axis the game
puts the box is not in the evidence. The effect is `compute`: nothing in either
function turns a placement away.

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

`mGridSnapSize` is 100.0 by default and two hologram Blueprints override it to
50.0 (`Holo_PowerPole_C` and `Holo_StreetLight_C`), which is six buildables:
all three power pole marks, the Power Tower and its platform, and the street
light. Only three of the six name their hologram themselves — the rest inherit
`mHologramClass` from a Blueprint super, which is what the extractor walks.
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

**Nothing in the function quantises anything**, which is why its effect is
`compute` and not `snap`: the evidence is a compare-and-return ladder that hands
a number of degrees back, and whichever caller asked for it applies the step.
A validator must not treat it as a bound; a placer reproduces it.

`FGFactoryHologram.h` adds nothing (it is a two-line subclass);
`FGFactoryBuildingHologram.h` overrides `CheckValidPlacement` and
`CheckValidFloor` for foundations and walls, which the placer does not use yet.

## What a machine's inventory filters hold — `manufacturer.inventory_filters`

The other `compute` rule, and the other thing this project has to reproduce
rather than enforce. `AFGBuildableManufacturer::SetRecipe`
(`Buildables/FGBuildableManufacturer.h:173`, `0x548d10`) assigns the recipe's
ingredients and products to the machine's factory connections through
`AssignInputAccessIndices` and `AssignOutputAccessIndices` — the primary vtable
`0xFAB4A8` at `+A88h` and `+A90h` — and only if both answer true does it store
`mCurrentRecipe` and call the slot at `+A80h`, which is `SetUpInventoryFilters`
(`:220`, `0x549a70`).

That function walks each inventory **once per slot**, not once per ingredient:

| Slot `i` of | gets | when |
| --- | --- | --- |
| `mInputInventory` | `mIngredients[i].ItemClass` | `i < mIngredients.Num()` |
| `mInputInventory` | `UFGItemDescriptor` | otherwise |
| `mOutputInventory` | `mProduct[i].ItemClass` | `i < mProduct.Num()` |
| `mOutputInventory` | `UFGItemDescriptor` | otherwise |

each through `UFGInventoryComponent::SetAllowedItemOnIndex(i, class)`. The loop
bound is the inventory's own `mInventoryStacks.Num()` (`[inventory+1C8h]`), so
the slot count belongs to the machine and never changes, and
`mArbitrarySlotSizes` is not touched at all. Two consequences worth stating: a
spare slot is written with the wildcard rather than left as it was, and an oil
refinery keeps the slot a solid recipe does not fill.

## What a power shard and a somersloop do — `factory.potential`, `manufacturer.production_boost`

Two more `compute` rules, and the ones M2's rates code reproduces. Neither
refuses anything: a potential the game does not like is not turned away, it is
simply never offered.

`AFGBuildableFactory::GetCurrentMaxPotential`
(`Buildables/FGBuildableFactory.h:244`, `0x4ed7c0`) is a forward into
`GetCurrentMaxPotentialForType(type, minValue, maxValue, perShardMultiplier)`
(`0x4ed7f0`), which is the whole accumulator:

```
total = maxValue
for each slot GetSlotsForPowerShardType(type) names:
    stack = mInventoryPotential[slot]
    if stack's class is a UFGPowerShardDescriptor of that type:
        total += UFGPowerShardDescriptor::GetBoostValue(class)
                 * stack.NumItems * perShardMultiplier
return max(total, minValue)
```

`GetSlotsForPowerShardType` gives `PST_Overclock` the indices
`[0, mPotentialShardSlots)` and `PST_ProductionBoost` the single index
`mPotentialShardSlots`. **`mPotentialShardSlots` is not the class default**:
`AFGBuildableFactory::BeginPlay` copies `AFGBuildableSubsystem`'s
`mDefaultPotentialShardSlots` (and `mDefaultProductionShardSlotSize`) over it
whenever the buildable's own `mOverridePotentialShardSlots` /
`mOverrideProductionShardSlotSize` bit is clear (`0x4d40eb`, `0x4d40fc`). Docs.json
dumps `mPotentialShardSlots = 0` on all 62 classes and *no* class sets the
override, so every machine has the subsystem's **3** overclock slots and a
maximum potential of `1.0 + 3 × 0.5 = 2.5`.

What the potential then does, in both places, first rounds it to a whole percent
(`RoundToInt(p × 100) × 0.01`, UE's SSE form):

| | function | arithmetic |
| --- | --- | --- |
| cycle time | `AFGBuildableManufacturer::CalcProductionCycleTimeForPotential` | `duration / mManufacturingSpeed / p` |
| power | `AFGBuildableFactory::CalcProducingPowerConsumptionForPotential` | `GetProducingPowerConsumption() × p ^ mPowerConsumptionExponent` |

`mPowerConsumptionExponent` is **per class** — 1.321929 on every manufacturer and
extractor, 1.6 on everything else — which is why `registry.json` carries it on
each buildable and there is no global exponent in `limits`.

The production-boost half is the same accumulator with different arguments.
`GetCurrentMaxProductionBoost` (`0x4eda10`) passes `PST_ProductionBoost`,
`mBaseProductionBoost` as both bounds and the class's own
`mProductionShardBoostMultiplier` as the per-shard multiplier, so

```
boost = mBaseProductionBoost + N × mExtraProductionBoost × mProductionShardBoostMultiplier
```

for N somersloops in the one slot. `AFGBuildableManufacturer::SetCurrentProductionBoost`
(`0x547800`) is what that multiplier *does*: for each of the recipe's products
it takes `RoundToInt(amount × mCurrentProductionBoost)` and asks the output
inventory whether a stack that size would fit. **A somersloop multiplies the
product amounts, not the cycle time.**

| machine | slot size | multiplier | full boost |
| --- | --- | --- | --- |
| Constructor, Smelter | 1 (the subsystem default) | 1.0 | 2.0 |
| Assembler, Foundry | 2 (own override) | 0.5 | 2.0 |
| Manufacturer | 4 (own override) | 0.25 | 2.0 |

The two shard values are Docs.json class defaults on the descriptors themselves:
`Desc_CrystalShard_C.mExtraPotential = 0.5` and
`Desc_WAT1_C.mExtraProductionBoost = 1.0`
(`Resources/FGPowerShardDescriptor.h:32` and `:36`). They are
`limits.potential_per_shard` and `limits.production_boost_per_slot`.

## What each rule does with the number: `effect`

A rule's `status` says how well it was read; its `effect` says what the hologram
*does*, and that is the field a validator has to read before it refuses
anything. Five values:

| `effect` | what the instructions show | what a placer does |
| --- | --- | --- |
| `refuse` | a validation disqualifies the hologram | stay inside the bound, or the build gun says no |
| `clamp` | the value is forced into range | any value is buildable; the game moves it |
| `snap` | the value is quantised or aligned | any value is buildable; the game moves it |
| `none` | nothing in the instructions read enforces it | treat the number as known-good practice, not a bound |
| `compute` | the function is not a validation: it works out a value the game then writes | reproduce the arithmetic when authoring; never enforce it |

`none` is only allowed beside a `partial` or `unextractable` status —
`flab2bp.sfy.rules.load_rules` refuses it on an `extracted` rule, because a
branch that was read says what it does. `compute` is the opposite case and is
allowed beside `extracted`: the code was read in full, and it does nothing to a
placement because it is not a validator. The shipped twenty-four:

| `effect` | rules |
| --- | --- |
| `refuse` | `belt.curvature`, `belt.incline`, `belt.min_length`, `belt.max_length`, `pipe.min_length`, `pipe.curvature`, `pipe.max_length`, `pipe.fluid_requirements`, `lift.placement`, `buildable.clearance` |
| `clamp` | `lift.height_range` |
| `snap` | `belt.snap_directions`, `buildable.grid_snap`, `lift.step` |
| `none` | — (no shipped rule claims one) |
| `compute` | `belt.cost`, `belt.clearance`, `lift.clearance`, `lift.connectors`, `lift.top_yaw`, `belt.straight_tangents`, `buildable.rotation_step`, `manufacturer.inventory_filters`, `factory.potential`, `manufacturer.production_boost` |

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

`lift.step` governs `lift_step_cm` with the effect `snap`: `UpdateTopTransform`
rounds a lift's height onto a multiple of it, and rounding is not refusing. So
the registry carries the number, its source `binary` and its governance, and a
placer reads all three — the height it authors has to be a multiple of 100 cm
because the game would otherwise move the lift, which is a reason of ours built
on a fact of theirs.

## What was extracted, and what was not

`hologram_rules.json` carries twenty-four rules; twenty-one are `extracted` and
three `partial`. A `partial` rule is a **bound the placer must not assume it
knows** — its `comparison` names where the comparison actually is, and its
`interpretation` is a lead for the next extraction, not a constraint.

The two reasons a rule is only `partial`:

- **the comparison is in a callee** — `buildable.clearance`
  (`AFGHologram::TestClearanceOverlap`), `buildable.grid_snap`
  (`FHologramHelpers::SnapToFloor`);
- **a number is in `.data`** — `lift.clearance`, whose two functions were both
  read whole and whose box half-extent is a mutable module global
  `sfy-native` will not quote as a constant.

There used to be a third — "the rule may not exist", claimed for `lift.step` —
and it was a misreading rather than a reason: the quantisation is in the
function, and the rule is `extracted` now.

`belt.clearance` used to be in the first list and is not any more: its callee
`AFGBuildableConveyorBelt::CreateClearanceData` was read whole, so the boxes it
lays are `extracted`.

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
