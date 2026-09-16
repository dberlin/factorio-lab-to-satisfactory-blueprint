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
from dataclasses import replace
from fractions import Fraction
from functools import cache

import pytest

from flab2bp.sfy.geometry import box_bounds
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import net_plan, nets_for, terminal_for
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, occupancy_for
from flab2bp.sfy.layout.manifold import (
    hard_footprint_cm,
    machine_pitch_cm,
    shortest_belt_cm,
    slab_top_cm,
)
from flab2bp.sfy.layout.model import MachineObj
from flab2bp.sfy.layout.packer import (
    HOT_NODES,
    Feedback,
    Pack,
    PackError,
    pack,
    port_apron_nodes,
)
from flab2bp.sfy.layout.refusals import GAME_DATA, REFUSALS
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM, TOUCH_CM
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec, SfyMachineGroup, designer, direct_pairs
from flab2bp.spec import BeltTier
from tests.sfy.conftest import flow_spec

CONSTRUCTOR = "Build_ConstructorMk1_C"
SMELTER = "Build_SmelterMk1_C"
ASSEMBLER = "Build_AssemblerMk1_C"
INGOT = "Recipe_IngotIron_C"
ROD = "Recipe_IronRod_C"
PLATE = "Recipe_IronPlateReinforced_C"

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


def _row(count: int = 5) -> SfyBuildSpec:
    """``count`` Smelters, every one of them fed from the designer wall.

    One net, ``count`` sinks and no machine source, so every Smelter wants the
    smallest ``Y`` it can have and they all compete for the bottom row.  What
    they fit into there is the registry's own arithmetic, asserted where it is
    relied on.
    """
    smelters = _group(SMELTER, INGOT, count, {ORE: Fraction(1)}, {IRON_INGOT: Fraction(1)})
    return _spec(smelters, external_inputs={ORE: Fraction(count)})


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


# --- the apron uses the grid realiser's minimum turn ------------------------


def test_concrete_ports_leave_their_own_machine_into_free_floor() -> None:
    """The required approach must extend beyond the port's opened own-body reach."""
    lattice, registry = _lattice(), _registry()
    packed = _packed(flow_spec("concrete-60"))
    occupancy = occupancy_for(lattice, packed.machines, (), (), (), registry)
    for machine in packed.machines:
        for port in registry.buildables[machine.class_name].ports:
            if port.kind != "belt":
                continue
            terminal = terminal_for(machine, port, lattice, registry)
            step = (round(terminal.facing[0]), round(terminal.facing[1]))
            node = terminal.node
            while node in terminal.reach:
                node = (node[0] + step[0], node[1] + step[1], node[2])
            assert occupancy.free(node), (
                f"{machine.id}'s {port.name} first approach {node} is blocked by another machine"
            )


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
            assert _gap_cm(a, b) >= _lattice().grid_cm - TOUCH_CM


def test_every_machine_stands_on_a_node_with_a_grid_step_between_hard_boxes() -> None:
    """R-M3-1 for a machine, and R7 for two of them: ONE grid step, not two.

    ``(x, y)`` is a node pair -- the machine's hologram-snapped centre -- ``z``
    is the slab top and the yaw is one of the build gun's four.  Between two
    machines R7 keeps one grid step, and the packed row shows it both ways: no
    pair is closer than a step, and the closest pair is a step apart EXACTLY,
    so a model that reserved two would fail here.

    The row is what forces it, and the forcing is read rather than arranged:
    five Smelters, each a sink of the one net the ``-Y`` wall feeds, so every
    one of them wants the smallest ``Y`` it can have; and five Smelter
    footprints with one grid step between them come to exactly the open floor's
    width, so the only way they all stand in the bottom row is shoulder to
    shoulder.
    """
    lattice, registry = _lattice(), _registry()
    grid = lattice.grid_cm
    packed = _packed(_row())
    assert len(packed.machines) == 5

    for machine in packed.machines:
        assert lattice.node((machine.pose.x, machine.pose.y, 0.0)) is not None
        assert machine.pose.z == pytest.approx(slab_top_cm(registry))
        assert machine.pose.yaw_deg in {0.0, 90.0, 180.0, -90.0}

    # Four pitches exactly span the centre nodes the open floor leaves for a
    # Smelter, which is why the row is forced -- read, not assumed, and the
    # pitch is the manifold's own: a footprint plus ONE grid step.
    pitch = machine_pitch_cm(registry.buildables[SMELTER], registry.limits)
    x0, _, x1, _ = hard_footprint_cm(registry.buildables[SMELTER])
    lines = lattice.open_lines
    low = lattice.world((lines.start, lines.start, 0))
    high = lattice.world((lines.stop - 1, lines.stop - 1, 0))
    reach = (x1 - x0) / 2.0 + grid / 2.0
    stands = [
        node
        for node in lines
        if low[0] - TOUCH_CM <= lattice.world((node, 0, 0))[0] - reach
        and lattice.world((node, 0, 0))[0] + reach <= high[0] + TOUCH_CM
    ]
    assert (len(packed.machines) - 1) * pitch == pytest.approx(grid * (stands[-1] - stands[0]))

    boxes = [_world_box(machine) for machine in packed.machines]
    gaps = [_gap_cm(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1 :]]
    assert min(gaps) == pytest.approx(grid, abs=TOUCH_CM)


def test_port_aprons_are_free_of_other_machines() -> None:
    """Every belt port's straight run is somewhere a belt centreline may stand.

    Read back through the router's own two statements rather than through a
    distance written here: :func:`~flab2bp.sfy.layout.grid_nets.terminal_for`
    says where a belt leaves a port, and
    :meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` -- the predicate the
    kernel searches -- says whether a node is passable at all with every packed
    machine standing.  A reserved run the occupancy denies would be a reservation
    of nothing, which is what an apron held off the wrong rectangle buys.

    The exception is the terminal's own ``reach`` (R-M3-2 (d)): the nodes between
    a port and its own machine's box edge lie INSIDE that box, so the occupancy
    denies them to everyone and they travel with the terminal to be opened for
    that one net.  Those are the only denied nodes an apron may contain.
    """
    lattice, registry = _lattice(), _registry()
    apron = port_apron_nodes(_measures())
    packed = _packed(_chain())
    occupancy = occupancy_for(lattice, packed.machines, (), (), (), registry)

    for machine in packed.machines:
        buildable = registry.buildables[machine.class_name]
        for port in buildable.ports:
            if port.kind != "belt":
                continue
            terminal = terminal_for(machine, port, lattice, registry)
            step = (round(terminal.facing[0]), round(terminal.facing[1]))
            for index in range(apron + 1):
                node = (
                    terminal.node[0] + step[0] * index,
                    terminal.node[1] + step[1] * index,
                    terminal.node[2],
                )
                assert lattice.holds(node)
                assert occupancy.free(node) or node in terminal.reach, (
                    f"{machine.class_name} {machine.id}'s {port.name} apron node {node} "
                    "is denied by the occupancy the router will search"
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
    apron wants only the grid realiser's minimum straight run out of that face.  So "when room
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

    The same real flow packed twice, once with nothing learned and once with one
    net's weight raised, on a spec whose nets really do compete for the same
    floor: ``iron-plate-60`` belts ore in at the ``-Y`` wall, pairs each smelter
    to a constructor, and sends plates out at the ``+Y`` wall, so the build can
    sit against one wall or the other but not both, and every metre net 5 gains
    net 4 loses.

    So the assertion is two-sided and strict -- the weighted net gets STRICTLY
    shorter and the net it competes with STRICTLY longer -- which is what a
    weight buys and what an unweighted pack cannot produce by chance.  The
    control is ``failed_nets={}``, the same call with the same feedback object
    and nothing in it, so what is being measured is the WEIGHT rather than the
    presence of feedback.
    """
    lattice, registry = _lattice(), _registry()
    spec = flow_spec("iron-plate-60")
    plans = net_plan(spec, registry)
    ore = next(plan for plan in plans if not plan.sources)
    plate = next(plan for plan in plans if not plan.sinks)

    control = _packed(spec, feedback=Feedback(failed_nets={}, hot_nodes={}))
    weighted = _packed(spec, feedback=Feedback(failed_nets={plate.id: 8.0}, hot_nodes={}))

    def out_to_wall(packed: Pack) -> int:
        """How far the plate net's sources stand from the wall they drain to."""
        ys = [_port_node(packed, port)[1] for port in plate.sources]
        return (lattice.open_lines.stop - 1) - sum(ys) // len(ys)

    def in_from_wall(packed: Pack) -> int:
        """How far the ore net's sinks stand from the wall that feeds them."""
        return sum(_port_node(packed, port)[1] - lattice.open_lines.start for port in ore.sinks)

    assert out_to_wall(weighted) < out_to_wall(control)
    assert in_from_wall(weighted) > in_from_wall(control)


def _port_node(packed: Pack, port: tuple[int, str]) -> tuple[int, int, int]:
    """Where one :func:`net_plan` port stands, once its machine has been placed."""
    machine = packed.machines[port[0]]
    buildable = _registry().buildables[machine.class_name]
    named = next(one for one in buildable.ports if one.name == port[1])
    return terminal_for(machine, named, _lattice(), _registry()).node


def test_feedback_decays_by_the_dsp_factor() -> None:
    """Evidence fades between arrangements, weights and hot nodes alike."""
    faded = Feedback(failed_nets={3: 1.0}, hot_nodes={(4, 5, 2): 2.0}).decayed()
    assert faded.failed_nets == {3: pytest.approx(0.85)}
    assert faded.hot_nodes == {(4, 5, 2): pytest.approx(1.7)}


def test_hot_nodes_push_a_machine_off_the_floor_the_router_blamed() -> None:
    """The worst of the blamed floor comes back as floor a belt can use.

    Read through the router's own predicate rather than through a distance: the
    occupancy built from the packed machines must leave every one of the
    :data:`~flab2bp.sfy.layout.packer.HOT_NODES` heaviest blamed nodes
    ``free()``.  The rest of the blame is not honoured, and the test says so --
    that bound is what makes a blame set of any size affordable, and a packer
    that honoured all of it would be the one that never returns an arrangement.
    """
    lattice, registry = _lattice(), _registry()
    lines = lattice.open_lines
    spec = _spec(_group(SMELTER, INGOT, 1, {ORE: Fraction(1)}, {IRON_INGOT: Fraction(1)}))

    # The whole floor blamed, and the heaviest charges in one corner.
    worst = tuple(
        (lines.start + index % 4, lines.start + index // 4, GROUND_LEVEL)
        for index in range(HOT_NODES)
    )
    hot = {(i, j, GROUND_LEVEL): 1.0 for i in lines for j in lines}
    hot.update(dict.fromkeys(worst, 40.0))

    packed = _packed(spec, feedback=Feedback(failed_nets={}, hot_nodes=hot))
    occupancy = occupancy_for(lattice, packed.machines, (), (), (), registry)
    assert all(occupancy.free(node) for node in worst)
    assert any(not occupancy.free(node) for node in hot)


def test_blame_no_arrangement_can_honour_does_not_refuse_a_build_that_fits() -> None:
    """Evidence may not refuse a build that fits.

    The blamed nodes are held out of the machines as constraints, which can be
    unsatisfiable even when the build fits.  The blame is laid where no arrangement
    can honour it, and the arithmetic that makes that true is read rather than
    guessed: a quarter turn makes an Assembler as narrow as its narrowest side
    on either axis, which is the least its belt keepout can reach and the widest
    its centre can wander; one blamed node every keepout's width across that
    wander puts a blamed node inside the machine wherever it stands.

    The machine still stands, and one of the blamed nodes is still covered --
    the pack went ahead without evidence it could not honour rather than
    refusing a build whose one machine plainly fits.
    """
    lattice, registry = _lattice(), _registry()
    grid, lines = lattice.grid_cm, lattice.open_lines
    spec = _spec(_group(ASSEMBLER, PLATE, 1, {ORE: Fraction(1)}, {IRON_INGOT: Fraction(1)}))

    x0, y0, x1, y1 = hard_footprint_cm(registry.buildables[ASSEMBLER])
    narrow = min(x1 - x0, y1 - y0)
    reach = math.floor((narrow / 2.0 + BELT_CLEARANCE_HALF_WIDTH_CM) / grid)
    grown = narrow / 2.0 + grid / 2.0
    low = lattice.world((lines.start, lines.start, 0))
    high = lattice.world((lines.stop - 1, lines.stop - 1, 0))
    stands = [
        node
        for node in lines
        if low[0] - TOUCH_CM <= lattice.world((node, node, 0))[0] - grown
        and lattice.world((node, node, 0))[0] + grown <= high[0] + TOUCH_CM
    ]
    blocking = tuple(
        (i, j, GROUND_LEVEL)
        for i in range(stands[0] + reach, stands[-1] + reach + 1, 2 * reach + 1)
        for j in range(stands[0] + reach, stands[-1] + reach + 1, 2 * reach + 1)
    )
    assert blocking and len(blocking) <= HOT_NODES, (
        "the blame has to fit in what the packer honours"
    )
    assert all(node[0] in lines and node[1] in lines for node in blocking)

    packed = _packed(
        spec, feedback=Feedback(failed_nets={}, hot_nodes=dict.fromkeys(blocking, 1.0))
    )
    assert len(packed.machines) == 1
    occupancy = occupancy_for(lattice, packed.machines, (), (), (), registry)
    assert any(not occupancy.free(node) for node in blocking)


@pytest.mark.parametrize("seed", (2, 3))
def test_later_arrangements_with_blame_still_pack_the_real_flow(seed: int) -> None:
    """Feedback must not exhaust the deterministic cap before finding a pack.

    The wall-time comparison belongs in task8-measure-feedback.py, not a
    machine-load-sensitive assertion in the permanent suite.  This regression
    exercises both later seeds with the normal deterministic allowance.
    """
    registry, lattice = _registry(), _lattice()
    spec = flow_spec("iron-plate-60")
    plans = net_plan(spec, registry)
    lines = lattice.open_lines
    hot = {
        (lines.start + index % 4, lines.start + index // 4, GROUND_LEVEL): 1.0
        for index in range(HOT_NODES)
    }
    packed = pack(
        spec,
        _designer(),
        registry,
        lattice,
        feedback=Feedback(
            failed_nets=dict.fromkeys((plan.id for plan in plans), 1.0), hot_nodes=hot
        ),
        deadline=time.monotonic() + 60.0,
        workers=WORKERS,
        seed=seed,
        measures=_measures(),
    )
    occupancy = occupancy_for(lattice, packed.machines, (), (), (), registry)
    assert all(occupancy.free(node) for node in hot)
    assert len(packed.machines) == spec.machine_count


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


def test_an_oversized_rotating_build_refuses_geometry_not_the_search_budget() -> None:
    """Variable rectangle widths must not hide an orientation-independent obstruction."""
    registry = _registry()
    target = designer("mk3", registry)
    lattice = Lattice.over(target, registry)
    with pytest.raises(PackError) as refused:
        pack(
            flow_spec("rotor-10"),
            target,
            registry,
            lattice,
            feedback=None,
            deadline=time.monotonic() + 1.0,
            workers=WORKERS,
            seed=1,
            measures=_measure(registry, target),
        )
    assert refused.value.cause == "the packer found no arrangement"


def test_incompatible_side_lengths_refuse_even_when_total_area_fits() -> None:
    """A packing obstruction can survive the ordinary total-area relaxation."""
    registry = _registry()
    target = designer("mk3", registry)
    lattice = Lattice.over(target, registry)
    with pytest.raises(PackError) as refused:
        pack(
            flow_spec("modular-frame-5"),
            target,
            registry,
            lattice,
            feedback=None,
            deadline=time.monotonic() + 1.0,
            workers=WORKERS,
            seed=1,
            measures=_measure(registry, target),
        )
    assert refused.value.cause == "the packer found no arrangement"


def test_game_data_the_packer_cannot_lay_a_box_on_refuses_with_the_game_data_cause() -> None:
    """A hole in the game data leaves by the refusal table, not by a bare error.

    Two of them, and both are the same answer to the caller -- this build cannot
    be authored from the game data we have -- so both carry
    :data:`~flab2bp.sfy.layout.refusals.GAME_DATA`: a hologram grid that is not a
    whole number of centimetres, which is a grid this packer cannot state a box
    on; and a belt port that does not stand at the port level with its machine on
    the slab, which R-M3-3 says it must.
    """
    registry = _registry()
    spec = _chain(1)

    # A grid that divides the designer evenly and is still not whole centimetres.
    fractional = Lattice(_designer(), 6.25)
    with pytest.raises(PackError) as refused:
        pack(
            spec,
            _designer(),
            registry,
            fractional,
            feedback=None,
            deadline=None,
            workers=WORKERS,
            seed=SEED,
        )
    assert refused.value.cause == GAME_DATA
    assert refused.value.cause in REFUSALS

    # The same Smelter with its output port a grid step higher than the game
    # puts it, which lands it above the level a grid-snapped machine's belts
    # leave on.
    smelter = registry.buildables[SMELTER]
    lifted = tuple(
        replace(port, translation=(port.translation[0], port.translation[1], 300.0))
        if port.name == "Output2"
        else port
        for port in smelter.ports
    )
    doctored = replace(
        registry,
        buildables={**registry.buildables, SMELTER: replace(smelter, ports=lifted)},
    )
    with pytest.raises(PackError) as lifted_port:
        pack(
            spec,
            _designer(),
            doctored,
            _lattice(),
            feedback=None,
            deadline=None,
            workers=WORKERS,
            seed=SEED,
        )
    assert lifted_port.value.cause == GAME_DATA
