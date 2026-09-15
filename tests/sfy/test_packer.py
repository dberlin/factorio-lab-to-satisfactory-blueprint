"""Where a grid-routed build stands its machines: the CP-SAT packer.

Nothing here writes a distance down.  The grid step, the hard footprints, the
port offsets, the shortest legal belt and the port apron are read out of
``registry.json`` through :class:`~flab2bp.sfy.layout.lattice.Lattice`,
:func:`~flab2bp.sfy.layout.manifold.hard_footprint_cm`,
:func:`~flab2bp.sfy.layout.manifold.shortest_belt_cm` and
:func:`~flab2bp.sfy.layout.packer.port_apron_nodes`, and every figure that
appears is asserted against the read value beside it rather than instead of it.

The boxes a test measures are built by :func:`flab2bp.sfy.geometry.box_bounds`
off each placed machine's own pose, which is the path the validator takes -- not
the packer's own rotated rectangles, so a packer that turned a box the wrong way
would be caught here rather than agreed with.
"""

from __future__ import annotations

import math
import time
from fractions import Fraction
from functools import cache

import pytest

from flab2bp.sfy.geometry import box_bounds
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.corridors import Measures, attachment_turn
from flab2bp.sfy.layout.grid_nets import net_plan, nets_for, terminal_for
from flab2bp.sfy.layout.lattice import Lattice
from flab2bp.sfy.layout.manifold import shortest_belt_cm, slab_top_cm
from flab2bp.sfy.layout.model import MachineObj
from flab2bp.sfy.layout.packer import Feedback, Pack, PackError, pack, port_apron_nodes
from flab2bp.sfy.layout.refusals import REFUSALS
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.validate import TOUCH_CM
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec, SfyMachineGroup, designer, direct_pairs
from flab2bp.spec import BeltTier
from tests.sfy.conftest import flow_spec

CONSTRUCTOR = "Build_ConstructorMk1_C"
SMELTER = "Build_SmelterMk1_C"
INGOT = "Recipe_IngotIron_C"
ROD = "Recipe_IronRod_C"

ORE = "ore"
IRON_INGOT = "ingot"
ROD_ITEM = "rod"

MK1 = BeltTier(item_id="conveyor-belt-mk1", items_per_second=Fraction(1))

#: The seed and the worker count every test packs with, so that two calls that
#: should agree really do: ortools reads ``num_search_workers == 0`` as all
#: cores, and more than one worker makes the answer a race.
SEED = 20260915
WORKERS = 1


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _designer() -> Designer:
    return designer("mk1", _registry())


@cache
def _lattice() -> Lattice:
    return Lattice.over(_designer(), _registry())


@cache
def _measures() -> Measures:
    return _measure(_registry(), _designer())


def _group(
    machine_class_name: str,
    recipe_class: str,
    count: int,
    inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
) -> SfyMachineGroup:
    """``count`` machines on one recipe, every one of them at the same clock."""
    return SfyMachineGroup(
        recipe_id=recipe_class.lower(),
        recipe_class=recipe_class,
        machine_item_id="machine",
        machine_class=machine_class_name,
        count=count,
        clock=Fraction(1),
        last_clock=Fraction(1),
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
        "label": "test",
    }
    base.update(fields)
    return SfyBuildSpec(**base)  # type: ignore[arg-type]


def _chain(count: int = 3) -> SfyBuildSpec:
    """``count`` smelters feeding ``count`` constructors, machine for machine.

    The rates are equal per machine and the ingot goes nowhere else, which is
    what makes :func:`~flab2bp.sfy.spec.direct_pairs` call the two groups a
    direct pair -- asserted in the test that reads the pair back, so a spec that
    stopped being one would not quietly become a different test.
    """
    smelters = _group(SMELTER, INGOT, count, {ORE: Fraction(1)}, {IRON_INGOT: Fraction(1)})
    constructors = _group(
        CONSTRUCTOR, ROD, count, {IRON_INGOT: Fraction(1)}, {ROD_ITEM: Fraction(2)}
    )
    return _spec(smelters, constructors)


def _packed(spec: SfyBuildSpec, *, feedback: Feedback | None = None) -> Pack:
    """One pack with the limits HANDED DOWN, the way a strategy calls it.

    The tests that call :func:`~flab2bp.sfy.layout.packer.pack` directly leave
    ``measures`` off instead, so both readings -- a strategy's one
    :class:`~flab2bp.sfy.layout.corridors.Measures` passed along, and a caller
    with no run around it -- are exercised.
    """
    return pack(
        spec,
        _designer(),
        _registry(),
        _lattice(),
        feedback=feedback,
        deadline=time.monotonic() + 60.0,
        workers=WORKERS,
        seed=SEED,
        measures=_measures(),
    )


def _world_box(machine: MachineObj) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)``: what ``machine``'s HARD clearance covers on the ground.

    Built from the placed pose through :func:`flab2bp.sfy.geometry.box_bounds`,
    which is the validator's own reader, rather than from anything the packer
    computed.
    """
    buildable = _registry().buildables[machine.class_name]
    transform = machine.pose.transform()
    spans = [box_bounds(box, transform) for box in buildable.clearance if not box.soft]
    assert spans, f"{machine.class_name} has no hard clearance box"
    return (
        min(low[0] for low, _ in spans),
        min(low[1] for low, _ in spans),
        max(high[0] for _, high in spans),
        max(high[1] for _, high in spans),
    )


def _gap_cm(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """How far apart two ground boxes are: the widest separation on either axis.

    Negative where they lap.  Two boxes are apart when they are apart on ONE
    axis, so the widest of the two is the gap between them.
    """
    return max(
        max(a[0] - b[2], b[0] - a[2]),
        max(a[1] - b[3], b[1] - a[3]),
    )


# --- the apron is derived, never written -----------------------------------


def test_the_port_apron_is_an_attachment_turn_of_straight_run() -> None:
    """R-M3-1 and Task 7: every approach to a port is horizontal.

    A lift can never land on a port node -- its column would run through the
    machine -- so the last corner before a port is an attachment turn, and what
    that turn costs beside the box is ``box + lead_in``.  The apron is that
    length in whole nodes, and it is READ rather than written.
    """
    measures = _measures()
    assert port_apron_nodes(measures) == math.ceil(attachment_turn(measures).cost / measures.grid)


# --- the 3 + 3 chain --------------------------------------------------------


def test_three_smelters_and_three_constructors_pack_inside_mk1_with_no_overlap() -> None:
    """The whole chain stands, inside the walls, with nothing lapping anything."""
    spec = _chain()
    packed = _packed(spec)

    assert packed.status in {"OPTIMAL", "FEASIBLE"}
    assert len(packed.machines) == spec.machine_count
    assert [machine.class_name for machine in packed.machines] == [SMELTER] * 3 + [CONSTRUCTOR] * 3

    lattice = _lattice()
    lines = lattice.open_lines
    low = lattice.world((lines.start, lines.start, 0))
    high = lattice.world((lines.stop - 1, lines.stop - 1, 0))
    for machine in packed.machines:
        x0, y0, x1, y1 = _world_box(machine)
        assert low[0] - TOUCH_CM <= x0 and x1 <= high[0] + TOUCH_CM
        assert low[1] - TOUCH_CM <= y0 and y1 <= high[1] + TOUCH_CM

    boxes = [_world_box(machine) for machine in packed.machines]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert _gap_cm(a, b) > TOUCH_CM


def test_every_machine_stands_on_a_node_with_a_grid_step_between_hard_boxes() -> None:
    """R-M3-1 for a machine, and R7 for two of them.

    ``(x, y)`` is a node pair -- the machine's hologram-snapped centre -- ``z``
    is the slab top, the yaw is one of the build gun's four, and no two hard
    boxes come closer than the one grid step R7 keeps between them.
    """
    lattice, registry = _lattice(), _registry()
    packed = _packed(_chain())

    for machine in packed.machines:
        assert lattice.node((machine.pose.x, machine.pose.y, 0.0)) is not None
        assert machine.pose.z == pytest.approx(slab_top_cm(registry))
        assert machine.pose.yaw_deg in {0.0, 90.0, 180.0, -90.0}

    boxes = [_world_box(machine) for machine in packed.machines]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert _gap_cm(a, b) >= lattice.grid_cm - TOUCH_CM


def test_port_aprons_are_free_of_other_machines() -> None:
    """Every belt port keeps its own straight run clear of every other machine.

    The apron is read back through
    :func:`~flab2bp.sfy.layout.grid_nets.terminal_for`, which is the router's own
    statement of where a belt leaves a port, so a packer that reserved a
    different run from the one the router uses would fail here.
    """
    lattice, registry = _lattice(), _registry()
    grid = lattice.grid_cm
    apron = port_apron_nodes(_measures())
    packed = _packed(_chain())
    boxes = {machine.id: _world_box(machine) for machine in packed.machines}

    for machine in packed.machines:
        buildable = registry.buildables[machine.class_name]
        for port in buildable.ports:
            if port.kind != "belt":
                continue
            terminal = terminal_for(machine, port, lattice, registry)
            step = (
                round(terminal.facing[0]),
                round(terminal.facing[1]),
            )
            for index in range(apron):
                node = (
                    terminal.node[0] + step[0] * index,
                    terminal.node[1] + step[1] * index,
                    terminal.node[2],
                )
                assert lattice.holds(node)
                here = lattice.world(node)
                for other in packed.machines:
                    if other.id == machine.id:
                        continue  # a port sits inside its own machine's box
                    x0, y0, x1, y1 = boxes[other.id]
                    inside = (
                        x0 - grid + TOUCH_CM < here[0] < x1 + grid - TOUCH_CM
                        and y0 - grid + TOUCH_CM < here[1] < y1 + grid - TOUCH_CM
                    )
                    assert not inside, (
                        f"{machine.class_name} {machine.id}'s {port.name} apron node {node} "
                        f"stands in {other.class_name} {other.id}'s inflated box"
                    )


def test_the_packer_reads_its_nets_from_the_plan_the_router_numbers() -> None:
    """The packer places the machines a :func:`net_plan` port INDEX means.

    Net ids have one source, and it is not this module: the packer and
    :func:`~flab2bp.sfy.layout.grid_nets.nets_for` both read
    :func:`~flab2bp.sfy.layout.grid_nets.net_plan`, so ``GridNet.id`` is
    ``NetPlan.id`` by construction.  What the packer still owes the plan is the
    machine ORDER -- a plan names a port as ``(machine index, port name)`` into
    the flat machine list, and a packer that emitted its machines in any other
    order would weight the wrong ports and hand the router the wrong poses.

    So this reads a real FactorioLab flow, packs it, and asserts that every
    plan port resolved through :attr:`Pack.machines` is the terminal
    ``nets_for`` puts on that net: ore from the ``-Y`` wall into three smelters,
    three paired ingot nets, plates from three constructors out through the
    ``+Y`` wall -- five nets, and the wall terminals after the machine ones.
    """
    lattice, registry = _lattice(), _registry()
    spec = flow_spec("iron-plate-60")
    planned = net_plan(spec, registry)
    assert [plan.id for plan in planned] == [1, 2, 3, 4, 5]

    packed = _packed(spec)
    assert len(packed.machines) == spec.machine_count
    routed = nets_for(spec, packed.machines, lattice, registry, load_lab_map())

    assert [net.id for net in routed] == [plan.id for plan in planned]
    for net, plan in zip(routed, planned, strict=True):
        for terminals, ports in ((net.sources, plan.sources), (net.sinks, plan.sinks)):
            assert [terminal.port for terminal in terminals[: len(ports)]] == [
                (packed.machines[index].id, name) for index, name in ports
            ]
            # Anything past the plan's machine ports is the designer wall.
            assert all(terminal.kind == "wall" for terminal in terminals[len(ports) :])


def test_direct_pairs_face_each_other_a_shortest_belt_apart_when_room_allows() -> None:
    """A direct pair stands as close as the geometry lets it, never closer.

    The term rewards a port-to-port span of one shortest legal belt, which for a
    Smelter feeding a Constructor is not reachable: both machines are 1000 cm
    deep, R7 keeps a grid step between their boxes, and the consumer's input
    apron wants four nodes of straight run out of that face.  So "when room
    allows" is READ rather than written -- the same pair is packed alone in the
    designer, with the whole floor to itself and nothing to get in its way, and
    that span is what room allows.  Every pair of the crowded 3 + 3 build
    reaches it too, which is what the term buys.

    "Facing each other" is the same sentence the router uses: the belt LEAVES
    the producer along ``+facing`` and ARRIVES at the consumer along
    ``-facing``, so neither port points away from the other.
    """
    lattice, registry = _lattice(), _registry()
    shortest = math.ceil(shortest_belt_cm(registry.limits) / lattice.grid_cm)
    assert len(direct_pairs(_chain(1))) == 1, "the chain is meant to be one direct pair"

    alone = _span(_packed(_chain(1)), 0, 1)
    assert alone >= shortest

    spec = _chain(3)
    assert len(direct_pairs(spec)) == 1
    packed = _packed(spec)
    for index in range(3):
        assert _span(packed, index, 3) == alone
        maker, eater = packed.machines[index], packed.machines[index + 3]
        out = terminal_for(maker, _belt_port(maker, "output"), lattice, registry)
        into = terminal_for(eater, _belt_port(eater, "input"), lattice, registry)
        towards = (into.node[0] - out.node[0], into.node[1] - out.node[1])
        assert out.facing[0] * towards[0] + out.facing[1] * towards[1] >= 0.0
        assert into.facing[0] * towards[0] + into.facing[1] * towards[1] <= 0.0


def _belt_port(machine: MachineObj, direction: str) -> Port:
    buildable = _registry().buildables[machine.class_name]
    ports = sorted(
        (port for port in buildable.ports if port.kind == "belt" and port.direction == direction),
        key=lambda port: port.name,
    )
    assert ports, f"{machine.class_name} has no belt {direction} port"
    return ports[0]


def _span(packed: Pack, index: int, count: int) -> int:
    """How many nodes apart one direct pair's two ports stand.

    The chain packs the producers first and the consumers after, which is the
    order :func:`~flab2bp.sfy.layout.grid_nets.nets_for` pairs them in, so
    machine ``index`` feeds machine ``index + count``.
    """
    lattice, registry = _lattice(), _registry()
    maker, eater = packed.machines[index], packed.machines[index + count]
    out = terminal_for(maker, _belt_port(maker, "output"), lattice, registry)
    into = terminal_for(eater, _belt_port(eater, "input"), lattice, registry)
    return abs(out.node[0] - into.node[0]) + abs(out.node[1] - into.node[1])


# --- feedback ---------------------------------------------------------------


def test_a_failed_net_weight_pulls_its_terminals_closer() -> None:
    """A net the router could not lay is worth more than the ones it did.

    The same spec, packed twice: once with nothing learned and once with one
    net's weight raised.  That net's own terminals do not end up further apart
    than they were, which is the whole of what the weight buys -- it cannot
    promise closer, because the boxes may already be as close as they go.
    """
    spec = _chain()
    before = _packed(spec)
    after = _packed(spec, feedback=Feedback(failed_nets={1: 8.0}, hot_nodes={}))
    assert _span(after, 0, 3) <= _span(before, 0, 3)


def test_feedback_decays_by_the_dsp_factor() -> None:
    """Evidence fades between arrangements, weights and hot nodes alike."""
    faded = Feedback(failed_nets={3: 1.0}, hot_nodes={(4, 5, 2): 2.0}).decayed()
    assert faded.failed_nets == {3: pytest.approx(0.85)}
    assert faded.hot_nodes == {(4, 5, 2): pytest.approx(1.7)}


def test_hot_nodes_push_a_machine_off_the_floor_the_router_blamed() -> None:
    """A node the router blamed costs whatever machine stands on it.

    One smelter, and a blame history laid over the half of the designer it would
    otherwise be free to stand in: the machine ends up clear of every blamed
    node, which is what the summed-area term is for.
    """
    lattice = _lattice()
    spec = _spec(_group(SMELTER, INGOT, 1, {ORE: Fraction(1)}, {IRON_INGOT: Fraction(1)}))
    lines = lattice.open_lines
    hot = {
        (i, j, 2): 40.0 for i in lines for j in range(lines.start, lines.start + (len(lines) // 2))
    }
    packed = _packed(spec, feedback=Feedback(failed_nets={}, hot_nodes=hot))

    machine = packed.machines[0]
    x0, y0, x1, y1 = _world_box(machine)
    for i, j, _ in hot:
        here = lattice.world((i, j, 0))
        assert not (x0 < here[0] < x1 and y0 < here[1] < y1)


# --- refusals ---------------------------------------------------------------


def test_the_solver_stops_at_the_deadline_with_the_best_feasible_or_refuses() -> None:
    """The wall is honoured: an incumbent comes back, and nothing does not."""
    spec = _chain()
    with pytest.raises(PackError) as spent:
        pack(
            spec,
            _designer(),
            _registry(),
            _lattice(),
            feedback=None,
            deadline=time.monotonic() - 1.0,
            workers=WORKERS,
            seed=SEED,
        )
    assert spent.value.cause == "packing exceeded the budget"
    assert spent.value.cause in REFUSALS

    packed = pack(
        spec,
        _designer(),
        _registry(),
        _lattice(),
        feedback=None,
        deadline=time.monotonic() + 30.0,
        workers=WORKERS,
        seed=SEED,
    )
    assert packed.status in {"OPTIMAL", "FEASIBLE"}
    assert len(packed.machines) == spec.machine_count


def test_a_spec_that_cannot_fit_refuses_with_the_packer_cause() -> None:
    """More machines than the designer has floor is a refusal, not a bad pack."""
    spec = _spec(_group(SMELTER, INGOT, 60, {ORE: Fraction(1)}, {IRON_INGOT: Fraction(1)}))
    with pytest.raises(PackError) as refused:
        _packed(spec)
    assert refused.value.cause == "the packer found no arrangement"
    assert refused.value.cause in REFUSALS
