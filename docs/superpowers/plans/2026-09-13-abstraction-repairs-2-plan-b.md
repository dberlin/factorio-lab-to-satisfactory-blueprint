# Abstraction Repairs 2, Plan B: The Deadline and Budget Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the layout package's five deadline predicates, three "clock ran out" exception types, 42 `["left"]` dict-budget sites and the `ExpansionBudget` ledger into one typed, injectable `WorkBudget` contract owned by a single module, with placement results byte-identical.

**Architecture:** A new `src/flab2bp/layout/budget.py` becomes the home of the concept. It is the existing `transport_routing/budget.py` moved up a level, its `deadline` widened to `float | None`, its clock made late-binding, plus one free `expired()` predicate and one `BudgetExhausted` exception base. The dict budgets then become that same `WorkBudget` object carrying an `int` ledger in `left`, migrated family by family: first the search leaf (`_geometric_search`), then the `_route_all` body, then the seeding sites, then tests and scripts. `ExpansionBudget` moves into the module last. The deadline itself stays an explicit `float | None` argument in `routing_domain`: see Global Constraints for why, and Task 4's risk line for the evidence.

**Tech Stack:** Python 3.14, uv venv at `.venv`, pytest (no summary line prints on this box: use the exit code), ruff, mypy strict (`files = ["src", "tests"]`; `scripts/` is not type-checked), Serena symbol tools for reads and renames.

**Spec:** `docs/superpowers/specs/2026-09-13-abstraction-review.md` (section 5 item P1, section 3 item R2, section 9 item 1). Program sequence: Plan A (merged at 9758411d) → sprayed-Universe routing (merged at 549852e5) → **Plan B (this)** → Plan C `_route_all` decomposition (review item 3) → Plan D private-name promotion, import guard, routing_domain split (items 5 and 8) → parameter objects (item 10).

**Branch point:** master `549852e5`, not the `9758411d` in the task brief; the sprayed-Universe routing merge landed after Plan A. Every line number below was verified at `549852e5`.

## Global Constraints

- No behavior change. Placement results must be identical; the corpus gate below is the proof.
- `left` semantics are preserved exactly, site for site. The dict is *aliased* (`net_budget = {"left": allowance} if coverage_pass else budget` at `routing_domain.py:9437` hands the caller's own object through), *set* rather than decremented in the search leaf (`budget["left"] = start_left - work` at `routing_domain.py:4999`), and *decremented by a child's spend* at three carve sites (`routing_domain.py:7894`, `:8497`, `:8879`, `:9451`). The replacement type must therefore be a **mutable, non-frozen, non-slots-required dataclass shared by reference**. A frozen dataclass or a `dataclasses.replace` anywhere in this migration is a defect.
- Every deadline comparison stays `>=` against a `float | None` where `None` never expires. A `>` anywhere in this plan is a defect.
- The deadline stays an explicit parameter in `routing_domain`; it is **not** folded into the budget object there. `_route_all` rebinds its `deadline` nonlocal mid-run (`finally: deadline = route_deadline`, `routing_domain.py:8164`) and derives per-query deadlines (`_ordinary_query_deadline`, `routing_domain.py:7802`). Folding a rebound nonlocal into a shared object changes which clock a later check reads.
- The three exception types keep their identity as **subclasses of one new `BudgetExhausted` base**; they are not merged into one class. `routing_domain.py:8161-8162` converts a caught `_PreparationDeadline` into a `_GeometricDeadline` (`routing_proposals.Deadline`) precisely so the enclosing `except _GeometricDeadline` at `:7798` returns `None` instead of letting the preparation deadline propagate. One class makes that conversion a no-op and changes which handler fires. This is a deliberate, evidenced deviation from the spec's "three exception types become one `BudgetExhausted` with an optional payload".
- Names that tests and scripts monkeypatch must survive as module-level attributes in their current module, even when their body becomes a one-line delegation: `routing_domain._expired` (patched at `tests/layout/test_freeform.py:20914`), `routing_proposals.check_deadline` (patched at `tests/layout/test_routing_integration.py:202` and `:236`), `routing_domain._geometric_search`, `routing_domain._MAX_SEARCH_WORK`, `routing_domain._make_grid`.
- A delegating predicate must pass **its own module's** `time.monotonic` as the clock. `tests/layout/hierarchy/test_compose.py:536` replaces `compose.time` with a fake clock object, and `tests/layout/hierarchy/test_strategy.py` does the same for `strategy` at six sites. A helper that silently reads `budget.time.monotonic` stops seeing those fakes and the tests pass for the wrong reason.
- Stats keys are evidence names read by committed tooling and keep their current spelling: `"expansions"`, `"expansion_allowance"` (`sequence_solver.py:6728`), `"route_backend"`, `"work"` in `last_mile_counts`. Only code identifiers, comments and docstrings change.
- `BudgetCause` (`route_feedback.py:84`: `UNKNOWN`/`DEADLINE`/`ALLOWANCE`/`BOUNDED`) keeps its spelling and its values on `_PathSearchResult` and `NetFailure`. Tests and committed evidence match the refusal text.
- Adopting `WorkBudget` in layout must **not** import the transport 4 GiB RSS refusal into layout paths. `WorkBudget.check()` keeps the memory probe; the new `expired()` predicate is clock-only. A layout site that calls `check()` is a defect.
- Do not touch `pyproject.toml`, `uv.lock`, or anything under `.venv`.
- Never run interactive git. Prefix git commands with `GIT_EDITOR=true`; commit with `-m` or `-F`. Never run `git commit` without `-m`/`-F`, `git rebase -i`, or `git add -i`.
- One logical change per commit, on branch `abstraction-repairs-2b` cut from `549852e5` in the main checkout. Every commit must leave the tree importable: `./.venv/bin/python3.14 -c "import flab2bp.pipeline"` exits 0. Commit messages end with the attribution lines the session provides.
- Serena: reads (`find_symbol`, `find_referencing_symbols`, `get_symbols_overview`) and `rename_symbol` are the required tools for symbol work; grep is for strings and comments. **Serena `rename_symbol` misses `dataclasses.replace(..., f=...)` keyword arguments and untyped attribute reads.** After every rename run `grep -rn "\.<old>\b\|<old>=" src scripts tests` and fix what it finds before committing.
- Only one agent may hold the Serena project at a time; if a concurrent worktree agent is running, use Read/Edit plus the LSP tools instead and say so in the commit message.
- Box discipline: before any test run heavier than one file, `vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}'` must print below 64 (never use load average). Pin runs with `taskset -c 96-127`. Record the figure beside every timing.
- Delete only what a reference search including string references proves unused: `find_referencing_symbols`, then `grep -rn "<name>" src scripts tests docs/superpowers/specs web 2>/dev/null`. `monkeypatch.setattr(..., "<name>", ...)` targets and the `dsp/registry.py` `LintException` table (which names `"_geometric_search"` at `src/flab2bp/dsp/registry.py:1217`) reference symbols as strings.

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
cd $S && python -m compileall -q $S/src && taskset -c 96-127 env PYTHONHASHSEED=0 PYTHONPATH=$S/src:$S /home/dannyb/sources/factorio-lab-to-blueprint/.venv/bin/python3.14 $S/scripts/audit.py --tier stress --strategy all --budget 15 --jobs 4 --max-seconds 900 --json $S/audit.jsonl
./.venv/bin/python3.14 .local-evidence/2026-09-13-session/probes/compare_audits.py .local-evidence/2026-09-13-session/audit-master-control-9758411d.jsonl $S/audit.jsonl
```

**Control:** `.local-evidence/2026-09-13-session/audit-master-control-9758411d.jsonl` — 180 rows, **167 CLEAN / 13 REFUSED**, audit exit 1, no `TEARDOWN FAULT`. Its notes are in `audit-master-control-9758411d.md`. It was taken at `9758411d`, one commit behind this plan's branch point; the notes record that `549852e5` is a cache-bound re-measurement with no geometry change, so it is the right baseline for this branch. Do not take a new control and do not use Plan A's `audit-master-control-20260913.jsonl`.

Pass: 167 CLEAN / 13 REFUSED, zero status changes versus the control, and area changes only on the known run-to-run movers the notes list: super-magnetic-ring (all strategies), electromagnetic-matrix (freeform), casimir-crystal (freeform, best, hierarchical), processor-1 best, information-matrix (0 freeform and hierarchical, 1 best, 2 sequence-pair and hierarchical), plastic-1 (best and freeform), quantum-chip-1 best, quantum-chip-2 hierarchical, universe-matrix-0 freeform and best. A status change on any cell, or an area change outside that list, fails the task — and before calling it a regression, run a paired same-commit second round, which is what the notes require. The audit exits 1 on any refusal; that is expected, and the 13 are the fixed set of universe-matrix cells the notes name. A `TEARDOWN FAULT` line after the rows is a harness cleanup fault, not a result.

## Verified inventory

Counted at `549852e5`. The spec's figures are in the last column; where they differ, this plan's figure governs.

| Thing | Verified | Sites | Spec said |
|---|---|---|---|
| Named deadline predicates | 5 | `routing_domain.py:559` `_expired` (43 calls), `compact_seed.py:1377` `_deadline_reached` (5), `finalize.py:4511` `_completion_expired` (5), `routing_proposals.py:146` `check_deadline` (22, raising), `hierarchy/compose.py:605` `_spent` (19) | 4 |
| Deadline closures | 3 | `pipeline.py:1154` `attempt_expired`, `sequence_solver.py:4859` `deadline_reached`, `sequence_solver.py:5150` `compact_deadline_reached` | 3 |
| Inline `monotonic() >= deadline` outside those | 8 | `route_primitives.py:216,246`; `freeform.py:3645,5749`; `sequence_solver.py:3446,3497,3503,5538` | 33 |
| "Clock ran out" exception types | 3 | `routing_proposals.py:142` `Deadline`, `routing_domain.py:12513` `_PreparationDeadline`, `hierarchy/compose.py:248` `_PackingDeadline` (plus `transport_routing/budget.py:17` `TransportRefusal("DEADLINE")`) | 3 |
| `raise _PreparationDeadline` | 155 | all in `routing_domain.py` | not counted |
| `except _PreparationDeadline` in src | 23 | `routing_domain` 12, `hierarchy/compose` 7, `freeform` 4 (plus `sequence_solver.py:5368`, `transport_routing/runtime.py:142`) | not counted |
| `raise _PackingDeadline` | 12 | `hierarchy/compose.py` | not counted |
| `["left"]` subscripts in src | 43 on 37 lines (42 code, 1 in a comment at `freeform.py:273`) | `budget` 29, `search_budget` 5, `logical` 3, `private` 3, `attempt_budget` 2, `net_budget` 1 | 35 |
| `{"left": …}` constructions in src | 9 | `routing_domain.py:6269,7876,8468,8872,9437,12264`, `sequence_solver.py:4798`, `hierarchy/compose.py:1787`, `freeform.py:4576` | not counted |
| `["left"]` lines in tests / scripts | 25 / 6 | `test_freeform` 27 constructions, `test_technology_routing_search` 13, `test_route_witnesses` 12, `test_routing_integration` 9, `test_routing_lifecycle` 9, `test_route_kernel` 4, `test_last_mile` 3, `test_sequence_solver` 2; `route_bench.py` 3, `last_mile_bench.py` 3 | "medium test churn" |
| `budget: dict[str, int]` parameter declarations | 23 | src 9, tests 8, scripts 6 | not counted |
| `ExpansionBudget` references | 45 | src 5 (`sequence_solver.py:430,1007,5955` plus `expansion_total` at `:4246,5908`), tests 40 (all `test_sequence_solver.py` bar 2 name mentions in `test_freeform.py`) | "absorbed by Plan B" |
| `_ROUTING_BUDGET` consumers | 3 | `freeform.py:4576` (late-bound through the module object), `sequence_solver.py:93` and `hierarchy/compose.py:53` (both `from … import`, bound at import time) | "runtime rewrite" |

**The spec's "runtime rewrite of `_ROUTING_BUDGET`" is not in the tree.** There is exactly one assignment, the definition at `routing_domain.py:402`. `freeform.py:4576` *reads* `routing_domain._ROUTING_BUDGET` through the module object inside `lay_out`, while `sequence_solver` and `hierarchy/compose` bind the value at import. Nothing monkeypatches it, so the inconsistency is latent rather than live; Task 6 removes it by giving all three one factory.

**Out of scope, with reasons.** `global_router._CapacityLedger` (per-cell routing capacity), `sequence_alns._Ledger` (move acceptance), `sequence_solver._RelationNoGoodLedger` (no-good memory), `sequence_solver._DeferredFeedbackBudget` (feedback deferral) and `freeform._BuildBudgetStage` (a build stage enum) are five of the spec's "7 budget/ledger classes" that count things other than charged work against a clock. Merging them would be a behavior change, not a repair. `last_mile.ClusterEnvironment.budget_left: Callable[[], int]` and `budget_floor: int` stay as they are: they are a read-only view the cluster search already has, and Task 5 keeps the lambda that feeds them.

---

### Task 1: One module owns the deadline and the ledger

**Files:**
- Create: `src/flab2bp/layout/budget.py`
- Delete: `src/flab2bp/layout/transport_routing/budget.py`
- Modify: the eleven transport importers — `src/flab2bp/layout/transport_routing/{allocation,cnf,composition,geometry,inventory,paths,repair,routing,runtime,solver}.py` and `tests/layout/test_transport_routing.py`
- Create: `tests/layout/test_budget.py`

**Interfaces:**
- Consumes: `flab2bp.layout.process_resources.usage()`.
- Produces, all in `flab2bp.layout.budget`:
```python
WorkKind = Literal["arcs", "augmentations", "candidates", "audit_cells", "predicates", "assignments"]

class BudgetExhausted(Exception): ...
class TransportRefusal(BudgetExhausted, RuntimeError):
    def __init__(self, reason: str, detail: str) -> None: ...   # .reason, .detail unchanged

@dataclass(frozen=True)
class WorkLimits: ...   # unchanged field names and defaults

def now(clock: Callable[[], float] | None = None) -> float: ...
def expired(deadline: float | None, clock: Callable[[], float] | None = None) -> bool: ...

@dataclass
class WorkBudget:
    deadline: float | None = None
    left: int | None = None
    limits: WorkLimits = field(default_factory=WorkLimits)
    clock: Callable[[], float] | None = None
    counts: dict[WorkKind, int] = field(default_factory=dict)
    def expired(self) -> bool: ...
    def check(self) -> None: ...            # clock + RSS, raises TransportRefusal
    def charge(self, kind: WorkKind, amount: int = 1) -> None: ...
```

**Risk:** the clock default. Today `clock: Callable[[], float] = time.monotonic` captures the function object when the class body executes, so a test that does `monkeypatch.setattr(time, "monotonic", fake)` does **not** change what an already-constructed default sees. Every layout deadline test patches the clock that way (40+ sites, e.g. `tests/layout/test_freeform.py:4836`, `tests/layout/test_sequence_solver.py:5532`). Making `clock` default to `None` and resolving `time.monotonic` at call time is what lets layout adopt this type without those tests silently going green on the real clock. Pinned by `test_a_default_clock_follows_a_patched_time_monotonic`.

- [ ] **Step 1: Cut the branch**

```
GIT_EDITOR=true git switch -c abstraction-repairs-2b 549852e5
ls -l .local-evidence/2026-09-13-session/audit-master-control-9758411d.jsonl
```
The control already exists; do not take a new one. Confirm the file is there before starting, because every corpus gate in this plan compares against it.

- [ ] **Step 2: Write the failing tests**

`tests/layout/test_budget.py`:
```python
"""The one deadline-and-ledger contract, pinned at its edges."""

from __future__ import annotations

import time

import pytest

from flab2bp.layout import budget as work


def test_a_none_deadline_never_expires() -> None:
    assert work.expired(None) is False
    assert work.WorkBudget(deadline=None).expired() is False


def test_the_deadline_comparison_is_greater_or_equal() -> None:
    ticks = [99.0, 100.0, 100.5]
    clock = lambda: ticks.pop(0)  # noqa: E731
    budget = work.WorkBudget(deadline=100.0, clock=clock)
    assert budget.expired() is False
    assert budget.expired() is True
    assert budget.expired() is True


def test_a_default_clock_follows_a_patched_time_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = work.WorkBudget(deadline=100.0)
    monkeypatch.setattr(time, "monotonic", lambda: 99.0)
    assert budget.expired() is False
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    assert budget.expired() is True


def test_expired_never_probes_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> tuple[int, int, int]:
        raise AssertionError("expired() must not touch process_resources")

    monkeypatch.setattr(work.process_resources, "usage", forbidden)
    assert work.WorkBudget(deadline=None).expired() is False
    assert work.expired(None) is False


def test_check_still_refuses_on_the_deadline_and_on_rss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(work.process_resources, "usage", lambda: (0, 0, 0))
    with pytest.raises(work.TransportRefusal) as expired_at:
        work.WorkBudget(deadline=100.0, clock=lambda: 100.0).check()
    assert expired_at.value.reason == "DEADLINE"

    monkeypatch.setattr(work.process_resources, "usage", lambda: (0, 0, 5 * 1024 * 1024))
    with pytest.raises(work.TransportRefusal) as too_big:
        work.WorkBudget(deadline=100.0, clock=lambda: 1.0).check()
    assert too_big.value.reason == "MEMORY_BOUND"


def test_check_with_no_deadline_only_probes_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work.process_resources, "usage", lambda: (0, 0, 0))
    work.WorkBudget(deadline=None, clock=lambda: 1e9).check()


def test_charge_counts_per_kind_and_refuses_past_its_limit() -> None:
    budget = work.WorkBudget(deadline=None, limits=work.WorkLimits(arcs=3))
    budget.charge("arcs", 2)
    assert budget.counts["arcs"] == 2
    with pytest.raises(work.TransportRefusal) as refused:
        budget.charge("arcs", 2)
    assert refused.value.reason == "POLICY_BOUND"
    assert budget.counts["arcs"] == 2


def test_a_negative_charge_is_rejected() -> None:
    with pytest.raises(ValueError, match="work charge must be nonnegative"):
        work.WorkBudget(deadline=None).charge("arcs", -1)


def test_a_transport_refusal_is_a_budget_exhausted_and_a_runtime_error() -> None:
    refusal = work.TransportRefusal("DEADLINE", "detail")
    assert isinstance(refusal, work.BudgetExhausted)
    assert isinstance(refusal, RuntimeError)
    assert str(refusal) == "detail"
```

- [ ] **Step 3: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py; echo "exit=$?"`
Expected: non-zero exit, `ModuleNotFoundError: No module named 'flab2bp.layout.budget'`.

- [ ] **Step 4: Write the module**

`src/flab2bp/layout/budget.py`:
```python
"""One clock and one charged-work ledger for every layout phase.

Before this module the concept had five named predicates, three closures,
eight inline comparisons, three "clock ran out" exception types and an
untyped ``budget["left"]`` dict at 42 sites (see
``docs/superpowers/specs/2026-09-13-abstraction-review.md`` section 5, P1).
Two rules hold everything together:

* a ``None`` deadline never expires, and the comparison is always ``>=``;
* the clock is resolved at the moment it is read, never captured, so a test
  that patches ``time.monotonic`` is seen by a budget built before the patch.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from flab2bp.layout import process_resources

WorkKind = Literal[
    "arcs", "augmentations", "candidates", "audit_cells", "predicates", "assignments"
]


class BudgetExhausted(Exception):
    """A bound -- clock, allowance or memory -- ended a phase.

    The base of every "ran out" signal in layout. Handlers keep catching the
    concrete subclass they always caught: the conversion at
    ``routing_domain._route_all`` (a caught ``_PreparationDeadline`` re-raised
    as the proposals ``Deadline``) depends on the two being distinguishable.
    """


class TransportRefusal(BudgetExhausted, RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class WorkLimits:
    arcs: int = 100_000
    augmentations: int = 100_000
    candidates: int = 2_000_000
    audit_cells: int = 5_000_000
    predicates: int = 100_000_000
    assignments: int = 100_000


def now(clock: Callable[[], float] | None = None) -> float:
    """The current monotonic reading, or an injected clock's."""
    return time.monotonic() if clock is None else clock()


def expired(deadline: float | None, clock: Callable[[], float] | None = None) -> bool:
    """Has ``deadline`` passed? ``None`` never has.

    Callers in a module whose ``time`` attribute a test may replace must pass
    ``clock=time.monotonic`` so the lookup happens in *their* module.
    """
    return deadline is not None and now(clock) >= deadline


@dataclass
class WorkBudget:
    """One phase's clock, its remaining charged work, and its per-kind counts.

    Mutable and shared by reference on purpose: a routing pass hands the same
    object to every net, and a carved child ledger is reconciled against its
    parent by subtracting what the child did not spend.
    """

    deadline: float | None = None
    #: Charged geometric work units still available to this pass. ``None`` is
    #: "unbounded" and is what a caller that passed no budget at all gets.
    left: int | None = None
    limits: WorkLimits = field(default_factory=WorkLimits)
    clock: Callable[[], float] | None = None
    counts: dict[WorkKind, int] = field(default_factory=dict)
    _next_memory_check_s: float = field(default=0.0, init=False)

    def expired(self) -> bool:
        """Clock only. Never probes memory: layout has no RSS bound."""
        return expired(self.deadline, self.clock)

    def check(self) -> None:
        reading = now(self.clock)
        if self.deadline is not None and reading >= self.deadline:
            raise TransportRefusal("DEADLINE", "absolute transport-routing deadline reached")
        if reading >= self._next_memory_check_s:
            self._next_memory_check_s = reading + 0.05
            if process_resources.usage()[2] > 4 * 1024 * 1024:
                raise TransportRefusal("MEMORY_BOUND", "transport-routing exceeded 4 GiB peak RSS")

    def charge(self, kind: WorkKind, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("work charge must be nonnegative")
        self.check()
        current = self.counts.get(kind, 0)
        if current + amount > getattr(self.limits, kind):
            raise TransportRefusal("POLICY_BOUND", f"{kind} work limit reached at {current}")
        self.counts[kind] = current + amount
```

Two differences from the file being replaced, both deliberate: `deadline` is now `float | None` with a `None` default (layout deadlines are optional), and `check()` guards the deadline comparison with `is not None`. Transport always passes a float, so its behavior is unchanged.

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 6: Move the transport importers over and delete the old module**

```
grep -rn "transport_routing.budget\|transport_routing import budget\|from .budget import\|from \.\.budget" src tests scripts --include='*.py'
```
Rewrite each hit to `from flab2bp.layout.budget import …` / `from flab2bp.layout import budget`, then `git rm src/flab2bp/layout/transport_routing/budget.py`. Confirm nothing is left:
```
grep -rn "transport_routing.budget\|transport_routing import budget" src tests scripts docs/superpowers/specs
```
Expected: only the spec's historical mentions, which stay.

- [ ] **Step 7: Lint gate, suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_transport_routing.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 8: Commit**

```bash
GIT_EDITOR=true git add src/flab2bp/layout/budget.py tests/layout/test_budget.py src/flab2bp/layout/transport_routing
GIT_EDITOR=true git commit -m "Give the deadline and work ledger one home in flab2bp.layout.budget"
```
Body: the two contract changes (optional deadline, late-binding clock) with the reason for each.

---

### Task 2: One deadline predicate behind five names

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:559-577` (`_expired`), `src/flab2bp/layout/compact_seed.py:1377` (`_deadline_reached`), `src/flab2bp/layout/finalize.py:4511` (`_completion_expired`), `src/flab2bp/layout/hierarchy/compose.py:605` (`_spent`), `src/flab2bp/layout/routing_proposals.py:142-149` (`Deadline`, `check_deadline`)
- Modify the three closures and eight inline comparisons: `src/flab2bp/pipeline.py:1154`, `src/flab2bp/layout/sequence_solver.py:3446,3497,3503,4859,5150,5538`, `src/flab2bp/layout/route_primitives.py:216,246`, `src/flab2bp/layout/freeform.py:3645,5749`
- Test: `tests/layout/test_budget.py` (add), plus the existing suites in step 5

**Interfaces:**
- Consumes: `flab2bp.layout.budget.expired(deadline, clock=None)` from Task 1.
- Produces: no new names. Every one of the five module-level predicates keeps its current name, module and signature; only its body changes to a delegation. `routing_proposals.Deadline` keeps its name and now subclasses `BudgetExhausted` (Task 3 does the other two).

**Risk:** a delegating predicate that lets `budget.py` look up `time.monotonic` breaks the module-level clock fakes. `tests/layout/hierarchy/test_compose.py:536` does `monkeypatch.setattr(compose, "time", clock)`, replacing compose's `time` attribute with a stand-in object; `_spent` reads `compose.time.monotonic` today. Every delegation in this task therefore passes `clock=time.monotonic` explicitly, resolved in the calling module at call time. Pinned by `test_each_module_predicate_reads_its_own_time_attribute`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_budget.py`:
```python
def test_each_module_predicate_reads_its_own_time_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A module-scoped fake clock must still reach the module's predicate.

    tests/layout/hierarchy/test_compose.py replaces ``compose.time`` wholesale;
    a predicate that resolved ``time.monotonic`` inside flab2bp.layout.budget
    would read the real clock and the test would pass for the wrong reason.
    """
    from types import SimpleNamespace

    from flab2bp.layout import compact_seed, finalize, routing_domain
    from flab2bp.layout.hierarchy import compose

    cases = (
        (routing_domain, routing_domain._expired),
        (compact_seed, compact_seed._deadline_reached),
        (finalize, finalize._completion_expired),
        (compose, compose._spent),
    )
    for module, predicate in cases:
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 50.0))
        assert predicate(100.0) is False, module.__name__
        assert predicate(None) is False, module.__name__
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 100.0))
        assert predicate(100.0) is True, module.__name__


def test_check_deadline_raises_the_proposals_deadline_from_the_shared_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from flab2bp.layout import routing_proposals

    monkeypatch.setattr(routing_proposals, "time", SimpleNamespace(monotonic=lambda: 99.0))
    routing_proposals.check_deadline(100.0)
    routing_proposals.check_deadline(None)
    monkeypatch.setattr(routing_proposals, "time", SimpleNamespace(monotonic=lambda: 100.0))
    with pytest.raises(routing_proposals.Deadline):
        routing_proposals.check_deadline(100.0)
    assert issubclass(routing_proposals.Deadline, work.BudgetExhausted)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py -k "own_time_attribute or shared_rule"; echo "exit=$?"`
Expected: non-zero exit. `test_each_module_predicate_reads_its_own_time_attribute` passes already (the bodies read their own `time`); `test_check_deadline_raises_the_proposals_deadline_from_the_shared_rule` fails on `issubclass(... BudgetExhausted)`. The first test is a regression guard for what the next step must not break; keep it.

- [ ] **Step 3: Delegate the five predicates**

`routing_domain.py` (keep the long docstring at `:560-575` verbatim, replace only the return):
```python
def _expired(deadline: float | None) -> bool:
    """<the existing docstring, unchanged>"""
    return budget_module.expired(deadline, time.monotonic)
```
Import it at the top of routing_domain as `from flab2bp.layout import budget as budget_module` — the plain name `budget` is already a parameter name at `routing_domain.py:4852`, `:6184`, `:12232`, `:12478`, `:12499` and would shadow the module inside those functions.

`compact_seed.py:1377`, `finalize.py:4511`, `hierarchy/compose.py:605`: same shape, each keeping its own name, signature and docstring:
```python
def _deadline_reached(deadline: float | None) -> bool:
    return budget.expired(deadline, time.monotonic)
```
`compose.py` has no bare `budget` binding (only `route_budget` at `:1798` and prose in comments), so it imports the module plainly as `budget`. `compact_seed.py`, `finalize.py`, `routing_proposals.py`, `route_primitives.py` and `pipeline.py` have none either; `sequence_solver.py` binds only `attempt_budget`, `validation_budget` and `deferred_feedback_budget`. Only `routing_domain.py` and `freeform.py` need the `budget_module` alias.

`routing_proposals.py:142-149`:
```python
class Deadline(budget.BudgetExhausted):
    """The proposal search's own clock ran out; not a proof of impossibility."""


def check_deadline(deadline: float | None) -> None:
    if budget.expired(deadline, time.monotonic):
        raise Deadline
```

- [ ] **Step 4: Fold the three closures and the eight inline comparisons**

Each becomes a call to its module's predicate where one exists, and to `budget.expired(deadline, time.monotonic)` where none does. Exact replacements:

```python
# pipeline.py:1154 -- `time` is imported at module scope; keep the default-argument
# binding, it exists so the closure captures the deadline value not the name.
            def attempt_expired(_deadline: float = attempt_deadline) -> bool:
                return budget.expired(_deadline, time.monotonic)

# sequence_solver.py:4859
    def deadline_reached() -> bool:
        return budget.expired(deadline, time.monotonic)

# sequence_solver.py:5150
                def compact_deadline_reached() -> bool:
                    return budget.expired(compact_deadline, time.monotonic)

# sequence_solver.py:3446, :3497, :3503 -- `if deadline is not None and time.monotonic() >= deadline:`
    if budget.expired(deadline, time.monotonic):

# sequence_solver.py:5538 -- `if time.monotonic() >= completion_deadline:`
        if budget.expired(completion_deadline, time.monotonic):

# route_primitives.py:216, :246
        if budget.expired(deadline, time.monotonic):

# freeform.py:3645
    cancelled = None if deadline is None else lambda: budget_module.expired(deadline, time.monotonic)

# freeform.py:5749 -- a SOFT deadline, same rule
                    and budget_module.expired(improvement_soft, time.monotonic)
```
`sequence_solver.py:4859` and `:5538` compare against a non-optional `float` today; `budget.expired` handles `None` too, which is a widening, not a change. `freeform.py` already binds the name `budget` as a parameter at `:3641`, `:3713`, `:4949`, so import the module there as `budget_module` as well.

After the edits:
```
grep -rn "monotonic() >= \|monotonic() > \|perf_counter() >= " src --include='*.py'
```
Expected: two hits only — `src/flab2bp/layout/budget.py` (the rule itself) and nothing else. Any other hit is a missed site.

- [ ] **Step 5: Lint gate, suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_routing_integration.py tests/layout/test_route_primitives.py tests/layout/hierarchy tests/layout/test_compact_seed.py tests/layout/test_finalize.py tests/test_pipeline.py; echo "exit=$?"`
Expected: `exit=0`. If `tests/layout/test_compact_seed.py` does not exist, drop it and say so in the commit message.

- [ ] **Step 6: Corpus gate** (routing-touching). Expected: identical to the control.

- [ ] **Step 7: Commit**

`Check one deadline rule behind the five names that already existed`, body listing each predicate, each closure and each inline site, and the `clock=time.monotonic` reason.

---

### Task 3: One exception base under the three deadline types

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:12513-12522` (`_PreparationDeadline`), `src/flab2bp/layout/hierarchy/compose.py:248-257` (`_PackingDeadline`)
- Test: `tests/layout/test_budget.py` (add)

**Interfaces:**
- Consumes: `flab2bp.layout.budget.BudgetExhausted`.
- Produces: `routing_domain._PreparationDeadline`, `hierarchy.compose._PackingDeadline` and `routing_proposals.Deadline` (Task 2) are all subclasses of `BudgetExhausted`. Their names, payload attributes (`_PreparationDeadline.work`, `.net_index`, `.failures`; `_PackingDeadline.packing`) and constructor signatures are unchanged.

**Risk:** collapsing the three into one class. `routing_domain.py:8161-8162` does `except _PreparationDeadline as error: raise _GeometricDeadline from error` inside a `try` whose outer handler at `:7798` is `except _GeometricDeadline: return None`. If the two were the same class, the conversion would stop distinguishing them and the 12 `except _PreparationDeadline` handlers in `routing_domain` would start catching proposal deadlines they never saw. The task therefore adds a base and changes no dispatch. Pinned by `test_the_three_deadline_types_stay_distinct_under_one_base`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_budget.py`:
```python
def test_the_three_deadline_types_stay_distinct_under_one_base() -> None:
    from flab2bp.layout import routing_domain, routing_proposals
    from flab2bp.layout.hierarchy import compose

    types = (
        routing_proposals.Deadline,
        routing_domain._PreparationDeadline,
        compose._PackingDeadline,
    )
    for kind in types:
        assert issubclass(kind, work.BudgetExhausted), kind.__name__
    # Distinct: the router converts one into another on purpose.
    for kind in types:
        others = [other for other in types if other is not kind]
        assert not any(issubclass(kind, other) for other in others), kind.__name__


def test_a_preparation_deadline_still_carries_its_partial_result() -> None:
    from flab2bp.layout import routing_domain

    stopped = routing_domain._PreparationDeadline()
    assert stopped.work == 0
    assert stopped.net_index is None
    assert stopped.failures == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py -k "stay_distinct or partial_result"; echo "exit=$?"`
Expected: non-zero exit, the `issubclass(..., BudgetExhausted)` assertion failing for `_PreparationDeadline`.

- [ ] **Step 3: Reparent the two classes**

```python
# routing_domain.py:12513
class _PreparationDeadline(budget_module.BudgetExhausted):
    """Exact candidate preparation stopped before producing a reusable result."""

    def __init__(self) -> None:
        super().__init__()
        # A routing owner retains work and real refusals completed before the
        # interrupted query, without interpreting that query as a failed one.
        self.work = 0
        self.net_index: int | None = None
        self.failures: dict[int, NetFailure] = {}


# hierarchy/compose.py:248  (compose imports the module plainly as `budget`)
class _PackingDeadline(budget.BudgetExhausted):
    """<the existing four-line docstring, unchanged>"""

    def __init__(self, packing: _Packing) -> None:
        super().__init__("no gap could be judged before the deadline")
        self.packing = packing
```
Change nothing else. In particular do not touch the 155 `raise _PreparationDeadline` sites, the 12 `raise _PackingDeadline(packing)` sites, or any `except` clause.

- [ ] **Step 4: Prove no handler widened**

```
grep -rn "except .*BudgetExhausted\|except Exception\b" src/flab2bp/layout --include='*.py'
```
Every `except Exception` hit must be inspected: a bare `except Exception` that previously did not see a deadline still does not, because the three types already derived from `Exception`. Record in the commit message that the base is new and therefore caught nowhere. If a handler catches `RuntimeError` in a path that can now see a `TransportRefusal` it could not before, stop and report — `TransportRefusal` was already a `RuntimeError`, so this should find nothing.

- [ ] **Step 5: Lint gate, suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/hierarchy tests/layout/test_transport_routing.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 6: Corpus gate** (routing-touching). Expected: identical to the control.

- [ ] **Step 7: Commit**

`Put the three deadline exceptions under one BudgetExhausted base`, body quoting `routing_domain.py:8161-8162` as the reason they stay distinct.

---

### Task 4: The search leaf takes a WorkBudget

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:4845-5010` (`_geometric_search` signature, `:4892`, `:4981`, `:4999`)
- Test: `tests/layout/test_route_kernel.py`, `tests/layout/test_technology_routing_search.py`, `tests/layout/test_budget.py` (add)

**Interfaces:**
- Consumes: `flab2bp.layout.budget.WorkBudget` with `left: int | None`.
- Produces: `_geometric_search(..., budget: WorkBudget | None = None, deadline: float | None = None, ...) -> _PathSearchResult`. Parameter position 7 and its `None` default are unchanged, so the ~50 positional call sites that pass no budget keep working untouched. `deadline` stays its own parameter.

**Risk:** a budget re-read after the rewrite. `routing_domain.py:4981` reads `start_left = budget["left"]`, the kernel runs, and `:4999` *sets* `budget["left"] = start_left - work` rather than decrementing it. Anything that mutated the same object during the kernel call would be silently discarded, and swapping the set for a `-=` would change the value when `max_work` was clamped below `start_left`. The replacement keeps set-not-decrement and keeps the read before the call. Pinned by `test_the_search_leaf_sets_left_from_the_value_it_read`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_budget.py`:
```python
from array import array  # noqa: F401  (already imported at the top of the file if present)
from dataclasses import replace

from flab2bp.lab.techs import belt_rules_for_url

_BELT_RULES = belt_rules_for_url(
    "https://factoriolab.github.io/dsp/list?o=iron-ingot*60&v=11"
)


def _tiny_open_canvas() -> tuple[object, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int, int]]:
    """A three-tile corridor with one free ground path from start to goal."""
    from flab2bp.layout import routing_domain

    bounds = (0, 0, 2, 0)
    canvas = routing_domain._Canvas(
        limit=bounds, belt_rules=replace(_BELT_RULES, vertical_construction=False)
    )
    start, goal = (0, 0, 0), (2, 0, 0)
    free = {start, (1, 0, 0), goal}
    canvas.guard.update(
        (x, 0, level)
        for x in range(3)
        for level in range(canvas.levels)
        if (x, 0, level) not in free
    )
    return canvas, start, goal, bounds


def test_the_search_leaf_sets_left_from_the_value_it_read() -> None:
    """`left` after a query is the value read before it, minus the charge.

    Not a decrement: if the kernel is clamped to `_MAX_SEARCH_WORK` below the
    ledger, the two differ, and the committed refusal evidence matches the set.
    """
    from flab2bp.layout import routing_domain
    from flab2bp.layout.budget import WorkBudget

    canvas, start, goal, bounds = _tiny_open_canvas()
    budget = WorkBudget(left=20_000)
    result = routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds, budget)
    assert result.path is not None
    assert budget.left == 20_000 - result.work
    assert isinstance(budget.left, int)


def test_an_empty_ledger_refuses_before_the_kernel_runs() -> None:
    from flab2bp.layout import route_feedback, routing_domain
    from flab2bp.layout.budget import WorkBudget

    canvas, start, goal, bounds = _tiny_open_canvas()
    budget = WorkBudget(left=0)
    result = routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds, budget)
    assert result.path is None
    assert result.kind is route_feedback.RouteFailureKind.BUDGET
    assert budget.left == 0


def test_no_budget_still_means_unbounded() -> None:
    from flab2bp.layout import routing_domain

    canvas, start, goal, bounds = _tiny_open_canvas()
    assert routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds).path
```
The three-tile canvas above is the same construction `tests/layout/test_route_kernel.py:20-31` uses, with the free corridor laid flat at ground level so the query always routes. Annotate `_tiny_open_canvas`'s first return element as `routing_domain._Canvas` rather than `object` if mypy accepts the import at module scope; `object` is there only because the import is function-local.

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py -k "search_leaf or empty_ledger or unbounded"; echo "exit=$?"`
Expected: non-zero exit, `TypeError: 'WorkBudget' object is not subscriptable`.

- [ ] **Step 3: Change the three sites**

```python
# routing_domain.py:4852 (signature)
    budget: budget_module.WorkBudget | None = None,

# routing_domain.py:4892
    if budget is not None and budget.left is not None and budget.left <= 0:

# routing_domain.py:4981
    start_left = budget.left if budget is not None and budget.left is not None else 1 << 62

# routing_domain.py:4999
    if budget is not None and budget.left is not None:
        budget.left = start_left - work
```
The `budget.left is not None` guards are what make `left: int | None` safe; `left=None` now means exactly what `budget=None` meant, so a caller can pass a budget for its deadline alone without the ledger biting. Update the docstring paragraph at `:4863-4872` that describes the charge to name `WorkBudget.left` instead of `budget["left"]`.

- [ ] **Step 4: Migrate the leaf's direct test and script callers**

Only the sites that pass a budget dict to `_geometric_search` change, and only to `WorkBudget(left=n)`:
```
grep -rn '_geometric_search' tests scripts --include='*.py' -A 12 | grep -n '{"left"'
```
Expected hits: `tests/layout/test_route_kernel.py:191,230,240,253`; `tests/layout/test_technology_routing_search.py:98,233,275,296,325,338,340,355,372,386,400,426,437`; `tests/layout/test_freeform.py:11985,14893`; `tests/layout/test_last_mile.py:408`; `scripts/last_mile_bench.py:51,110`; `scripts/route_bench.py:293`. Replace each `{"left": n}` with `WorkBudget(left=n)` and each `ledger["left"]` read with `ledger.left`. `tests/layout/test_technology_routing_search.py:324` asserts `ordinary.work == routing_domain._MAX_SEARCH_WORK`; leave it.

- [ ] **Step 5: Tests pass; lint gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_route_kernel.py tests/layout/test_technology_routing_search.py tests/layout/test_route_primitives.py tests/layout/test_last_mile.py tests/layout/test_freeform.py; echo "exit=$?"`
Expected: `exit=0`. `test_freeform.py` and `test_last_mile.py` are in scope because step 4 edited their leaf callers. Check `vmstat` first and record the figure.

- [ ] **Step 6: Corpus gate** (routing-touching). Expected: identical to the control.

- [ ] **Step 7: Commit**

`Hand the geometric search a typed WorkBudget instead of a left dict`.

---

### Task 5: The routing pass takes a WorkBudget

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` at the 31 remaining `["left"]` sites and the five sub-ledger constructions: `:6184` (signature), `:6269` (seed), `:6312`, `:7814` (signature), `:7838`, `:7840`, `:7876`, `:7894`, `:7926`, `:8132` (signature), `:8196`, `:8401`, `:8468`, `:8470`, `:8497`, `:8519`, `:8597`, `:8870`, `:8872`, `:8879`, `:8885`, `:8936`, `:9008`, `:9016`, `:9069`, `:9178`, `:9235`, `:9242`, `:9311`, `:9436`, `:9437`, `:9451`, `:9615`, `:9634`, `:9831`, `:9839`, `:12232` (signature), `:12264` (seed), `:12478` (signature), `:12499` (signature)
- Modify (seeds only, so nothing feeds the new signature a dict): `src/flab2bp/layout/freeform.py:4575-4577`, `src/flab2bp/layout/hierarchy/compose.py:1787`, `src/flab2bp/layout/sequence_solver.py:4798`, `:4811`, `:4817`
- Test: `tests/layout/test_routing_integration.py`, `tests/layout/test_route_witnesses.py`, `tests/layout/test_routing_lifecycle.py`, `tests/layout/test_last_mile.py`, `tests/layout/test_freeform.py`, `tests/layout/test_sequence_solver.py`, `tests/layout/test_budget.py` (add)

**Interfaces:**
- Consumes: `WorkBudget` from Task 1; `_geometric_search(budget: WorkBudget | None)` from Task 4.
- Produces: `_route_all(..., budget: WorkBudget | None = None, ...)`, `_route_boundary_nets(..., budget: WorkBudget | None = None)` and the two `_search_route` inner signatures at `:7814` and `:8132` taking `search_budget: WorkBudget`. `last_mile.ClusterEnvironment.budget_left: Callable[[], int]` and `.budget_floor: int` are unchanged; the lambda at `:8885` becomes `lambda: budget.left or 0`.

**Risk:** the aliasing at `:9437`. `net_budget = {"left": allowance} if coverage_pass else budget` hands the *caller's own object* through on the non-coverage branch, and `:9451` then reconciles `budget["left"] -= allowance - net_budget["left"]` — which on that branch would double-count if it ran, and does not, because the `-=` sits inside the coverage branch. Read `routing_domain.py:9430-9460` in full with Serena `find_symbol` before touching it and preserve the branch structure exactly; a `WorkBudget` copy instead of an alias here silently drops every non-coverage net's charge. Pinned by `test_a_non_coverage_net_charges_the_pass_ledger_directly`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_budget.py`:
```python
def test_a_carved_child_ledger_returns_its_unspent_work_to_the_parent() -> None:
    """The three carve sites all reconcile parent -= allowance - child.left."""
    from flab2bp.layout.budget import WorkBudget

    parent = WorkBudget(left=1_000)
    allowance = 400
    child = WorkBudget(left=allowance, deadline=parent.deadline, clock=parent.clock)
    child.left = 150  # the child spent 250
    parent.left -= allowance - child.left
    assert parent.left == 750


def test_a_non_coverage_net_charges_the_pass_ledger_directly() -> None:
    """`net_budget` is the pass ledger itself when the pass is not a coverage pass."""
    from flab2bp.layout.budget import WorkBudget

    budget = WorkBudget(left=1_000)
    coverage_pass = False
    net_budget = WorkBudget(left=500) if coverage_pass else budget
    assert net_budget is budget


```
and, in `tests/layout/test_routing_integration.py`, bind the ledger of the existing test at `:469` instead of passing it inline, so a real pass is asserted against a fixture that already exists. Read `tests/layout/test_routing_integration.py:440-476` first, then change those two lines to:
```python
    refusal_budget = WorkBudget(left=100_000)
    refused = domain._route_all(canvas, nets, 2003, 37, bounds, budget=refusal_budget)
    assert refused.status is not DetailedRouteStatus.ROUTED
    assert tuple(canvas.buildings) == before
    assert 0 <= refusal_budget.left < 100_000, "a refused pass still charges its ledger"

    canvas.guard.remove((100, 80, 0))
    accept_budget = WorkBudget(left=100_000)
    accepted = domain._route_all(canvas, nets, 2003, 37, bounds, budget=accept_budget)
    assert accepted.status is DetailedRouteStatus.ROUTED
    assert 0 <= accept_budget.left < 100_000
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py -k "carved_child or non_coverage" tests/layout/test_routing_integration.py -k "not slow"; echo "exit=$?"`
Expected: non-zero exit from the edited `test_routing_integration.py` case with `TypeError: 'WorkBudget' object is not subscriptable`, raised inside `_route_all` where it still subscripts. The two `test_budget.py` cases pass immediately; they are the shape guards the rewrite must not violate, not red-first tests.

- [ ] **Step 3: Rewrite the subscripts**

Mechanically, `X["left"]` becomes `X.left` at all 31 sites. Only five sites need more than that; do each by hand after reading its enclosing function with Serena `find_symbol(..., include_body=True)`:

```python
# :7876  private = {"left": ordinary_remaining}
        private = budget_module.WorkBudget(left=ordinary_remaining)
# :7894  unchanged in shape
            search_budget.left -= ordinary_remaining - private.left

# :8468  logical = {"left": min(remaining, budget["left"])}
                logical = budget_module.WorkBudget(left=min(remaining, budget.left))
# :8497
                        budget.left -= before - logical.left

# :8872  private = {"left": allowance}
        private = budget_module.WorkBudget(left=allowance)
# :8879
            budget.left -= allowance - private.left

# :8885  ClusterEnvironment.budget_left is Callable[[], int]
            budget_left=lambda: budget.left or 0,
# :8936
                budget_left=budget.left or 0,

# :9437  the alias, structure preserved exactly
                net_budget = budget_module.WorkBudget(left=allowance) if coverage_pass else budget
```
`budget.left or 0` is correct here and not a `None` bug: these two sites sit inside `_route_all`, which is always entered with a ledger (Task 6 makes that a construction-time guarantee), and `0` is the same refusal `left=0` already produces.

`budget.left` is `int | None` to mypy. Inside `_route_all` the ledger is never `None`, so add one narrowing assertion at the top of `_route_all`'s body rather than 31 guards:
```python
    assert budget is not None and budget.left is not None, "a routing pass needs a ledger"
```
Place it immediately after the `budget = {"left": _ROUTING_BUDGET}` default at `:6269` (Task 6 replaces that line). Record in the commit message that this assertion is the only new runtime check and that it cannot fire: `:6266-6269` already defaults the argument.

- [ ] **Step 4: Convert the four seeds so nothing feeds the new signature a dict**

The signatures changed in step 3, so every caller that constructs a budget must construct the type in the same commit or the tree stops importing cleanly under mypy and the routing tests fail. Convert all four mechanically here; Task 6 routes them through one factory afterwards.
```python
# routing_domain.py:6269 and :12264
        budget = budget_module.WorkBudget(left=_ROUTING_BUDGET)

# freeform.py:4575-4577
        budget = budget_module.WorkBudget(
            left=max(routing_domain._ROUTING_BUDGET, int(_ROUTING_WORK_PER_SECOND * ceiling))
        )

# hierarchy/compose.py:1787
    route_budget = budget.WorkBudget(left=_ROUTING_BUDGET)

# sequence_solver.py:4798, and its two reads at :4811 and :4817
    attempt_budget = budget.WorkBudget(left=allowance)
        work = allowance - attempt_budget.left
    spent = allowance - attempt_budget.left
```
Keep the arithmetic byte-identical: `freeform`'s `max(...)` expression is copied across unchanged, deadline still absent, and `_ROUTING_BUDGET` still read through the module object there.

- [ ] **Step 5: Migrate the test callers of `_route_all` and friends**

```
grep -rn '{"left"' tests --include='*.py'
```
Replace each with `WorkBudget(left=n)`, importing `from flab2bp.layout.budget import WorkBudget` at the top of each file. Sites: `tests/layout/test_routing_lifecycle.py` 9, `tests/layout/test_route_witnesses.py` 12, `tests/layout/test_routing_integration.py` 9, `tests/layout/test_freeform.py` 27, `tests/layout/test_last_mile.py` 3, `tests/layout/test_sequence_solver.py` 2 (the two `budget: dict[str, int]` parameter annotations at `:3348` and `:3391` become `budget: WorkBudget`). Also fix the eight `budget: dict[str, int]` annotations in tests and the six in scripts listed in the inventory table.

After the rewrite:
```
grep -rn '\["left"\]\|{"left"' src scripts tests --include='*.py'
```
Expected: one hit, the comment at `src/flab2bp/layout/freeform.py:273`, which Task 6 rewrites. Anything else is a site this task missed.

- [ ] **Step 6: Grep for what Serena's rename misses**

This task renames no symbol, but the same class of miss applies to the hand edits:
```
grep -rn '\.get("left"\|"left" in \|budget\.copy()\|dict(budget\|replace(.*left=' src scripts tests --include='*.py'
```
Expected: zero hits. A `dataclasses.replace` on a `WorkBudget` anywhere is a defect: it copies, and every carve site depends on aliasing.

- [ ] **Step 7: Lint gate, suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py tests/layout/test_last_mile.py tests/layout/test_technology_routing_search.py tests/layout/test_route_kernel.py tests/layout/test_freeform.py tests/layout/test_sequence_solver.py tests/layout/hierarchy; echo "exit=$?"`
Expected: `exit=0`. Step 4 touched `freeform`, `compose` and `sequence_solver`, so their suites are in scope here. Check `vmstat` first and record the figure.

- [ ] **Step 8: Corpus gate** (routing-touching). Expected: identical to the control. Also run `taskset -c 96-127 env PYTHONHASHSEED=0 ./.venv/bin/python3.14 scripts/route_bench.py` and record its MATCH line: it replays a committed A\* capture and is the byte-identity witness for the search itself. Remember that a MATCH is router-only evidence and does not speak for sequence-pair or ALNS; the corpus compare is what covers those.

- [ ] **Step 9: Commit** (two commits): `Carry the routing pass ledger as a WorkBudget` (src, steps 3 and 4), then `Build routing test budgets as WorkBudget objects` (tests and scripts, step 5).

---

### Task 6: One factory seeds every routing budget

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:397-402` (`_MAX_SEARCH_WORK`, `_ROUTING_BUDGET`), `:6266-6269`, `:12264`; `src/flab2bp/layout/freeform.py:261`, `:273`, `:282`, `:4567-4577`; `src/flab2bp/layout/sequence_solver.py:93`, `:4798`, `:4811`, `:4817`, `:5908-5913`; `src/flab2bp/layout/hierarchy/compose.py:53`, `:1787`
- Test: `tests/layout/test_budget.py` (add), `tests/layout/test_freeform.py`, `tests/layout/test_sequence_solver.py`

**Interfaces:**
- Consumes: `WorkBudget` from Task 1.
- Produces:
```python
# routing_domain.py
def _routing_pass_budget(
    deadline: float | None = None, seconds: float | None = None
) -> budget_module.WorkBudget:
    """One routing pass's ledger: the floor, or work-per-second over ``seconds``."""
```
`_ROUTING_BUDGET` and `freeform._ROUTING_WORK_PER_SECOND` keep their names and values as the two constants the factory reads; `_MAX_SEARCH_WORK` is untouched and stays monkeypatchable.

**Risk:** the three consumers disagree today. `freeform.py:4576` reads `routing_domain._ROUTING_BUDGET` through the module object at call time, while `sequence_solver.py:93` and `hierarchy/compose.py:53` bound the value at import. Nothing patches the constant, so the difference is latent — but a factory that some callers import by value reintroduces it. Every caller must call `routing_domain._routing_pass_budget(...)`, never import the constant. Pinned by `test_every_routing_budget_seed_goes_through_one_factory`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_budget.py`:
```python
def test_the_factory_floor_is_the_routing_budget_constant() -> None:
    from flab2bp.layout import routing_domain

    seeded = routing_domain._routing_pass_budget()
    assert seeded.left == routing_domain._ROUTING_BUDGET
    assert seeded.deadline is None


def test_the_factory_scales_with_seconds_but_never_below_the_floor() -> None:
    from flab2bp.layout import freeform, routing_domain

    generous = routing_domain._routing_pass_budget(deadline=1234.0, seconds=20.0)
    assert generous.left == int(freeform._ROUTING_WORK_PER_SECOND * 20.0)
    assert generous.deadline == 1234.0

    stingy = routing_domain._routing_pass_budget(deadline=None, seconds=0.5)
    assert stingy.left == routing_domain._ROUTING_BUDGET


def test_every_routing_budget_seed_goes_through_one_factory() -> None:
    """No module may bind the floor constant by value: they would drift."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "flab2bp"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "routing_domain.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "_ROUTING_BUDGET" for alias in node.names
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "import _routing_pass_budget instead of the floor constant: " + ", ".join(offenders)
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py -k "factory"; echo "exit=$?"`
Expected: non-zero exit, `AttributeError: module 'flab2bp.layout.routing_domain' has no attribute '_routing_pass_budget'`, and the third test listing `sequence_solver.py:93` and `compose.py:53`.

- [ ] **Step 3: Write the factory**

In `routing_domain.py`, immediately after `_ROUTING_BUDGET` at `:402`:
```python
def _routing_pass_budget(
    deadline: float | None = None, seconds: float | None = None
) -> budget_module.WorkBudget:
    """One routing pass's clock and ledger.

    ``_ROUTING_BUDGET`` is the floor: it bounds one pass deterministically so a
    re-run reproduces. When the caller knows how many seconds the pass may have,
    the ledger scales with ``freeform._ROUTING_WORK_PER_SECOND`` so that it stays
    a backstop rather than becoming the thing that ends the sweep.
    """
    from flab2bp.layout.freeform import _ROUTING_WORK_PER_SECOND

    left = _ROUTING_BUDGET
    if seconds is not None:
        left = max(left, int(_ROUTING_WORK_PER_SECOND * seconds))
    return budget_module.WorkBudget(deadline=deadline, left=left)
```
The import is function-local because `freeform` imports `routing_domain` at module scope; a top-level import here is a cycle. Confirm with `./.venv/bin/python3.14 -c "import flab2bp.layout.routing_domain"` before committing.

- [ ] **Step 4: Point the four seeding sites at it**

Task 5 left these as `WorkBudget(left=…)` constructions. Replace the three that read a constant with the factory, and give `freeform`'s the deadline it already has in scope:
```python
# routing_domain.py:6269 and :12264
        budget = _routing_pass_budget()

# freeform.py:4575-4577
        budget = routing_domain._routing_pass_budget(deadline=deadline, seconds=ceiling)

# hierarchy/compose.py:1787
    route_budget = routing_domain._routing_pass_budget()
```
`sequence_solver.py:4798`'s `WorkBudget(left=allowance)` stays as it is: it carves an attempt-sized ledger from a caller's allowance, not a pass floor.

Setting `deadline` on freeform's budget is inert and stays inert: `routing_domain` never calls `WorkBudget.expired()`, it calls `_expired(deadline)` against the explicit parameter, exactly as before. The field is carried so a later plan can retire the parallel argument; adding a `budget.expired()` call anywhere in `routing_domain` is out of scope here and would reintroduce the rebound-nonlocal hazard from the Global Constraints.

Then delete `_ROUTING_BUDGET` from the `from … import` lists at `sequence_solver.py:93` and `hierarchy/compose.py:53`, and from `sequence_solver.py:5909`'s argument list if it is passed there — read `sequence_solver.py:5900-5920` first; that block computes `expansion_total = max(_ROUTING_BUDGET, int(_ROUTING_WORK_PER_SECOND * ceiling))`, which is the factory's arithmetic spelled out. Replace it with `expansion_total = routing_domain._routing_pass_budget(seconds=ceiling).left`, keeping the name `expansion_total` (Task 8 renames it).

Rewrite the three comments that describe the old shape: `freeform.py:261` ("One shared `_ROUTING_BUDGET` was tried…"), `freeform.py:273` (the `budget["left"] // nets_remaining` arithmetic → `budget.left // nets_remaining`), and `routing_domain.py:6266-6268`.

- [ ] **Step 5: Tests pass; lint gate; suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_freeform.py tests/layout/test_sequence_solver.py tests/layout/hierarchy; echo "exit=$?"`
Expected: `exit=0`. This is the heaviest run in the plan (`test_freeform.py` is 25,510 lines and `test_sequence_solver.py` 11,458); check `vmstat` first and record the figure.

- [ ] **Step 6: Corpus gate** (routing-touching). Expected: identical to the control.

- [ ] **Step 7: Commit**

`Seed every routing pass ledger through one factory`.

---

### Task 7: The staged ledger joins the module

**Files:**
- Modify: `src/flab2bp/layout/sequence_solver.py:430-576` (move `ExpansionBudget`, `_fraction_ceiling`, `_check_spend` out), `:1007`, `:1061`, `:4246-4255`, `:5908-5913`, `:5955`, `:5962`, `:6031`, `:6175`, `:6728`
- Modify: `src/flab2bp/layout/budget.py` (receive the class)
- Test: `tests/layout/test_sequence_solver.py` (40 references), `tests/layout/test_budget.py` (add)

**Interfaces:**
- Consumes: `flab2bp.layout.budget.WorkBudget`.
- Produces: `flab2bp.layout.budget.StagedWorkBudget` — `ExpansionBudget` moved and renamed, with every method name, field name and semantic unchanged: `total`, `spent`, `discovery_by_height`, `shared_left`, `final_reserved`, `final_left`, `discovery_complete`, `configure`, `discovery_allowance`, `charge_discovery`, `detailed_discovery_allowance`, `charge_detailed_discovery`, `settle_detailed_discovery`, `settle_discovery`, `shared_allowance`, `settle_shared`. `SequenceSolver.__init__`'s keyword becomes `work_budget`; `sequence_solver._work_total(...)` replaces the local name `expansion_total`.

**Risk:** the four `ValueError` texts. `_check_spend` and `__post_init__` raise `"adapter expansion spend must be within its allowance"`, `"total expansion budget must be a non-negative integer"`, `"expansion budget is already configured for other heights"`, `"shared expansion budget is locked until discovery completes"`, and `sequence_solver.py:4252` raises `"expansion total must be a positive integer"`. A grep of `tests` and `scripts` for all five finds **no** `pytest.raises(match=…)` against them, so they may be reworded to say "work"; verify that grep returns nothing before rewording, and if it returns anything, leave the text alone and say so. The stats key `"expansion_allowance"` at `:6728` does **not** change either way.

- [ ] **Step 1: Confirm no test pins the messages**

```
grep -rn "expansion budget must\|already configured for other heights\|shared expansion budget is locked\|adapter expansion spend\|expansion total must be" tests scripts docs/superpowers
```
Expected: zero hits. Record the result in the commit message. If it is non-empty, keep every message byte-identical and skip the rewording in step 4.

- [ ] **Step 2: Write the failing test**

Append to `tests/layout/test_budget.py`:
```python
def test_the_staged_ledger_partitions_and_returns_unspent_discovery() -> None:
    from fractions import Fraction

    from flab2bp.layout.budget import StagedWorkBudget

    ledger = StagedWorkBudget(total=100)
    assert ledger.final_reserved == 25
    assert ledger.shared_left == 75

    ledger.configure((3, 4), Fraction(1, 5))
    assert ledger.final_reserved == 20
    assert ledger.discovery_by_height == {3: 40, 4: 40}
    assert ledger.shared_left == 0

    ledger.charge_discovery(3, 10)
    assert ledger.discovery_allowance(3) == 30
    assert ledger.spent == 10

    ledger.settle_discovery(3, 5)
    ledger.settle_discovery(4, 0)
    assert ledger.discovery_complete is True
    assert ledger.shared_allowance() == 25 + 40
    assert ledger.spent == 15


def test_the_staged_ledger_rejects_a_negative_total() -> None:
    from flab2bp.layout.budget import StagedWorkBudget

    with pytest.raises(ValueError):
        StagedWorkBudget(total=-1)
```

- [ ] **Step 3: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py -k "staged_ledger"; echo "exit=$?"`
Expected: non-zero exit, `ImportError: cannot import name 'StagedWorkBudget'`.

- [ ] **Step 4: Move the class**

Cut `ExpansionBudget` (`sequence_solver.py:429-566`, including its `@dataclass` decorator line), `_fraction_ceiling` (`:568-570`) and `_check_spend` (`:573-575`) into `src/flab2bp/layout/budget.py` below `WorkBudget`. Rename the class to `StagedWorkBudget` with Serena `rename_symbol(name_path="ExpansionBudget", relative_path="src/flab2bp/layout/sequence_solver.py", new_name="StagedWorkBudget")` **before** moving it, so the 45 references follow; then move the body. Reword the docstrings and the five `ValueError` texts from "expansion" to "work" only if step 1 came back empty, e.g. `"total work budget must be a non-negative integer"`.

In `sequence_solver.py`, add `from flab2bp.layout.budget import StagedWorkBudget` and rename the parameter `expansion_budget` → `work_budget` (`:1007`, `:5955` and the 20 test call sites) and the local `expansion_total` → `work_total` (`:4246`, `:4251`, `:4255`, `:5908`, `:5913`, `:5962`, `:6031`, `:6175`, and `:10124`'s test). `self.budget` at `:1061` keeps its name.

- [ ] **Step 5: Grep for what the rename missed**

Serena `rename_symbol` does not follow `dataclasses.replace(..., f=…)` keyword arguments or untyped attribute reads. After each rename:
```
grep -rn "\.ExpansionBudget\b\|ExpansionBudget=\|expansion_budget\|expansion_total" src scripts tests
```
Expected: zero hits except the two test *function names* `test_boundary_goal_search_reaches_exit_without_exhausting_expansion_budget` (`tests/layout/test_freeform.py:11978`) and `test_an_exhausted_expansion_budget_never_reaches_the_cluster_search` (`:20926`), plus `test_expansion_budget_exhaustion_keeps_its_own_refusal_under_continuation` (`tests/layout/test_sequence_solver.py:10731`). Rename those three to say "work budget" in the same commit.

- [ ] **Step 6: Tests pass; lint gate; suite gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_budget.py tests/layout/test_sequence_solver.py tests/layout/test_freeform.py; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first.

- [ ] **Step 7: Corpus gate** (routing-touching by extent: the sequence-pair strategy routes). Expected: identical to the control.

- [ ] **Step 8: Commit** (two commits): `Move the staged work ledger into flab2bp.layout.budget`, then `Call the staged ledger's charge work, not expansions`.

---

### Task 8: Delete the old shapes, with the evidence

**Files:**
- Modify: `src/flab2bp/layout/budget.py` (docstring), `docs/superpowers/specs/2026-09-13-abstraction-review.md` (status note)
- Create: `tests/test_one_deadline_rule.py`

**Interfaces:**
- Consumes: everything Tasks 1 to 7 produced.
- Produces: a guard test that fails when a new deadline one-liner or a new `{"left": …}` budget dict appears in `src/flab2bp`.

**Risk:** deleting a name a string references. `src/flab2bp/dsp/registry.py:1097-1266` holds a `LintException` table that names layout symbols as strings, including `"_geometric_search"` at `:1217` and `"_route_all"`; a rename in layout silently invalidates it and nothing fails. This plan renames neither, but the reference search must cover that file before any deletion.

- [ ] **Step 1: Prove the old shapes are gone**

```
grep -rn '\["left"\]\|{"left"' src scripts tests --include='*.py'
grep -rn "monotonic() >= \|monotonic() > \|perf_counter() >= " src --include='*.py'
grep -rn "transport_routing.budget\|ExpansionBudget" src scripts tests --include='*.py'
grep -rn "_ROUTING_BUDGET" src --include='*.py'
```
Expected: the second returns only `src/flab2bp/layout/budget.py`; the others return nothing, except `_ROUTING_BUDGET`'s definition and its two uses inside `routing_domain.py`. Paste the four outputs into the commit message. Any surviving hit is a missed site: fix it here, not in a follow-up.

- [ ] **Step 2: Write the guard**

`tests/test_one_deadline_rule.py`:
```python
"""One module owns the deadline and the charged-work ledger.

The 2026-09-13 abstraction review (section 5, P1) found the concept spread
over five named predicates, three closures, eight inline comparisons, three
exception types and an untyped ``budget["left"]`` dict at 42 sites. Plan B
collapsed them into ``flab2bp.layout.budget``. This keeps it collapsed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "flab2bp"
OWNER = SRC / "layout" / "budget.py"


def _is_clock_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"monotonic", "perf_counter"}
    )


def _deadline_comparisons(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{path.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.GtE, ast.Gt)) for op in node.ops)
        and _is_clock_call(node.left)
    ]


def _left_dicts(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "left" for key in node.keys
        ):
            found.append(f"{path.name}:{node.lineno}")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "left"
        ):
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_only_the_budget_module_compares_a_clock_to_a_deadline() -> None:
    offenders = [
        site
        for path in sorted(SRC.rglob("*.py"))
        if path != OWNER
        for site in _deadline_comparisons(path)
    ]
    assert offenders == [], (
        "call flab2bp.layout.budget.expired(deadline, time.monotonic) instead: "
        + ", ".join(offenders)
    )


def test_no_module_carries_a_work_ledger_as_a_left_dict() -> None:
    offenders = [site for path in sorted(SRC.rglob("*.py")) for site in _left_dicts(path)]
    assert offenders == [], (
        "carry a flab2bp.layout.budget.WorkBudget instead: " + ", ".join(offenders)
    )
```

- [ ] **Step 3: Run it**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/test_one_deadline_rule.py; echo "exit=$?"`
Expected: `exit=0`. If `test_only_the_budget_module_compares_a_clock_to_a_deadline` reports a site, it is either a deadline comparison Task 2 missed (fix it) or a genuine elapsed-time measurement of the form `time.monotonic() - started > limit`, which the `node.left` check should already exclude because its left operand is a `BinOp`. If a real exclusion is needed, add the file and line to an explicit allowlist constant in the test with a one-line reason, and keep the allowlist short.

- [ ] **Step 4: Record the outcome in the spec**

Append to `docs/superpowers/specs/2026-09-13-abstraction-review.md` a short "Plan B outcome" note under section 9 item 1: the verified counts from this plan's inventory table, the two places the spec was wrong (five named predicates not four, eight inline comparisons not 33, and no runtime rewrite of `_ROUTING_BUDGET`), and the one deliberate deviation (three exception subclasses under one base, with the `routing_domain.py:8161-8162` reason).

- [ ] **Step 5: Lint gate, full suite, corpus gate**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first and record it. Then run the corpus gate one final time. Expected: identical to the control.

- [ ] **Step 6: Commit**

`Keep the deadline and the work ledger in one module`, body carrying the four grep outputs from step 1.

---

## Self-review notes

**Spec coverage.** Section 5 P1's list is covered item for item: the four (verified five) deadline helpers and three closures in Task 2; the three exception types in Task 3; the `budget` dict at every site in Tasks 4, 5 and 6; the `_ROUTING_BUDGET` seeding in Task 6; `ExpansionBudget` in Task 7; the durable guard in Task 8. Section 3 R2's inline comparisons are in Task 2. Section 9 item 1's "makes every deadline injectable in tests" is delivered by `WorkBudget.clock` plus the late-binding default that Task 1 pins.

**Deliberate departures from the spec, each with its reason in the Global Constraints.** (1) The three exception types become subclasses of one base rather than one class, because `routing_domain.py:8161-8162` converts one into another on purpose. (2) The deadline is not folded into the budget object inside `routing_domain`, because `_route_all` rebinds its `deadline` nonlocal at `:8164` and derives per-query deadlines; folding a rebound name into a shared object changes which clock a later check reads. (3) `_geometric_search` does not shrink to fewer parameters here; the spec predicted it would shrink "once P1 and P4 land", and P4 (the canvas/grid representations) is not in this plan.

**Could not confirm from the spec.** The spec's "the runtime rewrite of `_ROUTING_BUDGET`" describes something that is not in the tree at `549852e5`: there is one assignment, the definition. What exists is a cross-module late-bound *read* in `freeform` against two import-time bindings elsewhere. Task 6 removes that inconsistency, so the spec's intent is served, but the plan does not delete a rewrite that was never there. The spec's "33 inline comparisons" and "35 dict sites" are also off: 8 and 42 respectively, counted in the inventory table.

**Type consistency.** `WorkBudget.left` is `int | None` throughout (Task 1 defines it, Task 4 guards it at the leaf, Task 5 narrows it once at the top of `_route_all`). `budget.expired(deadline, clock)` has the same two-argument shape in Tasks 1, 2 and 8. The module is imported as `budget_module` in `routing_domain` and `freeform`, the only two files that bind `budget` as a parameter name, and as `budget` in the other seven; every code block above follows that split, and Task 2 step 3 names the files on each side. `StagedWorkBudget` is the Task 7 name and appears nowhere earlier. `_routing_pass_budget(deadline=None, seconds=None)` has one signature, used in Tasks 5 and 6.

**Ordering.** Task 4 must precede Task 5 (the leaf's type has to exist before the pass hands it one) and Task 5 must precede Task 6 (the seeding sites feed `_route_all`). Tasks 2 and 3 are independent of 4 to 7 and of each other, and either can be reviewed and rejected without blocking the rest. Task 7 must follow Task 6, which rewrites `sequence_solver.py:5908-5913` into `_routing_pass_budget(seconds=ceiling).left`; Task 7 then renames the `expansion_total` that line binds. Task 8 is last by construction. The one task a reviewer cannot reject in isolation is Task 5: its signature changes and its four seed conversions are one commit because splitting them leaves the tree failing mypy and the routing suites.
