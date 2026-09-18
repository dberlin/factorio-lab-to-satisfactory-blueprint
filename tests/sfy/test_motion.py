"""Registry geometry boundaries exercised through the actual interval router."""

from __future__ import annotations

from fractions import Fraction
from itertools import count

import pytest

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.layout.lattice import Node, occupancy_for
from flab2bp.sfy.layout.manifold import SPLITTER_CLASS
from flab2bp.sfy.layout.model import AttachmentObj, Pose, SfyPlacement, Vector
from flab2bp.sfy.layout.motion import motion_profile
from flab2bp.sfy.layout.realise import Terminal, realise
from flab2bp.sfy.layout.router import Routed, RouteFailureKind, route_net
from flab2bp.sfy.layout.transitions import incline_run_nodes, sfy_transitions
from flab2bp.sfy.layout.validate import validate
from tests.sfy.test_rrr import BELT, ITEM, LIFT, _lattice, _measures, _registry


def _fixed(
    path: tuple[Node, ...],
    *,
    sink_facing: Vector = (0.0, -1.0, 0.0),
    sink_world: Vector | None = None,
    lift_heights: range = range(0),
    obstacles: tuple[AttachmentObj, ...] = (),
) -> tuple[Routed, Terminal, Terminal]:
    lattice = _lattice()
    source = Terminal(path[0], lattice.world(path[0]), (1.0, 0.0, 0.0), None, "wall")
    sink = Terminal(
        path[-1],
        lattice.world(path[-1]) if sink_world is None else sink_world,
        sink_facing,
        None,
        "wall",
    )
    allowed = frozenset(path)
    closed = tuple(
        (x, y, z)
        for x in range(lattice.n + 1)
        for y in range(lattice.n + 1)
        for z in range(lattice.n + 1)
        if (x, y, z) not in allowed
    )
    transitions = sfy_transitions(
        lattice.n + 1, lift_heights, incline_run_nodes(_registry().limits, lattice.grid_cm)
    )
    routed = route_net(
        occupancy_for(lattice, (), obstacles, (), (), _registry()),
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


@pytest.mark.parametrize("shared,legal", ((4, False), (5, True)))
def test_consecutive_attachment_turns_reserve_their_rendered_bodies(
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


@pytest.mark.parametrize("flat,legal", ((1, False), (2, True)))
@pytest.mark.parametrize("following", ("lift", "slope", "sink"))
def test_lift_connector_requires_a_real_belt_and_clear_shaft_approach(
    flat: int, legal: bool, following: str
) -> None:
    approach = tuple((x, 4, 2) for x in range(4, 9))
    bridge = tuple((x, 4, 6) for x in range(8, 9 + flat))
    x = 8 + flat
    if following == "lift":
        departure = tuple((x + step, 4, 10) for step in range(5))
    elif following == "slope":
        departure = ((x + 1, 4, 6), *((x + step, 4, 5) for step in range(2, 7)))
    else:
        departure = ()
    path = (*approach, *bridge, *departure)
    routed, source, sink = _fixed(path, sink_facing=(-1.0, 0.0, 0.0), lift_heights=range(4, 5))
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
    placement = SfyPlacement(
        designer=_lattice().designer,
        belts=built.belts,
        lifts=built.lifts,
        attachments=built.attachments,
        links=built.links,
    )
    report = validate(placement, None, _registry(), only={"belt.capsule"})
    assert report.ok, report.errors


@pytest.mark.parametrize("separation,legal", ((300.0, False), (500.0, True)))
def test_tight_turn_body_is_admitted_before_the_route_is_selected(
    separation: float, legal: bool
) -> None:
    """A clear belt centreline does not imply room for the required splitter."""
    path = (*((x, 4, 2) for x in range(4, 8)), *((7, y, 2) for y in range(5, 8)))
    x, y, z = _lattice().world((7, 4, 2))
    obstacle = AttachmentObj(1, SPLITTER_CLASS, Pose(x + separation, y + 300, z, 0))
    routed, source, sink = _fixed(path, obstacles=(obstacle,))
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
    report = validate(
        SfyPlacement(
            designer=_lattice().designer,
            attachments=(obstacle, *built.attachments),
            belts=built.belts,
            links=built.links,
        ),
        None,
        _registry(),
        only={"geom.attachment_body", "belt.capsule"},
    )
    assert report.ok, report.errors


@pytest.mark.parametrize("separation,legal", ((300.0, False), (400.0, True)))
def test_a_lift_route_checks_the_native_shaft_between_clear_endpoints(
    separation: float, legal: bool
) -> None:
    path = (
        *((x, 4, 2) for x in range(4, 9)),
        *((x, 4, 10) for x in range(8, 13)),
    )
    x, y, _ = _lattice().world((8, 4, 2))
    obstacle = AttachmentObj(1, SPLITTER_CLASS, Pose(x + separation, y, 600.0, 0))
    routed, source, sink = _fixed(
        path,
        sink_facing=(-1.0, 0.0, 0.0),
        lift_heights=range(8, 9),
        obstacles=(obstacle,),
    )
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
    report = validate(
        SfyPlacement(
            designer=_lattice().designer,
            attachments=(obstacle,),
            belts=built.belts,
            lifts=built.lifts,
            links=built.links,
        ),
        None,
        _registry(),
        only={"geom.attachment_body", "belt.capsule"},
    )
    assert report.ok, report.errors


@pytest.mark.parametrize("separation,legal", ((300.0, False), (500.0, True)))
def test_an_arc_cannot_cut_through_a_body_inside_clear_lattice_legs(
    separation: float, legal: bool
) -> None:
    path = (*((x, 8, 2) for x in range(4, 9)), *((8, y, 2) for y in range(9, 13)))
    x, y, z = _lattice().world((8, 8, 2))
    obstacle = AttachmentObj(1, SPLITTER_CLASS, Pose(x - separation, y + separation, z, 0))
    occupancy = occupancy_for(_lattice(), (), (obstacle,), (), (), _registry())
    assert all(occupancy.free(node) for node in path)
    routed, _, _ = _fixed(path, obstacles=(obstacle,))
    if legal:
        assert routed.path == path
    else:
        assert routed.path is None
        assert routed.kind is RouteFailureKind.MOTION_EXHAUSTED


def test_a_lift_cannot_reverse_in_place_to_evade_a_tight_turn() -> None:
    path = (
        (4, 4, 2),
        (5, 4, 2),
        (6, 4, 2),
        (6, 4, 6),
        (6, 4, 2),
        (6, 5, 2),
        (6, 6, 2),
    )
    routed, _, _ = _fixed(path, lift_heights=range(4, 5))
    assert routed.path is None
    assert routed.kind is RouteFailureKind.MOTION_EXHAUSTED


def test_a_blocked_terminal_arc_keeps_the_clear_attachment_alternative() -> None:
    path = tuple((x, 8, 2) for x in range(4, 9))
    x, y, z = _lattice().world(path[-1])
    obstacle = AttachmentObj(1, SPLITTER_CLASS, Pose(x - 450, y + 300, z, 0))
    routed, source, sink = _fixed(path, sink_world=(x, y + 700, z), obstacles=(obstacle,))
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
    assert built.turns == ("attachment",)
    report = validate(
        SfyPlacement(
            designer=_lattice().designer,
            attachments=(obstacle, *built.attachments),
            belts=built.belts,
            links=built.links,
        ),
        None,
        _registry(),
        only={"geom.attachment_body", "belt.capsule"},
    )
    assert report.ok, report.errors
