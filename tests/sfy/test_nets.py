"""The net planner: which item runs up which corridor, and where its nodes stand.

Where a merger stands, and which side of the build an item is wanted on, are
arithmetic on a handful of numbers, and a whole build is a poor place to read
either of them.  The nets these tests build by hand are the shapes no
two-row build in a Blueprint Designer can put in front of the planner at all.
"""

from __future__ import annotations

from fractions import Fraction
from functools import partial

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.layout import strategy
from flab2bp.sfy.layout.corridors import CorridorError
from flab2bp.sfy.layout.nets import NetPlanner, _carried, _Net, _Terminal
from flab2bp.sfy.spec import SfyBuildSpec, designer
from tests.sfy.conftest import flow_spec, sfy_registry

#: The designer the numbers below are read against; see ``test_strategy.py``.
MARK = "mk3"


def _planner(spec: SfyBuildSpec, mark: str = MARK) -> NetPlanner:
    """A net planner with its numbers read and nothing laid out yet."""
    return NetPlanner(
        spec=spec,
        registry=sfy_registry(),
        budget=WorkBudget(),
        measures=strategy._measure(sfy_registry(), designer(mark, sfy_registry())),
        refuse=partial(strategy._refuse, spec),
    )


def _terminal(y: float, rate: Fraction, *, wall: bool = False) -> _Terminal:
    return _Terminal(item="iron-ingot", rate=rate, side=1, y=y, wall=wall)


def _net(sources: list[_Terminal], sinks: list[_Terminal]) -> _Net:
    return _Net(item="iron-ingot", side=1, sources=sources, sinks=sinks)


def test_the_nodes_stand_in_the_order_the_spine_meets_them_not_in_kind_order() -> None:
    """A source can stand between two sinks, and then the spine merges it in on
    the way past -- after it has already split some off.

    Mergers are built from the sources and splitters from the sinks, so taking
    them in that order would put a merger at 1000 before a splitter at 0 and the
    belt between them would run backwards.  The sort is by ``Y``, which is the
    order a belt running up one column actually arrives in, and
    :func:`_carried` reads the rate the same way.
    """
    planner = _planner(flow_spec("iron-plate-60"))
    net = _net(
        sources=[_terminal(-2400.0, Fraction(1), wall=True), _terminal(1000.0, Fraction(1, 2))],
        sinks=[_terminal(0.0, Fraction(1, 4)), _terminal(2400.0, Fraction(5, 4), wall=True)],
    )
    planner._plan_nodes(net)
    assert [(node.y, node.merging) for node in net.nodes] == [(0.0, False), (1000.0, True)]
    # What the spine carries between them: one in, a quarter off, a half in.
    assert _carried(net, 0) == Fraction(1)
    assert _carried(net, 1) == Fraction(3, 4)
    assert _carried(net, 2) == Fraction(5, 4)


def test_a_source_outside_the_span_the_trunk_runs_through_is_refused_by_its_own_cause() -> None:
    """The reviewer's case: made at y = 1000 and y = 3000, eaten at y = 2000.

    One column carries one direction, and the source above the sink would have to
    run back down it against everything else in the corridor.  This build form
    cannot do that, and says so: it is not a designer that is too shallow, which
    is the cause it used to wear.
    """
    planner = _planner(flow_spec("iron-plate-60"))
    net = _net(
        sources=[_terminal(1000.0, Fraction(1)), _terminal(3000.0, Fraction(1))],
        sinks=[_terminal(2000.0, Fraction(2))],
    )
    with pytest.raises(CorridorError) as caught:
        planner._plan_nodes(net)
    assert caught.value.cause == "backwards"
    assert strategy._CORRIDOR_CAUSES["backwards"] in strategy.REFUSALS


def test_an_item_made_only_on_the_other_side_of_the_rows_is_refused_by_its_own_cause() -> None:
    """A corridor is one side of the build and a belt cannot cross the rows.

    Three rows would be needed for a build to do this, and no designer is deep
    enough for three, so the refusal is put in front of the code that raises it
    rather than through a spec that cannot exist.
    """
    planner = _planner(flow_spec("iron-plate-60"))
    made = _Terminal(
        item="iron-ingot", rate=Fraction(1), side=-1, y=0.0, wall=False, link=(1, "Output1")
    )
    eaten = _Terminal(
        item="iron-ingot", rate=Fraction(1), side=1, y=500.0, wall=False, link=(2, "Input1")
    )
    with pytest.raises(NoValidLayout) as caught:
        planner._nets_for("iron-ingot", {("iron-ingot", -1): [made]}, {("iron-ingot", 1): [eaten]})
    assert caught.value.reason == "a row is fed from the corridor on the other side of the build"


def test_an_item_nothing_supplies_is_this_packages_own_bug_and_not_a_refusal() -> None:
    """``SfyBuildSpec`` turns that spec away at construction, so a spec that
    reaches the layout stage with one is not a build anybody asked for."""
    planner = _planner(flow_spec("iron-plate-60"))
    eaten = _Terminal(
        item="iron-ingot", rate=Fraction(1), side=1, y=500.0, wall=False, link=(2, "Input1")
    )
    with pytest.raises(ValueError, match="bug in this package"):
        planner._nets_for("iron-ingot", {}, {("iron-ingot", 1): [eaten]})
