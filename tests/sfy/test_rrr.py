"""Every net, negotiated across rip-up rounds.

The claims here are about the LOOP: that two nets that want the same floor end
up at different levels, that a net another one walled in gets its floor back in
a later round, that a corner the realiser refuses is charged and avoided, that a
tree with a tap stands a splitter and carries the sum above it, and that a
refusal never leaves half a net in the placement.

Nothing here counts expanded cells -- the router is a geometric interval router
-- and nothing writes a distance down: the grid step, the machines' boxes, the
turn radius and the belt tiers are read out of ``registry.json`` and out of the
spec.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Sequence
from fractions import Fraction
from functools import cache
from itertools import count

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import GridNet, belt_class_for, terminal_for
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node, Occupancy, occupancy_for
from flab2bp.sfy.layout.manifold import SPLITTER_CLASS
from flab2bp.sfy.layout.model import MachineObj, Pose
from flab2bp.sfy.layout.realise import Terminal
from flab2bp.sfy.layout.router import BudgetCause, RouteFailureKind
from flab2bp.sfy.layout.rrr import RRR_MAX, RoutingOutcome, route_all
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.spec import BeltTier

CONSTRUCTOR = "Build_ConstructorMk1_C"
SMELTER = "Build_SmelterMk1_C"
LIFT = "Build_ConveyorLiftMk1_C"
ROD = "Recipe_IronRod_C"
INGOT = "Recipe_IngotIron_C"
ITEM = "iron-ingot"

#: A machine stands on the slab, whose top is one hologram grid step up.
SLAB_TOP_CM = 100.0

#: A pair of tiers a hair apart, so that a trunk carrying two sinks' worth needs
#: the faster one and each branch below the tap does not.  The rates are the
#: test's, the way a spec's are: what is asserted is that the tier follows the
#: rate, never that a belt is called Mk2.
SLOW = BeltTier(item_id="conveyor-belt-mk1", items_per_second=Fraction(1, 4))
FAST = BeltTier(item_id="conveyor-belt-mk2", items_per_second=Fraction(1, 2))


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


def _machine(oid: int, class_name: str, x: float, y: float, yaw: float, recipe: str) -> MachineObj:
    return MachineObj(oid, class_name, Pose(x, y, SLAB_TOP_CM, yaw), recipe)


def _port(class_name: str, name: str) -> Port:
    return next(p for p in _registry().buildables[class_name].ports if p.name == name)


def _terminal(machine: MachineObj, name: str) -> Terminal:
    return terminal_for(machine, _port(machine.class_name, name), _lattice(), _registry())


def _occupancy(*machines: MachineObj) -> Occupancy:
    return occupancy_for(_lattice(), machines, (), (), (), _registry())


def _net(
    net_id: int,
    sources: Sequence[Terminal],
    sinks: Sequence[Terminal],
    per_sink: Sequence[Fraction],
) -> GridNet:
    per_source = tuple(sum(per_sink, Fraction(0)) / len(sources) for _ in range(len(sources)))
    return GridNet(
        id=net_id,
        item_id=ITEM,
        sources=tuple(sources),
        sinks=tuple(sinks),
        rate=sum(per_sink, Fraction(0)),
        per_sink=tuple(per_sink),
        per_source=per_source,
    )


def _spec(*, slow: BeltTier = SLOW, fast: BeltTier | None = FAST) -> SfyBuildSpec:
    """A one-group fragment that funds two belt tiers, which is all the loop reads."""
    group = SfyMachineGroup(
        recipe_id="ingot-iron",
        recipe_class=INGOT,
        machine_item_id="machine",
        machine_class=SMELTER,
        count=1,
        clock=Fraction(1),
        last_clock=Fraction(1),
        max_clock=Fraction(1),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine={"iron-ore": Fraction(1)},
        outputs_per_machine={ITEM: Fraction(1, 4)},
        power_mw_per_machine=0.0,
        last_power_mw=0.0,
    )
    return SfyBuildSpec(
        groups=(group,),
        belt_item_id=slow.item_id,
        belt_items_per_second=slow.items_per_second,
        belt_upgrades=() if fast is None else (fast,),
        label="test",
    )


def _ids() -> Iterator[int]:
    return count(1)


def _route(
    nets: Sequence[GridNet],
    occupancy: Occupancy,
    *,
    deadline: float | None = None,
    spec: SfyBuildSpec | None = None,
    left: int = 4_000_000,
) -> RoutingOutcome:
    return route_all(
        nets,
        occupancy,
        measures=_measures(),
        registry=_registry(),
        budget=WorkBudget(deadline=deadline, left=left),
        deadline=time.monotonic() + 60.0 if deadline is None else deadline,
        ids=_ids(),
        belt_class_for=belt_class_for(spec if spec is not None else _spec(), _lab_map()),
        lift_class=LIFT,
    )


def _levels(path: Sequence[Node]) -> set[int]:
    return {node[2] for node in path}


# --- two nets that want the same floor -------------------------------------


def _crossing() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...]]:
    """An X: one net south to north, one west to east, crossing in the middle."""
    south = _machine(101, CONSTRUCTOR, 0.0, -1100.0, 0.0, ROD)
    north = _machine(102, CONSTRUCTOR, 0.0, 1100.0, 0.0, ROD)
    west = _machine(103, CONSTRUCTOR, -1100.0, 0.0, -90.0, ROD)
    east = _machine(104, CONSTRUCTOR, 1100.0, 0.0, -90.0, ROD)
    up = _net(1, (_terminal(south, "Output0"),), (_terminal(north, "Input0"),), (Fraction(1, 4),))
    across = _net(2, (_terminal(west, "Output0"),), (_terminal(east, "Input0"),), (Fraction(1, 8),))
    return ((south, north, west, east), (up, across))


def test_two_crossing_nets_settle_with_one_over_the_other() -> None:
    """Both routed, and the one that got the floor second climbed over the first.

    Level 2 is the port level and the only level a belt can meet a port on
    (R-M3-3), so two belts that have to cross can only do it by one of them
    climbing -- and the cheaper net gets the floor because the loop offers it
    first.
    """
    machines, nets = _crossing()
    outcome = _route(nets, _occupancy(*machines))

    assert outcome.stranded == ()
    assert len(outcome.trees) == 2
    assert outcome.rounds <= 3
    by_id = {tree.net.id: tree for tree in outcome.trees}
    first = by_id[1].paths[0]
    second = by_id[2].paths[0]
    assert _levels(first) == {GROUND_LEVEL}
    assert max(_levels(second)) > GROUND_LEVEL
    assert not set(first) & set(second)


# --- one net standing in another's only doorway ----------------------------


def _doorway() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...], Node]:
    """A port with one node of floor in front of it, and a net that wants that node.

    Nothing is flanked by hand: a machine's own hard box already denies the nodes
    beside its port's reach, so the only node an output can step onto is the one
    straight out in front, past the box's own edge.  The other net's cheapest
    path lies straight across that node, and it is the lighter of the two, so it
    is offered the floor second.
    """
    maker = _machine(101, SMELTER, 0.0, -600.0, 0.0, INGOT)
    eater = _machine(102, CONSTRUCTOR, 0.0, 1100.0, 0.0, ROD)
    crosser = _machine(103, CONSTRUCTOR, -1100.0, 0.0, -90.0, ROD)
    catcher = _machine(104, CONSTRUCTOR, 1100.0, 0.0, -90.0, ROD)
    source = _terminal(maker, "Output2")
    penned = _net(1, (source,), (_terminal(eater, "Input0"),), (Fraction(1, 4),))
    across = _net(
        2,
        (_terminal(crosser, "Output0"),),
        (_terminal(catcher, "Input0"),),
        (Fraction(1, 8),),
    )
    edge = source.reach[-1]
    doorway = (
        edge[0] + round(source.facing[0]),
        edge[1] + round(source.facing[1]),
        edge[2],
    )
    return ((maker, eater, crosser, catcher), (penned, across), doorway)


def test_a_net_blocked_by_another_rips_it_up_and_both_route() -> None:
    """The net that cannot do without a node gets it, and the other goes over.

    Every net is ripped up at the top of every round and offered the floor again
    in order of what it carries, so the belt with one node of floor in front of
    its port is the one that gets it; the net whose straight line lay across it
    is refused that node and comes back over the top, which is the only other
    way past on a lattice whose ports all stand at one level (R-M3-3).
    """
    machines, nets, doorway = _doorway()
    occupancy = _occupancy(*machines)
    outcome = _route(nets, occupancy)

    assert outcome.stranded == ()
    assert len(outcome.trees) == 2
    by_id = {tree.net.id: tree for tree in outcome.trees}
    penned, across = by_id[1].paths[0], by_id[2].paths[0]
    assert doorway in set(penned), "the port's one node of floor went to the net that needs it"
    assert _levels(penned) == {GROUND_LEVEL}
    assert doorway not in set(across)
    assert max(_levels(across)) > GROUND_LEVEL
    assert not set(penned) & set(across)


# --- a corner the realiser will not build ----------------------------------


def _pinch() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...], Node]:
    """A source and a sink at right angles, close enough that the corner pinches.

    The sink's own hard box denies every node beside its port's reach, so a belt
    reaches that port along one line and one line only, and the corner onto it
    has three grid steps of run out of it.  Task 6 measured what a corner costs
    against the shipped registry -- the cheaper of an arc and an attachment turn
    spends 301 cm of the straight either side -- so 300 cm has room for neither.

    The third value is the node the two legs meet on, which is what the refusal
    has to name.
    """
    maker = _machine(101, SMELTER, 0.0, -800.0, 0.0, INGOT)
    eater = _machine(102, CONSTRUCTOR, 600.0, 200.0, -90.0, ROD)
    source, sink = _terminal(maker, "Output2"), _terminal(eater, "Input0")
    net = _net(1, (source,), (sink,), (Fraction(1, 8),))
    return ((maker, eater), (net,), (source.node[0], sink.node[1], GROUND_LEVEL))


def test_a_realise_failure_is_charged_and_the_next_round_avoids_the_corner() -> None:
    """A corner the realiser refuses is blamed by NODE, and no round lays it.

    The blame is deliberately local -- the corner and its two neighbours along
    the path, which is what :class:`~flab2bp.sfy.layout.realise.RealiseError`
    names -- because the loop prices congestion per node and a wide blame moves
    belts that were never in the way.  Every charge reaches BOTH the congestion
    history the kernel prices and the blame the packer reads.

    This geometry has no legal corner at all: the sink's port is reachable along
    one line, so every path onto it turns where this one does, and the honest
    end of the negotiation is a stranded net rather than a bent belt.  What is
    asserted is that the corner was named and priced and that nothing was built
    standing on it.
    """
    machines, nets, corner = _pinch()
    occupancy = _occupancy(*machines)
    outcome = _route(nets, occupancy)

    charged = {node for node, weight in outcome.blame.items() if weight > 0.0}
    assert corner in charged, "the refusal names the node the two legs meet on"
    for node in charged:
        assert occupancy.history[_lattice().index(node)] > 0.0
    for tree in outcome.trees:
        for path in tree.paths:
            assert not (set(path) & charged), "no round lays a belt on a charged node"


# --- a tree with a tap -----------------------------------------------------


def _tapped() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...]]:
    """One smelter feeding two constructors: the second one has to tap the first belt."""
    maker = _machine(101, SMELTER, 0.0, -1100.0, 0.0, INGOT)
    near = _machine(102, CONSTRUCTOR, 0.0, 1100.0, 0.0, ROD)
    far = _machine(103, CONSTRUCTOR, 1000.0, 1100.0, 0.0, ROD)
    net = _net(
        1,
        (_terminal(maker, "Output2"),),
        (_terminal(near, "Input0"), _terminal(far, "Input0")),
        (Fraction(1, 4), Fraction(1, 4)),
    )
    return ((maker, near, far), (net,))


def test_a_tapped_tree_stands_a_splitter_on_the_tap_and_carries_the_sum_upstream() -> None:
    """R-M3-7: the second sink taps the first belt, and the trunk is tiered for both.

    The tiers are the spec's: the trunk carries both sinks' rates and the pieces
    below the splitter carry one each, so the trunk is on the faster belt and the
    branches are not -- which is asserted against
    :func:`~flab2bp.sfy.layout.grid_nets.tier_for`'s own answer rather than
    against a class name written here.
    """
    machines, nets = _tapped()
    outcome = _route(nets, _occupancy(*machines))

    assert outcome.stranded == ()
    tree = outcome.trees[0]
    assert len(tree.paths) == 2
    assert len(tree.taps) == 1
    tap, tapped = tree.taps[0]
    assert tap in set(tree.paths[tapped])

    attachments = [obj for laid in outcome.realised for obj in laid.attachments]
    stood = [obj for obj in attachments if _lattice().node(obj.pose.location) == tap]
    assert len(stood) == 1, "one attachment stands on the tap, whatever the turns stand"
    assert stood[0].class_name == SPLITTER_CLASS

    belts = [belt for laid in outcome.realised for belt in laid.belts]
    trunk = {belt.class_name for belt in belts if belt.items_per_second == Fraction(1, 2)}
    branch = {belt.class_name for belt in belts if belt.items_per_second == Fraction(1, 4)}
    assert len(trunk) == 1
    assert len(branch) == 1
    assert trunk != branch


# --- the bounds ------------------------------------------------------------


def test_the_loop_stops_at_the_deadline_and_reports_untouched_nets_as_budget() -> None:
    """A clock that has already run out routes nothing and says the clock did it."""
    machines, nets = _crossing()
    outcome = _route(nets, _occupancy(*machines), deadline=time.monotonic() - 1.0)

    assert outcome.trees == ()
    assert outcome.realised == ()
    assert len(outcome.stranded) == len(nets)
    for _stranded, routed in outcome.stranded:
        assert routed.kind is RouteFailureKind.BUDGET
        assert routed.cause is BudgetCause.DEADLINE
    assert outcome.rounds == 0


def test_the_loop_runs_no_more_rounds_than_it_says() -> None:
    machines, nets = _crossing()
    outcome = _route(nets, _occupancy(*machines))
    assert 1 <= outcome.rounds <= RRR_MAX


# --- a net that cannot be routed at all ------------------------------------


def _sealed() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...]]:
    """A maker whose one node of floor is taken by a machine, and a net that needs it.

    The blocker stands on the node the maker's output would step onto, so the
    port is walled into its own hard box: no belt leaves it at the port level,
    and every lift the movement table offers has its column inside that box.
    """
    maker = _machine(101, SMELTER, 0.0, -600.0, 0.0, INGOT)
    blocker = _machine(102, CONSTRUCTOR, 0.0, 500.0, 0.0, ROD)
    far = _machine(103, CONSTRUCTOR, 1200.0, -1000.0, 0.0, ROD)
    other = _machine(104, SMELTER, -1200.0, -1000.0, 0.0, INGOT)
    catcher = _machine(105, CONSTRUCTOR, -1200.0, 1000.0, 0.0, ROD)
    dead = _net(1, (_terminal(maker, "Output2"),), (_terminal(far, "Input0"),), (Fraction(1, 8),))
    live = _net(
        2, (_terminal(other, "Output2"),), (_terminal(catcher, "Input0"),), (Fraction(1, 4),)
    )
    return ((maker, blocker, far, other, catcher), (live, dead))


def test_stranded_nets_never_produce_a_partial_placement() -> None:
    """A net with no path leaves nothing behind, and the nets beside it still route."""
    machines, nets = _sealed()
    outcome = _route(nets, _occupancy(*machines))

    stranded = {net.id for net, _routed in outcome.stranded}
    assert stranded == {1}
    assert {tree.net.id for tree in outcome.trees} == {2}
    dead = next(net for net in nets if net.id == 1)
    ends = {dead.sources[0].world, dead.sinks[0].world}
    for laid in outcome.realised:
        for belt in laid.belts:
            assert belt.start not in ends
            assert belt.end not in ends
    # The evidence beside a stranded net is the LAST search's, and a net the
    # BUILDER refused kept a path: "a way was found and it could not be built"
    # is different evidence from "there is no way", and the packer reads them
    # differently.  What the contract promises is that nothing was built from it.
    assert all(tree.net.id != 1 for tree in outcome.trees)
    assert outcome.blame, "a stranded net names what stood in its way"


def test_a_routed_net_holds_its_own_nodes_in_the_occupancy_when_the_loop_returns() -> None:
    """The occupancy the caller kept is the BEST round's, not the last one tried.

    Task 9 stands poles on what is left over, so a loop that returned one round's
    paths and another round's occupancy would put a pole through a belt.
    """
    machines, nets = _crossing()
    occupancy = _occupancy(*machines)
    outcome = _route(nets, occupancy)

    for tree in outcome.trees:
        for path in tree.paths:
            for node in path:
                assert not occupancy.free(node), f"{node} is held by a committed belt"


def test_the_work_the_loop_reports_is_what_its_searches_charged() -> None:
    machines, nets = _crossing()
    outcome = _route(nets, _occupancy(*machines))
    assert outcome.work > 0


def test_a_lift_is_only_built_where_its_whole_column_is_free() -> None:
    """The kernel admits a lift on its two ends alone; the column is ours to hold.

    Every lift the loop builds is committed with the nodes between its ends, so
    nothing else may stand in the shaft.
    """
    machines, nets = _crossing()
    occupancy = _occupancy(*machines)
    outcome = _route(nets, occupancy)

    for laid in outcome.realised:
        for low, high in laid.lift_columns:
            for level in range(low[2], high[2] + 1):
                assert not occupancy.free((low[0], low[1], level))


def test_nothing_the_loop_returns_is_a_path_with_a_diagonal_step() -> None:
    """A belt on this lattice turns on a node, so every step is one axis at a time."""
    machines, nets = _tapped()
    outcome = _route(nets, _occupancy(*machines))
    for tree in outcome.trees:
        for path in tree.paths:
            for here, there in zip(path, path[1:], strict=False):
                moved = [axis for axis in range(3) if here[axis] != there[axis]]
                assert len(moved) <= 2
                if len(moved) == 2:
                    assert 2 in moved, f"{here} -> {there} moves in two ground axes"
                assert math.isclose(sum(1 for _ in moved), len(moved))
