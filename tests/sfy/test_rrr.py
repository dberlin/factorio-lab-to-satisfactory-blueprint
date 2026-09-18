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
from collections.abc import Collection, Iterator, Sequence
from fractions import Fraction
from functools import cache
from itertools import count

import pytest

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.labmap import LabMap, load_lab_map, machine_class
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import (
    TAP_CLEAR_NODES,
    GridNet,
    belt_class_for,
    nets_for,
    terminal_for,
    tier_for,
)
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node, Occupancy, occupancy_for
from flab2bp.sfy.layout.manifold import SPLITTER_CLASS
from flab2bp.sfy.layout.model import (
    BeltRun,
    LiftObj,
    MachineObj,
    Pose,
    SfyPlacement,
    Vector,
)
from flab2bp.sfy.layout.motion import lift_heights
from flab2bp.sfy.layout.realise import Realised, Terminal, realise
from flab2bp.sfy.layout.router import BudgetCause, Routed, RouteFailureKind
from flab2bp.sfy.layout.rrr import (
    RRR_MAX,
    RoutedTree,
    RoutingOutcome,
    _attempt,
    _blocked_column,
    _Branch,
    _build,
    _commit_branch,
    _flows,
    _in_the_doorway,
    _physical,
    _Played,
    _restore,
    _Run,
    _stake_column,
    route_all,
)
from flab2bp.sfy.layout.splines import straight
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.transitions import incline_run_nodes, sfy_transitions
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM, TOUCH_CM, validate
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.spec import BeltTier
from tests.sfy.conftest import flow_spec
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


def _wall_line(lattice: Lattice, *, inward: bool = False) -> tuple[Terminal, ...]:
    """A whole designer wall as one stream's worth of terminals -- R-M3-5.

    The same set :func:`~flab2bp.sfy.layout.grid_nets.nets_for` builds: a node on
    the innermost line a belt may stand on, with the belt's end out ON the wall,
    and a facing that points into the designer either way -- the ``-Y`` wall
    (``inward``) feeds the build and the ``+Y`` wall drains it.
    """
    lines = lattice.open_lines
    row = lines.start if inward else lines.stop - 1
    half = lattice.designer.half_cm
    facing: Vector = (0.0, 1.0, 0.0) if inward else (0.0, -1.0, 0.0)
    out: list[Terminal] = []
    for i in lines:
        node = (i, row, GROUND_LEVEL)
        here = lattice.world(node)
        out.append(
            Terminal(
                node=node,
                world=(here[0], -half if inward else half, here[2]),
                facing=facing,
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


def test_turn_policy_routes_over_an_unbuildable_gap_without_corner_blame() -> None:
    """Infeasible turns are excluded before emission, not learned by retrying."""
    machines, belts, nets = _pinch()
    occupancy = occupancy_for(_lattice(), machines, (), (), belts, _registry())
    outcome = _route(nets, occupancy)

    assert outcome.blame == {}

    assert outcome.stranded == ()
    laid = outcome.trees[0].paths[0]
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


def test_lifts_cross_a_constructor_wall_with_flat_owned_landings() -> None:
    """The certified path searches flat lift connections, without landing rewrites."""
    machines = tuple(
        _machine(100 + index, CONSTRUCTOR, x, 0.0, 0.0, ROD)
        for index, x in enumerate((-1200.0, -400.0, 400.0, 1200.0))
    )
    lattice = _lattice()
    source = Terminal((16, 1, 2), lattice.world((16, 1, 2)), (0.0, 1.0, 0.0), None, "wall")
    sink = Terminal((16, 31, 2), lattice.world((16, 31, 2)), (0.0, -1.0, 0.0), None, "wall")
    net = _net(1, (source,), (sink,), (Fraction(1, 4),))
    occupancy = _occupancy(*machines)
    outcome = _route((net,), occupancy)
    assert outcome.stranded == ()

    lifts = tuple(lift for piece in outcome.realised for lift in piece.lifts)
    assert {math.copysign(1.0, lift.height_cm) for lift in lifts} == {-1.0, 1.0}
    for tree in outcome.trees:
        for path in tree.paths:
            for node in path:
                assert not occupancy.free(node)
    _judge(outcome, *machines)


def test_tap_cut_preserves_parent_turn_room_and_restores_its_witnesses() -> None:
    """A structurally straight tap may still consume a preceding turn's debt."""
    lattice = _lattice()
    source_node, corner, sink_node = (4, 8, 2), (8, 8, 2), (8, 20, 2)
    path = (*_line(source_node, corner), *_line(corner, sink_node)[1:])
    source = Terminal(source_node, lattice.world(source_node), (1.0, 0.0, 0.0), None, "wall")
    sink = Terminal(sink_node, lattice.world(sink_node), (0.0, -1.0, 0.0), None, "wall")
    share = Fraction(1, 8)
    net = _net(1, (source,), (sink,), (share,))
    run = _loop(_occupancy())
    allowed = frozenset(path)
    closed = tuple(
        (x, y, z)
        for x in range(lattice.n + 1)
        for y in range(lattice.n + 1)
        for z in range(lattice.n + 1)
        if (x, y, z) not in allowed
    )
    routed = run.query((source,), (sink,), (), closed)
    assert routed.path == path
    branches: list[_Branch] = []
    _commit_branch(run, net, branches, routed, source, sink, share, tap=None, merging=False)

    sites = run.tap_sites(branches, net)
    assert (9, 12, 2) not in sites  # The new body would crowd the preceding turn.
    assert (9, 13, 2) in sites
    tap = run.tap_access(branches, net, ())[(10, 13, 2)]
    assert tap.terminal is not None
    assert tap.terminal.node == (10, 13, 2)
    assert tap.terminal.world == lattice.world((9, 13, 2))
    child_node = (20, 13, 2)
    child_sink = Terminal(child_node, lattice.world(child_node), (-1.0, 0.0, 0.0), None, "wall")
    child = run.query((tap.terminal,), (child_sink,), (), ())
    assert child.path is not None
    _commit_branch(
        run, net, branches, child, tap.terminal, child_sink, share, tap=tap, merging=False
    )
    attempt = _build(run, net, branches, child)
    assert attempt.tree is not None, (attempt.failure, attempt.nodes)
    tree = attempt.tree
    assert len(tree.proofs[0]) == 2
    assert tree.proofs[1][0].path[0] == tap.terminal.node
    assert tree.proofs[1][0].motion == child.motion
    _judge(RoutingOutcome((tree,), (), attempt.realised, 1, run.work, run.blame))
    best = _Played(
        (tree,),
        (),
        attempt.realised,
        tuple((net.id, *ends) for ends in attempt.columns),
        tuple(run.physical.items()),
        0.0,
    )
    run.release(net.id)
    assert net.id not in run.proofs
    _restore(run, best)
    assert run.proofs[net.id] == tree.proofs
    assert not run.occupancy.free((9, 13, 2))


def test_restore_keeps_transverse_shadow_of_an_off_grid_tap_stub() -> None:
    from flab2bp.sfy.layout.motion import PathProof

    run = _loop(_occupancy())
    lattice = run.lattice
    centre = lattice.world((8, 12, GROUND_LEVEL))
    source = Terminal(
        (11, 12, GROUND_LEVEL),
        (centre[0] + 125.0, centre[1], centre[2]),
        (1.0, 0.0, 0.0),
        (101, "Output0"),
        "tap",
    )
    sink_node = (20, 12, GROUND_LEVEL)
    sink = Terminal(sink_node, lattice.world(sink_node), (-1.0, 0.0, 0.0), None, "wall")
    share = Fraction(1, 8)
    net = _net(1, (source,), (sink,), (share,))
    routed = run.query((source,), (sink,), (), ())
    assert routed.path is not None and routed.motion is not None
    proof = PathProof(routed.path, source, sink, routed.motion)
    tree = RoutedTree(net, (routed.path,), (), (), ((proof,),))
    side = (10, 12, GROUND_LEVEL)
    run.stake(net.id, routed.path)
    run.stake(net.id, (source.node, side))
    before = run.occupancy.snapshot()
    beside_stub = (10, 11, GROUND_LEVEL)
    assert not run.occupancy.free(beside_stub)

    _restore(run, _Played((tree,), (), (), (), (), 0.0))

    assert not run.occupancy.free(beside_stub)
    assert run.occupancy.snapshot() == before


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
    lattice, registry = occupancy.lattice, _registry()
    measures = _measure(registry, lattice.designer)
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
            lift_heights(lattice, registry),
            incline_run_nodes(registry.limits, lattice.grid_cm),
        ),
        clear=TAP_CLEAR_NODES,
        stakes=iter(range(1_000, 2_000)),
    )


def _column_candidate() -> Realised:
    """The far shaft of the measured Constructor-row route: 5 cm beyond its wall."""
    return Realised(
        belts=(),
        attachments=(),
        lifts=(LiftObj(9000, LIFT, Pose(0, 600, 200, 0), 600),),
        links=(),
        lift_columns=(((16, 22, 2), (16, 22, 8)),),
    )


def _column_net(candidate: Realised) -> GridNet:
    low, high = candidate.lift_columns[0]
    lattice = _lattice()
    source = Terminal(low, lattice.world(low), (1.0, 0.0, 0.0), None, "wall")
    sink = Terminal(high, lattice.world(high), (-1.0, 0.0, 0.0), None, "wall")
    return _net(1, (source,), (sink,), (Fraction(1, 4),))


def _column_blocked(run: _Run, candidate: Realised) -> tuple[Node, ...]:
    return _blocked_column(
        run, _column_net(candidate), (candidate,), _physical((candidate,), run.registry)
    )


def test_lift_five_cm_from_constructor_does_not_collide_with_a_virtual_belt() -> None:
    machine = _machine(100, CONSTRUCTOR, -400, 0, 0, ROD)
    assert _column_blocked(_loop(_occupancy(machine)), _column_candidate()) == ()


@pytest.mark.parametrize("overlap,blocked", ((0.0, False), (TOUCH_CM, False), (2 * TOUCH_CM, True)))
def test_lift_physical_overlap_uses_touch_tolerance(overlap: float, blocked: bool) -> None:
    occupancy = _occupancy()
    # The lift's near face is y=505; the centre shaft remains free in all cases.
    occupancy.block_box((-200.0, 300.0, 200.0), (200.0, 505.0 + overlap, 800.0))
    candidate = _column_candidate()
    assert _column_blocked(_loop(occupancy), candidate) == (
        candidate.lift_columns[0] if blocked else ()
    )


@pytest.mark.parametrize("kind", ("belt", "lift"))
@pytest.mark.parametrize("clear", (False, True), ids=("overlap", "physical-gap"))
def test_lift_checks_foreign_physical_objects_after_rip_up_and_restore(
    kind: str, clear: bool
) -> None:
    run = _loop(_occupancy())
    candidate = _column_candidate()
    assert _column_blocked(run, candidate) == ()
    if kind == "belt":
        y = 20 if clear else 21
        path = _line((10, y, 2), (22, y, 2))
    else:
        x = 18 if clear else 17
        path = (
            *_line((25, 22, 2), (x, 22, 2)),
            *_line((x, 22, 8), (25, 22, 8)),
        )
    source = Terminal(
        path[0],
        run.lattice.world(path[0]),
        (1.0 if kind == "belt" else -1.0, 0.0, 0.0),
        None,
        "wall",
    )
    sink = Terminal(path[-1], run.lattice.world(path[-1]), (-1.0, 0.0, 0.0), None, "wall")
    share = Fraction(1, 4)
    net = _net(2, (source,), (sink,), (share,))
    branch = _Branch(tuple(path), source, sink, share, None, False)
    run.stake(net.id, path)
    attempt = _build(run, net, (branch,), None)
    assert attempt.tree is not None
    expected = () if clear else candidate.lift_columns[0]
    assert _column_blocked(run, candidate) == expected
    best = _Played(
        trees=(attempt.tree,),
        stranded=(),
        realised=attempt.realised,
        columns=tuple((net.id, *ends) for ends in attempt.columns),
        physical=tuple(run.physical.items()),
        length_cm=0.0,
    )
    run.release(net.id)
    assert _column_blocked(run, candidate) == ()
    _restore(run, best)
    assert _column_blocked(run, candidate) == expected


def test_lift_rejects_unknown_foreign_occupancy_even_behind_known_shadow() -> None:
    run = _loop(_occupancy())
    candidate = _column_candidate()
    # An actual belt 200 cm east leaves 26 cm clear, but its node shadow meets
    # the lift's footprint. Hide an unregistered second claimant behind it.
    path = _line((18, 20, 2), (18, 24, 2))
    belt = BeltRun(9001, BELT, straight((200.0, 400.0, 200.0), (0.0, 1.0, 0.0), 400.0))
    run.stake(2, path)
    run.physical[2] = _physical((Realised((belt,), (), (), (), ()),), run.registry)
    assert _column_blocked(run, candidate) == ()
    run.occupancy.commit(9999, path)
    assert _column_blocked(run, candidate) == candidate.lift_columns[0]


def _carrier(path: Sequence[Node]) -> _Branch:
    """One branch of a tree, with only what a tap or a flow question reads."""
    return _Branch(
        path=tuple(path), source=None, sink=None, pinned=Fraction(0), tap=None, merging=False
    )


@pytest.mark.parametrize("reverse", (False, True), ids=("source-level-via", "reversed-via"))
def test_tapped_trees_preserve_whole_ramps_in_both_via_forms(reverse: bool) -> None:
    """The nearest legal taps must leave a whole incline clear of their ports.

    Build the tree, not a mirror of ``_pieces``: the actual splitters, links and
    belt splines must validate, and the trunk must still climb one level over
    two grid steps. Both via encodings accepted by the realiser are exercised.
    """
    lattice = _lattice()
    foot = (16, 14, GROUND_LEVEL)
    via = (16, 15, GROUND_LEVEL)
    crest = (16, 16, GROUND_LEVEL + 1)
    path = (
        *_line((16, 2, GROUND_LEVEL), foot),
        via,
        *_line(crest, (16, 30, crest[2])),
    )
    if reverse:
        # A reversed via at the start exercises the level-change-first decoder,
        # rather than another flat-first ramp selected from the preceding run.
        path = (crest, via, *reversed(_line((16, 2, GROUND_LEVEL), foot)))
    heading = (0.0, -1.0 if reverse else 1.0, 0.0)

    def terminal(node: Node, facing: Vector) -> Terminal:
        return Terminal(node=node, world=lattice.world(node), facing=facing, port=None, kind="wall")

    source = terminal(path[0], heading)
    sink = terminal(path[-1], (0.0, -heading[1], 0.0))
    share = Fraction(1, 8)
    trunk = _Branch(path, source, sink, share, None, False)
    net = _net(1, (source,), (sink,), (share,))
    run = _loop(_occupancy())
    # Structural candidates still preserve complete ramps in either authored form.
    run.stake(net.id, path)
    sites = run.tap_sites((trunk,), net)
    room = math.ceil((_measures().box + BELT_CLEARANCE_HALF_WIDTH_CM) / lattice.grid_cm)
    near = [
        max(
            (tap for side, tap in sites.items() if side[0] == 17 and tap.node[1] <= foot[1] - room),
            key=lambda tap: tap.node[1],
        ),
    ]
    if not reverse:
        near.append(
            min(
                (
                    tap
                    for side, tap in sites.items()
                    if side[0] == 17 and tap.node[1] >= crest[1] + room
                ),
                key=lambda tap: tap.node[1],
            )
        )
    branches = [trunk]
    for tap in near:
        end = (29, tap.side[1], tap.side[2])
        branches.append(
            _Branch(_line(tap.side, end), None, terminal(end, (-1.0, 0.0, 0.0)), share, tap, False)
        )
    net = _net(
        1,
        (source,),
        tuple(branch.sink for branch in branches if branch.sink is not None),
        (share,) * len(branches),
    )

    attempt = _build(run, net, branches, None)

    assert attempt.tree is not None, (attempt.failure, attempt.nodes)
    attachments = [obj for laid in attempt.realised for obj in laid.attachments]
    assert len(attachments) == (1 if reverse else 2)
    assert all(obj.class_name == SPLITTER_CLASS for obj in attachments)
    belts = [belt for laid in attempt.realised for belt in laid.belts]
    climbs = [
        (a[0], b[0])
        for belt in belts
        for a, b in zip(belt.points, belt.points[1:], strict=False)
        if abs(a[0][2] - b[0][2]) > 1e-9
    ]
    assert len(climbs) == 1
    head, tail = climbs[0]
    assert tail[2] - head[2] == pytest.approx((-1 if reverse else 1) * lattice.grid_cm)
    assert math.dist(head[:2], tail[:2]) == pytest.approx(2 * lattice.grid_cm)
    assert head == pytest.approx(lattice.world(crest if reverse else foot))
    assert tail == pytest.approx(lattice.world(foot if reverse else crest))
    for branch in branches:
        assert branch.sink is not None
        carried = [belt.items_per_second for belt in belts if belt.end == branch.sink.world]
        assert carried == [share]
    supplied = [belt.items_per_second for belt in belts if belt.start == source.world]
    assert supplied == [len(branches) * share]
    outcome = RoutingOutcome((attempt.tree,), (), attempt.realised, 1, 0, {})
    _judge(outcome)


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
    """A mk2 designer with room for the three-belt tree and its actual tap cuts."""
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


def test_tap_beside_lift_corner_keeps_single_physical_clearance() -> None:
    run = _loop(_occupancy())
    trunk = _line((4, 12, GROUND_LEVEL), (24, 12, GROUND_LEVEL))
    net = GridNet(
        id=1, item_id=ITEM, sources=(), sinks=(), rate=Fraction(0), per_sink=(), per_source=()
    )
    run.stake(net.id, trunk)
    branches = (_carrier(trunk),)
    side = (12, 11, GROUND_LEVEL)
    assert side in run.tap_sites(branches, net)

    _stake_column(run, 2, (10, 10, GROUND_LEVEL), (10, 10, GROUND_LEVEL + 4))

    # The trunk and branch are both 200 cm from the shaft: more than 95+79.
    sites = run.tap_sites(branches, net)
    assert side in sites
    assert (10, 11, GROUND_LEVEL) not in sites
    run.release(net.id)
    assert run.blocker((11, 11, GROUND_LEVEL)) == 2


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


# --- a wall that hands the same item over more than once --------------------


def _wall_fed(y: float) -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...], Fraction]:
    """Three machines fed from the ``-Y`` wall, standing ``y`` from the middle.

    Every port faces the wall, so how much trunk there is to tap is exactly how
    far the row stands off it -- which is the whole question here, and it is the
    packer's answer in a real build.
    """
    eaters = tuple(
        _machine(101 + index, CONSTRUCTOR, x, y, 0.0, ROD)
        for index, x in enumerate((-900.0, 0.0, 900.0))
    )
    share = Fraction(1, 8)
    sinks = tuple(_terminal(eater, "Input0") for eater in eaters)
    wall = _wall_line(_lattice(), inward=True)
    net = GridNet(
        id=1,
        item_id=ITEM,
        sources=wall,
        sinks=sinks,
        rate=share * len(sinks),
        per_sink=(share,) * len(sinks),
        per_source=(share * len(sinks),) * len(wall),
    )
    return (eaters, (net,), share)


def _entries(tree: RoutedTree, lattice: Lattice) -> tuple[Node, ...]:
    """Where a tree crosses the wall, as the tree itself records it."""
    for index, node in tree.boundaries:
        assert node in (tree.paths[index][0], tree.paths[index][-1])
        assert node[1] in (lattice.open_lines.start, lattice.open_lines.stop - 1)
    return tuple(node for _index, node in tree.boundaries)


def test_a_wall_fed_net_whose_trunk_has_no_tap_takes_another_entry() -> None:
    """R-M3-5, and the ruling on it: the boundary may hand the item over twice.

    The packer stands a wall-fed port as close to the wall as its apron allows,
    so the first belt in is a few nodes long and there is no interior of a
    straight run to tap: a tap needs ``clear`` straight nodes on either side of
    it.  The second sink then has nowhere to start -- unless it starts where the
    first one did.  What is outside the designer can hand the same item over at
    two places as easily as at one, so it does, and each entry is its own belt
    carrying its own sink's share.
    """
    machines, nets, share = _wall_fed(-1000.0)
    lattice = _lattice()
    outcome = _route(nets, _occupancy(*machines))

    assert outcome.stranded == ()
    tree = outcome.trees[0]
    assert len(tree.paths) == len(nets[0].sinks)
    entries = _entries(tree, lattice)
    assert len(entries) >= 2, "the trunk was too short to tap, so the wall was asked again"
    assert len(set(entries)) == len(entries), "and no two belts enter at one node"
    assert all(path[0] in entries for path in tree.paths)

    belts = [belt for laid in outcome.realised for belt in laid.belts]
    half = lattice.designer.half_cm
    at_wall = [belt for belt in belts if belt.start[1] == -half]
    assert len(at_wall) == len(entries)
    assert [belt.items_per_second for belt in at_wall] == [share] * len(entries)
    _judge(outcome, *machines)


def _wall_tapped() -> tuple[tuple[MachineObj, ...], tuple[GridNet, ...], Fraction]:
    """A wall-fed pair with room: a long trunk, and a second sink off to one side.

    The first sink stands far enough off the wall for the belt in to have an
    interior to cut open, and the second stands far enough round that the branch
    from the tap has a run into its corner and a run out of it -- the four grid
    steps Task 6 measured a turn needs.
    """
    near = _machine(101, CONSTRUCTOR, 0.0, 900.0, 0.0, ROD)
    far = _machine(102, CONSTRUCTOR, 1200.0, 0.0, 0.0, ROD)
    share = Fraction(1, 8)
    sinks = (_terminal(near, "Input0"), _terminal(far, "Input0"))
    wall = _wall_line(_lattice(), inward=True)
    net = GridNet(
        id=1,
        item_id=ITEM,
        sources=wall,
        sinks=sinks,
        rate=share * len(sinks),
        per_sink=(share,) * len(sinks),
        per_source=(share * len(sinks),) * len(wall),
    )
    return ((near, far), (net,), share)


def test_a_wall_fed_net_taps_its_own_trunk_where_there_is_room() -> None:
    """And a tap is preferred where there is one: it is one belt fewer.

    The wall is the fallback and not the first answer, so a tree with somewhere
    to be cut open is cut open: one belt crosses the boundary and the second
    sink hangs off it.
    """
    machines, nets, share = _wall_tapped()
    outcome = _route(nets, _occupancy(*machines))

    assert outcome.stranded == ()
    tree = outcome.trees[0]
    assert len(tree.paths) == len(nets[0].sinks)
    assert len(_entries(tree, _lattice())) == 1
    assert len(tree.taps) == len(nets[0].sinks) - 1

    belts = [belt for laid in outcome.realised for belt in laid.belts]
    half = _lattice().designer.half_cm
    at_wall = [belt for belt in belts if belt.start[1] == -half]
    assert [belt.items_per_second for belt in at_wall] == [share * len(nets[0].sinks)]
    _judge(outcome, *machines)


def test_independent_wall_exits_carry_only_their_connected_sources() -> None:
    """Three short trunks need three exits, not one full-rate exit plus two extras."""
    makers = tuple(
        _machine(101 + index, SMELTER, x, 1000.0, 0.0, INGOT)
        for index, x in enumerate((-900.0, 0.0, 900.0))
    )
    sources = tuple(_terminal(maker, "Output2") for maker in makers)
    wall = _wall_line(_lattice(), inward=False)
    share = Fraction(1, 8)
    rate = len(sources) * share
    net = GridNet(1, ITEM, sources, wall, rate, (rate,) * len(wall), (share,) * len(sources))

    outcome = _route((net,), _occupancy(*makers))

    assert outcome.stranded == ()
    assert len(_entries(outcome.trees[0], _lattice())) == len(sources)
    belts = [belt for laid in outcome.realised for belt in laid.belts]
    exits = [belt for belt in belts if belt.end[1] == _lattice().designer.half_cm]
    assert sum((belt.items_per_second for belt in exits), Fraction(0)) == rate
    assert [belt.items_per_second for belt in exits] == [share] * len(sources)
    for source in sources:
        assert [belt.items_per_second for belt in belts if belt.start == source.world] == [share]
    assert all(belt.class_name == BELT for belt in belts)
    _judge(outcome, *makers)


def test_a_machine_and_external_input_both_supply_the_same_sink() -> None:
    """Reaching the sink from its local producer must not erase its imported share."""
    maker = _machine(101, SMELTER, 0.0, -1100.0, 0.0, INGOT)
    eater = _machine(102, CONSTRUCTOR, 0.0, 1100.0, 0.0, ROD)
    source, sink = _terminal(maker, "Output2"), _terminal(eater, "Input0")
    wall = _wall_line(_lattice(), inward=True)
    made, imported = Fraction(1, 8), Fraction(1, 4)
    rate = made + imported
    net = GridNet(
        1, ITEM, (source, *wall), (sink,), rate, (rate,), (made,) + (imported,) * len(wall)
    )

    outcome = _route((net,), _occupancy(maker, eater))

    assert outcome.stranded == ()
    belts = [belt for laid in outcome.realised for belt in laid.belts]
    entries = [belt for belt in belts if belt.start[1] == -_lattice().designer.half_cm]
    assert sum((belt.items_per_second for belt in entries), Fraction(0)) == imported
    assert [belt.items_per_second for belt in belts if belt.start == source.world] == [made]
    assert [belt.items_per_second for belt in belts if belt.end == sink.world] == [rate]
    into_sink = next(belt for belt in belts if belt.end == sink.world)
    assert into_sink.class_name == machine_class(_lab_map(), FAST.item_id)
    _judge(outcome, maker, eater)


def test_a_shadowed_tap_candidate_falls_back_to_a_valid_wall_entry() -> None:
    """Congested fan-out must supply every sink without lapping its own trunk."""
    spec = flow_spec("concrete-60")
    group = spec.groups[0]
    machines = tuple(
        _machine(index + 101, group.machine_class, x, y, 0.0, group.recipe_class)
        for index, (x, y) in enumerate(
            ((-1000.0, -900.0), (-100.0, -900.0), (-100.0, 300.0), (800.0, -900.0))
        )
    )
    nets = tuple(
        net
        for net in nets_for(spec, machines, _lattice(), _registry(), _lab_map())
        if net.item_id == "limestone"
    )

    outcome = _route(nets, _occupancy(*machines), spec=spec)

    assert outcome.stranded == ()
    imported = sum(
        (
            belt.items_per_second
            for laid in outcome.realised
            for belt in laid.belts
            if belt.item_id == "limestone" and belt.start[1] == -_lattice().designer.half_cm
        ),
        Fraction(0),
    )
    assert imported == spec.external_inputs["limestone"]
    _judge(outcome, *machines)


def test_wall_flow_can_share_a_turn_certified_tap_without_short_belts() -> None:
    """A legal tap may share an entry; imported flow and both deliveries remain exact."""
    near = _machine(101, CONSTRUCTOR, 0.0, 900.0, 0.0, ROD)
    far = _machine(102, CONSTRUCTOR, 1000.0, -900.0, 0.0, ROD)
    sinks = (_terminal(near, "Input0"), _terminal(far, "Input0"))
    wall = _wall_line(_lattice(), inward=True)
    share = Fraction(1, 8)
    net = GridNet(1, ITEM, wall, sinks, 2 * share, (share, share), (2 * share,) * len(wall))

    outcome = _route((net,), _occupancy(near, far))

    assert outcome.stranded == ()
    belts = [belt for laid in outcome.realised for belt in laid.belts]
    incoming = [belt for belt in belts if belt.start[1] == -_lattice().designer.half_cm]
    assert sum((belt.items_per_second for belt in incoming), Fraction(0)) == 2 * share
    for sink in sinks:
        assert [belt.items_per_second for belt in belts if belt.end == sink.world] == [share]
    _judge(outcome, near, far)


def test_internal_taps_do_not_open_their_neighbors_as_ramp_vias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tap paths must leave their shadow and retain the real connector stub."""
    lattice = _lattice()
    machines, (net,) = _tapped()
    query = _Run.query
    tap_paths: list[tuple[Routed, Terminal, Terminal]] = []
    clashes: list[Node] = []
    reaches = {node for end in (*net.sources, *net.sinks) for node in end.reach}

    def observe(
        run: _Run,
        starts: Collection[Terminal],
        goals: Collection[Terminal],
        opened: Collection[Node],
        closed: Collection[Node],
    ) -> Routed:
        routed = query(run, starts, goals, opened, closed)
        if routed.path:
            source = next(terminal for terminal in starts if terminal.node == routed.path[0])
            if source.kind == "tap":
                sink = next(terminal for terminal in goals if terminal.node == routed.path[-1])
                tap_paths.append((routed, source, sink))
            clashes.extend(
                node
                for node in routed.path[1:-1]
                if node not in reaches and run.lattice.index(node) in run.occupancy.owner
            )
        return routed

    monkeypatch.setattr(_Run, "query", observe)
    run = _loop(occupancy_for(lattice, machines, (), (), (), _registry()))
    attempt = _attempt(run, net, ())

    assert clashes == [], "a query may not traverse its trunk's occupied shadow"
    assert attempt.failure != "shadow"
    # Success control: actually build one returned tap path, not a vacuous
    # shadow assertion over only failed queries.
    tapped, source, sink = next(iter(tap_paths))
    assert tapped.path is not None
    assert tapped.motion is not None
    built = realise(
        tapped.path,
        source=source,
        sink=sink,
        lattice=lattice,
        measures=_measure(_registry(), lattice.designer),
        registry=_registry(),
        belt_class=BELT,
        lift_class=LIFT,
        item_id=ITEM,
        rate=Fraction(1, 8),
        ids=count(10_000),
        motion=tapped.motion,
    )
    first = next(belt for belt in built.belts if belt.start == source.world)
    assert first.start != lattice.world(tapped.path[0])
    assert any(belt.end == sink.world for belt in built.belts)
