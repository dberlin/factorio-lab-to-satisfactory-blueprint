# The Satisfactory placement model

`flab2bp.sfy.layout` is where a build stops being rates and becomes geometry.
Three modules carry it:

| module | what it holds |
| --- | --- |
| `splines.py` | the spline shapes the game's own router builds, and how it measures them |
| `model.py` | `SfyPlacement`: every object, its class, its id and where it stands |
| `emit.py` | `emit` writes a placement into a `.sbp`, `decode` reads one back out |
| `validate.py` | the neutral judge: is this placement one the build gun would accept? |

Distances are centimetres and the axes are Unreal's, left-handed, `+Z` up.

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

### The sixteen checks

| id | rule | effect | what it enforces |
| --- | --- | --- | --- |
| `geom.bounds` | `project` | — | every origin, clearance-box corner and spline point inside `[-half, half]² × [0, height]` of the designer, sized from `designer_dims` and the shipped foundation's footprint |
| `geom.hard_clearance` | `buildable.clearance` | refuse | no two **hard** clearance boxes lap, by a separating-axis test on the boxes' full `RelativeTransform`. Soft boxes may share space — that is how a machine stands on a foundation. A box flagged `ExcludeForSnapping` is still tested: the flag excludes it from *snapping*, not from clearance |
| `belt.capsule` | `project` | — | a belt's clearance chain — `Min = (-L/2, -79, -15)`, `Max = (L/2, 79, 15)` per segment, from `belt.clearance` — laps no other belt's and no hard box |
| `belt.max_length` | `belt.max_length` | refuse | `spline_length` ≤ `mMaxSplineLength` (`limits.belt_max_spline_cm`), arc length, strict |
| `belt.min_length` | `belt.min_length` | refuse | the **polyline** between stored points > `mMeshLength × 0.5001` (`limits.belt_min_length_cm`), strict |
| `belt.incline` | `belt.incline` | refuse | per chord, <code>&#124;π/2 − acos(clamp(u.Z, −1, 1))&#124;</code> ≤ `mMaxIncline × 0.017453292` |
| `belt.curvature` | `belt.curvature` | refuse | `step / acos(A·B)` ≥ `mBendRadius × 1.5 − 15` at every one of `RoundToInt(L × 0.02)` samples (see below) |
| `ports.connected_once` | `project` | — | every belt end wired exactly once, no connection carrying two belts, nothing wired to a `snap_only` or `unknown` connection |
| `ports.direction` | `belt.snap_directions` | snap | a link runs output → input, and meets a belt by the end `flow` names |
| `ports.position` | `project` | — | a belt's ends sit within 1 cm of the ports they are wired to and leave within 0.01 rad of the port's facing |
| `flow.capacity` | `project` | — | a belt carries no more than its mark does, and every machine input is fed at the group's per-machine rate |
| `flow.balance` | `project` | — | per item, rows produced + belted in ≥ rows consumed + sent out |
| `spec.machines` | `project` | — | classes, counts, recipes and clocks match the spec |
| `slab.under_every_foot` | `project` | — | every machine's hard footprint is covered by foundation tops at its own `z` |
| `power.wires` | `project` | — | every wire inside `wire_max_cm`, every connection inside `max_connections`, every machine on a pole |
| `roundtrip` | `project` | — | `decode(emit(placement)) == placement` |

`flow.capacity`, `flow.balance` and `spec.machines` need an `SfyBuildSpec`;
without one they are listed in `skipped` rather than passing in silence.

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
  half, `cvtss2si` (which rounds half to **even** under the default `MXCSR`),
  arithmetic-shift back. `_round_to_int` reproduces it with Python's own
  half-to-even `round` and `>> 1`.
* **The radius is arc over angle**, `step / theta` — `xmm14` holds `step` and
  `xmm0` is `acos`'s answer. It is not a circumradius and not a discrete
  curvature; the numerator is the sample spacing, the same for every sample.
* **A straight pair divides by zero and passes.** `theta == 0` gives `+inf` in
  hardware and `comiss/jb` does not take the branch. `validate.py` yields `inf`
  rather than skipping the sample, so the two agree.

`GetSafeNormal2D` zeroes `Z` (the `1e-8` guard and reciprocal square root at
`0xaa53d6..0xaa53e7`), so a climb is **not** curvature here — `belt.incline`
judges that separately, on the chords rather than on the curve.

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
| `BELT_CONNECTION_CM` | 5 | two belts joined end to end lap by a sliver where their tangents differ; the game's own tolerance is inside the unread `TestClearanceOverlap` |
| `PORT_CM` / `PORT_ANGLE_RAD` | 1 cm / 0.01 rad | the slack on M1b's stated assumption that a belt begins at its port and leaves along its facing |
| `CAPSULE_SEGMENT_CM` | 50 | `belt.clearance` leaves `GetNextDistanceExceedingTolerance` unread, so the game's segment **lengths** cannot be reproduced; shorter boxes hug the spline more closely than the game's, never less |
| `CURVATURE_SAMPLES` | 2048 | parameter steps used to invert arc length; a resolution, four orders below the 50 cm the rule samples at |
| `DEG_TO_RAD` | 0.017453292 | the game's own `float` constant at `0xaa57de`, not `math.pi / 180`: the incline branch is strict, so at exactly 35° the two spellings disagree |

### Two places the validator is deliberately blind

* **A belt is not tested against a buildable it is wired to** (`belt.capsule`).
  Forced by the game's own numbers, not by convenience: a Constructor's
  `Output0` sits at `(0, 300, 100)` inside a hard box that runs to `y = 500`, so
  a belt that starts at its own port starts 200 cm inside the machine feeding
  it. The game must exclude the snapped building somewhere inside
  `TestClearanceOverlap`, which is unread.
* **`power.wires` stands aside on a placement with no wires**, because `emit`
  refuses to write one until Task 9 decodes the power-line trailer. It says so
  in an `INFO` finding rather than reporting a clean pass.
