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
from flab2bp.sfy.labmap import LabMap, load_lab_map, machine_class
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import (
    TAP_CLEAR_NODES,
    GridNet,
    belt_class_for,
    terminal_for,
    tier_for,
)
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node, Occupancy, occupancy_for
from flab2bp.sfy.layout.manifold import SPLITTER_CLASS
from flab2bp.sfy.layout.model import BeltRun, MachineObj, Pose, SfyPlacement
from flab2bp.sfy.layout.realise import Terminal
from flab2bp.sfy.layout.router import BudgetCause, RouteFailureKind
from flab2bp.sfy.layout.rrr import (
    RRR_MAX,
    RoutingOutcome,
    _Branch,
    _flows,
    _in_the_doorway,
    _lift_heights,
    _Run,
    route_all,
)
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.transitions import incline_run_nodes, sfy_transitions
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.spec import BeltTier
from tests.sfy.test_realise import BELT_CHECKS, LIFT_CHECKS

#: The one clearance check the validator will actually judge.  Belt-to-belt
#: clearance is NOT among them: ``buildable.clearance`` is a ``partial`` rule, so
#: ``geom.hard_clearance`` declines to judge and reports itself skipped rather
#: than pass an opinion it does not have (global constraint 2).  R-M3-2's own
#: rule -- two belts a grid step apart lap by 58 cm -- is therefore asserted on
#: the lattice instead, by :func:`_no_foreign_lap`.
CLEARANCE_CHECKS = frozenset({"geom.bounds"})

CONSTRUCTOR = "Build_ConstructorMk1_C"
ASSEMBLER = "Build_AssemblerMk1_C"
SMELTER = "Build_SmelterMk1_C"
LIFT = "Build_ConveyorLiftMk1_C"
BELT = "Build_ConveyorBeltMk1_C"
ROD = "Recipe_IronRod_C"
INGOT = "Recipe_IngotIron_C"
PLATE = "Recipe_IronPlateReinforced_C"
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
    measures: Measures | None = None,
) -> RoutingOutcome:
    return route_all(
        nets,
        occupancy,
        measures=_measures() if measures is None else measures,
        registry=_registry(),
        budget=WorkBudget(deadline=deadline, left=left),
        deadline=time.monotonic() + 60.0 if deadline is None else deadline,
        ids=_ids(),
        belt_class_for=belt_class_for(spec if spec is not None else _spec(), _lab_map()),
        lift_class=LIFT,
    )


def _line(a: Node, b: Node) -> tuple[Node, ...]:
    """The nodes from ``a`` to ``b`` along the one axis they differ on."""
    axis = next(index for index in range(3) if a[index] != b[index])
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


def _levels(path: Sequence[Node]) -> set[int]:
    return {node[2] for node in path}


def _judge(outcome: RoutingOutcome, *machines: MachineObj, mark: str = "mk1") -> None:
    """Everything the loop built, assembled and put to the validator.

    Global constraint 2: every placement fragment a stage returns has to
    validate clean once it is assembled.  The checks are Task 6's own
    (:data:`BELT_CHECKS`, :data:`LIFT_CHECKS`) plus what the validator will
    judge about where a belt may stand, and they are applied to the WHOLE
    outcome at once, so that two nets' belts are judged together and a splitter
    the loop stood is judged against the belts wired into it.

    Shaped like ``test_realise._judge`` and holding the same guard -- no errors,
    and no check in ``only`` quietly skipped -- but building its own placement,
    because which designer a build stands in is a spec input here and that one
    judges one mk1 path.
    """
    laid = outcome.realised
    placement = SfyPlacement(
        designer=designer(mark, _registry()),
        machines=machines,
        attachments=tuple(obj for one in laid for obj in one.attachments),
        belts=tuple(belt for one in laid for belt in one.belts),
        lifts=tuple(lift for one in laid for lift in one.lifts),
        links=tuple(link for one in laid for link in one.links),
    )
    only = BELT_CHECKS | LIFT_CHECKS | CLEARANCE_CHECKS
    report = validate(placement, None, _registry(), only=only)
    assert report.errors == (), [finding.message for finding in report.errors]
    assert set(report.skipped).isdisjoint(only), report.skipped
    _no_foreign_lap(outcome)


def _no_foreign_lap(outcome: RoutingOutcome) -> None:
    """No net ever stands in another net's SHADOW -- R-M3-2.

    The rule the validator cannot state for us: ``buildable.clearance`` is
    ``partial``, so ``geom.hard_clearance`` declines to judge belt against belt
    and reports itself skipped.  So the lattice's own reading is asserted here,
    node for node, the way
    :meth:`~flab2bp.sfy.layout.lattice.Occupancy._shadow` computes it -- a run's
    own nodes and the two across it at each end of every segment, and NOT the
    node beyond its last, which stays free for a belt crossing.

    Between two branches of ONE net a lap is legal and expected: a tap branch
    starts on the node beside its own trunk, with the splitter standing between
    the two.  Between two nets there is nothing standing between them.
    """
    for tree in outcome.trees:
        mine = {node for path in tree.paths for node in path}
        for other in outcome.trees:
            if other.net.id == tree.net.id:
                continue
            for path in other.paths:
                clash = mine & _shadow_of(path)
                assert not clash, f"net {other.net.id} shadows net {tree.net.id} at {clash}"


def _shadow_of(path: Sequence[Node]) -> set[Node]:
    """Every node a committed run holds or shadows -- ``Occupancy._shadow``'s rule."""
    out = set(path)
    for head, tail in zip(path, path[1:], strict=False):
        dx, dy = tail[0] - head[0], tail[1] - head[1]
        if not dx and not dy:
            continue
        across = (0, 1) if dx else (1, 0)
        for node in (head, tail):
            for sign in (1, -1):
                out.add((node[0] + sign * across[0], node[1] + sign * across[1], node[2]))
    return out


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
    _judge(outcome, *machines)


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
    _judge(outcome, *machines)


# --- a corner the realiser will not build ----------------------------------


def _wall_line(lattice: Lattice) -> tuple[Terminal, ...]:
    """The whole ``+Y`` wall as one stream's worth of terminals -- R-M3-5.

    The same set :func:`~flab2bp.sfy.layout.grid_nets.nets_for` builds for an
    output: a node on the innermost line a belt may stand on, with the belt's
    end out ON the wall, and a facing that points into the designer.
    """
    lines = lattice.open_lines
    row = lines.stop - 1
    half = lattice.designer.half_cm
    out: list[Terminal] = []
    for i in lines:
        node = (i, row, GROUND_LEVEL)
        here = lattice.world(node)
        out.append(
            Terminal(
                node=node,
                world=(here[0], half, here[2]),
                facing=(0.0, -1.0, 0.0),
                port=None,
                kind="wall",
            )
        )
    return tuple(out)


def _crossing_belt(oid: int, a: Node, b: Node) -> BeltRun:
    """One straight belt already in the placement, from node ``a`` to node ``b``."""
    lattice = _lattice()
    head, tail = lattice.world(a), lattice.world(b)
    along = (tail[0] - head[0], tail[1] - head[1], 0.0)
    return BeltRun(id=oid, class_name=BELT, points=((head, along, along), (tail, along, along)))


def _pinch() -> tuple[tuple[MachineObj, ...], tuple[BeltRun, ...], tuple[GridNet, ...]]:
    """A belt already in the way, with one node of gap in it and open air above.

    Going through the gap means turning twice with one node between the turns.
    Task 6 measured the corner against the shipped registry -- the cheaper of an
    arc and an attachment turn spends 301 cm of the straight either side -- so
    100 cm has room for neither, and the gap is a way through that cannot be
    built.

    Over the top can: a belt is 30 cm of clearance tall, so one level of climb
    clears it, and a climb is not a corner at all -- it is two straights at
    different slopes, which is why the way round a refused corner here is a way
    the realiser will have.  It costs a few nodes more than the gap, which is
    why the router offers the gap first and why the corner has to be priced
    before the climb is taken.
    """
    maker = _machine(101, CONSTRUCTOR, 0.0, -1100.0, 0.0, ROD)
    eater = _machine(102, CONSTRUCTOR, 0.0, 1100.0, 0.0, ROD)
    lines = _lattice().open_lines
    row = 14
    belts = (
        _crossing_belt(901, (lines.start, row, GROUND_LEVEL), (15, row, GROUND_LEVEL)),
        _crossing_belt(902, (19, row, GROUND_LEVEL), (lines.stop - 1, row, GROUND_LEVEL)),
    )
    net = _net(
        1,
        (_terminal(maker, "Output0"),),
        (_terminal(eater, "Input0"),),
        (Fraction(1, 8),),
    )
    return ((maker, eater), belts, (net,))


def test_a_realise_failure_is_charged_and_the_next_round_avoids_the_corner() -> None:
    """A corner the realiser refuses is blamed by NODE, and the net goes round it.

    The blame is deliberately local -- the corner and its two neighbours along
    the path, which is what :class:`~flab2bp.sfy.layout.realise.RealiseError`
    names -- because the loop prices congestion per node and a wide blame moves
    belts that were never in the way.  Every charge reaches BOTH the congestion
    history the kernel prices and the blame the packer reads, and the belt that
    is finally laid stands on none of it.
    """
    machines, belts, nets = _pinch()
    occupancy = occupancy_for(_lattice(), machines, (), (), belts, _registry())
    outcome = _route(nets, occupancy)

    charged = {node for node, weight in outcome.blame.items() if weight > 0.0}
    assert charged, "a refused corner names the nodes that pinched it"
    for node in charged:
        assert occupancy.history[_lattice().index(node)] > 0.0

    assert outcome.stranded == ()
    laid = outcome.trees[0].paths[0]
    assert not (set(laid) & charged), "the belt that was laid stands on none of it"
    assert max(_levels(laid)) > GROUND_LEVEL, "it went over the belt in the way"
    _judge(outcome, *machines)


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
    _judge(outcome, *machines)


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
    # Nothing is named here, and that is the rule rather than a gap: what is
    # standing in this net's way is a MACHINE, which is nobody's to move on the
    # router's say-so.  The loop names belts, and the packer -- which already
    # knows where its machines are -- is what moves a machine.
    assert outcome.blame == {}, "a machine in the way is nobody's to move"
    _judge(outcome, *machines)


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


# --- three sources into one sink -------------------------------------------


def _loop(occupancy: Occupancy) -> _Run:
    """A ``_Run`` built exactly the way :func:`route_all` builds one.

    So that the parts of the loop can be asked a question directly: which tap
    sides it would offer, in a world the test stood up node by node.
    """
    lattice, registry, measures = _lattice(), _registry(), _measures()
    return _Run(
        occupancy=occupancy,
        lattice=lattice,
        measures=measures,
        registry=registry,
        budget=WorkBudget(deadline=None, left=1_000_000),
        deadline=None,
        ids=_ids(),
        belt_class_for=belt_class_for(_spec(), _lab_map()),
        lift_class=LIFT,
        transitions=sfy_transitions(
            lattice.n + 1,
            _lift_heights(lattice, registry),
            incline_run_nodes(registry.limits, lattice.grid_cm),
        ),
        clear=max(TAP_CLEAR_NODES, math.ceil(measures.radius / measures.grid)),
        stakes=iter(range(1_000, 2_000)),
    )


def _carrier(path: Sequence[Node]) -> _Branch:
    """One branch of a tree, with only what a tap or a flow question reads."""
    return _Branch(
        path=tuple(path), source=None, sink=None, pinned=Fraction(0), tap=None, merging=False
    )


def _flow_branch(*, merging: bool, pinned: Fraction) -> _Branch:
    return _Branch(path=(), source=None, sink=None, pinned=pinned, tap=None, merging=merging)


def test_a_source_merging_into_a_merging_branch_carries_the_sum_below_it() -> None:
    """R-M3-7's arithmetic from the end each branch's own terminal pins.

    Three sources into one sink: A feeds the sink, B merges into A's branch, and
    C merges into B's.  Above its own merger B carries B alone; below it, B and
    C.  Reading a merging branch backwards from its source's rate -- which is
    right only while nothing merges into it -- makes both of B's pieces short by
    C, which tiers the belt below what it carries and can go negative.
    """
    a, b, c = Fraction(1, 2), Fraction(1, 3), Fraction(1, 5)
    branches = [
        _flow_branch(merging=False, pinned=a + b + c),
        _flow_branch(merging=True, pinned=b),
        _flow_branch(merging=True, pinned=c),
    ]

    pieces = _flows(branches, [[1], [2], []])

    assert pieces[2] == (c,)
    assert pieces[1] == (b, b + c)
    assert pieces[0] == (a, a + b + c)
    assert all(rate > 0 for branch in pieces for rate in branch)


@cache
def _wide() -> Lattice:
    """A mk2 designer, which is where a tree of three belts has room to stand.

    The taps a tree needs are interior nodes of a straight run with
    ``ceil(radius / grid)`` straight nodes on either side, and the mk1 designer
    is 3200 cm across: two machine rows and their port reaches leave a trunk
    with no interior left.  The designer is a spec input, so this is a bigger
    build rather than a looser rule.
    """
    return Lattice.over(designer("mk2", _registry()), _registry())


@cache
def _wide_measures() -> Measures:
    return _measure(_registry(), designer("mk2", _registry()))


def _three_sources() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...], Fraction]:
    """Three smelters and one constructor: two of the three have to merge in."""
    makers = tuple(
        _machine(101 + index, SMELTER, x, -1500.0, 0.0, INGOT)
        for index, x in enumerate((-700.0, 0.0, 700.0))
    )
    eater = _machine(201, CONSTRUCTOR, 0.0, 1500.0, 0.0, ROD)
    rate = FAST.items_per_second
    net = _net(
        1,
        tuple(
            terminal_for(maker, _port(SMELTER, "Output2"), _wide(), _registry()) for maker in makers
        ),
        (terminal_for(eater, _port(CONSTRUCTOR, "Input0"), _wide(), _registry()),),
        (rate,),
    )
    return ((*makers, eater), (net,), rate)


def test_three_sources_merge_into_one_sink_and_every_piece_is_tiered_for_its_own_rate() -> None:
    """A net with more sources than sinks grows mergers, and the rates follow.

    What is asserted holds whatever tree the router finds: every belt carries
    something, the belt into the sink carries the sink's whole draw, a belt out
    of a source port carries that source's share, and every belt is on the tier
    :func:`~flab2bp.sfy.layout.grid_nets.tier_for` gives its own rate -- which
    is the ceiling rule (R5) applied piece by piece rather than to the net.
    """
    machines, nets, rate = _three_sources()
    occupancy = occupancy_for(_wide(), machines, (), (), (), _registry())
    outcome = _route(nets, occupancy, measures=_wide_measures())

    assert outcome.stranded == ()
    tree = outcome.trees[0]
    assert len(tree.paths) == len(nets[0].sources)
    assert len(tree.taps) == len(nets[0].sources) - 1

    belts = [belt for laid in outcome.realised for belt in laid.belts]
    assert belts
    tiers = _spec().belt_tiers
    for belt in belts:
        assert belt.items_per_second > 0
        wanted = tier_for(belt.items_per_second, tiers, ITEM)
        assert belt.class_name == machine_class(_lab_map(), wanted.item_id)
    into_sink = [belt for belt in belts if belt.end == nets[0].sinks[0].world]
    assert [belt.items_per_second for belt in into_sink] == [rate]
    share = rate / len(nets[0].sources)
    for source in nets[0].sources:
        out_of = [belt for belt in belts if belt.start == source.world]
        assert [belt.items_per_second for belt in out_of] == [share]
    _judge(outcome, *machines, mark="mk2")


# --- a tap side another net is standing beside -----------------------------


def test_a_tap_side_another_nets_belt_shadows_is_not_offered() -> None:
    """R-M3-2: a node a grid step from ANY belt is denied, held or shadowed.

    A tap is the one exception and it is a narrow one: the node beside a net's
    OWN trunk, which the splitter it stands there will occupy the middle of.  A
    node beside somebody else's belt has nothing standing between the two, and
    offering it would open it for the query and lay a belt 58 cm into another.
    """
    occupancy = _occupancy()
    run = _loop(occupancy)
    trunk = _line((16, 8, GROUND_LEVEL), (16, 26, GROUND_LEVEL))
    net = GridNet(
        id=1, item_id=ITEM, sources=(), sinks=(), rate=Fraction(0), per_sink=(), per_source=()
    )
    run.stake(net.id, trunk)
    branches = (_carrier(trunk),)

    both_sides = run.tap_sites(branches, net)
    assert (17, 16, GROUND_LEVEL) in both_sides
    assert (15, 16, GROUND_LEVEL) in both_sides

    run.stake(2, _line((18, 10, GROUND_LEVEL), (18, 22, GROUND_LEVEL)))
    left_only = run.tap_sites(branches, net)

    assert (17, 16, GROUND_LEVEL) not in left_only, "that side stands in net 2's shadow"
    assert (15, 16, GROUND_LEVEL) in left_only


def test_a_stranded_net_names_the_belt_in_its_doorway_and_no_machine() -> None:
    """What a walled-in port learns, when no search ever proves a pocket.

    A lift out of a port is shut in advance -- its column would run through the
    port's own machine -- so a net whose one node of floor is taken usually ends
    its round without a sealed pocket to census, and this is the blame that case
    deserves: whoever is HOLDING or shadowing the floor just outside its ports.
    A machine there is named by nothing, because a machine is the packer's to
    move and the packer knows where it put them.
    """
    occupancy = _occupancy()
    run = _loop(occupancy)
    port = Terminal(
        node=(16, 12, GROUND_LEVEL),
        world=_lattice().world((16, 12, GROUND_LEVEL)),
        facing=(0.0, 1.0, 0.0),
        port=(101, "Output2"),
        kind="port",
        reach=((16, 12, GROUND_LEVEL), (16, 13, GROUND_LEVEL)),
    )
    net = GridNet(
        id=1,
        item_id=ITEM,
        sources=(port,),
        sinks=(),
        rate=Fraction(0),
        per_sink=(),
        per_source=(Fraction(0),),
    )

    assert _in_the_doorway(run, net) == ()

    across = _line((10, 14, GROUND_LEVEL), (22, 14, GROUND_LEVEL))
    run.stake(2, across)

    named = set(_in_the_doorway(run, net))

    assert (16, 14, GROUND_LEVEL) in named, "the belt on the one node of floor is named"
    assert named <= _shadow_of(across), "and nothing that net 2 is not holding or shadowing"
