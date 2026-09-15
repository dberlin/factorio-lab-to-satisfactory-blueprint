"""What a grid-routed build has to belt: nets, terminals, taps and tiers.

Nothing here writes a distance down.  The grid step, the machines' hard boxes,
the ports' offsets and the turn radius are read out of ``registry.json`` through
:class:`~flab2bp.sfy.layout.lattice.Lattice`,
:func:`~flab2bp.sfy.layout.manifold.hard_footprint_cm` and
:class:`~flab2bp.sfy.layout.corridors.Measures`, and every figure that appears
is asserted against the read value beside it rather than instead of it.

The rates are the spec's own exact ``Fraction``s: a net that moved a different
number of items from the one FactorioLab balanced would be a net that re-solved
the flow.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction
from functools import cache

import pytest

from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import (
    LATTICE_TOUCH_CM,
    TAP_CLEAR_NODES,
    GridNet,
    NetError,
    belt_class_for,
    nets_for,
    tap_nodes,
    terminal_for,
    tier_for,
)
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node, occupancy_for
from flab2bp.sfy.layout.manifold import hard_footprint_cm
from flab2bp.sfy.layout.model import MachineObj, Pose
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.spec import BeltTier

CONSTRUCTOR = "Build_ConstructorMk1_C"
SMELTER = "Build_SmelterMk1_C"
ASSEMBLER = "Build_AssemblerMk1_C"
INGOT = "Recipe_IngotIron_C"
ROD = "Recipe_IronRod_C"
PLATE = "Recipe_IronPlateReinforced_C"

#: A machine stands on the slab, whose top is one hologram grid step up.
SLAB_TOP_CM = 100.0

MK1 = BeltTier(item_id="conveyor-belt-mk1", items_per_second=Fraction(1))
MK2 = BeltTier(item_id="conveyor-belt-mk2", items_per_second=Fraction(2))


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _lab_map() -> LabMap:
    return load_lab_map()


@cache
def _lattice() -> Lattice:
    return Lattice.over(designer("mk1", _registry()), _registry())


@cache
def _measures() -> Measures:
    return _measure(_registry(), designer("mk1", _registry()))


def _group(
    machine_class_name: str,
    recipe_class: str,
    count: int,
    inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
    *,
    last_clock: Fraction = Fraction(1),
) -> SfyMachineGroup:
    """``count`` machines on one recipe, the last one at ``last_clock``."""
    return SfyMachineGroup(
        recipe_id=recipe_class.lower(),
        recipe_class=recipe_class,
        machine_item_id="machine",
        machine_class=machine_class_name,
        count=count,
        clock=Fraction(1),
        last_clock=last_clock,
        max_clock=Fraction(1),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine=inputs,
        outputs_per_machine=outputs,
        power_mw_per_machine=0.0,
        last_power_mw=0.0,
    )


def _spec(*groups: SfyMachineGroup, **fields: object) -> SfyBuildSpec:
    """A spec with no boundary of its own, so the dangling check leaves it alone."""
    base: dict[str, object] = {
        "groups": groups,
        "belt_item_id": MK1.item_id,
        "belt_items_per_second": MK1.items_per_second,
        "belt_upgrades": (MK2,),
        "label": "test",
    }
    base.update(fields)
    return SfyBuildSpec(**base)  # type: ignore[arg-type]


def _machine(oid: int, class_name: str, x: float, y: float, yaw: float, recipe: str) -> MachineObj:
    return MachineObj(oid, class_name, Pose(x, y, SLAB_TOP_CM, yaw), recipe)


def _row(
    first_id: int, class_name: str, recipe: str, count: int, y: float, yaw: float
) -> tuple[MachineObj, ...]:
    """``count`` machines in a line along ``X``, a hard box and a grid step apart."""
    grid = _lattice().grid_cm
    x0, _, x1, _ = hard_footprint_cm(_registry().buildables[class_name])
    pitch = math.ceil((x1 - x0 + grid) / grid) * grid
    left = -pitch * (count - 1) / 2.0
    return tuple(
        _machine(first_id + index, class_name, left + index * pitch, y, yaw, recipe)
        for index in range(count)
    )


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


def _net(nets: Sequence[GridNet], item_id: str) -> GridNet:
    matching = [net for net in nets if net.item_id == item_id]
    assert len(matching) == 1, [(n.item_id, len(n.sources), len(n.sinks)) for n in nets]
    return matching[0]


# --- one net per item, and one per pair where the spec pairs them -----------


def _ingots(count: int) -> SfyMachineGroup:
    return _group(SMELTER, INGOT, count, {"iron-ore": Fraction(1)}, {"iron-ingot": Fraction(1, 2)})


def _rods(count: int) -> SfyMachineGroup:
    return _group(
        CONSTRUCTOR, ROD, count, {"iron-ingot": Fraction(1, 2)}, {"iron-rod": Fraction(1)}
    )


def _paired() -> tuple[SfyBuildSpec, tuple[MachineObj, ...], tuple[MachineObj, ...]]:
    """Three smelters that exactly feed three constructors, and where they stand."""
    spec = _spec(_ingots(3), _rods(3))
    smelters = _row(101, SMELTER, INGOT, 3, -900.0, 0.0)
    constructors = _row(201, CONSTRUCTOR, ROD, 3, 900.0, 180.0)
    return (spec, smelters, constructors)


def test_a_direct_pair_is_one_net_per_machine_pair() -> None:
    """A pairing the SPEC states is N one-to-one nets, not one N-to-N net.

    ``direct_pairs`` is a property of the rates alone: where three smelters make
    exactly what three constructors eat and nothing else touches the item, the
    flow is three separate belts and this says so.
    """
    spec, smelters, constructors = _paired()
    nets = nets_for(spec, (*smelters, *constructors), _lattice(), _registry(), _lab_map())

    ingots = [net for net in nets if net.item_id == "iron-ingot"]
    assert len(ingots) == 3
    assert [len(net.sources) for net in ingots] == [1, 1, 1]
    assert [len(net.sinks) for net in ingots] == [1, 1, 1]
    assert {net.sources[0].port[0] for net in ingots if net.sources[0].port} == {
        machine.id for machine in smelters
    }
    assert {net.sinks[0].port[0] for net in ingots if net.sinks[0].port} == {
        machine.id for machine in constructors
    }
    for net in ingots:
        assert net.rate == Fraction(1, 2)
        assert net.per_source == (Fraction(1, 2),)
        assert net.per_sink == (Fraction(1, 2),)


def test_a_three_to_three_group_pair_is_one_net_with_three_sources_and_three_sinks() -> None:
    """A consumer with a second input is not a pair, so the item is one tree.

    The rates are the spec's: the net moves what the three makers make, and the
    three sinks draw what the three eaters eat.
    """
    spec = _spec(
        _ingots(3),
        _group(
            ASSEMBLER,
            PLATE,
            3,
            {"iron-ingot": Fraction(1, 2), "screw": Fraction(1, 5)},
            {"reinforced-iron-plate": Fraction(1, 10)},
        ),
    )
    smelters = _row(101, SMELTER, INGOT, 3, -700.0, 0.0)
    assemblers = _row(201, ASSEMBLER, PLATE, 3, 700.0, 180.0)

    nets = nets_for(spec, (*smelters, *assemblers), _lattice(), _registry(), _lab_map())
    ingot = _net(nets, "iron-ingot")

    assert len(ingot.sources) == 3
    assert len(ingot.sinks) == 3
    assert ingot.per_source == (Fraction(1, 2),) * 3
    assert ingot.per_sink == (Fraction(1, 2),) * 3
    assert ingot.rate == Fraction(3, 2)
    assert {terminal.port[0] for terminal in ingot.sources if terminal.port} == {
        machine.id for machine in smelters
    }


def test_the_odd_last_machines_share_is_the_rate_the_spec_gives_it() -> None:
    """FactorioLab's flow is authoritative: the underclocked machine moves less."""
    spec = _spec(
        _ingots(2),
        _group(
            ASSEMBLER,
            PLATE,
            2,
            {"iron-ingot": Fraction(1, 2), "screw": Fraction(1, 5)},
            {"reinforced-iron-plate": Fraction(1, 10)},
            last_clock=Fraction(1, 2),
        ),
    )
    smelters = _row(101, SMELTER, INGOT, 2, -700.0, 0.0)
    eaters = _row(201, ASSEMBLER, PLATE, 2, 700.0, 180.0)

    ingot = _net(
        nets_for(spec, (*smelters, *eaters), _lattice(), _registry(), _lab_map()), "iron-ingot"
    )
    assert ingot.per_sink == (Fraction(1, 2), Fraction(1, 4))
    assert ingot.rate == Fraction(3, 4)


def test_an_external_input_enters_through_the_minus_y_wall_and_an_output_leaves_by_plus_y() -> None:
    """R-M3-5, and the wall is a SET of terminals: any ``X`` the router likes."""
    lattice = _lattice()
    spec = _spec(
        _ingots(1),
        external_inputs={"iron-ore": Fraction(1)},
        outputs={"iron-ingot": Fraction(1, 2)},
    )
    smelters = _row(101, SMELTER, INGOT, 1, 0.0, 0.0)

    nets = nets_for(spec, smelters, lattice, _registry(), _lab_map())
    ore, ingot = _net(nets, "iron-ore"), _net(nets, "iron-ingot")

    assert len(ore.sources) == len(lattice.open_lines)
    assert {terminal.kind for terminal in ore.sources} == {"wall"}
    assert {terminal.node[1] for terminal in ore.sources} == {lattice.open_lines.start}
    assert {terminal.world[1] for terminal in ore.sources} == {-lattice.designer.half_cm}
    assert {terminal.facing for terminal in ore.sources} == {(0.0, 1.0, 0.0)}
    assert {terminal.node[2] for terminal in ore.sources} == {GROUND_LEVEL}

    assert {terminal.kind for terminal in ingot.sinks} == {"wall"}
    assert {terminal.node[1] for terminal in ingot.sinks} == {lattice.open_lines.stop - 1}
    assert {terminal.facing for terminal in ingot.sinks} == {(0.0, -1.0, 0.0)}


# --- what a terminal knows about its own port ------------------------------


def test_a_port_terminal_stands_on_the_first_node_out_along_its_own_normal() -> None:
    spec, smelters, _ = _paired()
    nets = nets_for(
        spec,
        (*smelters, *_row(201, CONSTRUCTOR, ROD, 3, 900.0, 180.0)),
        _lattice(),
        _registry(),
        _lab_map(),
    )
    source = next(net for net in nets if net.item_id == "iron-ingot").sources[0]

    machine = next(m for m in smelters if source.port and m.id == source.port[0])
    port = next(
        p
        for p in _registry().buildables[machine.class_name].ports
        if source.port and p.name == source.port[1]
    )
    transform = machine.pose.transform()
    authored = world_port(transform, port)
    # The port's own position, to within the cooked asset's float noise: this
    # Smelter's output is authored at x = 0 and arrives as -3.5e-05, and a
    # terminal that carried the noise would lay a belt leaving its port
    # sideways (see `grid_nets.snapped`).
    assert source.world == pytest.approx(authored, abs=LATTICE_TOUCH_CM)
    assert source.world == _lattice().world(source.node)
    forward = port_forward(transform, port)
    span = math.hypot(forward[0], forward[1])
    assert source.facing == pytest.approx((forward[0] / span, forward[1] / span, 0.0), abs=1e-3)
    assert max(abs(axis) for axis in source.facing) == 1.0, "a port faces along its own axis"


def test_a_ports_reach_is_the_nodes_inside_its_own_machines_hard_box() -> None:
    """R-M3-2 (d): the reach is exactly what this machine denies along the ray.

    Asserted against the occupancy rather than against a count: the reach exists
    to open what the flattening denied, so the node it stops at must be one the
    occupancy denies and the node past it one the occupancy allows.  A box whose
    edge does not land on a lattice line -- an Assembler turned a quarter turn
    has its 50 cm off -- denies one node further than it reaches, and a reach
    measured to the edge would leave that node walled in and in nobody's hands.
    """
    spec, smelters, constructors = _paired()
    lattice = _lattice()
    for machines, name in ((smelters, "Output2"), (constructors, "Input0")):
        occupancy = occupancy_for(lattice, machines, (), (), (), _registry())
        for machine in machines:
            port = next(
                p for p in _registry().buildables[machine.class_name].ports if p.name == name
            )
            terminal = terminal_for(machine, port, lattice, _registry())
            assert terminal.reach[0] == terminal.node
            for node in terminal.reach:
                assert not occupancy.free(node), f"{node} is inside the machine"
            axis = 0 if abs(terminal.facing[0]) >= abs(terminal.facing[1]) else 1
            step = 1 if terminal.facing[axis] > 0.0 else -1
            beyond = list(terminal.reach[-1])
            beyond[axis] += step
            assert occupancy.free((beyond[0], beyond[1], beyond[2])), (
                "the reach stops where the machine stops denying, and not before"
            )
    del spec


def test_a_machine_whose_port_is_off_the_lattice_refuses_rather_than_rounding() -> None:
    """R-M3-1: half a grid step sideways is a port no belt can leave on a line."""
    spec, smelters, constructors = _paired()
    nudged = (
        MachineObj(
            smelters[0].id,
            smelters[0].class_name,
            Pose(smelters[0].pose.x + 50.0, smelters[0].pose.y, smelters[0].pose.z, 0.0),
            smelters[0].recipe_class,
        ),
        *smelters[1:],
    )
    with pytest.raises(NetError) as raised:
        nets_for(spec, (*nudged, *constructors), _lattice(), _registry(), _lab_map())
    assert raised.value.cause == "lattice"


# --- taps ------------------------------------------------------------------


def test_tap_nodes_are_interior_straight_nodes_two_from_any_corner() -> None:
    """R-M3-7: a tap keeps straight nodes on either side of the splitter.

    Two is the rule's own number and :data:`TAP_CLEAR_NODES` is one more, for
    the reason it states: two nodes of run is one grid step, which is under the
    101 cm floor R-M3-4 holds every piece of belt to.  Both are asserted -- the
    rule's floor, and the constant's own -- rather than a number written here.
    """
    clear = TAP_CLEAR_NODES
    assert clear >= 2  # R-M3-7's own floor, which the constant may only raise
    corner = (5, 15, GROUND_LEVEL)
    path = _join(_line((5, 5, GROUND_LEVEL), corner), _line(corner, (15, 15, GROUND_LEVEL)))

    taps = set(tap_nodes((path,), _lattice()))

    assert (5, 5 + clear, GROUND_LEVEL) in taps
    assert (5, 15 - clear, GROUND_LEVEL) in taps
    assert (5 + clear, 15, GROUND_LEVEL) in taps
    assert corner not in taps
    for short in range(clear):
        assert (5, 5 + short, GROUND_LEVEL) not in taps
        assert (5, 15 - short, GROUND_LEVEL) not in taps
    for tap in taps:
        along = max(abs(tap[0] - corner[0]), abs(tap[1] - corner[1]))
        assert along >= clear


def test_a_tap_keeps_the_turn_radius_clear_when_the_measures_say_so() -> None:
    """The brief's simple reading of the corner reach: ``ceil(radius / grid)``.

    The radius is the registry's -- ``turn_radius_cm`` -- so the number of nodes
    is read rather than written, and the assertion is that the tap stands that
    many nodes clear rather than that the number is four.
    """
    measures = _measures()
    clear = math.ceil(measures.radius / measures.grid)
    corner = (5, 20, GROUND_LEVEL)
    path = _join(_line((5, 2, GROUND_LEVEL), corner), _line(corner, (25, 20, GROUND_LEVEL)))

    taps = tap_nodes((path,), _lattice(), clear=clear)

    assert taps
    for tap in taps:
        assert max(abs(tap[0] - corner[0]), abs(tap[1] - corner[1])) >= clear


def test_a_node_a_run_is_broken_at_is_no_longer_a_tap_or_beside_one() -> None:
    """An attachment already standing on a run ends it, both ways."""
    path = _line((5, 2, GROUND_LEVEL), (5, 30, GROUND_LEVEL))
    stood = (5, 16, GROUND_LEVEL)

    taps = set(tap_nodes((path,), _lattice(), breaks=(stood,)))

    assert stood not in taps
    for short in range(TAP_CLEAR_NODES):
        assert (5, 15 - short, GROUND_LEVEL) not in taps
        assert (5, 17 + short, GROUND_LEVEL) not in taps
    assert (5, 15 - TAP_CLEAR_NODES, GROUND_LEVEL) in taps
    assert (5, 17 + TAP_CLEAR_NODES, GROUND_LEVEL) in taps


def test_a_climb_ends_a_run_so_no_tap_stands_on_a_ramp() -> None:
    """An incline's via is a node of the path and a splitter cannot sit on it."""
    flat = _line((5, 2, GROUND_LEVEL), (5, 12, GROUND_LEVEL))
    climbed = (*flat, (5, 13, GROUND_LEVEL), (5, 14, GROUND_LEVEL + 1))
    beyond = _line((5, 14, GROUND_LEVEL + 1), (5, 24, GROUND_LEVEL + 1))

    taps = set(tap_nodes((_join(climbed, beyond),), _lattice()))

    assert (5, 13, GROUND_LEVEL) not in taps
    assert (5, 14, GROUND_LEVEL + 1) not in taps
    assert (5, 5, GROUND_LEVEL) in taps
    assert (5, 20, GROUND_LEVEL + 1) in taps


# --- tiers -----------------------------------------------------------------


def test_the_tier_is_the_slowest_belt_the_spec_funds_that_carries_the_rate() -> None:
    tiers = (MK1, MK2)
    assert tier_for(Fraction(1, 2), tiers, "iron-ingot") is MK1
    assert tier_for(Fraction(1), tiers, "iron-ingot") is MK1
    assert tier_for(Fraction(3, 2), tiers, "iron-ingot") is MK2


def test_a_run_over_the_top_tier_refuses_with_the_belt_ceiling() -> None:
    with pytest.raises(NetError) as raised:
        tier_for(Fraction(5), (MK1, MK2), "iron-ingot")
    assert raised.value.cause == "ceiling"
    assert str(raised.value).startswith("run exceeds the belt ceiling")


def test_a_belt_class_is_the_game_class_the_lab_map_gives_the_tier() -> None:
    spec = _spec(_ingots(1))
    belt_class = belt_class_for(spec, _lab_map())
    assert belt_class(Fraction(1, 2), "iron-ingot") == "Build_ConveyorBeltMk1_C"
    assert belt_class(Fraction(3, 2), "iron-ingot") == "Build_ConveyorBeltMk2_C"
