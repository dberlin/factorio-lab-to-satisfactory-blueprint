"""`_route_all`'s run state is one typed object, and the semantics are unchanged.

The 2026-09-13 abstraction review (section 9 item 3) asked for the 67 closures
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
        ("_search", "run.total_work = 0"),
        ("_search", "run.capped_ordinary = None"),
        ("_search", "run.ordinary_remaining = allowance"),
        ("_ends", "run.neighborhood = None if replay is None else replay.neighborhood"),
        ("_search_route", "run.admitted_path = None"),
        ("_search_route", "run.admitted_future = None"),
        ("_repair", "run.repair_guards = set(guard_claims)"),
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
