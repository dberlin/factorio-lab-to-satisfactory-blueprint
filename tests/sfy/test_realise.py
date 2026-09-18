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

from flab2bp.layout.budget import WorkBudget
from flab2bp.layout.geometric_motion import MotionStep, MotionWitness
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.layout.corridors import (
    ARC,
    ATTACHMENT,
    Measures,
    attachment_turn,
    attachment_turn_tight,
)
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node, Occupancy, occupancy_for
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
from flab2bp.sfy.layout.motion import motion_moves
from flab2bp.sfy.layout.realise import Realised, RealiseError, Terminal, realise
from flab2bp.sfy.layout.router import route_net
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.transitions import incline_run_nodes, sfy_transitions
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


@cache
def _transitions(lifts: bool = True) -> tuple[tuple[tuple[int, int, int, bool, float], ...], ...]:
    """The movement table for this lattice, every number of it read.

    The lift window is ``Registry.limits``' own, in levels, clamped by the
    designer (R-M3-3); the ramp run is
    :func:`~flab2bp.sfy.layout.transitions.incline_run_nodes`'.  ``lifts=False``
    is a table with the lift family left out, which is a perfectly legal build
    (``sfy_transitions`` says so) and is how a test asks for a path that has to
    ramp over what is in its way rather than hop it.
    """
    lattice, limits = _lattice(), _registry().limits
    assert limits.lift_min_cm is not None
    assert limits.lift_max_cm is not None
    assert limits.lift_step_cm is not None
    grid = lattice.grid_cm
    heights = range(
        round(limits.lift_min_cm / grid),
        min(round(limits.lift_max_cm / grid), lattice.n - GROUND_LEVEL) + 1,
        round(limits.lift_step_cm / grid),
    )
    return sfy_transitions(
        lattice.n + 1, heights if lifts else range(0), incline_run_nodes(limits, grid)
    )


def _wall_of_constructors() -> tuple[tuple[MachineObj, ...], Occupancy]:
    """A line of Constructors across one row, wall to wall: no way round, only over."""
    lattice = _lattice()
    machines = tuple(
        _machine(200 + i, CONSTRUCTOR, lattice.world((c, 0, 0))[0], 0.0, 0.0, ROD)
        for i, c in enumerate(range(0, lattice.n + 1, 4))
    )
    return (machines, occupancy_for(lattice, machines, (), (), (), _registry()))


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
    motion: MotionWitness | None = None,
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
        motion=motion,
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
    assert set(report.skipped).isdisjoint(only - {"belt.capsule"}), report.skipped
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


def test_a_legal_l_path_prefers_one_continuous_belt_over_a_turning_attachment() -> None:
    path, maker, eater = _l_path(7)
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")
    chosen = _run(path, source, sink)
    assert chosen.turns == (ARC,)
    assert chosen.attachments == ()
    assert len(chosen.belts) == 1
    _judge(chosen, maker, eater, only=BELT_CHECKS | {"belt.capsule"})


def test_a_corner_with_three_node_legs_is_laid_as_an_attachment_turn() -> None:
    """A tight turn reserves its body and perpendicular belt, not soft emptiness."""
    grid = _lattice().grid_cm
    measures = _measures()
    assert attachment_turn_tight(measures).cost < 3.0 * grid < attachment_turn(measures).cost

    path, maker, eater = _l_path(3)
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")
    realised = _run(path, source, sink)

    assert realised.turns == (ATTACHMENT,)
    assert len(realised.attachments) == 1
    assert len(realised.belts) == 2
    _judge(realised, maker, eater, only=BELT_CHECKS)


def test_two_attachment_turns_share_one_minimum_belt_on_the_leg_between_them() -> None:
    lattice, measures = _lattice(), _measures()
    gap = math.ceil(max(2 * measures.box, 2 * measures.reach + measures.lead_in) / lattice.grid_cm)

    def path_with_gap(steps: int) -> tuple[Node, ...]:
        first = (10, 10, GROUND_LEVEL)
        second = (10, 10 + steps, GROUND_LEVEL)
        return _join(
            _line((5, 10, GROUND_LEVEL), first),
            _line(first, second),
            _line(second, (15, 10 + steps, GROUND_LEVEL)),
        )

    path = path_with_gap(gap)
    realised = _run_wall_corner(path)
    assert realised.turns == (ATTACHMENT, ATTACHMENT)
    assert len(realised.belts) == 3
    assert _chord_cm(realised.belts[1]) == gap * lattice.grid_cm - 2 * measures.reach
    _judge(realised, only=BELT_CHECKS | {"geom.bounds", "geom.attachment_body", "belt.capsule"})

    with pytest.raises(RealiseError) as caught:
        _run_wall_corner(path_with_gap(gap - 1))
    assert caught.value.cause == "corner"
    assert (10, 10 + gap - 1, GROUND_LEVEL) in caught.value.nodes


@pytest.mark.parametrize("at_source", [True, False])
def test_exact_node_terminal_refuses_a_sideways_heading(at_source: bool) -> None:
    path = _line((5, 10, GROUND_LEVEL), (10, 10, GROUND_LEVEL))
    source = _wall(path[0], (0.0, 1.0, 0.0) if at_source else (1.0, 0.0, 0.0))
    sink = _wall(path[-1], (-1.0, 0.0, 0.0) if at_source else (0.0, 1.0, 0.0))
    with pytest.raises(RealiseError) as caught:
        _run(path, source, sink)
    assert caught.value.cause == "stub"
    assert (path[0] if at_source else path[-1]) in caught.value.nodes


def _flat_motion(path: Sequence[Node], selected: dict[int, int]) -> MotionWitness:
    shapes = motion_moves(_lattice(), _registry(), LIFT)
    return MotionWitness(
        initial_state=0,
        final_state=0,
        steps=tuple(
            MotionStep(
                path_index=index,
                move=shapes.index((b[0] - a[0], b[1] - a[1], b[2] - a[2], False)),
                source=0,
                target=0,
                action=selected.get(index, -1),
            )
            for index, (a, b) in enumerate(zip(path, path[1:], strict=False))
        ),
        final_action=selected.get(len(path) - 1, -1),
    )


def test_witness_keeps_selected_attachment_instead_of_preferred_arc() -> None:
    z = GROUND_LEVEL
    path = _join(_line((5, 10, z), (10, 10, z)), _line((10, 10, z), (10, 15, z)))
    source, sink = _wall(path[0], (1.0, 0.0, 0.0)), _wall(path[-1], (0.0, -1.0, 0.0))
    realised = _run(path, source, sink, motion=_flat_motion(path, {5: 1}))
    assert realised.turns == (ATTACHMENT,)
    assert len(realised.attachments) == 1
    _judge(realised, only=BELT_CHECKS)


def test_witness_does_not_substitute_a_feasible_turn_for_an_infeasible_selection() -> None:
    z = GROUND_LEVEL
    path = _join(
        _line((5, 10, z), (10, 10, z)),
        _line((10, 10, z), (10, 17, z)),
        _line((10, 17, z), (15, 17, z)),
    )
    source, sink = _wall(path[0], (1.0, 0.0, 0.0)), _wall(path[-1], (-1.0, 0.0, 0.0))
    realised = _run(path, source, sink, motion=_flat_motion(path, {5: 1, 12: 0}))
    assert realised.turns == (ATTACHMENT, ARC)
    with pytest.raises(RealiseError) as caught:
        _run(path, source, sink, motion=_flat_motion(path, {5: 0, 12: 0}))
    assert caught.value.cause == "corner"


@pytest.mark.parametrize("selected", [{}, {5: 2}, {4: 0, 5: 0}])
def test_witness_refuses_missing_invalid_or_extra_turn_actions(selected: dict[int, int]) -> None:
    z = GROUND_LEVEL
    path = _join(_line((5, 10, z), (10, 10, z)), _line((10, 10, z), (10, 15, z)))
    with pytest.raises(RealiseError) as caught:
        _run(
            path,
            _wall(path[0], (1.0, 0.0, 0.0)),
            _wall(path[-1], (0.0, -1.0, 0.0)),
            motion=_flat_motion(path, selected),
        )
    assert caught.value.cause == "corner"


@pytest.mark.parametrize("defect", ["index", "shape", "missing", "extra"])
def test_witness_refuses_changed_primitive_sequence(defect: str) -> None:
    path = _line((5, 10, GROUND_LEVEL), (10, 10, GROUND_LEVEL))
    motion = _flat_motion(path, {})
    steps = list(motion.steps)
    if defect == "index":
        steps[0] = replace(steps[0], path_index=1)
    elif defect == "shape":
        shapes = motion_moves(_lattice(), _registry(), LIFT)
        steps[0] = replace(steps[0], move=shapes.index((0, 1, 0, False)))
    elif defect == "missing":
        steps.pop()
    else:
        steps.append(steps[-1])
    with pytest.raises(RealiseError) as caught:
        _run(
            path,
            _wall(path[0], (1.0, 0.0, 0.0)),
            _wall(path[-1], (-1.0, 0.0, 0.0)),
            motion=replace(motion, steps=tuple(steps)),
        )
    assert caught.value.cause == "leg"


def test_source_stub_corner_action_belongs_to_first_native_primitive() -> None:
    z = GROUND_LEVEL
    path = _line((10, 10, z), (10, 15, z))
    world = _lattice().world(path[0])
    source = Terminal(
        node=path[0],
        world=(world[0] - 500.0, world[1], world[2]),
        facing=(1.0, 0.0, 0.0),
        port=None,
        kind="wall",
    )
    sink = _wall(path[-1], (0.0, -1.0, 0.0))
    realised = _run(path, source, sink, motion=_flat_motion(path, {0: 0}))
    assert realised.turns == (ARC,)
    with pytest.raises(RealiseError) as caught:
        _run(path, source, sink, motion=_flat_motion(path, {}))
    assert caught.value.cause == "corner"


@pytest.mark.parametrize("action,kind", ((0, ARC), (1, ATTACHMENT)))
def test_sink_stub_corner_replays_its_explicit_endpoint_action(action: int, kind: str) -> None:
    path = _line((5, 10, GROUND_LEVEL), (10, 10, GROUND_LEVEL))
    world = _lattice().world(path[-1])
    sink = Terminal(
        node=path[-1],
        world=(world[0], world[1] + 500.0, world[2]),
        facing=(0.0, -1.0, 0.0),
        port=None,
        kind="wall",
    )
    realised = _run(
        path,
        _wall(path[0], (1.0, 0.0, 0.0)),
        sink,
        motion=_flat_motion(path, {len(path) - 1: action}),
    )
    assert realised.turns == (kind,)
    with pytest.raises(RealiseError) as caught:
        _run(path, _wall(path[0], (1.0, 0.0, 0.0)), sink, motion=_flat_motion(path, {}))
    assert caught.value.cause == "corner"


def test_witness_ramp_counts_via_atomically_and_requires_source_level() -> None:
    z = GROUND_LEVEL
    path = ((10, 10, z), (11, 10, z), (12, 10, z + 1), (13, 10, z + 1))
    shapes = motion_moves(_lattice(), _registry(), LIFT)
    motion = MotionWitness(
        initial_state=0,
        final_state=0,
        steps=(
            MotionStep(0, shapes.index((2, 0, 1, True)), 0, 0),
            MotionStep(2, shapes.index((1, 0, 0, False)), 0, 0),
        ),
    )
    source, sink = _wall(path[0], (1.0, 0.0, 0.0)), _wall(path[-1], (-1.0, 0.0, 0.0))
    realised = _run(path, source, sink, motion=motion)
    assert realised.turns == ()
    assert realised.belts[0].points[-1][0] == sink.world
    wrong_via = (path[0], (11, 10, z + 1), *path[2:])
    with pytest.raises(RealiseError) as caught:
        _run(wrong_via, source, sink, motion=motion)
    assert caught.value.cause == "leg"


def test_a_corner_with_two_node_legs_refuses_naming_the_corner() -> None:
    path, maker, eater = _l_path(2)
    source, sink = _terminal(maker, "Output0"), _terminal(eater, "Input0")
    corner = (source.node[0], source.node[1] + 2, source.node[2])

    with pytest.raises(RealiseError) as caught:
        _run(path, source, sink)
    assert caught.value.cause == "corner"
    assert corner in caught.value.nodes


def _wall_corner(line: int, axis: int = 0, upper: bool = False) -> tuple[tuple[Node, ...], Node]:
    """An L path along a wall, turning inward with room for either turn."""
    lattice = _lattice()
    leg = math.ceil(_measures().radius / lattice.grid_cm) + 1
    middle = lattice.n // 2

    def node(normal: int, tangent: int) -> Node:
        normal = lattice.n - normal if upper else normal
        i, j = (normal, tangent) if axis == 0 else (tangent, normal)
        return (i, j, GROUND_LEVEL)

    corner = node(line, middle)
    path = _join(
        _line(node(line, middle - leg), corner),
        _line(corner, node(line + leg, middle)),
    )
    return path, corner


def _run_wall_corner(path: Sequence[Node], *, measures: Measures | None = None) -> Realised:
    source = (float(path[1][0] - path[0][0]), float(path[1][1] - path[0][1]), 0.0)
    sink = (float(path[-2][0] - path[-1][0]), float(path[-2][1] - path[-1][1]), 0.0)
    return _run(path, _wall(path[0], source), _wall(path[-1], sink), measures=measures)


@pytest.mark.parametrize(("axis", "upper"), [(0, False), (0, True), (1, False), (1, True)])
def test_a_wall_corner_refuses_an_attachment_and_names_its_nodes(axis: int, upper: bool) -> None:
    path, corner = _wall_corner(_lattice().open_lines.start, axis, upper)
    leg = math.ceil(attachment_turn_tight(_measures()).cost / _lattice().grid_cm)
    at = path.index(corner)
    path = path[at - leg : at + leg + 1]
    with pytest.raises(RealiseError) as caught:
        _run_wall_corner(path)
    assert caught.value.cause == "corner"
    at = path.index(corner)
    assert caught.value.nodes == path[at - 1 : at + 2]


def test_an_attachment_can_stand_on_the_first_object_line() -> None:
    path, corner = _wall_corner(_lattice().object_lines.start)
    leg = math.ceil(attachment_turn_tight(_measures()).cost / _lattice().grid_cm)
    at = path.index(corner)
    path = path[at - leg : at + leg + 1]
    realised = _run_wall_corner(path)
    assert realised.turns == (ATTACHMENT,)
    assert realised.attachments[0].pose.location[:2] == _lattice().world(corner)[:2]
    _judge(realised, only=BELT_CHECKS | {"geom.bounds"})


def test_a_legal_arc_near_the_wall_needs_no_object_standing_room() -> None:
    path, _ = _wall_corner(_lattice().open_lines.start)
    realised = _run_wall_corner(path)
    assert realised.turns == (ARC,)
    assert realised.attachments == ()
    assert len(realised.belts) == 1
    _judge(realised, only=BELT_CHECKS | {"geom.bounds"})


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


def _incline_corner(levels: int) -> tuple[tuple[Node, ...], Node]:
    path = [(10, 10, GROUND_LEVEL)]
    for level in range(levels):
        path.extend(
            (
                (10, 11 + 2 * level, GROUND_LEVEL + level),
                (10, 12 + 2 * level, GROUND_LEVEL + level + 1),
            )
        )
    crest = path[-1]
    path.extend(_line(crest, (15, crest[1], crest[2]))[1:])
    return tuple(path), crest


@pytest.mark.parametrize("at_start", [True, False])
def test_an_attachment_turn_cannot_cut_a_long_incline(at_start: bool) -> None:
    """Six ramps leave room for an attachment, but no legal arc or flat ports."""
    path, corner = _incline_corner(6)
    if at_start:
        path = tuple(reversed(path))
    with pytest.raises(RealiseError) as caught:
        _run_wall_corner(path)
    assert caught.value.cause == "corner"
    assert corner in caught.value.nodes


@pytest.mark.parametrize("at_start", [True, False])
def test_an_arc_can_turn_at_a_long_incline_without_an_attachment(at_start: bool) -> None:
    path, _ = _incline_corner(7)
    if at_start:
        path = tuple(reversed(path))
    realised = _run_wall_corner(path)
    assert realised.turns == (ARC,)
    assert realised.attachments == ()
    _judge(realised, only=BELT_CHECKS | {"geom.bounds"})


def test_a_ramp_reads_the_same_whichever_end_the_path_starts_from() -> None:
    """A ramp is three nodes and only two of them are places a belt really is.

    The kernel reports the midpoint via on the move's SOURCE level, so the two
    nodes that share a level are the source and the via and the level change is
    the other step.  Which step comes FIRST is only which way the path is read,
    and both read as the same ramp: the segmenter recognises it from the
    level-change step and the collinear flat step beside it, on either side.
    """
    grid = _lattice().grid_cm
    foot = (10, 13, GROUND_LEVEL)
    crest = (foot[0] + 2, foot[1], foot[2] + 1)
    forward = _join(
        (foot, (foot[0] + 1, foot[1], foot[2]), crest), _line(crest, (24, 13, crest[2]))
    )
    laid = _run(forward, _wall(foot, (1.0, 0.0, 0.0)), _wall((24, 13, crest[2]), (-1.0, 0.0, 0.0)))

    backward = _join(
        (crest, (crest[0] - 1, crest[1], foot[2]), foot), _line(foot, (2, 13, foot[2]))
    )
    other = _run(backward, _wall(crest, (-1.0, 0.0, 0.0)), _wall((2, 13, foot[2]), (1.0, 0.0, 0.0)))

    for realised, rise in ((laid, grid), (other, -grid)):
        assert realised.turns == ()
        assert len(realised.belts) == 1
        belt = realised.belts[0]
        climbs = [
            (a[0], b[0])
            for a, b in zip(belt.points, belt.points[1:], strict=False)
            if abs(b[0][2] - a[0][2]) > 1e-9
        ]
        assert len(climbs) == 1
        head, tail = climbs[0]
        assert tail[2] - head[2] == pytest.approx(rise)
        assert math.dist((head[0], head[1], 0.0), (tail[0], tail[1], 0.0)) == pytest.approx(
            2.0 * grid
        )


def test_a_path_that_stops_half_way_up_a_ramp_refuses_as_a_leg() -> None:
    """A level change over one grid step is half a ramp, and it says so.

    One of the two nodes is the midpoint, which stands half a level up where no
    belt end and no attachment may be.  Naming it "a move the lattice does not
    offer" sent a reader to the movement table; the move is the table's, the
    slicing is not.
    """
    foot = (10, 13, GROUND_LEVEL)
    orphan = ((foot[0] + 1, foot[1], foot[2] + 1), (foot[0] + 1, foot[1] + 1, foot[2] + 1))
    path = (foot, *orphan)
    with pytest.raises(RealiseError) as caught:
        _run(path, _wall(foot, (1.0, 0.0, 0.0)), _wall(orphan[-1], (0.0, -1.0, 0.0)))
    assert caught.value.cause == "leg"
    assert "half a ramp" in caught.value.detail
    assert foot in caught.value.nodes


def test_a_router_path_over_a_wall_of_machines_is_laid_as_belts() -> None:
    """The shape the kernel really returns, realised -- not a path written here.

    A solid line of Constructors across the designer leaves a belt no way round,
    so with the lift family left out of the movement table ``route_net`` climbs
    over the wall with the 2:1 ramps that are left, and comes back with every via
    in place.  What this pins is that the realiser reads that path -- the shape
    the kernel really returns, not one written here -- and that the ramps become
    inclines inside the game's own limit.
    """
    limit = _registry().limits.belt_max_incline_deg
    assert limit is not None
    machines, occupancy = _wall_of_constructors()
    column = 16
    start = (column, _lattice().open_lines.start, GROUND_LEVEL)
    goal = (column, _lattice().open_lines.stop - 1, GROUND_LEVEL)
    routed = route_net(
        occupancy,
        starts=(start,),
        goals=(goal,),
        pressure=0.0,
        budget=WorkBudget(left=4_000_000),
        deadline=None,
        transitions=_transitions(lifts=False),
    )
    assert routed.path is not None
    ramps = [
        (a, b)
        for a, b in zip(routed.path, routed.path[1:], strict=False)
        if a[2] != b[2] and (a[0], a[1]) != (b[0], b[1])
    ]
    assert ramps, "the wall has to be climbed, so the path must ramp"
    # Every one of them arrives with its via in front of it, which is the shape
    # the movement table's ``via`` flag puts on the SOURCE level.
    for index, (here, there) in enumerate(zip(routed.path, routed.path[1:], strict=False)):
        if (here, there) not in ramps:
            continue
        before = routed.path[index - 1]
        assert index and (
            here[0] - before[0],
            here[1] - before[1],
            here[2] - before[2],
        ) == (there[0] - here[0], there[1] - here[1], 0)

    # No step of it is unreadable.  This path also turns straight off a ramp,
    # which is a corner with no room and a concern of its own, so the claim is
    # about the SEGMENTER: a real router path is never refused as a leg.
    try:
        realised = _run(routed.path, _wall(start, (0.0, 1.0, 0.0)), _wall(goal, (0.0, -1.0, 0.0)))
    except RealiseError as refused:
        assert refused.cause == "corner", refused.detail
        return
    assert realised.lifts == ()
    for belt in realised.belts:
        for a, b in zip(belt.points, belt.points[1:], strict=False):
            run = math.dist((a[0][0], a[0][1], 0.0), (b[0][0], b[0][1], 0.0))
            assert math.degrees(math.atan2(abs(b[0][2] - a[0][2]), run)) <= limit
    _judge(realised, *machines, only=BELT_CHECKS)


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


def test_descending_lift_entry_and_turned_exit_follow_native_outward_normals() -> None:
    start, entry, exit_, finish = (8, 8, 8), (14, 8, 8), (14, 8, 4), (14, 14, 4)
    path = _join(_line(start, entry), (entry, exit_), _line(exit_, finish))
    laid = _run(path, _wall(start, (1.0, 0.0, 0.0)), _wall(finish, (0.0, -1.0, 0.0)))
    lift = laid.lifts[0]
    geometry = lift_geometry(_registry(), LIFT)
    _, input_normal = lift.bottom_end(geometry)
    _, output_normal = lift.top_end(geometry)
    assert input_normal == pytest.approx((-1.0, 0.0, 0.0))
    assert output_normal == pytest.approx((0.0, 1.0, 0.0))
    _judge(laid, only=BELT_CHECKS | LIFT_CHECKS | {"belt.capsule"})


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
    lines = _lattice().object_lines
    end = (node[0] + step[0] * want, node[1] + step[1] * want)
    return end[0] in lines and end[1] in lines


def _random_path(rng: random.Random) -> tuple[tuple[Node, ...], Vector, Vector]:
    """A path of the shape the router returns: long straights, 2:1 climbs, lifts.

    Legs are at least seven nodes so that a turn at each end has the room the
    attachment costs, and every climb and every lift is followed by seven more,
    so that this is a test about the belts rather than about the corners. Turns
    stay inside object_lines. The path never ends on a lift, because a lift at
    the designer wall is a terminal with no belt to reach it and rule 4's business
    rather than R-M3-4's.
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
