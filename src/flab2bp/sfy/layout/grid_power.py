"""Power poles on the free nodes a grid-routed build's router left behind.

A manifold build has rows, and :func:`flab2bp.sfy.layout.power.place` stands a
pole LINE along each one.  A grid-routed build has no rows: its machines are
packed on the build gun's own 1 m hologram grid and its belts are found by a
geometric interval router over :class:`~flab2bp.sfy.layout.lattice.Lattice`.
What is left over after routing is exactly what this module needs -- the nodes
:meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` still answers ``True`` for --
so a pole here stands on a NODE rather than in a band, and the same
:class:`~flab2bp.sfy.layout.power.PowerPlan` comes back.

Where the numbers come from
---------------------------
All of them out of ``registry.json``, through
:mod:`flab2bp.sfy.layout.power`'s own readers, so there is one statement of each:

* the pole's connection and where it sits --
  :func:`~flab2bp.sfy.layout.power.power_port`, ``PowerConnection`` at
  ``(0, 0, 760)`` on a Mk2;
* how many wires that connection takes -- ``Port.max_connections``, 7 on a Mk2,
  and a machine's own, which is 1;
* how far a wire reaches -- :func:`~flab2bp.sfy.layout.power.wire_limit_cm`,
  ``AFGBuildableWire::mMaxLength``;
* the grid a pole snaps to -- :func:`~flab2bp.sfy.layout.power.pole_grid_cm`,
  50 cm off ``Holo_PowerPole_C``;
* which levels a pole occupies -- READ off its own clearance box's height, by
  the same :func:`~flab2bp.sfy.layout.lattice.belt_levels` the flattening uses.

Three things are **ours**, and each says so where it is made: :data:`SLAB_LEVEL`
(that a machine, and so a pole beside it, stands on the slab's top),
:func:`_free_nodes` (that a node a belt may not pass is a node a pole may not
stand on, which is stricter than the game -- a pole's box is ``CT_Soft`` and a
belt may run through it), and the greedy set cover in
:func:`place_on_free_nodes` (which node to take, and how many of a pole's links
to hold back for the chain).

Legality is the validator's
---------------------------
Nothing here decides what the game accepts.  ``power.wires`` in
:mod:`flab2bp.sfy.layout.validate` measures every wire, counts every
connection's links and walks the wire graph to a pole; ``geom.hard_clearance``
judges whether a pole's box shares space it may not.  This module refuses
early, with a cause, so that a placement it already knows those checks would
fail never leaves it.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence

from flab2bp.sfy.geometry import world_port
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node, Occupancy, belt_levels
from flab2bp.sfy.layout.model import (
    Link,
    MachineObj,
    PipeAttachmentObj,
    PoleObj,
    Pose,
    Vector,
    WireObj,
)
from flab2bp.sfy.layout.power import (
    POLE_CLASS,
    WIRE_CLASS,
    Pole,
    PowerError,
    PowerPlan,
    box_bounds,
    hold_to_the_link_count,
    pole_grid_cm,
    power_port,
    wire,
    wire_limit_cm,
)
from flab2bp.sfy.layout.validate import TOUCH_CM
from flab2bp.sfy.registry import Port, Registry
from flab2bp.sfy.spec import Designer

#: ``PowerPlan`` is re-exported because it is what :func:`place_on_free_nodes`
#: hands back: a caller should not have to reach past this module into the
#: manifold's to name the type of the thing it was given.
__all__ = ["SLAB_LEVEL", "PowerPlan", "place_on_free_nodes"]

SLAB_LEVEL = 1
"""Which lattice level a pole's foot stands on.

**Ours**, in the same sense :data:`~flab2bp.sfy.layout.lattice.GROUND_LEVEL` is:
the slab is one grid step thick, so its top is level 1 and everything standing
on the floor -- a machine, and a pole beside it -- has its origin there.  The
centimetres come from the lattice (``grid_cm * SLAB_LEVEL``), so a registry that
ever states a different hologram grid moves the poles with it.
"""

#: Where the designer's centre is.  Its floor is ``[-half_cm, +half_cm]`` on
#: both axes -- :func:`flab2bp.sfy.spec.designer` builds it that way -- so the
#: centre a tie is broken towards is the world origin.
_CENTRE: tuple[float, float] = (0.0, 0.0)


def place_on_free_nodes(
    machines: Sequence[MachineObj | PipeAttachmentObj],
    occupancy: Occupancy,
    registry: Registry,
    *,
    ids: Iterator[int],
    designer: Designer,
    pole_class: str = POLE_CLASS,
    wire_class: str = WIRE_CLASS,
) -> PowerPlan:
    """Stand poles on free nodes until every machine is on one, or refuse.

    A greedy set cover, and **ours**: take the free node whose pole would reach
    the most still-unpowered machine connections inside ``wire_max_cm`` -- ties
    to the node nearest the designer's centre, then to the lowest flat index, so
    the choice never depends on how a set iterates -- wire the nearest of those
    machines to it, and go again with what is left.

    Each pole holds ONE of its links back for the pole-to-pole chain, so the
    next pole always has somewhere to chain into; the pole that finishes the
    build holds nothing back, because there is no next pole to make room for.
    That is why a Mk2 carries six machines here and five in a manifold row: a
    row's pole has a neighbour on each side, and this chain is a single path.

    Refuses with ``"room"`` when a machine is left that no free node reaches --
    the node it would want is the one inside the machine -- and with ``"port"``
    when a machine ends up on no wire at all.  The second is a backstop rather
    than a refusal: it cannot fire while the loop above it runs to completion,
    and it is here so that a placement this module hands back has never passed
    over a machine in silence.
    """
    if not machines:
        return PowerPlan((), (), ())
    lattice = occupancy.lattice
    limit = wire_limit_cm(registry, wire_class)
    pole_port = power_port(registry, pole_class)
    links_each = _link_count(registry, pole_class, pole_port)
    stand_z = lattice.world((0, 0, SLAB_LEVEL))[2]
    levels = _pole_levels(registry, pole_class, stand_z, lattice)
    nodes = _free_nodes(occupancy, levels, pole_grid_cm(registry, pole_class))

    unpowered = [
        (machine, world_port(machine.pose.transform(), power_port(registry, machine.class_name)))
        for machine in machines
    ]
    poles: list[Pole] = []
    placed: list[PoleObj] = []
    stood: list[Node] = []
    links: list[int] = []
    wires: list[WireObj] = []
    lines: list[str] = []
    taken: set[Node] = set()

    while unpowered:
        found = _best_node(lattice, nodes, taken, unpowered, stand_z, pole_port, limit)
        if found is None:
            stranded = unpowered[0][0]
            raise PowerError(
                "room",
                f"no free node in the {designer.mark} designer stands a {pole_class} within "
                f"the {limit:.0f} cm a {wire_class} reaches of {stranded.class_name} "
                f"{stranded.id}",
            )
        node, reach = found
        here = len(poles)
        pose = _pose(lattice, node, stand_z)
        poles.append(Pole(0, pose, world_port(pose.transform(), pole_port)))
        placed.append(PoleObj(next(ids), pole_class, pose))
        stood.append(node)
        links.append(0)
        taken.add(node)

        partner = _chain_to(poles, links, here, links_each, limit)
        if here and partner is None:
            raise PowerError(
                "wire",
                f"the pole on node {node} is further than {limit:.0f} cm from every pole "
                f"with a link to spare, so this build would be two circuits",
            )
        if partner is not None:
            wires.append(wire(poles, placed, partner, here, ids, wire_class, limit, pole_port))
            links[partner] += 1
            links[here] += 1

        budget = _machine_budget(links_each, links[here], reach, unpowered, pole_class, pole_port)
        carried = sorted(
            reach, key=lambda pair: (math.dist(poles[here].where, pair[1]), pair[0].id)
        )[:budget]
        for machine, _ in carried:
            wires.append(
                WireObj(
                    next(ids),
                    wire_class,
                    Link(
                        (placed[here].id, pole_port.name),
                        (machine.id, power_port(registry, machine.class_name).name),
                    ),
                )
            )
        # Every machine wire is a link SPENT on this pole's one connection, so
        # the next pole's chain looks for a neighbour that still has one left.
        # Leaving this out is how a pole ends up carrying eight wires on a
        # connection the asset gives seven.
        links[here] += len(carried)
        lines.append(_line(pole_class, node, pose, len(carried), stood, partner))
        served = {machine.id for machine, _ in carried}
        unpowered = [pair for pair in unpowered if pair[0].id not in served]

    classes: dict[int, str] = {pole.id: pole.class_name for pole in placed}
    classes.update({machine.id: machine.class_name for machine in machines})
    hold_to_the_link_count(wires, registry, classes)
    _hold_every_machine(machines, wires)
    return PowerPlan(tuple(placed), tuple(wires), tuple(lines))


# --- the numbers a pole on a node stands on ---------------------------------


def _link_count(registry: Registry, pole_class: str, port: Port) -> int:
    """How many wires this pole's connection takes, out of the cooked asset."""
    if port.max_connections is None:
        raise PowerError("data", f"{pole_class}'s {port.name} has no link count in the registry")
    if port.max_connections < 1:
        raise PowerError("port", f"{pole_class}'s {port.name} takes no wires at all")
    return port.max_connections


def _pole_levels(
    registry: Registry, pole_class: str, stand_z: float, lattice: Lattice
) -> tuple[int, ...]:
    """Every lattice level a pole standing on the slab occupies.

    Read off the pole's own clearance boxes -- a Mk2's is ``min (-40, -40, -320)
    max (40, 40, 420)`` at translation ``(0, 0, 400)``, so standing on the slab
    it reaches from 180 to 920 cm -- and turned into levels by the same
    :func:`~flab2bp.sfy.layout.lattice.belt_levels` the flattening uses, so what
    this asks of ``free`` is the question that array was written to answer.  The
    node the pole's own foot is on is always among them, even for a class whose
    box somehow spans nothing.
    """
    buildable = registry.buildables.get(pole_class)
    if buildable is None:
        raise PowerError("data", f"the registry has no buildable called {pole_class}")
    origin = Pose(0.0, 0.0, stand_z, 0.0)
    spanned = {GROUND_LEVEL}
    for box in buildable.clearance:
        low, high = box_bounds(box, origin)
        spanned.update(belt_levels(low[2], high[2], lattice.grid_cm))
    levels = tuple(sorted(spanned & set(lattice.open_levels)))
    if not levels:
        raise PowerError(
            "room",
            f"a {pole_class} on the slab spans no level the {lattice.designer.mark} "
            "designer leaves open",
        )
    return levels


def _free_nodes(occupancy: Occupancy, levels: tuple[int, ...], grid: float) -> list[Node]:
    """Every node a pole may stand on, nearest the designer's centre first.

    **Ours, and stricter than the game.**  A node is offered only where
    :meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` says a belt centreline
    may pass it at EVERY level the pole occupies.  That is stricter twice over:
    a free node has nothing hard within 79 cm where the pole's own box reaches
    40, and the pole's box is ``CT_Soft``, so the game would let a belt run
    through it.  The trade is deliberate -- it costs a couple of aisle nodes,
    and in exchange a pole can never be the reason a committed belt has to move.

    The order is the tie-break, stated once here rather than at each use:
    nearest :data:`_CENTRE`, then lowest flat index.  The distance is compared
    SQUARED, so two nodes the same way out compare exactly equal rather than by
    however ``sqrt`` rounded each of them.
    """
    lattice = occupancy.lattice
    foot = levels[0]
    out: list[Node] = []
    for i in lattice.open_lines:
        for j in lattice.open_lines:
            node = (i, j, foot)
            x, y, _ = lattice.world(node)
            if not _snaps(x, grid) or not _snaps(y, grid):
                continue
            if all(occupancy.free((i, j, level)) for level in levels):
                out.append(node)
    out.sort(key=lambda node: (_from_centre(lattice, node), lattice.index(node)))
    return out


def _from_centre(lattice: Lattice, node: Node) -> float:
    where = lattice.world(node)
    return (where[0] - _CENTRE[0]) ** 2 + (where[1] - _CENTRE[1]) ** 2


def _snaps(value: float, grid: float) -> bool:
    """Whether a pole standing at ``value`` would be on its own hologram grid.

    The grid the lattice steps by is 100 and a pole's own is 50, so every node
    satisfies this today; it is asked rather than assumed because both numbers
    are read out of the registry and either could move.
    """
    return abs(value - round(value / grid) * grid) <= TOUCH_CM


def _pose(lattice: Lattice, node: Node, stand_z: float) -> Pose:
    """Where a pole on ``node`` stands: the node's own x and y, on the slab.

    Yaw is zero because a pole's connection is on its axis and its box is square
    about it, so no yaw changes anything this module or the validator measures.
    """
    x, y, _ = lattice.world(node)
    return Pose(x, y, stand_z, 0.0)


# --- the greedy -------------------------------------------------------------


def _best_node(
    lattice: Lattice,
    nodes: Sequence[Node],
    taken: set[Node],
    unpowered: Sequence[tuple[MachineObj | PipeAttachmentObj, Vector]],
    stand_z: float,
    port: Port,
    limit: float,
) -> tuple[Node, list[tuple[MachineObj | PipeAttachmentObj, Vector]]] | None:
    """The free node whose pole reaches the most of ``unpowered``, or ``None``.

    ``nodes`` already carries the tie-break order, so the FIRST node with the
    best count wins and nothing here restates the tie.  A node that reaches all
    of them cannot be beaten, so the walk stops at one -- which is the usual
    case, since a power line reaches 10000 cm and no two points in the largest
    designer are 6800 apart.
    """
    best: tuple[Node, list[tuple[MachineObj | PipeAttachmentObj, Vector]]] | None = None
    for node in nodes:
        if node in taken:
            continue
        where = world_port(_pose(lattice, node, stand_z).transform(), port)
        reach = [pair for pair in unpowered if math.dist(where, pair[1]) <= limit]
        if not reach:
            continue
        if best is None or len(reach) > len(best[1]):
            best = (node, reach)
        if len(reach) == len(unpowered):
            break
    return best


def _machine_budget(
    links_each: int,
    spent: int,
    reach: Sequence[tuple[MachineObj | PipeAttachmentObj, Vector]],
    unpowered: Sequence[tuple[MachineObj | PipeAttachmentObj, Vector]],
    pole_class: str,
    port: Port,
) -> int:
    """How many machines this pole may carry: ``max_connections - 1``, or all of
    them where this is the pole that finishes the build.

    **Ours**, and the reason the chain always closes: every pole but the last
    keeps one link free, so the pole placed after it always has a neighbour with
    room to chain into.  A pole that can reach every machine still unpowered and
    has the links for all of them is the last one, and spends its spare on a
    machine instead.
    """
    room = links_each - spent
    finishes = len(reach) == len(unpowered) and len(unpowered) <= room
    budget = min(links_each - 1, room if finishes else room - 1)
    if budget < 1:
        raise PowerError(
            "port",
            f"{pole_class}'s {port.name} takes {links_each} wires, which the pole chain "
            "alone spends",
        )
    return budget


def _chain_to(
    poles: Sequence[Pole], links: Sequence[int], here: int, links_each: int, limit: float
) -> int | None:
    """Which earlier pole this one chains into: the nearest with a link to spare.

    ``None`` for the first pole, which has nothing to chain into, and ``None``
    for a later one no earlier pole reaches; the caller tells the two apart by
    the index and refuses the second.
    """
    reachable = [
        index
        for index in range(here)
        if links[index] < links_each and math.dist(poles[index].where, poles[here].where) <= limit
    ]
    if not reachable:
        return None
    return min(
        reachable, key=lambda index: (math.dist(poles[index].where, poles[here].where), index)
    )


def _line(
    pole_class: str,
    node: Node,
    pose: Pose,
    carried: int,
    stood: Sequence[Node],
    partner: int | None,
) -> str:
    """One sentence for the build's description: where this pole stands, and why."""
    joined = (
        "the first of the chain"
        if partner is None
        else f"chained to the pole on node {stood[partner]}"
    )
    return (
        f"{pole_class} on free node {node} at ({pose.x:.0f}, {pose.y:.0f}), carrying "
        f"{carried} machines, {joined}"
    )


def _hold_every_machine(
    machines: Sequence[MachineObj | PipeAttachmentObj], wires: Sequence[WireObj]
) -> None:
    """Refuse a machine this module left on no wire, before the validator sees it."""
    wired = {side[0] for one in wires for side in (one.link.a, one.link.b)}
    missing = [machine for machine in machines if machine.id not in wired]
    if missing:
        raise PowerError(
            "port",
            f"{missing[0].class_name} {missing[0].id} is on no wire, so nothing joins it to a pole",
        )
