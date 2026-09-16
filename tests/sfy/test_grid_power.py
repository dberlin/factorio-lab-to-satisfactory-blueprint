"""Poles on a grid-routed build's own lattice: where they may stand, and what they reach.

The manifold's power stage stands a pole LINE along a row; a grid-routed build
has no rows, so its poles stand on the free nodes the belt router left behind.
Every number is still the registry's -- the pole's connection and its
``max_connections``, the wire's ``wire_max_cm``, the soft clearance box whose
height says which levels a pole occupies -- and the judge is still the validator:
the last test here puts the poles and their wires in front of ``power.wires``
with the geometry checks running beside it.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from functools import cache

import pytest

from flab2bp.sfy.geometry import world_port
from flab2bp.sfy.layout import grid_power
from flab2bp.sfy.layout.lattice import (
    GROUND_LEVEL,
    Lattice,
    Node,
    Occupancy,
    belt_levels,
    occupancy_for,
)
from flab2bp.sfy.layout.model import MachineObj, Pose, SfyPlacement
from flab2bp.sfy.layout.power import POLE_CLASS, WIRE_CLASS, PowerError, box_bounds, power_port
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import designer

CONSTRUCTOR = "Build_ConstructorMk1_C"
ROD = "Recipe_IronRod_C"

#: A machine stands on the slab, whose top is one grid step up -- the same
#: reading :mod:`tests.sfy.test_lattice` states.
SLAB_TOP_CM = 100.0


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _lattice(mark: str) -> Lattice:
    return Lattice.over(designer(mark, _registry()), _registry())


def _machines(xs: tuple[float, ...], ys: tuple[float, ...]) -> tuple[MachineObj, ...]:
    """A block of Constructors on the slab, one per ``(x, y)``, centred on nodes.

    The pitches are the caller's and they are chosen so that no two hard boxes
    come within a grid step of each other, which is what ``geom.hard_clearance``
    and this project's own R7 ask of a packed build.
    """
    return tuple(
        MachineObj(index + 1, CONSTRUCTOR, Pose(x, y, SLAB_TOP_CM, 0.0), ROD)
        for index, (y, x) in enumerate((y, x) for y in ys for x in xs)
    )


def _six() -> tuple[MachineObj, ...]:
    return _machines((-1000.0, 0.0, 1000.0), (-600.0, 600.0))


def _nine() -> tuple[MachineObj, ...]:
    return _machines((-1000.0, 0.0, 1000.0), (-1200.0, 0.0, 1200.0))


def _fifteen() -> tuple[MachineObj, ...]:
    return _machines((-2000.0, -1000.0, 0.0, 1000.0, 2000.0), (-1200.0, 0.0, 1200.0))


def _occupancy(machines: tuple[MachineObj, ...], mark: str) -> Occupancy:
    return occupancy_for(_lattice(mark), machines, (), (), (), _registry())


def _plan(
    machines: tuple[MachineObj, ...],
    occupancy: Occupancy,
    mark: str,
    registry: Registry | None = None,
) -> grid_power.PowerPlan:
    return grid_power.place_on_free_nodes(
        machines,
        occupancy,
        _registry() if registry is None else registry,
        ids=itertools.count(10_000),
        designer=designer(mark, _registry()),
    )


def _pole_levels(mark: str) -> tuple[int, ...]:
    """The levels a pole standing on the slab occupies, from its own soft box."""
    lattice = _lattice(mark)
    box = _registry().buildables[POLE_CLASS].clearance[0]
    low, high = box_bounds(box, Pose(0.0, 0.0, SLAB_TOP_CM, 0.0))
    spanned = {GROUND_LEVEL, *belt_levels(low[2], high[2], lattice.grid_cm)}
    return tuple(sorted(spanned & set(lattice.open_levels)))


def _machine_wires(plan: grid_power.PowerPlan, machines: tuple[MachineObj, ...]) -> list[int]:
    """Which machine each wire that ends on one reaches."""
    ids = {machine.id for machine in machines}
    return [side[0] for wire in plan.wires for side in (wire.link.a, wire.link.b) if side[0] in ids]


def test_six_machines_get_one_pole_within_reach_and_six_wires() -> None:
    """A Mk2's connection takes seven wires, so one pole carries six machines and
    keeps nothing back: there is no second pole for it to chain to."""
    machines = _six()
    plan = _plan(machines, _occupancy(machines, "mk1"), "mk1")
    assert [pole.class_name for pole in plan.poles] == [POLE_CLASS]
    assert sorted(_machine_wires(plan, machines)) == [m.id for m in machines]
    assert len(plan.wires) == len(machines)
    assert all(wire.class_name == WIRE_CLASS for wire in plan.wires)

    limit = _registry().limits.wire_max_cm[WIRE_CLASS]
    pole = plan.poles[0]
    at = world_port(pole.pose.transform(), power_port(_registry(), POLE_CLASS))
    for machine in machines:
        port = world_port(machine.pose.transform(), power_port(_registry(), CONSTRUCTOR))
        assert math.dist(at, port) <= limit
    assert len(plan.lines) == len(plan.poles)


def test_a_pole_never_stands_inside_a_hard_box_or_on_a_belt_node() -> None:
    """The two things a pole may not do, and the refusal when it can do neither.

    Its own box is SOFT -- a belt may pass through it and it may share space with
    another soft box -- so what is tested is that it meets no HARD box, and that
    it stands on no node a committed belt holds at any level the pole occupies.
    """
    machines = _six()
    lattice = _lattice("mk1")
    occupancy = _occupancy(machines, "mk1")
    # The free aisle at y = 0 runs the width of the build, and its nodes are the
    # ones nearest the designer centre -- so a belt committed along it is in the
    # way of exactly the poles this placer would otherwise pick first.
    run: tuple[Node, ...] = tuple((i, 16, GROUND_LEVEL) for i in range(13, 20))
    assert all(occupancy.free(node) for node in run)
    occupancy.commit(1, run)

    plan = _plan(machines, occupancy, "mk1")
    assert plan.poles
    taken = {(node[0], node[1]) for node in run}
    hard = [
        box_bounds(box, machine.pose)
        for machine in machines
        for box in _registry().buildables[machine.class_name].clearance
        if not box.soft
    ]
    for pole in plan.poles:
        node = lattice.node((pole.pose.x, pole.pose.y, GROUND_LEVEL * lattice.grid_cm))
        assert node is not None
        assert (node[0], node[1]) not in taken
        for box in _registry().buildables[pole.class_name].clearance:
            low, high = box_bounds(box, pole.pose)
            for other_low, other_high in hard:
                apart = any(
                    high[axis] <= other_low[axis] + 1e-6 or low[axis] >= other_high[axis] - 1e-6
                    for axis in range(3)
                )
                assert apart, f"the pole's box laps {other_low}..{other_high}"

    # And every level the box spans is asked about, not just the one a belt runs
    # on: a node free at the port level but not above it holds no pole.
    walled = _occupancy(machines, "mk1")
    walled.block_box(
        (-lattice.designer.half_cm, -lattice.designer.half_cm, 0.0),
        (lattice.designer.half_cm, lattice.designer.half_cm, lattice.designer.height_cm),
    )
    with pytest.raises(PowerError) as caught:
        _plan(machines, walled, "mk1")
    assert caught.value.cause == "room"


def test_poles_beyond_the_connection_count_chain_through_a_second_pole() -> None:
    """Nine machines is past what one Mk2 carries, so a second pole stands and the
    two are wired to each other -- which is what makes the build one circuit."""
    machines = _nine()
    plan = _plan(machines, _occupancy(machines, "mk2"), "mk2")
    assert len(plan.poles) == 2
    assert sorted(_machine_wires(plan, machines)) == [m.id for m in machines]

    ids = {pole.id for pole in plan.poles}
    chain = [wire for wire in plan.wires if {wire.link.a[0], wire.link.b[0]} <= ids]
    assert len(chain) == 1
    limit = _registry().limits.wire_max_cm[WIRE_CLASS]
    where = {
        pole.id: world_port(pole.pose.transform(), power_port(_registry(), POLE_CLASS))
        for pole in plan.poles
    }
    assert math.dist(where[chain[0].link.a[0]], where[chain[0].link.b[0]]) <= limit

    counts: dict[tuple[int, str], int] = {}
    for wire in plan.wires:
        for side in (wire.link.a, wire.link.b):
            counts[side] = counts.get(side, 0) + 1
    assert max(counts[side] for side in counts if side[0] in ids) <= 7
    assert len(plan.lines) == 2


def test_a_machines_own_wires_are_counted_against_the_poles_link_budget() -> None:
    """Fifteen machines is three poles, and the middle one's chain has to find a
    neighbour with a link left.

    The first cut of this placer counted only the CHAIN wires against a pole's
    connection, so the third pole chained back into the first -- which was
    already carrying six machines and a chain wire -- and handed back a
    connection with eight wires on it.
    """
    machines = _fifteen()
    plan = _plan(machines, _occupancy(machines, "mk3"), "mk3")
    assert len(plan.poles) == 3
    assert sorted(_machine_wires(plan, machines)) == [m.id for m in machines]

    ids = {pole.id for pole in plan.poles}
    counts: dict[tuple[int, str], int] = {}
    for wire in plan.wires:
        for side in (wire.link.a, wire.link.b):
            counts[side] = counts.get(side, 0) + 1
    assert max(counts[side] for side in counts if side[0] in ids) == 7
    chain = [wire for wire in plan.wires if {wire.link.a[0], wire.link.b[0]} <= ids]
    assert len(chain) == len(plan.poles) - 1


def test_power_wires_validates_clean() -> None:
    """The judge, on the chained build: every wire short enough, every connection
    inside its link count, every machine on a wire that reaches a pole."""
    machines = _fifteen()
    mark = "mk3"
    plan = _plan(machines, _occupancy(machines, mark), mark)
    placement = SfyPlacement(
        designer=designer(mark, _registry()),
        machines=machines,
        poles=plan.poles,
        wires=plan.wires,
    )
    report = validate(
        placement,
        None,
        _registry(),
        only={"power.wires", "geom.bounds", "geom.hard_clearance"},
    )
    assert [finding.message for finding in report.errors] == []
    assert "power.wires" in report.checks_run
    assert "geom.bounds" in report.checks_run


def test_a_machine_no_free_node_reaches_refuses_for_want_of_room() -> None:
    """A wire that reaches 50 cm puts every free node out of reach of every
    machine, which is a pole that has nowhere to stand rather than a wire that is
    too long: the node the machine wants is inside the machine."""
    machines = _six()
    registry = _registry()
    short = replace(registry, limits=replace(registry.limits, wire_max_cm={WIRE_CLASS: 50.0}))
    with pytest.raises(PowerError) as caught:
        _plan(machines, _occupancy(machines, "mk1"), "mk1", short)
    assert caught.value.cause == "room"


def test_a_pole_occupies_every_level_its_soft_box_spans() -> None:
    """The levels are READ off the box: a Mk2 on the slab reaches from 180 to 920
    cm, which is levels 2 to 9 -- so a belt crossing an aisle five levels up is
    as much in a pole's way as one on the floor of it.
    """
    assert _pole_levels("mk1") == (2, 3, 4, 5, 6, 7, 8, 9)
    machines = _six()
    lattice = _lattice("mk1")
    occupancy = _occupancy(machines, "mk1")
    # The nodes this placer would take first, blocked at level 5 alone: nothing
    # is in the way of the pole's FOOT, only of its shaft.
    overhead: tuple[Node, ...] = tuple((i, 16, 5) for i in range(15, 18))
    assert all(occupancy.free(node) for node in overhead)
    assert all(occupancy.free((i, 16, GROUND_LEVEL)) for i, _, _ in overhead)
    occupancy.commit(2, overhead)

    plan = _plan(machines, occupancy, "mk1")
    assert plan.poles
    for pole in plan.poles:
        node = lattice.node((pole.pose.x, pole.pose.y, GROUND_LEVEL * lattice.grid_cm))
        assert node is not None
        assert (node[0], node[1]) not in {(i, j) for i, j, _ in overhead}
        assert all(occupancy.free((node[0], node[1], level)) for level in _pole_levels("mk1"))


def test_a_build_with_no_machines_in_it_gets_no_poles_and_no_complaint() -> None:
    plan = grid_power.place_on_free_nodes(
        (),
        _occupancy((), "mk1"),
        _registry(),
        ids=itertools.count(10_000),
        designer=designer("mk1", _registry()),
    )
    assert plan == grid_power.PowerPlan((), (), ())


def test_a_pole_stands_on_the_grid_its_own_hologram_snaps_to() -> None:
    grid = _registry().buildables[POLE_CLASS].grid_snap_cm
    assert grid == 50.0
    machines = _six()
    plan = _plan(machines, _occupancy(machines, "mk1"), "mk1")
    for pole in plan.poles:
        assert pole.pose.x % grid == pytest.approx(0.0, abs=1e-6)
        assert pole.pose.y % grid == pytest.approx(0.0, abs=1e-6)
        assert pole.pose.z == pytest.approx(SLAB_TOP_CM)
