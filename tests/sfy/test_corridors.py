"""Corridor columns: which trunk runs where, and where a transverse has to bridge.

Every test here is a statement about INTERVALS.  ``assign_columns`` is a pure
function of the intervals it is handed -- it knows nothing about belts, rows or
designers -- which is what lets the crossing rule be pinned without building a
placement first.  The centimetres in the second half come from the registry:
the column pitch out of ``belt.clearance``'s own box plus a hologram grid step,
the turn radius out of ``belt_bend_radius_cm``.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

import pytest

from flab2bp.layout.budget import WorkBudget, WorkLimits
from flab2bp.sfy.layout.corridors import (
    Assignment,
    ColumnRequest,
    CorridorError,
    Interval,
    assign_columns,
    attachment_pitch_cm,
    belt_pitch_cm,
    bridge_z_cm,
    turn_radius_cm,
)
from flab2bp.sfy.layout.manifold import crossing_gap_cm, grid_ceil
from flab2bp.sfy.registry import Registry, load_registry


@cache
def _registry() -> Registry:
    return load_registry()


def _request(
    belt: str, y_a: float, y_b: float, *, joins_a: str = "", inner_a: int = 0
) -> ColumnRequest:
    return ColumnRequest(belt=belt, y_a=y_a, y_b=y_b, joins_a=joins_a, inner_a=inner_a)


def _by_belt(assignments: Sequence[Assignment]) -> dict[str, Assignment]:
    return {assignment.belt: assignment for assignment in assignments}


# --- the crossing rule -----------------------------------------------------


def test_trunks_that_do_not_overlap_all_ride_the_innermost_column() -> None:
    """Three trunks stacked up the corridor are three belts on one line."""
    assignments = assign_columns(
        [_request("a", -1000.0, -600.0), _request("b", -400.0, 200.0), _request("c", 400.0, 900.0)],
        columns=4,
    )
    assert [a.column for a in assignments] == [0, 0, 0]
    assert all(a.bridged_a == () and a.bridged_b == () for a in assignments)


def test_two_trunks_that_overlap_take_different_columns() -> None:
    """One column is one lane: two belts cannot share the Y it runs through.

    The one that leaves the corridor first gets the inner column, because the
    requests are taken in order of ``y_b``: a belt that turns out early has the
    shortest way back to a row, and standing it inside leaves the long runs
    outside it.
    """
    assignments = _by_belt(
        assign_columns([_request("a", -1000.0, 1000.0), _request("b", -500.0, 500.0)], columns=4)
    )
    assert assignments["b"].column == 0
    assert assignments["a"].column == 1


def test_a_transverse_that_must_cross_an_occupied_column_is_bridged_never_ignored() -> None:
    """The brief's crossing case: ``[y0, y2]`` against ``[y1, y3]``.

    The second belt's own interval is not contained in the first, but the end it
    enters the corridor by is: its transverse at ``y1`` has to get past the first
    belt's column, and the only legal way past is over the top.  A layout that
    took column 1 and said nothing would be two belts through each other.
    """
    y0, y1, y2, y3 = -1000.0, -400.0, 300.0, 1200.0
    assignments = _by_belt(
        assign_columns([_request("first", y0, y2), _request("second", y1, y3)], columns=4)
    )
    assert assignments["first"].column == 0
    second = assignments["second"]
    assert second.column == 1
    assert second.bridged_a == (0,)
    assert second.bridged_b == ()


def test_moving_further_out_never_undoes_a_crossing() -> None:
    """Why "the innermost column where no bridge is needed" is the innermost FREE one.

    The set of columns a transverse crosses is every occupied column inside the
    one it lands in, so it only grows as the belt moves out.  A column that
    needs a bridge is therefore never rescued by taking the next one, and the
    rule's two halves collapse into: take the innermost column with room, and
    bridge what stands inside it.
    """
    assignments = _by_belt(
        assign_columns(
            [
                _request("first", -1000.0, 300.0),
                _request("second", -400.0, 1200.0),
                _request("third", -300.0, 1250.0),
            ],
            columns=4,
        )
    )
    assert assignments["second"].column == 1 and assignments["second"].bridged_a == (0,)
    assert assignments["third"].column == 2 and assignments["third"].bridged_a == (0, 1)


def test_a_crossing_that_cannot_be_bridged_is_refused_by_its_own_cause() -> None:
    requests = [
        _request("a", -1000.0, 1000.0),
        _request("b", -900.0, 1100.0),
        _request("c", 0.0, 1200.0),
    ]
    with pytest.raises(CorridorError) as caught:
        assign_columns(requests, columns=3, bridgeable=lambda request, column: False)
    assert caught.value.cause == "bridge"


def test_a_corridor_with_no_column_left_is_refused_as_width() -> None:
    requests = [_request(name, -1000.0, 1000.0) for name in ("a", "b", "c")]
    with pytest.raises(CorridorError) as caught:
        assign_columns(requests, columns=2)
    assert caught.value.cause == "width"


def test_a_belt_never_bridges_the_column_it_is_joined_to() -> None:
    """A branch leaves its splitter ON the spine's column; that is a join, not a
    crossing, and nothing rises over the belt it is wired to."""
    spine = _request("spine", -1000.0, 1000.0)
    branch = _request("branch", 0.0, 1400.0, joins_a="spine")
    assignments = _by_belt(assign_columns([spine, branch], columns=4))
    assert assignments["branch"].column == 1
    assert assignments["branch"].bridged_a == ()


def test_a_transverse_is_only_crossed_by_the_columns_it_actually_reaches() -> None:
    """``inner_a`` is how far in a transverse goes; it does not cross what it never
    reaches."""
    assignments = _by_belt(
        assign_columns(
            [
                _request("deep", -1000.0, 1000.0),
                _request("spine", -900.0, 1100.0),
                _request("branch", 0.0, 1200.0, joins_a="spine", inner_a=1),
            ],
            columns=4,
        )
    )
    assert assignments["deep"].column == 0
    assert assignments["spine"].column == 1
    assert assignments["branch"].column == 2
    assert assignments["branch"].bridged_a == ()


def test_the_margin_widens_an_interval_by_the_belt_it_stands_for() -> None:
    """A transverse level with another trunk's END still has to get past its box."""
    plain = _by_belt(
        assign_columns([_request("a", -1000.0, 0.0), _request("b", 0.0, 1000.0)], columns=4)
    )
    assert plain["b"].column == 0
    widened = _by_belt(
        assign_columns(
            [_request("a", -1000.0, 0.0), _request("b", 0.0, 1000.0)], columns=4, margin=79.0
        )
    )
    assert widened["b"].column == 1
    assert widened["b"].bridged_a == (0,)


def test_the_column_search_is_charged_against_the_budget_it_is_given() -> None:
    budget = WorkBudget(limits=WorkLimits(assignments=2))
    with pytest.raises(Exception, match="assignments work limit"):
        assign_columns(
            [_request(name, -1000.0, 1000.0) for name in ("a", "b", "c", "d")],
            columns=8,
            budget=budget,
        )


def test_an_interval_holds_the_y_it_spans_whichever_way_it_was_written() -> None:
    down = Interval(column=0, y_a=500.0, y_b=-500.0, belt="down")
    assert (down.low, down.high) == (-500.0, 500.0)
    assert down.strictly_contains(0.0)
    assert not down.strictly_contains(500.0)
    assert down.overlaps(Interval(column=0, y_a=400.0, y_b=900.0, belt="up"))
    assert not down.overlaps(Interval(column=0, y_a=600.0, y_b=900.0, belt="up"))


# --- the centimetres, all of them read -------------------------------------


def test_the_column_pitch_is_two_belt_boxes_and_a_grid_step() -> None:
    """``belt.clearance`` gives a belt 79 cm to each side; two lanes may not lap,
    and the hologram grid is the smallest gap the build gun can leave."""
    registry = _registry()
    grid = registry.limits.hologram_grid_cm
    assert grid is not None
    assert belt_pitch_cm(registry) == grid_ceil(2.0 * 79.0 + grid, grid)
    assert belt_pitch_cm(registry) == 300.0


def test_the_turn_radius_is_twice_the_bend_radius_on_the_grid() -> None:
    registry = _registry()
    grid = registry.limits.hologram_grid_cm
    bend = registry.limits.belt_bend_radius_cm
    assert grid is not None and bend is not None
    assert turn_radius_cm(registry) == grid_ceil(2.0 * bend, grid)
    assert turn_radius_cm(registry) == 400.0


def test_a_bridge_stands_one_crossing_gap_over_the_belt_it_crosses() -> None:
    registry = _registry()
    assert bridge_z_cm(200.0, registry) == 200.0 + crossing_gap_cm(registry)


def test_two_attachments_stand_far_enough_apart_for_a_legal_belt_between_them() -> None:
    """A grid step apart is not a spacing this geometry allows.

    A splitter's through ports are 100 cm out on each side, so two of them one
    grid step apart would want a belt of -100 cm; ``belt.min_length`` refuses
    anything at or under 100.02 cm.  The pitch is the smallest grid multiple
    that leaves a legal belt, and every number in it is the registry's.
    """
    registry = _registry()
    grid = registry.limits.hologram_grid_cm
    floor = registry.limits.belt_min_length_cm
    assert grid is not None and floor is not None
    assert attachment_pitch_cm(registry) == grid_ceil(200.0 + floor, grid)
    assert attachment_pitch_cm(registry) == 400.0
