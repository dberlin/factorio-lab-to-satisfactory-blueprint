"""Power poles for a manifold build, and the wires that put every machine on one.

A row of machines is unpowered until something joins each machine's power
connection to a pole, and the poles to each other.  This module is that stage:
it stands a line of poles along each row and hands back the
:class:`~flab2bp.sfy.layout.model.PoleObj` and
:class:`~flab2bp.sfy.layout.model.WireObj` a placement carries, or refuses with a
named cause.

Where the numbers come from
---------------------------
Everything about a pole and a wire is read out of ``registry.json``:

* which connection a pole offers and where it sits -- ``PowerConnection``'s
  ``translation``, ``(0, 0, 760)`` on a Mk2, and the machine's own ``PowerInput``
  or ``PowerConnection`` likewise, so a wire's length is measured between the two
  points the game would join;
* how many wires one connection takes -- ``Port.max_connections``, 7 on a Mk2 and
  1 on a machine, each with a ``max_connections_source`` saying which game asset
  it was read from;
* how far a wire reaches -- ``limits.wire_max_cm``, which is
  ``AFGBuildableWire::mMaxLength`` out of Docs.json;
* the grid a pole stands on -- :func:`pole_grid_cm`;
* every clearance box the pole line is placed against.

Three things are **ours**, and each says so where it is made:
:data:`POLE_CLASS` and :data:`WIRE_CLASS` (which of the game's classes this
project builds with), :data:`CHAIN_LINKS_PER_POLE` (how many of a pole's links
are held back for the pole-to-pole chain), and where along the row a pole line
stands -- :func:`_pole_band`, which computes the choice from the boxes rather
than assuming a gap.

Legality is the validator's
---------------------------
Nothing here decides what the game accepts.  ``power.wires`` in
:mod:`flab2bp.sfy.layout.validate` is the judge: it measures every wire against
``wire_max_cm``, counts the links on every connection against
``max_connections``, and walks the wire graph to check that every machine reaches
a pole.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from flab2bp.sfy.geometry import box_bounds as geometry_box_bounds
from flab2bp.sfy.geometry import world_port
from flab2bp.sfy.layout.manifold import grid_ceil
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    Vector,
    WireObj,
)
from flab2bp.sfy.registry import ClearanceBox, Port, Registry
from flab2bp.sfy.spec import Designer

__all__ = [
    "CHAIN_LINKS_PER_POLE",
    "POLE_CLASS",
    "WIRE_CLASS",
    "PowerError",
    "PowerPlan",
    "PowerRow",
    "box_bounds",
    "machine_budget",
    "place",
    "pole_grid_cm",
    "power_port",
    "wire_limit_cm",
]

POLE_CLASS = "Build_PowerPoleMk2_C"
"""Which pole this project builds with.  **Ours**: the registry offers three
free-standing marks and a wall socket, and nothing in the game says which one a
build should use.  The Mk2 is chosen because its ``PowerConnection`` takes seven
wires where the Mk1's takes four, so one pole covers five machines instead of
two, and because the fixture corpus carries a template of it (the Mk3's is not in
any fixture, so it cannot be stamped out)."""

WIRE_CLASS = "Build_PowerLine_C"
"""Which wire this project builds with.  **Ours** in the same narrow sense: the
registry gives a ``wire_max_cm`` to exactly two classes, and the other is
``Build_XmassLightsLine_C``, a decoration."""

CHAIN_LINKS_PER_POLE = 2
"""How many of a pole's links are held back for the pole-to-pole chain.

**Ours.**  A pole in the middle of a line spends one link on each neighbour; a
pole at the end of a row spends one inside the row and one on the row beside it.
Two is therefore the most any pole in this build form spends on the chain, and
holding two back on every pole makes the machine budget the same number for all
of them -- five on a Mk2 -- rather than a figure that depends on where in the
line the pole happens to land.
"""

_EPS = 1e-6


class PowerError(ValueError):
    """Power this module will not lay, with the cause the strategy names.

    ``cause`` is a discriminator rather than prose: ``"wire"`` when no pole line
    this module will stand puts a machine inside ``wire_max_cm``, ``"room"``
    when no place in the designer holds a pole whose box meets no hard box, and
    ``"port"`` when a class the build uses has no power connection to wire at
    all.  :mod:`flab2bp.sfy.layout.strategy` turns it into the refusal a caller
    sees, so the refusal strings live in one place.
    """

    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(detail)
        self.cause = cause
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PowerRow:
    """One row as this stage needs it: its machines and its chain attachments.

    The attachments are what the pole line is placed around -- a row's merger
    chain stands between the machine line and the corridor -- and they are read
    for their clearance boxes alone.
    """

    machines: tuple[MachineObj, ...]
    attachments: tuple[AttachmentObj, ...] = ()


@dataclass(frozen=True, slots=True)
class PowerPlan:
    """What this stage adds to a placement, and what it did.

    ``lines`` is one sentence per row for the build's description: where the
    pole line stands and why, since that is a choice of ours rather than a
    number out of the game.
    """

    poles: tuple[PoleObj, ...]
    wires: tuple[WireObj, ...]
    lines: tuple[str, ...]


# --- the numbers a pole stands on ------------------------------------------


def power_port(registry: Registry, class_name: str) -> Port:
    """The one connection this class takes power through.

    ``kind`` is the registry's own classification of a connection component, so
    a machine's ``PowerInput`` and a pole's ``PowerConnection`` are found by what
    they are rather than by what they are called -- the corpus spells the same
    component five different ways.
    """
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        raise PowerError("port", f"the registry has no buildable called {class_name}")
    ports = [port for port in buildable.ports if port.kind == "power"]
    if len(ports) != 1:
        raise PowerError(
            "port",
            f"{class_name} has {len(ports)} power connections in the registry, and this "
            "module wires exactly one",
        )
    return ports[0]


def pole_grid_cm(registry: Registry, class_name: str) -> float:
    """The grid a pole of this class snaps to, in centimetres.

    ``Buildable.grid_snap_cm`` is filled only where the class's own hologram
    overrides ``AFGBuildableHologram::mGridSnapSize``; in the shipped assets that
    is ``Holo_PowerPole_C`` (the Mk1 and the Power Tower) and
    ``Holo_StreetLight_C``, all three at 50.  A class that states none -- and
    :data:`POLE_CLASS` is one, because the extraction found no ``mHologramClass``
    on its class default object -- snaps on the global grid instead, which is
    ``limits.hologram_grid_cm``: 100, read out of the shipped DLL.

    Taking the global grid where the class is silent is the safe reading in both
    directions: 100 is a multiple of 50, so a pole placed on it stands on the
    50 cm grid as well, whatever the Mk2's own hologram turns out to override.
    """
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        raise PowerError("port", f"the registry has no buildable called {class_name}")
    if buildable.grid_snap_cm is not None:
        return buildable.grid_snap_cm
    grid = registry.limits.hologram_grid_cm
    if grid is None:
        raise PowerError("room", "the registry states no hologram grid, so a pole has no grid")
    return grid


def wire_limit_cm(registry: Registry, class_name: str) -> float:
    """How far one wire of this class reaches, from ``limits.wire_max_cm``."""
    limit = registry.limits.wire_max_cm.get(class_name)
    if limit is None:
        raise PowerError("wire", f"the registry gives {class_name} no maximum wire length")
    return limit


def machine_budget(registry: Registry, class_name: str) -> int:
    """How many machines one pole of this class may carry.

    Its connection's ``max_connections`` less :data:`CHAIN_LINKS_PER_POLE`.
    """
    port = power_port(registry, class_name)
    if port.max_connections is None:
        raise PowerError("port", f"{class_name}'s {port.name} has no link count in the registry")
    budget = port.max_connections - CHAIN_LINKS_PER_POLE
    if budget < 1:
        raise PowerError(
            "port",
            f"{class_name}'s {port.name} takes {port.max_connections} wires, which the "
            f"pole chain alone spends",
        )
    return budget


# --- clearance boxes, where they stand -------------------------------------


def box_bounds(box: ClearanceBox, pose: Pose) -> tuple[Vector, Vector]:
    """An axis-aligned ``(min, max)`` around one clearance box on an object.

    :func:`flab2bp.sfy.geometry.box_bounds` does the arithmetic -- the same
    composition the validator places a box with and the row builder measures a
    band with -- and this takes the :class:`~flab2bp.sfy.layout.model.Pose` a
    placed object carries rather than its transform.
    """
    return geometry_box_bounds(box, pose.transform())


def _laps(a: tuple[Vector, Vector], b: tuple[Vector, Vector]) -> bool:
    return all(a[1][i] > b[0][i] + _EPS and a[0][i] < b[1][i] - _EPS for i in range(3))


def _boxes(
    registry: Registry, class_name: str, pose: Pose, *, soft: bool
) -> list[tuple[Vector, Vector]]:
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        return []
    return [box_bounds(box, pose) for box in buildable.clearance if box.soft is soft]


# --- where a pole line stands ----------------------------------------------


def _pole_band(
    row: PowerRow, registry: Registry, grid: float, depth: float, designer: Designer
) -> list[tuple[float, str]]:
    """Every ``Y`` a pole line may stand at in this row, best first.

    **Ours**, and computed rather than assumed.  The brief's first choice is the
    gap between the machine line and the merger chain, so the band from the
    machines' own hard ``+Y`` face to the chain's near face is tried first, and
    only where that band is narrower than the pole's own box does the line go
    past the chain instead.  Which of the two happened is reported, because it
    is a choice and not a measurement.
    """
    line_y = max(machine.pose.y for machine in row.machines)
    machine_face = max(
        high[1]
        for machine in row.machines
        for _, high in _boxes(registry, machine.class_name, machine.pose, soft=False)
    )
    chain = [
        bounds
        for attachment in row.attachments
        if attachment.pose.y > line_y
        for bounds in (
            *_boxes(registry, attachment.class_name, attachment.pose, soft=False),
            *_boxes(registry, attachment.class_name, attachment.pose, soft=True),
        )
    ]
    wall = designer.half_cm - depth / 2.0
    out: list[tuple[float, str]] = []
    if chain:
        near = min(low[1] for low, _ in chain)
        far = max(high[1] for _, high in chain)
        first = grid_ceil(machine_face + depth / 2.0, grid)
        out += [
            (y, "between the machine line and the merger chain")
            for y in _steps(first, min(near - depth / 2.0, wall), grid)
        ]
        out += [
            (y, "beyond the merger chain")
            for y in _steps(grid_ceil(far + depth / 2.0, grid), wall, grid)
        ]
    else:
        out += [
            (y, "past the machine line")
            for y in _steps(grid_ceil(machine_face + depth / 2.0, grid), wall, grid)
        ]
    return out


def _steps(first: float, last: float, grid: float) -> list[float]:
    out = []
    y = first
    while y <= last + _EPS:
        out.append(y)
        y += grid
    return out


def _pole_xs(row: PowerRow, count: int, registry: Registry, grid: float) -> list[float]:
    """Where ``count`` poles stand along the row, one per group of machines.

    **Ours**: the candidates are the midpoints of the machine pitch -- between
    each pair of neighbours, and half a pitch outside each end -- and each group
    of machines takes the free candidate nearest its own centre.  A midpoint is
    the one place along a row that is never inside a machine.
    """
    machines = sorted(row.machines, key=lambda m: m.pose.x)
    xs = [machine.pose.x for machine in machines]
    if len(xs) > 1:
        pitch = min(b - a for a, b in itertools.pairwise(xs))
        mids = [(a + b) / 2.0 for a, b in itertools.pairwise(xs)]
    else:
        low, _ = _boxes(registry, machines[0].class_name, machines[0].pose, soft=False)[0]
        pitch = 2.0 * (xs[0] - low[0])
        mids = []
    candidates = [xs[0] - pitch / 2.0, *mids, xs[-1] + pitch / 2.0]
    taken: set[int] = set()
    out: list[float] = []
    for group in _groups(machines, count):
        centre = (group[0].pose.x + group[-1].pose.x) / 2.0
        pick = min(
            (i for i in range(len(candidates)) if i not in taken),
            key=lambda i: (abs(candidates[i] - centre), candidates[i]),
            default=None,
        )
        if pick is None:
            raise PowerError(
                "room",
                f"a row of {len(machines)} machines has no {count} midpoints to stand poles at",
            )
        taken.add(pick)
        out.append(_grid_round(candidates[pick], grid))
    if len(set(out)) != len(out):
        raise PowerError("room", f"two poles in one row snap to the same x on a {grid:.0f} cm grid")
    return sorted(out)


def _groups(machines: Sequence[MachineObj], count: int) -> list[tuple[MachineObj, ...]]:
    """``count`` runs of neighbouring machines, as even as the count allows."""
    base, extra = divmod(len(machines), count)
    out: list[tuple[MachineObj, ...]] = []
    at = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        out.append(tuple(machines[at : at + size]))
        at += size
    return out


def _grid_round(value: float, grid: float) -> float:
    return round(value / grid) * grid


def _stand(
    row: PowerRow, count: int, registry: Registry, grid: float, designer: Designer
) -> tuple[list[Pose], str]:
    """``count`` pole poses along this row, and which band they stand in."""
    pole = registry.buildables[POLE_CLASS]
    origin = Pose(0.0, 0.0, 0.0, 0.0)
    spans = [box_bounds(box, origin) for box in pole.clearance]
    depth = max((high[1] - low[1] for low, high in spans), default=0.0)
    z = min(machine.pose.z for machine in row.machines)
    xs = _pole_xs(row, count, registry, grid)
    standing: list[MachineObj | AttachmentObj] = [*row.machines, *row.attachments]
    hard = [
        bounds
        for obj in standing
        for bounds in _boxes(registry, obj.class_name, obj.pose, soft=False)
    ]
    for y, where in _pole_band(row, registry, grid, depth, designer):
        poses = [Pose(x, y, z, 0.0) for x in xs]
        if all(
            not _laps(box_bounds(box, pose), other)
            for pose in poses
            for box in pole.clearance
            for other in hard
        ):
            return poses, where
    raise PowerError(
        "room",
        f"no y in this row holds a {POLE_CLASS} whose box meets no hard box",
    )


# --- wiring -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Pole:
    """One pole before it has an id: where it stands, and which row it serves."""

    row: int
    pose: Pose
    where: Vector


def _port_at(pose: Pose, port: Port) -> Vector:
    return world_port(pose.transform(), port)


def _plan_wires(
    rows: Sequence[PowerRow],
    poles: Sequence[_Pole],
    registry: Registry,
    *,
    limit: float,
    capacity: int,
) -> tuple[list[tuple[int, MachineObj]], int | None]:
    """Each machine's pole, or the row whose machine no pole reaches.

    A machine takes the nearest pole that still has a link free, measured
    between the two connection points -- the pole's ``PowerConnection`` on its
    own transform and the machine's power port on its -- which is the distance
    ``power.wires`` will measure.
    """
    left = [capacity] * len(poles)
    chosen: list[tuple[int, MachineObj]] = []
    for index, row in enumerate(rows):
        for machine in row.machines:
            at = _port_at(machine.pose, power_port(registry, machine.class_name))
            order = sorted(
                (i for i in range(len(poles)) if left[i] > 0),
                key=lambda i: (math.dist(at, poles[i].where), i),
            )
            if not order:
                raise PowerError("wire", "the pole line has no link left for a machine")
            pick = order[0]
            if math.dist(at, poles[pick].where) > limit:
                return chosen, index
            left[pick] -= 1
            chosen.append((pick, machine))
    return chosen, None


def place(
    rows: Sequence[PowerRow],
    registry: Registry,
    *,
    ids: Iterator[int],
    designer: Designer,
    pole_class: str = POLE_CLASS,
    wire_class: str = WIRE_CLASS,
) -> PowerPlan:
    """Stand a pole line along every row and wire the build onto it.

    One pole per ``ceil(machines / machine_budget)`` to start with, and one more
    whenever a machine's nearest free pole is past ``wire_max_cm``.  The poles
    are chained along each row and row to row, so the whole build is one circuit
    -- which is what ``power.wires`` walks.
    """
    if not rows or not any(row.machines for row in rows):
        return PowerPlan((), (), ())
    if pole_class != POLE_CLASS:
        raise PowerError("port", f"this module stands {POLE_CLASS}, not {pole_class}")
    grid = pole_grid_cm(registry, pole_class)
    limit = wire_limit_cm(registry, wire_class)
    budget = machine_budget(registry, pole_class)
    pole_port = power_port(registry, pole_class)
    counts = [max(1, -(-len(row.machines) // budget)) for row in rows]
    while True:
        poles: list[_Pole] = []
        wheres: list[str] = []
        for index, row in enumerate(rows):
            poses, where = _stand(row, counts[index], registry, grid, designer)
            wheres.append(where)
            poles += [_Pole(index, pose, _port_at(pose, pole_port)) for pose in poses]
        chosen, short = _plan_wires(rows, poles, registry, limit=limit, capacity=budget)
        if short is None:
            break
        counts[short] += 1
        if counts[short] > len(rows[short].machines):
            raise PowerError(
                "wire",
                f"row {short} cannot be reached by a {wire_class}: even one pole per machine "
                f"leaves a machine further than {limit:.0f} cm from its pole",
            )
    placed = [PoleObj(next(ids), pole_class, pole.pose) for pole in poles]
    wires = _chain(poles, placed, ids=ids, wire_class=wire_class, limit=limit, port=pole_port)
    classes = {pole.id: pole.class_name for pole in placed}
    for pick, machine in chosen:
        classes[machine.id] = machine.class_name
        wires.append(
            WireObj(
                next(ids),
                wire_class,
                Link(
                    (placed[pick].id, pole_port.name),
                    (machine.id, power_port(registry, machine.class_name).name),
                ),
            )
        )
    _hold_to_the_link_count(wires, registry, classes)
    lines = tuple(
        f"row {index}: {counts[index]} x {pole_class} at y="
        f"{next(p.pose.y for p in poles if p.row == index):.0f}, {where}"
        for index, where in enumerate(wheres)
    )
    return PowerPlan(tuple(placed), tuple(wires), lines)


def _chain(
    poles: Sequence[_Pole],
    placed: Sequence[PoleObj],
    *,
    ids: Iterator[int],
    wire_class: str,
    limit: float,
    port: Port,
) -> list[WireObj]:
    """Pole to pole along each row, and the last of one row to the first of the next.

    The row-to-row wire is what makes the build ONE circuit rather than one per
    row, which is what ``power.wires``' walk asks for.  A wire past the limit is
    refused rather than shortened with an extra pole in the corridor margin: no
    two points inside the largest Blueprint Designer are 6800 cm apart and a
    power line reaches 10000, so a chain wire that is too long means the numbers
    have moved, not that the build needs a relay.
    """
    out: list[WireObj] = []
    by_row: dict[int, list[int]] = {}
    for index, pole in enumerate(poles):
        by_row.setdefault(pole.row, []).append(index)
    ends: list[tuple[int, int]] = []
    for row in sorted(by_row):
        line = sorted(by_row[row], key=lambda i: poles[i].pose.x)
        ends.append((line[0], line[-1]))
        out += [
            _wire(poles, placed, a, b, ids, wire_class, limit, port)
            for a, b in itertools.pairwise(line)
        ]
    for (_, last), (first, _) in itertools.pairwise(ends):
        out.append(_wire(poles, placed, last, first, ids, wire_class, limit, port))
    return out


def _wire(
    poles: Sequence[_Pole],
    placed: Sequence[PoleObj],
    a: int,
    b: int,
    ids: Iterator[int],
    wire_class: str,
    limit: float,
    port: Port,
) -> WireObj:
    span = math.dist(poles[a].where, poles[b].where)
    if span > limit:
        raise PowerError(
            "wire",
            f"the pole chain wants a wire of {span:.0f} cm and a {wire_class} reaches "
            f"{limit:.0f} cm",
        )
    return WireObj(
        next(ids), wire_class, Link((placed[a].id, port.name), (placed[b].id, port.name))
    )


def _hold_to_the_link_count(
    wires: Sequence[WireObj], registry: Registry, classes: dict[int, str]
) -> None:
    """Refuse a connection this module has overloaded, before the validator sees it.

    ``power.wires`` would report the same thing; refusing here means the module
    never hands back a placement it already knows the validator will fail.
    """
    counts: dict[tuple[int, str], int] = {}
    for wire in wires:
        for side in (wire.link.a, wire.link.b):
            counts[side] = counts.get(side, 0) + 1
    for side, count in counts.items():
        allowed = power_port(registry, classes[side[0]]).max_connections
        if allowed is not None and count > allowed:
            raise PowerError(
                "port",
                f"object {side[0]}'s {side[1]} would carry {count} wires, past the {allowed} "
                "the registry says it takes",
            )
