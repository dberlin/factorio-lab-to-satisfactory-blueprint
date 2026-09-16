"""Registry geometry boundaries exercised through the actual interval router."""

from __future__ import annotations

from fractions import Fraction
from itertools import count

import pytest

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.layout.lattice import Node
from flab2bp.sfy.layout.model import Vector
from flab2bp.sfy.layout.motion import motion_profile
from flab2bp.sfy.layout.realise import Terminal, realise
from flab2bp.sfy.layout.router import Routed, RouteFailureKind, route_net
from flab2bp.sfy.layout.transitions import incline_run_nodes, sfy_transitions
from tests.sfy.test_rrr import BELT, ITEM, LIFT, _lattice, _measures, _occupancy, _registry


def _fixed(
    path: tuple[Node, ...], *, sink_facing: Vector = (0.0, -1.0, 0.0)
) -> tuple[Routed, Terminal, Terminal]:
    lattice = _lattice()
    source = Terminal(path[0], lattice.world(path[0]), (1.0, 0.0, 0.0), None, "wall")
    sink = Terminal(path[-1], lattice.world(path[-1]), sink_facing, None, "wall")
    allowed = frozenset(path)
    closed = tuple(
        (x, y, z)
        for x in range(lattice.n + 1)
        for y in range(lattice.n + 1)
        for z in range(lattice.n + 1)
        if (x, y, z) not in allowed
    )
    transitions = sfy_transitions(
        lattice.n + 1, range(0), incline_run_nodes(_registry().limits, lattice.grid_cm)
    )
    routed = route_net(
        _occupancy(),
        starts=(source.node,),
        goals=(sink.node,),
        closed=closed,
        pressure=0.0,
        budget=WorkBudget(left=200_000),
        deadline=None,
        transitions=transitions,
        profile=motion_profile(lattice, _measures(), _registry(), LIFT, transitions),
        sources=(source,),
        sinks=(sink,),
    )
    return routed, source, sink


def _ramps(start: Node, heading: tuple[int, int], amount: int) -> tuple[Node, ...]:
    nodes = [start]
    x, y, z = start
    dx, dy = heading
    for _ in range(amount):
        nodes.append((x + dx, y + dy, z))
        x, y, z = x + 2 * dx, y + 2 * dy, z + 1
        nodes.append((x, y, z))
    return tuple(nodes)


@pytest.mark.parametrize("amount,legal", ((6, False), (7, True)))
@pytest.mark.parametrize("outgoing", (False, True), ids=("incoming-reserve", "outgoing-debt"))
def test_merged_ramp_reserve_and_same_heading_slope_closure(
    amount: int,
    legal: bool,
    outgoing: bool,
) -> None:
    """Six merged ramps leave 300 cm, seven 400; flat credit cannot pay ramp debt."""
    if outgoing:
        approach = tuple((x, 4, 2) for x in range(4, 9))
        ramps = _ramps(approach[-1], (0, 1), amount)
        x, y, z = ramps[-1]
        path = (*approach, *ramps[1:], *((x, y + step, z) for step in range(1, 5)))
    else:
        ramps = _ramps((4, 4, 2), (1, 0), amount)
        x, y, z = ramps[-1]
        path = (*ramps, *((x, y + step, z) for step in range(1, 5)))
    routed, source, sink = _fixed(path)
    if not legal:
        assert routed.path is None
        assert routed.kind is RouteFailureKind.MOTION_EXHAUSTED
        assert routed.wall == ()
        return
    assert routed.path == path
    assert routed.motion is not None
    built = realise(
        path,
        source=source,
        sink=sink,
        lattice=_lattice(),
        measures=_measures(),
        registry=_registry(),
        belt_class=BELT,
        lift_class=LIFT,
        item_id=ITEM,
        rate=Fraction(1, 8),
        ids=count(1000),
        motion=routed.motion,
    )
    assert built.turns == ("arc",)


@pytest.mark.parametrize("shared,legal", ((3, False), (4, True)))
def test_consecutive_attachment_turns_share_previous_reach_not_previous_cost(
    shared: int,
    legal: bool,
) -> None:
    approach = tuple((x, 4, 2) for x in range(4, 9))
    middle = tuple((8, y, 2) for y in range(5, 5 + shared))
    path = (*approach, *middle, *((x, 4 + shared, 2) for x in range(9, 13)))
    routed, source, sink = _fixed(path, sink_facing=(-1.0, 0.0, 0.0))
    if not legal:
        assert routed.path is None
        assert routed.kind is RouteFailureKind.MOTION_EXHAUSTED
        return
    assert routed.path == path
    assert routed.motion is not None
    built = realise(
        path,
        source=source,
        sink=sink,
        lattice=_lattice(),
        measures=_measures(),
        registry=_registry(),
        belt_class=BELT,
        lift_class=LIFT,
        item_id=ITEM,
        rate=Fraction(1, 8),
        ids=count(1000),
        motion=routed.motion,
    )
    assert built.turns == ("attachment", "attachment")
