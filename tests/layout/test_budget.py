"""The one deadline-and-ledger contract, pinned at its edges."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from flab2bp.lab.techs import belt_rules_for_url
from flab2bp.layout import budget as work
from flab2bp.layout import process_resources, routing_domain

# ``budget`` holds this very module object, so patching ``usage`` here is what
# the budget's memory probe sees. Reaching it as ``work.process_resources``
# would be the same object but mypy's strict no-implicit-reexport rejects it.


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

    monkeypatch.setattr(process_resources, "usage", forbidden)
    assert work.WorkBudget(deadline=None).expired() is False
    assert work.expired(None) is False


def test_check_still_refuses_on_the_deadline_and_on_rss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_resources, "usage", lambda: (0, 0, 0))
    with pytest.raises(work.TransportRefusal) as expired_at:
        work.WorkBudget(deadline=100.0, clock=lambda: 100.0).check()
    assert expired_at.value.reason == "DEADLINE"

    monkeypatch.setattr(process_resources, "usage", lambda: (0, 0, 5 * 1024 * 1024))
    with pytest.raises(work.TransportRefusal) as too_big:
        work.WorkBudget(deadline=100.0, clock=lambda: 1.0).check()
    assert too_big.value.reason == "MEMORY_BOUND"


def test_check_with_no_deadline_only_probes_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_resources, "usage", lambda: (0, 0, 0))
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


_BELT_RULES = belt_rules_for_url("https://factoriolab.github.io/dsp/list?o=iron-ingot*60&v=11")


def _tiny_open_canvas() -> tuple[
    routing_domain._Canvas,
    tuple[int, int, int],
    tuple[int, int, int],
    tuple[int, int, int, int],
]:
    """A three-tile corridor with one free ground path from start to goal."""
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
    canvas, start, goal, bounds = _tiny_open_canvas()
    budget = work.WorkBudget(left=20_000)
    result = routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds, budget)
    assert result.path is not None
    assert budget.left == 20_000 - result.work
    assert isinstance(budget.left, int)


def test_an_empty_ledger_refuses_before_the_kernel_runs() -> None:
    from flab2bp.layout import route_feedback

    canvas, start, goal, bounds = _tiny_open_canvas()
    budget = work.WorkBudget(left=0)
    result = routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds, budget)
    assert result.path is None
    assert result.kind is route_feedback.RouteFailureKind.BUDGET
    assert budget.left == 0


def test_a_ledger_without_a_left_is_unbounded_like_no_ledger_at_all() -> None:
    """`left=None` is what `budget=None` was: a ledger carried for its clock."""
    canvas, start, goal, bounds = _tiny_open_canvas()
    budget = work.WorkBudget(deadline=None)
    assert routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds, budget).path
    assert budget.left is None


def test_no_budget_still_means_unbounded() -> None:
    canvas, start, goal, bounds = _tiny_open_canvas()
    assert routing_domain._geometric_search(canvas, [start], {goal}, {}, 1.0, bounds).path


def test_a_carved_child_ledger_returns_its_unspent_work_to_the_parent() -> None:
    """The carve sites all reconcile parent -= allowance - child.left."""
    parent = work.WorkBudget(left=1_000)
    allowance = 400
    child = work.WorkBudget(left=allowance, deadline=parent.deadline, clock=parent.clock)
    child.left = 150  # the child spent 250
    assert parent.left is not None
    parent.left -= allowance - child.left
    assert parent.left == 750


def test_a_non_coverage_net_charges_the_pass_ledger_directly() -> None:
    """`net_budget` is the pass ledger itself when the pass is not a coverage pass.

    The shape of `_route_all`'s per-net ledger choice, with the numbers worked
    by hand: a coverage pass carves a FRESH child and reconciles it against the
    parent afterwards; any other pass hands the net the parent OBJECT, so the
    net's spend is already charged and the reconciliation must not run. A copy
    on the second branch would silently drop every net's charge.
    """

    def net_budget(budget: work.WorkBudget, allowance: int, coverage_pass: bool) -> work.WorkBudget:
        """`_route_all`'s per-net ledger choice, verbatim."""
        return work.WorkBudget(left=allowance) if coverage_pass else budget

    budget = work.WorkBudget(left=1_000)
    allowance = 400

    carved = net_budget(budget, allowance, True)
    assert carved is not budget
    assert carved.left == 400
    carved.left = 150  # the carved child spent 250
    assert budget.left is not None
    budget.left -= allowance - carved.left  # only a coverage pass reconciles
    assert budget.left == 750

    direct = net_budget(budget, allowance, False)
    assert direct is budget
    assert direct.left == 750
    direct.left = 500  # the net spent 250 out of the pass ledger itself
    assert budget.left == 500  # charged once, with no reconciliation at all


def test_the_factory_floor_is_the_routing_budget_constant() -> None:
    seeded = routing_domain._routing_pass_budget()
    assert seeded.left == routing_domain._ROUTING_BUDGET
    assert seeded.deadline is None


def test_the_factory_scales_with_seconds_but_never_below_the_floor() -> None:
    from flab2bp.layout import freeform

    generous = routing_domain._routing_pass_budget(deadline=1234.0, seconds=20.0)
    assert generous.left == int(freeform._ROUTING_WORK_PER_SECOND * 20.0)
    assert generous.deadline == 1234.0

    stingy = routing_domain._routing_pass_budget(deadline=None, seconds=0.5)
    assert stingy.left == routing_domain._ROUTING_BUDGET


def test_the_factory_reads_the_floor_at_call_time_not_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every seed must see the same floor, whenever its module was imported.

    `freeform` reached the constant through the module object and got the value
    live; `sequence_solver` and `hierarchy.compose` bound it in a `from ...
    import` list and got whatever it was when they were first imported. Nothing
    patches it today, so the disagreement was latent -- the factory removes it
    by reading the global inside its own body, which this pins.
    """
    floor = routing_domain._ROUTING_BUDGET
    monkeypatch.setattr(routing_domain, "_ROUTING_BUDGET", 3)
    assert routing_domain._routing_pass_budget().left == 3
    assert routing_domain._routing_pass_budget(seconds=0.0).left == 3
    monkeypatch.undo()
    assert routing_domain._routing_pass_budget().left == floor


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
