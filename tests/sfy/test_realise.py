"""One lattice path, realised as conveyors, attachment turns and lifts.

Nothing here writes a distance down.  Every number a claim rests on -- the grid
step, the turn radius, the attachment's box, the shortest belt this project
authors, the lift window -- is read out of ``registry.json`` through
:class:`~flab2bp.sfy.layout.corridors.Measures` or ``Registry.limits``, and the
few figures that appear are asserted against the read value beside them rather
than instead of it.

The judge is the validator: a realised path is turned into an
:class:`~flab2bp.sfy.layout.model.SfyPlacement` and handed to
:func:`~flab2bp.sfy.layout.validate.validate` with the checks that speak about
belts, ports and lifts.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import replace
from fractions import Fraction
from functools import cache
from itertools import count

import pytest

from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.layout.corridors import ARC, ATTACHMENT, Measures, attachment_turn, turn_radius_cm
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node
from flab2bp.sfy.layout.manifold import shortest_belt_cm
from flab2bp.sfy.layout.model import (
    BeltRun,
    MachineObj,
    Pose,
    SfyPlacement,
    Vector,
    belt_ends,
    lift_geometry,
)
from flab2bp.sfy.layout.realise import Realised, RealiseError, Terminal, realise
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.validate import BELT_CONNECTION_CM, Report, validate
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import designer

BELT = "Build_ConveyorBeltMk1_C"
LIFT = "Build_ConveyorLiftMk1_C"
CONSTRUCTOR = "Build_ConstructorMk1_C"
MANUFACTURER = "Build_ManufacturerMk1_C"
ROD = "Recipe_IronRod_C"
COMPUTER = "Recipe_Computer_C"
ITEM = "iron-rod"
RATE = Fraction(1, 4)

#: A machine stands on the slab, whose top is one hologram grid step up.
SLAB_TOP_CM = 100.0

#: The checks that speak about a belt and the ports it meets.
BELT_CHECKS = frozenset(
    {
        "belt.min_length",
        "belt.max_length",
        "belt.incline",
        "belt.curvature",
        "ports.connected_once",
        "ports.direction",
        "ports.position",
    }
)

#: The checks that speak about a conveyor lift.
LIFT_CHECKS = frozenset({"lift.height", "lift.step", "lift.top_yaw", "lift.placement"})


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _lattice() -> Lattice:
    return Lattice.over(designer("mk1", _registry()), _registry())


@cache
def _measures() -> Measures:
    return _measure(_registry(), designer("mk1", _registry()))


def _ids() -> Iterator[int]:
    return count(1)


def _port(class_name: str, name: str) -> Port:
    return next(p for p in _registry().buildables[class_name].ports if p.name == name)


def _machine(oid: int, class_name: str, x: float, y: float, yaw: float, recipe: str) -> MachineObj:
    return MachineObj(oid, class_name, Pose(x, y, SLAB_TOP_CM, yaw), recipe)


def _flat(vector: Vector) -> Vector:
    span = math.hypot(vector[0], vector[1])
    return (vector[0] / span, vector[1] / span, 0.0)


def _node_on_ray(world: Vector, facing: Vector) -> Node:
    """The first lattice node out of a port, along the port's own normal.

    The dominant axis of the facing is stepped to the next line beyond the port
    and the other two are the lines the port already stands on.  This is the
    arithmetic Task 7 does when it builds a :class:`Terminal`; it lives in the
    test because ``Terminal.node`` is the caller's to state.
    """
    lattice = _lattice()
    grid, half = lattice.grid_cm, lattice.designer.half_cm
    origins = (-half, -half, 0.0)
    axis = max(range(3), key=lambda a: abs(facing[a]))
    lines = [round((world[a] - origins[a]) / grid) for a in range(3)]
    steps = (world[axis] - origins[axis]) / grid
    lines[axis] = math.ceil(steps - 1e-9) if facing[axis] > 0.0 else math.floor(steps + 1e-9)
    return (lines[0], lines[1], lines[2])


def _terminal(machine: MachineObj, name: str) -> Terminal:
    transform = machine.pose.transform()
    port = _port(machine.class_name, name)
    world = world_port(transform, port)
    facing = _flat(port_forward(transform, port))
    return Terminal(
        node=_node_on_ray(world, facing),
        world=world,
        facing=facing,
        port=(machine.id, name),
        kind="port",
    )


def _wall(node: Node, facing: Vector) -> Terminal:
    return Terminal(node=node, world=_lattice().world(node), facing=facing, port=None, kind="wall")


def _line(a: Node, b: Node) -> tuple[Node, ...]:
    """The nodes from ``a`` to ``b`` along the one axis they differ on."""
    axis = next(i for i in range(3) if a[i] != b[i])
    step = 1 if b[axis] > a[axis] else -1
    out: list[Node] = []
    node = a
    while node != b:
        out.append(node)
        moved = list(node)
        moved[axis] += step
        node = (moved[0], moved[1], moved[2])
    out.append(b)
    return tuple(out)


def _join(*legs: Sequence[Node]) -> tuple[Node, ...]:
    """Legs laid end to end, with the shared node written once."""
    out: list[Node] = []
    for leg in legs:
        out.extend(leg[1:] if out and out[-1] == leg[0] else leg)
    return tuple(out)


def _run(
    path: Sequence[Node],
    source: Terminal,
    sink: Terminal,
    *,
    measures: Measures | None = None,
) -> Realised:
    return realise(
        path,
        source=source,
        sink=sink,
        lattice=_lattice(),
        measures=measures if measures is not None else _measures(),
        registry=_registry(),
        belt_class=BELT,
        lift_class=LIFT,
        item_id=ITEM,
        rate=RATE,
        ids=_ids(),
    )


def _placement(realised: Realised, *machines: MachineObj) -> SfyPlacement:
    return SfyPlacement(
        designer=designer("mk1", _registry()),
        machines=machines,
        attachments=realised.attachments,
        belts=realised.belts,
        lifts=realised.lifts,
        links=realised.links,
    )


def _judge(realised: Realised, *machines: MachineObj, only: frozenset[str]) -> Report:
    report = validate(_placement(realised, *machines), None, _registry(), only=only)
    assert report.errors == (), [f.message for f in report.errors]
    assert set(report.skipped).isdisjoint(only), report.skipped
    return report


def _chord_cm(belt: BeltRun) -> float:
    """The length ``belt.min_length`` measures: the polyline, not the arc."""
    return sum(math.dist(a[0], b[0]) for a, b in zip(belt.points, belt.points[1:], strict=False))


# --- a straight run --------------------------------------------------------


def _facing_constructors() -> tuple[MachineObj, MachineObj]:
    """Two Constructors on one line, the first's output aimed at the second's input."""
    return (
        _machine(101, CONSTRUCTOR, 0.0, -600.0, 0.0, ROD),
        _machine(102, CONSTRUCTOR, 0.0, 600.0, 0.0, ROD),
    )


def test_a_straight_path_between_two_constructors_is_one_belt() -> None:
    maker, eater = _facing_constructors()
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")
    realised = _run(_line(source.node, sink.node), source, sink)

    assert len(realised.belts) == 1
    assert realised.attachments == ()
    assert realised.lifts == ()
    assert realised.lift_columns == ()
    belt = realised.belts[0]
    assert belt.start == source.world
    assert belt.end == sink.world
    assert _chord_cm(belt) == pytest.approx(math.dist(source.world, sink.world))
    _judge(realised, maker, eater, only=BELT_CHECKS)


# --- a corner --------------------------------------------------------------


def _l_path(corner_legs: int) -> tuple[tuple[Node, ...], MachineObj, MachineObj]:
    """A maker, an eater around the corner from it, and the path between them."""
    grid = _lattice().grid_cm
    maker = _machine(101, CONSTRUCTOR, 0.0, -600.0, 0.0, ROD)
    source = _terminal(maker, "Output0")
    corner = (source.node[0], source.node[1] + corner_legs, source.node[2])
    landing = (corner[0] + corner_legs, corner[1], corner[2])
    mouth = _lattice().world(landing)
    eater = _machine(102, CONSTRUCTOR, mouth[0] + 3.0 * grid, mouth[1], -90.0, ROD)
    sink = _terminal(eater, "Input0")
    assert sink.node == landing
    return (_join(_line(source.node, corner), _line(corner, landing)), maker, eater)


def test_an_l_path_with_four_node_legs_is_an_arc_or_an_attachment_and_says_which() -> None:
    """Which turn is taken is the registry's answer, and the result shows it.

    With the shipped registry an attachment turns inside its own box plus the
    shortest legal belt and an arc costs a whole turn radius, so the attachment
    is the cheaper of the two and stands in the build; drop the radius under
    the attachment's cost and the same path comes back as one unbroken belt
    with no object on the corner at all.
    """
    measures = _measures()
    assert attachment_turn(measures).cost < turn_radius_cm(_registry())

    path, maker, eater = _l_path(4)
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")

    chosen = _run(path, source, sink)
    assert len(chosen.attachments) == 1
    assert len(chosen.belts) == 2
    assert chosen.attachments[0].class_name != ""
    _judge(chosen, maker, eater, only=BELT_CHECKS)

    tight = replace(measures, radius=attachment_turn(measures).cost - measures.grid)
    arced = _run(path, source, sink, measures=tight)
    assert arced.attachments == ()
    assert len(arced.belts) == 1

    assert (ARC, ATTACHMENT) == ("arc", "attachment")


def test_a_corner_with_two_node_legs_refuses_naming_the_corner() -> None:
    path, maker, eater = _l_path(2)
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")
    corner = (source.node[0], source.node[1] + 2, source.node[2])

    with pytest.raises(RealiseError) as caught:
        _run(path, source, sink)
    assert caught.value.cause == "corner"
    assert corner in caught.value.nodes


def test_a_cut_one_node_from_a_port_is_refused_as_a_leg() -> None:
    """R-M3-4 from the other side: one node of lattice is under the floor.

    A lift one node out of the port would want a 100 cm conveyor between them,
    and ``belt.min_length`` refuses at or under its own floor -- so the shortest
    belt this project authors is a centimetre more than a node is wide.
    """
    grid = _lattice().grid_cm
    assert grid < shortest_belt_cm(_registry().limits)

    maker = _machine(101, CONSTRUCTOR, 0.0, -600.0, 0.0, ROD)
    source = _terminal(maker, "Output0")
    foot = (source.node[0], source.node[1] + 1, source.node[2])
    head = (foot[0], foot[1], foot[2] + _lift_levels())
    top = (head[0], _lattice().open_lines.stop - 1, head[2])
    path = _join((source.node, foot), (foot, head), _line(head, top))

    with pytest.raises(RealiseError) as caught:
        _run(path, source, _wall(top, (0.0, -1.0, 0.0)))
    assert caught.value.cause == "leg"
    assert foot in caught.value.nodes


# --- a climb ---------------------------------------------------------------


def test_an_incline_run_is_one_leg_at_thirty_five_degrees_or_under() -> None:
    """The via node is not a corner: one flat step and one up step are one leg."""
    limit = _registry().limits.belt_max_incline_deg
    assert limit is not None
    maker = _machine(101, CONSTRUCTOR, 0.0, -600.0, 0.0, ROD)
    source = _terminal(maker, "Output0")
    foot = (source.node[0], source.node[1] + 4, source.node[2])
    via = (foot[0], foot[1] + 1, foot[2])
    crest = (foot[0], foot[1] + 2, foot[2] + 1)
    top = (crest[0], _lattice().open_lines.stop - 1, crest[2])
    sink = _wall(top, (0.0, -1.0, 0.0))
    path = _join(_line(source.node, foot), (foot, via, crest), _line(crest, top))

    realised = _run(path, source, sink)
    assert len(realised.belts) == 1
    assert realised.attachments == ()
    assert realised.lifts == ()

    belt = realised.belts[0]
    climbs = [
        (a[0], b[0])
        for a, b in zip(belt.points, belt.points[1:], strict=False)
        if abs(b[0][2] - a[0][2]) > 1e-9
    ]
    assert len(climbs) == 1
    head, tail = climbs[0]
    angle = math.degrees(
        math.atan2(
            abs(tail[2] - head[2]), math.dist((head[0], head[1], 0.0), (tail[0], tail[1], 0.0))
        )
    )
    assert angle <= limit
    _judge(realised, maker, only=BELT_CHECKS)


# --- a lift ----------------------------------------------------------------


def _lift_levels() -> int:
    """The shortest lift the hologram clamps to, in levels."""
    limits = _registry().limits
    assert limits.lift_min_cm is not None
    return round(limits.lift_min_cm / _lattice().grid_cm)


def test_a_lift_of_four_levels_becomes_a_lift_object_that_validates() -> None:
    levels = _lift_levels()
    assert levels == 4
    maker = _machine(101, CONSTRUCTOR, 0.0, -600.0, 0.0, ROD)
    source = _terminal(maker, "Output0")
    foot = (source.node[0], source.node[1] + 4, source.node[2])
    head = (foot[0], foot[1], foot[2] + levels)
    top = (head[0], _lattice().open_lines.stop - 1, head[2])
    sink = _wall(top, (0.0, -1.0, 0.0))
    path = _join(_line(source.node, foot), (foot, head), _line(head, top))

    realised = _run(path, source, sink)
    assert len(realised.lifts) == 1
    assert len(realised.belts) == 2
    assert realised.lift_columns == ((foot, head),)

    lift = realised.lifts[0]
    assert lift.height_cm == levels * _lattice().grid_cm
    geometry = lift_geometry(_registry(), LIFT)
    bottom, _ = lift.bottom_end(geometry)
    crown, _ = lift.top_end(geometry)
    assert math.dist(bottom, realised.belts[0].end) <= BELT_CONNECTION_CM
    assert math.dist(crown, realised.belts[1].start) <= BELT_CONNECTION_CM
    _judge(realised, maker, only=LIFT_CHECKS)
    _judge(realised, maker, only=BELT_CHECKS)


def test_a_lift_on_a_port_node_connects_without_a_belt() -> None:
    levels = _lift_levels()
    maker = _machine(101, CONSTRUCTOR, 0.0, -600.0, 0.0, ROD)
    source = _terminal(maker, "Output0")
    head = (source.node[0], source.node[1], source.node[2] + levels)
    top = (head[0], _lattice().open_lines.stop - 1, head[2])
    sink = _wall(top, (0.0, -1.0, 0.0))
    path = _join((source.node, head), _line(head, top))

    realised = _run(path, source, sink)
    assert len(realised.lifts) == 1
    assert len(realised.belts) == 1

    lift = realised.lifts[0]
    entry, _ = belt_ends(_registry(), LIFT)
    assert any(link.a == source.port and link.b == (lift.id, entry) for link in realised.links)
    bottom, _ = lift.bottom_end(lift_geometry(_registry(), LIFT))
    assert math.dist(bottom, source.world) <= BELT_CONNECTION_CM
    _judge(realised, maker, only=LIFT_CHECKS)
    _judge(realised, maker, only=BELT_CHECKS)


# --- a port that does not stand on a node ----------------------------------


def test_a_manufacturer_input_gets_its_stub_inside_the_first_belt() -> None:
    """The Manufacturer's inputs sit 875 cm out, which is 25 cm off the lattice.

    The piece from the node to the port is the first (here the last) piece of
    the belt beside it, never a belt of its own: one 25 cm conveyor would be
    under ``belt.min_length``'s floor and the game would refuse it.
    """
    grid = _lattice().grid_cm
    offset = abs(_port(MANUFACTURER, "Input0").translation[1])
    assert offset % grid != 0.0

    maker = _machine(101, CONSTRUCTOR, 0.0, -1100.0, 0.0, ROD)
    eater = _machine(102, MANUFACTURER, -600.0, 600.0, 0.0, COMPUTER)
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")
    assert _lattice().world(sink.node) != sink.world

    realised = _run(_line(source.node, sink.node), source, sink)
    assert len(realised.belts) == 1
    belt = realised.belts[0]
    assert belt.end == sink.world
    assert _chord_cm(belt) == pytest.approx(math.dist(source.world, sink.world))
    _judge(realised, maker, eater, only=BELT_CHECKS)


# --- every belt this module authors ----------------------------------------


def _room(node: Node, step: tuple[int, int], want: int) -> bool:
    lines = _lattice().open_lines
    end = (node[0] + step[0] * want, node[1] + step[1] * want)
    return end[0] in lines and end[1] in lines


def _random_path(rng: random.Random) -> tuple[tuple[Node, ...], Vector, Vector]:
    """A path of the shape the router returns: long straights, 2:1 climbs, lifts.

    Legs are at least seven nodes so that a turn at each end has the room the
    attachment costs, and every climb and every lift is followed by seven more,
    so that this is a test about the belts rather than about the corners.  The
    path never ends on a lift, because a lift at the designer wall is a terminal
    with no belt to reach it and rule 4's business rather than R-M3-4's.
    """
    lines = _lattice().open_lines
    levels = _lattice().open_levels
    climb = _lift_levels()
    node: Node = (lines.start + 8, lines.start + 8, GROUND_LEVEL)
    step = rng.choice([(1, 0), (0, 1)])
    path: list[Node] = [node]
    entry = (float(step[0]), float(step[1]), 0.0)
    for leg in range(rng.randrange(3, 6)):
        if leg:
            turn = rng.choice([(-step[1], step[0]), (step[1], -step[0])])
            if not _room(node, turn, 14):
                turn = (-turn[0], -turn[1])
                if not _room(node, turn, 14):
                    break
            step = turn
        want = rng.randrange(7, 13)
        if not _room(node, step, want + 2):
            break
        for _ in range(want):
            node = (node[0] + step[0], node[1] + step[1], node[2])
            path.append(node)
        roll = rng.random()
        if roll < 0.3 and node[2] + 1 in levels and _room(node, step, 9):
            for rise in (0, 1):
                node = (node[0] + step[0], node[1] + step[1], node[2] + rise)
                path.append(node)
        elif roll < 0.5 and node[2] + climb in levels and _room(node, step, 7):
            node = (node[0], node[1], node[2] + climb)
            path.append(node)
        else:
            continue
        for _ in range(7):
            node = (node[0] + step[0], node[1] + step[1], node[2])
            path.append(node)
    leave = (float(step[0]), float(step[1]), 0.0)
    return (tuple(path), entry, leave)


@pytest.mark.parametrize("seed", range(24))
def test_every_belt_is_longer_than_the_minimum(seed: int) -> None:
    floor = shortest_belt_cm(_registry().limits)
    path, entry, leave = _random_path(random.Random(seed))
    if len(path) < 2:
        pytest.skip("the walk found no room to start")
    realised = _run(path, _wall(path[0], entry), _wall(path[-1], (-leave[0], -leave[1], 0.0)))
    assert realised.belts

    columns = tuple(
        (a, b) if b[2] > a[2] else (b, a)
        for a, b in zip(path, path[1:], strict=False)
        if a[:2] == b[:2] and a[2] != b[2]
    )
    assert realised.lift_columns == columns
    assert len(realised.lifts) == len(columns)
    for belt in realised.belts:
        assert _chord_cm(belt) >= floor
    _judge(realised, only=BELT_CHECKS | LIFT_CHECKS)
