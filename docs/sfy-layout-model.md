# The Satisfactory placement model

`flab2bp.sfy.layout` is where a build stops being rates and becomes geometry.
Three modules carry it:

| module | what it holds |
| --- | --- |
| `splines.py` | the spline shapes the game's own router builds, and how it measures them |
| `model.py` | `SfyPlacement`: every object, its class, its id and where it stands |
| `emit.py` | `emit` writes a placement into a `.sbp`, `decode` reads one back out |
| `validate.py` | the neutral judge: is this placement one the build gun would accept? |
| `manifold.py` | one row: a line of machines, its splitter chains and its merger chain |
| `corridors.py` | which column a trunk takes, and what a corridor path is made of |
| `strategy.py` | `ManifoldRows`: rows, corridors and a floor, or a named refusal |

Distances are centimetres and the axes are Unreal's, left-handed, `+Z` up.

## Two things the model states that the file does not

* **`links` is held in a canonical order.** A blueprint records a connection on
  both actors and records no *sequence*, so the order `decode` hands back is the
  order the connection components stand in the file, which no author chooses.
  `SfyPlacement` sorts `links` at construction; without that,
  `decode(emit(p)) == p` is false for every build with more than one belt, over a
  difference the file does not hold.
* **A belt end may be flagged as a boundary end.** `BeltRun.boundary_start` and
  `boundary_end` say that an end stands on the designer wall and is deliberately
  unwired, because what it meets is outside the blueprint. `ports.connected_once`
  wants *zero* links on a flagged end rather than one, and `flow.boundary` holds
  it to the wall it claims. Both flags are outside equality, like the two rate
  fields: no property in the file carries them, so `decode` hands a belt back
  unflagged.

## What the validator checks, and which rule each check names

`validate(placement, spec, registry, *, rules=None, only=None)` returns a
`Report` with `findings`, `checks_run`, `skipped`, `ok` and `errors` — the same
shape `flab2bp.layout.validate` returns for DSP, so the two judges read alike.
What is new is that **every check names its source**. The `@check` decorator
takes a `rule`:

* the id of a rule in `src/flab2bp/sfy/data/hologram_rules.json`, read out of the
  shipped game's machine code by `scripts/sfy_native_rules.py`; or
* `PROJECT` — the bound is this project's own, and the check's docstring has to
  say why we are stricter than the game.

`tests/sfy/test_validate.py::test_every_check_names_the_rule_it_enforces_or_says_it_is_ours`
fails the build if a check drifts from that.

### The rules of the discipline

1. **Only a rule whose `effect` is `refuse` turns a placement away.** A rule with
   the effect `compute` works out a value the game then writes and refuses
   nothing; `snap` and `clamp` rules move a value rather than rejecting it. A
   check that refused on one of those would be this project's own judgement
   dressed up as the game's. `belt.capsule` is exactly that case: the box shape
   is `belt.clearance`'s (a `compute` rule), so the check names `project`.
2. **A `partial` rule is a bound the validator may not assume it knows.** It runs
   what the rule's read part states, and lists itself in `Report.skipped` with
   the reason for the part that was never read. `geom.hard_clearance` is the only
   one, and it is in `skipped` on every run.
3. **No bound comes from a blueprint.** Every number is `registry.json`'s (which
   carries its own source and its governing rule) or a rule's. The corpus under
   `tests/fixtures/sfy` is used for exactly one thing here — as the template
   library and build version the `roundtrip` check writes a file with — which is
   format, not legality.

### The seventeen checks

`effect` is the effect of the rule the check names, and is blank for a check of
this project's own — a `project` check enforces no rule and so has no effect to
report. `needs spec` marks a check that cannot run without an `SfyBuildSpec`.

| id | rule | effect | needs spec | what it enforces |
| --- | --- | --- | --- | --- |
| `geom.bounds` | `project` | — | no | every origin, clearance-box corner and spline point inside `[-half, half]² × [0, height]` of the designer, sized from `designer_dims` and the shipped foundation's footprint |
| `geom.hard_clearance` | `buildable.clearance` | refuse | no | no two **hard** clearance boxes lap, by a separating-axis test on the boxes' full `RelativeTransform`. Soft boxes may share space — that is how a machine stands on a foundation. A box flagged `ExcludeForSnapping` is still tested: the flag excludes it from *snapping*, not from clearance, and the finding says the flag was there |
| `belt.capsule` | `project` | — | no | a belt's clearance chain — `Min = (-L/2, -79, -15)`, `Max = (L/2, 79, 15)` per segment, from `belt.clearance` — laps no other belt's and no hard box |
| `belt.max_length` | `belt.max_length` | refuse | no | `spline_length` ≤ `mMaxSplineLength` (`limits.belt_max_spline_cm`), arc length, strict |
| `belt.min_length` | `belt.min_length` | refuse | no | the **polyline** between stored points > `mMeshLength × 0.5001` (`limits.belt_min_length_cm`), strict |
| `belt.incline` | `belt.incline` | refuse | no | per chord, <code>&#124;π/2 − acos(clamp(u.Z, −1, 1))&#124;</code> ≤ `mMaxIncline × 0.017453292`, with the game's `float` `π/2` and its `ZeroVector` for a chord of no length |
| `belt.curvature` | `belt.curvature` | refuse | no | `step / acos(A·B)` ≥ `mBendRadius × 1.5 − 15` at every one of `RoundToInt(L × 0.02)` samples (see below) |
| `ports.connected_once` | `project` | — | no | every belt end wired exactly once, no connection carrying two belts, nothing wired to a `snap_only` or `unknown` connection |
| `ports.direction` | `project` | — | no | a link runs output → input, and meets a belt by the end `flow` names. `belt.snap_directions` is the *evidence* and not the rule enforced: its effect is `snap`, so the refusal is ours (see below) |
| `ports.position` | `project` | — | no | a belt's ends sit within 1 cm of the ports they are wired to and leave within 0.01 rad of the port's facing |
| `flow.capacity` | `project` | — | **yes** | a belt carries no more than its mark does, and every machine input is fed at the group's per-machine rate. The tier speed comes from the lab dataset through `spec.belt_tiers`, which is why a spec is needed |
| `flow.balance` | `project` | — | **yes** | per item, rows produced + belted in ≥ rows consumed + sent out |
| `flow.boundary` | `project` | — | no | every end flagged `boundary_start` stands on the `-Y` wall and every `boundary_end` on the `+Y`, within the same 1 cm `ports.position` allows, and the items at the two walls are the spec's `external_inputs` and its `outputs` plus `surplus_outputs`. A placement with no flagged end is a *fragment* and the check stands aside on it |
| `spec.machines` | `project` | — | **yes** | classes, counts, recipes and clocks match the spec |
| `slab.under_every_foot` | `project` | — | no | every machine's hard footprint is covered by foundation tops at its own `z` |
| `power.wires` | `project` | — | no | every wire inside `wire_max_cm`, every connection inside `max_connections`, every machine on a pole |
| `roundtrip` | `project` | — | no | `decode(emit(placement)) == placement`, with the templates `validate(..., library=...)` was given or the repo's own corpus |

Two checks are in `skipped` on **every** run, each with an `INFO` finding that
says what it could not cover: `geom.hard_clearance`, because
`buildable.clearance` is `partial`, and `belt.capsule`, because `belt.clearance`
leaves the `FFGClearanceData` flag bytes and
`GetNextDistanceExceedingTolerance` unread. They still run and their findings
still stand. `power.wires` joins them on a placement with no wires, and
`flow.boundary` on one with no boundary end.

### `ports.direction` names no rule, and why

It would be easy to register it against `belt.snap_directions`, and wrong. That
rule's effect is `snap`: the hologram does not turn a mismatched pair away, so a
refusal citing it would be this project's judgement wearing the game's name, and
`_may_refuse` in `validate.py` raises on exactly that. What the rule's
instructions *do* show is that the assignment
`mConnectionComponents[0]->mDirection = other->GetCompatibleSnapDirection()`
cannot produce an output wired to an output — a belt's two directions are taken
from the connection it met, never chosen. So the check cites the rule as
evidence, declares the bound ours, and refuses to author a link the build gun
never makes.

### The module enforces its own discipline

`_may_refuse(cid, rules)` runs whenever a check emits an `ERROR`: the check
either names a rule whose `effect` is `refuse`, or declares `PROJECT`. Anything
else raises `ValueError` rather than publishing the finding. A validator quietly
weakening itself is worse than one that stops, and a test alone would only catch
the drift after someone thought to look.

`Severity` has two values, `ERROR` and `INFO`. There is no `WARNING`: nothing
emits one, and a severity no finding carries is a promise the report does not
keep.

### Why `belt.curvature` applies to every belt

`ValidateConveyorBelt` calls `ValidateCurvature` only while the build gun is in
the **curve** build mode (`0xaa515e` reads `mBuildModeCurve`, `0xaa5187 je`
skips the call), so the game does not curvature-check a straight-mode belt at
all. A blueprint is pasted rather than built in a mode, and a turn the curve
mode would refuse is one this project declines to author, so the check runs on
every belt. That **scope** is ours; the arithmetic below is the game's.

### The curvature arithmetic, derived

Read straight off `AFGConveyorBeltHologram::ValidateCurvature` (`0xaa5280`), as
the rule's own `comparison` and `evidence` record it:

```
n     = RoundToInt(GetSplineLength() * 0.02)        ; 0xaa52dd mulss 0.02
                                                    ; 0xaa52ea addss xmm2,xmm2
                                                    ; 0xaa52ee addss 0.5
                                                    ; 0xaa52f6 cvtss2si
                                                    ; 0xaa52fa sar esi,1
if n <= 0: return legal                             ; 0xaa5307 jle
step  = GetSplineLength() / n                       ; 0xaa5300 divss
for i in [0, n):
    A = GetTangentAtDistanceAlongSpline(i*step).GetSafeNormal2D()      ; 0xaa535b
    B = GetTangentAtDistanceAlongSpline((i+1)*step).GetSafeNormal2D()  ; 0xaa540f
    theta  = acos(clamp(A . B, -1, 1))              ; 0xaa54a3/b8/c5, 0xaa54cd
    radius = step / theta                           ; 0xaa54da movaps xmm1,xmm14
                                                    ; 0xaa54de divsd xmm1,xmm0
                                                    ; 0xaa54ea cvtsd2ss
    if radius < mBendRadius * 1.5 - 15.0: return illegal
                                                    ; 0xaa54d2 mBendRadius
                                                    ; 0xaa54e2 mulss 1.5
                                                    ; 0xaa54ee subss 15.0
                                                    ; 0xaa54f6 comiss, 0xaa54f9 jb
```

Three details the instructions settle and prose would not:

* **`RoundToInt` is UE's SSE form**, not a floor and not a C cast: double, add a
  half, `cvtss2si`, arithmetic-shift back. `cvtss2si` rounds half to **even**
  under the default `MXCSR`, and the doubling is what stops that showing — the
  tie lands on `2n + 0.5` rather than on `n`, so the net result is
  `floor(n + 0.5)`, half **up**. `_round_to_int` reproduces the instruction
  sequence with Python's own half-to-even `round` and `>> 1`, rather than
  paraphrasing its result.
* **The radius is arc over angle**, `step / theta` — `xmm14` holds `step` and
  `xmm0` is `acos`'s answer. It is not a circumradius and not a discrete
  curvature; the numerator is the sample spacing, the same for every sample.
* **A straight pair divides by zero and passes.** `theta == 0` gives `+inf` in
  hardware and `comiss/jb` does not take the branch. `validate.py` yields `inf`
  rather than skipping the sample, so the two agree.
* **A vertical tangent is refused by this rule.** `GetSafeNormal2D` zeroes `Z`
  (the `1e-8` guard and reciprocal square root at `0xaa53d6..0xaa53e7`), so a
  climb does not *count* as curvature — `belt.incline` judges the slope
  separately, on the chords. But a tangent with no horizontal part at all is a
  different matter: `0xaa53a4` tests the squared 2-D length and `0xaa53ab` loads
  `FVector::ZeroVector`, which then goes into the dot product like any other
  vector. The dot is 0, `acos(0)` is `π/2`, and the radius comes out
  `step / (π/2)` — about 32 cm at the game's 50 cm sampling, far inside the
  floor. A belt going straight up is **refused** here, not passed over.

The sampling is by **arc length**, which a Hermite parameter is not, so
`splines.tangent_at_distance` inverts the length with a dense walk.
`GetTangentAtDistanceAlongSpline` is the engine function the rule's `calls` list
names.

### What the validator states itself

Every constant `validate.py` declares is a tolerance or a sampling resolution,
never a bound — the bounds are all in `registry.json`'s `limits`:

| constant | value | why it is ours |
| --- | --- | --- |
| `TOUCH_CM` | 1e-6 | two buildables that share a face lap by zero; floating point makes that ±1e-16 |
| `BELT_CONNECTION_CM` | 5 | two belts joined end to end lap by a sliver where their tangents differ; the game's own tolerance is inside the unread `TestClearanceOverlap`. It applies **only** to box pairs within one box length of the shared connection point — the same five centimetres ten metres down the belt is a belt through a belt |
| `HALF_PI_F32` | 1.5707963705062866 | Unreal's `PI` is a `float`, so the `π/2` `ValidateIncline` subtracts `acos` from is 4.4e-8 rad off `math.pi / 2` — which is the whole elevation of a level chord |
| `ZERO_NORMAL` | 1e-8 | the **squared** length below which `GetSafeNormal` hands back `ZeroVector`. Both callers then *use* that zero vector rather than stopping |
| `PORT_CM` / `PORT_ANGLE_RAD` | 1 cm / 0.01 rad | the slack on M1b's stated assumption that a belt begins at its port and leaves along its facing |
| `CAPSULE_SEGMENT_CM` | 50 | `belt.clearance` leaves `GetNextDistanceExceedingTolerance` unread, so the game's segment **lengths** cannot be reproduced; shorter boxes hug the spline more closely than the game's, never less |
| `CURVATURE_SAMPLES` | 2048 | parameter steps used to invert arc length; a resolution, four orders below the 50 cm the rule samples at |
| `DEG_TO_RAD` | 0.017453292 | the game's own `float` constant at `0xaa57de`, not `math.pi / 180`: the incline branch is strict, so at exactly 35° the two spellings disagree |

### Two places the validator is deliberately blind

* **A belt is not tested against the one box a port it is wired to sits inside**
  (`belt.capsule`). Forced by the game's own numbers, not by convenience: a
  Constructor's `Output0` sits at `(0, 300, 100)` inside a hard box that runs to
  `y = 500`, so a belt that starts at its own port starts 200 cm inside the
  machine feeding it. The game must exclude the snapped building somewhere
  inside `TestClearanceOverlap`, which is unread. Every *other* box of the same
  buildable stays under test — an Assembler's upper box is not forgiven because
  its lower one holds the port.
* **`power.wires` stands aside on a placement with no wires**, because a
  placement with none is a fragment and nothing in the file says whether power
  was left out or forgotten. It says so in an `INFO` finding rather than
  reporting a clean pass. Every build `ManifoldRows` lays out has wires in it,
  so the check *runs* on all of them.

## What a build looks like: rows, corridors and bridges

`ManifoldRows` (`strategy.py`) is the shape every Satisfactory build this project
authors takes. It is a shape a player would recognise, and every distance in it
is read out of the registry or the rules.

**A row** (`manifold.py`) is one machine group: `count` machines of one class in
a line along `X`, one splitter chain per input item feeding them and one merger
chain draining them. It is built about its first machine and moved into place.

**The rows stack along `Y`** in topological order of the item graph, so a row
that makes what another eats stands below it. Every other row is *flipped*, so
its chain input end faces the same corridor the row before it output into.

**A corridor** runs down each side of the rows: columns of one belt width at
fixed `X`, a column pitch apart. Every trunk — an external input, a row-to-row
hand-off, an output — runs up one column and reaches the rows by a *transverse*
piece across the corridor at one `Y`.

**A bridge** is what a transverse does when the column it has to cross is
occupied: it climbs one crossing gap before the crossing, crosses at that height,
and comes down after the last crossed column — inside its own column for a turn
out of a row, and across the corridor for a turn into one.

**Mergers and splitters** stand in the corridor where a trunk has more than one
source or more than one sink. Each stands at the `Y` of the row it joins, so the
belt between the attachment and the row is one straight transverse. The two walls
are never branches: the entry is below every row and the exit above them, so they
are always the trunk's own two ends.

**A pole line** (`power.py`) stands along every row, and a wire runs from every
machine's power connection to the nearest pole with a link free. The poles are
chained to each other along the row and row to row, so the whole build is one
circuit — which is what `power.wires` walks.

### Power: where a pole stands, and what a wire is

The build uses `Build_PowerPoleMk2_C` and `Build_PowerLine_C`. **Both choices are
ours**: the game ships three free-standing marks and two wire classes and says
nothing about which a build should use. The Mk2 is chosen because its
`PowerConnection` takes seven wires where the Mk1's takes four, and because the
fixture corpus carries a template of it and none of the Mk3.

| quantity | where it comes from | today |
| --- | --- | --- |
| a pole's connection, and where it sits | `Port.translation` on `PowerConnection` | `(0, 0, 760)` |
| how many wires it takes | `Port.max_connections`, `max_connections_source` `asset` | 7 |
| how many machines one pole carries | `max_connections` − `CHAIN_LINKS_PER_POLE`. **Ours**: two links held back on every pole, which is the most any pole in this shape spends on the chain (one neighbour each side, or one neighbour and the row beside it) | 5 |
| how far a wire reaches | `limits.wire_max_cm[Build_PowerLine_C]`, source `docs` (`mMaxLength`) | 10 000 cm |
| the grid a pole snaps to | `Buildable.grid_snap_cm` where the class's hologram overrides `mGridSnapSize` (the Mk1, the Power Tower and the street light, all 50); otherwise `limits.hologram_grid_cm`, read out of the shipped DLL. The Mk2 states none, so it takes the global 100 — which is a multiple of 50, so it stands on the pole grid either way | 100 cm |
| where along the row the line stands | **ours**, and computed rather than assumed: the band between the machine line's own hard `+Y` face and the merger chain's near face is tried first, and only where that band is narrower than the pole's own box does the line go past the chain. The placement's description says which of the two happened | past the chain, in both `iron-plate-60` rows |
| where along `X` | **ours**: the midpoints of the machine pitch, one per group of machines, each group taking the free midpoint nearest its own centre | — |

A wire is written as a `Build_PowerLine_C` actor stamped out of the fixture
template, with the two connection references in its **class trailer** —
Task 9a's reading of `AFGBuildableWire::Serialize`, recorded in
`docs/sfy-struct-layouts.md`. Two of its properties are authored rather than
copied: `mWireInstances` goes out empty, because the game's loader calls
`DestroyWireInstances` and then `CreateWireInstancesBetweenConnections` and
rebuilds the meshes from the two connections; and `mCachedLength` is the span
this wire really covers. The wire is also listed in the `mWires` array of each
connection component it joins, which is what the game writes: all 1016 ends of
the corpus's 508 wires carry it.

The actor stands **on the second connection's point**, unrotated. That is the
corpus's convention rather than a rule out of the binary: of the 330 fixture
wires whose two connection points the registry reproduces exactly, 276 stand
within a centimetre of `mConnections[1]` and two of `mConnections[0]`, and 482 of
all 508 carry a `mWireInstances` entry whose second `CachedRelativeLocations` is
zero. The yaw a fixture wire carries is the angle the player's build gun happened
to be at and says nothing, so ours is 0.

### The numbers, and which of them are ours

| quantity | where it comes from | today |
| --- | --- | --- |
| column pitch | two `belt.clearance` half-widths and one hologram grid step | 300 cm |
| turn radius | `grid_ceil(2 × belt_bend_radius_cm)`; `belt.curvature`'s floor is `199 × 1.5 − 15` | 400 cm |
| bridge height | belt height + the row builder's crossing gap (**ours**, twice a belt box's height) | 300 cm |
| attachment pitch | `grid_ceil(2 × through-port offset + belt_min_length_cm)`. The brief asked for one grid step; the game's own port offsets make that a belt of −100 cm | 400 cm |
| climb run | `grid_ceil(rise / tan(belt_max_incline_deg))` | 200 cm per 100 |
| flat lead out of a port | `grid_ceil(belt_min_length_cm)`, **ours**, because `ports.position` holds a belt to its port's own (horizontal) facing | 200 cm |
| room at each wall | turn radius + one grid step, less what the row's band already covers. **Ours**: a belt's clearance box is square to the belt, so a box on a turning piece that began ON the wall reaches 4.8 cm outside it and `geom.bounds` refuses the build | 300 cm |
| gap between two rows | whatever the two turns of a trunk still want after the rows' own overhangs, never less than a grid step. **Ours** | 400 cm |
| floor | a full field of the shipped foundation at half its own box's thickness | 8 m tiles at z 50 |

### The refusals

`ManifoldRows.lay_out` either hands back a placement the validator passes or
raises `NoValidLayout` with one of these, and nothing else:

`rows exceed the designer depth` · `rows exceed the designer width` ·
`corridor needs a bridge that does not fit` · `row too deep` ·
`run exceeds the belt ceiling` · `fluids are M4` ·
`corridor assignment exceeded the budget` ·
`a row makes something the spec never sends out` ·
`a row is fed from the corridor on the other side of the build` ·
`nothing in the build supplies a row's input` ·
`wire exceeds the maximum length` · `no room for a power pole` ·
`a machine has no power connection`

The last six are not in the brief's list; three are shapes of spec this build
form cannot realise and three are the power stage's, and each is a named cause
rather than a stray message. The
module refuses to raise anything else: `_refuse` checks the string against
`REFUSALS` and raises `ValueError` on a cause nobody declared.

**What fits.** Two rows and the room their trunks need to turn between them is
42 m of band. The mk1 designer is 32 m deep and the mk2 is 40, so a two-row build
is an mk3 one; `iron-plate-60` refuses both smaller marks on depth.
`reinforced-iron-plate-10` is five rows and 110 m of band, which no designer
holds, and it refuses for every mark the game ships.

### The broad phase

Every box-against-box test goes through two world-axis rejects first: `_apart`
compares the two boxes' own axis-aligned bounding boxes, and `_spans_miss` does
the same over a whole *set* of boxes — two belts at opposite ends of a designer
are settled in six comparisons rather than a hundred boxes against a hundred.
A world axis that separates the AABBs separates the oriented boxes inside them,
so a reject is a real answer and not an approximation. Measured over 40
full-length (5599 cm, 112 boxes each) belts: **69.2 s → 0.13 s** where the runs
are parallel and apart, and **70.4 s → 3.3 s** for a 20 × 20 crossing grid at
one height where all 400 pairs really clash.
