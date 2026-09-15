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
a property of the rates, read off the spec, and not a layout decision.

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
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import count

from flab2bp.sfy.geometry import box_bounds, port_forward, world_port
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node
from flab2bp.sfy.layout.model import MachineObj, Vector
from flab2bp.sfy.layout.realise import Terminal
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, direct_pairs
from flab2bp.spec import BeltTier

__all__ = [
    "TAP_CLEAR_NODES",
    "GridNet",
    "NetError",
    "belt_class_for",
    "nets_for",
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

TAP_CLEAR_NODES = 2
"""How many straight nodes R-M3-7 keeps on either side of a tap.

The floor rather than the answer: a caller that knows what a turn costs where it
stands passes its own, and :mod:`flab2bp.sfy.layout.rrr` does.  Ours, and the
reason is R-M3-4: the piece of belt between a splitter's port and whatever is
next is at least two nodes long, so a tap with fewer than two straight nodes
behind it is a tap whose own trunk cannot be cut legally.
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
class GridNet:
    """One item, everywhere it comes from and everywhere it has to go.

    ``per_source`` and ``per_sink`` are parallel to ``sources`` and ``sinks``,
    and ``rate`` is what the item moves in total.  A wall terminal set is the
    one place where the parallel entries are not a sum: every node of the wall
    line carries the whole rate, because the set is one stream that may enter or
    leave at any of them (R-M3-5), which is what :attr:`wall_sourced` and
    :attr:`wall_sunk` are for.
    """

    id: int
    item_id: str
    sources: tuple[Terminal, ...]
    sinks: tuple[Terminal, ...]
    rate: Fraction
    per_sink: tuple[Fraction, ...]
    per_source: tuple[Fraction, ...]

    @property
    def wall_sourced(self) -> bool:
        """Whether the sources are one wall line rather than N separate ports."""
        return bool(self.sources) and self.sources[0].kind == "wall"

    @property
    def wall_sunk(self) -> bool:
        """Whether the sinks are one wall line rather than N separate ports."""
        return bool(self.sinks) and self.sinks[0].kind == "wall"


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


def nets_for(
    spec: SfyBuildSpec,
    machines: Sequence[MachineObj],
    lattice: Lattice,
    registry: Registry,
    lab_map: LabMap,
) -> tuple[GridNet, ...]:
    """Every belt tree this build has to lay, in the order they are numbered.

    The direct pairs come first, one net per machine pair, because they are the
    spec's own statement that the flow is already balanced one to one; then one
    net per remaining item, with every producer's port as a source, every
    consumer's port as a sink, and the designer wall standing in for whatever
    the spec belts in or sends out (R-M3-5).

    An item with no source or no sink makes NO net.  A complete spec has neither
    -- ``_no_dangling_demand`` and the surplus outputs see to that -- so this
    arises only for the hand-built fragments the layout stages are tested on,
    where an item that goes nowhere is nothing to route rather than an error.

    ``lab_map`` is read once, here, to prove the spec's own belt can be named in
    the game before the router spends a budget on a build whose conveyors turn
    out to have no class.
    """
    belt_class_for(spec, lab_map)(spec.belt_items_per_second, spec.belt_item_id)
    by_group = _machines_by_group(spec, machines)
    ids = count(1)
    nets: list[GridNet] = []
    paired: set[int] = set()
    for pair in direct_pairs(spec):
        paired |= {id(pair.producer), id(pair.consumer)}
        makers = by_group[id(pair.producer)]
        eaters = by_group[id(pair.consumer)]
        outputs = tuple(sorted(pair.producer.outputs_per_machine))
        inputs = tuple(sorted(pair.consumer.inputs_per_machine))
        for index, (maker, eater) in enumerate(zip(makers, eaters, strict=True)):
            rate = _share(pair.producer, pair.producer.outputs_per_machine[pair.item], index)
            nets.append(
                GridNet(
                    id=next(ids),
                    item_id=pair.item,
                    sources=(_terminal(maker, pair.item, outputs, "output", lattice, registry),),
                    sinks=(_terminal(eater, pair.item, inputs, "input", lattice, registry),),
                    rate=rate,
                    per_sink=(rate,),
                    per_source=(rate,),
                )
            )
    for item_id in _loose_items(spec, paired):
        net = _tree_net(spec, item_id, by_group, paired, lattice, registry, next(ids))
        if net is not None:
            nets.append(net)
    return tuple(nets)


def _loose_items(spec: SfyBuildSpec, paired: Collection[int]) -> tuple[str, ...]:
    """Every item a net may still have to move, in a stable order.

    Sorted, because the order nets are routed in is the order they are offered
    the floor: an order that depended on a dict's insertion would make one pack
    route differently from the same pack read back out of a file.
    """
    items: set[str] = set(spec.external_inputs) | set(spec.outputs) | set(spec.surplus_outputs)
    for group in spec.groups:
        if id(group) in paired:
            continue
        items |= set(group.inputs_per_machine) | set(group.outputs_per_machine)
    return tuple(sorted(items))


def _tree_net(
    spec: SfyBuildSpec,
    item_id: str,
    by_group: Mapping[int, Sequence[MachineObj]],
    paired: Collection[int],
    lattice: Lattice,
    registry: Registry,
    net_id: int,
) -> GridNet | None:
    """One item's whole tree, or ``None`` where it has nowhere to come from or go."""
    sources: list[Terminal] = []
    sinks: list[Terminal] = []
    per_source: list[Fraction] = []
    per_sink: list[Fraction] = []
    for group in spec.groups:
        if id(group) in paired:
            continue
        if item_id in group.outputs_per_machine:
            names = tuple(sorted(group.outputs_per_machine))
            for index, machine in enumerate(by_group[id(group)]):
                sources.append(_terminal(machine, item_id, names, "output", lattice, registry))
                per_source.append(_share(group, group.outputs_per_machine[item_id], index))
        if item_id in group.inputs_per_machine:
            names = tuple(sorted(group.inputs_per_machine))
            for index, machine in enumerate(by_group[id(group)]):
                sinks.append(_terminal(machine, item_id, names, "input", lattice, registry))
                per_sink.append(_share(group, group.inputs_per_machine[item_id], index))
    drawn = sum(per_sink, Fraction(0))
    outward = spec.outputs.get(item_id, Fraction(0)) + spec.surplus_outputs.get(
        item_id, Fraction(0)
    )
    inward = spec.external_inputs.get(item_id, Fraction(0))
    if inward:
        wall = _wall_terminals(lattice, inward=True)
        sources.extend(wall)
        per_source.extend([inward] * len(wall))
    if outward:
        wall = _wall_terminals(lattice, inward=False)
        sinks.extend(wall)
        per_sink.extend([outward] * len(wall))
    if not sources or not sinks:
        return None
    return GridNet(
        id=net_id,
        item_id=item_id,
        sources=tuple(sources),
        sinks=tuple(sinks),
        rate=drawn + outward,
        per_sink=tuple(per_sink),
        per_source=tuple(per_source),
    )


def _share(group: SfyMachineGroup, per_machine: Fraction, index: int) -> Fraction:
    """What machine ``index`` of ``group`` moves: the last one runs slower.

    The spec states two clocks per group -- every machine's but the last's, and
    the last's -- so the odd machine at the end of a row moves its own share, and
    a net that gave it the group's rate would belt items that are never made.
    """
    if index == group.count - 1:
        return per_machine * group.last_clock / group.clock
    return per_machine


def _machines_by_group(
    spec: SfyBuildSpec, machines: Sequence[MachineObj]
) -> dict[int, tuple[MachineObj, ...]]:
    """Which placed machines are which group's, in the order they were placed.

    Matched on the recipe class and the machine class together, which is what a
    group is in a placement: two groups on the same recipe at different clocks
    are told apart by nothing a :class:`~...model.MachineObj` carries, so they
    are served in placement order.  A placement with fewer machines than the
    spec runs is refused rather than silently short-belted.

    Keyed by ``id(group)`` rather than by the group: two groups can be equal
    models and still be two rows of machines.
    """
    pool: dict[tuple[str, str], list[MachineObj]] = {}
    for machine in machines:
        pool.setdefault((machine.recipe_class, machine.class_name), []).append(machine)
    taken: dict[tuple[str, str], int] = {}
    out: dict[int, tuple[MachineObj, ...]] = {}
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
        out[id(group)] = tuple(placed)
    return out


# --- terminals -------------------------------------------------------------


def _terminal(
    machine: MachineObj,
    item_id: str,
    items: Sequence[str],
    direction: str,
    lattice: Lattice,
    registry: Registry,
) -> Terminal:
    """The terminal for the port of ``machine`` that carries ``item_id``.

    Which port that is is this project's own assignment, and it is stated once,
    here: ``items`` -- the recipe's own items in that direction, in id order --
    take the machine's belt ports of that direction in name order.  Nothing in
    the game ties an ingredient to a connection (a Manufacturer eats out of
    whichever of its four inputs the belt arrives on), so any total order will
    do and a STATED one is what makes two runs of the same build belt the same
    item to the same port.
    """
    buildable = _buildable(registry, machine.class_name)
    ports = sorted(
        (port for port in buildable.ports if port.kind == "belt" and port.direction == direction),
        key=lambda port: port.name,
    )
    if len(items) > len(ports):
        raise NetError(
            "ports",
            f"more input items than the machine has belt ports: {machine.class_name} has "
            f"{len(ports)} belt {direction} ports and its recipe moves {len(items)} items",
        )
    return terminal_for(machine, ports[items.index(item_id)], lattice, registry)


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
    facing = _flat(port_forward(transform, port), port.name)
    node = _node_on_ray(world, facing, lattice, port.name)
    low, high = _hard_bounds(buildable, machine)
    return Terminal(
        node=node,
        world=world,
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
    """The nodes from ``node`` to the hard box's edge along ``facing`` -- R-M3-2 (d).

    They lie inside the machine's own box, which denies them to everyone, and
    they are what one net opens for itself.  The count is the box's, in whole
    grid steps: a port already outside its own box reaches nothing, which is the
    honest answer rather than a special case.
    """
    axis = _facing_axis(facing)
    sign = 1 if facing[axis] > 0.0 else -1
    edge = high[axis] if sign > 0 else low[axis]
    grid = lattice.grid_cm
    steps = math.floor(((edge - lattice.world(node)[axis]) * sign + LATTICE_TOUCH_CM) / grid)
    out: list[Node] = []
    for step in range(max(steps, -1) + 1):
        moved = list(node)
        moved[axis] += sign * step
        reached = (moved[0], moved[1], moved[2])
        if not lattice.holds(reached):
            break
        out.append(reached)
    return tuple(out)


def _facing_axis(facing: Vector) -> int:
    """Which ground axis a port faces along, which is the one it reaches out on."""
    return 0 if abs(facing[0]) >= abs(facing[1]) else 1


def _flat(vector: Vector, name: str) -> Vector:
    span = math.hypot(vector[0], vector[1])
    if span <= _EPS:
        raise NetError(
            "lattice",
            f"a port is off the hologram lattice: {name} faces straight up or down, and a "
            "belt on this lattice leaves a port along the floor",
        )
    return (vector[0] / span, vector[1] / span, 0.0)


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
