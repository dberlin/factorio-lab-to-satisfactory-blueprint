# Interim corpus probes — why `_COATER_WEST_CHANNEL` moved 3 → 4

These three runs are not plan steps. They were run by the controller during Task 1, out of plan
order, because the plan's Task 1 change failed its own regression sweep and the plan deferred the
decisive measurement to Task 7. Measuring first was cheaper than committing Tasks 1-6 blind and
discovering the loss at the end.

Every run used the identical command and budget as the committed baseline, so the cells pair:

```
python3 scripts/audit.py --tier stress --strategy both --budget 30 --json <file>
```

| file | `_COATER_WEST_CHANNEL` | corpus clean | area ratio | p95 wall |
|---|---|---|---|---|
| `baseline.jsonl` (committed, Task 0) | 3, unchanged code | **72 / 72** | 1.0000 | — |
| `interim-task1.jsonl` | 3, with the new seat bound | **47 / 72** | 1.0343 | 31.3 s |
| `interim-task1-wc4.jsonl` | 4 — **the value shipped** | **70 / 72** | 1.0345 | 31.3 s |
| `interim-wc5.jsonl` | 5 — probe only, reverted immediately | **71 / 72** | 1.0268 | 31.4 s |

## Why 3 does not work

`_coater_keepout_hits` inflates the coater's 3x1 body about its seat, so a seat at lane index `i`
reserves columns `i - half_span .. i + half_span`. Two bounds must hold at once:

* head clearance, which this plan adds: `i >= 1 + half_span` — the lane head is the only cell of a
  sprayed lane a router path can reach, so it is where every producer net merges, and a body over it
  was the measured real-world defect;
* machine-band clearance, which was always there: `i + half_span <= west_channel - 1`.

Together they require `west_channel >= 2 + 2 * half_span`, which is 4 at `half_span = 1`. At 3 the
window is not narrowed to one candidate — it is **empty**. The design spec §5.1 claims 3 "leaves
exactly one candidate at `ox-1`"; that is the error, because `ox-1`'s body always covers strip column
0 and is rejected by the keepout whenever a machine sits there.

## What the widening actually costs

Less than it looks. `plan_strips` used to hand a `+1` preclearance lift to sprayed strips that needed
it, so those strips were **already 4 wide** before this change; for them the constant move changed
nothing. The measured area cost falls only on strips that never took the lift — which is why the
overall area ratio is 1.0345 while the **median cell is exactly 1.0000** and 34 of 70 paired cells are
byte-identical in area. The mean is carried by a few small specs where one column is a large relative
jump (`quantum-chip` spec 1 at 1.50, `energy-matrix` spec 2 at 1.30).

A side effect worth knowing: with the base channel at 4, no relation in any spec in the test suite is
risky any more, so the preclearance lift is currently unreachable in production. It is retained
because a narrower channel or a larger addon footprint would make it live again.

## The two cells lost at the shipped value

* `sequence-pair universe-matrix/all-products` — all four islands report "deadline exhausted"; the
  corpus's largest spec, and a search-cost loss rather than a correctness one.
* `freeform super-magnetic-ring/all-products` — a genuine validator rejection,
  `band 160 geom.collide (106, 572)` and `(219, 584)`.

## A reporting trap in `audit.py`, for whoever reads these files next

Most refusals in `interim-task1.jsonl` render as
`every packing that wired was rejected by our own validator (prolif.sprayed_cargo_reaches_machines)`.
**The validator never ran on those cells.** `freeform.py` does
`_retain_refusal(rejected, exc.failure or "prolif.sprayed_cargo_reaches_machines")`, so any
`_Unseatable` carrying no `failure` is reported under that check's name. The true message on those
cells is of the form `the iron-ore coater at (2, 0, z=0) has a full-body keepout intersecting Arc
Smelter at (3, 1, z=0)`. `_sweep` similarly launders `_Unseatable` into `NoValidLayout`'s generic
wording. Neither is fixed on this branch — both are outside this plan's file list — but both cost real
diagnosis time and should be fixed separately.

## Load figures

The `uptime` lines inside `baseline-audit.txt` and the three `interim-*-audit.txt` files are Linux
load averages, captured before the project switched to reporting CPU pressure as the five-second mean
of runnable processes (`vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}'`). On this box the
load average is dominated by I/O wait and should not be read as CPU saturation. Later evidence uses
the runnable-process mean instead; these files were not re-run to relabel them.
