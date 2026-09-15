"""What a grid-routed build has to belt, and where a belt may be tapped.

Everything the rip-up loop in :mod:`flab2bp.sfy.layout.rrr` routes is planned
here, and nothing here decides a geometry: a :class:`GridNet` says which ports
one item travels between and at what rate, a
:class:`~flab2bp.sfy.layout.realise.Terminal` says where one of those ports is
on the lattice, and :func:`tap_nodes` says where a committed belt may be cut
open for a splitter.  The rates are the spec's own exact ``Fraction``s --
FactorioLab's flow is authoritative and nothing here re-solves it.

**One net per item, except where the SPEC already split it** (R-M3-7).  An item
is one tree with every producer's output port as a source and every consumer's
input port as a sink, plus the designer wall where the spec belts the item in or
sends it out.  The exception is :func:`~flab2bp.sfy.spec.direct_pairs`: where
one group makes exactly what another eats, machine for machine, the flow is N
independent one-to-one flows that FactorioLab has already balanced, so it
becomes N nets of one source and one sink rather than one N-to-N tree.  That is
a property of the rates, read off the spec, and not a layout decision, and it
replaces exactly ONE item's flow: the groups a pair joins go on eating the ore
they eat and making the plate they make, and every one of those items gets its
net the way any group's does.

**Two readers, one numbering.**  :func:`net_plan` settles which nets there are,
what they carry and what they are called, from the spec alone; :func:`nets_for`
is that list with the placed machines' ports put on it.  The packer reads the
first (it needs ids and ports before it has a pose to give anything) and the
router reads the second, so a weight fed back under net 3 lands on net 3.

**A wall terminal set is a CHOICE, not a sum.**  A machine port is one place a
belt really starts, so a net with three machine sources has three belts leaving
three ports.  The designer wall is not: R-M3-5 lets an external input enter at
any ``X`` on the ``-Y`` wall, so the whole wall line arrives here as one
terminal per node and the router picks one of them.  ``per_source`` stays
parallel to ``sources`` either way, and every entry of a wall set carries the
net's whole rate because each is the same stream entering somewhere else.

**Where a terminal stands** (R-M3-1 and R-M3-2 (d)).  ``world`` is the port's
own position -- the machine's pose applied to ``Port.translation`` -- and
``node`` is the first lattice node out along the port's normal, which for a port
already standing on a node is that node.  ``reach`` is the run of nodes from
there to the machine's own hard-box edge along that normal: they lie INSIDE the
box and the occupancy denies them to everyone, so they travel with the terminal
and are opened for this net's query alone.  A port whose two other axes do not
land on lattice lines is REFUSED (``cause`` ``"lattice"``) rather than rounded
to one: a belt that leaves a port off its own normal is a belt
``ports.position`` turns away.

**A tap is a place a run CAN be cut, not a place a splitter is standing.**
:func:`tap_nodes` answers R-M3-7's question -- which nodes of a committed tree
are interior to a straight run with ``clear`` straight nodes on either side --
and the caller decides which one to use.  ``clear`` is the caller's because the
space a corner really takes is the turn's, which
:class:`~flab2bp.sfy.layout.corridors.Measures` states and this module never
reads: :mod:`flab2bp.sfy.layout.rrr` passes ``ceil(measures.radius / grid)``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import count

from flab2bp.sfy.geometry import box_bounds, port_forward, snap_zeros, world_port
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node
from flab2bp.sfy.layout.model import MachineObj, Vector
from flab2bp.sfy.layout.realise import Terminal
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM, TOUCH_CM
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, direct_pairs
from flab2bp.spec import BeltTier

__all__ = [
    "TAP_CLEAR_NODES",
    "GridNet",
    "NetError",
    "NetPlan",
    "belt_class_for",
    "facing_for",
    "net_plan",
    "nets_for",
    "snapped",
    "tap_nodes",
    "terminal_for",
    "tier_for",
]

_EPS = 1e-9

LATTICE_TOUCH_CM = 0.1
"""How far off a lattice line a port may stand and still be ON it.

Ours, and a tolerance rather than a bound.  ``registry.json``'s port
translations are the cooked asset's own 32-bit floats, so a Smelter output
authored at ``x = 0`` arrives here as ``-3.5e-05`` and a splitter port authored
at ``y = 0`` as ``1.6e-05``.  A tenth of a millimetre is three orders above that
noise and well below the 25 cm a Manufacturer's input really stands off the grid
(R-M3-1), so nothing this milestone meets falls on the wrong side of it.
"""

TAP_CLEAR_NODES = 3
"""How many straight nodes are kept on either side of a tap, at the least.

R-M3-7 says two, and two is one short: ``clear`` nodes of run span ``clear - 1``
grid steps, so a piece cut off two nodes is 100 cm of belt and R-M3-4's floor is
one centimetre over that.  Three nodes span 200 cm and clear it, which is why
this is three and the rule's own number is a node count rather than a length.

The floor rather than the answer, either way: a caller that knows what a turn
costs where it stands passes its own, and :mod:`flab2bp.sfy.layout.rrr` passes
``ceil(measures.radius / grid)``.
"""


class NetError(ValueError):
    """A build this module will not plan nets for, named by its cause.

    ``cause`` is a discriminator rather than prose, the way
    :class:`~flab2bp.sfy.layout.corridors.CorridorError`'s is, and the grid
    strategy turns each into one of
    :data:`~flab2bp.sfy.layout.refusals.REFUSALS`:

    * ``"ceiling"`` -- a run carries more than the fastest belt the spec funds,
      which is ``run exceeds the belt ceiling``;
    * ``"lattice"`` -- a port does not stand on the hologram lattice, which is
      ``a port is off the hologram lattice``;
    * ``"ports"`` -- a machine has fewer belt ports than its recipe has items,
      which is ``more input items than the machine has belt ports``;
    * ``"data"`` -- the registry or the lab map does not describe something this
      build needs, which is ``GAME_DATA``.

    The refusal strings live in :mod:`flab2bp.sfy.layout.refusals` and the
    mapping in the strategy, so that nothing below a strategy invents a cause.
    """

    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(detail)
        self.cause = cause
        self.detail = detail


@dataclass(frozen=True, slots=True)
class NetPlan:
    """One net as the SPEC states it, before anything has been placed.

    A net's identity and its rates are a property of the spec and of nothing
    else, so they are settled here and read by both of the stages that need
    them: the packer, which weights its objective by how far a net's ports are
    apart and needs the ids before it has any poses, and :func:`nets_for`, which
    puts the placed machines' ports on them afterwards.

    ``sources`` and ``sinks`` name MACHINE ports, as ``(machine index, port
    name)``, and the index is the position in the flat machine list
    :func:`net_plan` documents.  An empty tuple on a side means no machine is on
    it, which in a complete spec is the designer wall -- the ``-Y`` wall for a
    source and the ``+Y`` wall for a sink (R-M3-5).  The wall carries no machine
    index and no port name, so what it moves is not in ``per_source`` or
    ``per_sink``: it is the spec's own ``external_inputs`` and ``outputs``, and
    :func:`nets_for` reads it from there.  ``rate`` is what the item moves in
    total either way.

    ``paired`` marks the one-to-one nets a
    :func:`~flab2bp.sfy.spec.direct_pairs` pairing makes: one source, one sink,
    and a rate FactorioLab has already balanced between them.
    """

    id: int
    item_id: str
    sources: tuple[tuple[int, str], ...]
    sinks: tuple[tuple[int, str], ...]
    rate: Fraction
    per_source: tuple[Fraction, ...]
    per_sink: tuple[Fraction, ...]
    paired: bool


@dataclass(frozen=True, slots=True)
class GridNet:
    """One item, everywhere it comes from and everywhere it has to go.

    ``per_source`` and ``per_sink`` are parallel to ``sources`` and ``sinks``,
    and ``rate`` is what the item moves in total.  A wall terminal set is the
    one place where the parallel entries are not a sum: every node of the wall
    line carries the whole rate, because the set is one stream that may enter or
    leave at any of them (R-M3-5).  A terminal says which it is: ``kind`` is
    ``"wall"`` on every node of such a set.
    """

    id: int
    item_id: str
    sources: tuple[Terminal, ...]
    sinks: tuple[Terminal, ...]
    rate: Fraction
    per_sink: tuple[Fraction, ...]
    per_source: tuple[Fraction, ...]


# --- belt tiers ------------------------------------------------------------


def tier_for(rate: Fraction, tiers: Sequence[BeltTier], item_id: str) -> BeltTier:
    """The slowest belt the spec funds that carries ``rate``.

    Moved here from :mod:`flab2bp.sfy.layout.laying` because both strategies ask
    it: the manifold asks once per trunk, and the grid router asks once per
    piece of belt after the tree is known, when what a piece carries is the sum
    of everything downstream of the tap above it (R-M3-7).
    """
    for tier in sorted(tiers, key=lambda tier: tier.items_per_second):
        if tier.items_per_second >= rate:
            return tier
    raise NetError(
        "ceiling",
        f"run exceeds the belt ceiling: a run carries {rate} items/s of {item_id!r}, "
        "past every belt this spec allows",
    )


def belt_class_for(spec: SfyBuildSpec, lab_map: LabMap) -> Callable[[Fraction, str], str]:
    """``(rate, item) -> conveyor class``: the tier, named the way the game does.

    Handed to :func:`flab2bp.sfy.layout.rrr.route_all` so that the routing loop
    reads no spec and no lab map of its own: the tiers are fixed when this is
    built, and the only question left is which of them a rate needs.
    """
    tiers = spec.belt_tiers

    def belt_class(rate: Fraction, item_id: str) -> str:
        return _belt_class(lab_map, tier_for(rate, tiers, item_id))

    return belt_class


def _belt_class(lab_map: LabMap, tier: BeltTier) -> str:
    try:
        return machine_class(lab_map, tier.item_id)
    except KeyError as exc:
        raise NetError("data", f"the lab map has no conveyor class for {tier.item_id!r}") from exc


# --- the nets --------------------------------------------------------------


def net_plan(spec: SfyBuildSpec, registry: Registry) -> tuple[NetPlan, ...]:
    """Every net this spec has to belt, numbered, before anything is placed.

    THE place net ids come from.  The packer needs them before it knows where a
    machine stands -- its objective weights a net by how far its ports are apart
    -- and :func:`nets_for` needs them after; two readers numbering the same
    nets from two pieces of code is exactly how they come to disagree, so they
    read the same list and only one of them adds poses to it.

    **The machine index is the position in the flat machine list**, and the
    order is stated here because both sides build that list: the spec's groups
    in spec order, and within a group its machines in index order, so group
    ``g``'s machine ``i`` is at ``sum(count of every earlier group) + i``.

    The direct pairs come first, one net per machine pair, because they are the
    spec's own statement that the flow is already balanced one to one.  A pair
    replaces ONE item's producer-to-consumer net and nothing else: the groups it
    joins go on eating and making everything else they eat and make, and those
    items get their nets the way any group's do.  ``direct_pairs`` is what makes
    that safe -- it reports a pair only where no other group touches the item
    and the spec neither belts it in nor sends it out -- so the paired ITEM is
    the one thing dropped from the trees, rather than the paired GROUPS.

    An item with nowhere to come from or nowhere to go makes NO net.  A complete
    spec has neither, so this arises only for the hand-built fragments the
    layout stages are tested on, where an item that goes nowhere is nothing to
    route rather than an error.
    """
    first = _first_machine(spec)
    ids = count(1)
    plans: list[NetPlan] = []
    paired: set[str] = set()
    for pair in direct_pairs(spec):
        paired.add(pair.item)
        maker = _group_index(spec, pair.producer)
        eater = _group_index(spec, pair.consumer)
        out_port = _port_name(pair.producer, pair.item, "output", registry)
        in_port = _port_name(pair.consumer, pair.item, "input", registry)
        for index in range(pair.producer.count):
            rate = _share(pair.producer, pair.producer.outputs_per_machine[pair.item], index)
            plans.append(
                NetPlan(
                    id=next(ids),
                    item_id=pair.item,
                    sources=((first[maker] + index, out_port),),
                    sinks=((first[eater] + index, in_port),),
                    rate=rate,
                    per_source=(rate,),
                    per_sink=(rate,),
                    paired=True,
                )
            )
    for item_id in _loose_items(spec, paired):
        plan = _tree_plan(spec, item_id, registry, first, ids)
        if plan is not None:
            plans.append(plan)
    return tuple(plans)


def nets_for(
    spec: SfyBuildSpec,
    machines: Sequence[MachineObj],
    lattice: Lattice,
    registry: Registry,
    lab_map: LabMap,
) -> tuple[GridNet, ...]:
    """:func:`net_plan`'s nets, with the placed machines' ports put on them.

    Which nets there are, what they carry and what they are numbered is the
    plan's; all this adds is where each port really is -- the terminal -- and
    the designer wall, which has no machine index to carry and is read off the
    spec here (R-M3-5).

    ``lab_map`` is read once, to prove the spec's own belt can be named in the
    game before the router spends a budget on a build whose conveyors turn out
    to have no class.
    """
    belt_class_for(spec, lab_map)(spec.belt_items_per_second, spec.belt_item_id)
    placed = _machines_in_spec_order(spec, machines)
    nets: list[GridNet] = []
    for plan in net_plan(spec, registry):
        sources = [_at(placed, port, lattice, registry) for port in plan.sources]
        sinks = [_at(placed, port, lattice, registry) for port in plan.sinks]
        per_source = list(plan.per_source)
        per_sink = list(plan.per_sink)
        # A paired item is never belted in or out -- ``direct_pairs`` reports a
        # pair only where the spec does neither -- so these are zero there.
        inward = _inward(spec, plan.item_id)
        outward = _outward(spec, plan.item_id)
        if inward:
            wall = _wall_terminals(lattice, inward=True)
            sources.extend(wall)
            per_source.extend([inward] * len(wall))
        if outward:
            wall = _wall_terminals(lattice, inward=False)
            sinks.extend(wall)
            per_sink.extend([outward] * len(wall))
        nets.append(
            GridNet(
                id=plan.id,
                item_id=plan.item_id,
                sources=tuple(sources),
                sinks=tuple(sinks),
                rate=plan.rate,
                per_sink=tuple(per_sink),
                per_source=tuple(per_source),
            )
        )
    return tuple(nets)


def _at(
    placed: Sequence[MachineObj], port: tuple[int, str], lattice: Lattice, registry: Registry
) -> Terminal:
    """One of the plan's ``(machine index, port name)`` pairs, where it stands."""
    machine = placed[port[0]]
    return terminal_for(
        machine, _named_port(registry, machine.class_name, port[1]), lattice, registry
    )


def _loose_items(spec: SfyBuildSpec, paired: Collection[str]) -> tuple[str, ...]:
    """Every item a tree may still have to move, in a stable order.

    Everything the groups eat or make and everything the spec belts in or sends
    out, less the items a direct pair already belts one to one.  Sorted, because
    the order nets are routed in is the order they are offered the floor: an
    order that depended on a dict's insertion would make one pack route
    differently from the same pack read back out of a file.
    """
    items: set[str] = set(spec.external_inputs) | set(spec.outputs) | set(spec.surplus_outputs)
    for group in spec.groups:
        items |= set(group.inputs_per_machine) | set(group.outputs_per_machine)
    return tuple(sorted(items - set(paired)))


def _tree_plan(
    spec: SfyBuildSpec,
    item_id: str,
    registry: Registry,
    first: Sequence[int],
    ids: Iterator[int],
) -> NetPlan | None:
    """One item's whole tree, or ``None`` where it has nowhere to come from or go.

    Every group that makes the item is a source and every group that eats it is
    a sink, whether or not one of them is half of a direct pair on some OTHER
    item: a pair is a statement about one item's flow and says nothing about the
    ore its producer eats or the plate its consumer makes.
    """
    sources: list[tuple[int, str]] = []
    sinks: list[tuple[int, str]] = []
    per_source: list[Fraction] = []
    per_sink: list[Fraction] = []
    for index, group in enumerate(spec.groups):
        if item_id in group.outputs_per_machine:
            name = _port_name(group, item_id, "output", registry)
            for machine in range(group.count):
                sources.append((first[index] + machine, name))
                per_source.append(_share(group, group.outputs_per_machine[item_id], machine))
        if item_id in group.inputs_per_machine:
            name = _port_name(group, item_id, "input", registry)
            for machine in range(group.count):
                sinks.append((first[index] + machine, name))
                per_sink.append(_share(group, group.inputs_per_machine[item_id], machine))
    inward, outward = _inward(spec, item_id), _outward(spec, item_id)
    if not (sources or inward) or not (sinks or outward):
        return None
    return NetPlan(
        id=next(ids),
        item_id=item_id,
        sources=tuple(sources),
        sinks=tuple(sinks),
        rate=sum(per_sink, Fraction(0)) + outward,
        per_source=tuple(per_source),
        per_sink=tuple(per_sink),
        paired=False,
    )


def _inward(spec: SfyBuildSpec, item_id: str) -> Fraction:
    """What the spec belts in at the ``-Y`` wall for this item -- R-M3-5."""
    return spec.external_inputs.get(item_id, Fraction(0))


def _outward(spec: SfyBuildSpec, item_id: str) -> Fraction:
    """What has to leave by the ``+Y`` wall: the objective and the surplus."""
    return spec.outputs.get(item_id, Fraction(0)) + spec.surplus_outputs.get(item_id, Fraction(0))


def _group_index(spec: SfyBuildSpec, group: SfyMachineGroup) -> int:
    """Which of the spec's groups this one IS, by identity rather than by value.

    Two groups can be equal models and still be two rows of machines, so the
    comparison is ``is`` and not ``==``.
    """
    for index, other in enumerate(spec.groups):
        if other is group:
            return index
    raise NetError("data", f"{group.recipe_class} is not one of this spec's groups")


def _first_machine(spec: SfyBuildSpec) -> tuple[int, ...]:
    """Where each group's machines start in the flat machine list.

    The list is the spec's groups in spec order with each group's machines in
    index order, which is the order :func:`net_plan` numbers ports in and the
    order the packer builds its machines in.
    """
    out: list[int] = []
    start = 0
    for group in spec.groups:
        out.append(start)
        start += group.count
    return tuple(out)


def _share(group: SfyMachineGroup, per_machine: Fraction, index: int) -> Fraction:
    """What machine ``index`` of ``group`` moves: the last one runs slower.

    The spec states two clocks per group -- every machine's but the last's, and
    the last's -- so the odd machine at the end of a row moves its own share, and
    a net that gave it the group's rate would belt items that are never made.
    """
    if index == group.count - 1:
        return per_machine * group.last_clock / group.clock
    return per_machine


def _machines_in_spec_order(
    spec: SfyBuildSpec, machines: Sequence[MachineObj]
) -> tuple[MachineObj, ...]:
    """The placed machines in the order :func:`net_plan` indexes them.

    Matched on the recipe class and the machine class together, which is what a
    group is in a placement: two groups on the same recipe at different clocks
    are told apart by nothing a :class:`~...model.MachineObj` carries, so they
    are served in placement order.  A placement with fewer machines than the
    spec runs is refused rather than silently short-belted.
    """
    pool: dict[tuple[str, str], list[MachineObj]] = {}
    for machine in machines:
        pool.setdefault((machine.recipe_class, machine.class_name), []).append(machine)
    taken: dict[tuple[str, str], int] = {}
    out: list[MachineObj] = []
    for group in spec.groups:
        key = (group.recipe_class, group.machine_class)
        start = taken.get(key, 0)
        placed = pool.get(key, [])[start : start + group.count]
        if len(placed) != group.count:
            raise NetError(
                "data",
                f"the placement stands {len(placed)} of the {group.count} "
                f"{group.machine_class} the spec runs {group.recipe_class} on",
            )
        taken[key] = start + group.count
        out.extend(placed)
    return tuple(out)


def _port_name(group: SfyMachineGroup, item_id: str, direction: str, registry: Registry) -> str:
    """Which port of this group's machines carries ``item_id``.

    This project's own assignment, and it is stated once, here: the group's own
    items in that direction, in id order, take the machine's belt ports of that
    direction in name order.  Nothing in the game ties an ingredient to a
    connection -- a Manufacturer eats out of whichever of its four inputs the
    belt arrives on -- so any total order will do, and a STATED one is what
    makes two runs of the same build belt the same item to the same port.
    """
    buildable = _buildable(registry, group.machine_class)
    items = sorted(group.outputs_per_machine if direction == "output" else group.inputs_per_machine)
    ports = sorted(
        (port for port in buildable.ports if port.kind == "belt" and port.direction == direction),
        key=lambda port: port.name,
    )
    if len(items) > len(ports):
        raise NetError(
            "ports",
            f"more input items than the machine has belt ports: {group.machine_class} has "
            f"{len(ports)} belt {direction} ports and its recipe moves {len(items)} items",
        )
    return ports[items.index(item_id)].name


def _named_port(registry: Registry, class_name: str, name: str) -> Port:
    for port in _buildable(registry, class_name).ports:
        if port.name == name:
            return port
    raise NetError("data", f"{class_name} has no port called {name!r}")


# --- terminals -------------------------------------------------------------


def terminal_for(machine: MachineObj, port: Port, lattice: Lattice, registry: Registry) -> Terminal:
    """Where one named port of one placed machine meets the lattice.

    The whole of R-M3-1 and R-M3-2 (d) for a machine port, in one place, so that
    the packer, the router and a test all say the same thing about where a belt
    leaves a machine: ``world`` is the pose applied to the port's translation,
    ``facing`` the port's own normal flattened onto the floor, ``node`` the first
    lattice node out along it, and ``reach`` the nodes between the two that lie
    inside the machine's hard box.
    """
    buildable = _buildable(registry, machine.class_name)
    transform = machine.pose.transform()
    world = world_port(transform, port)
    facing = facing_for(port_forward(transform, port), lattice, port.name)
    node = _node_on_ray(world, facing, lattice, port.name)
    low, high = _hard_bounds(buildable, machine)
    return Terminal(
        node=node,
        world=snapped(world, node, lattice),
        facing=facing,
        port=(machine.id, port.name),
        kind="port",
        reach=_reach(node, facing, low, high, lattice),
    )


def _wall_terminals(lattice: Lattice, *, inward: bool) -> tuple[Terminal, ...]:
    """The whole ``-Y`` or ``+Y`` wall line at the port level -- R-M3-5.

    The node is the innermost line a belt may stand on rather than the wall line
    itself: a centreline on the outermost line hangs its own clearance outside
    the designer, which is why :attr:`~...lattice.Lattice.open_lines` stops one
    short of it.  ``world`` is still ON the wall, because that is where the belt
    end has to be for the build to join up to whatever feeds it, and the stub
    between the two is the first piece of the belt beside it rather than a
    conveyor of its own.

    ``facing`` points INTO the designer at both walls, which is the same
    sentence as a machine's: flow leaves a source along ``+facing`` and arrives
    at a sink along ``-facing``, so a ``-Y`` wall feeds the build and a ``+Y``
    wall drains it.
    """
    lines = lattice.open_lines
    j = lines.start if inward else lines.stop - 1
    facing: Vector = (0.0, 1.0, 0.0) if inward else (0.0, -1.0, 0.0)
    half = lattice.designer.half_cm
    out: list[Terminal] = []
    for i in lines:
        node = (i, j, GROUND_LEVEL)
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


def _buildable(registry: Registry, class_name: str) -> Buildable:
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        raise NetError("data", f"the registry does not describe {class_name}")
    return buildable


def _hard_bounds(buildable: Buildable, machine: MachineObj) -> tuple[Vector, Vector]:
    """The world box ``buildable``'s HARD clearance covers where it stands.

    The same reading as :func:`~flab2bp.sfy.layout.manifold.hard_footprint_cm`
    -- soft boxes are the game's own marking for a box that may be shared -- and
    turned by the machine's own pose, so a quarter turn moves the reach with the
    box rather than leaving it pointing the way the asset was authored.
    """
    transform = machine.pose.transform()
    spans = [box_bounds(box, transform) for box in buildable.clearance if not box.soft]
    if not spans:
        raise NetError(
            "data",
            f"{machine.class_name} has no hard clearance box, so this module cannot say "
            "how far a belt runs inside it",
        )
    low = tuple(min(span[0][axis] for span in spans) for axis in range(3))
    high = tuple(max(span[1][axis] for span in spans) for axis in range(3))
    return ((low[0], low[1], low[2]), (high[0], high[1], high[2]))


def _node_on_ray(world: Vector, facing: Vector, lattice: Lattice, name: str) -> Node:
    """The first lattice node out of a port, along the port's own normal -- R-M3-1.

    The axis the port faces along is stepped to the next line beyond it -- or is
    the line the port already stands on -- and the other two must already BE
    lines: a port half a grid step off one has no node a belt could leave it on,
    and rounding to the nearest would lay a belt that leaves its port off the
    normal ``ports.position`` holds it to.
    """
    grid, half = lattice.grid_cm, lattice.designer.half_cm
    origins = (-half, -half, 0.0)
    axis = _facing_axis(facing)
    lines: list[int] = []
    for a in range(3):
        steps = (world[a] - origins[a]) / grid
        line = round(steps)
        on_line = abs(steps - line) * grid <= LATTICE_TOUCH_CM
        if a == axis:
            if not on_line:
                line = math.ceil(steps) if facing[a] > 0.0 else math.floor(steps)
            lines.append(line)
            continue
        if not on_line:
            raise NetError(
                "lattice",
                f"a port is off the hologram lattice: {name} stands at {world} and its "
                f"{'xyz'[a]} is {abs(steps - line) * grid:.1f} cm off the nearest line",
            )
        lines.append(line)
    node = (lines[0], lines[1], lines[2])
    if not lattice.holds(node):
        raise NetError(
            "lattice",
            f"a port is off the hologram lattice: {name} at {world} leaves the designer at {node}",
        )
    return node


def _reach(
    node: Node, facing: Vector, low: Vector, high: Vector, lattice: Lattice
) -> tuple[Node, ...]:
    """The nodes from ``node`` out along ``facing`` that this machine denies.

    R-M3-2 (d): a belt's own path from its port to where its machine stops
    standing in the way lies inside that machine, the occupancy denies every
    node of it to everyone, and these are what one net opens for itself.

    "Where the machine stops standing in the way" is the occupancy's own reading
    and not the box edge, and the difference matters: a node is denied when the
    158 cm belt box on it MEETS the hard box, so a box whose edge does not land
    on a lattice line -- an Assembler turned a quarter turn has its at 50 cm off
    -- denies one node further than it reaches.  Stopping at the edge left that
    node denied and in nobody's reach, which walls the port in altogether; this
    is the same ``_lines_meeting`` arithmetic
    :func:`~flab2bp.sfy.layout.lattice._mark_box` denies it with, read along one
    ray.

    What that costs: where two machines stand a grid step apart, the node in the
    gap is denied by BOTH boxes, and a reach that opens it opens a node the
    other machine also denies.  The packer's port apron is what keeps a port
    from facing into such a gap; a port already outside its own machine's way
    reaches nothing, which is the honest answer and not a special case.
    """
    axis = _facing_axis(facing)
    sign = 1 if facing[axis] > 0.0 else -1
    edge = high[axis] if sign > 0 else low[axis]
    here = lattice.world(node)[axis]
    span = (edge - here) * sign + BELT_CLEARANCE_HALF_WIDTH_CM - TOUCH_CM
    steps = math.ceil(span / lattice.grid_cm) - 1
    out: list[Node] = []
    for step in range(max(steps, -1) + 1):
        moved = list(node)
        moved[axis] += sign * step
        reached = (moved[0], moved[1], moved[2])
        if not lattice.holds(reached):
            break
        out.append(reached)
    return tuple(out)


def snapped(world: Vector, node: Node, lattice: Lattice) -> Vector:
    """``world``, with every axis already on ``node`` moved exactly onto it.

    A port authored at ``x = 0`` arrives out of the cooked asset as
    ``-3.5e-05``, and a quarter turn through a quaternion leaves a few parts in
    ``1e-14`` of the same.  A belt drawn to that noise leaves its port a RIGHT
    ANGLE off the normal -- the piece from the node to the port is 3.5e-05 cm
    long and points sideways -- so a port within :data:`LATTICE_TOUCH_CM` of its
    own node is taken to be AT it, which is what the asset meant.  A port that
    really stands off the grid, like a Manufacturer's inputs at 25 cm, is
    further than that and is left where it is.

    Ours, and stated here rather than in the realiser: where a terminal stands
    is the caller's to say, and this is the one place this package says it.
    """
    here = lattice.world(node)
    out = list(world)
    for axis in range(3):
        if abs(world[axis] - here[axis]) <= LATTICE_TOUCH_CM:
            out[axis] = here[axis]
    return (out[0], out[1], out[2])


def _facing_axis(facing: Vector) -> int:
    """Which ground axis a port faces along, which is the one it reaches out on."""
    return 0 if abs(facing[0]) >= abs(facing[1]) else 1


def facing_for(vector: Vector, lattice: Lattice, name: str) -> Vector:
    """A port's normal as a unit vector along the floor, snapped to its own axis.

    Snapped for the same reason :func:`snapped` moves a port onto its node: a
    Smelter output authored facing ``+Y`` arrives out of the cooked asset as
    ``(3.5e-07, 1.0, 0)``, which is 2e-05 degrees round from the axis -- enough
    for the realiser to refuse the 90 degree top yaw of a lift that turns under
    it, because the build gun's yaw step is 90 degrees exactly.  The tolerance
    is the lattice's own: :data:`LATTICE_TOUCH_CM` over one grid step is how far
    off a line the same noise puts a port a grid step away.
    """
    snapped_vector = snap_zeros(vector, LATTICE_TOUCH_CM / lattice.grid_cm)
    span = math.hypot(snapped_vector[0], snapped_vector[1])
    if span <= _EPS:
        raise NetError(
            "lattice",
            f"a port is off the hologram lattice: {name} faces straight up or down, and a "
            "belt on this lattice leaves a port along the floor",
        )
    return (snapped_vector[0] / span, snapped_vector[1] / span, 0.0)


# --- taps ------------------------------------------------------------------


def tap_nodes(
    tree: Sequence[Sequence[Node]],
    lattice: Lattice,
    *,
    clear: int = TAP_CLEAR_NODES,
    breaks: Collection[Node] = (),
) -> tuple[Node, ...]:
    """Where a committed tree may be cut open for a splitter -- R-M3-7.

    A legal tap is an interior node of a straight run with at least ``clear``
    straight nodes on either side of it AT THE SAME LEVEL.  A corner ends a run,
    and so do a climb and a lift: an incline's via is a node of the path that no
    attachment could stand on, and a splitter on a corner is a turn rather than
    a tap.  ``breaks`` ends a run too, and is how a caller keeps later taps away
    from the attachments earlier ones already stood, and away from the nodes
    inside a machine's box that a terminal's reach opened.

    Nothing here searches: each path is walked once, as a path.
    """
    seen: set[Node] = set()
    out: list[Node] = []
    stop = frozenset(breaks)
    for path in tree:
        for run in _straight_runs(path, stop):
            if len(run) <= 2 * clear:
                continue
            for node in run[clear : len(run) - clear]:
                if node in seen or not lattice.holds(node):
                    continue
                seen.add(node)
                out.append(node)
    return tuple(out)


def _straight_runs(path: Sequence[Node], stop: Collection[Node]) -> Iterator[tuple[Node, ...]]:
    """The path's maximal straight, level pieces.

    A run continues while the step to the next node is one grid step in the same
    direction at the same level.  Anything else ends it: a corner (which belongs
    to the run either side of it, since a belt really does run up to the corner
    and away from it), the flat half of an incline, a lift, and a node in
    ``stop``, which belongs to neither run because something is already standing
    on it.
    """
    run: list[Node] = []
    heading: tuple[int, int] | None = None
    for index, node in enumerate(path):
        if node in stop:
            if len(run) > 1:
                yield tuple(run)
            run, heading = [], None
            continue
        step = _flat_step(node, path[index + 1]) if index + 1 < len(path) else None
        if step is None:
            run.append(node)
            if len(run) > 1:
                yield tuple(run)
            run, heading = [], None
            continue
        if heading is not None and step != heading:
            run.append(node)
            if len(run) > 1:
                yield tuple(run)
            run = [node]
        else:
            run.append(node)
        heading = step
    if len(run) > 1:
        yield tuple(run)


def _flat_step(here: Node, there: Node) -> tuple[int, int] | None:
    """The one grid step from ``here`` to ``there``, or ``None`` if it is not one."""
    step = (there[0] - here[0], there[1] - here[1])
    if here[2] != there[2] or abs(step[0]) + abs(step[1]) != 1:
        return None
    return step
