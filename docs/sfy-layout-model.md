# The Satisfactory placement model

`flab2bp.sfy.layout` is where a build stops being rates and becomes geometry.
The current production planner is `flab2bp.sfy.sections`; the shared layout
model, emitter, routers and validator also serve the retained legacy planners:

| module | what it holds |
| --- | --- |
| `splines.py` | the spline shapes the game's own router builds, and how it measures them |
| `model.py` | `SfyPlacement`: every object, its class, its id and where it stands |
| `emit.py` | `emit` writes a placement into a `.sbp`, `decode` reads one back out |
| `validate.py` | native-backed checks and explicitly project-owned authoring checks |
| `../sections/` | production sections, mixed-material routing and vertical stack boundaries |
| `manifold.py` | legacy row geometry, also reused by section construction |
| `corridors.py` | legacy horizontal trunk corridors |
| `strategy.py` | retired `ManifoldRows` reference planner |

Distances are centimetres and the axes are Unreal's, left-handed, `+Z` up.

`Pose` carries pitch, yaw and roll and preserves the stored actor quaternion,
including vertical/gimbal-lock poses; it is not yaw-only. Equality compares the
stored quaternion rather than non-unique Euler angles. A lift's *local*
`mTopTransform` still uses its signed height and local top yaw.

## Current production: repeatable sections and mixed materials

`sections/compose.py` builds complete connected sections, packs independent
sections together, and keeps a consumer above its producers. Dependency stages
are not necessarily separate physical floors. Routes join section interfaces,
preserving recipe counts, clocks and every material flow. Cyclic/recycled coupled
dependencies are refused; this is not a general fluid-network solver.

Solid sections use opposing rows when their complete physical depth fits, or
a longer single row before partitioning the machine count. Interface frames
reserve the minimum belt/lift escape, not a compulsory turning splitter.
Physical transport envelopes and slab thickness determine floor separation;
the roof clears the complete measured geometry without an extra empty aisle.

`sections/fluids.py` builds separate side manifolds for supported mixed recipes.
Pipes carry m³/s in the model (reports use m³/min); solid belts carry items/s.
Recipe-order port assignment retains fluid byproducts, including heavy-oil
residue from plastic. Available ports, their facing, funded transport tiers,
routing room and hydraulic constraints still bound support.

Fluid rows reserve native hard footprints and actual transport connector spans,
not an extra walking aisle between machines. Four refineries use 1000 cm pitch
within Mk2. Pipe interfaces terminate at native junction ports; a single-row
solid collector exposes its final merger directly instead of adding a turning
splitter. Composition reserves pipe-specific bends and mesh envelopes,
not conveyor-lift approaches, for those fluid interfaces.

`sections/pipe_routes.py` keeps pipe-actor joins on straight interiors, away
from curved mesh envelopes. Curved wall approaches reserve the cross-section's
diagonal extent; straight obstacle checks retain their transverse extent.
Near an obstacle, curved turns use the validator's sampled native mesh envelope
instead of treating the inflated virtual elbow's empty corners as solid.
Terminal candidates include one-radius legs as well as two-radius interiors.
Final collision validation is unchanged.

Routing prefers native four-way junctions for U-turns and bent elevation changes,
and tries them when rounded-pipe search cannot find a route. Junctions rotate
into the XY, XZ or YZ plane; unused ports are legal. Their actual connector
offsets, body clearance and straight-pipe minimum length still apply. Short
descents that cannot fit two junctions retain legal curved pipes. This search
does not yet explore arbitrary-angle junction orientations, although the
blueprint model and binary round-trip support them.

Conveyor routing prefers a continuous native-radius bend over a turning
splitter wherever it fits. Splitter/merger bodies use the cooked mesh-instance
envelope, not just their soft native clearance; `geom.attachment_body` is an
explicit project collision policy. Search and final admission reserve those
bodies and permit connected belts to enter only along the actual mouth's
straight approach. Lift entry normals are outward, like the known-good native
references: an incoming belt's flow tangent points opposite the entry normal.
Section approaches clear the connected actor's actual body, rather than the
entire section rectangle. Other machines and transport remain independent
obstacles: swept bend and full lift-shaft guards reject collisions before
search commits a route. Admission also compares complete realised geometry with
already admitted nets: opposing ramps can intersect between lattice nodes
without sharing a centreline cell. These geometry records follow net ownership
through rejection, rip-up and best-round restoration.
Stack lanes preserve each section interface's lift-width escape column.
Where both lift directions are blocked, the terminal lead instead clears a
native turning attachment; ordinary interfaces retain the shorter lift lead.

Rip-up routing promotes the net with the largest unavoidable terminal-level gap
ahead of the normal descending-flow order, breaking ties by flow and then net id.
Only that first choice changes: reordering every net by height can starve short,
high-rate connections. Each required terminal is compared with the nearest
opposite-side level; wall terminals are alternatives, not separate demands.
This offers the most vertically constrained net clear columns without making
any reachability decision or selecting priorities by material name.
After an incomplete round, stranded nets move ahead of successful nets,
preserving their relative order.
This prevents a lower-rate stream from repeatedly hitting its search work cap
behind the same reservations when there is no proven obstruction to penalize.
Round limits, the layout deadline, collision guards and best-round restoration
still apply.

Motion guards subtract occupied boxes and coalesce free lattice spans in the
existing native geometric extension. The admitted cells and deterministic box
order are unchanged; no occupancy-dependent cache survives a rip-up.

`sections/stacking.py` adds actual base/roof slabs, painted-beam outlines and corner
posts, with aligned conveyor/pipe floor holes. Stack search reserves the actual
lane parts and full-height shafts rather than their empty enclosing corners,
and can move solid branches above or below an obstructing collector.
Corner posts end at the next module's base, above this module's roof slab,
so their height preserves the manual connection seam rather than a flush roof.
Fluid bays likewise reserve the actual junction and pump bodies, with lateral
approaches only at branch height rather than extruded up the whole pump.
Each external input has a bottom feed, local branches and an upward continuation.
Exports have a top inlet for production from higher modules, local collectors,
and a bottom outlet. Importing and exporting the same material uses separate
shafts, never a bidirectional belt. Complete section flows
use both genuine side ports before adding inline boundary bodies for more peers.
Equal-rate obligations route directly; partial external contributions retain
aggregate routing. `StackLane` records local demand/export, trunk capacity and
any required external input head. These authoring obligations are not saved
transport properties and cannot be recovered just by decoding a `.sbp`.

Conveyors select the lowest available tier meeting their assigned flow, before
geometry routing. The FactorioLab `ibe` selection is retained as provenance,
not a minimum conveyor tier; all dataset tiers through `maxBelt` are available,
along with the explicitly selected tier. There is no belt-tier routing retry loop.
Trunks use each lane's local demand/export. The shared inter-section lift profile
uses the lowest tier meeting the busiest routed solid stream. Availability of a
higher tier does not itself justify using it. Repeat counts remain bounded by
the reported trunk capacities; selecting a higher `ibe` does not reserve extra
stacking throughput. Pipe selection retains its separate chosen-floor policy.

`sections/signs.py` adds native SmallWide sign pairs after routing, using the
reference blueprints' orange-on-black INPUT/OUTPUT header and material-row style.
Each lane has the same alphabetical identifier at both ends. Numeric labels use
local consumption or production, not trunk capacity or an assumed stacked flow;
unused ends explicitly say PASS THROUGH. Fluids use m³/min. Rates retain exact
terminating decimals up to six places, otherwise exact fractions.

Each pair has a native short painted beam mounted on the front (+X) base/roof
rail. All signs face +X; upper pairs hang beneath the roof and lower pairs stand
above the base. Placement checks both native body clearance and an unobstructed
view from the complete text face toward the designer's front edge. It keeps
signs and supports inside the designer and outside the manual seam. Missing
mounting space is an explicit refusal, not omitted labels or a clearance
exemption. `SignObj` preserves text, lane identifier and prefab layout through
binary round-trips; construction costs include Iron Plates, Quartz Crystals
and the mounts' Steel Beams. The viewer exposes native text in labels and details.
Corrected iron-plate Mk1 and plastic/rubber 80/min Mk2 smoke artifacts are in
`/home/dannyb/satisfactory-tests/paste-corrections/`; these are offline geometry,
connectivity, binary and schematic-viewer checks, not an in-game paste proof.

`sections/power.py` mounts double Wall Outlets Mk.2 on the painted corner posts.
The inside faces form a ring and supply all machines and powered pumps, respecting
seven wires per face and native wire lengths. Reciprocal hidden connections join
each outlet's two faces; outside faces remain unused for external hookups.
Socket poses and inherited ±70 cm connection offsets follow the native references.
Only each socket's own mounting post is exempt from its body collision check.

Repeat a module at its reported vertical pitch with the same XY/orientation.
**Manually bridge corresponding endpoints with one lift for solids**, or a pipe
for fluids. Endpoint separation may be any native-legal direct connection;
the current generator uses a 400 cm seam. Slabs remain distinct; transport ends
meet hole centres, not slab faces. No cross-blueprint references or automatic
lift joins are promised. Size supply for all copies and keep accumulated flow
within each trunk's capacity; connect power and drain every output, including
byproducts.

Upward input pipe trunks contain powered Mk2 pumps and real junction actors.
Output pipe trunks drain through their collectors to the bottom without an upward
pump. Gravity certification must reach every connected consumer, first pump inlet
and bottom export without exceeding the producer's endpoint head. Only dead-end
upper continuation arms may remain unprimed; an elevated parallel path cannot
hide behind a smaller gravity drain. External liquid supply must reach the highest
connected pipe endpoint before the next pump inlet. Interior spline humps and unused
junction mouths do not add head; splitting a run or connecting a junction at a
crest creates an elevated endpoint and changes that requirement. `pipe.head`
checks pump design head per forward region; ratings do not add together. This is
a project design envelope, not a runtime pressure/priming simulation or a
guarantee about world supply.
Automatic pumps are confined to the upward input stack lanes, not internal
manifold turns. A lower input is a pressurized supply contract with the module
below or an external pump; an unpowered or unprimed supply does not satisfy it.

The offline Mk3 examples include Plastic 10/min and 20/min with one refinery
and retained residue, and Reinforced Iron Plate 10/min with fourteen machines
on three mixed-recipe floors. Reports/native evidence are in
`/home/dannyb/satisfactory-tests/stackable-production/`. No in-game load, paste
or throughput validation has been performed.

Offline Mk2 builds also cover standard Plastic and Rubber at 20, 60 and 80/min:
one, three and four refineries respectively, on one production floor within a
40 × 40 m footprint and 40 m repeat pitch. Each retains its crude-oil supply,
residue drainage and solid output. These match the reference refinery packing,
not the output rates of the references' different recycled recipes. Captured
flows, emitted blueprint pairs and measured visual comparisons are in
`/home/dannyb/satisfactory-tests/refinery-density/`.

### Physical actors and binary ownership

`PipeRun` serializes a genuine pipeline spline; `PipeAttachmentObj` represents
junctions and pumps, with pump power carried by ordinary `WireObj` connections.
`BeamObj` authors local-+X length through `mLength`.
`PassthroughObj` authors `mSnappedBuildingThickness` and nullable top/bottom
**transport-component** references, not invented ports. Lifts and pipes carry
exactly two nullable `mSnappedPassthroughs` actor references, reciprocated by
the referenced hole. The emitter rebuilds those references after instantiation
instead of inheriting fixture owners, and decoding preserves this geometry.

Beam lengths and hole middle-mesh width/slab thickness are bounded. Constructed
passthrough cap-mesh extents remain unextracted (`geom.dynamic` reports the gap);
partial lift envelopes and project pipe mesh envelopes are not full game meshes.
The schematic viewer and offline checks do not certify in-game placement.

## Two things the model states that the file does not

* **`links` is held in a canonical order.** A blueprint records a connection on
  both actors and records no *sequence*, so the order `decode` hands back is the
  order the connection components stand in the file, which no author chooses.
  `SfyPlacement` sorts `links` at construction; without that,
  `decode(emit(p)) == p` is false for every build with more than one belt, over a
  difference the file does not hold.
* **Transport ends may be flagged as boundary ends.** `boundary_start` and
  `boundary_end` identify deliberately unwired ends that meet transport outside
  the blueprint. Current sections expose vertical lift/pipe endpoints through
  `StackLane`; `flow.boundary` checks their repeated geometry, ownership and
  rates. The older horizontal `BeltRun` wall flags remain for legacy fragments.
  Flags and rated flows are outside equality: no saved property carries the
  authoring contract, so `decode` does not invent it.

### A conveyor lift is an actor with a height

`LiftObj` carries dynamic geometry in addition to its pose.
`pose` is its input/actor end, because `AFGBuildableConveyorLift::SetupConnections`
puts `mConnection0` at the actor transform exactly. Unsnapped, it faces the actor's
own forward (`lift.connectors`, with geometry in `registry.json`).
The other end is `mTopTransform`, a `SaveGame` `FTransform`
declared at `Buildables/FGBuildableConveyorLift.h:266-267`, and the model carries
its two moving parts:

* `height_cm` is **signed**, the way that translation is: the top sits that far
  along the geometry's `top_offset_axis`, above the actor for a positive height
  and below it for a negative one. It is not held at `stored_float` width — a
  pose is ten 32-bit floats because that is what the object table writes, and
  `mTopTransform` is a property beside it, written as doubles.
* `top_yaw_deg` is the yaw that transform carries, **in the actor's own frame**
  (`Hologram/FGConveyorLiftHologram.h:102-104`: "in actor local space"), so the
  top end faces the pose's yaw plus this one. `lift.top_yaw` says it is a whole
  number of 90 degree steps, which is what the registry's `top_yaw_step_deg`
  records and what the `lift.top_yaw` **check** holds it to.

Which way items travel does **not** depend on the sign. `reversed_swaps_flow`
is false: items always enter by `mConnection0`, so `flow` names the entry at the
actor either way, and a lift that delivers *downward* into a machine port is one
whose actor stands at the top — where its feed arrives — with a negative height.
`GetConveyorLiftFlowDirection` reads nothing but the sign of that Z, and it
decides which way the *mesh* runs.

`emit` rebuilds `mTopTransform` from the model and writes exactly two nullable
`mSnappedPassthroughs` entries, never an empty array or inherited fixture refs.
A snapped end faces vertically and remains at the hole centre. The hole
reciprocally names that lift connection. `mIsReversed` is not written: the
header marks it `DEPRECATED 2023-01-30` and `SetupConnections` never touches it.
`decode` preserves height, local top yaw and the two slots for round-trip equality.

### A machine's clock and its somersloops *are* in the file

Task 12 read the properties out of the game's public headers, so the clock is no
longer carried by the `.sbpcfg` description alone.
`Source/FactoryGame/Public/Buildables/FGBuildableFactory.h` in
`CommunityResources/Headers.zip` declares four `SaveGame` floats —
`mPendingPotential` (line 609) and `mCurrentPotential` (line 670),
`mPendingProductionBoost` (line 613) and `mCurrentProductionBoost` (line 674) —
and `emit` writes all four on every machine that is not a plain 100 % machine
with no somersloop in it, first stripping any the template inherited from the
fixture player's own build. What the numbers mean is the game's own data and not
this project's reading of a corpus: a potential is a multiple of the rated speed,
because Docs.json gives a Constructor `mMaxPotential` 1.0 and a Power Shard
`mExtraPotential` 0.5, so three shard slots reach 2.5 and 250 % is written as
`2.5`; a production boost is `base_production_boost + n *
production_boost_per_slot * production_boost_multiplier`, which is
`GetCurrentMaxProductionBoost` adding `GetBoostValue` once per shard, and every
one of those numbers is in the registry. `decode` reads both back, so
`MachineObj.somersloops` — an integer count, encoded exactly at the stored width
— is part of equality again. `clock` is not, and the reason has changed rather
than gone: a clock is an exact `Fraction` and the file holds one 32-bit float, so
`moc=133`'s 133/100 comes back as the stored number beside it. Whether the *game*
keeps a pasted machine's potential when no shard sits in the slot is a question
no file can answer; `scripts/sfy_checkpoint2.py` writes the pair that asks it.

## What the validator checks, and which rule each check names

`validate(placement, spec, registry, *, rules=None, only=None)` returns a
`Report` with `findings`, `checks_run`, `skipped`, `ok` and `errors` — the same
shape `flab2bp.layout.validate` returns for DSP, so the two judges read alike.
What is new is that **every check names its source**. The `@check` decorator
takes a `rule`:

* the id of a rule in `src/flab2bp/sfy/data/hologram_rules.json`, read out of the
  shipped game's machine code by `scripts/sfy_native_rules.py`; or
* `PROJECT` — an explicitly project-owned authoring bound, not a native refusal.

The runtime `_may_refuse` guard enforces that distinction when emitting errors;
documentation wording is not the contract.

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

### Shared conveyor checks

`effect` is the effect of the rule the check names, and is blank for a check of
this project's own — a `project` check enforces no rule and so has no effect to
report. `needs spec` marks a check that cannot run without an `SfyBuildSpec`.

| id | rule | effect | needs spec | what it enforces |
| --- | --- | --- | --- | --- |
| `geom.bounds` | `project` | — | no | every origin, clearance-box corner and spline point inside `[-half, half]² × [0, height]` of the designer, sized from `designer_dims` and the shipped foundation's footprint |
| `geom.hard_clearance` | `buildable.clearance` | refuse | no | no two **hard** clearance boxes lap, by a separating-axis test on the boxes' full `RelativeTransform`. Soft boxes may share space — that is how a machine stands on a foundation. A box flagged `ExcludeForSnapping` is still tested: the flag excludes it from *snapping*, not from clearance, and the finding says the flag was there |
| `belt.capsule` | `project` | — | no | a conveyor's clearance laps no other conveyor's and no hard box: for a belt, the chain `belt.clearance` lays — `Min = (-L/2, -79, -15)`, `Max = (L/2, 79, 15)` per segment; for a lift, the one box `lift.clearance` says spans it, as wide as `limits.lift_clearance_half_extent_cm` — the game's own half-extent, read out of `.data` by the PDB symbol `AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D` and shrunk by the 5 cm `FitClearance` takes off each axis, 95 cm each way (a registry that carries no such limit falls back to half the connector clearance on the lift's two ports, and the skip note says which was used) |
| `belt.max_length` | `belt.max_length` | refuse | no | `spline_length` ≤ `mMaxSplineLength` (`limits.belt_max_spline_cm`), arc length, strict |
| `belt.min_length` | `belt.min_length` | refuse | no | the **polyline** between stored points > `mMeshLength × 0.5001` (`limits.belt_min_length_cm`), strict |
| `belt.incline` | `belt.incline` | refuse | no | per chord, <code>&#124;π/2 − acos(clamp(u.Z, −1, 1))&#124;</code> ≤ `mMaxIncline × 0.017453292`, with the game's `float` `π/2` and its `ZeroVector` for a chord of no length |
| `belt.curvature` | `belt.curvature` | refuse | no | `step / acos(A·B)` ≥ `mBendRadius × 1.5 − 15` at every one of `RoundToInt(L × 0.02)` samples (see below) |
| `ports.connected_once` | `project` | — | no | transport ends wired once except declared external ends; no duplicate occupancy, `snap_only` or unknown connections |
| `ports.direction` | `project` | — | no | compatible same-medium connections; directed conveyor/pump flow and bidirectional pipe junctions are distinguished |
| `ports.position` | `project` | — | no | connected endpoints within 1 cm, with compatible endpoint normals/tangents (0.01 rad); hole-snapped ends face vertically |
| `lift.height` | `project` | — | no | height stays in the sourced range, using the passthrough minimum for a snapped lift. Native `lift.height_range` clamps rather than refuses; unsnapped vertical connections can also get 250/350 cm native minima, so the ordinary 400 cm project floor remains stricter there |
| `lift.top_yaw` | `project` | — | no | a lift's `top_yaw_deg` is a whole number of `top_yaw_step_deg` steps — 90 degrees, which is what `GetRotationStep` hands out once the first placement point is down (`0xa7c1a2`) and what `ApplyScrollRotationTo` rounds onto. `lift.top_yaw` **computes** the yaw and refuses nothing, so the refusal is ours: an off-lattice top is a lift no player could build |
| `lift.step` | `project` | — | no | ordinary lift height follows `lift_step_cm`; one-hole lifts include the native half-thickness remainder; two-hole bridges use exact hole centres through `TrySnapToActor`. Native snapping is not a refusal |
| `lift.placement` | `lift.placement` | refuse | no | neither end of a lift ends on a connection that already carries something. `CheckValidPlacement` tests `mHasConnectedComponent` on each snapped connection (`0xa681fa`, `0xa68253`) and adds `UFGCDInvalidPlacement` |
| `flow.capacity` | `project` | — | **yes** | funded belt/pipe capacities and required machine supplies; pumps also have sourced flow limits |
| `flow.balance` | `project` | — | **yes** | exact supplied/consumed/exported material flow, including fluid byproducts |
| `flow.boundary` | `project` | — | no | current sections: aligned bottom/top lift or pipe ends, reciprocal slab holes, structural pitch and manual seam, continuous local branches and rated external totals; legacy fragments: flagged belts at `-Y`/`+Y` walls |
| `spec.machines` | `project` | — | **yes** | classes, counts, recipes and clocks match the spec |
| `slab.under_every_foot` | `project` | — | no | every machine's hard footprint is covered by foundation tops at its own `z` |
| `power.wires` | `project` | — | no | every wire inside `wire_max_cm`, every connection inside `max_connections`, every machine on a pole |
| `roundtrip` | `project` | — | no | `decode(emit(placement)) == placement`, with the templates `validate(..., library=...)` was given or the repo's own corpus |

### Pipe and structural checks

| id | source | what it checks |
| --- | --- | --- |
| `pipe.max_length` / `pipe.min_length` / `pipe.curvature` | corresponding native `refuse` rules | spline arc maximum, strict chord minimum and full-3D tangent curvature |
| `pipe.fluid_requirements` | native fluid-identity refusal | incompatible descriptors in a connected authored network |
| `pipe.capsule` | project | sampled mesh-envelope collision admission, not unextracted native pipe clearance |
| `ports.passthrough` | project | reciprocal component/actor ownership, transport medium, hole centre/normal and slab thickness |
| `geom.dynamic` | project | sourced beam dimensions and explicit partial coverage of constructed hole caps |
| `pipe.head` | project | routed external inlet-head obligations, powered-pump design-head regions and disclosed unsourced factory-output head |

Native `compute`, `snap` and `clamp` evidence informs geometry but is not relabelled
as a native refusal. A clean project check does not imply the unread native
clearance or runtime hydraulic behaviour has been verified.

Two checks are in `skipped` on **every** run, each with an `INFO` finding that
says what it could not cover: `geom.hard_clearance`, because
`buildable.clearance` is `partial`, and `belt.capsule`, because `belt.clearance`
leaves the `FFGClearanceData` flag bytes and
`GetNextDistanceExceedingTolerance` unread — and, since lifts joined it,
because `lift.clearance` is `partial` too, though no longer for its width:
`sfy-native` quotes the module global at `0x19B8118` by its PDB symbol now, so
the 95 cm each way is the game's. What is still unread is where along its axis
the game puts that box, and the skip note names the width each lift here
actually got and where it was read. They still run and their findings
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

The sampling is by **arc length**, which a Hermite parameter is not.
`splines.tangents_at_distances` builds a per-call chord table once for each used
segment, at the same dense-walk resolution as scalar `tangent_at_distance`.
Belt and pipe curvature reuse adjacent tangents without changing segment
selection, interpolation, thresholds or sample positions.
`GetTangentAtDistanceAlongSpline` is the engine function the rule's `calls` list
names.

Round-trip validation still emits and decodes the complete placement. A supplied
template library is reused directly; selecting the newest corpus header does
not decompress a second fallback library. Standalone validation loads fallback
templates only when the caller supplies none.

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
| `LIFT_YAW_TOLERANCE_DEG` | 1e-6 | noise, not slack: a top yaw goes into the file as four doubles and comes back through `atan2`, so a quarter turn is not always exactly 90 |
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
* **A lift is not tested against a conveyor it is wired to** (`belt.capsule`),
  which is the same blindness one step further. A lift's clearance box spans the
  lift itself, so whatever meets it meets it *inside* that box: the centimetre
  of slack two belts get where they join cannot express a belt that ends on a
  lift's top. The exclusion rests on the same unread `TestClearanceOverlap`, and
  it is narrow — a lift is still judged against every conveyor it is not wired
  to, and against every hard box but the one holding a port it is wired to.
* **`power.wires` stands aside on a placement with no wires**, because a
  placement with none is a fragment and nothing in the file says whether power
  was left out or forgotten. It says so in an `INFO` finding rather than
  reporting a clean pass. Every build `ManifoldRows` lays out has wires in it,
  so the check *runs* on all of them.

## Legacy reference: rows, corridors and bridges

The remainder of this document preserves the retired manifold/grid designs and
their historical measurements. Neither is a CLI/API production choice; their
horizontal wall boundaries, fluid refusal and fit results do **not** describe
current `sections` production. Shared helpers are still reused where applicable.

`ManifoldRows` (`strategy.py`) arranged rows and horizontal corridors using
registry-derived dimensions:

**A row** (`manifold.py`) is one machine group: `count` machines of one class in
a line along `X`, one splitter chain per input item feeding them and one merger
chain draining them. It is built about its first machine and moved into place.

**The rows stack along `Y`** in topological order of the item graph, so a row
that makes what another eats stands below it. Every other **group** is *flipped*,
so its chain input end faces the same corridor the group before it output into.

**A group longer than the wall is several rows.** The floor between the two
corridors holds `per_row = floor((usable - one machine's footprint) / pitch) + 1`
machines, and a group with more of them is laid as `ceil(count / per_row)` rows
of the same recipe, as even as they divide, with the underclocked last machine in
the last row. Those rows stand next to each other and share their group's flip —
which is the whole reason the flip is per group and not per row: two rows facing
opposite corridors could not be fed from one trunk. To the corridor they are
simply several sinks of one item and several sources of another, which is the
splitter chain and the merger chain it already had.

How wide the floor is depends on how many columns the corridors take, and that is
known only once the rows are placed, so the planner walks to a fixed point: the
first pass assumes the narrowest corridor there can be, and each pass re-splits
against what the last one measured. Splits only grow, so it settles.

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
| the grid a pole snaps to | `Buildable.grid_snap_cm` where the class's hologram overrides `mGridSnapSize` (all three pole marks, the Power Tower and its platform, and the street light, all 50); otherwise `limits.hologram_grid_cm`, read out of the shipped DLL. The Mk2 and the Mk3 name no hologram of their own and take the Mk1's `Holo_PowerPole_C` through the super chain | 50 cm |
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
| shortest belt | the first whole centimetre above `belt_min_length_cm`, which `AFGConveyorBeltHologram::ValidateMinLength` compares strictly. **Not** rounded to the grid: the grid is where a hologram snaps and a belt is a spline between two ports | 101 cm |
| flat lead out of a port | the shortest belt, **ours**, because `ports.position` holds a belt to its port's own (horizontal) facing | 101 cm |
| what a turn costs | an arc spends its radius of both straights; an attachment spends its own soft box plus one shortest belt, and no radius. `corridors.choose_turn` picks the cheaper of the two that fits, per turn | arc 400, attachment 301 cm |
| room at each wall | what the turn there costs, less what the row's band already covers -- and nothing at all where the chain end already faces the wall. An ARC asks one grid step more, because a belt's clearance box is square to the belt and a box on a turning piece that began ON the wall reaches 4.8 cm outside it; an attachment turn has no such piece | 101 cm |
| gap between two rows | whatever the two turns of a trunk still want after the rows' own overhangs, never less than a grid step. **Ours** | 202 cm |
| gap between two rows of ONE group | a grid step: no trunk turns between them, because the spine reaches into each across the corridor. **Ours** | 100 cm |
| machines per row | `floor((floor between the corridors − the widest machine's hard footprint) / pitch) + 1`; a PAIRED row is measured at the wider of its two machines, because its two lines share one pitch | mk1: 3 Constructors |
| closest a column may stand | one belt half width and a centimetre outside the rows' band, so the two do not share a face -- which `belt.capsule` reports as a lap of 0.0 cm. A corridor carrying TWO trunks gets a whole column pitch instead, because a crossing needs room to climb | 80 cm, or 300 |
| floor | a full field of the shipped foundation at half its own box's thickness | 8 m tiles at z 50 |

### The refusals

`ManifoldRows.lay_out` either hands back a placement the validator passes or
raises `NoValidLayout` with one of these, and nothing else. Each is its own
cause: a caller told "row too deep" about a machine class the registry does not
carry would go and look at the wrong thing.

| what went wrong | cause |
| --- | --- |
| the build is too big for the designer | `rows exceed the designer depth` · `rows exceed the designer width` · `row too deep` · `row too tall` |
| the corridor cannot be laid | `corridor needs a bridge that does not fit` · `a trunk would have to run back down the corridor` · `a corridor path has no length` · `a curved leg is longer than a belt may be` |
| the belts cannot carry it | `run exceeds the belt ceiling` · `this spec names no belt` |
| the machine will not take it | `more input items than the machine has belt ports` · `a row drains one of several products` · `a feeder crosses the chain inside it` |
| the spec sends something nowhere | `a row makes something the spec never sends out` · `a row is fed from the corridor on the other side of the build` |
| the power stage | `wire exceeds the maximum length` · `no room for a power pole` · `a machine has no power connection` |
| a bound ran out | `corridor assignment exceeded the budget` (columns tried) · `layout exceeded the budget` (the clock) |
| the extraction left a hole | `the game data does not describe a machine this build needs` · `the game data states no limit this build needs` |
| not this milestone | `fluids are M5` |

The module refuses to raise anything else. The table is
`flab2bp.sfy.layout.refusals.REFUSALS`, shared with the grid-routed strategy and
holding its causes too; the ones above are the manifold's own, plus the five both
raise. `refuse` checks the string against it and raises `ValueError` on a cause
nobody declared; `_row_cause` and
the two mapping tables beside it do the same for a cause arriving from the row
builder, the corridor or the power stage, rather than defaulting to some other
refusal's name. An input no row makes and the spec does not belt in is not in the
list at all: `SfyBuildSpec`'s own validator refuses that spec at construction, so
one reaching the layout stage is a bug in this package and is raised as one.

**What fits.** A row's own band is 15.6 m and two rows plus the gap their trunks
turn in plus a margin at each wall is 35 m, which is more than a mk1's 32 and less
than a mk2's 40. `reinforced-iron-plate-10` is five rows and 110 m of band, which
no designer holds, and it refuses for every mark the game ships.

**A paired build is smaller than that.** Where the spec's own rates say one group
makes exactly what another eats — the same machine count, the same rate per
machine, the same fraction on the odd last machine, and nothing else touching the
item — `flab2bp.sfy.spec.direct_pairs` reports the pair and the two groups are
laid as ONE row facing itself: producers along one line, consumers along another,
one straight belt from each machine to its partner, and no merger chain, no
splitter chain and no trunk between them. `iron-plate-60` is the corpus's own:
2763 cm of band against a mk1's 3200, where the manifold wanted 4200. Nothing is
re-solved to find one — every comparison is an exact `Fraction` out of the spec —
and the placement's description says which rows were paired and on what.

Splitting a wide group pays for width in depth — a row is 15.6 m of band — so the
corpus has **no width refusals left and 28 depth ones**: a build that was too wide
by a machine is now too deep by a row. The exception is a group whose split rows
still fit, which is `concrete-60` in an mk2: four Constructors as two rows of one
recipe.

### How a corridor turns a belt

Two ways, and `corridors.choose_turn` is a pure function of the room at that
corner that picks between them. An **arc** is `quarter_turn`'s quarter circle of
the corridor's radius: one belt, no object, and it spends that radius of both
straights. An **attachment turn** stands a conveyor splitter on the corner with
exactly one input and one output wired — the through input faces the way the belt
arrives and one side output the way it leaves, both read off the registry by
facing — so the path becomes two straight belts meeting on its ports, and it
spends its own 200 cm soft box plus one shortest belt rather than a radius.

With the registry the game ships the attachment is the cheaper everywhere (301
against 400), and it is what makes a mk1 build possible: an arc's radius is spent
at every wall and between every pair of rows. A tighter `mBendRadius` would make
the arc cheaper, which is why the choice is asked per turn rather than decided
once. A belt may not meet or leave an attachment's port on a slope
(`ports.position`) and may not be sloped at the designer wall either (a sloped
belt's clearance box has a corner outside it, which `geom.bounds` refuses), so a
climb in a column is held one shortest belt clear of both.

### One clearance box, placed in one place

An `FFGClearanceData` is a `Min`/`Max` box in the frame of its own
`RelativeTransform`, which is itself relative to the actor, so placing one means
composing two transforms in the right order: the box's scale, then its
`(pitch, yaw, roll)` rotator, then its offset, then the actor's rotation and
translation. Getting a step of that wrong puts a box somewhere the game does not.

`flab2bp.sfy.geometry.placed_box` is the one place it is written — returning
`(centre, axes, half)`, an oriented box — and `box_bounds` beside it projects that
onto the world axes for a caller measuring a band. The validator (`_place_box`,
which adds the flags and the label a finding names), the pole placer
(`power.box_bounds`) and the row builder (`manifold._box_bounds`) all go through
them, so the boxes a build is measured against and the boxes it is judged against
are the same boxes.

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

## Legacy reference: the lattice

The retired `grid-routed` planner packs machines on the build gun's own 1 m
hologram grid and finds belts with a geometric interval router rather than
the hand-written corridors above. Current sections reuse routing helpers,
not this planner's whole-factory packing or horizontal boundary contract.
`flab2bp.sfy.layout.lattice` is the world that router searches: `Lattice` says
where a node is, `Occupancy` says whether a belt centreline may pass one, and
`occupancy_for` flattens a placement onto both.

### Where a node is

Node `(i, j, k)` is world `(−half + 100·i, −half + 100·j, 100·k)` for
`0 ≤ i, j, k ≤ n`, where `half` is `Designer.half_cm`, `n` is the designer's side
in grid steps (`dims × 8` for every mark the game ships) and the `100` is
`limits.hologram_grid_cm` — the build gun's own smallest move, read from the game
and never written here. There is no half-step offset: a node sits on the grid
line, not in the middle of a cell, because what is being placed is a *centreline*
and not a tile.

A belt centreline runs along lattice lines between nodes, so a belt at level `k`
has centreline `z = 100·k`. That is the same lattice the hand-built corridors
already stand on: 200 cm for a row or corridor belt and 300 for a bridge are
levels 2 and 3.

### Which levels a belt may stand on

The slab's top is 100 cm — one grid step — and a grid-snapped machine's belt port
sits one step above that, at 200. So **levels 0 and 1 are impassable everywhere**:
level 0 is inside the foundation and level 1 is the band between the slab and the
ports. Level 2 is the port level and the ground belt level, and nothing routes
below it.

The topmost level and the four outermost *lines* are impassable too, for the
reason `geom.bounds` gives — see entry 5 of the list below — which is why
`Lattice` states the lines and levels a belt may stand on at all, once, and both
the predicate and the flattened array read them from there.

### What a level costs, and how tall a lift may be

R-M3-3 is not only a floor. A move that lands on level `k` pays `0.01 × (k − 2)`
on top of its family price, so a belt prefers the port level and climbs only to
cross something. **That toll is ours**: nothing in the game charges a belt for
height. It is sized so that it can never reorder two paths of different length —
a whole grid step costs `1`, so even the full height of a mk3 designer tolls less
than a third of one extra step. `transitions.level_toll` is the one place it is
written.

A conveyor lift is a vertical edge of this lattice, and how tall it may be is the
game's: `lift_min_cm` 400, `lift_max_cm` 4800 and `lift_step_cm` 100, all read
from the shipped binary, which divided by the grid step is `4 ≤ h ≤ 48` in steps
of one level. The designer caps it again — a lift may not be taller than the
lattice above the port level — so the window a router is handed is
`4 ≤ h ≤ min(48, n − 2)`, which on a mk1 is 4 to 30. Not one of those numbers is
written down: they are `Registry.limits` divided by `hologram_grid_cm`, clamped
by `Lattice.n`.

### What blocks a node

A node at level `k` is impassable for belts when the 158 × 158 × 30 cm box a belt
carries there — twice `BELT_CLEARANCE_HALF_WIDTH_CM` square, twice
`BELT_CLEARANCE_HALF_HEIGHT_CM` tall, and orientation-free because a node does not
yet know which way the belt through it will run — meets any hard clearance box,
the designer wall, or a lift's column box. The boxes are `registry.json`'s, placed
by the one composition `flab2bp.sfy.geometry.placed_box` writes; the lift's is
`validate.lift_box`; the belt's own half extents are `belt.clearance`'s, read out
of `AFGBuildableConveyorBelt::CreateClearanceData`. Soft boxes block nothing, on
the game's own `CT_Soft` marking, so a conveyor attachment — whose only box is
soft — denies a belt nothing at all.

A committed straight run blocks its own nodes and **the two nodes across the run
from each of them**, at its level, for every other net. Both halves are real
collisions rather than simplifications: two belts 100 cm apart lap by 58 cm, and a
belt ending or turning on the node beside a run reaches 50 cm along its own last
segment into the run's 79. Adjacent levels never interact — 100 cm of separation
is more than the 30 cm a belt box is tall.

### The seven places the lattice is stricter than the game

Legality is what the hologram allows; where this lattice allows less, it is ours,
and this list is the whole of it — every entry says what it costs.

1. **Belt pitch is 200 cm.** The game's own closest legal pitch is 158, which is
   not a multiple of the grid step. The lattice can offer 100, which laps by 58
   and is refused, or 200, which clears by 42.
2. **The node beyond a run's last node, along the run, stays free** for a
   perpendicular belt — and only that node. A run's clearance chain stops at its
   last node, so a belt crossing 100 cm past it comes no closer than 21 cm.
3. **R7's one grid step between two machines' hard boxes stays.**
   `buildable.clearance` is `partial`: `AFGHologram::TestClearanceOverlap` was
   never read, so the gap two holograms really need is unknown and this project
   keeps its own.
4. **A belt's own path out of its port is blocked here like any other node.** A
   Constructor's `Output0` sits at `(0, 300, 100)` inside a hard box that runs to
   `y = 500`, so the belt leaving it has to cross its own machine. Opening those
   nodes is not the occupancy's business: they belong to one port's net alone, so
   they travel on that terminal's own reach and are handed to the router per
   query. What the occupancy promises instead is the other half — committing a
   path never takes *ownership* of a node the world already denies, so a repair
   search can never rip a net up in the hope of freeing a node a machine is
   standing in.
5. **The outermost lines and the topmost level are closed.** A centreline on line
   `0` or line `n` hangs 79 cm of its own clearance outside the designer, and one
   at `z = height_cm` hangs 15 cm through the ceiling; `geom.bounds` refuses
   both, and `geom.bounds` is itself this project's rule — the designer *clips*
   what overhangs rather than refusing it. The cost is one line in from each
   wall and the top level, out of `n + 1` per axis.
6. **A rotated clearance box is blocked by its world-axis bounding box.** A box
   turned by a quarter turn — which is every box a grid-snapped build places — is
   its own AABB, so this costs nothing today. A box at any other yaw is
   over-covered: a Constructor's 800 × 1000 box turned 45° bounds to
   1273 × 1273, about three extra lines in `X`. It is stricter, never laxer, and
   `_mark_box` says so.
7. **Belt geometry is bounded by one box per spline segment, not by the
   game's chain.** `belt.clearance` lays a chain of short boxes hugging the
   curve; `_belt_boxes` lays one axis-aligned box over the whole segment, sized
   from the four Bézier control points, containing the curve with clearance
   padding. This is a conservative routing proxy, including endpoint padding,
   not an exact reconstruction of the game's chain. On a turn it also denies
   the corner the arc never reaches: up to `r(1 − 1/√2)`, about 58 cm on a
   200 cm quarter turn.

### The flat array and the predicate must agree

`Occupancy.free` is the predicate and `Occupancy.flags` is the flat byte array the
router's kernel actually searches. They must agree node for node. A `free()` that
knows something the array does not is the DSP router's worst failure mode: the
search happily returns a path, the committer asks `free()` about each node it is
about to build on, finds one refused and drops the whole net — every round, having
learned nothing, because nothing in the search was told. `base` is the array as
the world alone left it, before any path was committed, and rip-up restores from
it rather than writing "passable", because a ripped node is not necessarily a free
one.

The loops that flatten a placement onto the lattice are the only per-node Python
in the grid-routed strategy. They are written as such: a box's node range is
computed once per axis and the resulting slab is written a column at a time, never
one predicate call per node per box.

### Object collision is not centreline passability

`Occupancy.static_bounds` retains the physical obstacle bounds from flattening.
The routing loop separately caches bounds for committed realised objects and
removes/restores them with their net. Lift-column admission compares those
bounds, not two already belt-inflated node masks. A virtual belt overlapping
both a lift and a machine does not prove that the lift overlaps the machine:
the regression's lift starts at Y=505 cm beside a Constructor ending at Y=500 cm.
Their 5 cm gap is legal under the same box contract that previously rejected it.

Centre-shaft, floor, roof and designer checks remain. A linked belt's endpoint
segment may meet its lift; the rest of that belt is still collision-checked,
including a later segment that returns through the shaft. Unknown foreign
occupancy without registered physical bounds fails closed. Belt bounds retain
the conservative Bézier-hull proxy above; this does not claim that the unread
game clearance rule has become known.

### Turn and connector legality belong in search

Satisfactory supplies a finite motion policy to the shared geometric interval
kernel. Its product state retains heading, slope-leg progress, the previous
turn's geometric reach and the outgoing room still owed to that turn. Each
state has its own cost envelope over the shared occupancy/history profiles.
Transient progress translates whole intervals; mature straight states retain
whole-run closure. This is the same router, not a cell-priority fallback. DSP
queries without a policy keep their existing graph semantics.

The policy reads turn options, merged-ramp reserve and lift geometry from the
registry-backed geometry helpers. A lift's belt connection is flat; a ramp via
is not a corner or a cut. Actual connector facing and off-grid stub length enter
the initial and accepting states. A goal coordinate alone is not success.
Attachment turns additionally require flat legs and an eligible object standing
domain. Turn cost and geometric reach remain distinct: a shared minimum belt
is paid once, not once at each adjacent attachment.

Search preserves the selected primitive and turn actions, including a final
action for a corner into a sink stub. The realiser validates and replays that
witness rather than making a new greedy choice. Fixed authored paths use the
same geometry predicates and a finite option resolver. Certified paths are not
subsequently shortened by coordinate-only loop deletion or rewritten to repair
lift landings.

Motion-constrained queries run forward. Their exhaustion is distinct from a
physical sealed pocket and does not manufacture wall blame. Policy preparation,
state search and certification consume the original deadline and work ledger.
Only states whose remaining geometric obligations are equivalent are merged;
state growth and interval fragmentation can still exhaust the budget.

This certificate does not replace full physical admission. Lift shafts and
object collisions, maximum spline chunking and the final emitted-belt chord
minimum remain checked afterward. A first candidate may fail those checks;
only a fully admitted and validated placement is a successful factory.

## Legacy reference: Grid-routed

`flab2bp.sfy.layout.grid`'s `GridRouted` is retained as a reference, not an
available production strategy. It is a **loop** rather than a pipeline:
machines stand where a CP-SAT no-overlap model puts them on the hologram grid,
and belts follow the router's results.

### The loop

A fluid is refused first, before a limit is read or a lattice is built — the same
sentence `ManifoldRows` refuses on, asked of FactorioLab's own dataset. Then the
limits are read once into one `Measures`, the `Lattice` is built from the designer
and the registry, and for each deterministic arrangement under the deadline:

1. `packer.pack` stands the machines, seeded by the arrangement number so that two
   runs of one spec walk the same seed sequence, and priced by what the last
   arrangement's routing learned;
2. `lattice.occupancy_for` flattens them;
3. `grid_nets.nets_for` says what has to be belted;
4. `rrr.route_all` negotiates every net across rip-up rounds;
5. with nothing stranded, `grid_power.place_on_free_nodes` stands the poles on the
   occupancy the router left holding its **best** round — not its last — and
   `floor.foundations` lays the slab. The object counter starts past the ids the
   packer spent on its machines, so one numbering covers the build.

Where a net *is* stranded, its id and the router's blame become a `Feedback`: the
previous evidence decayed by `packer.DECAY` (0.85) at the boundary, plus one
`STRANDED_WEIGHT` per stranded net and one node of belt per `BLAME_WEIGHT` the
router charged (`HOT_NODE_SCALE`). Failed-net weights increase the span objective.
Hot-node weights select at most eight distinct XY positions whose belt-inflated
machine footprints are kept clear in the next arrangement. If those keep-outs
make the packing model infeasible, it retries without them under the same absolute
deadline: routing history is search guidance, not evidence that a factory cannot fit.

### The wall, and how it is split

The deadline is `absolute_deadline` when a caller gives one (a race hands both
strategies the same `time.monotonic()` frame) and `time.monotonic() +
time_budget_s` otherwise. The first `INITIAL_ARRANGEMENTS` (3, ours) reserve equal
shares of what is *left*. An early finish hands unused time on. From the third
arrangement onward the slice ends exactly at the original deadline, so additional
arrangements may use the remaining wall without a new count cap. Inside a slice
the packer takes `PACK_SHARE` (two thirds, ours) and the router the rest. The
packer also has a deterministic-work limit.
The recovery measurement found that a 1.67-second first pack returned an unroutable
incumbent for plate-60/mk1, whereas a three-second pack returned an arrangement
that routed in one round. The user-approved continuation preserves that initial
schedule and the total deadline. It converts concrete-60/mk1 within the original
15-second budget; stopping after three had refused it with time left.

Every packing failure keeps its own cause, whether it is the first arrangement
or a later one. An earlier routing miss cannot disguise a later packing timeout
as a ruled geometric refusal. A pack that proves the machines do not stand
(`the packer found no arrangement`) remains distinct from a search bound.

### The lift class

`route_all` takes one lift class for the whole build, so it has to be the one that
carries the fastest piece of belt the router can lay. No piece of a net's tree
carries more than that net's own total rate, so the heaviest net's tier bounds
every piece; the lift is the buildable whose `native_class` is
`FGBuildableConveyorLift` and whose `belt_speed_per_min` equals that tier's
conveyor's. Both numbers are the game's, on both sides — a registry that renamed
either class would still pair them.

### The refusals

Every cause is one of `refusals.REFUSALS` and every stage's own error is mapped
here, with no default anywhere: a cause this module has not been taught raises
`ValueError` at the point of use rather than reaching a caller wearing another
cause's name (the discipline `strategy._row_cause` states).

| raised by | cause | refusal |
| --- | --- | --- |
| `packer.PackError` | `the packer found no arrangement` | itself — a pack has one reading |
| `packer.PackError` | `packing exceeded the budget` | itself |
| `grid_nets.NetError` | `ceiling` | `run exceeds the belt ceiling` |
| | `lattice` | `a port is off the hologram lattice` |
| | `ports` | `more input items than the machine has belt ports` |
| | `data` | *the game data does not describe a machine this build needs* |
| `realise.RealiseError` | any of `corner`/`leg`/`lift`/`stub` | `a belt could not be laid` |
| `power.PowerError` | `wire`/`room`/`port`/`data`/`limits` | as the manifold names them |
| `corridors.CorridorError` | `limits`/`data` | the two shared extraction causes |
| `manifold.RowError` | by message | `strategy._row_cause`'s table |
| `budget.BudgetExhausted` | — | by stage: packing, or routing |

The continuation loop exits on its global deadline as `routing exceeded the
budget`, retaining the last stranded-net details. These diagnostics do not prove
game impossibility. Packing exceptions retain their stage-specific cause; an
earlier geometric miss never turns a later timeout into an acceptable corpus
pin. A placement finishing after the global deadline is not returned.

### What is ours

`INITIAL_ARRANGEMENTS` (3 reserved initial shares), `PACK_SHARE` (two thirds), `STRANDED_WEIGHT` (1),
`HOT_NODE_SCALE` (one node of belt per `BLAME_WEIGHT`) and `WORKERS` (1) are search
policy, not game limits. Single-worker CP-SAT avoids thread races, but a wall-time
limit still makes the returned incumbent sensitive to host load.

Before CP-SAT, necessary packing bounds can prove a refusal without consuming
the search budget. The first sums each machine's minimum complete-yaw rectangle
area. Three conservative size scales add stronger bounds: an exact one-dimensional
knapsack certifies each transformed side capacity, then transformed minimum-yaw
areas are compared against that capacity squared. These use the model's existing
rounded, half-grid-inflated hard rectangles, never apron area or routing blame.
Failure to find a certificate says nothing about feasibility; it cannot turn
an UNKNOWN solve into an impossibility claim.

The description counts the exact `Realised.turns` replayed by the realiser:
arcs and attachment corners separately, plus authored lift transitions. Tap
attachments have no turn entry, so subtracting the attachment-turn count from
all attachments gives the tap count without guessing from path geometry.

### Wall branches and realisation

A wall-sourced or wall-sunk item may use several independent boundary belts when
the committed tree has no legal tap. Each connected component carries its own
balanced share; a machine supplying the same item as an external input does not
erase the external obligation. The description lists every physical wall
crossing, not the inset routing node. These are the player's connections to make,
not an inferred single trunk.

Tap queries start or end one outward flat step beyond the attachment's actual
side port, outside the committed trunk's shadow. That displacement is explicit
connector-to-access stub credit, not a fictitious physical port or an
uncertified path rewrite. The selected stub and native path are staked as
separate adjoining segments. Unselected ports are not opened as transit cells,
so they cannot become ramp vias through the trunk's shadow.

A candidate tap must preserve legal witnesses for both remaining parent pieces.
Cached fixed-path prefix/suffix relations test the proposed cuts without
emitting objects or searching a graph per candidate. Selecting a tap commits
the updated parent and child witnesses with ownership; rip-up and restore keep
the proofs and physical claims together. This applies to splitters, mergers
and required external-input branches.

The grid packer and realiser use the same attachment-turn requirement:
`reach + shortest_belt` (201 cm, three grid steps for the current registry),
rather than the manifold corridor's conservative 301 cm. On a shared flat leg,
two attachment reaches need only one minimum-length belt between them, not two.
The attachment box is soft, but it must still remain inside the designer:
`Lattice.object_lines` intersects belt-legal nodes with the direct object-box
wall bound. Arcs need no attachment box. Attachment candidates also require
horizontal belt tangents on both ports: an incline's spare run does not create
a horizontal tangent. Those eligibility checks happen before turn selection,
so a legal arc remains available when an attachment is not. Tap candidates use
the same object bound and leave clearance on both sides of a ramp.
The packer's port apron includes the endpoint at that many steps from the port;
subtracting one step would let another machine block the first-free approach.

Routing can still refuse or exhaust its bounds. Turn-aware reachability does
not prove that every shaft is clear, every final spline chunk is long enough,
or the current packing can be completed. Changing designer size need not
improve a time-limited packing incumbent. These are measured outcomes, not
proof that the factory is impossible. The M3 audit records the actual
per-strategy matrix; budget exhaustion and invalid geometry are not ruled
geometric refusals.
