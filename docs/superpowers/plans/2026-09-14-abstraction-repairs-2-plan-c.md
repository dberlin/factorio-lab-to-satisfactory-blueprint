# Abstraction Repairs 2, Plan C: The `_route_all` Run Object Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `routing_domain._route_all` — 3,729 lines, 67 nested functions, 20 `nonlocal` statements over 23 rebound names, 88 distinct pieces of captured state — into a `_RouteAllRun` object with typed fields and methods grouped by the clusters the review measured, with placement results byte-identical at every step.

**Architecture:** One new private class, `_RouteAllRun`, lives beside `_route_all` in `routing_domain.py`. Phase 1 gives it the 23 rebound names as typed fields and leaves every closure a closure that reads and writes `run.x`; that alone deletes all 20 `nonlocal` statements and six of Plan B's 15 `assert left is not None` narrowings. Phase 2 lifts one cluster at a time into methods — shared vocabulary, then search, commit, repair, last-mile — each lift adding only the fields its cluster captures and leaving a one-line bound-method alias (`_ends = run._ends`) so the hundreds of internal call sites are untouched. Phase 3 deletes those aliases and rewrites the call sites. Phase 4 clears the two items Plan A parked. Phase 5 takes the parameter objects that fall out.

**Tech Stack:** Python 3.14, uv venv at `.venv`, pytest (no summary line prints on this box: use the exit code), ruff, mypy strict (`files = ["src", "tests"]`; `scripts/` is not type-checked), Serena symbol tools for reads and renames.

**Spec:** `docs/superpowers/specs/2026-09-13-abstraction-review.md` (section 2.1 routing_domain seams, section 9 item 3 `_route_all` decomposition, section 9 item 10 parameter objects). Program sequence: Plan A (merged at 9758411d) → sprayed-Universe routing (merged at 549852e5) → Plan B (merged at 7f4dff71) → **Plan C (this)** → Plan D private-name promotion, import guard, routing_domain split (spec items 5 and 8).

**Branch point:** master `7f4dff71`, the Plan B merge. Every line number below was verified at `7f4dff71`; `src/flab2bp/layout/routing_domain.py` is 19,241 lines there and is byte-identical to `ed54fd35` (the three follow-up commits touched only `budget.py`, tests and docstrings).

**What Plan B left, which this plan builds on:**
- The routing pass ledger is a mutable `flab2bp.layout.budget.WorkBudget` **shared by reference**: aliased at the coverage-pass site, SET (not decremented) in `_geometric_search`, and decremented by a child's spend at four carve sites. A `dataclasses.replace` or any copy of a `WorkBudget` is a defect.
- The deadline is an explicit `float | None` parameter of `_route_all` that the body **rebinds mid-run** at two `finally:` sites (`routing_domain.py:8216` `deadline = route_deadline`, `:8565` `deadline = saved_deadline`) and derives per-query deadlines from (`_ordinary_query_deadline`, `:7843`). It is *not* folded into the budget object.
- `_routing_pass_budget(deadline=None, seconds=None)` is the one factory that seeds a routing pass ledger.
- 15 `assert ... left is not None` narrowings live inside `_route_all` (`:6305, :6352, :7881, :7940, :7941, :8190, :8453, :8522, :8568, :8912, :8929, :9136, :9242, :9300, :9530`) because mypy does not carry a narrowing into a nested function. Typed fields remove the six that re-assert the pass ledger.
- `BudgetCause` (`route_feedback.py`) and every stats key keep their spelling.
- `tests/test_one_deadline_rule.py` guards new deadline comparisons and new `{"left": …}` dict budgets. It must stay green in every task.

---

## Global Constraints

- No behavior change. Placement results must be identical; the corpus gate below is the proof, and `scripts/route_bench.py`'s MATCH line is the router-only witness.
- `left` semantics are preserved exactly, site for site. `WorkBudget` is a **mutable, non-frozen dataclass shared by reference**. A frozen dataclass or a `dataclasses.replace` on a `WorkBudget` anywhere in this migration is a defect. Moving a `WorkBudget` onto a field must alias it, never copy it.
- Every deadline comparison stays `>=` against a `float | None` where `None` never expires. A `>` anywhere in this plan is a defect.
- The deadline stays an explicit parameter and a **rebindable** piece of run state. `_RouteAllRun.deadline` is a plain mutable field; the two `finally:` restores stay `finally:` restores. Never replace the rebind with a per-call argument, and never make `deadline` a property, a frozen field, or derived from a `WorkBudget`.
- The three exception types keep their identity as subclasses of `flab2bp.layout.budget.BudgetExhausted`; they are not merged. `routing_domain.py:8213-8214` converts a caught `_PreparationDeadline` into `_GeometricDeadline` (`routing_proposals.Deadline`) precisely so the enclosing `except _GeometricDeadline` at `:7839` returns `None` instead of letting the preparation deadline propagate. Preserve that conversion and both handlers exactly.
- Names that tests and scripts monkeypatch must survive as module-level attributes in their current module, even when their body becomes a one-line delegation. The 48 `routing_domain` monkeypatch targets counted in the inventory include all fifteen module globals `_route_all` reads: `_expired` (31 sites inside `_route_all`), `_MAX_SEARCH_WORK` (4), `_STEPS` (3), `_route_box` (2), `RRR_MAX` (2), `_merge_frontier` (2), `_geometric_search` (2), `_COMMIT_REPAIR_PASSES` (2), `_make_grid`, `_SINGLE_ROUND_NETS`, `_reserve_port_access`, `_canvas_span`, `_REPAIR_MAX_VICTIMS`, `_commit_paths`, `_REPAIR_PASSES`. A lifted method must keep calling these **through the module global**, never through a field or a bound alias captured at construction time; a field would freeze the pre-patch object and the monkeypatch would go unseen.
- `BudgetCause` (`route_feedback.py:84`) keeps its spelling and its values. Stats keys are evidence names read by committed tooling and keep their current spelling: `"expansions"`, `"expansion_allowance"`, `"route_backend"`, `"work"` in `last_mile_counts`.
- `src/flab2bp/dsp/registry.py:1217` and `:1229` name `"_geometric_search"` and `"_route_all"` as **strings** in a `LintException` table. Neither symbol may be renamed by this plan, and any reference search before a deletion must cover that file.
- Do not touch `pyproject.toml`, `uv.lock`, or anything under `.venv`.
- Never run interactive git. Prefix git commands with `GIT_EDITOR=true`; commit with `-m` or `-F`. Never run `git commit` without `-m`/`-F`, `git rebase -i`, or `git add -i`.
- One logical change per commit, on branch `abstraction-repairs-2c` cut from `7f4dff71` in the main checkout. Every commit must leave the tree importable: `./.venv/bin/python3.14 -c "import flab2bp.pipeline"` exits 0. Commit messages end with the attribution lines the session provides.
- Serena: reads (`find_symbol`, `find_referencing_symbols`, `get_symbols_overview`) and `rename_symbol` are the required tools for symbol work; grep is for strings and comments. **Serena `rename_symbol` misses `dataclasses.replace(..., f=...)` keyword arguments and untyped attribute reads.** After every rename run `grep -rn "\.<old>\b\|<old>=" src scripts tests` and fix what it finds before committing.
- Only one agent may hold the Serena project at a time; if a concurrent worktree agent is running, use Read/Edit plus the LSP tools instead and say so in the commit message.
- Box discipline: before any test run heavier than one file, `vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}'` must print below 64 (never use load average). Pin runs with `taskset -c 96-127`. Record the figure beside every timing.
- Kill a stuck child by PID only (`kill <pid>`), never by name pattern. `pgrep` never matches its own process; if a `pgrep` for a stuck `git` or editor returns nothing, fix the pattern (anchor the interpreter, or use `[g]it`) rather than falling back to `ps | grep`.
- Scratchpad files written during a task go under the session scratchpad directory with a `plan-c-<task>-` prefix, never in the repo and never in `/tmp` directly.
- Delete only what a reference search including string references proves unused: `find_referencing_symbols`, then `grep -rn "<name>" src scripts tests docs/superpowers/specs web 2>/dev/null`. `monkeypatch.setattr(..., "<name>", ...)` targets and the `dsp/registry.py` `LintException` table reference symbols as strings.

## Gate

Every task ends with the lint gate. Every task in this plan except Tasks 10 and 11 is **routing-touching** and also ends with the corpus gate; the task says so.

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

Router gate (every routing-touching task, alongside the corpus gate):
```
taskset -c 96-127 env PYTHONHASHSEED=0 ./.venv/bin/python3.14 scripts/route_bench.py
```
Record the `MATCH` line. It replays a committed A\* capture and is the byte-identity witness for the search itself. A `MATCH` is **router-only evidence** and does not speak for sequence-pair or ALNS; the corpus compare is what covers those. A `DIFFER` fails the task outright — there is no run-to-run noise allowance on the replay.

**Control:** `.local-evidence/2026-09-13-session/audit-master-control-9758411d.jsonl` — 180 rows, **167 CLEAN / 13 REFUSED**, audit exit 1, no `TEARDOWN FAULT`. Its notes are in `audit-master-control-9758411d.md`. Do not take a new control and do not use Plan A's `audit-master-control-20260913.jsonl`.

Pass: 167 CLEAN / 13 REFUSED, zero status changes versus the control, and area changes only on the known run-to-run movers the notes list: super-magnetic-ring (all strategies), electromagnetic-matrix (freeform), casimir-crystal (freeform, best, hierarchical), processor-1 best, information-matrix (0 freeform and hierarchical, 1 best, 2 sequence-pair and hierarchical), plastic-1 (best and freeform), quantum-chip-1 best, quantum-chip-2 hierarchical, universe-matrix-0 freeform and best. A status change on any cell, or an area change outside that list, fails the task — and **before calling it a regression, run a paired same-commit second round**, which is what the notes require; an area mover that is not on that list needs a paired second round at the same commit before it may be reported as a regression or accepted as noise. The audit exits 1 on any refusal; that is expected, and the 13 are the fixed set of universe-matrix cells the notes name. A `TEARDOWN FAULT` line after the rows is a harness cleanup fault, not a result.

---

## Verified inventory

Counted at `7f4dff71` with `ast` and `symtable` over `src/flab2bp/layout/routing_domain.py`, cross-checked with Serena `find_symbol`/`get_symbols_overview`. The spec's figures are in the last column; where they differ, this plan's figure governs.

| Thing | Verified | Spec said |
|---|---|---|
| `_route_all` span | `routing_domain.py:6208-9936`, 3,729 lines | 5919-9411, 3,493 lines |
| Nested `def`s inside `_route_all` | **67** — 55 at depth 1, 12 at depth 2 | 61 closures |
| Nested `lambda`s | 5 (`:6431`, `:6929`, `:8266`, `:8519`, `:8954`) | not counted |
| Total lines of depth-1 nested defs | 2,938 | 3,057 |
| `nonlocal` statements | **20** | 18 |
| Distinct `nonlocal` names | **23** | 22 |
| Distinct outer names captured by depth-1 closures | 141, of which 88 become run fields (23 rebound + 65 state) | not counted |
| `_route_all` local/parameter names in scope | 195 | not counted |
| `assert ... left is not None` inside `_route_all` | 15 | Plan B: 15 |
| Module globals read inside `_route_all` that tests monkeypatch | 15 | not counted |
| Distinct private `routing_domain._x` names referenced from tests | **142 names at 1,071 sites** (`from … import _x` plus `routing_domain.`/`domain.`/`rd.` attribute reads) | 122 names at 883 sites |
| Of those, references that name a `_route_all` **closure** | **0** | "tests reach the closures today only through 883 private references" |
| Test references to `_route_all` itself | **51 sites in 10 files** | not counted |
| Script references to `_route_all` | 3 sites | not counted |
| `routing_domain` monkeypatch targets (distinct names) | 48 | 43 setattr sites |
| `_merge_frontier` | `routing_domain.py:5621-5806`, **18 parameters** (4 positional, 14 keyword-only), 27 reference sites | 17 params |
| `_pack_window` | `freeform.py:3130-3213`, **17 parameters** (1 positional, 16 keyword-only), 15 reference sites, 2 src callers (`freeform.py:6438`, `sequence_solver.py:5680`) | 17 params |
| `_geometric_search` | `routing_domain.py:4871-5130`, 15 parameters | 15 params |

**The spec's "883 private references reach the closures" does not hold as stated.** A closure of `_route_all` is not a module attribute; **zero** of the 1,071 private `routing_domain` references from tests name one. The 1,071 figure is the whole-module private surface (section 7's 883 was measured with a narrower attribute pattern). The real testability claim is narrower and stronger: the 67 closures have **exactly one door**, the 51 test call sites of `_route_all` itself, and a run object is what turns 67 untestable bodies into 67 addressable methods. Plan C does not change the door; Plan D's private-name promotion does.

### The 23 rebound names (the `nonlocal` set) and where they are written

| Name | Declared `nonlocal` in | Cluster |
|---|---|---|
| `last_junction_blame` | `_can_junction` `:6890`, `_ends` `:7375` | search |
| `neighborhood` | `_ends` `:7375` | search |
| `geometry_screen`, `geometry_world` | `_analytic_ordinary` `:7798` | search |
| `capped_ordinary`, `ordinary_remaining`, `total_work` | `_search` `:7849` | search |
| `admitted_future`, `admitted_path` | `_search_route` `:8177` (inner `admit_source_family` `:8197`) | search |
| `deadline` | `_search_route`/`admit_source_family` `:8200`, `_repair`/`_grouped_overcap_alternative` `:8452` | search + repair |
| `policy_restricted`, `repair_guards`, `work` | `_repair` `:8303` | repair |
| `proposal_used` | `_restrict_proposal` `:8839`, `_last_mile` `:9296` | last-mile |
| `last_mile_seconds` | `_tally` `:9064`, `_solve_cluster` `:9070` | last-mile |
| `relaxed_junctions` | `_relaxed_cluster_result` `:9100` | last-mile |
| `proved_round`, `relation_evidence`, `relation_strips` | `_record_cluster_relation` `:9191`, `_last_mile` `:9296` | last-mile |
| `commit_attempt` | `_complete_source_dependents` `:9239`, `_last_mile` `:9296` | last-mile |
| `last_mile_done`, `last_mile_floor` | `_last_mile` `:9296` | last-mile |
| `contextual_seen` | `retain_commit_failures` `:9608` | commit |
| `work` (again) | `_complete_source_dependents` `:9239` | last-mile |

### The 55 depth-1 closures, by cluster

Clusters are the spec's 2.1 `route_all / commit / repair` cluster decomposed by the sub-groups section 9 item 3 names, plus one leaf group the measured call graph forces out (see "Lift order", below).

**V — shared vocabulary (10 closures, Task 2).** `connector_is_powered` 6269-6270; `_net_id` 6338-6342; `_pass_budget_cause` 6344-6355; `_endpoint_cells` 6357-6359; `role_rows` 6361-6367 (**generator**, `yield`); `_blocking_endpoint_cells` 6371-6379; `_blocking_nets` 6381-6392; `_failure` 6394-6411; `_selection_key` 6477-6500; `_last_mile_report` 8822-8837. Nonlocal writes: none. Captures 12 outer names; **7 new fields** (`budget` is already Task 1's).

**S — search (22 depth-1 + 6 nested, Task 3).** `_junction_stacks_collide` 6864-6870 (**`@cache`**); `_peer_taps` 6872-6888; `_can_junction` 6890-7022 (*nonlocal* `last_junction_blame`; nested `remember` 6936-6953); `_direct_tap_clear` 7024-7065; `_inside_grid` 7089-7092; `_claim_junction_guard` 7094-7124; `_selected_hints` 7126-7136; `_set_source_hint` 7138-7148; `_stake` 7150-7177; `_unstake` 7179-7227; `_prebuilt_branch_port` 7232-7239 (**`@cache`**); `_prebuilt_source_starts` 7241-7373; `_ends` 7375-7721 (*nonlocal* `last_junction_blame`, `neighborhood`; nested `prebuilt_starts` 7458-7479, `admit_witness` 7503-7519, `frontier_junctionable` 7547-7563); `_future_source_offers` 7723-7796; `_analytic_ordinary` 7798-7841 (*nonlocal* `geometry_screen`, `geometry_world`); `_ordinary_query_deadline` 7843-7847; `_search` 7849-8153 (*nonlocal* `capped_ordinary`, `ordinary_remaining`, `total_work`; nested `probe_ordinary` 7896-7951); `_preserves_source_frontier` 8155-8175; `_search_route` 8177-8259 (*nonlocal* `admitted_future`, `admitted_path`, `deadline`; nested `admit_source_family` 8197-8216); `_route_order` 8261-8276; `_endpoint_dependents` 8278-8286; `_dependency_closure` 8288-8301. Captures 51 outer names; **40 new fields**, plus 2 more for the two `@cache` callables (Task 3's Risk line) — 42 declared. `_prebuilt_path_port` (the module-scope local `lru_cache(maxsize=None)(...)` at `:7229`) is one of the 40.

**C — commit (6 depth-1, Task 4).** `_budget_result` 6413-6475; `_commit_selection` 6502-6614; `_finish` 6616-6724; and, **defined once per round inside the loop body**, `commit_once` 9598-9599, `terminal_attempt` 9601-9606, `retain_commit_failures` 9608-9661 (*nonlocal* `contextual_seen`; **default-argument snapshot** of `round_failures` and `search_blockers`). Captures 35 outer names; **12 new fields**, plus the 2 `retained_*` snapshots — 14 declared.

**R — repair (1 depth-1 + 6 nested, Task 5).** `_repair` 8303-8797 (*nonlocal* `deadline`, `policy_restricted`, `repair_guards`, `work`); nested `_tap_guard_victims` 8365-8382, `_source_tap_guard_victims` 8384-8397, `_refresh_repair_guards` 8399-8409, `_rebuild_route` 8413-8439, `_grouped_overcap_alternative` 8441-8566 (*nonlocal* `deadline`, `work`), `admit_repair_tap` 8591-8608 (**default-argument snapshot** of `index`, `victims`, `dependents`). Captures 21 outer names; **0 new fields** — every one is already a field by Task 4.

**L — last-mile (16 depth-1, Task 6).** `_restrict_proposal` 8839-8884 (*nonlocal* `proposal_used`); `_cluster_offers` 8886-8892; `_cluster_search` 8894-8936; `_pass_budget_left` 8938-8946; `_cluster_environment` 8948-8956; `_source_is_junctionable` 8958-8976; `_capture` 8978-9014; `_round_state` 9016-9035; `_restore_staked` 9037-9062; `_tally` 9064-9068 (*nonlocal* `last_mile_seconds`); `_solve_cluster` 9070-9086 (*nonlocal* `last_mile_seconds`); `_cluster_is_sibling_free` 9088-9098; `_relaxed_cluster_result` 9100-9189 (*nonlocal* `relaxed_junctions`); `_record_cluster_relation` 9191-9237 (*nonlocal* `proved_round`, `relation_evidence`, `relation_strips`); `_complete_source_dependents` 9239-9294 (*nonlocal* `commit_attempt`, `work`); `_last_mile` 9296-9449 (*nonlocal* `commit_attempt`, `last_mile_done`, `last_mile_floor`, `proposal_used`, `proved_round`). Captures 47 outer names; **5 new fields**: `blame`, `pressure`, `round_failures`, `search_blockers`, `search_failures`.

**Field totals.** 24 (Task 1) + 7 (Task 2) + 42 (Task 3) + 14 (Task 4) + 0 (Task 5) + 5 (Task 6) = **92 declared fields**: the 88 distinct outer names the closures capture, plus the two `retained_*` snapshots and the two `@cache` callables. `left` is a property, not a field.

### Lift order, forced by the measured call graph

Cross-cluster call edges between depth-1 closures, counted from the AST:

```
L -> S 20    R -> S 13    C -> V 11    L -> C 6    L -> V 5
S -> V 4     C -> L 1     R -> V 1
```

`V` is the only group nothing depends on that is not itself a leaf, once `_last_mile_report` joins it — the single `C -> L` edge is `_finish`/`_budget_result` calling `_last_mile_report`, and `_budget_result` is the only `V`-shaped closure with an outward edge, so it moves to `C`. With that one reassignment the order **V → S → C → R → L** has **zero** method-calls-unlifted-closure edges. Any other order needs a callback-field bridge; do not invent one.

`_net_id` is the hottest of them: 24 call sites, called from 14 different closures across all five groups. It is lifted first, in Task 2.

---

### Task 1: `_RouteAllRun` carries the 23 rebound names

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — add `_RouteAllRun` immediately before `_route_all` at `:6208`; edit `_route_all` at the 20 `nonlocal` statements and every read and write of the 23 names
- Create: `tests/layout/test_route_all_run.py`

**Interfaces:**
- Consumes: `flab2bp.layout.budget.WorkBudget` (Plan B), imported in `routing_domain` as `budget_module`.
- Produces, in `routing_domain`:
```python
@dataclass(slots=True)
class _RouteAllRun:
    """The mutable state one `_route_all` call rebinds while it runs.

    Every field here was a `nonlocal` name. Nothing that is bound once and
    only read belongs in this class yet; Tasks 2 to 6 add those as each
    cluster becomes methods.
    """

    budget: budget_module.WorkBudget
    deadline: float | None
    work: int = 0
    total_work: int = 0
    capped_ordinary: bool = False
    ordinary_remaining: int = 0
    contextual_seen: bool = False
    proposal_used: bool = False
    policy_restricted: bool = False
    relaxed_junctions: bool = False
    last_mile_done: bool = False
    last_mile_floor: int = 0
    last_mile_seconds: float = 0.0
    proved_round: int = -1
    relation_evidence: str = ""
    relation_strips: tuple[int, ...] = ()
    last_junction_blame: tuple[int, ...] = ()
    admitted_path: tuple[Cell, ...] | None = None
    admitted_future: dict[Cell, Cell] | None = None
    geometry_world: projection_world.ClearanceOracle | None = None
    geometry_screen: FlatScreen | None = None
    neighborhood: JunctionNeighborhood | None = None
    repair_guards: frozenset[Cell] = frozenset()
    commit_attempt: _CommittedAttempt | None = None

    @property
    def left(self) -> int:
        """The pass ledger, narrowed once for every reader.

        `WorkBudget.left` is `int | None`; a routing pass is always entered
        with a ledger (`_route_all` defaults the argument at :6299). mypy
        cannot carry that narrowing into a nested function, which is why six
        closures re-asserted it.
        """
        left = self.budget.left
        assert left is not None, "a routing pass needs a ledger"
        return left
```
Field types, defaults and initial values must be copied from the current `_route_all` initialisers, not guessed: `work = 0` `:6285`, `contextual_seen = False` `:6332`, `proposal_used = False` `:6334`, `commit_attempt = None` `:6335`, `geometry_world`/`geometry_screen = None` `:6274-6275`, `last_junction_blame = ()` `:6861`, `relaxed_junctions = False` `:6857`, `last_mile_seconds = 0.0` `:8811`, `last_mile_done = False` `:8812`, `last_mile_floor = 0` `:8816`, `proved_round = -1` `:8818`, `relation_strips = ()` `:8819`, `relation_evidence = ""` `:8820`. `admitted_path`, `admitted_future`, `capped_ordinary`, `ordinary_remaining`, `total_work`, `policy_restricted`, `repair_guards` and `neighborhood` are initialised inside `_search`, `_search_route`, `_repair` and `_ends` respectively; read each initialiser with Serena `find_symbol(..., include_body=True)` and copy it.

**Risk:** a name whose initialiser runs *later* than construction. `admitted_path`, `admitted_future`, `capped_ordinary`, `ordinary_remaining`, `total_work`, `policy_restricted` and `repair_guards` are today reset at the **top of the closure that owns them**, once per call of that closure, not once per `_route_all`. Turning them into dataclass fields with a default makes the default a *once-per-run* initialisation, and the per-call reset disappears — a sibling closure would then see the previous call's value. Every one of these must keep its per-call reset as an explicit `run.<name> = <initial>` at the same line where the closure sets it today. Pinned by `test_the_per_call_resets_are_still_per_call`.

The mirror risk is the same one from the other side: a field write is visible to a sibling closure *immediately*, exactly as a `nonlocal` write is. That is the semantics being preserved, not changed, and `test_a_field_write_is_visible_to_a_sibling_reader` pins it so a later task cannot quietly turn a field into a constructor argument.

- [ ] **Step 1: Cut the branch and take the golden capture**

```
GIT_EDITOR=true git switch -c abstraction-repairs-2c 7f4dff71
ls -l .local-evidence/2026-09-13-session/audit-master-control-9758411d.jsonl
```
The control already exists; do not take a new one. Then capture the literal a whole `_route_all` pass produces on the unmodified tree — this is the characterization value every later task asserts against:
```
./.venv/bin/python3.14 - <<'PY'
from fractions import Fraction
from dataclasses import replace
from flab2bp.layout import routing_domain as domain
from flab2bp.layout.budget import WorkBudget
from flab2bp.layout.base import PlacedBuilding
from flab2bp.layout.route_feedback import NetId, NetRole
from flab2bp.layout.band_policy import BandPolicy

bounds = (-2, -2, 202, 161)
policy = BandPolicy("200")
canvas = domain._Canvas(limit=bounds, belt_rules=replace(domain._DEFAULT_BELT_RULES, max_z=Fraction(0)))
tower = canvas.power_building
for x, y in ((0, 0), (200, 159)):
    canvas.add(PlacedBuilding(tower.item_id, tower.model_index, x, y, width=tower.width, height=tower.height), solid=True)
def port(x, y):
    index = canvas.add(PlacedBuilding(2003, 37, x, y, carries_item="gear"))
    return domain._Port(index, x, y, x, x)
source, destination = port(50, 80), port(150, 80)
canvas.guard.update((100, y, 0) for y in range(160))
canvas.junction_projection = domain._CompositionProjection(canvas.buildings, bounds, policy, belt_rules=canvas.belt_rules)
nets = [domain._Net(source, destination, "gear", net_id=NetId(0, 1, "gear", NetRole.INTERNAL, 0))]
canvas.guard.remove((100, 80, 0))
ledger = WorkBudget(left=100_000)
result = domain._route_all(canvas, nets, 2003, 37, bounds, budget=ledger)
print("status", result.status)
print("left", ledger.left)
print("paths", {i: len(p) for i, p in sorted(result.paths.items())} if hasattr(result, "paths") else "n/a")
PY
```
Paste the three printed values into `tests/layout/test_route_all_run.py` in step 2 where the code below says `GOLDEN_*`. If `result` has no `paths` attribute, print `result` itself and pin `repr(result)`'s status and stranded identities instead; record which form you used in the commit message.

- [ ] **Step 2: Write the failing tests**

`tests/layout/test_route_all_run.py`:
```python
"""`_route_all`'s run state is one typed object, and the semantics are unchanged.

The 2026-09-13 abstraction review (section 9 item 3) asked for the 61 closures
to become methods on a `_RouteAllRun` grouped by cluster. This file pins the
two properties the lift must not break: a rebound name is still shared state
seen by every sibling, and a per-call reset is still per call.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from flab2bp.layout import routing_domain as domain
from flab2bp.layout.band_policy import BandPolicy
from flab2bp.layout.base import PlacedBuilding
from flab2bp.layout.budget import WorkBudget
from flab2bp.layout.route_feedback import NetId, NetRole

SRC = Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout" / "routing_domain.py"

#: Captured from the unmodified tree at 7f4dff71 by Task 1 step 1.
GOLDEN_STATUS = "ROUTED"          # replace with the printed status name
GOLDEN_LEFT = 0                   # replace with the printed ledger value
GOLDEN_PATH_LENGTHS = {0: 0}      # replace with the printed path lengths


def _route_all_node() -> ast.FunctionDef:
    tree = ast.parse(SRC.read_text(), filename=str(SRC))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_route_all":
            return node
    raise AssertionError("_route_all is gone")


def _corridor() -> tuple[domain._Canvas, list[domain._Net], tuple[int, int, int, int]]:
    """The 200-wide guarded corridor from test_routing_integration, opened."""
    bounds = (-2, -2, 202, 161)
    policy = BandPolicy("200")
    canvas = domain._Canvas(
        limit=bounds, belt_rules=replace(domain._DEFAULT_BELT_RULES, max_z=Fraction(0))
    )
    tower = canvas.power_building
    for x, y in ((0, 0), (200, 159)):
        canvas.add(
            PlacedBuilding(
                tower.item_id, tower.model_index, x, y, width=tower.width, height=tower.height
            ),
            solid=True,
        )

    def port(x: int, y: int) -> domain._Port:
        index = canvas.add(PlacedBuilding(2003, 37, x, y, carries_item="gear"))
        return domain._Port(index, x, y, x, x)

    source, destination = port(50, 80), port(150, 80)
    canvas.guard.update((100, y, 0) for y in range(160))
    canvas.junction_projection = domain._CompositionProjection(
        canvas.buildings, bounds, policy, belt_rules=canvas.belt_rules
    )
    nets = [
        domain._Net(
            source, destination, "gear", net_id=NetId(0, 1, "gear", NetRole.INTERNAL, 0)
        )
    ]
    canvas.guard.remove((100, 80, 0))
    return canvas, nets, bounds


def test_a_whole_pass_still_produces_the_captured_result() -> None:
    """The characterization witness every lift task re-runs unchanged."""
    canvas, nets, bounds = _corridor()
    ledger = WorkBudget(left=100_000)
    result = domain._route_all(canvas, nets, 2003, 37, bounds, budget=ledger)
    assert result.status.name == GOLDEN_STATUS
    assert ledger.left == GOLDEN_LEFT
    assert {index: len(path) for index, path in sorted(result.paths.items())} == (
        GOLDEN_PATH_LENGTHS
    )


def test_route_all_declares_no_nonlocal_names() -> None:
    """All 23 rebound names live on `_RouteAllRun` instead."""
    offenders = [
        f"{node.lineno}: {', '.join(node.names)}"
        for node in ast.walk(_route_all_node())
        if isinstance(node, ast.Nonlocal)
    ]
    assert offenders == [], "put the rebound name on _RouteAllRun: " + "; ".join(offenders)


def test_the_run_object_carries_exactly_the_rebound_names() -> None:
    from dataclasses import fields

    assert {field.name for field in fields(domain._RouteAllRun)} == {
        "budget",
        "deadline",
        "work",
        "total_work",
        "capped_ordinary",
        "ordinary_remaining",
        "contextual_seen",
        "proposal_used",
        "policy_restricted",
        "relaxed_junctions",
        "last_mile_done",
        "last_mile_floor",
        "last_mile_seconds",
        "proved_round",
        "relation_evidence",
        "relation_strips",
        "last_junction_blame",
        "admitted_path",
        "admitted_future",
        "geometry_world",
        "geometry_screen",
        "neighborhood",
        "repair_guards",
        "commit_attempt",
    }


def test_a_field_write_is_visible_to_a_sibling_reader() -> None:
    """A field write must behave exactly like the `nonlocal` write it replaced."""
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)

    def writer() -> None:
        run.work += 7

    def reader() -> int:
        return run.work

    assert reader() == 0
    writer()
    assert reader() == 7


def test_the_run_object_narrows_the_ledger_once() -> None:
    run = domain._RouteAllRun(budget=WorkBudget(left=42), deadline=None)
    assert run.left == 42
    run.budget.left = None
    with pytest.raises(AssertionError, match="a routing pass needs a ledger"):
        _ = run.left


def test_the_per_call_resets_are_still_per_call() -> None:
    """Seven fields are reset at the top of the closure that owns them.

    A dataclass default is a once-per-run initialisation. If the reset were
    dropped, a second call of the owning closure would start from the previous
    call's value, and a sibling would read stale admission evidence.
    """
    source = SRC.read_text()
    for owner, reset in (
        ("_search", "run.total_work = 0"),
        ("_search", "run.capped_ordinary = False"),
        ("_search_route", "run.admitted_path = None"),
        ("_search_route", "run.admitted_future = None"),
        ("_repair", "run.policy_restricted = False"),
    ):
        assert reset in source, f"{owner} must still reset {reset!r} on every call"


def test_the_two_deadline_restores_are_still_finally_clauses() -> None:
    """`deadline` is rebound mid-run and restored; a lift must not drop that."""
    restores = [
        node.lineno
        for node in ast.walk(_route_all_node())
        if isinstance(node, ast.Try)
        and node.finalbody
        and any(
            isinstance(stmt, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "deadline"
                for target in stmt.targets
            )
            for stmt in node.finalbody
        )
    ]
    assert len(restores) == 2, f"expected two finally-restores of the deadline, got {restores}"
```

- [ ] **Step 3: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py; echo "exit=$?"`
Expected: non-zero exit. `test_a_whole_pass_still_produces_the_captured_result` passes (it is the golden capture). The other five fail: `AttributeError: module 'flab2bp.layout.routing_domain' has no attribute '_RouteAllRun'`, and `test_route_all_declares_no_nonlocal_names` lists 20 offending lines.

- [ ] **Step 4: Add the class**

Insert `_RouteAllRun` (the code block in **Interfaces** above) immediately before `def _route_all(` at `:6208`, using Serena `insert_before_symbol(name_path="_route_all", relative_path="src/flab2bp/layout/routing_domain.py", body=...)`. `slots=True` is deliberate: a typo'd field name must be an `AttributeError`, not a silent new attribute, and the object is constructed once per routing pass so the slot layout costs nothing.

- [ ] **Step 5: Construct it and rewrite the 23 names**

Immediately after the ledger default and its narrowing at `:6299-6305`, construct:
```python
    run = _RouteAllRun(budget=budget, deadline=deadline)
```
Then, in order:
1. Delete all 20 `nonlocal` statements.
2. Rewrite every read of the 23 names to `run.<name>` and every write to `run.<name> = …` / `run.<name> += …`. Do this one name at a time with Serena `rename_symbol` where the LSP resolves it, and verify each with `grep -n "\b<name>\b" src/flab2bp/layout/routing_domain.py | awk -F: '$1>=6208 && $1<=9960'` — the only surviving bare occurrences must be the `_RouteAllRun` field declarations and `run.<name>`.
3. Delete the initialiser lines that the dataclass defaults now own (`:6274-6275`, `:6285`, `:6332`, `:6334`, `:6335`, `:6857`, `:6861`, `:8811`, `:8812`, `:8816`, `:8818`, `:8819`, `:8820`) **only** where the value is identical to the field default. Keep every per-call reset inside `_search`, `_search_route`, `_repair` and `_ends` as an explicit `run.<name> = <initial>` at the same line.
4. Replace the six pass-ledger re-assertions at `:8453`, `:8568`, `:8912`, `:9136`, `:9242`, `:9300` with reads of `run.left`. Leave the nine others (`:6305` becomes the property's assert, `:6352`, `:7881`, `:7940`, `:7941`, `:8190`, `:8522`, `:8929`, `:9530`) exactly as they are: they narrow *local* `WorkBudget`s and closure parameters, which the run object does not own.
5. Leave `deadline` as `run.deadline`, including both `finally:` restores at `:8216` and `:8565`. The local `route_deadline`/`saved_deadline` snapshots stay plain locals.

- [ ] **Step 6: Prove nothing was copied**

```
grep -n "replace(run\|replace(.*budget=\|run.budget = \|copy(run" src/flab2bp/layout/routing_domain.py
```
Expected: zero hits. `run.budget` is the caller's ledger, aliased; reassigning or replacing it breaks the four carve sites Plan B preserved.

- [ ] **Step 7: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py tests/test_one_deadline_rule.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first and record the figure.

- [ ] **Step 8: Lint gate**

- [ ] **Step 9: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 10: Commit**

`Carry _route_all's rebound state on one typed run object`, body listing the 23 names, the 20 deleted `nonlocal` statements, the six re-assertions replaced by `run.left`, and the seven per-call resets kept explicit.

---

### Task 2: Lift the shared vocabulary

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — `_RouteAllRun` (add 8 fields and 10 methods); `_route_all` at `:6269-6270`, `:6338-6411`, `:6477-6500`, `:8822-8837`
- Test: `tests/layout/test_route_all_run.py` (add)

**Interfaces:**
- Consumes: `_RouteAllRun` from Task 1.
- Produces, as methods on `_RouteAllRun` with the closures' current signatures minus the captured names:
```python
def _connector_is_powered(self, building: PlacedBuilding) -> bool: ...
def _net_id(self, index: int) -> NetId: ...
def _pass_budget_cause(self) -> BudgetCause: ...
def _endpoint_cells(self, net: _Net) -> tuple[Cell | None, Cell]: ...
def _role_rows(self) -> Iterator[tuple[NetId, str, str, Cell, str, tuple[int, _Net]]]: ...
def _blocking_endpoint_cells(
    self, blocking_nets: tuple[NetId, ...]
) -> tuple[tuple[Cell | None, Cell | None], ...]: ...
def _blocking_nets(
    self, wall: Sequence[Cell], source_blockers: Sequence[NetId] = ()
) -> tuple[NetId, ...]: ...
def _failure(
    self, index: int, search: _PathSearchResult, blocking_nets: tuple[NetId, ...]
) -> NetFailure: ...
def _selection_key(
    self,
    selected_paths: Mapping[int, tuple[Cell, ...]],
    selected_source_hints: Mapping[int, Cell],
    selected_sink_hints: Mapping[int, Cell],
    selected_taps: Mapping[int, Cell],
) -> tuple[object, ...]: ...
def _last_mile_report(self) -> LastMileReport: ...
```
Each signature above is the closure's current one at `:6269`, `:6338`, `:6344`, `:6357`, `:6361`, `:6371`, `:6381`, `:6394`, `:6477`, `:8822` with `self` prepended; no parameter is added, removed or retyped.
and 7 new fields, all set in `_route_all`'s prologue and never rebound after (`budget` is already Task 1's):
```python
    canvas: _Canvas = field(init=False)
    nets: list[_Net] = field(init=False)
    net_index: Nets[NetId] = field(init=False)
    owner: dict[Cell, int] = field(init=False)
    power_discs: tuple[tuple[int, int, int], ...] | None = field(init=False)
    primitives: RoutePrimitives = field(init=False)
    last_mile_counts: dict[str, int] = field(init=False)
```
`power_discs` is `_power_coverage_discs`'s `tuple[tuple[int, int, int], ...]` or `None` when `planned_power_sites` is `None` (`:6259-6267`); `net_index` is what `Nets.of` returns for `NetId` rows (`indexed/nets.py:141`).

**Risk:** `role_rows` is the one **generator** closure (`yield` at `:6361-6367`) and it is consumed exactly once, at `:6369` `net_index = Nets.of(role_rows())`. A generator method is still a generator, but the field it would need — `net_index` — is the thing `:6369` is *producing*. The method must therefore be called before `run.net_index` exists. Set `run.net_index = Nets.of(run._role_rows())` at `:6369` and declare `net_index` as `field(init=False)` **with no default**, so a reader that runs before `:6369` raises `AttributeError` exactly where the closure would have raised `NameError`. A placeholder default (`Nets.of(())`, `None`) would silently serve an empty index instead. Pinned by `test_the_net_index_field_is_unset_before_the_prologue_fills_it`.

Note also that the closure is named `role_rows` without a leading underscore and every other vocabulary closure has one; the method is `_role_rows` for consistency. Nothing outside `_route_all` can see either name (the inventory confirms zero test references to any closure name), so this is not an API change.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_all_run.py`:
```python
def test_the_vocabulary_is_methods_not_closures() -> None:
    lifted = {
        "_net_id",
        "_pass_budget_cause",
        "_endpoint_cells",
        "_role_rows",
        "_blocking_endpoint_cells",
        "_blocking_nets",
        "_failure",
        "_selection_key",
        "_last_mile_report",
        "_connector_is_powered",
    }
    assert lifted <= set(vars(domain._RouteAllRun)), sorted(lifted - set(vars(domain._RouteAllRun)))
    still_closures = {
        node.name
        for node in ast.walk(_route_all_node())
        if isinstance(node, ast.FunctionDef) and node is not _route_all_node()
    }
    assert lifted & still_closures == set(), sorted(lifted & still_closures)


def test_the_net_index_field_is_unset_before_the_prologue_fills_it() -> None:
    """A late-bound field must raise, not serve an empty index."""
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    with pytest.raises(AttributeError):
        _ = run.net_index
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py -k "vocabulary or net_index"; echo "exit=$?"`
Expected: non-zero exit, the ten names listed as missing from `_RouteAllRun`.

- [ ] **Step 3: Add the 8 fields**

In `_RouteAllRun`, below the 23 rebound fields, with a comment separating the two groups:
```python
    # Bound once in `_route_all`'s prologue and only read afterwards. These are
    # fields because a method cannot capture; they are not run state.
    canvas: _Canvas = field(init=False)
    nets: list[_Net] = field(init=False)
    net_index: Nets[NetId] = field(init=False)
    owner: dict[Cell, int] = field(init=False)
    power_discs: tuple[tuple[int, int, int], ...] | None = field(init=False)
    primitives: RoutePrimitives = field(init=False)
    last_mile_counts: dict[str, int] = field(init=False)
```
`field(init=False)` with no default is deliberate throughout this plan: every one of these is assigned exactly once in the prologue, and an unassigned read must be an `AttributeError`. The annotations come from `canvas` `:6248`, `nets` the parameter, `owner` `:6283`, `power_discs` `:6259-6267`, `primitives` `:6273`, `last_mile_counts` `:8799-8810`.

- [ ] **Step 4: Move the ten bodies**

For each closure, in this order — `_connector_is_powered`, `_net_id`, `_endpoint_cells`, `_blocking_endpoint_cells`, `_blocking_nets`, `_pass_budget_cause`, `_failure`, `_selection_key`, `_role_rows`, `_last_mile_report` — do exactly this, one closure per edit:
1. Read the closure body with Serena `find_symbol(name_path="_route_all/<name>", relative_path="src/flab2bp/layout/routing_domain.py", include_body=True)`.
2. Paste it into `_RouteAllRun` as a method, adding `self` and prefixing each captured name with `self.`. **Do not prefix a module global**: `_expired`, `_splitter_stack_geometry`, `_building_collider_hits`, `_commit_paths` and the other twelve stay bare so a `monkeypatch.setattr(routing_domain, …)` still reaches them.
3. Replace the closure in `_route_all` with a one-line alias at the same place: `_net_id = run._net_id`. The alias keeps all 24 call sites byte-identical; Task 7 deletes it.
4. Run `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py::test_a_whole_pass_still_produces_the_captured_result` after each one. A failure isolates to the closure you just moved.

For `_role_rows`, also change `:6369` to `run.net_index = Nets.of(run._role_rows())` and delete the local `net_index` binding; `net_index` is read by three closures in the search cluster and by `_budget_result`, which Task 3 and Task 4 convert to `self.net_index`.

- [ ] **Step 5: Prove no global became a field**

```
grep -n "self\._expired\|self\._geometric_search\|self\._commit_paths\|self\._make_grid\|self\._merge_frontier\|self\._MAX_SEARCH_WORK\|self\.RRR_MAX\|self\._REPAIR_PASSES" src/flab2bp/layout/routing_domain.py
```
Expected: zero hits. Any hit is a monkeypatch target that a test can no longer reach.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py tests/test_one_deadline_rule.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py tests/layout/test_physical_flow.py; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 9: Commit**

`Lift _route_all's shared net vocabulary onto the run object`.

---

### Task 3: Lift the search cluster

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — `_RouteAllRun` (add 46 fields and 22 methods, 6 of them holding nested helpers); `_route_all` at `:6864-8301` and at the prologue bindings those closures read
- Test: `tests/layout/test_route_all_run.py` (add)

**Interfaces:**
- Consumes: `_RouteAllRun` with the vocabulary methods from Task 2.
- Produces, as methods on `_RouteAllRun`, the 22 depth-1 search closures listed in the inventory, each keeping its current parameter list minus the captured names, plus **40 new `field(init=False)` fields**: `_prebuilt_path_port`, `admission_memo`, `belt_id`, `belt_model`, `bounds`, `corridor_reservations`, `destination_access_walls`, `dst_group`, `flow_limits`, `grid`, `guard_claims`, `hinted_to`, `history`, `junction_frame_bans`, `junction_obstacle_span`, `junction_ok`, `junction_reservation_blockers`, `own_source_nets`, `owned_source_starts`, `path_guards`, `path_tap`, `paths`, `permanent_guard`, `planned_taps`, `prioritize_source_families`, `priority`, `rejected_goals`, `rejected_path_cells`, `rejected_sink_hints`, `rejected_source_hints`, `rejected_starts`, `route_distance`, `sink_hint`, `source_access_blockers`, `source_access_walls`, `source_family`, `source_family_distance`, `source_hint`, `src_group`, `src_group_set`. This cluster also reads six fields Task 2 added (`canvas`, `nets`, `net_index`, `owner`, `power_discs`, `primitives`) and does not redeclare them.
- Also produces two more `Callable` fields, `_junction_stacks_collide` and `_prebuilt_branch_port`, built in `_route_all`'s prologue rather than declared as `@cache` methods — see the Risk line. 42 fields declared in all.

**Risk:** three caches whose lifetime is the call, and six fields bound after the closures that read them.

*The caches.* `_junction_stacks_collide` (`:6863` `@cache`), `_prebuilt_branch_port` (`:7231` `@cache`) and `_prebuilt_path_port` (`:7229` `lru_cache(maxsize=None)`) are created fresh on every `_route_all` call today. `@cache` on a method is **process-lifetime** and keyed on `self`, so it would (a) survive across routing passes and across tests, and (b) keep every `_RouteAllRun` — and through it every `_Canvas` — alive forever. Worse, `_junction_stacks_collide` calls `_building_collider_hits`, which three tests monkeypatch; a process-lifetime cache serves pre-patch verdicts. These three must stay per-run: build them in `_route_all`'s prologue as `run._junction_stacks_collide = cache(run._junction_stacks_collide_uncached)` (declare the field `Callable[[Cell, Cell], bool] = field(init=False)`), and keep the uncached body a plain method. Pinned by `test_the_three_per_run_caches_do_not_outlive_the_run`.

*The late bindings.* `route_distance` `:9451`, `source_family` `:9454`, `source_family_distance` `:9457` and `priority` `:9462` are bound **after** every search closure that reads them is defined — `:9451` is 1,076 lines below `_ends`. As closures they were read at call time and a call before `:9451` would have raised `NameError`. As `field(init=False)` with no default they raise `AttributeError` at the same point. Do not give any of them a default, and do not hoist their binding up to the prologue to "tidy" it: `route_distance` and the two `source_family*` maps are derived from `paths`, `src_group` and `same_src`, which the prologue has not finished building. (`pressure` `:9481` and `blame` `:9490` have the same shape but belong to the last-mile cluster; Task 6 declares them.) Pinned by `test_the_late_search_fields_are_unset_until_the_loop_binds_them`.

*The deadline.* `_search_route`'s inner `admit_source_family` (`:8197-8216`) narrows `run.deadline` for the proposal only and restores it in `finally:`. Its `except _PreparationDeadline as error: raise _GeometricDeadline from error` at `:8213-8214` is the conversion the Global Constraints protect: the enclosing `except _GeometricDeadline` at `:7839` inside `_analytic_ordinary` is what returns `None` instead of propagating. `admit_source_family` stays a nested function inside the `_search_route` method — it is created conditionally (`if any(sibling not in paths …)` at `:8195`) and only when created is it passed as `admit_proposal`. Lifting it to a method would make it unconditionally available and would change `admit_proposal`'s `None`-ness. Do not lift it.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_all_run.py`:
```python
SEARCH_METHODS = (
    "_junction_stacks_collide_uncached",
    "_peer_taps",
    "_can_junction",
    "_direct_tap_clear",
    "_inside_grid",
    "_claim_junction_guard",
    "_selected_hints",
    "_set_source_hint",
    "_stake",
    "_unstake",
    "_prebuilt_branch_port_uncached",
    "_prebuilt_source_starts",
    "_ends",
    "_future_source_offers",
    "_analytic_ordinary",
    "_ordinary_query_deadline",
    "_search",
    "_preserves_source_frontier",
    "_search_route",
    "_route_order",
    "_endpoint_dependents",
    "_dependency_closure",
)


def test_the_search_cluster_is_methods() -> None:
    missing = [name for name in SEARCH_METHODS if name not in vars(domain._RouteAllRun)]
    assert missing == [], missing


def test_the_late_search_fields_are_unset_until_the_loop_binds_them() -> None:
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    for name in ("route_distance", "source_family", "source_family_distance", "priority"):
        with pytest.raises(AttributeError):
            getattr(run, name)


def test_the_three_per_run_caches_do_not_outlive_the_run() -> None:
    """Two `_route_all` calls must not share a shape cache.

    `_junction_stacks_collide` reaches `_building_collider_hits`, which three
    tests monkeypatch; a process-lifetime cache would serve pre-patch verdicts.
    """
    canvas, nets, bounds = _corridor()
    domain._route_all(canvas, nets, 2003, 37, bounds, budget=WorkBudget(left=100_000))
    for name in ("_junction_stacks_collide", "_prebuilt_branch_port", "_prebuilt_path_port"):
        attribute = vars(domain._RouteAllRun).get(name)
        assert attribute is None or not hasattr(attribute, "cache_info"), (
            f"{name} must be built per run in the prologue, not cached on the class"
        )


def test_the_proposal_deadline_narrowing_is_still_conditional_and_nested() -> None:
    """`admit_source_family` is created only when a sibling is unrouted."""
    source = SRC.read_text()
    assert "def admit_source_family(" in source
    assert "admit_proposal = admit_source_family" in source
    assert "raise _GeometricDeadline from error" in source
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py -k "search_cluster or late_search or per_run_caches"; echo "exit=$?"`
Expected: non-zero exit, the 22 method names listed as missing.

- [ ] **Step 3: Add the 42 fields**

Append the 40 captured names listed in **Interfaces** to `_RouteAllRun` under the "bound once in the prologue" comment, each `field(init=False)` with no default, each annotation copied from its current binding site with Serena (never re-derived). Group them with four one-line comments matching where they are bound: the grid block (`:6777-6779`: `grid`, `corridor_reservations`), the net-grouping block (`:6786-6818`: `dst_group`, `src_group`, `src_group_set`, `own_source_nets`, `hinted_to`), the staking block (`:7067-7087`: `owned_source_starts`, `source_hint`, `sink_hint`, `rejected_starts`, `rejected_goals`, `rejected_path_cells`, `rejected_source_hints`, `rejected_sink_hints`, `guard_claims`, `path_guards`, `planned_taps`, `source_access_walls`, `destination_access_walls`, `source_access_blockers`, `permanent_guard`, `path_tap`), and the late block (`:9451-9462`: `route_distance`, `source_family`, `source_family_distance`, `priority`). The remainder (`belt_id`, `belt_model`, `bounds`, `junction_frame_bans`, `flow_limits`, `prioritize_source_families`) are `_route_all` parameters and take their annotations from the signature at `:6208-6221`; `paths` is `StakedPaths` from `:6282`, `history` `dict[Cell, float]` from `:6272`, `junction_obstacle_span` from `:6249-6258`, `junction_ok`/`admission_memo`/`junction_reservation_blockers` from `:6845-6858`.

Then add the two cache callables:
```python
    #: Built per run in the prologue, never `@cache` on the class: a class-level
    #: cache outlives the pass, keeps the canvas alive, and would serve verdicts
    #: computed before a test patched `_building_collider_hits`.
    _junction_stacks_collide: Callable[[Cell, Cell], bool] = field(init=False)
    _prebuilt_path_port: Callable[..., int | None] = field(init=False)
    _prebuilt_branch_port: Callable[[PlacedBuilding, PlacedBuilding, Cell], int | None] = field(
        init=False
    )
```

- [ ] **Step 4: Move the 22 bodies**

Same four-step recipe as Task 2 step 4, one closure per edit, in source order from `:6864` to `:8301`, running the golden test after each. Nested defs move with their owner and stay nested: `remember` inside `_can_junction`; `prebuilt_starts`, `admit_witness`, `frontier_junctionable` inside `_ends`; `probe_ordinary` inside `_search`; `admit_source_family` inside `_search_route`. Inside a nested def, a former capture of a `_route_all` local becomes `self.<name>` — the enclosing method's `self` is in scope, so nothing else changes.

For the two `@cache` closures, the body becomes `_junction_stacks_collide_uncached` / `_prebuilt_branch_port_uncached` and the prologue adds, right where the closure used to be defined:
```python
    run._junction_stacks_collide = cache(run._junction_stacks_collide_uncached)
    run._prebuilt_path_port = lru_cache(maxsize=None)(splitter_ports.expected_path_port)
    run._prebuilt_branch_port = cache(run._prebuilt_branch_port_uncached)
```
with the three fields declared as `Callable[..., …] = field(init=False)`. `_prebuilt_branch_port_uncached` calls `self._prebuilt_path_port`, which the line above it has already bound.

- [ ] **Step 5: Prove no global became a field**

```
grep -n "self\._expired\|self\._geometric_search\|self\._make_grid\|self\._merge_frontier\|self\._MAX_SEARCH_WORK\|self\._route_box\|self\._canvas_span\|self\._reserve_port_access" src/flab2bp/layout/routing_domain.py
```
Expected: zero hits.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py tests/test_one_deadline_rule.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py tests/layout/test_technology_routing_search.py tests/layout/test_route_kernel.py tests/layout/test_physical_flow.py; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first and record the figure.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control. This is the task most likely to move the router; if `route_bench.py` says `DIFFER`, bisect by reverting the individual closure moves from step 4 in reverse order.

- [ ] **Step 9: Commit**

`Lift _route_all's search cluster onto the run object`, body naming the three per-run caches and why they are built in the prologue.

---

### Task 4: Lift the commit cluster

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — `_RouteAllRun` (add 31 fields and 6 methods); `_route_all` at `:6413-6724` and `:9598-9661`
- Test: `tests/layout/test_route_all_run.py` (add)

**Interfaces:**
- Consumes: `_RouteAllRun` with the vocabulary and search methods.
- Produces, as methods on `_RouteAllRun`: `_budget_result`, `_commit_selection`, `_finish`, `_commit_once`, `_terminal_attempt`, `_retain_commit_failures`. **12 new fields**, all `field(init=False)` with no default: `best_attempt`, `best_failures`, `best_path_taps`, `best_paths`, `best_sink_hints`, `best_source_hints`, `destination_canvas`, `iterations`, `proposals`, `proved_stranded`, `round_work`, `settle`; plus `retained_failures: dict[int, NetFailure]` and `retained_blockers: dict[int, tuple[NetId, ...]]` (see the Risk line) — 14 declared. The other 21 of this cluster's 35 captures are fields Tasks 1 to 3 already added.

**Risk:** the default-argument snapshot in `retain_commit_failures`. At `:9608-9613` the closure declares
```python
                *,
                retained_failures: dict[int, NetFailure] = round_failures,
                retained_blockers: dict[int, tuple[NetId, ...]] = search_blockers,
```
The defaults bind the **objects that existed when the `def` executed**, which is once per round at `:9608`, after `round_failures` is rebound at `:9583`. `round_failures` is rebound again at `:9829` and `search_blockers` at `:9484`. A method reading `self.round_failures` would read the *current* object at call time, so a call that happens after `:9829` in the same round would mutate a different dict. Preserve the snapshot explicitly: at `:9608`, where the `def` used to be, write
```python
            run.retained_failures = run.round_failures
            run.retained_blockers = run.search_blockers
```
and have the method read `self.retained_failures` / `self.retained_blockers`. That is the same object identity the default argument captured, at the same point in the round, and now it is visible instead of hidden. Pinned by `test_retain_commit_failures_uses_the_rounds_snapshot_not_the_live_dict`.

Secondary: `commit_once`, `terminal_attempt` and `retain_commit_failures` are the only three closures defined **inside** the round loop, so they are re-created every iteration. As methods they exist once; the two snapshot assignments above are what re-create the per-round part. Nothing else in their bodies is round-scoped — verify with Serena `find_symbol(..., include_body=True)` before moving, and if any other name turns out to be round-scoped, snapshot it the same way and say so in the commit message.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_all_run.py`:
```python
def test_the_commit_cluster_is_methods() -> None:
    lifted = {
        "_budget_result",
        "_commit_selection",
        "_finish",
        "_commit_once",
        "_terminal_attempt",
        "_retain_commit_failures",
    }
    missing = sorted(lifted - set(vars(domain._RouteAllRun)))
    assert missing == [], missing


def test_retain_commit_failures_uses_the_rounds_snapshot_not_the_live_dict() -> None:
    """The default-argument snapshot became two explicit per-round fields.

    `round_failures` is rebound at routing_domain.py:9829, after the point the
    closure's default bound it. Reading the live field instead would write a
    round's commit evidence into the wrong dict.
    """
    source = SRC.read_text()
    assert "run.retained_failures = run.round_failures" in source
    assert "run.retained_blockers = run.search_blockers" in source
    assert "retained_failures: dict[int, NetFailure] = round_failures" not in source

    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    first: dict[int, object] = {}
    run.round_failures = first
    run.retained_failures = run.round_failures
    run.round_failures = {}
    assert run.retained_failures is first
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py -k "commit_cluster or rounds_snapshot"; echo "exit=$?"`
Expected: non-zero exit, the six method names listed as missing.

- [ ] **Step 3: Add the 14 new fields**

Append to `_RouteAllRun` as `field(init=False)` with no default, annotations copied from `:6247` (`destination_canvas`), `:6284` (`iterations`), `:6326-6331` (`best_paths`, `best_failures`, `best_source_hints`, `best_path_taps`, `best_sink_hints`, `round_work`), `:6333` (`proposals`), `:8817` (`proved_stranded`), `:6336` (`best_attempt`), the `settle` parameter at `:6220`, and the two `retained_*` snapshots. `iterations`, the six `best_*` and `round_work` are rebound in the round loop (`:9474`, `:9475`, `:9885-9904`); those rebinds become `run.<name> = …` at the same lines.

- [ ] **Step 4: Move the six bodies**

Same recipe as Task 2 step 4, in source order: `_budget_result` `:6413`, `_commit_selection` `:6502`, `_finish` `:6616`, then the three per-round closures at `:9598`, `:9601`, `:9608`. For the last three, replace the three `def`s with the two snapshot assignments from the Risk line and three aliases (`commit_once = run._commit_once`, and so on) at the same place inside the loop body — the aliases keep the call sites in `_complete_source_dependents` and `_last_mile` working until Task 7.

`_budget_result` and `_finish` call `self._last_mile_report()`, lifted in Task 2; `_commit_selection` calls `self._failure`, `self._net_id`, `self._selection_key`, all lifted in Task 2; `_commit_selection` also calls the module global `_commit_paths`, which six tests monkeypatch — leave it bare.

- [ ] **Step 5: Prove the snapshot is the only per-round state**

```
grep -n "run.retained_failures\|run.retained_blockers\|round_failures\|search_blockers" src/flab2bp/layout/routing_domain.py | awk -F: '$1>=9460 && $1<=9940'
```
Read every hit. The two `retained_*` assignments must sit between `:9583`'s rebind and the first call of `run._retain_commit_failures`, and no other per-round object may be read through a live field by a method defined outside the loop.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py tests/test_one_deadline_rule.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py tests/layout/test_physical_flow.py tests/layout/hierarchy; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 9: Commit**

`Lift _route_all's commit cluster onto the run object`, body quoting `:9608-9613` and naming the snapshot that replaced the default arguments.

---

### Task 5: Lift the repair cluster

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — `_RouteAllRun` (add 19 fields, 1 method with 6 nested helpers); `_route_all` at `:8303-8797`
- Test: `tests/layout/test_route_all_run.py` (add)

**Interfaces:**
- Consumes: `_RouteAllRun` with the vocabulary, search and commit methods.
- Produces: `_RouteAllRun._repair` with `_repair`'s current signature minus captures. All 19 of this cluster's captured names are fields Tasks 2 to 4 already added; this task adds **no** new field. Confirm that with `grep` before writing code, and if a name turns out to be missing, add it as `field(init=False)` with no default and say so in the commit message.

**Risk:** two of them.

*The deadline restore.* `_grouped_overcap_alternative` (`:8441-8566`) declares `nonlocal deadline, work`, snapshots `saved_deadline = deadline` at `:8456`, narrows `deadline` to `min(deadline, query_deadline)` at `:8459` and restores it in `finally: deadline = saved_deadline` at `:8565`. The narrowed clock is read by `_expired(deadline)` at `:8464` and `:8475` and by everything the nested search reaches. As a field that becomes `run.deadline`, and the restore must stay a `finally:` restore of `run.deadline` from the same local snapshot. Moving the restore out of `finally:`, or replacing it with a per-call argument, changes which clock a later check reads on the exception path — the exact hazard the Global Constraints name. Pinned by `test_the_two_deadline_restores_are_still_finally_clauses` (Task 1) and by the corpus gate.

*The second default-argument snapshot.* `admit_repair_tap` (`:8591-8608`) binds `index`, `victims` and `dependents` as default arguments — locals of `_repair`, not of `_route_all`. Because `admit_repair_tap` stays nested inside the `_repair` method, its defaults keep binding the enclosing method's locals at the same point and nothing changes. Do not lift it, and do not convert its defaults to `self.` reads. Pinned by `test_admit_repair_tap_keeps_its_default_argument_snapshot`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_all_run.py`:
```python
def test_the_repair_cluster_is_a_method_with_its_helpers_nested() -> None:
    assert callable(vars(domain._RouteAllRun).get("_repair"))
    repair = _method_ast("_repair")
    nested = {
        node.name
        for node in ast.walk(repair)
        if isinstance(node, ast.FunctionDef) and node is not repair
    }
    assert nested == {
        "_tap_guard_victims",
        "_source_tap_guard_victims",
        "_refresh_repair_guards",
        "_rebuild_route",
        "_grouped_overcap_alternative",
        "admit_repair_tap",
    }, sorted(nested)


def _method_ast(name: str) -> ast.FunctionDef:
    tree = ast.parse(SRC.read_text(), filename=str(SRC))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_RouteAllRun":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return child
    raise AssertionError(f"_RouteAllRun.{name} is missing")


def test_admit_repair_tap_keeps_its_default_argument_snapshot() -> None:
    """Its defaults bind `_repair`'s locals, which stay locals of the method."""
    source = SRC.read_text()
    assert "def admit_repair_tap(" in source
    assert "self.index" not in source, "index is a _repair local, never a run field"


def test_the_repair_cluster_added_no_new_field() -> None:
    from dataclasses import fields

    names = {field.name for field in fields(domain._RouteAllRun)}
    assert "repair_guards" in names
    assert "policy_restricted" in names
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py -k "repair_cluster or admit_repair_tap"; echo "exit=$?"`
Expected: non-zero exit, `AssertionError: _RouteAllRun._repair is missing`.

- [ ] **Step 3: Confirm every captured name is already a field**

```
./.venv/bin/python3.14 - <<'PY'
import ast, pathlib, symtable
path = pathlib.Path("src/flab2bp/layout/routing_domain.py")
source = path.read_text()
table = symtable.symtable(source, str(path), "exec")
def find(scope, name):
    for child in scope.get_children():
        if child.get_name() == name and child.get_type() == "function":
            return child
        found = find(child, name)
        if found:
            return found
run = find(table, "_route_all")
repair = find(run, "_repair")
outer = {s.get_name() for s in run.get_symbols() if s.is_local() or s.is_parameter()}
print(sorted({s.get_name() for s in repair.get_symbols() if s.is_free()} & outer))
PY
```
Every printed name must already be a `_RouteAllRun` field or a sibling method. Anything else is a new field; add it before step 4.

- [ ] **Step 4: Move the body**

One edit. Read `_repair` with Serena `find_symbol(name_path="_route_all/_repair", relative_path="src/flab2bp/layout/routing_domain.py", include_body=True)`, paste it into `_RouteAllRun` as a method with `self`, prefix each captured name with `self.`, keep all six nested defs nested and their default arguments untouched, keep `_expired`, `_REPAIR_MAX_VICTIMS` and every other module global bare, and replace the closure with `_repair = run._repair`.

- [ ] **Step 5: Prove the deadline restore survived**

```
grep -n "saved_deadline" src/flab2bp/layout/routing_domain.py
```
Expected: exactly three hits — the snapshot, the `finally:` restore, and nothing else. The restore must read `run.deadline = saved_deadline` (or `self.deadline = saved_deadline` inside the method) inside a `finally:` block.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py tests/test_one_deadline_rule.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py tests/layout/test_physical_flow.py; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 9: Commit**

`Lift _route_all's repair cluster onto the run object`.

---

### Task 6: Lift the last-mile cluster

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — `_RouteAllRun` (add up to 4 fields and 16 methods); `_route_all` at `:8839-9449`
- Test: `tests/layout/test_route_all_run.py` (add)

**Interfaces:**
- Consumes: `_RouteAllRun` with the vocabulary, search, commit and repair methods.
- Produces, as methods: `_restrict_proposal`, `_cluster_offers`, `_cluster_search`, `_pass_budget_left`, `_cluster_environment`, `_source_is_junctionable`, `_capture`, `_round_state`, `_restore_staked`, `_tally`, `_solve_cluster`, `_cluster_is_sibling_free`, `_relaxed_cluster_result`, `_record_cluster_relation`, `_complete_source_dependents`, `_last_mile`. **5 new fields**, all `field(init=False)` with no default: `round_failures`, `search_blockers`, `search_failures` (rebound once per round at `:9483-9485`, and `round_failures` again at `:9583` and `:9829`), `pressure` (`:9481`) and `blame` (`:9490`). The other 42 of this cluster's 47 captures are fields Tasks 1 to 4 already added.

**Risk:** the `last_mile` ledger view, which is a callable in one place and an `int` in the other, one line apart in shape. `_cluster_environment` (`:8948-8956`) passes `budget_left=_pass_budget_left` — the closure **object**, matching `last_mile.ClusterEnvironment.budget_left: Callable[[], int]` (`last_mile.py:394`), read three times inside `last_mile.py:461-481` while the cluster search spends the ledger. `_capture` (`:8978-9014`) passes `budget_left=_pass_budget_left()` — the closure **called**, an `int` snapshot for the recorded `ClusterProblem` (`last_mile.py:582`). After the lift these become `budget_left=self._pass_budget_left` and `budget_left=self._pass_budget_left()` respectively. Dropping or adding a pair of parentheses here type-checks in neither direction under mypy strict, but a `lambda: …` wrapper introduced "for clarity" at `:8952` would type-check and would change nothing — while replacing `:8952` with a call would silently freeze the allowance and turn a bounded cluster search into an unbounded one. `budget_floor=last_mile_floor` is an `int` at both sites and stays one, becoming `self.last_mile_floor`. Pinned by `test_the_cluster_environment_passes_the_ledger_view_not_a_reading`.

Secondary: `_last_mile` is the single largest consumer, capturing 44 outer names and calling ten sibling closures across four clusters plus the three per-round commit methods. It moves last, after every callee is a method, so `self._x` resolves for all of them.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_all_run.py`:
```python
LAST_MILE_METHODS = (
    "_restrict_proposal",
    "_cluster_offers",
    "_cluster_search",
    "_pass_budget_left",
    "_cluster_environment",
    "_source_is_junctionable",
    "_capture",
    "_round_state",
    "_restore_staked",
    "_tally",
    "_solve_cluster",
    "_cluster_is_sibling_free",
    "_relaxed_cluster_result",
    "_record_cluster_relation",
    "_complete_source_dependents",
    "_last_mile",
)


def test_the_last_mile_cluster_is_methods() -> None:
    missing = [name for name in LAST_MILE_METHODS if name not in vars(domain._RouteAllRun)]
    assert missing == [], missing


def test_route_all_has_no_closures_left_except_the_six_nested_helpers() -> None:
    """Everything liftable is lifted; what remains is nested inside a method."""
    remaining = sorted(
        node.name
        for node in ast.iter_child_nodes(_route_all_node())
        if isinstance(node, ast.FunctionDef)
    )
    assert remaining == [], remaining


def _budget_left_argument(method: str) -> ast.expr:
    for node in ast.walk(_method_ast(method)):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "budget_left":
                    return keyword.value
    raise AssertionError(f"_RouteAllRun.{method} passes no budget_left")


def test_the_cluster_environment_passes_the_ledger_view_not_a_reading() -> None:
    """`ClusterEnvironment.budget_left` is `Callable[[], int]`; `ClusterProblem`'s is `int`.

    last_mile.py:461-481 calls the environment's view three times while the
    cluster search spends the ledger. A reading frozen at construction turns a
    bounded search into an unbounded one.
    """
    environment = _budget_left_argument("_cluster_environment")
    assert isinstance(environment, ast.Attribute) and environment.attr == "_pass_budget_left", (
        "pass the bound method itself, not a reading of it"
    )

    problem = _budget_left_argument("_capture")
    assert isinstance(problem, ast.Call), "the recorded problem keeps its int snapshot"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py -k "last_mile_cluster or ledger_view or no_closures_left"; echo "exit=$?"`
Expected: non-zero exit, the 16 method names listed as missing and `test_route_all_has_no_closures_left_except_the_six_nested_helpers` listing the 16 that remain.

- [ ] **Step 3: Add the five round-scoped fields**

```python
    #: Rebound once per round in the loop body; declared with no default so a
    #: read before the first round is an AttributeError, as it was a NameError.
    search_failures: dict[int, _PathSearchResult] = field(init=False)
    search_blockers: dict[int, tuple[NetId, ...]] = field(init=False)
    round_failures: dict[int, NetFailure] = field(init=False)
    pressure: float = field(init=False)
    blame: dict[Cell, float] = field(init=False)
```
Annotations copied from `:9483-9485`, `:9481` and `:9490`. Their per-round rebinds at `:9481`, `:9483-9485`, `:9490`, `:9583` and `:9829` become `run.<name> = …` at the same lines. Task 4's `retained_*` snapshots must still be taken after `:9583`'s rebind — re-read that block before editing.

- [ ] **Step 4: Move the 16 bodies**

Same recipe as Task 2 step 4, in source order from `:8839` to `:9449`, `_last_mile` last, running the golden test after each. At `:8952` `budget_left=_pass_budget_left` becomes `budget_left=self._pass_budget_left` (no parentheses); at `:9003` `budget_left=_pass_budget_left()` keeps its parentheses and becomes `budget_left=self._pass_budget_left()`. `budget_floor=last_mile_floor` becomes `budget_floor=self.last_mile_floor` at both `:8953` and `:9004`.

- [ ] **Step 5: Prove nothing reads a stale ledger**

```
grep -n "budget_left=\|budget_floor=\|self.left\|run.left" src/flab2bp/layout/routing_domain.py
```
Read every hit. Exactly one `budget_left=` must be parenthesis-free (the environment's view) and exactly one must be a call (the recorded problem); both `budget_floor=` must be `self.last_mile_floor`.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py tests/test_one_deadline_rule.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_routing_lifecycle.py tests/layout/test_last_mile.py tests/layout/test_physical_flow.py tests/layout/hierarchy; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first and record the figure.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 9: Commit**

`Lift _route_all's last-mile cluster onto the run object`.

---

### Task 7: Delete the closure shells

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py` — delete the 55 bound-method aliases in `_route_all` and rewrite their call sites
- Test: `tests/layout/test_route_all_run.py` (add)

**Interfaces:**
- Consumes: `_RouteAllRun` with all 55 methods.
- Produces: `_route_all` as a thin driver — the prologue that fills the fields, the round loop, and `run._x(...)` at every call site. `_route_all`'s own signature, name and return type are unchanged; `dsp/registry.py:1229` names it as a string and must keep resolving.

**Risk:** an alias that is passed as a value, not called. Serena `rename_symbol` will not find these and a mechanical `_ends(` → `run._ends(` rewrite misses them. Three shapes to search for: an alias passed as an argument (`admit_proposal=admit_source_family` at `:8218` is the in-method case; `_stake`, `_unstake` and `_cluster_search` are the likely outer ones), an alias stored in a dict or tuple, and an alias compared with `is`. A bound method compares unequal to a freshly-bound one (`run._ends is run._ends` is `False`), so any `is` comparison against an alias is a latent bug that only appears after this task. Pinned by `test_no_alias_survives_and_no_bound_method_is_compared_by_identity`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_all_run.py`:
```python
def test_no_alias_survives_and_no_bound_method_is_compared_by_identity() -> None:
    node = _route_all_node()
    aliases = [
        f"{stmt.lineno}: {ast.unparse(stmt)}"
        for stmt in ast.walk(node)
        if isinstance(stmt, ast.Assign)
        and isinstance(stmt.value, ast.Attribute)
        and isinstance(stmt.value.value, ast.Name)
        and stmt.value.value.id == "run"
        and stmt.value.attr.startswith("_")
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
    ]
    assert aliases == [], "call run._x(...) directly: " + "; ".join(aliases)

    identity = [
        f"{cmp.lineno}: {ast.unparse(cmp)}"
        for cmp in ast.walk(node)
        if isinstance(cmp, ast.Compare)
        and any(isinstance(op, (ast.Is, ast.IsNot)) for op in cmp.ops)
        and any(
            isinstance(side, ast.Attribute)
            and isinstance(side.value, ast.Name)
            and side.value.id == "run"
            and callable(getattr(domain._RouteAllRun, side.attr, None))
            for side in [cmp.left, *cmp.comparators]
        )
    ]
    assert identity == [], "a bound method is a fresh object each access: " + "; ".join(identity)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_all_run.py -k "no_alias_survives"; echo "exit=$?"`
Expected: non-zero exit, listing the 55 alias assignments.

- [ ] **Step 3: Find every non-call use before rewriting**

```
./.venv/bin/python3.14 - <<'PY'
import ast, pathlib
path = pathlib.Path("src/flab2bp/layout/routing_domain.py")
tree = ast.parse(path.read_text())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_route_all")
aliases = {
    stmt.targets[0].id
    for stmt in ast.walk(node)
    if isinstance(stmt, ast.Assign)
    and len(stmt.targets) == 1
    and isinstance(stmt.targets[0], ast.Name)
    and isinstance(stmt.value, ast.Attribute)
}
called = {
    n.func.id
    for n in ast.walk(node)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
}
for n in ast.walk(node):
    if isinstance(n, ast.Name) and n.id in aliases and isinstance(n.ctx, ast.Load):
        parent_call = False
        for candidate in ast.walk(node):
            if isinstance(candidate, ast.Call) and candidate.func is n:
                parent_call = True
        if not parent_call:
            print("value use:", n.lineno, n.id)
PY
```
Every printed line is an alias used as a value. Rewrite each to `run._x` (still a bound method, still callable) and record the list in the commit message.

- [ ] **Step 4: Delete the aliases and rewrite the calls**

Delete the 55 alias assignments. For each, rewrite its call sites inside `_route_all` from `_x(` to `run._x(`. Do this one alias at a time, running the golden test after each. The order does not matter; there is no dependency between aliases.

Inside a method, a sibling call is already `self._x(...)` from Tasks 2 to 6 and does not change.

- [ ] **Step 5: Prove `_route_all` shrank to a driver**

```
./.venv/bin/python3.14 -c "
import ast, pathlib
path = pathlib.Path('src/flab2bp/layout/routing_domain.py')
tree = ast.parse(path.read_text())
run = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == '_RouteAllRun')
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_route_all')
print('_RouteAllRun', run.end_lineno - run.lineno + 1, 'lines,', sum(isinstance(c, ast.FunctionDef) for c in run.body), 'methods')
print('_route_all', fn.end_lineno - fn.lineno + 1, 'lines,', sum(isinstance(n, ast.FunctionDef) for n in ast.walk(fn)) - 1, 'nested defs')
"
```
Expected: `_route_all` under 500 lines with 0 nested defs; `_RouteAllRun` carrying 55 methods. Paste the output into the commit message.

- [ ] **Step 6: Prove the registry string still resolves**

```
grep -n "_route_all\|_geometric_search" src/flab2bp/dsp/registry.py
./.venv/bin/python3.14 -c "from flab2bp.layout import routing_domain; assert callable(routing_domain._route_all); assert callable(routing_domain._geometric_search); print('ok')"
```

- [ ] **Step 7: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests; echo "exit=$?"`
Expected: `exit=0`. This is the first full-suite run in the plan; check `vmstat` first and record the figure and the wall time.

- [ ] **Step 8: Lint gate**

- [ ] **Step 9: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 10: Commit**

`Call the run object's methods directly and drop the closure aliases`.

---

### Task 8: One flat-index codec in the geometric search

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:4979`, `:4983`, `:5002-5003`, `:5045-5047`, and `_Grid.refresh_history` at `:4684-4686`
- Test: `tests/layout/test_route_kernel.py` (add)

**Interfaces:**
- Consumes: `flab2bp.layout.geometric_world.GridIndex` (`encode`/`decode`) and `_Grid.codec`, both already in the tree.
- Produces: no new name. `_geometric_search` and `_Grid.refresh_history` stop spelling the x-major formula themselves.

**Risk:** this is Plan A's parked item and it is on the **router hot path**, so it is a measured change, not an assumed one. `:5002` encodes once per start and `:5045-5047` decodes once per path cell; replacing inline arithmetic with a `GridIndex` method call adds one Python call per cell. `GridIndex` is a frozen dataclass without `slots`, and `flat.codec` is an attribute load per call. If `scripts/route_bench.py`'s wall time moves more than 3 percent against the pre-change run, keep the inline arithmetic at `:5002` and `:5045-5047`, convert only `:4979`, `:4983` and `:4684-4686` (which run once per extra edge and once per rip-up round, not per cell), and record the measurement and the decision in the commit message. The spec asks for one codec, not for a slower router; a documented partial conversion with a number beside it satisfies it and an unmeasured full conversion does not.

The correctness hazard is narrower and absolute: `GridIndex.encode` does **not** bounds-check, while `_Grid.index` raises `IndexError` for a cell outside the domain. `:5002`'s comprehension is reached only after `:4957-4963` has filtered starts to the span, so `encode` is the right call there; `flat.index(...)` at `:4994`, `:4998`, `:5067`, `:5084`, `:5087`, `:5090` must keep raising and must not be converted.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_kernel.py`:
```python
def test_the_grid_codec_round_trips_every_cell_the_search_encodes() -> None:
    """The search's flat index and `GridIndex` are the same arithmetic.

    routing_domain spelled the x-major formula inline at four sites inside
    `_geometric_search`. `GridIndex` documents itself as the one place that
    knows the layout; this pins that the two agree before they are merged.
    """
    from flab2bp.layout.geometric_world import GridIndex

    codec = GridIndex(gx0=-3, gy0=5, rows=7, levels=4)
    for x in range(-3, 4):
        for y in range(5, 12):
            for level in range(4):
                index = codec.encode((x, y, level))
                assert codec.decode(index) == (x, y, level)
                assert index == ((x + 3) * 7 + (y - 5)) * 4 + level


def test_the_geometric_search_spells_the_index_once() -> None:
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout"
    text = (source / "routing_domain.py").read_text()
    tree = ast.parse(text)
    search = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_geometric_search"
    )
    body = ast.unparse(search)
    assert "divmod(source // levels, gh)" not in body
    assert "divmod(target // levels, gh)" not in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_kernel.py -k "grid_codec or spells_the_index"; echo "exit=$?"`
Expected: non-zero exit on `test_the_geometric_search_spells_the_index_once`; the round-trip test passes already and is the agreement guard.

- [ ] **Step 3: Measure the router before the change**

```
taskset -c 96-127 env PYTHONHASHSEED=0 ./.venv/bin/python3.14 -m timeit -n 1 -r 3 -s "import subprocess" "subprocess.run(['./.venv/bin/python3.14','scripts/route_bench.py'],capture_output=True)"
```
Record the figure and the `vmstat` reading beside it. This is the baseline the Risk line's 3 percent is measured against.

- [ ] **Step 4: Convert the two once-per-edge decodes and the history flattener**

```python
# routing_domain.py:4979 and :4983
        source_x, source_y, _ = flat.codec.decode(source)
            target_x, target_y, _ = flat.codec.decode(target)

# routing_domain.py:4684-4686, inside _Grid.refresh_history
        for cell, used in history.items():
            cx, cy, clvl = cell
            if lo_x <= cx <= hi_x and lo_y <= cy <= hi_y and 0 <= clvl < self.levels:
                flat[self.codec.encode(cell)] = used
```
`decode` returns a 3-tuple, so the `_` is required; the two call sites use only `x` and `y`.

- [ ] **Step 5: Convert the two per-cell sites and measure again**

```python
# routing_domain.py:5002-5003
    start_indices = [flat.codec.encode(s) for s in starts]

# routing_domain.py:5045-5047
        cells = [flat.codec.decode(index) for index in path_indices]
```
Re-run step 3's measurement. If the figure moved more than 3 percent, revert these two hunks, keep step 4's, and record both numbers and the decision in the commit message.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_kernel.py tests/layout/test_technology_routing_search.py tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_route_all_run.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 9: Commit**

`Let the geometric search share the grid's one flat-index codec`, body carrying both timing figures.

---

### Task 9: One gate behind `_Canvas.free` and `free_owned_guard`

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:2859-2888` (`_Canvas.free`), `:2919-2939` (`_Canvas.free_owned_guard`)
- Test: `tests/layout/test_routing_integration.py` (add)

**Interfaces:**
- Consumes: nothing new.
- Produces: one private helper on `_Canvas`, with the two public twins keeping their names, signatures and docstrings:
```python
def _free_outside_the_guard(self, cell: Cell, *, belt: bool) -> bool:
    """Every refusal the two `free` gates share, in the order they check them."""
```
`free(cell, *, belt: bool = True)` and `free_owned_guard(cell)` each keep their own guard clause and delegate the rest. This is the second item Plan A parked; it is on the routing hot path (93 `.free(`/`free_owned_guard` call sites across src, scripts and tests).

**Risk:** the two twins are **not** the same function with a flipped sign, and a careless merge changes three things at once.
1. `free` refuses when `cell in self.guard`; `free_owned_guard` refuses when `cell not in self.guard`. Opposite senses of the same set.
2. `free` applies `belt_keepout` only when `belt` is true; `free_owned_guard` applies it **unconditionally** and takes no `belt` argument. The helper therefore needs the `belt` keyword and `free_owned_guard` passes `belt=True`.
3. `free` checks `blocked`/`keep_out` **before** `guard`; `free_owned_guard` checks `guard` membership and `blocked`/`keep_out` in one `or` chain. Both are short-circuit refusals with no side effects, so the order is unobservable — but only because neither `self.blocked`, `self.keep_out`, `self.belt_ban`, `self.belt_keepout`, `self.limit` nor `self.reserved` has a `__contains__` or `get` with a side effect. Verify that with Serena before merging: they are a `set`, a `set`, two `dict`s, a tuple and a `dict` on a plain dataclass. Record the verification in the commit message.
Pinned by `test_the_two_free_gates_disagree_only_about_the_guard`.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_routing_integration.py`:
```python
def test_the_two_free_gates_disagree_only_about_the_guard() -> None:
    """`free` and `free_owned_guard` share every refusal but the guard set.

    They were two hand-copied gates. A merged helper must keep `free`'s
    `belt=False` escape (which drops `belt_keepout` only) and must keep
    `free_owned_guard` applying `belt_keepout` unconditionally.
    """
    bounds = (0, 0, 4, 4)
    canvas = domain._Canvas(limit=bounds)
    inside = [(x, y, z) for x in range(5) for y in range(5) for z in range(canvas.levels)]

    for cell in inside:
        assert not (canvas.free(cell) and canvas.free_owned_guard(cell)), cell

    guarded = (2, 2, 0)
    canvas.guard.add(guarded)
    assert canvas.free(guarded) is False
    assert canvas.free_owned_guard(guarded) is True

    canvas.blocked.add(guarded)
    assert canvas.free(guarded) is False
    assert canvas.free_owned_guard(guarded) is False
    canvas.blocked.discard(guarded)

    canvas.belt_keepout[(2, 2)] = frozenset({0})
    assert canvas.free(guarded, belt=True) is False
    assert canvas.free_owned_guard(guarded) is False, (
        "free_owned_guard applies belt_keepout unconditionally"
    )

    open_cell = (3, 3, 0)
    canvas.belt_keepout[(3, 3)] = frozenset({0})
    assert canvas.free(open_cell, belt=True) is False
    assert canvas.free(open_cell, belt=False) is True

    outside = (9, 9, 0)
    assert canvas.free(outside) is False
    canvas.guard.add(outside)
    assert canvas.free_owned_guard(outside) is False
```

- [ ] **Step 2: Run to verify it passes on the unmodified tree**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_routing_integration.py -k "two_free_gates"; echo "exit=$?"`
Expected: `exit=0`. This is a characterization test, not a red-first test: it states the behavior the merge must preserve. If any assertion fails on the unmodified tree, the assertion is wrong — fix the test to match the tree and say so in the commit message before touching `_Canvas`.

- [ ] **Step 3: Add the shared helper**

Insert immediately before `free` at `:2859`:
```python
    def _free_outside_the_guard(self, cell: Cell, *, belt: bool) -> bool:
        """Every refusal the two `free` gates share.

        `free` and `free_owned_guard` differ in exactly one clause -- whether a
        junction guard on `cell` refuses it or is the point -- and in whether a
        non-belt caller may ignore `belt_keepout`. Everything else was copied.
        """
        x, y, z = cell
        if not 0 <= z < self.levels:
            return False
        # `solid` is deliberately NOT consulted: a machine denies the levels
        # `add` wrote into `blocked`, and the ones above its collider are the
        # game's to sell.  `_make_grid` must agree, and does.
        if cell in self.blocked or (x, y) in self.keep_out:
            return False
        # `belt_ban` is the height a belt owes whatever it crosses (a Spray
        # Coater wants 1.8975); `guard`, the caller's clause, is a junction's
        # own collider. Either one alone would let the other's case through.
        if z in self.belt_ban.get((x, y), ()):
            return False
        if belt and z in self.belt_keepout.get((x, y), ()):
            return False
        if self.limit is not None:
            min_x, min_y, max_x, max_y = self.limit
            if not (min_x <= x <= max_x and min_y <= y <= max_y):
                return False
        port = self.reserved.get(cell)
        return port is None or port in self.routing_ports
```

- [ ] **Step 4: Delegate the two twins**

```python
    def free(self, cell: tuple[int, int, int], *, belt: bool = True) -> bool:
        """<the existing eight-line docstring, unchanged>"""
        return cell not in self.guard and self._free_outside_the_guard(cell, belt=belt)

    def free_owned_guard(self, cell: Cell) -> bool:
        """<the existing eight-line docstring, unchanged>"""
        return cell in self.guard and self._free_outside_the_guard(cell, belt=True)
```
Both twins' docstrings are copied verbatim; only the bodies change. Do not change either name — `free_owned_guard` is read from `projection_world.py:147` and five sites in `routing_domain`.

- [ ] **Step 5: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_routing_integration.py tests/layout/test_route_witnesses.py tests/layout/test_route_kernel.py tests/layout/test_route_all_run.py tests/layout/test_technology_routing_search.py tests/layout/test_physical_flow.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 6: Lint gate**

- [ ] **Step 7: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 8: Commit**

`Put one gate behind _Canvas.free and free_owned_guard`, body naming the three differences the merge preserves and the side-effect verification from the Risk line.

---

### Task 10: A request object for `_merge_frontier`

**Files:**
- Modify: `src/flab2bp/layout/routing_domain.py:5621-5806` (`_merge_frontier`) and its two call sites inside `_RouteAllRun._ends`
- Test: `tests/layout/test_route_witnesses.py` (add)

**Interfaces:**
- Consumes: nothing new.
- Produces:
```python
@dataclass(frozen=True, slots=True)
class _MergeFrontierRequest:
    """The 14 keyword-only inputs `_merge_frontier` reads and never rebinds."""

    provenance: dict[Cell, Cell] | None = None
    belt_prefab: tuple[int, int] | None = None
    tentative_ok: bool = False
    owned_guard: Mapping[Cell, Cell] | None = None
    primitives: RoutePrimitives | None = None
    source_choices: dict[Cell, set[Cell]] | None = None
    witness: Callable[[Cell, Cell], bool] | None = None
    admit_tap: Callable[[Cell], bool] | None = None
    deadline: float | None = None
    path_ranges: Mapping[int, tuple[int, int]] | None = None
    merged_cells: Collection[Cell] = frozenset()
    protected_sinks: Collection[Cell] = frozenset()
    source_feeds: Mapping[int, int] | None = None
    trace: list[tuple[Cell, Cell, tuple[Cell, ...]]] | None = None


def _merge_frontier(
    canvas: _Canvas,
    paths: Mapping[int, Sequence[Cell]],
    siblings: tuple[int, ...],
    junctionable: Callable[[int, int, int], bool] | None = None,
    request: _MergeFrontierRequest = _MergeFrontierRequest(),
) -> set[Cell]: ...
```
Every annotation and default above is copied verbatim from the current keyword-only parameter list at `routing_domain.py:5625-5640`. The four positional parameters and the `set[Cell]` return stay as they are. A frozen dataclass is safe as a default argument (it is immutable and shared), and both call sites override it. `_merge_frontier` keeps its name: one test monkeypatches it and tests reference it at 25 sites.

**Risk:** `frozen=True` on an object holding `deadline`. `_merge_frontier`'s `deadline` is a value passed in by `_ends`, which reads `run.deadline` at call time — and `run.deadline` is rebound mid-run at two `finally:` sites. Constructing the request **once** outside the call would freeze the clock; the request must be constructed **at each call site, in the call expression**, so `deadline=run.deadline` is read at the same moment it is read today. `merged_cells` and `path_ranges` are mutable collections the callee writes through; `frozen=True` freezes the *reference*, not the contents, which is what is wanted — but confirm with Serena `find_symbol(name_path="_merge_frontier", include_body=True)` that no parameter is *rebound* inside the body before choosing `frozen`. If one is, drop `frozen` for that object and say so. Pinned by `test_the_merge_frontier_request_is_built_at_the_call_site`.

This task is **not** routing-touching in the sense of the gate exemption — it changes a routing function's signature, so it runs the corpus gate too. It is listed after the lifts because `_ends` is a method by then and the two call sites are in one place.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_route_witnesses.py`:
```python
def test_the_merge_frontier_request_is_built_at_the_call_site() -> None:
    """`deadline` is rebound mid-run, so the request cannot be hoisted.

    routing_domain rebinds the routing deadline in two `finally:` clauses. A
    request built once and reused would carry the clock from whichever branch
    built it.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout" / "routing_domain.py"
    tree = ast.parse(path.read_text())
    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_MergeFrontierRequest"
    ]
    assert len(constructions) == 2, [node.lineno for node in constructions]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_merge_frontier"
    ]
    assert len(calls) == 2
    for call in calls:
        inline = [
            argument
            for argument in [*call.args, *(keyword.value for keyword in call.keywords)]
            if isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.func.id == "_MergeFrontierRequest"
        ]
        assert inline, f"_merge_frontier at line {call.lineno} must build its request inline"


def test_merge_frontier_takes_five_parameters() -> None:
    import inspect

    from flab2bp.layout import routing_domain

    parameters = inspect.signature(routing_domain._merge_frontier).parameters
    assert list(parameters) == ["canvas", "paths", "siblings", "junctionable", "request"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_witnesses.py -k "merge_frontier"; echo "exit=$?"`
Expected: non-zero exit, `NameError`/`AssertionError` on the zero `_MergeFrontierRequest` constructions and the 18-parameter signature.

- [ ] **Step 3: Add the dataclass and change the signature**

Insert `_MergeFrontierRequest` immediately before `_merge_frontier` at `:5621` with Serena `insert_before_symbol`, copying each of the 14 annotations from the current keyword-only parameters. Then replace the keyword-only block with `request: _MergeFrontierRequest` and prefix each use inside the body with `request.` — Serena `rename_symbol` does not do this, so use `find_symbol(..., include_body=True)` to read the body and rewrite it in one `replace_symbol_body` call, then grep each of the 14 names inside the new body to confirm none survives bare.

- [ ] **Step 4: Rewrite the two call sites inline**

Both are inside `_RouteAllRun._ends` (they were at `routing_domain.py:7567` and `:7688`, both within `_ends`' 7375-7721 span; after Task 3 find them with `grep -n "_merge_frontier(" src/flab2bp/layout/routing_domain.py`). Each becomes
```python
# the source-frontier call, routing_domain.py:7567
            frontier = _merge_frontier(
                self.canvas,
                paths,
                siblings,
                frontier_junctionable,
                _MergeFrontierRequest(
                    provenance=source_provenance,
                    belt_prefab=(self.belt_id, self.belt_model),
                    tentative_ok=tentative_ok,
                    owned_guard=owned_guard,
                    primitives=self.primitives,
                    source_choices=source_choices,
                    witness=witness,
                    admit_tap=admit_source_tap,
                    deadline=self.deadline,
                    path_ranges=source_ranges,
                    merged_cells=existing_sink_targets,
                    source_feeds=source_feeds,
                    trace=walk_offers,
                ),
            )

# the sink-frontier call, routing_domain.py:7688 -- it skips `junctionable`,
# so `request` must be passed by keyword.
        frontier = _merge_frontier(
            self.canvas,
            paths,
            self.dst_group.get(index, ()),
            request=_MergeFrontierRequest(
                provenance=sink_provenance,
                primitives=self.primitives,
                path_ranges=sink_ranges,
                protected_sinks=_protected_merge_cells(
                    paths, self.dst_group.get(index, ()), self.path_tap
                )
                | reverse_link_guard
                | self.rejected_sink_hints[index],
            ),
        )
```
Every keyword is copied unchanged from the current call; only the `self.` prefixes (added by Task 3) and the wrapping differ. `deadline=self.deadline` must be spelled at the call site; do not hoist either construction above an `if` or out of a loop.

- [ ] **Step 5: Grep for what the rename misses**

```
grep -rn "_merge_frontier(" src scripts tests --include='*.py'
grep -rn "_MergeFrontierRequest" src scripts tests --include='*.py'
grep -rn "replace(.*_MergeFrontierRequest\|replace(request" src scripts tests --include='*.py'
```
The first two must agree on the call sites; the third must be empty. The single `monkeypatch.setattr(..., "_merge_frontier", ...)` site in tests must be updated to the five-parameter shape.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_route_witnesses.py tests/layout/test_routing_integration.py tests/layout/test_route_all_run.py tests/layout/test_physical_flow.py; echo "exit=$?"`
Expected: `exit=0`.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Router gate and corpus gate** (routing-touching). Expected: `MATCH`, and identical to the control.

- [ ] **Step 9: Commit**

`Give _merge_frontier one request object instead of 14 keywords`.

---

### Task 11: A request object for `_pack_window`

**Files:**
- Modify: `src/flab2bp/layout/freeform.py:3130-3213` (`_pack_window`), `freeform.py:6438`, `src/flab2bp/layout/sequence_solver.py:5680`
- Test: `tests/layout/test_freeform.py` (add)

**Interfaces:**
- Consumes: nothing new.
- Produces:
```python
@dataclass(frozen=True, slots=True)
class _PackWindowRequest:
    """The 16 keyword-only inputs `_pack_window` reads."""

    height: int
    width_bound: int
    direct_candidates: Mapping[tuple[int, int], _DirectCandidate]
    window: frozenset[int]
    fixed_at: Mapping[int, tuple[int, int]]
    seed: routing_domain._Pack | None = None
    width_target: int | None = None
    arrangement: int = 0
    projection_no_goods: tuple[ProjectionNoGood, ...] = ()
    exact_pack_no_goods: tuple[ExactPackNoGood, ...] = ()
    direct_relation_no_goods: tuple[_DirectRelationNoGood, ...] = ()
    cluster_relation_no_goods: tuple[ClusterRelationNoGood, ...] = ()
    feedback: FeedbackState | None = None
    time_budget_s: float = C_WINDOW_SECONDS
    deterministic_work: float = C_WINDOW_DETERMINISTIC_WORK
    on_skipped: Callable[[int], None] | None = None


def _pack_window(
    strips: list[routing_domain.Strip], request: _PackWindowRequest
) -> _PackSolveOutcome | None: ...
```
Every annotation and default above is copied verbatim from the current parameter list at `freeform.py:3131-3148`. The first five have no defaults today and keep none, so they stay first in the dataclass. `time_budget_s` is a plain `float` defaulting to `C_WINDOW_SECONDS`, not `float | None`.

**Risk:** `sequence_solver.py:93` imports `_pack_window` from `freeform` by name (it is one of the 19 private names the spec's section 7 lists). The new `_PackWindowRequest` must be imported alongside it, and `sequence_solver.py:5680` must construct it **in the call expression**, because `time_budget_s` is derived from a live clock there:
```python
            time_budget_s=min(C_WINDOW_SECONDS, remaining - C_WINDOW_DEADLINE_SAFETY_SECONDS),
```
`remaining` is recomputed each ALNS iteration; a request hoisted out of the loop would pin the first iteration's allowance for every later one, which is a silent behavior change the corpus gate might not catch on a 15-second budget. Read `sequence_solver.py:5660-5700` in full with Serena before editing and keep that expression byte-identical, at the same place. `_pack_window` keeps its name: 15 sites reference it.

This task does not touch `routing_domain`, but the freeform and sequence-pair strategies both reach it, so it runs the corpus gate.

- [ ] **Step 1: Write the failing test**

Append to `tests/layout/test_freeform.py`:
```python
def test_pack_window_takes_a_request_built_at_each_call_site() -> None:
    """`time_budget_s` is derived from a live clock at both call sites."""
    import ast
    import inspect
    from pathlib import Path

    from flab2bp.layout import freeform

    assert list(inspect.signature(freeform._pack_window).parameters) == ["strips", "request"]

    layout = Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout"
    for name, expected in (("freeform.py", 1), ("sequence_solver.py", 1)):
        tree = ast.parse((layout / name).read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_pack_window"
        ]
        assert len(calls) == expected, (name, [node.lineno for node in calls])
        for call in calls:
            arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
            assert any(
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "_PackWindowRequest"
                for argument in arguments
            ), f"{name}:{call.lineno} must build its request inline"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_freeform.py -k "pack_window_takes_a_request"; echo "exit=$?"`
Expected: non-zero exit on the 17-parameter signature.

- [ ] **Step 3: Add the dataclass and change the signature**

Insert `_PackWindowRequest` immediately before `_pack_window` at `freeform.py:3130` with Serena `insert_before_symbol`, then rewrite the body with `replace_symbol_body`, prefixing each of the 16 names with `request.`. Grep each name inside the new body to confirm none survives bare.

- [ ] **Step 4: Rewrite the two call sites and the import**

`freeform.py:6438` and `sequence_solver.py:5680` each build `_PackWindowRequest(...)` inline with every keyword copied unchanged, including `time_budget_s`'s current expression. Add `_PackWindowRequest` to `sequence_solver.py:93`'s `from flab2bp.layout.freeform import …` list beside `_pack_window`.

- [ ] **Step 5: Grep for what the rename misses**

```
grep -rn "_pack_window(" src scripts tests --include='*.py'
grep -rn "_PackWindowRequest" src scripts tests --include='*.py'
grep -rn "replace(request\|replace(.*_PackWindowRequest" src scripts tests --include='*.py'
```
The third must be empty. Any test that calls `_pack_window` with keywords must be converted in this commit.

- [ ] **Step 6: Run the tests**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests/layout/test_freeform.py tests/layout/test_sequence_solver.py; echo "exit=$?"`
Expected: `exit=0`. This is the heaviest two-file run in the plan (`test_freeform.py` is 25,510 lines and `test_sequence_solver.py` 11,458); check `vmstat` first and record the figure.

- [ ] **Step 7: Lint gate**

- [ ] **Step 8: Corpus gate** (freeform and sequence-pair both route through this). Expected: identical to the control. The router gate is unaffected by this task but run it anyway and record the `MATCH`.

- [ ] **Step 9: Commit**

`Give _pack_window one request object instead of 16 keywords`.

---

### Task 12: The guard and the evidence

**Files:**
- Create: `tests/test_route_all_stays_decomposed.py`
- Modify: `docs/superpowers/specs/2026-09-13-abstraction-review.md` (status note under section 9 item 3 and item 10)

**Interfaces:**
- Consumes: everything Tasks 1 to 11 produced.
- Produces: a guard test that fails when a new closure appears inside `_route_all` or a new `nonlocal` appears in `routing_domain`.

**Risk:** a guard that is too tight. Six nested defs legitimately remain inside `_RouteAllRun` methods (`remember`, `prebuilt_starts`, `admit_witness`, `frontier_junctionable`, `probe_ordinary`, `admit_source_family`) plus six inside `_repair`, and `admit_source_family`'s existence is *load-bearing* — it is created conditionally and its `None`-ness selects the admission path. A guard that bans all nested functions in `routing_domain` would force that one to be lifted and would change behavior. The guard therefore bans nested defs inside `_route_all` only, and `nonlocal` in `routing_domain` only. Both scopes are stated in the test's docstring so a later reader does not widen it by accident.

- [ ] **Step 1: Write the guard**

`tests/test_route_all_stays_decomposed.py`:
```python
"""`_route_all` is a driver over `_RouteAllRun`, and stays one.

The 2026-09-13 abstraction review (section 9 item 3) found `_route_all` at
3,729 lines with 67 nested functions and 20 `nonlocal` statements over 23
rebound names. Plan C lifted them onto `_RouteAllRun` as fields and methods.

Scope, deliberately narrow: this bans nested functions inside `_route_all`
itself and `nonlocal` anywhere in routing_domain. It does NOT ban nested
functions inside `_RouteAllRun`'s methods -- six of them are load-bearing, and
`_search_route`'s `admit_source_family` is created conditionally so that
`admit_proposal` is `None` when no sibling is unrouted.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTING_DOMAIN = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "flab2bp"
    / "layout"
    / "routing_domain.py"
)


def _tree() -> ast.Module:
    return ast.parse(ROUTING_DOMAIN.read_text(), filename=str(ROUTING_DOMAIN))


def test_route_all_holds_no_closures() -> None:
    node = next(
        item
        for item in _tree().body
        if isinstance(item, ast.FunctionDef) and item.name == "_route_all"
    )
    offenders = [
        f"{child.lineno}: {child.name}"
        for child in ast.walk(node)
        if isinstance(child, ast.FunctionDef) and child is not node
    ]
    assert offenders == [], (
        "put it on _RouteAllRun as a method instead: " + ", ".join(offenders)
    )


def test_routing_domain_declares_no_nonlocal_name() -> None:
    offenders = [
        f"{node.lineno}: {', '.join(node.names)}"
        for node in ast.walk(_tree())
        if isinstance(node, ast.Nonlocal)
    ]
    assert offenders == [], (
        "carry rebound state on a typed field, not a nonlocal: " + "; ".join(offenders)
    )


def test_route_all_is_a_driver() -> None:
    node = next(
        item
        for item in _tree().body
        if isinstance(item, ast.FunctionDef) and item.name == "_route_all"
    )
    assert node.end_lineno is not None
    assert node.end_lineno - node.lineno + 1 < 700, (
        f"_route_all is {node.end_lineno - node.lineno + 1} lines; it was 3,729 and "
        "should now be the prologue plus the round loop"
    )
```

- [ ] **Step 2: Run it**

Run: `./.venv/bin/python3.14 -m pytest -o addopts= -q tests/test_route_all_stays_decomposed.py; echo "exit=$?"`
Expected: `exit=0`. If `test_route_all_is_a_driver` fails, raise the bound only to the measured figure from Task 7 step 5 rounded up to the next hundred, and say so in the commit message; never raise it to a number the tree does not meet.

- [ ] **Step 3: Record the outcome in the spec**

Append to `docs/superpowers/specs/2026-09-13-abstraction-review.md`, under section 9 item 3, a "Plan C outcome" note carrying: the verified counts from this plan's inventory table; the four places the spec was off (67 nested defs not 61, 20 `nonlocal` statements over 23 names not 18 over 22, `_route_all` 3,729 lines not 3,493, `_merge_frontier` 18 parameters not 17); the correction that **zero** of the 1,071 private `routing_domain` references from tests name a `_route_all` closure, so the testability claim is "one door, 51 call sites" rather than "883 private references reach the closures"; the fifth cluster (shared vocabulary) the measured call graph forced out of the spec's four; and the two hazards that shaped the design (the three per-run caches, and the two default-argument snapshots at `:9608` and `:8591`). Add a one-line note under item 10 recording that `_merge_frontier` and `_pack_window` are done and that `SequenceSolver.__init__` (26), `pipeline.build` (25) and `_record_routing_observation` (24) remain.

- [ ] **Step 4: Full suite, lint gate, final gates**

Run: `taskset -c 96-127 ./.venv/bin/python3.14 -m pytest -o addopts= -q tests; echo "exit=$?"`
Expected: `exit=0`. Check `vmstat` first and record it. Then run the router gate and the corpus gate one final time. Expected: `MATCH`, and identical to the control.

- [ ] **Step 5: Commit**

`Keep _route_all a driver over its run object`, body carrying the final `_route_all` and `_RouteAllRun` line counts and the last corpus compare.

---

## Self-review notes

**Spec coverage.** Section 9 item 3 is covered end to end: the closures become methods on `_RouteAllRun` (Tasks 2 to 6), grouped by the clusters section 2.1 measured (Task 2's vocabulary group is the one addition, forced by the call graph and justified under "Lift order"); the 22 (verified 23) `nonlocal` names become typed fields (Task 1); the work is phased, each phase landing independently behind the `route_bench` MATCH line and the corpus gate, byte-identical (every task's steps 8 and 9). Section 9 item 10's two `_route_all`-adjacent parameter objects are Tasks 10 and 11. Section 2.1's `_route_all` figures are re-measured in the inventory table. Plan A's two parked items — the index arithmetic in `_geometric_search`'s per-cell loop and the `_Canvas.free`/`free_owned_guard` twins — are Tasks 8 and 9.

**Deliberate departures from the spec, each with its reason.** (1) A fifth cluster, the shared vocabulary, is lifted before the spec's four, because `_net_id` is called from 14 closures across all four and `_finish`/`_budget_result` call `_last_mile_report`; without it every order leaves a method calling an unlifted closure. (2) `admit_source_family` and the eleven other nested helpers stay nested rather than becoming methods, because `admit_source_family` is created conditionally and its `None`-ness selects the admission path. (3) The three per-call caches become per-run attributes built in the prologue rather than `@cache` methods, because a `@cache` method is process-lifetime and `_junction_stacks_collide` reaches a monkeypatched global. (4) Task 8 is allowed to land partially, with a measurement, if the per-cell codec calls cost more than 3 percent of `route_bench` wall time; the spec asks for one codec, not a slower router.

**Could not confirm from the spec.** The spec's "tests reach the closures today only through 883 private references" is not a fact about the closures: a closure of `_route_all` is not a module attribute, and **zero** of the 1,071 private `routing_domain` references from tests name one. The 883 figure is section 7's whole-module private surface measured with a narrower pattern; the number at `7f4dff71` is 142 distinct names at 1,071 sites. The testability claim this plan can defend is "67 closures behind one door, the 51 test call sites of `_route_all`". The spec's 61 closures, 18 `nonlocal` statements, 22 names, 3,493 lines and 17-parameter `_merge_frontier` are all off by the amounts the inventory table records; the module grew between the review and `7f4dff71`.

**Type consistency.** `_RouteAllRun` is one class with one name throughout; its field set grows monotonically and the arithmetic closes: 24 (Task 1: the 23 rebound names plus `budget`) + 7 (Task 2) + 42 (Task 3: 40 captured plus 2 cache callables) + 14 (Task 4: 12 captured plus the 2 `retained_*` snapshots) + 0 (Task 5) + 5 (Task 6) = **92**, which is the 88 distinct captured names plus the 2 snapshots and the 2 cache callables. `left` is a property, not a field, and is counted nowhere. `run.left` is the `int` property defined in Task 1 and used in Tasks 1, 4 and 6; `run.budget` is the aliased `WorkBudget` and is never reassigned. `run.deadline` is `float | None` in Tasks 1, 3, 5 and 10. `_MergeFrontierRequest` (Task 10) and `_PackWindowRequest` (Task 11) each appear in exactly one task and one test. `_free_outside_the_guard(cell, *, belt: bool)` (Task 9) has one signature. Method names on `_RouteAllRun` are the closure names verbatim, with two exceptions stated where they happen: `role_rows` becomes `_role_rows`, and the two `@cache` bodies become `_junction_stacks_collide_uncached` and `_prebuilt_branch_port_uncached` with the cached callables held in same-named fields.

**Ordering.** Task 1 must precede everything (the class has to exist). Tasks 2 to 6 must run in that exact order — V, S, C, R, L — because it is the only order with no method-calls-unlifted-closure edge; the call-graph counts under "Lift order" are the evidence. Task 7 must follow Task 6 (there is nothing to de-alias until every cluster is lifted). Tasks 8 and 9 are independent of 1 to 7 and of each other and could be reviewed and rejected without blocking anything, but they are placed after Task 7 so the corpus gate is not carrying two unrelated changes at once. Task 10 is placed after Task 7 because its two call sites are inside what is by then `_RouteAllRun._ends`; Task 11 touches neither `routing_domain` nor the run object and could move anywhere after Task 1. Task 12 is last by construction.

**Reviewer-rejectable in isolation.** Every task except Task 1 can be rejected while its neighbours stand: each lift is one cluster, one commit, one gate. Task 1 is the exception — the class, the 20 deleted `nonlocal` statements and the 23 rewritten names are one commit because splitting them leaves the tree failing mypy and every routing suite.
