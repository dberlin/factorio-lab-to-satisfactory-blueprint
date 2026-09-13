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
