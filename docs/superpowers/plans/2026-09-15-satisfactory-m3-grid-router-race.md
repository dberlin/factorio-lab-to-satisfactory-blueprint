# Satisfactory M3: grid packer, geometric belt router, strategy race Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second Satisfactory layout strategy, `grid-routed`, that packs machines on the hologram grid and lays every belt with a geometric interval router in the shape of `flab2bp.layout.geometric_router` (straight runs as intervals on the 1 m lattice, inclines as ramp moves, conveyor lifts as vertical edges, turns made by arcs, attachments or lifts wherever each costs least), raced against `ManifoldRows` under one `SfyLayoutStrategy` protocol and measured on the corpus; plus the three chores M2 left (the `Tier` seam, the pole hologram grid, the lift clearance width).

**Architecture:** `flab2bp.sfy.layout` gains a lattice world that flattens the placement model's hard boxes, belts, lifts and attachments into the byte-per-node passability the DSP kernel searches; a transitions table in the DSP router's shape (flat step, 2:1 incline with a via node, lift as a `(0, 0, ±h)` edge for `h ≥ 4`); a per-net query wrapper that calls `flab2bp.layout.geometric_router.route` and reads its result exactly as `routing_domain._geometric_search` does; a realiser that turns a lattice path into `BeltRun`s, `AttachmentObj`s and `LiftObj`s through the M2 `Route`/`lay_path` machinery and `choose_turn`; a rip-up-and-reroute loop with history and blame; a CP-SAT packer whose objective is net length plus routing feedback; a `GridRouted` strategy that assembles them; and a serial race in `pipeline.build` that picks by the spec's own key (fewest blueprints, then occupied volume, then belt length). Nothing here is a cell-by-cell search: every path is found by the interval kernel, and Python touches cells only to flatten occupancy and to commit a path.

**Tech Stack:** Python 3.14, ortools CP-SAT (already a dependency), the existing Cython kernel `flab2bp.layout._geometric_kernel` (reused, not copied), pytest; `tools/sfy-native` (Rust) for the one new game fact.

**Spec:** `docs/superpowers/specs/2026-09-13-satisfactory-target-design.md`, sections 8, 9, 10, 12 and 14. Reference material for implementers: `.superpowers/sdd/m3-router-reference.md` (git-ignored, in the worktree; 6567 lines) quotes the DSP router end to end, the DSP packers, the sfy side as it stands, the lattice arithmetic (section 4) and the test conventions (section 5); every task below names the sections its implementer reads. `.superpowers/sdd/m2-interface-reference.md` quotes the M2 signatures.

**Milestone order (ruling R-M3-0).** Spec section 12 lists the grid-routed strategy under M5 and the stacking contract under M3. The user ruled on 2026-09-15 that M3 does real packing and belt routing and that the router is a geometric line router, so this plan is spec section 9's strategy 2 brought forward; the stacking contract, passthroughs, manifest, zip and the web viewer become M4, and fluids, power completion and the continuous CP-SAT experiment M5. Task 1 rewrites section 12 to say so.

## Global Constraints

Binding user rulings, carried into every brief and review as compliance checks (violations are corrected, never listed):

1. **Game data over inference.** Every game fact comes from Docs.json, the cooked assets, the public headers or the shipped binary via its PDB, with the source cited. The blueprint corpus is never a source, cross-check, corroboration, oracle or evidence for a game fact or a legality rule; it is format fixtures only. Port names are not a source. FactorioLab's dataset and source code are the authority for what FactorioLab computed and nothing else.
2. **Legality is what the hologram does or allows.** A bound the validator or a placer enforces names the rule in `hologram_rules.json` it comes from; only a rule with `effect: "refuse"` turns a placement away; a `partial` rule is a bound nobody may assume they know, and no bound is invented in its place. Stricter rules of this project's own are stated as ours, in the docstring and in `docs/sfy-layout-model.md`.
3. **FactorioLab's chosen flow is authoritative.** Machine counts, recipes and rates come from the flow export; nothing re-solves the flow.
4. **Format guarantees hold.** All 49 fixtures round-trip byte-identically; `decode(emit(placement)) == placement` for every placement any strategy returns.
5. **Process.** Subagents run on opus. Every commit ends with the two attribution lines the session reminder gives. `GIT_EDITOR=true`, always `-m`, explicit-path `git add` AND a pathspec on `git commit` (the index is shared with other sessions), never interactive git. Serena is a shared server: implementers use Read/Edit. Scratch files are prefixed with the task name under the session scratchpad; never `pkill` by pattern. `uv run pytest tests/sfy -q; echo exit=$?`: the summary line never prints here, the exit code is the signal. The worktree runs `uv sync` first and verifies `flab2bp.__file__` is inside it. Generated JSON is written only by its script; the drift tests must pass. Ruff (`E,F,I,UP,B,SIM`, 100 columns) and mypy strict over `src` and `tests`.
6. **Units.** Centimetres, degrees, items per second (`Fraction`) inside the spec and rates; the Unreal frame (Z up, yaw turns +X toward +Y). Every placement coordinate is a float in cm; no float reaches the rates.
7. **The router is a geometric interval router.** Paths are found by `flab2bp.layout._geometric_kernel.search_intervals` through `flab2bp.layout.geometric_router.route` and nothing else: no cell-priority search, no A*, no Dijkstra over cells, in Python or otherwise. Python code above the kernel works on intervals, runs and paths; it visits individual lattice nodes only to flatten occupancy and to commit or rip up a path. A reviewer who finds a per-cell search loop anywhere under `flab2bp.sfy` rejects the task.
8. **Turn primitives are options, not a template.** At every turn the realiser picks whichever of arc, attachment or lift costs the least space where it stands, and a lift is charged for the column it blocks. Nothing in this plan reproduces the manifold's rows, corridors or chains as a target shape; the manifold is the opponent in the race and a source of reusable geometry helpers, nothing more.
9. **Import cost.** `tests/sfy/test_corpus.py::test_importing_the_satisfactory_corpus_loads_no_placer_or_router` stays green: nothing reachable from `flab2bp.bench.sfy_corpus` imports `flab2bp.layout.geometric_router`, `_geometric_kernel`, `freeform`, `routing_domain`, `finalize` or `_sequence_kernel`. The refusal vocabulary and the strategy names move into leaf modules; the router imports the kernel inside the function that calls it.

Controller rulings for M3 (recorded so no implementer re-decides them):

- **R-M3-1. The lattice is the hologram grid, addressed by nodes.** Node `(i, j, k)` is world `(-half + 100 i, -half + 100 j, 100 k)` for `0 ≤ i, j ≤ n` and `0 ≤ k ≤ n`, `n = dims × 8`, `half = Designer.half_cm`; the 100 is `limits.hologram_grid_cm` read, never written. A belt centreline runs along lattice lines between nodes; a belt at level `k` has centreline `z = 100 k`. The slab is level 1, machines stand on it, and every belt port of a grid-snapped machine sits at level 2. A machine's centre stands on a node; a test over the registry proves every belt port of every machine the corpus uses then lies on a lattice line in the port's own transverse axis (they do: port `x` and `y` offsets are multiples of 100 except the Manufacturer's inputs at `y = -875`, which lie on a lattice line in `x` and 25 cm off one along their facing). A machine whose ports violate that refuses with `a port is off the hologram lattice`.
- **R-M3-2. Occupancy is conservative and stated.** A node at level `k` is impassable for belts when the 158 × 158 × 30 cm box centred on it (twice `BELT_CLEARANCE_HALF_WIDTH_CM` square, twice `BELT_CLEARANCE_HALF_HEIGHT_CM` tall, orientation-free) meets any hard clearance box, the designer wall, or a lift's column box. A committed belt blocks its own nodes and their four lattice neighbours at its level for every other net; adjacent levels never interact (belt boxes are 30 cm tall, levels 100 cm apart). This forbids a perpendicular belt passing one node beside a committed one, which the game's boxes would allow; it is this project's own simplification, recorded in `docs/sfy-layout-model.md`, measured in the evidence, and listed as a lever.
- **R-M3-3. Levels 0 and 1 are impassable everywhere** (the slab and the band below the ports). Level 2 is the ground belt level. Levels above cost a toll of `0.01 × (k − 2)` per step so the router prefers the ground and climbs only to cross. Legal lift heights are `4 ≤ h ≤ min(48, n − 2)` levels (`lift_min_cm`, `lift_max_cm`, `lift_step_cm`, read).
- **R-M3-4. Belt legs between cuts are at least two nodes.** A belt is cut only at a port, an attachment or a lift; `belt.min_length` refuses at or under 100.02 cm, so a cut one node from another is illegal and the realiser never authors one; a port that stands on a node may take a lift directly (no belt at all); a port off a node (the Manufacturer's inputs) always takes a belt whose first cut is at least one node past the first node on its facing ray.
- **R-M3-5. Faces as in M2 (R3):** external inputs enter through the `-Y` wall and outputs leave through the `+Y` wall, at any `X` the router chooses; the wall node line at level 2 is the goal interval.
- **R-M3-6. The race is serial in-process.** `pipeline.build(strategy="best")` runs each strategy in turn under an equal share of the budget in one `time.monotonic()` frame and picks the validator-clean placement with the smallest `(blueprints, occupied volume, belt length, strategy index)`. A multiprocess race is not M3.
- **R-M3-7. Nets are routed as trees by sequential two-pin queries with taps.** The first sink of an item is routed from its source; each later sink is routed from the set of legal tap nodes on the item's committed tree (interior nodes of a straight run with at least two straight nodes on either side), and the tap becomes a splitter standing on that node; sources merge the same way with mergers. Belt tiers are assigned after the tree is known, from the rate every segment carries, and a segment over the ceiling refuses per R5.
- **R-M3-8. A lift's clearance width comes out of `sfy-native` or nothing.** Task 3 makes the tool quote the `.data` initialiser of `AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D` through its PDB symbol. If that fails, the lift column keeps the M2 reading (the connector `mClearance` of 200 cm as the full width, this project's own, `_CAPSULE_UNREAD`) and the plan says so in the evidence.

---

## File structure

| File | Responsibility |
|---|---|
| `src/flab2bp/sfy/strategy_names.py` | leaf: `SFY_STRATEGY_CHOICES`, `SfyStrategyName`, `SFY_PRODUCTION_STRATEGIES` |
| `src/flab2bp/sfy/layout/refusals.py` | leaf: `REFUSALS` (both strategies' vocabulary, sectioned), `refuse()` |
| `src/flab2bp/sfy/layout/protocol.py` | `SfyLayoutStrategy` protocol |
| `src/flab2bp/sfy/layout/measure.py` | `Measure`, `measure(placement, registry)`, `race_key` |
| `src/flab2bp/sfy/layout/floor.py` | `foundations(designer, registry, ids)` shared by both strategies |
| `src/flab2bp/sfy/layout/lattice.py` | `Lattice` (node ↔ world, flat index), `Occupancy` (flags, owners, history), flattening from placement objects, the `free()` predicate |
| `src/flab2bp/sfy/layout/transitions.py` | `sfy_transitions(levels, lift_heights)` in the DSP transition shape |
| `src/flab2bp/sfy/layout/router.py` | `route_net(...)`: one query through `geometric_router.route`, budget attribution, blame census |
| `src/flab2bp/sfy/layout/realise.py` | lattice path → `Route` legs, cuts, `choose_turn`, lifts; `realise(path, ...) -> Realised` |
| `src/flab2bp/sfy/layout/grid_nets.py` | nets from the spec, terminals and tap sets, tier assignment |
| `src/flab2bp/sfy/layout/rrr.py` | the rip-up-and-reroute loop over nets, history and pressure |
| `src/flab2bp/sfy/layout/packer.py` | CP-SAT machine packing on nodes, feedback terms, arrangements |
| `src/flab2bp/sfy/layout/grid_power.py` | poles on free nodes, wires |
| `src/flab2bp/sfy/layout/grid.py` | `GridRouted` strategy |
| `src/flab2bp/sfy/pipeline.py`, `src/flab2bp/cli.py`, `src/flab2bp/web/jobs.py` | strategy selection and the serial race |
| `src/flab2bp/bench/sfy_corpus.py`, `scripts/sfy_audit.py` | per-strategy cells and pins |
| `src/flab2bp/bench/tier.py` | `Tier` as a leaf (the M2 chore) |
| `tools/sfy-extract/Program.cs` | `HologramLimits` walks the class's supers (the M2 chore) |
| `tools/sfy-native/src/main.rs`, `scripts/sfy_native_rules.py` | `.data` initialiser quoting through a PDB symbol; `lift.clearance` width |
| `scripts/sfy_checkpoint3.py` | checkpoint 3: a grid-routed build with a lift and an attachment turn |
| `docs/sfy-layout-model.md`, `docs/superpowers/specs/...design.md` | the lattice, the router, the race; spec sections 9, 12, 15 |

Review batches (user ruling: long plans get batch reviews): **A** = Tasks 1, 2, 3 (chores and seams); **B** = Tasks 4, 5 (lattice, transitions, router); **C** = Tasks 6, 7 (realiser, nets and the loop); **D** = Tasks 8, 9 (packer, power); **E** = Tasks 10, 11 (strategy, race, audit, evidence); **F** = Task 12 (checkpoint) with the final whole-branch review.

---

### Task 1: milestone order, the `Tier` seam and the pole hologram grid

**Files:**
- Modify: `docs/superpowers/specs/2026-09-13-satisfactory-target-design.md` sections 9 (strategy 2's sentence) and 12 (milestones)
- Create: `src/flab2bp/bench/tier.py`; Modify: `src/flab2bp/bench/corpus.py` (re-export), `src/flab2bp/bench/sfy_corpus.py` (import from the leaf), `tests/sfy/test_corpus.py`
- Modify: `tools/sfy-extract/Program.cs:~652` (`HologramLimits`), regenerate `src/flab2bp/sfy/data/registry.json` per `docs/sfy-regenerating-game-data.md`; Test: `tests/sfy/test_registry.py`

**Interfaces:**
- Produces: `flab2bp.bench.tier.Tier` (the enum moved verbatim; `flab2bp.bench.corpus.Tier` stays importable as a re-export); `Buildable.grid_snap_cm` populated for `Build_PowerPoleMk2_C` and `Build_PowerPoleMk3_C`.

- [ ] **Step 1: Spec.** Section 9 strategy 2: replace "routed by a 3D orthogonal router on the lattice" with "found by a geometric interval router in the shape of the DSP `geometric_router`: straight runs as intervals on the 1 m lattice, turns where runs meet, inclines as 2:1 ramp moves, lifts as vertical edges; never a cell-by-cell search". Section 12: M3 = this plan (packing, routing, race, checkpoint 3 = a grid-routed build pastes and runs); M4 = stacking contract, lifts through passthroughs, manifest, zip, web viewer, checkpoint 4 = a two-blueprint stack auto-connects; M5 = fluids, power completion, the continuous CP-SAT experiment, the bench. Add one line under section 14 pointing at R-M3-0. Commit: `Reorder the Satisfactory milestones: packing and routing before stacking`.
- [ ] **Step 2: Tier leaf.** Failing test in `tests/sfy/test_corpus.py`: extend `test_importing_the_satisfactory_corpus_adds_no_dsp_module_of_its_own` so that importing `flab2bp.bench.sfy_corpus` in a fresh interpreter loads **no** `flab2bp.dsp.*` and no `flab2bp.rates` module at all (today it is "no more than `bench.corpus` pulls", which is nine). Move `Tier` into `bench/tier.py` importing nothing but `enum`; `bench/corpus.py` imports it from there; `sfy_corpus.py` imports the leaf. (`flab2bp.sfy.layout.strategy` pulls only `flab2bp.layout.{base, budget, process_resources}`, measured at 8d35d074; if `flab2bp.sfy.pipeline` turns out to pull a DSP module of its own, name it in the report and keep the guard at that measured set rather than zero.) Run: `uv run pytest tests/sfy/test_corpus.py -q; echo exit=$?` → 0. Commit: `Move Tier into a leaf so the Satisfactory corpus imports no DSP module`.
- [ ] **Step 3: Pole grid.** Failing test in `tests/sfy/test_registry.py`: `test_every_power_pole_mark_states_its_own_hologram_grid` asserts `grid_snap_cm` is not `None` for the three pole marks and equals what `Build_PowerPoleMk1_C` states (50.0) unless the asset says otherwise. Fix `HologramLimits` to walk `mHologramClass` up the super chain (the Mk2/Mk3 pole holograms inherit the Mk1's `mGridSnapSize`), regenerate the registry with the documented command, confirm the drift test (`test_the_committed_registry_is_what_the_merge_produces`) passes, and record the provenance source as `assets` on those limits. Commit: `Read a power pole's hologram grid through the hologram class's supers`.

---

### Task 2: strategy protocol, refusal vocabulary, names, floor and measure

**Files:**
- Create: `src/flab2bp/sfy/layout/protocol.py`, `src/flab2bp/sfy/layout/refusals.py`, `src/flab2bp/sfy/strategy_names.py`, `src/flab2bp/sfy/layout/floor.py`, `src/flab2bp/sfy/layout/measure.py`
- Modify: `src/flab2bp/sfy/layout/strategy.py` (import `REFUSALS`/`refuse` from the leaf; `_floor` moves to `floor.py`), `src/flab2bp/bench/sfy_corpus.py` (imports `REFUSALS` from the leaf)
- Test: `tests/sfy/test_strategy.py`, `tests/sfy/test_measure.py`

**Interfaces:**
- Consumes: `ManifoldRows.lay_out`'s existing signature (`m3-router-reference.md` §3.4), `SfyPlacement`, `Designer`, `Registry`.
- Produces:

```python
# protocol.py
class SfyLayoutStrategy(Protocol):
    name: str
    def lay_out(self, spec: SfyBuildSpec, designer: Designer, *, time_budget_s: float = 15.0,
                absolute_deadline: float | None = None, registry: Registry | None = None,
                lab_map: LabMap | None = None) -> SfyPlacement: ...

# refusals.py  (every cause both strategies may raise; sections MANIFOLD, GRID, SHARED)
REFUSALS: tuple[str, ...]
def refuse(spec: SfyBuildSpec, reason: str, detail: str = "") -> NoValidLayout   # the one builder

# strategy_names.py (imports nothing from flab2bp)
SfyStrategyName = Literal["best", "manifold-rows", "grid-routed"]
SFY_STRATEGY_CHOICES: tuple[SfyStrategyName, ...] = ("best", "manifold-rows", "grid-routed")
SFY_PRODUCTION_STRATEGIES: tuple[str, ...] = ("manifold-rows", "grid-routed")   # race order = tie order

# floor.py
def foundations(designer: Designer, registry: Registry, ids: Iterator[int]) -> tuple[FoundationObj, ...]

# measure.py
@dataclass(frozen=True, slots=True)
class Measure:
    blueprints: int            # 1 in M3
    volume_cm3: float          # AABB of every machine, attachment, lift and belt point, hard boxes applied
    belt_cm: float             # sum of spline lengths (splines.segment_length over every run)
    lifts: int
    attachments: int
def measure(placement: SfyPlacement, registry: Registry) -> Measure
def race_key(measure: Measure, strategy_index: int) -> tuple[int, float, float, int]
```

The grid strategy's causes (added now so the corpus module can name them before Task 10 exists): `a port is off the hologram lattice`, `the packer found no arrangement`, `a belt could not be routed`, `a belt could not be laid`, `a tap has no room for a splitter`, `routing exceeded the budget`, `packing exceeded the budget`, `no room for a power pole`, `a machine has no power connection`, `wire exceeds the maximum length`, `run exceeds the belt ceiling`, `fluids are M4`. The last five are shared with the manifold and are listed once.

- [ ] **Step 1: Failing tests.** `test_manifold_rows_satisfies_the_strategy_protocol` (a `SfyLayoutStrategy` annotated variable assigned `ManifoldRows()` passes mypy; at runtime `isinstance` with `runtime_checkable`); `test_every_refusal_is_named_once_and_only_refuse_builds_one`; `test_the_corpus_ruled_causes_are_refusals` (already exists; now against the leaf); `test_measure_of_the_iron_plate_manifold_is_its_bounding_volume_and_belt_length` (build `iron-plate-60` in mk1 through `ManifoldRows`, assert `volume_cm3` equals the AABB computed independently in the test from the hard boxes and spline points, and `belt_cm` equals the sum of `spline_length`); `test_race_key_orders_by_blueprints_then_volume_then_belt_then_strategy`; `test_floor_covers_the_designer` (moved from the strategy tests).
- [ ] **Step 2: Implement**, `uv run pytest tests/sfy -q; echo exit=$?` → 0, one commit per file: `Name the Satisfactory strategy protocol`, `Move the refusal vocabulary into a leaf`, `Name the Satisfactory strategies in a leaf`, `Share the designer floor between strategies`, `Measure a placement for the race`.

---

### Task 3: the lift's clearance width from the binary's `.data`

**Files:**
- Modify: `tools/sfy-native/src/main.rs` (a `data` subcommand: `sfy-native data <symbol> --bytes N` resolves the PDB symbol to an RVA, refuses unless the RVA falls in `.data` or `.rdata`, prints the section name, the RVA and the bytes as hex and as little-endian `f64`s), `scripts/sfy_native_rules.py` (the `lift.clearance` rule reads the two doubles and states the half-extent with `source: binary .data initialiser via PDB symbol AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D`), `src/flab2bp/sfy/data/hologram_rules.json` (regenerated), `src/flab2bp/sfy/data/registry.json` (regenerated: `lift_clearance_half_extent_cm` on `Limits`, governed by `lift.clearance`, effect `compute`), `src/flab2bp/sfy/registry.py`, `src/flab2bp/sfy/layout/validate.py::_lift_half_width`
- Test: `tests/sfy/test_native.py`, `tests/sfy/test_rules.py`, `tests/sfy/test_validate.py`

**Interfaces:**
- Produces: `Limits.lift_clearance_half_extent_cm: float | None` (95.0 = 100 − 5 after `FitClearance`'s shrink, if the read succeeds), `_lift_half_width` reads it and falls back to the M2 connector reading with `_CAPSULE_UNREAD` naming the fallback.

Read first: `m3-router-reference.md` §4.4 item 5; the `lift.clearance` rule's interpretation in `hologram_rules.json` (the formula for the box's vertical span is already read: `(|h| + mMeshHeight + 100)/2 − 30` to `(h + mMeshHeight − 100)/2`, with the passthrough adjustments this project never triggers).

- [ ] **Step 1: Failing test** in `tests/sfy/test_native.py`: the tool run on the installed binary and PDB (skipped when either is absent, the way the existing native tests skip) prints `.data` for the symbol and two doubles; and a rule test: `test_lift_clearance_states_its_width_from_the_data_initialiser` asserts the rule's `reads` names `CLEARANCE_EXTENT_2D` and its status is `extracted` for the width. A validator test: `test_a_lift_box_is_the_rules_span_and_the_binarys_width` builds a 4 m lift and asserts `_lift_box`'s half extents are `(span/2, 95, 95)`.
- [ ] **Step 2: Implement.** The subcommand reads the section table, refuses any RVA outside `.data`/`.rdata` with the section it did fall in, and never disassembles. The rule generator states the mutable-global caveat once: an initialiser is what the image holds before the game runs, and `FitClearance` reads it at hologram time; if the game ever wrote to it the reading would be stale, and the rule says so. If the symbol is not in the PDB or the read fails, stop: leave the M2 reading, write the failure into the rule's interpretation, and report `DONE_WITH_CONCERNS` (R-M3-8).
- [ ] **Step 3: Regenerate, drift tests, commit** (one per file: tool, script+JSON, registry field, validator).

---

### Task 4: the lattice world

**Files:**
- Create: `src/flab2bp/sfy/layout/lattice.py`
- Test: `tests/sfy/test_lattice.py`
- Docs: `docs/sfy-layout-model.md` (new section "The lattice", stating R-M3-1..3)

**Interfaces:**
- Consumes: `Designer`, `Registry` (`hard_footprint_cm`'s inputs: `Buildable.clearance`), `MachineObj`, `AttachmentObj`, `BeltRun`, `LiftObj`, `validate._lift_box` and `validate._lift_half_width` (renamed public as `lift_box` and `lift_half_width`; the validator keeps them), `BELT_CLEARANCE_HALF_WIDTH_CM`, `BELT_CLEARANCE_HALF_HEIGHT_CM`, `flab2bp.layout.geometric_world.GridIndex` (imported at module level: it imports nothing from the DSP placer, and the import-cost test proves it).
- Produces:

```python
Node = tuple[int, int, int]

@dataclass(frozen=True, slots=True)
class Lattice:
    designer: Designer
    grid_cm: float               # limits.hologram_grid_cm
    n: int                       # nodes per axis minus one: dims[0] * foundation_cm / grid_cm
    codec: GridIndex             # GridIndex(0, 0, rows=n + 1, levels=n + 1)
    def world(self, node: Node) -> Vector
    def node(self, point: Vector) -> Node | None          # None when off the lattice by > 1e-6
    def level(self, z: float) -> int | None
    def index(self, node: Node) -> int                    # bounds-checked, raises IndexError
    def size(self) -> int

@dataclass
class Occupancy:
    """flags: 1 = a belt centreline may pass this node at this level; 0 = may not.
    base: flags before any path was committed (rip-up restores from it).
    owner: node index -> net id, for every node a committed path holds or shadows.
    history: array('d') of congestion history per node."""
    lattice: Lattice
    flags: bytearray
    base: bytes
    owner: dict[int, int]
    history: array[float]
    def free(self, node: Node) -> bool                    # THE predicate; flags must agree with it
    def block_box(self, low: Vector, high: Vector) -> None  # hard box → nodes per R-M3-2
    def commit(self, net: int, path: Sequence[Node]) -> None   # nodes + 4 neighbours at each level
    def rip_up(self, net: int) -> None                    # restore from base for that owner
    def snapshot(self) -> bytes                           # flags copy for a per-net query

def occupancy_for(lattice: Lattice, machines: Sequence[MachineObj], attachments: Sequence[AttachmentObj],
                  lifts: Sequence[LiftObj], belts: Sequence[BeltRun], registry: Registry) -> Occupancy
def belt_levels(z0: float, z1: float, grid: float) -> range   # levels k with 100k ± 15 meeting [z0, z1]
```

`block_box` inflates the box by `(79, 79, 15)` (the belt box half extents) and marks every node whose world position lies inside; wall lines `i ∈ {0, n}` and `j ∈ {0, n}` are blocked at every level except as goals (Task 5 opens them per query); levels 0 and 1 are blocked everywhere.

- [ ] **Step 1: Failing tests.** `test_node_and_world_are_inverses_on_every_mark`; `test_a_constructor_blocks_levels_one_to_seven_and_four_lines_out` (centre on a node: nodes with `|dx| ≤ 4` and `|dy| ≤ 5` at levels 1..7 blocked, level 8 free, line 5 in x free); `test_a_committed_belt_shadows_its_neighbours_at_its_level_only`; `test_rip_up_restores_the_base_not_one`; `test_flags_agree_with_free_on_every_node` (the invariant, over a placement with two machines, one belt, one lift and one splitter: `all(occ.flags[occ.lattice.index(node)] == occ.free(node) for node in every node)`); `test_a_lift_column_blocks_its_levels_between_the_ends`; `test_levels_zero_and_one_are_never_passable`.
- [ ] **Step 2: Implement**; the flattening loops are the only per-node Python in this milestone and say so in the docstring. **Step 3: run, commit** `Flatten a Satisfactory placement onto the routing lattice`.

---

### Task 5: transitions and one routed net

**Files:**
- Create: `src/flab2bp/sfy/layout/transitions.py`, `src/flab2bp/sfy/layout/router.py`
- Test: `tests/sfy/test_router.py`

**Interfaces:**
- Consumes: `Lattice`, `Occupancy`, `flab2bp.layout.geometric_world.GeometricWorld` and `GeometricTransition`, `flab2bp.layout.geometric_router.{GeometricQuery, GeometricResult, route, summarize}` (imported **inside** `route_net`), `flab2bp.layout.budget.{WorkBudget, expired}`.
- Produces:

```python
# transitions.py
GROUND_LEVEL = 2
def level_toll(level: int) -> float                       # 0.01 * (level - GROUND_LEVEL), never below 0
def lift_cost(height_levels: int) -> float                # 2.0 + float(height_levels)
def sfy_transitions(levels: int, lift_heights: range) -> tuple[tuple[GeometricTransition, ...], ...]
# per source level: 4 flat steps (dx,dy,0,False,1+toll); 8 inclines (2dx,2dy,±1,True,3+toll(next));
# lifts (0,0,±h,False,lift_cost(h)+toll(next)) for h in lift_heights where the target is in range;
# nothing for levels 0 and 1 (no moves out of an impassable level)

# router.py
class RouteFailureKind(Enum): SEALED_POCKET, DYNAMIC_ACCESS, BUDGET
class BudgetCause(Enum): ALLOWANCE, DEADLINE, UNKNOWN

@dataclass(frozen=True, slots=True)
class Routed:
    path: tuple[Node, ...] | None
    kind: RouteFailureKind | None
    wall: tuple[Node, ...]         # only complete exhaustion fills this
    work: int
    cause: BudgetCause = BudgetCause.UNKNOWN

MAX_SEARCH_WORK = 200_000
def route_net(occupancy: Occupancy, *, starts: Collection[Node], goals: Collection[Node],
              opened: Collection[Node] = (), pressure: float, budget: WorkBudget,
              deadline: float | None, transitions: ..., blame: dict[Node, float] | None = None) -> Routed
```

`route_net` is `routing_domain._geometric_search` (`m3-router-reference.md` §1.4) with the DSP-only parameters gone: pre-flight refusals at zero work; a private `bytes` copy of the flags with `opened` nodes set passable (the net's own tap nodes and its wall goals); `GeometricWorld(nx=n+1, ny=n+1, nz=n+1, gx0=0, gy0=0, flags=..., history=occupancy.history, transitions=...)`; `max_work = min(MAX_SEARCH_WORK, budget.left)`; the budget attribution rule verbatim from §1.4 step 6; on exhaustion the blame census over the returned intervals (§1.9, `_BLAME_MAX_POCKET = 32_768`, `_BLAME_MAX_WALL = 64`) restricted to nodes whose `owner` is another net.

- [ ] **Step 1: Failing tests.** `test_a_straight_run_on_an_empty_lattice_is_one_interval` (mk1, start `(4, 4, 2)`, goal `(4, 28, 2)`: the path is 25 nodes on one line and `metrics["interval_pops"]` is small — assert it is below 64, since the kernel expands whole runs); `test_a_wall_of_machines_is_crossed_by_an_incline_over_the_top` (a line of constructors across `y = 16`: the path climbs to level 8 with 2:1 vias and comes down); `test_a_lift_edge_is_taken_when_the_column_is_free_and_cheaper` (a narrow gap where an incline needs 12 nodes of run and a lift of 4 needs none: the path contains a `(0, 0, +4)` step); `test_the_kernel_accepts_a_multi_level_vertical_move` — **this is the spike**: if `search_intervals` rejects or mis-decodes a `dz = 4` transition, the implementer switches lifts to `GeometricQuery.extra_edges` built for every passable node at the ground level and every port node, records the choice in the ledger, and keeps the same test; `test_a_sealed_start_names_the_wall_only_on_exhaustion` (a start boxed in by another net's committed belt: `kind == SEALED_POCKET`, `wall` non-empty and every wall node owned by that net; the same query with `max_work = 1` gives `BUDGET`/`ALLOWANCE` and an empty wall); `test_importing_the_router_module_does_not_load_the_kernel` (fresh interpreter: `import flab2bp.sfy.layout.router` leaves `flab2bp.layout._geometric_kernel` out of `sys.modules`).
- [ ] **Step 2: Implement**, run, commit `Describe Satisfactory belt moves for the interval kernel` and `Route one Satisfactory net through the geometric router`.

---

### Task 6: realise a lattice path as conveyors, attachments and lifts

**Files:**
- Create: `src/flab2bp/sfy/layout/realise.py`
- Test: `tests/sfy/test_realise.py`

**Interfaces:**
- Consumes: `corridors.{Route, Turn, choose_turn, lay_path, LaidPath, Measures, TurnStop}`, `splines`, `LiftObj`, `lift_geometry`, `belt_ends`, `Lattice`, `shortest_belt_cm`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class Terminal:
    """One end of a path: a port on an object, or a wall node."""
    node: Node
    world: Vector                  # the port's world position (may be off the lattice along the facing)
    facing: Vector                 # unit, the port's normal (for a wall terminal, into the designer)
    port: tuple[int, str] | None   # None at the wall
    kind: Literal["port", "wall", "tap"]

@dataclass(frozen=True, slots=True)
class Realised:
    belts: tuple[BeltRun, ...]
    attachments: tuple[AttachmentObj, ...]
    lifts: tuple[LiftObj, ...]
    links: tuple[Link, ...]
    lift_columns: tuple[tuple[Node, Node], ...]   # (bottom node, top node) per lift, for occupancy

class RealiseError(ValueError):     # .cause in {"corner", "leg", "lift", "stub"}, .nodes: tuple[Node, ...]

def realise(path: Sequence[Node], *, source: Terminal, sink: Terminal, lattice: Lattice, measures: Measures,
            registry: Registry, belt_class: str, lift_class: str, item_id: str, rate: Fraction,
            ids: Iterator[int]) -> Realised
```

Rules the realiser applies (all from the registry through `Measures` and `limits`; the numbers here are what the shipped registry gives and appear in the docstrings as examples, never as constants):

1. **Segments.** Collapse the path into maximal straight runs at one level, incline runs (a `(2dx, 2dy, ±1)` move is one leg of `Route.go(200, ±100)`), and vertical edges (lifts). Consecutive collinear flat runs merge.
2. **Corners** between two flat runs at one level: `choose_turn(measures, along, across)` with `along`/`across` the free straight length on each side (the leg length minus what the neighbouring cut or corner already spent). An arc needs `measures.radius` on both sides; an attachment `measures.box + measures.lead_in`. Where neither fits, `RealiseError("corner", corner nodes)`.
3. **Cuts.** A belt is cut at a port, an attachment and a lift end; every belt between cuts is `≥ shortest_belt_cm(limits)`; a violation is `RealiseError("leg", nodes)` (R-M3-4).
4. **Lifts.** A vertical edge `(x, y, k) → (x, y, k ± h)` becomes a `LiftObj` whose actor is the input end (bottom for upward, top for downward with negative `height_cm`), `pose.yaw_deg` chosen so the arriving belt meets the bottom port along its normal, `top_yaw_deg` a multiple of `top_yaw_step_deg` chosen so the leaving belt departs along the top's normal; a lift whose input end is on the source terminal's node when that terminal is a port on a node connects with no belt.
5. **Stubs.** A port terminal off the lattice (the Manufacturer input) gets a straight stub from the port to its node as the first (or last) piece of the adjoining belt, never a belt of its own.
6. **Links.** Every belt end links to what it meets, in the `Link(a=upstream, b=downstream)` orientation `model.Link` documents; belt-to-belt joins never happen (one run per cut pair).

- [ ] **Step 1: Failing tests.** `test_a_straight_path_between_two_constructors_is_one_belt` (validates clean with `validate(..., only=belt checks + ports checks)`); `test_an_l_path_with_four_node_legs_is_an_arc_or_an_attachment_and_says_which`; `test_a_corner_with_two_node_legs_refuses_naming_the_corner`; `test_an_incline_run_is_one_leg_at_thirty_five_degrees_or_under`; `test_a_lift_of_four_levels_becomes_a_lift_object_that_validates` (`lift.height`, `lift.step`, `lift.top_yaw`, `lift.placement` all clean; the lift's ends match the belt ends within `BELT_CONNECTION_CM`); `test_a_lift_on_a_port_node_connects_without_a_belt`; `test_a_manufacturer_input_gets_its_stub_inside_the_first_belt`; `test_every_belt_is_longer_than_the_minimum` (property test over random lattice paths of legal shape).
- [ ] **Step 2: Implement**, run, commit `Lay a lattice path as belts, attachment turns and lifts`.

---

### Task 7: nets, taps, tiers and the rip-up-and-reroute loop

**Files:**
- Create: `src/flab2bp/sfy/layout/grid_nets.py`, `src/flab2bp/sfy/layout/rrr.py`
- Test: `tests/sfy/test_grid_nets.py`, `tests/sfy/test_rrr.py`

**Interfaces:**
- Consumes: `SfyBuildSpec`, `direct_pairs`, `Terminal`, `route_net`, `realise`, `Occupancy`, `WorkBudget`, `laying._tier` (moved to `grid_nets.tier_for(rate, tiers, item)` and re-imported by `laying`).
- Produces:

```python
@dataclass(frozen=True, slots=True)
class GridNet:
    id: int
    item_id: str
    sources: tuple[Terminal, ...]     # machine output ports, or one wall terminal set (-Y)
    sinks: tuple[Terminal, ...]       # machine input ports, or the +Y wall
    rate: Fraction                    # total items/s the item moves
    per_sink: tuple[Fraction, ...]
    per_source: tuple[Fraction, ...]

def nets_for(spec: SfyBuildSpec, machines: Sequence[MachineObj], lattice: Lattice, registry: Registry,
             lab_map: LabMap) -> tuple[GridNet, ...]       # one net per item; direct pairs become 1:1 nets
def tap_nodes(tree: Sequence[Sequence[Node]], lattice: Lattice) -> tuple[Node, ...]   # R-M3-7 taps

@dataclass(frozen=True, slots=True)
class RoutedTree:
    net: GridNet
    paths: tuple[tuple[Node, ...], ...]          # one per sink (and per extra source)
    taps: tuple[tuple[Node, int], ...]            # (tap node, index of the path tapped)

@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    trees: tuple[RoutedTree, ...]
    stranded: tuple[tuple[GridNet, Routed], ...]  # nets with no path, and the last search's evidence
    realised: tuple[Realised, ...]
    rounds: int
    work: int

RRR_MAX = 8
def route_all(nets: Sequence[GridNet], occupancy: Occupancy, *, measures: Measures, registry: Registry,
              budget: WorkBudget, deadline: float | None, ids: Iterator[int],
              belt_class_for: Callable[[Fraction, str], str], lift_class: str) -> RoutingOutcome
```

`route_all` is the DSP loop's shape (`m3-router-reference.md` §1.7) with the sfy pieces: nets ordered by rate descending; per round every net is ripped up and re-routed with the round's pressure; each path is realised immediately and committed together with its lift columns; a `RealiseError` charges `history` at the error's nodes by `BLAME_WEIGHT = 40.0` and strands the net for this round; a sealed pocket charges the wall; the best round (fewest stranded, then least belt length) is kept; the deadline is read between rounds and between nets. Tiers are assigned per belt from the rate every segment carries after the tree is known; a rate over the top tier refuses `run exceeds the belt ceiling`.

- [ ] **Step 1: Failing tests.** `test_a_direct_pair_is_one_net_per_machine_pair`; `test_a_three_to_three_group_pair_is_one_net_with_three_sources_and_three_sinks`; `test_tap_nodes_are_interior_straight_nodes_two_from_any_corner`; `test_two_crossing_nets_settle_with_one_over_the_other` (an X of four terminals: both routed, the crossing at different levels, `rounds ≤ 3`); `test_a_net_blocked_by_another_rips_it_up_and_both_route`; `test_a_realise_failure_is_charged_and_the_next_round_avoids_the_corner`; `test_a_tapped_tree_stands_a_splitter_on_the_tap_and_carries_the_sum_upstream` (belt tiers: the trunk before the tap carries both sinks' rates); `test_the_loop_stops_at_the_deadline_and_reports_untouched_nets_as_budget`; `test_stranded_nets_never_produce_a_partial_placement`.
- [ ] **Step 2: Implement**, run, commit `Plan Satisfactory nets and taps from the spec` and `Negotiate every net across rip-up rounds`.

---

### Task 8: the packer

**Files:**
- Create: `src/flab2bp/sfy/layout/packer.py`
- Test: `tests/sfy/test_packer.py`

**Interfaces:**
- Consumes: `SfyBuildSpec`, `Designer`, `Registry` (`hard_footprint_cm`, ports), `Lattice`, ortools `cp_model`, `budget.WorkBudget`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class Feedback:
    failed_nets: Mapping[int, float]        # net id -> weight (decays 0.85 per arrangement, DSP shape)
    hot_nodes: Mapping[Node, float]         # blame history projected onto the ground plane

@dataclass(frozen=True, slots=True)
class Pack:
    machines: tuple[MachineObj, ...]        # poses on nodes, yaw in {0, 90, 180, -90}
    objective: float
    status: str                             # cp_model status name

PORT_APRON_NODES = 3
def pack(spec: SfyBuildSpec, designer: Designer, registry: Registry, lattice: Lattice, *,
         feedback: Feedback | None, deadline: float | None, workers: int, seed: int) -> Pack
```

Model: per machine an `(x, y)` node pair inside `[margin, n − margin]` where the margin keeps the hard box plus one grid step inside the designer (R7 against the wall); four yaw literals with the footprint swapped for 90°/−90°; `AddNoOverlap2D` on hard footprints inflated by one grid step on every side (R7); per belt port an apron of `PORT_APRON_NODES` nodes along its facing, modelled as an extra rectangle tied to the yaw literal, in the same no-overlap; objective = Σ over item nets of the Manhattan distance between the centroid of its sources' port nodes and each sink's port node (linear through `AddAbsEquality`), weighted by `1 + failed_nets[net]`, plus Σ over machines of hot-node weight within their footprint (a summed-area table of `hot_nodes`, read as a constant per candidate position through element constraints only when `hot_nodes` is non-empty), with `max_deterministic_time` scaled to the machine count and the wall deadline honoured. Direct pairs get an extra term rewarding output-port-to-input-port distance of exactly the shortest legal belt. No cut predicts routability (the DSP negative result, `m3-router-reference.md` §2.4).

- [ ] **Step 1: Failing tests.** `test_three_smelters_and_three_constructors_pack_inside_mk1_with_no_overlap`; `test_every_machine_stands_on_a_node_with_a_grid_step_between_hard_boxes`; `test_port_aprons_are_free_of_other_machines`; `test_direct_pairs_face_each_other_a_shortest_belt_apart_when_room_allows`; `test_a_failed_net_weight_pulls_its_terminals_closer` (same spec, feedback on one net: that net's Manhattan sum does not increase); `test_the_solver_stops_at_the_deadline_with_the_best_feasible_or_refuses`; `test_a_spec_that_cannot_fit_refuses_with_the_packer_cause`.
- [ ] **Step 2: Implement**, run, commit `Pack Satisfactory machines on the hologram grid with CP-SAT`.

---

### Task 9: power on free nodes

**Files:**
- Create: `src/flab2bp/sfy/layout/grid_power.py`; Modify: `src/flab2bp/sfy/layout/power.py` (share `power_port`, `wire_limit_cm`, `machine_budget`, `_wire`, `_hold_to_the_link_count`)
- Test: `tests/sfy/test_grid_power.py`

**Interfaces:**
- Consumes: `MachineObj`, `Occupancy.free`, `PowerPlan`, `PoleObj`, `WireObj`, the pole's `grid_snap_cm` (Task 1).
- Produces: `def place_on_free_nodes(machines: Sequence[MachineObj], occupancy: Occupancy, registry: Registry, *, ids: Iterator[int], designer: Designer, pole_class: str = POLE_CLASS, wire_class: str = WIRE_CLASS) -> PowerPlan`.

Poles stand on ground nodes free at every level the pole's soft box spans and outside every machine's hard footprint, chosen greedily: the free node within `wire_limit_cm` of the most unpowered machine power ports, until every machine is wired or no node serves one (refuse `no room for a power pole`); poles chain to each other within the wire limit and each pole's `max_connections`; the pole line is described in `PowerPlan.lines`.

- [ ] **Step 1: Failing tests.** `test_six_machines_get_one_pole_within_reach_and_six_wires`; `test_a_pole_never_stands_inside_a_hard_box_or_on_a_belt_node`; `test_poles_beyond_the_connection_count_chain_through_a_second_pole`; `test_power_wires_validates_clean`.
- [ ] **Step 2: Implement**, run, commit `Stand power poles on the lattice's free nodes`.

---

### Task 10: the `GridRouted` strategy

**Files:**
- Create: `src/flab2bp/sfy/layout/grid.py`
- Test: `tests/sfy/test_grid.py`
- Docs: `docs/sfy-layout-model.md` (section "Grid-routed": the loop, the refusals, what is ours)

**Interfaces:**
- Consumes: everything from Tasks 2 to 9.
- Produces: `class GridRouted:` with `name = "grid-routed"` and the `SfyLayoutStrategy.lay_out` signature.

The loop: read `Measures` once (`strategy._measure`); refuse fluids (R4) the way the manifold does; build the lattice; for arrangement `a` in `1..ARRANGEMENTS (3)` under the budget: `pack` → `occupancy_for(machines)` → `nets_for` → `route_all` → if no net is stranded: `place_on_free_nodes` → `foundations` → `SfyPlacement` with a description naming the strategy, the arrangement, the rounds, the lifts and attachments authored and the turns chosen; else fold the stranded nets and the blame into `Feedback` (decay 0.85) and pack again; after the last arrangement refuse `a belt could not be routed` naming the nets, or the budget cause. Every refusal goes through `refuse` with a cause in `REFUSALS`; every placement returned validates clean under `validate(placement, spec, registry)` (a test asserts it on the corpus's clean cells).

- [ ] **Step 1: Failing tests.** `test_iron_plate_60_lays_out_in_mk1_and_validates_clean`; `test_the_description_names_the_strategy_and_counts_lifts_and_attachments`; `test_a_fluid_spec_refuses_fluids_are_m4`; `test_grid_routed_satisfies_the_protocol`; `test_no_refusal_escapes_the_vocabulary` (monkeypatch each stage to raise its error type, assert the cause); `test_the_placement_round_trips_through_emit_and_decode`.
- [ ] **Step 2: Implement**, run, commit `Lay a Satisfactory build out by packing and geometric routing`.

---

### Task 11: the race, the CLI, the web, the audit and the evidence

**Files:**
- Modify: `src/flab2bp/sfy/pipeline.py` (`build(..., strategy: SfyStrategyName = "best")`, `STRATEGIES: dict[str, SfyLayoutStrategy]`, the serial race per R-M3-6, `SfyBuild.strategy` = the winner, `SfyBuild.refused` = the losers' refusals, `SfyBuild.measure: Measure`), `src/flab2bp/cli.py` (`--strategy` accepts the sfy choices on an sfy URL; the report prints the measure and the losing strategies' causes), `src/flab2bp/web/jobs.py` (strategy in the sfy job), `src/flab2bp/bench/sfy_corpus.py` (`SfyCorpusEntry.expects` keyed by `(strategy, mark)`; `RULED_CAUSES` gains the grid causes that are honest refusals: `a belt could not be routed`, `the packer found no arrangement`), `scripts/sfy_audit.py` (`--strategy`, cells keyed by strategy, `--strict` pins per strategy, report table with a column per strategy and the measure)
- Test: `tests/sfy/test_pipeline.py`, `tests/sfy/test_corpus.py`, `tests/test_cli.py` (sfy arms)
- Evidence: `docs/superpowers/evidence/sfy-m3-audit-<date>.md`; spec section 15 "Status after M3"

- [ ] **Step 1: Failing tests.** `test_best_runs_both_strategies_in_one_clock_frame_and_picks_by_the_race_key`; `test_a_strategy_that_refuses_is_reported_beside_the_winner`; `test_both_refusing_raises_no_valid_layout_naming_both_causes`; `test_the_cli_accepts_the_sfy_strategy_choices_only_for_an_sfy_url`; `test_a_cell_is_keyed_by_strategy_and_mark`; `test_strict_pins_are_per_strategy`.
- [ ] **Step 2: Implement**, run `uv run pytest tests/sfy tests/test_cli.py -q; echo exit=$?`, commit per file.
- [ ] **Step 3: Evidence.** Run `uv run python scripts/sfy_audit.py --strategy manifold-rows`, `--strategy grid-routed` and `--strategy best` over the whole matrix (36 cells each) with the CPU load figure recorded beside the timings the way the M2 evidence does (`vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}'`); pin the grid strategy's cells from the run; write the evidence file with, per cell, verdict, cause, measure, rounds, lifts, attachments, turns by kind; write spec section 15 from the numbers (which of the 25 depth refusals the grid strategy converts, and what it costs in volume and belt against the manifold on the 7 cells both clear); commit `Measure the grid-routed strategy against the manifold on the corpus`.

---

### Task 12: checkpoint 3

**Files:**
- Create: `scripts/sfy_checkpoint3.py`; Output: `out/sfy/checkpoint3-README.md`, `out/sfy/checkpoint3-*.sbp/.sbpcfg` (untracked)
- Test: `tests/sfy/test_checkpoint3.py` (the script's self-checks: validator clean, round trip, at least one lift and one attachment turn in the chosen build)

- [ ] **Step 1:** The script builds, with `--strategy grid-routed`, the smallest corpus cell the manifold refuses and the grid clears (by the evidence table) into the smallest mark that holds it, plus `iron-plate-60` in mk1 for a like-for-like paste against checkpoint 2; the README states for each file what a paste test answers (does a lift authored from `LiftGeometry` connect at both ends; does an attachment turn carry the rate; does the build run at the flow's rate).
- [ ] **Step 2:** Self-checks, commit `Write checkpoint 3: a grid-routed build with lifts and attachment turns`.

---

## Self-review

- **Spec coverage.** Section 8.1 (grid, levels): Task 4. Section 8.2 (lift, splitter/merger objects): Tasks 6, 7. Section 8.4 (lifts as turns, per-turn choice): Tasks 6, 7 and constraint 8. Section 9 (strategy 2, protocol, race key): Tasks 2, 10, 11. Section 10 (validation as the gate): every strategy test asserts `validate` clean; Task 3 narrows the one `partial` width. Section 12 (milestones, corpus gate): Tasks 1, 11. Section 14's two extractor gaps: Task 1. Not covered, by R-M3-0: stacking, passthroughs, manifest, zip, web viewer (M4).
- **Placeholders.** None: every task carries its files, signatures, tests and commit lines; the two open outcomes (the kernel's multi-level move, the `.data` read) each name their fallback and who records it.
- **Type consistency.** `Node`, `Terminal`, `Realised`, `Routed`, `GridNet`, `RoutedTree`, `RoutingOutcome`, `Feedback`, `Pack`, `Measure` are defined once and consumed by name; `refuse` replaces `strategy._refuse` everywhere; `foundations` replaces `_Layout._floor`.
