# Abstraction Repairs 2, Plan A: Mechanical Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the independent, behavior-neutral repairs from the 2026-09-13 abstractions review: dead code, indexed-scan filters plus a guard against scans in loops, naming debt left by the tile-A* removal, same-file twins, one quaternion module, one grid-index codec and one search-result adapter.

**Architecture:** Every task is a refactor that must leave routing results byte-identical. Tasks are ordered by the review's ranking (dead code, naming, indexed scans, twins, quaternion, codec) and are independent of each other, so a reviewer can reject one without blocking the next. The later, larger items of the review (the `WorkBudget` contract, the `_route_all` decomposition, private-name promotion and the routing_domain file split) are Plans B, C and D, written after this plan and the concurrent sprayed-Universe routing branch land, because they restructure the same module.

**Tech Stack:** Python 3.14, uv venv at `.venv`, pytest (no summary line prints on this box: use the exit code), ruff, mypy strict (`files = ["src", "tests"]`; `scripts/` is not type-checked), Serena symbol tools for reads and renames.

**Spec:** `docs/superpowers/specs/2026-09-13-abstraction-review.md` (the review; section 9 is the ranking this plan follows). Program sequence: Plan A (this) → sprayed-Universe routing branch lands → Plan B `WorkBudget` contract (review item 1) → Plan C `_route_all` decomposition (item 3) → Plan D private-name promotion, import guard, routing_domain split (items 5 and 8) → parameter objects (item 10).

## Global Constraints

- No behavior change. Placement results must be identical; the corpus gate below is the proof.
- Do not touch `pyproject.toml`, `uv.lock`, or anything under `.venv`.
- Stats keys written into `placement.stats` (for example `"expansions"`, `"route_backend"`) are evidence names read by committed tooling; they keep their current spelling. Only code identifiers, comments and docstrings are renamed.
- Never run interactive git. Prefix git commands with `GIT_EDITOR=true`; commit with `-m` or `-F`.
- One logical change per commit, on the current branch of the main checkout (`master` unless the lead says otherwise). Commit messages end with the attribution lines the session provides.
- Serena: reads (`find_symbol`, `find_referencing_symbols`, `get_symbols_overview`) and `rename_symbol` are the required tools for symbol work; grep is for strings and comments. A concurrent routing agent works in a worktree without Serena, so Serena edits from this plan land in the main checkout only.
- Box discipline: before any test run heavier than one file, `vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}'` must print below 64 (never use load average). Pin runs with `taskset -c 96-127`.
- Delete only what a reference search including string references proves unused: `find_referencing_symbols`, then `grep -rn "<name>" src scripts tests docs/superpowers/specs` (monkeypatch targets and `dsp/registry.py` name lists reference symbols as strings).

## Gate

Every task ends with the lint gate. Tasks marked **routing-touching** also end with the corpus gate.

Lint gate:
```
./.venv/bin/ruff check <changed files>
./.venv/bin/ruff format --check <changed files>
./.venv/bin/mypy <changed src files> <changed test files>
```

Suite gate (task says which files):
```
taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q <test files>; echo "exit=$?"
```

Corpus gate (routing-touching tasks), run from an exported tree so later edits cannot leak into child processes:
```
S=$(mktemp -d)
git archive HEAD | tar -x -C $S
for f in src/flab2bp/layout/_geometric_kernel.cpython-314-x86_64-linux-gnu.so src/flab2bp/layout/_sequence_kernel.cpython-314-x86_64-linux-gnu.so src/flab2bp/layout/_junction_neighborhood.cpython-314-x86_64-linux-gnu.so src/flab2bp/dsp/_geometry_kernel.cpython-314-x86_64-linux-gnu.so; do cp $f $S/$f; done
cd $S && taskset -c 96-127 env PYTHONHASHSEED=0 PYTHONPATH=$S/src:$S /home/dannyb/sources/factorio-lab-to-blueprint/.venv/bin/python3.14 $S/scripts/audit.py --tier stress --strategy all --budget 15 --jobs 4 --max-seconds 900 --json $S/audit.jsonl
./.venv/bin/python3.14 .local-evidence/2026-09-13-session/probes/compare_audits.py .local-evidence/2026-09-13-session/audit-master-control-20260913.jsonl $S/audit.jsonl
```
Pass: 167 CLEAN / 13 REFUSED, zero status changes versus the control, and area changes only on cells that already vary run to run (the control notes list super-magnetic-ring, processor, casimir-crystal, electromagnetic-matrix, information-matrix hierarchical, plastic best). A status change or an area change on any other cell fails the task. The audit exits 1 on any refusal; that is expected. A `TEARDOWN FAULT` line after the rows is a harness cleanup fault, not a result.

---

### Task 1: Delete dead code

**Files:**
- Modify: `src/flab2bp/__init__.py` (`hello`), `src/flab2bp/bench/snaporacle.py` (`to_tiles`, `tile_gap`, `Disagreement`), `src/flab2bp/lab/schema.py` (`Number`, `Module.is_productivity`, `Module.is_speed`, `Dataset.get_machine`, `Dataset.belt_ids`), `src/flab2bp/layout/finalize.py` (`_extent_fits`), `src/flab2bp/layout/freeform.py` (`OUTER_MAX`), `src/flab2bp/layout/hierarchy/partition.py` (`boundary_balances`), `src/flab2bp/layout/hierarchy/pressure.py` (`cut_pressure`), `src/flab2bp/layout/routing_domain.py` (`_belt_keepout_clear`, `Strip.input_is_shared`, `Strip.slot_of_input`, `Strip.east_of_input`), `src/flab2bp/layout/slots.py` (`direct_anchors`), `src/flab2bp/layout/transport_routing/allocation.py` (`ORDERS`), `src/flab2bp/layout/validate.py` (`_belt_run_rate`, `Context.junctions_feeding`, `Context.runs_drawing_from_junction`), `src/flab2bp/layout/sequence_solver.py` (`ExpansionBudget.searchable_total`, `SequenceSearchResult.exact_energy`), `src/flab2bp/layout/strategy_race.py` (`RaceChannels.publish_no_good`), `src/flab2bp/bench/types.py` (`CellResult.verified`), `src/flab2bp/dsp/catalog.py` (`Building.has_explicit_slots`), `src/flab2bp/rates/adjust.py` (`AdjustedRecipe.net_rate`), `src/flab2bp/rates/solve.py` (`SolvedGroup.utilisation`)
- Test: existing suites for each touched module (listed in step 4)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing; removals only. `pressure.py` may become empty apart from imports; if its only public symbol was `cut_pressure`, delete the module and its test file `tests/layout/hierarchy/test_pressure.py` if that file tests nothing else.

- [ ] **Step 1: Prove each symbol unreferenced**

For every symbol above, run both checks and record the result in the commit message:
```
# Serena: find_referencing_symbols(name_path="<symbol or Class/method>", relative_path="<file>") must return no references outside the definition.
grep -rn "<bare name>" src scripts tests docs/superpowers/specs web 2>/dev/null
```
A grep hit that is a string (a `monkeypatch.setattr(..., "<name>", ...)` target, a `dsp/registry.py` list entry, a docs mention) counts as a reference only if it names this symbol: a docs mention is fine to leave and does not block deletion; a monkeypatch target or registry entry blocks deletion until that site is removed in the same commit. If any symbol turns out referenced, leave it and list it in the commit message under "kept".

- [ ] **Step 2: Delete the symbols**

Use Serena `safe_delete_symbol` for each (it refuses when references exist). Delete tests that exist only to test a deleted symbol; keep tests that exercise a caller.

- [ ] **Step 3: Lint gate**

Run the lint gate on every changed file. `mypy` must stay clean: an unused import left behind by a deletion is a ruff F401 and must go too.

- [ ] **Step 4: Suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/lab tests/bench tests/rates tests/layout/hierarchy tests/layout/test_slots.py tests/layout/test_validate.py tests/layout/test_finalize.py tests/layout/test_strategy_race.py tests/layout/test_sequence_solver.py tests/layout/test_freeform.py; echo "exit=$?"`
Expected: `exit=0`. (If a listed file does not exist, drop it from the command and say so.)

- [ ] **Step 5: Commit**

One commit: `Delete unreferenced symbols left behind by retired designs`, body listing every deleted symbol with file, and any kept symbol with the reference that kept it.

---

### Task 2: Query the Buildings index instead of scanning it

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` at the three hand-rolled filters and the two `_power_plan` bucketing scans
- Test: `tests/layout/test_freeform.py`, `tests/layout/test_routing_lifecycle.py`, `tests/layout/hierarchy/test_compose.py` (existing)

**Interfaces:**
- Consumes: `Buildings.by_item(item_id) -> tuple[int, ...]`, `Buildings.by_kind(kind) -> tuple[int, ...]`, `Buildings.belts() -> tuple[int, ...]`, `Buildings.machines()`, `Buildings.sorters()`, `Buildings.by_index(index)` from `src/flab2bp/layout/buildings.py`; `Kind` from the same module (`from flab2bp.layout.buildings import Kind as BuildingKind` already exists in routing_domain).
- Produces: nothing new.

- [ ] **Step 1: Locate the five sites**

```
grep -n "enumerate(self.buildings)\|for b in canvas.buildings" src/flab2bp/layout/routing_domain.py
```
Expected five hits: two `enumerate(self.buildings)` comprehensions inside `_CompositionProjection` (one filtering `building.item_id == catalog.SPRAY_COATER_ID`, one filtering `not catalog.is_belt(...) and not catalog.is_sorter(...)`), two `for b in canvas.buildings:` loops inside `_power_plan` that bucket buildings by kind, and one `for b in canvas.buildings:` loop that skips non-belts. Read each with Serena `find_symbol` on the enclosing function (include_body) before editing.

- [ ] **Step 2: Rewrite each scan as an index query, preserving iteration order**

The index methods return indices in ascending order, which is the same order `enumerate` yields, so ordering-sensitive callers see the same sequence. Patterns:

```python
# was: [index for index, building in enumerate(self.buildings) if building.item_id == catalog.SPRAY_COATER_ID]
coaters = list(self.buildings.by_item(catalog.SPRAY_COATER_ID))

# was: [(index, building) for index, building in enumerate(self.buildings)
#       if not catalog.is_belt(building.item_id) and not catalog.is_sorter(building.item_id)]
belt_like = frozenset(self.buildings.belts()) | frozenset(self.buildings.sorters())
others = [(index, self.buildings[index]) for index in range(len(self.buildings)) if index not in belt_like]
# If Buildings.by_kind covers every non-belt, non-sorter kind, prefer:
# others = [(index, self.buildings[index]) for kind in (BuildingKind.MACHINE, BuildingKind.OTHER) for index in self.buildings.by_kind(kind)]
# and then sort by index to keep ascending order: others.sort(key=lambda pair: pair[0]).

# was: for b in canvas.buildings: if not catalog.is_belt(b.item_id): continue ...
for index in canvas.buildings.belts():
    b = canvas.buildings[index]
    ...

# _power_plan bucketing (two loops): replace each `for b in canvas.buildings:` with iteration over the
# kinds the loop's body distinguishes, e.g. machines = [canvas.buildings[i] for i in canvas.buildings.machines()]
```
Check what `Kind` values exist (`get_symbols_overview` on `buildings.py`, then `find_symbol("Kind", include_body=True)`) and confirm that the union of the kinds you iterate equals the set the original predicate accepted. Write that equivalence in the commit message.

- [ ] **Step 3: Lint gate, then suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_freeform.py tests/layout/test_routing_lifecycle.py tests/layout/hierarchy/test_compose.py tests/layout/test_routing_integration.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 4: Corpus gate** (routing-touching)

Run the corpus gate from the header. Expected: 167/13, zero status changes, no new area movers.

- [ ] **Step 5: Commit**

`Query the Buildings index at the five remaining hand-rolled scans`, body naming each site and the equivalence argument.

---

### Task 3: Guard against building scans nested in loops

**Files:**
- Create: `tests/test_no_building_scans_in_loops.py`

**Interfaces:**
- Consumes: the source tree under `src/flab2bp`.
- Produces: a test that fails when a `for` over `<expr>.buildings` or `enumerate(<expr>.buildings)` sits inside another loop in the same function.

- [ ] **Step 1: Write the guard**

```python
"""No pass over every building may sit inside another loop.

The indexed-scans rule (docs/superpowers/specs/2026-09-13-abstraction-review.md,
section 4): a linear scan of the building collection is a phase-boundary
pass, never a per-item step. Anything that needs "the buildings with
property X" inside a loop asks the Buildings index (by_kind, by_item,
belts, at_tile, in_box, ...). The 2026-09-13 review found zero violations;
this keeps it that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "flab2bp"


def _scans_buildings(node: ast.For | ast.comprehension) -> bool:
    target = node.iter
    if isinstance(target, ast.Call) and isinstance(target.func, ast.Name):
        if target.func.id == "enumerate" and target.args:
            target = target.args[0]
    return isinstance(target, ast.Attribute) and target.attr == "buildings"


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []

    def visit(node: ast.AST, loop_depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            depth = loop_depth
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                depth = 0
            if isinstance(child, ast.For):
                if _scans_buildings(child) and loop_depth > 0:
                    found.append(f"{path.name}:{child.lineno}")
                depth = loop_depth + 1
            elif isinstance(child, ast.While):
                depth = loop_depth + 1
            elif isinstance(child, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for generator in child.generators:
                    if _scans_buildings(generator) and loop_depth > 0:
                        found.append(f"{path.name}:{child.lineno}")
                depth = loop_depth + 1
            visit(child, depth)

    visit(tree, 0)
    return found


def test_no_building_scan_sits_inside_another_loop() -> None:
    violations = [v for path in sorted(SRC.rglob("*.py")) for v in _violations(path)]
    assert violations == [], (
        "a pass over every building inside a loop; use the Buildings index instead: "
        + ", ".join(violations)
    )
```

- [ ] **Step 2: Run it**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/test_no_building_scans_in_loops.py; echo "exit=$?"`
Expected: `exit=0`. If it reports violations, each is either a real nested scan (fix it as in Task 2 and note it in the commit) or a phase-boundary pass inside a loop over something small (for example a loop over blocks that scans each block's own buildings). For the second kind, add the site to an explicit allowlist in the test with a one-line reason, and keep the allowlist short.

- [ ] **Step 3: Lint gate, commit**

`Guard against building scans nested in loops`.

---

### Task 4: Finish the naming left by the tile-A* removal

**Files:**
- Modify: `src/flab2bp/layout/projection_world.py` (class `GeometricWorld` at line 63 → `ClearanceOracle`), `src/flab2bp/layout/routing_proposals.py` and every other importer (Serena rename covers them)
- Delete: `src/flab2bp/layout/route_kernel.py`
- Modify: `src/flab2bp/layout/geometric_router.py` (add `BACKEND: Final = "geometric"`), `src/flab2bp/layout/freeform.py:4680-4682`, `src/flab2bp/layout/sequence_solver.py:23,6539,6654`, `scripts/audit.py:95,225`, `scripts/route_profile.py:52,688`, `tests/layout/test_sequence_solver.py:21` and any other `route_kernel` reference
- Modify: `src/flab2bp/bench/snaporacle.py:197` (`Verdict` → `LadderVerdict`) and its references
- Modify: comments and docstrings that still say "A*", "astar" or describe expansions as node counts, in `src/flab2bp/layout/*.py` and `scripts/route_profile.py`; the `_geometric_kernel.pyx` and `geometric_router.py` lines that state "no A* fallback" stay.

**Interfaces:**
- Produces: `flab2bp.layout.geometric_router.BACKEND == "geometric"`; `flab2bp.layout.projection_world.ClearanceOracle`; `flab2bp.bench.snaporacle.LadderVerdict`.
- The `"route_backend"` stats key and its value `"geometric"` are unchanged.

- [ ] **Step 1: Rename the second `GeometricWorld`**

Serena: `rename_symbol(name_path="GeometricWorld", relative_path="src/flab2bp/layout/projection_world.py", new_name="ClearanceOracle")`. Then `grep -rn "GeometricWorld" src scripts tests` and confirm every remaining hit refers to `flab2bp.layout.geometric_world.GeometricWorld` (the kernel input). Update the class docstring's first line to say what it is: "Canvas clearance oracle for source and cell admission during proposals."

- [ ] **Step 2: Replace `route_kernel`**

Add to `geometric_router.py` after the imports:
```python
from typing import Final

#: The only routing backend; reported in placement stats as ``route_backend``.
BACKEND: Final = "geometric"
```
Replace each `route_kernel.selected_backend()` with `geometric_router.BACKEND` (import `geometric_router` where `route_kernel` was imported), then delete `src/flab2bp/layout/route_kernel.py` and any test that only tested it (`grep -rn route_kernel tests`).

- [ ] **Step 3: Rename the snap-oracle `Verdict`**

Serena: `rename_symbol(name_path="Verdict", relative_path="src/flab2bp/bench/snaporacle.py", new_name="LadderVerdict")`. Confirm `flab2bp.bench.scoring.Verdict` is untouched.

- [ ] **Step 4: Comments and docstrings**

```
grep -rn -i "astar\|A\*\|a-star" src/flab2bp scripts/route_profile.py --include='*.py' | grep -v "no A\* fallback\|not A\* expansion"
```
Rewrite each remaining hit to describe the geometric search ("the geometric search", "the search"), keeping historical measurements as they are ("the router had just got 2.1x faster"). Expected after: zero hits.

- [ ] **Step 5: Lint gate, suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_routing_proposals.py tests/layout/test_sequence_solver.py tests/bench tests/scripts/test_route_profile.py tests/layout/test_freeform.py; echo "exit=$?"` (drop files that do not exist and say so). Expected `exit=0`.

- [ ] **Step 6: Commit** (three commits, one per rename/removal): `Name the clearance oracle for what it is`, `Retire route_kernel: the backend is a constant`, `Name the snap-oracle verdict distinctly from the A/B verdict`.

---

### Task 5: Rename "expansions" to charged work in code identifiers

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` (`_MAX_EXPANSIONS` → `_MAX_SEARCH_WORK`; `_PathSearchResult.expansions` → `work`; `_PreparationDeadline.expansions` → `work`; local `pending_expansion` → `pending_work`; `total_expansions` and similar locals inside `_route_all`), `src/flab2bp/layout/global_router.py` (`_SearchResult.expansions` → `work`), `src/flab2bp/layout/last_mile.py` (`B_LOW_LEVEL_EXPANSIONS` → `B_LOW_LEVEL_WORK`; `ClusterResult.expansions` → `work`), `src/flab2bp/layout/freeform.py` (`_ROUTING_EXPANSIONS_PER_SECOND` → `_ROUTING_WORK_PER_SECOND`), `src/flab2bp/layout/sequence_solver.py` (importer of the constant), `scripts/route_profile.py`, `scripts/route_bench.py`, `scripts/last_mile_bench.py`, tests that reference the fields
- Leave: `sequence_solver.ExpansionBudget` (absorbed by Plan B), every `stats[...]` key string, `placement.stats` consumers, evidence JSON.

**Interfaces:**
- Produces: `_PathSearchResult.work`, `_SearchResult.work`, `ClusterResult.work`, `_PreparationDeadline.work`, `_MAX_SEARCH_WORK`, `B_LOW_LEVEL_WORK`, `_ROUTING_WORK_PER_SECOND`. Plan B and the routing branch use these names.

- [ ] **Step 1: Inventory**

```
grep -rn -i "expansion" src/flab2bp scripts tests --include='*.py' | grep -v "ExpansionBudget" | wc -l
grep -rn "\"[a-z_]*expansion[a-z_]*\"" src/flab2bp | cut -c1-120
```
The second command lists stats keys and string literals; those are the ones that must NOT change. Record both counts in the commit message.

- [ ] **Step 2: Rename with Serena, one symbol per `rename_symbol` call**

Order: the four dataclass fields (each `rename_symbol(name_path="<Class>/expansions", ...)`), then the three constants, then the `_route_all` locals (`find_symbol("_route_all", depth=1)` to see closure names; rename `pending_expansion` and any `*_expansions` local through Serena so call sites inside closures follow). After each rename run `./.venv/bin/ruff check src tests scripts` to catch a stale reference early.

- [ ] **Step 3: Docstrings**

Every renamed field's docstring says "charged geometric work units (see `GeometricMetrics.charged_work`)". The stats key writers get a one-line comment: `# stats key kept as "expansions": evidence tooling reads it`.

- [ ] **Step 4: Lint gate, suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_kernel.py tests/layout/test_last_mile.py tests/layout/test_route_witnesses.py tests/layout/test_routing_integration.py tests/layout/test_routing_lifecycle.py tests/layout/test_technology_routing_search.py tests/scripts tests/layout/test_freeform.py tests/layout/test_sequence_solver.py; echo "exit=$?"`. Expected `exit=0`.

- [ ] **Step 5: Corpus gate** (routing-touching by extent, not by intent). Expected: identical to control.

- [ ] **Step 6: Commit**: `Call the search charge what it is: work, not expansions`.

---

### Task 6: Merge the same-file twins and the hierarchy flow loops

**Files:**
- Modify: `src/flab2bp/dsp/catalog.py` (`_recipe_ids`, `_item_ids`), `src/flab2bp/layout/slots.py` (`slot_offset`, `port_offset`), `src/flab2bp/layout/hierarchy/partition.py` (`_flow_between`, `derive_cuts`, `sub_spec`)
- Test: `tests/dsp/test_catalog.py`, `tests/layout/test_slots.py`, `tests/layout/hierarchy/test_partition.py` (existing; add the two tests below)

**Interfaces:**
- Produces: `catalog._ids_table(path: Path, aliases: Mapping[str, str]) -> dict[str, int]`; `slots._grid_offset(pose) -> tuple[float, float, float]`; `partition.made_by(units: Iterable[Unit]) -> dict[str, Fraction]`.

- [ ] **Step 1: Write the failing tests**

In `tests/dsp/test_catalog.py`:
```python
def test_recipe_and_item_tables_share_one_reader() -> None:
    from flab2bp.dsp import catalog

    assert catalog._recipe_ids() == catalog._ids_table(catalog._RECIPES, catalog._RECIPE_ALIASES)
    assert catalog._item_ids() == catalog._ids_table(catalog._ITEMS, catalog._ITEM_ALIASES)
```
In `tests/layout/hierarchy/test_partition.py`:
```python
def test_made_by_sums_each_unit_output_once(units_fixture) -> None:
    from collections import defaultdict
    from fractions import Fraction

    from flab2bp.layout.hierarchy import partition

    units = units_fixture  # any existing fixture that yields a list[Unit]
    expected: dict[str, Fraction] = defaultdict(Fraction)
    for u in units:
        for item in u.group.outputs_per_machine:
            expected[item] += u.produces(item)
    assert dict(partition.made_by(units)) == dict(expected)
```
Use whatever fixture the file already has for a unit list (read the file first; if none, build two `Unit`s the way an existing test does).

- [ ] **Step 2: Run them to see them fail** (`AttributeError`).

- [ ] **Step 3: Implement**

`catalog.py`: one reader,
```python
def _ids_table(path: Path, aliases: Mapping[str, str]) -> dict[str, int]:
    values = _array(_json(path), str(path))
    table: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for index, value in enumerate(values):
        row_path = f"{path}[{index}]"
        row = _mapping(value, row_path)
        name = _string(_required(row, "name", row_path), f"{row_path}.name")
        dsp_id = _integer(_required(row, "id", row_path), f"{row_path}.id")
        table[_kebab(name)] = dsp_id
        by_name[name] = dsp_id
    for factoriolab_id, dsp_name in aliases.items():
        if dsp_name in by_name:
            table[factoriolab_id] = by_name[dsp_name]
    return table


@cache
def _recipe_ids() -> dict[str, int]:
    return _ids_table(_RECIPES, _RECIPE_ALIASES)


@cache
def _item_ids() -> dict[str, int]:
    return _ids_table(_ITEMS, _ITEM_ALIASES)
```
`slots.py`: both offsets call one converter,
```python
def _grid_offset(pose, yaw: float) -> tuple[float, float, float]:
    """A prefab pose relative to the building centre, in tiles and levels."""
    wx, wy = to_world((pose.dx, pose.dy), yaw)
    return (wx / colliders.GRID_ARC, wy / colliders.GRID_ARC, pose.dz / WORLD_UNITS_PER_LEVEL)


def slot_offset(item_id: int, yaw: float, slot: int) -> tuple[float, float, float]:
    """(docstring unchanged)"""
    return _grid_offset(_pose(item_id, slot), yaw)


def port_offset(item_id: int, yaw: float, port: int) -> tuple[float, float, float]:
    """(docstring unchanged)"""
    return _grid_offset(_port_pose(item_id, port), yaw)
```
`partition.py`: one accumulator used by `_flow_between`, `derive_cuts` and `sub_spec` (read each with Serena first; each has a `made`/`produced` dict built by the same two nested loops):
```python
def made_by(units: Iterable[Unit]) -> dict[str, Fraction]:
    """Item production of a unit set, summed per item."""
    made: dict[str, Fraction] = defaultdict(Fraction)
    for u in units:
        for item in u.group.outputs_per_machine:
            made[item] += u.produces(item)
    return made
```
and in `_flow_between`:
```python
    for src, dst in ((a, b), (b, a)):
        made = made_by(src)
        for u in dst:
            for item in u.group.inputs_per_machine:
                if item in made:
                    total += min(made[item], u.consumes(item))
```
If `derive_cuts` or `sub_spec` sums consumption rather than production, add the mirror `consumed_by(units)` the same way rather than forcing `made_by` to fit.

- [ ] **Step 4: Tests pass; lint gate; suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/dsp tests/layout/test_slots.py tests/layout/hierarchy; echo "exit=$?"`. Expected `exit=0`.

- [ ] **Step 5: Commit** (three commits, one per file).

---

### Task 7: One quaternion module

**Files:**
- Create: `src/flab2bp/dsp/quaternion.py`
- Modify: `src/flab2bp/dsp/colliders.py` (delete `_qmul`, `_qrot`, `_norm`, `_cross`, `_dot`, `_look_rotation`, `_spherical_rotation`; import from quaternion), `src/flab2bp/dsp/planet.py` (delete `_norm`, `_cross`, `_dot3`, `_qmul`, `_look_rotation`; make `spherical_rotation` delegate), `src/flab2bp/dsp/splitter_ports.py` (`_rotate_vector` → `quaternion.rotate`), `scripts/extract_dsp_colliders.py`, `scripts/extract_dsp_slot_poses.py` (import instead of copying)
- Create: `tests/dsp/test_quaternion.py`

**Interfaces:**
- Produces: `quaternion.Vec3 = tuple[float, float, float]`, `quaternion.Quat = tuple[float, float, float, float]`, `multiply(a: Quat, b: Quat) -> Quat`, `rotate(q: Quat, v: Vec3) -> Vec3`, `normalize(v: Vec3) -> Vec3` (zero vector returns itself, matching planet's guard), `cross(a, b) -> Vec3`, `dot(a, b) -> float`, `look_rotation(forward: Vec3, up: Vec3) -> Quat` (colliders' form with the degenerate-right guard), `spherical_rotation(direction: Vec3, yaw_deg: float) -> Quat`, `angle_deg(a: Quat, b: Quat) -> float` (moved from planet).

- [ ] **Step 1: Write the agreement test**

`tests/dsp/test_quaternion.py` embeds the two reference implementations as they stand at master 9c958bc9 and checks the new module against both:
```python
from __future__ import annotations

import math
import random

from flab2bp.dsp import quaternion as q

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


# Reference: flab2bp/dsp/colliders.py at 9c958bc9 (Unity port used by the collider extractor).
def _c_norm(v: Vec3) -> Vec3:
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m)


def _c_cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _c_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _c_qmul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _c_qrot(qq: Quat, v: Vec3) -> Vec3:
    x, y, z, w = qq
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


def _c_look_rotation(forward: Vec3, up: Vec3) -> Quat:
    f = _c_norm(forward)
    r = _c_cross(up, f)
    r = _c_norm(r) if _c_dot(r, r) > 1e-12 else (1.0, 0.0, 0.0)
    u = _c_cross(f, r)
    m00, m01, m02 = r[0], u[0], f[0]
    m10, m11, m12 = r[1], u[1], f[1]
    m20, m21, m22 = r[2], u[2], f[2]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def _c_spherical_rotation(pos: Vec3, angle_deg: float) -> Quat:
    p = _c_norm(pos)
    r = _c_cross(p, (0.0, 1.0, 0.0))
    if _c_dot(r, r) < 1e-4:
        sign = 1.0 if p[1] >= 0.0 else -1.0
        r = (sign, 0.0, 0.0)
        forward = (0.0, 0.0, sign)
    else:
        r = _c_norm(r)
        forward = _c_norm(_c_cross(r, p))
    out = _c_look_rotation(forward, p)
    if angle_deg == 0.0:
        return out
    h = math.radians(angle_deg) * 0.5
    return _c_qmul(out, (0.0, math.sin(h), 0.0, math.cos(h)))


# Reference: flab2bp/dsp/planet.py at 9c958bc9 (the runtime's own port, transposed naming).
def _p_look_rotation(forward: Vec3, up: Vec3) -> Quat:
    f = _c_norm(forward)
    r = _c_norm(_c_cross(up, f))
    u = _c_cross(f, r)
    m00, m01, m02 = r
    m10, m11, m12 = u
    m20, m21, m22 = f
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return ((m12 - m21) / s, (m20 - m02) / s, (m01 - m10) / s, s * 0.25)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (s * 0.25, (m10 + m01) / s, (m20 + m02) / s, (m12 - m21) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m10 + m01) / s, s * 0.25, (m21 + m12) / s, (m20 - m02) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m20 + m02) / s, (m21 + m12) / s, s * 0.25, (m01 - m10) / s)


def _p_spherical_rotation(direction: Vec3, yaw_deg: float) -> Quat:
    p = _c_norm(direction)
    r = _c_cross(p, (0.0, 1.0, 0.0))
    if _c_dot(r, r) < 1e-4:
        sign = 1.0 if p[1] >= 0.0 else -1.0
        forward = (0.0, 0.0, sign)
    else:
        forward = _c_norm(_c_cross(_c_norm(r), p))
    out = _p_look_rotation(forward, p)
    if yaw_deg == 0.0:
        return out
    half = math.radians(yaw_deg) * 0.5
    return _c_qmul(out, (0.0, math.sin(half), 0.0, math.cos(half)))


def _close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-9) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _random_vec(rng: random.Random) -> Vec3:
    return (rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-3, 3))


def _random_quat(rng: random.Random) -> Quat:
    raw = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
    n = math.sqrt(sum(c * c for c in raw))
    return (raw[0] / n, raw[1] / n, raw[2] / n, raw[3] / n)


def test_multiply_rotate_cross_match_the_collider_port() -> None:
    rng = random.Random(20260913)
    for _ in range(500):
        a, b, v = _random_quat(rng), _random_quat(rng), _random_vec(rng)
        assert _close(q.multiply(a, b), _c_qmul(a, b))
        assert _close(q.rotate(a, v), _c_qrot(a, v))
    for _ in range(200):
        v, w = _random_vec(rng), _random_vec(rng)
        assert _close(q.cross(v, w), _c_cross(v, w))
        assert abs(q.dot(v, w) - _c_dot(v, w)) <= 1e-9


def test_look_rotation_matches_both_ports_away_from_the_degenerate_axis() -> None:
    rng = random.Random(7)
    for _ in range(500):
        forward, up = _random_vec(rng), _random_vec(rng)
        r = _c_cross(up, forward)
        if _c_dot(r, r) <= 1e-6:
            continue
        got = q.look_rotation(forward, up)
        assert _close(got, _c_look_rotation(forward, up))
        assert _close(got, _p_look_rotation(forward, up))


def test_look_rotation_keeps_the_collider_guard_when_up_is_parallel_to_forward() -> None:
    forward, up = (0.0, 1.0, 0.0), (0.0, 2.0, 0.0)
    assert _close(q.look_rotation(forward, up), _c_look_rotation(forward, up))


def test_spherical_rotation_matches_both_ports_including_the_poles() -> None:
    rng = random.Random(11)
    cases = [_random_vec(rng) for _ in range(300)] + [(0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 1.0, 1e-3)]
    for direction in cases:
        for yaw in (0.0, 90.0, -33.5, 180.0):
            got = q.spherical_rotation(direction, yaw)
            assert _close(got, _c_spherical_rotation(direction, yaw))
            assert _close(got, _p_spherical_rotation(direction, yaw))


def test_normalize_leaves_the_zero_vector_alone() -> None:
    assert q.normalize((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert _close(q.normalize((0.0, 3.0, 4.0)), (0.0, 0.6, 0.8))
```
- [ ] **Step 2: Run to fail** (`ModuleNotFoundError: flab2bp.dsp.quaternion`).

- [ ] **Step 3: Write the module**

```python
"""Unity quaternion arithmetic shared by the collider extractor and the runtime.

One port of ``Quaternion.LookRotation``, ``Maths.SphericalRotation`` and the
vector helpers around them. Twelve copies of these lived in colliders,
planet, splitter_ports and two extractor scripts; numeric drift between the
extractor and the runtime was invisible because each carried its own.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


def normalize(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return v if n == 0.0 else (v[0] / n, v[1] / n, v[2] / n)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def multiply(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def rotate(q: Quat, v: Vec3) -> Vec3:
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def look_rotation(forward: Vec3, up: Vec3) -> Quat:
    """``Quaternion.LookRotation``; a degenerate right axis falls back to +X."""
    f = normalize(forward)
    r = cross(up, f)
    r = normalize(r) if dot(r, r) > 1e-12 else (1.0, 0.0, 0.0)
    u = cross(f, r)
    m00, m01, m02 = r[0], u[0], f[0]
    m10, m11, m12 = r[1], u[1], f[1]
    m20, m21, m22 = r[2], u[2], f[2]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def spherical_rotation(direction: Vec3, yaw_deg: float) -> Quat:
    """``Maths.SphericalRotation``: upright at ``direction``, turned by ``yaw``."""
    p = normalize(direction)
    r = cross(p, (0.0, 1.0, 0.0))
    if dot(r, r) < 1e-4:
        sign = 1.0 if p[1] >= 0.0 else -1.0
        forward: Vec3 = (0.0, 0.0, sign)
    else:
        forward = normalize(cross(normalize(r), p))
    q = look_rotation(forward, p)
    if yaw_deg == 0.0:
        return q
    half = math.radians(yaw_deg) * 0.5
    return multiply(q, (0.0, math.sin(half), 0.0, math.cos(half)))


def angle_deg(a: Quat, b: Quat) -> float:
    """``Quaternion.Angle(a, b)`` in degrees: ``2 * acos(min(|a·b|, 1))``."""
    d = min(abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]), 1.0)
    return math.degrees(2.0 * math.acos(d))
```
Note the two prior `_norm` guards differed: colliders divided by zero on a zero vector, planet returned it. The module keeps planet's guard; the test for `look_rotation` skips the degenerate cases where colliders would have raised, and the poles test covers the branch both ports guard explicitly.

- [ ] **Step 4: Switch the five users**

`colliders.py`: delete its seven helpers, `from flab2bp.dsp import quaternion`, replace `_qmul` → `quaternion.multiply`, `_qrot` → `quaternion.rotate`, `_cross` → `quaternion.cross`, `_dot` → `quaternion.dot`, `_norm` → `quaternion.normalize`, `_look_rotation` → `quaternion.look_rotation`, `_spherical_rotation` → `quaternion.spherical_rotation` (Serena `find_referencing_symbols` on each before deleting; keep `Quat`/`Vec3` aliases in colliders re-exported from quaternion since other modules import them from there). `planet.py`: same for its five, and `spherical_rotation = quaternion.spherical_rotation`, `quaternion_angle_deg = quaternion.angle_deg` kept as names because callers import them from planet. `splitter_ports.py`: `_rotate_vector` → `quaternion.rotate`. The two scripts: replace their copies with `from flab2bp.dsp import quaternion` (scripts already put `src` on `sys.path`; check the first 20 lines of each).

- [ ] **Step 5: Tests pass; lint gate; suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/dsp tests/layout/test_slots.py tests/layout/test_validate.py tests/scripts; echo "exit=$?"`. Expected `exit=0`. The existing planet test `test_the_projection_agrees_with_colliders_at_the_equator` must still pass and now compares a function with itself; rewrite its docstring to say it pins the port against the recorded game values instead, or delete it if it has no independent oracle (say which in the commit).

- [ ] **Step 6: Corpus gate** (colliders feed placement geometry; treat as routing-touching). Expected identical.

- [ ] **Step 7: Commit**: `Share one quaternion port between the extractor and the runtime`.

---

### Task 8: One grid-index codec and one search-result adapter

**Files:**
- Modify: `src/flab2bp/layout/geometric_world.py` (add `GridIndex`, make `GeometricWorld.cell` use it), `src/flab2bp/layout/routing_domain.py` (`_Grid.index` delegates; `_geometric_search` builds `_PathSearchResult` through the adapter), `src/flab2bp/layout/global_router.py` (`_live_index`, `_decode_cell` use the codec; `_SearchResult` built through the adapter), `src/flab2bp/layout/geometric_router.py` (add `SearchOutcome` and `summarize`)
- Test: `tests/layout/test_geometric_world.py` (create), `tests/layout/test_route_kernel.py`, `tests/layout/test_global_router.py` if present

**Interfaces:**
- Produces:
```python
# geometric_world.py
@dataclass(frozen=True, slots=True)
class GridIndex:
    """Flat index <-> cell for a box of rows*levels columns starting at (gx0, gy0)."""
    gx0: int
    gy0: int
    rows: int      # gh in _Grid: cells per column in y
    levels: int
    def encode(self, cell: Cell) -> int: return ((cell[0] - self.gx0) * self.rows + (cell[1] - self.gy0)) * self.levels + cell[2]
    def decode(self, index: int) -> Cell:
        column, z = divmod(index, self.levels)
        x, y = divmod(column, self.rows)
        return x + self.gx0, y + self.gy0, z

# geometric_router.py
@dataclass(frozen=True, slots=True)
class SearchOutcome:
    path: tuple[int, ...] | None
    work: int
    exhausted_budget: bool   # kind == "budget"
    cancelled: bool          # kind == "cancelled"
    exhausted: bool          # kind == "exhausted"
def summarize(result: GeometricResult) -> SearchOutcome
```
- Consumes: `_Grid.gx0`, `_Grid.gy0`, `_Grid.gh`, `_Grid.levels`, `_Grid.xstep == gh * levels` (verify with `find_symbol("_Grid", include_body=True)`; if `xstep` is not `gh * levels`, stop and report).

- [ ] **Step 1: Write the failing tests**

`tests/layout/test_geometric_world.py`:
```python
from flab2bp.layout.geometric_world import GridIndex


def test_grid_index_round_trips_every_cell_of_a_small_box() -> None:
    codec = GridIndex(gx0=-3, gy0=5, rows=4, levels=3)
    seen = set()
    for x in range(-3, 3):
        for y in range(5, 9):
            for z in range(3):
                index = codec.encode((x, y, z))
                assert codec.decode(index) == (x, y, z)
                seen.add(index)
    assert seen == set(range(6 * 4 * 3))


def test_grid_index_matches_the_routing_grid_layout() -> None:
    # _Grid computes (x - gx0) * xstep + (y - gy0) * levels + lvl with xstep = gh * levels.
    codec = GridIndex(gx0=2, gy0=-1, rows=7, levels=2)
    assert codec.encode((2, -1, 0)) == 0
    assert codec.encode((2, 0, 1)) == 3
    assert codec.encode((3, -1, 0)) == 7 * 2
```
and in `tests/layout/test_route_kernel.py` (append):
```python
def test_summarize_maps_each_kernel_outcome_to_one_flag() -> None:
    from flab2bp.layout import geometric_router as gr

    def result(kind: str) -> gr.GeometricResult:
        metrics: gr.GeometricMetrics = {
            "charged_work": 5, "prepared_cells": 0, "interval_pops": 0, "labels": 0, "offers": 0,
            "intersections": 0, "profile_scans": 0, "certified_edges": 0, "retained_native_bytes": 0,
            "copied_history_cells": 0, "preparation_s": 0.0, "search_s": 0.0, "certification_s": 0.0,
        }
        return gr.GeometricResult(kind, (1, 2) if kind == "routed" else None, None, (), None, metrics)  # type: ignore[arg-type]

    routed = gr.summarize(result("routed"))
    assert (routed.path, routed.work, routed.exhausted_budget, routed.cancelled, routed.exhausted) == ((1, 2), 5, False, False, False)
    assert gr.summarize(result("budget")).exhausted_budget
    assert gr.summarize(result("cancelled")).cancelled
    assert gr.summarize(result("exhausted")).exhausted
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement the codec and adapter** exactly as in Interfaces, then rewire:
  - `_Grid.index`: keep the bounds check (it raises `IndexError`; `GridIndex.encode` does not check), then `return self.codec.encode(cell)` where `codec` is a `GridIndex(self.gx0, self.gy0, self.gh, self.levels)` stored on the grid at construction (`_make_grid`).
  - `GeometricWorld.cell` → `self.codec.decode(index)` with `codec` built in `from_grid`.
  - `global_router._decode_cell(grid, index)` → `grid.codec.decode(index)`; `_live_index` keeps its `None`-on-outside check and then `grid.codec.encode(cell)` (no double check).
  - `transport_routing/paths.py` `_key`/`_decode` encode a different domain; leave them and note it in the commit.
  - `_geometric_search`: after `result = geometric_router.route(...)`, `outcome = geometric_router.summarize(result)`; use `outcome.work` for the budget charge and `outcome.exhausted_budget or outcome.cancelled` for the BUDGET return; keep everything else. `global_router`: build `_SearchResult(outcome.path_cells..., outcome.work, outcome.exhausted_budget, outcome.cancelled)` from the same summary.

- [ ] **Step 4: Tests pass; lint gate; suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_geometric_world.py tests/layout/test_route_kernel.py tests/layout/test_technology_routing_search.py tests/layout/test_last_mile.py tests/layout/test_routing_lifecycle.py tests/layout/test_sequence_solver.py tests/layout/test_freeform.py; echo "exit=$?"`. Expected `exit=0`.

- [ ] **Step 5: Corpus gate** (routing-touching). Expected identical. Also run `taskset -c 96-127 env PYTHONHASHSEED=0 ./.venv/bin/python3.14 scripts/route_bench.py` if it has a replay MATCH mode (read its docstring); a MATCH line is the byte-identity witness for the search itself.

- [ ] **Step 6: Commit** (two commits): `Encode and decode routing cells through one GridIndex`, `Summarize a kernel result once for both routers`.

---

## Self-review notes

- Spec coverage: review items 2 (Task 1), 4 (Tasks 4 and 5), 6 (Task 7), 7 (Task 8), 9 (Tasks 2, 3 and 6) are covered here. Items 1, 3, 5, 8 and 10 are Plans B, C and D by the program sequence in the header. The indexed-scans rule has a durable guard (Task 3).
- The `url.py` twin pair (`_parse_recipes`/`_parse_machines`) is deliberately not merged: the field layouts differ per setting type and a shared parser would hide that. The `validate.py` and `finalize.py` pairs at 0.60 and 0.56 similarity are left for a reader with those files open; they are not mechanical.
- The `_Canvas.free`/`free_owned_guard` pair is on the routing hot path and is left to Plan C, which restructures that class's users.
- Type consistency: `GridIndex.rows` is `_Grid.gh`; `SearchOutcome.work` is `GeometricMetrics["charged_work"]`; Task 5 renames the `expansions` fields to `work` before Task 8 reads them, so Task 8 must run after Task 5.
