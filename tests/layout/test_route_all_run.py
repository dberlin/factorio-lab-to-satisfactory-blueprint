"""`_route_all`'s run state is one typed object, and the semantics are unchanged.

The 2026-09-13 abstraction review (section 9 item 3) asked for the 67 closures
to become methods on a `_RouteAllRun` grouped by cluster. This file pins the
two properties the lift must not break: a rebound name is still shared state
seen by every sibling, and a per-call reset is still per call.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from flab2bp.layout import routing_domain as domain
from flab2bp.layout.band_policy import BandPolicy
from flab2bp.layout.base import PlacedBuilding
from flab2bp.layout.budget import WorkBudget
from flab2bp.layout.route_feedback import NetId, NetRole, RouteFailureKind

SRC = Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout" / "routing_domain.py"

#: Captured from the unmodified tree at af7a395f by Task 1 step 1, three
#: identical runs. `DetailedRouteResult` has no `paths` attribute, so the
#: witness is the returned status, the routed identities, and the exact work
#: this pass charged the caller's ledger.
GOLDEN_STATUS = "ROUTED"
GOLDEN_LEFT = 99_688
GOLDEN_ROUTED = (0,)
GOLDEN_FAILURES = 0
GOLDEN_WORK = 312
GOLDEN_ITERATIONS = 1


def _route_all_node() -> ast.FunctionDef:
    tree = ast.parse(SRC.read_text(), filename=str(SRC))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_route_all":
            return node
    raise AssertionError("_route_all is gone")


def _run_class_node() -> ast.ClassDef:
    tree = ast.parse(SRC.read_text(), filename=str(SRC))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "_RouteAllRun":
            return node
    raise AssertionError("_RouteAllRun is gone")


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
        domain._Net(source, destination, "gear", net_id=NetId(0, 1, "gear", NetRole.INTERNAL, 0))
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
    assert tuple(net_id.ordinal for net_id in result.routed) == GOLDEN_ROUTED
    assert len(result.failures) == GOLDEN_FAILURES
    assert (result.work, result.iterations) == (GOLDEN_WORK, GOLDEN_ITERATIONS)


def test_route_all_declares_no_nonlocal_names() -> None:
    """All 23 rebound names live on `_RouteAllRun` instead."""
    offenders = [
        f"{node.lineno}: {', '.join(node.names)}"
        for node in ast.walk(_route_all_node())
        if isinstance(node, ast.Nonlocal)
    ]
    assert offenders == [], "put the rebound name on _RouteAllRun: " + "; ".join(offenders)


#: Bound once in `_route_all`'s prologue and only read afterwards. They are
#: fields because a method cannot capture what a closure could; they are not
#: run state, which is why they are excluded from the rebound-name pin below.
PROLOGUE_FIELDS = {
    "canvas",
    "nets",
    "net_index",
    "owner",
    "power_discs",
    "primitives",
    "last_mile_counts",
}


#: What the search cluster used to capture. Like `PROLOGUE_FIELDS` these are
#: bound once and only read -- except `bounds`, narrowed once before any closure
#: runs, and `priority`, re-derived once per round exactly as the local was --
#: so they are excluded from the rebound-name pin below too.
SEARCH_FIELDS = {
    "belt_id",
    "belt_model",
    "bounds",
    "junction_frame_bans",
    "prioritize_source_families",
    "flow_limits",
    "junction_obstacle_span",
    "history",
    "paths",
    "grid",
    "corridor_reservations",
    "dst_group",
    "src_group",
    "src_group_set",
    "own_source_nets",
    "hinted_to",
    "junction_ok",
    "admission_memo",
    "junction_reservation_blockers",
    "owned_source_starts",
    "source_hint",
    "sink_hint",
    "rejected_starts",
    "rejected_goals",
    "rejected_path_cells",
    "rejected_source_hints",
    "rejected_sink_hints",
    "guard_claims",
    "path_guards",
    "planned_taps",
    "source_access_walls",
    "destination_access_walls",
    "source_access_blockers",
    "permanent_guard",
    "path_tap",
    "_junction_stacks_collide",
    "_prebuilt_path_port",
    "_prebuilt_branch_port",
    "route_distance",
    "source_family",
    "source_family_distance",
    "priority",
}


def test_the_run_object_carries_exactly_the_rebound_names() -> None:
    from dataclasses import fields

    assert {
        field.name for field in fields(domain._RouteAllRun)
    } - PROLOGUE_FIELDS - SEARCH_FIELDS - COMMIT_FIELDS - LAST_MILE_FIELDS == {
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


def test_the_run_object_carries_the_prologue_bound_vocabulary() -> None:
    """The seven names the lifted vocabulary methods used to capture."""
    from dataclasses import fields

    assert {field.name for field in fields(domain._RouteAllRun)} >= PROLOGUE_FIELDS


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


def test_the_run_object_aliases_the_caller_ledger() -> None:
    """`budget` is the caller's ledger, held by reference and never copied."""
    ledger = WorkBudget(left=42)
    run = domain._RouteAllRun(budget=ledger, deadline=None)
    assert run.budget is ledger
    ledger.left = 40
    assert run.left == 40


def test_a_late_field_is_unset_until_its_owner_binds_it() -> None:
    """The eight late fields must raise where the old code raised.

    Reading one before the closure that owns it has run was an
    `UnboundLocalError`; a dataclass default would have turned that into a
    silent stale read, so they carry no default at all.
    """
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    for name in (
        "total_work",
        "capped_ordinary",
        "ordinary_remaining",
        "neighborhood",
        "admitted_path",
        "admitted_future",
        "repair_guards",
        "policy_restricted",
    ):
        with pytest.raises(AttributeError):
            getattr(run, name)


def test_the_per_call_resets_are_still_per_call() -> None:
    """Eight fields are reset by the closure that owns them, on every call.

    A dataclass default is a once-per-run initialisation. If the reset were
    dropped, a second call of the owning closure would start from the previous
    call's value, and a sibling would read stale admission evidence. The
    initial values are the tree's, not the brief's: `capped_ordinary` starts as
    `None` (it holds a `_PathSearchResult`), `ordinary_remaining` starts at the
    search's own `allowance`, and `repair_guards` starts as the live guard set.
    """
    source = SRC.read_text()
    for owner, reset in (
        ("_search", "self.total_work = 0"),
        ("_search", "self.capped_ordinary = None"),
        ("_search", "self.ordinary_remaining = allowance"),
        ("_ends", "self.neighborhood = None if replay is None else replay.neighborhood"),
        ("_search_route", "self.admitted_path = None"),
        ("_search_route", "self.admitted_future = None"),
        ("_repair", "self.repair_guards = set(self.guard_claims)"),
        ("_repair", "self.policy_restricted = False"),
    ):
        assert reset in source, f"{owner} must still reset {reset!r} on every call"


def test_the_two_deadline_restores_are_still_finally_clauses() -> None:
    """`deadline` is rebound mid-run and restored; a lift must not drop that.

    One restore lives in `_RouteAllRun._search_route`'s nested
    `admit_source_family` since Task 3 lifted the search cluster; the other is
    still `_route_all._repair._grouped_overcap_alternative`'s.
    """
    scopes = (_route_all_node(), _run_class_node())
    restores = [
        node.lineno
        for scope in scopes
        for node in ast.walk(scope)
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


def test_the_run_object_holds_no_copy_of_the_ledger() -> None:
    """Plan B's carve sites need the ledger aliased, so nothing may replace it.

    `run.budget` IS the caller's `WorkBudget`. Copying the run object, calling
    `dataclasses.replace` on it, or rebinding `run.budget` would hand the
    spending sites a ledger the caller never sees.
    """
    node = _route_all_node()
    copies = [
        f"{call.lineno}: {ast.unparse(call)[:60]}"
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "run"
        and (
            (isinstance(call.func, ast.Name) and call.func.id in {"replace", "copy", "deepcopy"})
            or (isinstance(call.func, ast.Attribute) and call.func.attr in {"replace", "copy"})
        )
    ]
    assert copies == [], f"the run object is never copied: {copies}"
    rebinds = [
        stmt.lineno
        for stmt in ast.walk(node)
        if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign))
        for target in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target])
        if isinstance(target, ast.Attribute)
        and target.attr == "budget"
        and isinstance(target.value, ast.Name)
        and target.value.id == "run"
    ]
    assert rebinds == [], f"the run object's ledger is never rebound: {rebinds}"


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
    root = _route_all_node()
    still_closures = {
        node.name
        for node in ast.walk(root)
        if isinstance(node, ast.FunctionDef) and node is not root
    }
    assert lifted & still_closures == set(), sorted(lifted & still_closures)


def test_the_net_index_field_is_unset_before_the_prologue_fills_it() -> None:
    """A late-bound field must raise, not serve an empty index."""
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    with pytest.raises(AttributeError):
        _ = run.net_index


def test_role_rows_is_still_a_generator_consumed_once() -> None:
    """`_role_rows` yields; a method that eagerly built a list would change when
    `nets` is read and would materialise every row before `Nets.of` asks."""
    import inspect

    assert inspect.isgeneratorfunction(domain._RouteAllRun._role_rows)
    source = SRC.read_text()
    driver = ast.get_source_segment(source, _route_all_node())
    assert driver is not None
    assert driver.count("Nets.of(run._role_rows())") == 1


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


def test_the_run_object_carries_the_search_cluster_fields() -> None:
    """The 42 names the lifted search methods used to capture."""
    from dataclasses import fields

    assert {field.name for field in fields(domain._RouteAllRun)} >= SEARCH_FIELDS


def test_the_late_search_fields_are_unset_until_the_loop_binds_them() -> None:
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    for name in ("route_distance", "source_family", "source_family_distance", "priority"):
        with pytest.raises(AttributeError):
            getattr(run, name)


def test_the_three_per_run_caches_do_not_outlive_the_run() -> None:
    """Two `_route_all` calls must not share a shape cache.

    `_junction_stacks_collide` reaches `_building_collider_hits`, which
    `test_prepared_junction_ban_cancels_inside_cell_level_scan` monkeypatches;
    a process-lifetime cache would serve verdicts computed before the patch.
    """
    canvas, nets, bounds = _corridor()
    domain._route_all(canvas, nets, 2003, 37, bounds, budget=WorkBudget(left=100_000))
    for name in ("_junction_stacks_collide", "_prebuilt_branch_port", "_prebuilt_path_port"):
        attribute = vars(domain._RouteAllRun).get(name)
        assert attribute is None or not hasattr(attribute, "cache_info"), (
            f"{name} must be built per run in the prologue, not cached on the class"
        )


def test_the_per_run_caches_are_fresh_on_every_pass() -> None:
    """The stronger witness: two passes must not share one cache object."""
    seen: list[tuple[object, object, object]] = []
    original = domain._RouteAllRun._ends

    def spy(self: domain._RouteAllRun, *args: Any, **kwargs: Any) -> Any:
        seen.append(
            (
                self._junction_stacks_collide,
                self._prebuilt_branch_port,
                self._prebuilt_path_port,
            )
        )
        return original(self, *args, **kwargs)

    domain._RouteAllRun._ends = spy  # type: ignore[method-assign]
    try:
        for _ in range(2):
            canvas, nets, bounds = _corridor()
            domain._route_all(canvas, nets, 2003, 37, bounds, budget=WorkBudget(left=100_000))
    finally:
        domain._RouteAllRun._ends = original  # type: ignore[method-assign]

    assert seen, "the corridor pass never derived a net's ends"
    first, last = seen[0], seen[-1]
    assert all(a is not b for a, b in zip(first, last, strict=True)), (
        "a cache survived from one `_route_all` call into the next"
    )
    assert all(hasattr(entry, "cache_clear") for entry in first), first


def test_the_proposal_deadline_narrowing_is_still_conditional_and_nested() -> None:
    """`admit_source_family` is created only when a sibling is unrouted."""
    source = SRC.read_text()
    assert "def admit_source_family(" in source
    assert "admit_proposal = admit_source_family" in source
    assert "raise _GeometricDeadline from error" in source


COMMIT_METHODS = (
    "_budget_result",
    "_commit_selection",
    "_finish",
    "_commit_once",
    "_terminal_attempt",
    "_retain_commit_failures",
)

#: What the lifted commit cluster used to capture and Tasks 1-3 had not added.
#: `settle`, `destination_canvas` and `proposals` are bound once; the six
#: `best_*`, `round_work` and `iterations` are the incumbent, re-derived per
#: round exactly as the locals were; `proved_stranded` is the last-mile proof's
#: set; `retained_failures` and `retained_blockers` are the per-round snapshot
#: that replaced `retain_commit_failures`'s default arguments.
COMMIT_FIELDS = {
    "best_attempt",
    "best_failures",
    "best_path_taps",
    "best_paths",
    "best_sink_hints",
    "best_source_hints",
    "destination_canvas",
    "iterations",
    "proposals",
    "proved_stranded",
    "round_work",
    "settle",
    "retained_failures",
    "retained_blockers",
}


def test_the_commit_cluster_is_methods() -> None:
    missing = sorted(set(COMMIT_METHODS) - set(vars(domain._RouteAllRun)))
    assert missing == [], missing


def test_the_run_object_carries_the_commit_cluster_fields() -> None:
    """The fourteen names the lifted commit methods used to capture."""
    from dataclasses import fields

    assert {field.name for field in fields(domain._RouteAllRun)} >= COMMIT_FIELDS


def test_the_late_commit_fields_are_unset_until_their_owner_binds_them() -> None:
    """No default: a read before the binding point must still be a hard error."""
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    for name in ("proved_stranded", "retained_failures", "retained_blockers"):
        with pytest.raises(AttributeError):
            getattr(run, name)


def test_retain_commit_failures_takes_no_snapshot_default_arguments() -> None:
    """The two keyword-only defaults are gone from the signature.

    `def f(..., x=round_failures)` evaluated `round_failures` once, when the
    `def` executed. A method cannot carry that, so the two parameters became
    two fields written at the same statement slot; the signature must no longer
    offer them, or a caller could pass a third dict and split the evidence.
    """
    import inspect

    parameters = inspect.signature(domain._RouteAllRun._retain_commit_failures).parameters
    assert list(parameters) == ["self", "unlinked", "details"]

    source = SRC.read_text()
    assert "retained_failures: dict[int, NetFailure] = round_failures" not in source
    assert "retained_blockers: dict[int, tuple[NetId, ...]] = search_blockers" not in source


def test_retain_commit_failures_uses_the_rounds_snapshot_not_the_live_dict() -> None:
    """The default-argument snapshot became two explicit per-round fields.

    `round_failures` is rebound again later in the round, after the point the
    closure's default bound it. Reading a live binding instead would write a
    round's commit evidence into the wrong dict, so the method must write
    through `self.retained_failures`, set once where the `def` used to stand.
    """
    source = SRC.read_text()
    assert "run.retained_failures = run.round_failures" in source
    assert "run.retained_blockers = run.search_blockers" in source

    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    first: dict[int, Any] = {}
    run.retained_failures = first
    run.retained_blockers = {}
    run.nets = [
        domain._Net(
            domain._Port(0, 1, 1, 1, 1),
            domain._Port(1, 5, 5, 5, 5),
            "gear",
            net_id=NetId(0, 1, "gear", NetRole.INTERNAL, 0),
        )
    ]
    run.paths = {0: ((3, 4, 0), (5, 5, 0))}  # type: ignore[assignment]
    run.history = defaultdict(float)
    run.rejected_path_cells = defaultdict(set)

    run._retain_commit_failures((0,), {0: domain._CommitFailure((3, 4, 0), "path")})
    assert list(first) == [0], "the method wrote somewhere other than the snapshot"
    assert first[0].kind is RouteFailureKind.COMMIT_LINK

    # Rebinding the field afterwards must not retro-fit the write.
    second: dict[int, Any] = {}
    run.retained_failures = second
    assert list(first) == [0] and second == {}


def test_the_snapshot_is_taken_where_the_def_stood() -> None:
    """Between the round's `round_failures` rebind and the first call of the method."""
    node = _route_all_node()
    lines: dict[str, int] = {}
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Assign)
            and len(child.targets) == 1
            and isinstance(child.targets[0], ast.Attribute)
            and child.targets[0].attr in ("retained_failures", "retained_blockers")
            # Both sources are fields since Task 6; the snapshot copies the
            # reference the round holds now, not a name bound somewhere else.
            and isinstance(child.value, ast.Attribute)
            and child.value.attr in ("round_failures", "search_blockers")
        ):
            lines[child.targets[0].attr] = child.lineno
    assert sorted(lines) == ["retained_blockers", "retained_failures"], lines

    # Only the round loop's own calls order lexically against the snapshot;
    # `_complete_source_dependents` and `_last_mile` are defined above the loop
    # and called from inside it, so their call sites are lexically earlier and
    # dynamically later.
    calls: list[int] = []

    def own_scope(scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(scope):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "run"
                and child.func.attr == "_retain_commit_failures"
            ):
                calls.append(child.lineno)
            own_scope(child)

    own_scope(node)
    assert calls, "the round loop no longer calls the method -- rewrite this pin"
    snapshot = max(lines.values())
    assert snapshot < min(calls), (snapshot, sorted(calls))

    rebinds = {
        child.lineno: child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "round_failures"
            for target in child.targets
        )
    }
    before = [rebind for rebind in rebinds if rebind < snapshot]
    assert before, (snapshot, sorted(rebinds))
    # The pin is the round's DERIVED rebind -- the table built from this
    # round's stranded set -- not merely the `= {}` reset at the top of the
    # round. A snapshot taken between the reset and the derived rebind would
    # retain an empty dict and lose the round's commit evidence.
    derived = rebinds[max(before)]
    assert isinstance(derived, ast.DictComp), (max(before), ast.dump(derived))


def _method_ast(name: str) -> ast.FunctionDef:
    for child in _run_class_node().body:
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return child
    raise AssertionError(f"_RouteAllRun.{name} is missing")


#: The nineteen data names `_repair` captured from `_route_all`'s scope. Every
#: one was already a field before this task: Task 1 gave `budget`, Task 3 the
#: search cluster's fifteen, Task 4 `round_work`.
REPAIR_FIELDS = {
    "budget",
    "canvas",
    "corridor_reservations",
    "destination_access_walls",
    "grid",
    "guard_claims",
    "history",
    "nets",
    "owned_source_starts",
    "owner",
    "path_tap",
    "paths",
    "rejected_path_cells",
    "round_work",
    "sink_hint",
    "source_access_blockers",
    "source_access_walls",
    "source_hint",
    "src_group",
}

#: The fourteen sibling closures it captured, all already methods.
REPAIR_METHOD_CAPTURES = (
    "_dependency_closure",
    "_endpoint_dependents",
    "_ends",
    "_future_source_offers",
    "_inside_grid",
    "_net_id",
    "_ordinary_query_deadline",
    "_preserves_source_frontier",
    "_route_order",
    "_search",
    "_search_route",
    "_selected_hints",
    "_stake",
    "_unstake",
)


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


def test_admit_repair_tap_keeps_its_default_argument_snapshot() -> None:
    """Its defaults bind `_repair`'s locals, which stay locals of the method."""
    repair = _method_ast("_repair")
    tap = next(
        node
        for node in ast.walk(repair)
        if isinstance(node, ast.FunctionDef) and node.name == "admit_repair_tap"
    )
    defaults = [
        node.id
        for node in list(tap.args.defaults) + [d for d in tap.args.kw_defaults if d is not None]
        if isinstance(node, ast.Name)
    ]
    assert defaults == ["index", "victims", "dependents"], defaults
    # ... and none of the three became a field read anywhere in the method.
    reads = {
        node.attr
        for node in ast.walk(repair)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    assert reads.isdisjoint({"index", "victims", "dependents"}), sorted(reads)


def test_the_repair_cluster_added_no_new_field() -> None:
    from dataclasses import fields

    names = {field.name for field in fields(domain._RouteAllRun)}
    assert "repair_guards" in names
    assert "policy_restricted" in names


def test_the_run_object_already_carried_every_repair_capture() -> None:
    """The lift adds no state: all 33 captures were fields or methods already."""
    from dataclasses import fields

    names = {field.name for field in fields(domain._RouteAllRun)}
    assert names >= REPAIR_FIELDS, sorted(REPAIR_FIELDS - names)
    missing = [name for name in REPAIR_METHOD_CAPTURES if name not in vars(domain._RouteAllRun)]
    assert missing == [], missing


def test_repair_is_the_last_closure_lifted_out_of_the_route_order_cluster() -> None:
    """`_route_all` reaches `_repair` through the run object, and nowhere else.

    Task 5 lifted the closure and left `_repair = run._repair` in the slot the
    `def` held; Task 7 deleted that alias, so the pin is now the single call
    site `run._repair(...)` inside the round loop.
    """
    node = _route_all_node()
    defs = [
        child.name
        for child in ast.walk(node)
        if isinstance(child, ast.FunctionDef) and child.name == "_repair"
    ]
    assert defs == [], defs
    calls = [
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "_repair"
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "run"
    ]
    assert len(calls) == 1, calls
    bare = [
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and child.id == "_repair"
    ]
    assert bare == [], bare


#: The five names the last-mile cluster captured that were not fields yet. All
#: five are re-derived once per round exactly where the locals were, so they
#: carry no default: a read before the first round must stay a hard error.
LAST_MILE_FIELDS = {
    "pressure",
    "blame",
    "search_failures",
    "search_blockers",
    "round_failures",
}


def test_the_run_object_carries_the_last_mile_cluster_fields() -> None:
    from dataclasses import fields

    names = {field.name for field in fields(domain._RouteAllRun)}
    assert names >= LAST_MILE_FIELDS, sorted(LAST_MILE_FIELDS - names)


def test_the_late_last_mile_fields_are_unset_until_the_round_binds_them() -> None:
    """No default: a read before the round binds one must stay a hard error."""
    run = domain._RouteAllRun(budget=WorkBudget(left=10), deadline=None)
    for name in sorted(LAST_MILE_FIELDS):
        with pytest.raises(AttributeError):
            getattr(run, name)


#: The sixteen depth-1 closures of the last-mile cluster, in source order.
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

#: Methods of earlier clusters the last-mile cluster captured as siblings.
LAST_MILE_METHOD_CAPTURES = (
    "_blocking_nets",
    "_can_junction",
    "_commit_once",
    "_dependency_closure",
    "_endpoint_cells",
    "_endpoint_dependents",
    "_ends",
    "_failure",
    "_net_id",
    "_preserves_source_frontier",
    "_retain_commit_failures",
    "_route_order",
    "_search_route",
    "_selected_hints",
    "_stake",
    "_terminal_attempt",
    "_unstake",
)

#: Fields of earlier clusters the last-mile cluster captured.
LAST_MILE_CAPTURED_FIELDS = {
    "admission_memo",
    "bounds",
    "budget",
    "canvas",
    "corridor_reservations",
    "dst_group",
    "grid",
    "guard_claims",
    "history",
    "last_mile_counts",
    "nets",
    "owned_source_starts",
    "owner",
    "path_guards",
    "path_tap",
    "paths",
    "planned_taps",
    "primitives",
    "proposals",
    "proved_stranded",
    "rejected_goals",
    "rejected_path_cells",
    "rejected_sink_hints",
    "rejected_source_hints",
    "rejected_starts",
    "round_work",
    "sink_hint",
    "source_access_blockers",
    "source_hint",
    "src_group",
}


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


def test_the_run_object_already_carried_every_other_last_mile_capture() -> None:
    """Only five of the cluster's captures were new; the rest were there."""
    from dataclasses import fields

    names = {field.name for field in fields(domain._RouteAllRun)}
    assert names >= LAST_MILE_CAPTURED_FIELDS, sorted(LAST_MILE_CAPTURED_FIELDS - names)
    missing = [name for name in LAST_MILE_METHOD_CAPTURES if name not in vars(domain._RouteAllRun)]
    assert missing == [], missing


def _budget_left_argument(method: str) -> ast.expr:
    for node in ast.walk(_method_ast(method)):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "budget_left":
                    return keyword.value
    raise AssertionError(f"_RouteAllRun.{method} passes no budget_left")


def test_the_cluster_environment_passes_the_ledger_view_not_a_reading() -> None:
    """`ClusterEnvironment.budget_left` is `Callable[[], int]`; `ClusterProblem`'s is `int`.

    last_mile.py calls the environment's view repeatedly while the cluster
    search spends the ledger. A reading frozen at construction turns a bounded
    search into an unbounded one.
    """
    environment = _budget_left_argument("_cluster_environment")
    assert isinstance(environment, ast.Attribute) and environment.attr == "_pass_budget_left", (
        "pass the bound method itself, not a reading of it"
    )

    problem = _budget_left_argument("_capture")
    assert isinstance(problem, ast.Call), "the recorded problem keeps its int snapshot"


def test_both_budget_floors_are_the_same_field_read() -> None:
    """`budget_floor` is an `int` at both sites and stays one."""
    for method in ("_cluster_environment", "_capture"):
        for node in ast.walk(_method_ast(method)):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "budget_floor":
                        assert ast.unparse(keyword.value) == "self.last_mile_floor", (
                            method,
                            ast.unparse(keyword.value),
                        )


def test_no_alias_survives_and_no_bound_method_is_compared_by_identity() -> None:
    """`_route_all` is a driver: it calls `run._x(...)`, it does not alias them.

    An alias was Tasks 2-6's scaffolding, one line per lifted closure, so the
    call sites could stay byte-identical while the bodies moved. Task 7 removes
    the scaffolding. The second half is the hazard the removal creates: a bound
    method is a **fresh object on every attribute access**, so `run._ends is
    run._ends` is `False` and any `is`/`is not` against one is a latent bug that
    could only appear once the single aliased object was gone.
    """
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


def test_route_all_declares_no_nested_def_and_calls_only_bound_methods() -> None:
    """The other half of the driver claim, and the one a mechanical rewrite can miss.

    Every bare `name(...)` left in `_route_all` must resolve to a module global
    or a local built in the prologue -- never to a name the deleted aliases used
    to supply. A missed rewrite would either raise `NameError` or, worse,
    silently bind a module-level function of the same name.
    """
    node = _route_all_node()
    nested = [
        child.name
        for child in ast.walk(node)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node
    ]
    assert nested == [], f"_route_all still declares closures: {nested}"

    method_names = {
        name for name in vars(domain._RouteAllRun) if callable(getattr(domain._RouteAllRun, name))
    }
    leaked = sorted(
        {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in method_names
        }
    )
    assert leaked == [], f"these call a bare name that names a run method: {leaked}"
