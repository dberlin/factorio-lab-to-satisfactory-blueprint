"""One manifold row: a line of identical machines, fed and drained by conveyor chains.

A row is the unit Task 8 composes a build out of.  ``count`` machines of one
class stand in a line along ``X``; each item the recipe eats gets a *chain* --
one conveyor splitter per machine, wired end to end along ``X``, with a feeder
belt from each splitter's side port into the machine's input port -- and the
output port of every machine feeds a merger chain that leaves the row by one
belt.

**Every distance is the game's.**  The machine pitch is the machine's own hard
clearance boxes plus one hologram grid step; the chains stand where the
registry's ports are; how steep a feeder may be is ``limits.belt_max_incline_deg``
and how short a belt may be is ``limits.belt_min_length_cm``.  Four numbers are
this project's own and each says so where it is computed:

* :func:`crossing_gap_cm`, the height one chain stands above the next, which is
  twice the ``belt.clearance`` box's own height so that two crossing belts have
  a full belt box of air between their centrelines;
* :data:`LEAD_IN_MULTIPLE`, the flat piece a feeder leaves its splitter by
  before it starts to descend, because :mod:`flab2bp.sfy.layout.validate`'s
  ``ports.position`` holds this project to belts that leave a port along the
  port's own facing, and a port's facing is horizontal;
* the rule that a chain clears the whole machine LINE rather than the one
  machine beside it, so that a row of one stands where a row of ten would;
* the choice to lay chains outward in the order the input ports run along ``X``.

**Legality is the validator's.**  Nothing here decides what the game accepts:
:func:`build_row` refuses with a named cause when it cannot lay a row the
validator would pass, and every row it does return is meant to come back clean
from ``validate(placement, None, registry)`` once a slab is under it.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fractions import Fraction

from flab2bp.sfy.geometry import quat_rotate, world_port
from flab2bp.sfy.labmap import LabMap, load_lab_map, machine_class
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    Link,
    MachineObj,
    Pose,
    SplinePoint,
    Vector,
    belt_ends,
)
from flab2bp.sfy.layout.splines import concat, incline, straight
from flab2bp.sfy.layout.validate import (
    BELT_CLEARANCE_HALF_HEIGHT_CM,
    BELT_CLEARANCE_HALF_WIDTH_CM,
)
from flab2bp.sfy.registry import Buildable, ClearanceBox, Limits, Port, Registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, SfyMachineGroup
from flab2bp.spec import BeltTier

__all__ = [
    "LEAD_IN_MULTIPLE",
    "MERGER_CLASS",
    "SPLITTER_CLASS",
    "ChainEnd",
    "RowError",
    "RowGeometry",
    "build_row",
    "crossing_gap_cm",
    "grid_ceil",
    "grid_floor",
    "hard_footprint_cm",
    "machine_pitch_cm",
    "slab_top_cm",
]

#: The two conveyor attachments a manifold is built from.  Named here the way
#: :data:`flab2bp.sfy.spec.FOUNDATION_CLASS` is named there: the registry has no
#: way to be asked for "the splitter", so the class is stated once and every
#: number about it -- where its ports are, how big its box is -- is read.
SPLITTER_CLASS = "Build_ConveyorAttachmentSplitter_C"
MERGER_CLASS = "Build_ConveyorAttachmentMerger_C"

LEAD_IN_MULTIPLE = 1
"""How many ``belt_min_length_cm``, rounded up to the grid, a feeder runs flat
out of its splitter before it descends.

Ours.  ``ports.position`` refuses a belt that leaves a port more than
``PORT_ANGLE_RAD`` off the port's own facing, and a splitter's side port faces
along the ground, so a feeder that dropped straight out of the port would be a
belt this project refuses to author.  One minimum belt length is the shortest
flat piece the game would let stand on its own, which makes it the smallest
honest answer.
"""

_EPS = 1e-9
"""Slack for a grid comparison, so that a value already on the grid stays there
rather than rounding up a whole step on a float's last bit."""


class RowError(ValueError):
    """A row this module will not lay, named by its cause.

    The message always opens with the cause -- ``row too deep``, ``row too
    wide``, ``row too tall``, ``run exceeds the belt ceiling``, ``more input
    items than ...`` -- and then gives the numbers that settled it.
    """


# --- the numbers a row stands on -------------------------------------------


def grid_ceil(value: float, grid: float) -> float:
    """``value`` rounded up to the next multiple of ``grid``."""
    return math.ceil(value / grid - _EPS) * grid


def grid_floor(value: float, grid: float) -> float:
    """``value`` rounded down to the previous multiple of ``grid``."""
    return math.floor(value / grid + _EPS) * grid


def _grid(limits: Limits) -> float:
    if limits.hologram_grid_cm is None:
        raise RowError("the registry states no hologram grid, so a row has nothing to stand on")
    return limits.hologram_grid_cm


def hard_footprint_cm(buildable: Buildable) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)``: what the buildable's HARD clearance covers on the ground.

    Soft boxes are left out on the game's own marking: ``CT_Soft`` is a box that
    may be shared -- a foundation's is soft, which is why a machine may stand on
    one -- and ``geom.hard_clearance`` tests only the hard ones.  The footprint
    is what the machine really occupies, so it is what a pitch and a slab are
    measured from.
    """
    origin = Pose(0.0, 0.0, 0.0, 0.0)
    spans = [_box_bounds(box, origin) for box in buildable.clearance if not box.soft]
    if not spans:
        raise RowError(
            f"{buildable.class_name} has no hard clearance box, so this module cannot "
            "say how much ground it takes"
        )
    return (
        min(low[0] for low, _ in spans),
        min(low[1] for low, _ in spans),
        max(high[0] for _, high in spans),
        max(high[1] for _, high in spans),
    )


def machine_pitch_cm(buildable: Buildable, limits: Limits) -> float:
    """How far apart two of these machines stand, centre to centre.

    The machine's own hard footprint across ``X`` plus one hologram grid step,
    rounded up to the grid: the grid step is the gap, and it is the game's own
    smallest move rather than a number chosen here.  Two machines at this pitch
    are the closest two holograms the build gun will place without a clearance
    disqualifier and still leave a lane between them.
    """
    grid = _grid(limits)
    x0, _, x1, _ = hard_footprint_cm(buildable)
    return grid_ceil(x1 - x0 + grid, grid)


def crossing_gap_cm(registry: Registry) -> float:
    """How much higher one chain stands than the chain inside it.

    Ours, and derived rather than chosen: ``belt.clearance`` gives every belt a
    box ``15 cm`` above and below its own centreline
    (:data:`~flab2bp.sfy.layout.validate.BELT_CLEARANCE_HALF_HEIGHT_CM`), so a
    belt is ``30 cm`` of clearance tall.  Two belts whose centrelines are twice
    that apart have a whole belt box of air between them, which is the gap this
    project crosses belts at.  Rounded up to the hologram grid, because every
    attachment in a row stands on it.
    """
    grid = _grid(registry.limits)
    box_height = 2.0 * BELT_CLEARANCE_HALF_HEIGHT_CM
    return grid_ceil(2.0 * box_height, grid)


def slab_top_cm(registry: Registry) -> float:
    """How high the top of one foundation is, which is where a machine stands.

    The shipped foundation's own clearance box is as thick as the slab and its
    transform sits at mid-thickness, so a slab laid at half its thickness has
    its top at the thickness.  That is where Task 8 lays the floor and so where
    this row stands its machines.
    """
    foundation = registry.buildables[FOUNDATION_CLASS]
    boxes = [box for box in foundation.clearance]
    if not boxes:
        raise RowError(f"{FOUNDATION_CLASS} has no clearance box, so a slab has no top")
    low, high = _box_bounds(boxes[0], Pose(0.0, 0.0, 0.0, 0.0))
    return high[2] - low[2]


# --- clearance boxes, where they stand -------------------------------------


def _rotator_axes(rotation: Vector) -> tuple[Vector, Vector, Vector]:
    """``FRotationMatrix``'s three axes for a ``(pitch, yaw, roll)`` rotator in degrees.

    The same composition :mod:`flab2bp.sfy.layout.validate` places a clearance
    box with, so that the extents this module measures are the extents
    ``geom.bounds`` will judge.
    """
    pitch, yaw, roll = (math.radians(angle) for angle in rotation)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    return (
        (cp * cy, cp * sy, sp),
        (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp),
        (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp),
    )


def _box_bounds(box: ClearanceBox, pose: Pose) -> tuple[Vector, Vector]:
    """An axis-aligned ``(min, max)`` around one clearance box on an actor."""
    axes = _rotator_axes(box.rotation)
    half = [(box.max[i] - box.min[i]) / 2.0 * abs(box.scale[i]) for i in range(3)]
    mid = [(box.max[i] + box.min[i]) / 2.0 * box.scale[i] for i in range(3)]
    local = tuple(box.translation[i] + sum(mid[k] * axes[k][i] for k in range(3)) for i in range(3))
    transform = pose.transform()
    turned = quat_rotate(transform.rotation, (local[0], local[1], local[2]))
    centre = [turned[i] + transform.translation[i] for i in range(3)]
    world = [quat_rotate(transform.rotation, axis) for axis in axes]
    reach = [sum(half[k] * abs(world[k][i]) for k in range(3)) for i in range(3)]
    return (
        (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
        (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
    )


@dataclass(frozen=True, slots=True)
class _Band:
    """One box's reach across ``Y`` and ``Z``: what a belt lane has to miss."""

    y0: float
    y1: float
    z0: float
    z1: float

    def hits(self, y: float, z: float, half_y: float, half_z: float) -> bool:
        return (
            y + half_y > self.y0
            and y - half_y < self.y1
            and z + half_z > self.z0
            and z - half_z < self.z1
        )


def _hard_bands(buildable: Buildable, pose: Pose) -> tuple[_Band, ...]:
    return tuple(
        _Band(low[1], high[1], low[2], high[2])
        for low, high in (_box_bounds(box, pose) for box in buildable.clearance if not box.soft)
    )


def _lane_is_clear(y: float, z: float, bands: Sequence[_Band]) -> bool:
    """Would a belt running along ``X`` at ``(y, z)`` lap one of these boxes?

    The belt's own clearance is ``belt.clearance``'s box -- 79 cm to each side,
    15 cm above and below -- and ``X`` is ignored on purpose: a chain runs the
    length of the row, so it has to clear the machine LINE and not the one
    machine it happens to pass.  That is ours, and it is what makes a row of one
    stand where a row of ten would, so that Task 8 can lengthen a row without
    moving its chains.
    """
    return not any(
        band.hits(y, z, BELT_CLEARANCE_HALF_WIDTH_CM, BELT_CLEARANCE_HALF_HEIGHT_CM)
        for band in bands
    )


# --- what a row is ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainEnd:
    """One open end of the row: where the next stage wires a belt.

    ``pose`` is where the PORT sits, carrying the yaw of the attachment it
    belongs to, so that a caller can ask the registry which way it faces.
    ``belt_class`` is the tier that carries ``items_per_second``; a belt wired
    here must be at least that fast.
    """

    object_id: int
    port: str
    pose: Pose
    item_id: str
    items_per_second: Fraction
    belt_class: str


@dataclass(frozen=True, slots=True)
class RowGeometry:
    """One laid-out row, in its own frame: machine 0 at the origin.

    Chains run at negative ``Y`` and the merger chain at positive ``Y``, so the
    row's ``-Y`` face is its input side.  ``flip`` mirrors ``X``, which puts the
    chain inputs at the ``+X`` end for a row that faces the other way.

    The band is ``[x_min_cm, x_max_cm] x [y_min_cm, y_max_cm]``, measured over
    every clearance box and every belt's own clearance: it is what Task 8 packs
    designer cells with, and it is what the row was refused against.
    """

    machine_class: str
    pitch_cm: float
    belt_z_cm: float
    crossing_gap_cm: float
    chain_in: tuple[ChainEnd, ...]
    chain_out: ChainEnd
    machines: tuple[MachineObj, ...]
    attachments: tuple[AttachmentObj, ...]
    belts: tuple[BeltRun, ...]
    links: tuple[Link, ...]
    machine_footprint_cm: tuple[float, float, float, float]
    x_min_cm: float
    y_min_cm: float
    x_max_cm: float
    y_max_cm: float

    @property
    def width_cm(self) -> float:
        return self.x_max_cm - self.x_min_cm

    @property
    def depth_cm(self) -> float:
        return self.y_max_cm - self.y_min_cm


# --- laying one out --------------------------------------------------------


def build_row(
    group: SfyMachineGroup,
    registry: Registry,
    *,
    designer: Designer,
    belt_tiers: Sequence[BeltTier],
    flip: bool = False,
    next_id: Iterator[int] | None = None,
    limits: Limits | None = None,
    lab_map: LabMap | None = None,
) -> RowGeometry:
    """Lay ``group``'s machines out as one manifold row.

    ``next_id`` is the build's own numbering, so that Task 8 can merge rows
    without renumbering them; it defaults to a fresh count for a row built on
    its own.  ``designer`` is the box the row has to stand inside: a row that
    cannot is refused with :class:`RowError` rather than returned for the
    validator to turn away.

    The row is returned in its own frame and carries no foundations -- a slab is
    a property of the whole build, not of one row -- so a placement made of one
    row alone will fault ``slab.under_every_foot`` until the floor is under it.
    """
    limits = registry.limits if limits is None else limits
    lab_map = load_lab_map() if lab_map is None else lab_map
    ids = itertools.count(1) if next_id is None else next_id
    grid = _grid(limits)

    machine = registry.buildables.get(group.machine_class)
    if machine is None:
        raise RowError(f"the registry has no buildable called {group.machine_class!r}")
    tiers = tuple(sorted(belt_tiers, key=lambda tier: tier.items_per_second))
    if not tiers:
        raise RowError("this spec names no belt, so a row has nothing to carry items on")

    inputs, out_port = _ports_for(group, machine)
    pitch = machine_pitch_cm(machine, limits)
    stand = slab_top_cm(registry)
    gap = crossing_gap_cm(registry)
    # ``flip`` mirrors the LINE, not the machines: a machine keeps yaw 0 so that
    # its input face still looks down ``-Y``, and its ports therefore keep their
    # own ``X`` offsets.  What turns about is every attachment, so that a chain
    # runs from the ``+X`` end of the row towards ``-X``.
    sign = -1.0 if flip else 1.0
    yaw = 180.0 if flip else 0.0

    machines = tuple(
        MachineObj(
            id=next(ids),
            class_name=group.machine_class,
            pose=Pose(sign * index * pitch, 0.0, stand, 0.0),
            recipe_class=group.recipe_class,
            clock=group.clock if index < group.count - 1 else group.last_clock,
            somersloops=group.somersloops,
        )
        for index in range(group.count)
    )
    bands = _hard_bands(machine, machines[0].pose)
    belt_z = stand + inputs[0][1].translation[2]

    attachments: list[AttachmentObj] = []
    belts: list[BeltRun] = []
    links: list[Link] = []
    chain_in: list[ChainEnd] = []

    placed: list[tuple[float, float]] = []  # (y, z) of every chain laid so far
    for depth, (item_id, port) in enumerate(inputs):
        end = _lay_input_chain(
            registry,
            lab_map,
            limits,
            group,
            item_id,
            port,
            depth=depth,
            gap=gap,
            grid=grid,
            belt_z=belt_z,
            bands=bands,
            placed=placed,
            designer=designer,
            machines=machines,
            tiers=tiers,
            yaw=yaw,
            ids=ids,
            attachments=attachments,
            belts=belts,
            links=links,
        )
        chain_in.append(end)

    chain_out = _lay_output_chain(
        registry,
        lab_map,
        limits,
        group,
        out_port,
        grid=grid,
        belt_z=belt_z,
        bands=bands,
        machines=machines,
        tiers=tiers,
        yaw=yaw,
        ids=ids,
        attachments=attachments,
        belts=belts,
        links=links,
    )

    low, high = _extents(registry, machines, tuple(attachments), tuple(belts))
    _fits(low, high, designer)
    x0, y0, x1, y1 = hard_footprint_cm(machine)
    feet = (
        min(m.pose.x for m in machines) + x0,
        y0,
        max(m.pose.x for m in machines) + x1,
        y1,
    )
    return RowGeometry(
        machine_class=group.machine_class,
        pitch_cm=pitch,
        belt_z_cm=belt_z,
        crossing_gap_cm=gap,
        chain_in=tuple(chain_in),
        chain_out=chain_out,
        machines=machines,
        attachments=tuple(attachments),
        belts=tuple(belts),
        links=tuple(links),
        machine_footprint_cm=feet,
        x_min_cm=low[0],
        y_min_cm=low[1],
        x_max_cm=high[0],
        y_max_cm=high[1],
    )


def _ports_for(
    group: SfyMachineGroup, machine: Buildable
) -> tuple[tuple[tuple[str, Port], ...], Port]:
    """Which machine port each input item arrives at, and where the output leaves.

    Ports are taken in the order they run along ``X`` and items in their own
    sorted order, so the same group always lays the same row.  Which item goes
    to which port is free -- a machine eats whatever arrives at any of its input
    ports -- and this is ours only in the sense that a choice had to be made.
    """
    belt_ports = [port for port in machine.ports if port.kind == "belt"]
    in_ports = sorted(
        (port for port in belt_ports if port.direction == "input"),
        key=lambda port: port.translation[0],
    )
    out_ports = sorted(
        (port for port in belt_ports if port.direction == "output"),
        key=lambda port: port.translation[0],
    )
    items = sorted(group.inputs_per_machine)
    if len(items) > len(in_ports):
        raise RowError(
            f"more input items than {machine.class_name} has belt input ports: "
            f"{group.recipe_id} eats {items}, and the registry gives the machine "
            f"{len(in_ports)} belt input port(s)"
        )
    products = sorted(group.outputs_per_machine)
    if len(products) != 1:
        raise RowError(
            f"a manifold row drains one output item, and {group.recipe_id} makes "
            f"{products}; a row per product is Task 8's composition, not this row's"
        )
    if not out_ports:
        raise RowError(f"{machine.class_name} has no belt output port, so a row cannot drain it")
    return (tuple(zip(items, in_ports, strict=False)), out_ports[0])


def _lay_input_chain(
    registry: Registry,
    lab_map: LabMap,
    limits: Limits,
    group: SfyMachineGroup,
    item_id: str,
    port: Port,
    *,
    depth: int,
    gap: float,
    grid: float,
    belt_z: float,
    bands: Sequence[_Band],
    placed: list[tuple[float, float]],
    designer: Designer,
    machines: Sequence[MachineObj],
    tiers: Sequence[BeltTier],
    yaw: float,
    ids: Iterator[int],
    attachments: list[AttachmentObj],
    belts: list[BeltRun],
    links: list[Link],
) -> ChainEnd:
    """One splitter chain and its feeders, at the shallowest depth that works."""
    splitter = registry.buildables[SPLITTER_CLASS]
    through_in, through_out, side = _attachment_ports(splitter, "output", flip=yaw != 0.0)
    port_y = port.translation[1]
    chain_z = belt_z + depth * gap

    drop = _descent_run(depth * gap, limits, grid)
    lead = _lead_in(limits, grid)
    y = grid_floor(port_y - abs(side.translation[1]) - drop - lead, grid)
    if placed:
        y = min(y, placed[-1][0] - grid)
    while True:
        back, _ = _box_bounds(splitter.clearance[0], Pose(0.0, y, chain_z, yaw))
        if back[1] < -designer.half_cm:
            raise RowError(
                f"row too deep: chain {depth} for {item_id!r} would reach y = {back[1]:.0f} "
                f"cm, outside the {designer.mark} designer's "
                f"{2 * designer.half_cm:.0f} x {2 * designer.half_cm:.0f} cm floor"
            )
        if _lane_is_clear(y, chain_z, bands) and _clears_lower_chains(
            y, chain_z, drop, port_y, belt_z, gap, placed
        ):
            break
        y -= grid

    per_machine = _per_machine(group.inputs_per_machine[item_id], group)
    tail = [sum(per_machine[index:], Fraction(0)) for index in range(group.count)]

    first: AttachmentObj | None = None
    previous: AttachmentObj | None = None
    for index, machine in enumerate(machines):
        node = AttachmentObj(
            id=next(ids),
            class_name=SPLITTER_CLASS,
            pose=Pose(machine.pose.x + port.translation[0], y, chain_z, yaw),
        )
        attachments.append(node)
        first = first if first is not None else node
        if previous is not None:
            _run(
                registry,
                lab_map,
                _port_at(previous, through_out),
                _port_at(node, through_in),
                item_id=item_id,
                rate=tail[index],
                tiers=tiers,
                what=f"chain {depth} between machines {index - 1} and {index}",
                ids=ids,
                belts=belts,
                links=links,
                upstream=(previous.id, through_out.name),
                downstream=(node.id, through_in.name),
            )
        _run(
            registry,
            lab_map,
            _port_at(node, side),
            _port_at(machine, port),
            item_id=item_id,
            rate=per_machine[index],
            tiers=tiers,
            what=f"the feeder of chain {depth} into machine {index}",
            ids=ids,
            belts=belts,
            links=links,
            upstream=(node.id, side.name),
            downstream=(machine.id, port.name),
            descend=drop,
        )
        previous = node

    assert first is not None  # a group has at least one machine
    placed.append((y, chain_z))
    return ChainEnd(
        object_id=first.id,
        port=through_in.name,
        pose=Pose(*_port_at(first, through_in), yaw),
        item_id=item_id,
        items_per_second=tail[0],
        belt_class=_belt_class(lab_map, _tier(tail[0], tiers, item_id, f"chain {depth}")),
    )


def _lay_output_chain(
    registry: Registry,
    lab_map: LabMap,
    limits: Limits,
    group: SfyMachineGroup,
    port: Port,
    *,
    grid: float,
    belt_z: float,
    bands: Sequence[_Band],
    machines: Sequence[MachineObj],
    tiers: Sequence[BeltTier],
    yaw: float,
    ids: Iterator[int],
    attachments: list[AttachmentObj],
    belts: list[BeltRun],
    links: list[Link],
) -> ChainEnd:
    """The merger chain every machine's output drains into."""
    merger = registry.buildables[MERGER_CLASS]
    through_in, through_out, side = _attachment_ports(merger, "input", flip=yaw != 0.0)
    item_id = sorted(group.outputs_per_machine)[0]
    port_y = port.translation[1]

    y = grid_ceil(port_y + abs(side.translation[1]) + _lead_in(limits, grid), grid)
    while not _lane_is_clear(y, belt_z, bands):
        y += grid

    per_machine = _per_machine(group.outputs_per_machine[item_id], group)
    head = [sum(per_machine[: index + 1], Fraction(0)) for index in range(group.count)]

    last: AttachmentObj | None = None
    for index, machine in enumerate(machines):
        node = AttachmentObj(
            id=next(ids),
            class_name=MERGER_CLASS,
            pose=Pose(machine.pose.x + port.translation[0], y, belt_z, yaw),
        )
        attachments.append(node)
        _run(
            registry,
            lab_map,
            _port_at(machine, port),
            _port_at(node, side),
            item_id=item_id,
            rate=per_machine[index],
            tiers=tiers,
            what=f"the drain of machine {index}",
            ids=ids,
            belts=belts,
            links=links,
            upstream=(machine.id, port.name),
            downstream=(node.id, side.name),
        )
        if last is not None:
            _run(
                registry,
                lab_map,
                _port_at(last, through_out),
                _port_at(node, through_in),
                item_id=item_id,
                rate=head[index - 1],
                tiers=tiers,
                what=f"the merger chain between machines {index - 1} and {index}",
                ids=ids,
                belts=belts,
                links=links,
                upstream=(last.id, through_out.name),
                downstream=(node.id, through_in.name),
            )
        last = node

    assert last is not None
    return ChainEnd(
        object_id=last.id,
        port=through_out.name,
        pose=Pose(*_port_at(last, through_out), yaw),
        item_id=item_id,
        items_per_second=head[-1],
        belt_class=_belt_class(lab_map, _tier(head[-1], tiers, item_id, "the merger chain")),
    )


def _attachment_ports(
    attachment: Buildable, side_kind: str, *, flip: bool
) -> tuple[Port, Port, Port]:
    """``(through in, through out, side)`` of a splitter or a merger.

    The through axis is the pair of ports on ``+-X``; the side port is the one
    on the machines' side, which is ``+Y`` when the attachment stands unturned
    and ``-Y`` when it is turned about ``Z`` to face the other way.  All three
    are picked out of the registry's own port table by position, never by name.
    """
    belt_ports = [port for port in attachment.ports if port.kind == "belt"]
    through = [port for port in belt_ports if abs(port.translation[0]) > abs(port.translation[1])]
    entry = next(port for port in through if port.direction == "input")
    exit_ = next(port for port in through if port.direction == "output")
    wanted = -1.0 if flip else 1.0
    sides = [
        port for port in belt_ports if port not in (entry, exit_) and port.direction == side_kind
    ]
    side = next(port for port in sides if math.copysign(1.0, port.translation[1]) == wanted)
    return (entry, exit_, side)


def _port_at(placed: MachineObj | AttachmentObj, port: Port) -> Vector:
    """Where one of ``placed``'s ports sits in the row's frame."""
    return world_port(placed.pose.transform(), port)


def _per_machine(rate: Fraction, group: SfyMachineGroup) -> list[Fraction]:
    """What each machine in the row moves, the last one at its own clock."""
    share = group.last_clock / group.clock
    return [rate if index < group.count - 1 else rate * share for index in range(group.count)]


def _lead_in(limits: Limits, grid: float) -> float:
    if limits.belt_min_length_cm is None:
        raise RowError("the registry states no minimum belt length, so no run can be sized")
    return grid_ceil(LEAD_IN_MULTIPLE * limits.belt_min_length_cm, grid)


def _descent_run(fall: float, limits: Limits, grid: float) -> float:
    """The shortest run, on the grid, a belt may fall ``fall`` over.

    ``belt.incline`` refuses a chord steeper than ``limits.belt_max_incline_deg``,
    so the run is the fall over that angle's tangent, rounded up to the grid.
    The steepest legal descent is deliberate: it keeps the feeder flat for as
    long as possible, which is what carries it over the chains below it.
    """
    if fall <= 0.0:
        return 0.0
    if limits.belt_max_incline_deg is None:
        raise RowError("the registry states no maximum belt incline, so no descent can be sized")
    return grid_ceil(fall / math.tan(math.radians(limits.belt_max_incline_deg)), grid)


def _clears_lower_chains(
    y: float,
    chain_z: float,
    drop: float,
    port_y: float,
    belt_z: float,
    gap: float,
    placed: Sequence[tuple[float, float]],
) -> bool:
    """Does this chain's feeder pass over every chain inside it with room to spare?

    A feeder runs flat out of its splitter and then descends over ``drop`` into
    the machine port, so its height where it crosses a lower chain is fixed by
    the descent alone.  The room asked for is one :func:`crossing_gap_cm`, which
    is a whole belt clearance box: ours, and the same number the chains are
    stacked at.
    """
    del y
    seam = port_y - drop
    for lower_y, lower_z in placed:
        here = (
            chain_z if lower_y <= seam else belt_z + (port_y - lower_y) / drop * (chain_z - belt_z)
        )
        if here + _EPS < lower_z + gap:
            return False
    return True


# --- belts -----------------------------------------------------------------


def _run(
    registry: Registry,
    lab_map: LabMap,
    start: Vector,
    finish: Vector,
    *,
    item_id: str,
    rate: Fraction,
    tiers: Sequence[BeltTier],
    what: str,
    ids: Iterator[int],
    belts: list[BeltRun],
    links: list[Link],
    upstream: tuple[int, str],
    downstream: tuple[int, str],
    descend: float = 0.0,
) -> None:
    """One belt from ``start`` to ``finish``, wired at both ends."""
    tier = _tier(rate, tiers, item_id, what)
    class_name = _belt_class(lab_map, tier)
    points = _shape(start, finish, descend)
    belt = BeltRun(
        id=next(ids),
        class_name=class_name,
        points=points,
        item_id=item_id,
        items_per_second=rate,
    )
    belts.append(belt)
    entry, exit_ = belt_ends(registry, class_name)
    links.append(Link(a=upstream, b=(belt.id, entry)))
    links.append(Link(a=(belt.id, exit_), b=downstream))


def _shape(start: Vector, finish: Vector, descend: float) -> tuple[SplinePoint, ...]:
    """The spline one feeder or chain belt runs along.

    A belt at one height is a straight run.  A belt that has to come down runs
    flat out of its port first -- see :data:`LEAD_IN_MULTIPLE` -- and descends
    over the last ``descend`` centimetres, which is the steepest descent
    ``belt.incline`` allows and so the shortest.  Both pieces are straight, so
    the spline never bends and ``belt.curvature`` has nothing to measure.
    """
    span = math.dist(start[:2], finish[:2])
    if abs(finish[2] - start[2]) <= _EPS or descend <= 0.0:
        return straight(start, _towards(start, finish), math.dist(start, finish))
    lead = straight(start, _towards(start, finish), span - descend)
    seam = lead[-1][0]
    fall = incline(seam, _towards(seam, finish), descend, finish[2] - seam[2])
    return concat(lead, fall)


def _towards(start: Vector, finish: Vector) -> Vector:
    return (finish[0] - start[0], finish[1] - start[1], 0.0)


def _tier(rate: Fraction, tiers: Sequence[BeltTier], item_id: str, what: str) -> BeltTier:
    """The slowest belt in the spec that carries ``rate``."""
    for tier in tiers:
        if tier.items_per_second >= rate:
            return tier
    fastest = tiers[-1]
    raise RowError(
        f"run exceeds the belt ceiling: {what} carries {rate} items/s of {item_id!r}, "
        f"past the {fastest.items_per_second}/s of {fastest.item_id!r}, the fastest "
        "belt this spec allows"
    )


def _belt_class(lab_map: LabMap, tier: BeltTier) -> str:
    try:
        return machine_class(lab_map, tier.item_id)
    except KeyError as exc:
        raise RowError(f"the lab map has no conveyor class for {tier.item_id!r}") from exc


# --- the band the row occupies ---------------------------------------------


def _extents(
    registry: Registry,
    machines: Sequence[MachineObj],
    attachments: Sequence[AttachmentObj],
    belts: Sequence[BeltRun],
) -> tuple[Vector, Vector]:
    """The whole row's reach, over every clearance box and every belt's own.

    Belts are bounded by ``belt.clearance``'s box at every spline point rather
    than along the chords, which over-states a run's ends by the box's own width
    -- conservative in the direction of a smaller row, never a bigger one.
    """
    lows: list[Vector] = []
    highs: list[Vector] = []
    standing: list[MachineObj | AttachmentObj] = [*machines, *attachments]
    for placed in standing:
        buildable = registry.buildables[placed.class_name]
        for box in buildable.clearance:
            low, high = _box_bounds(box, placed.pose)
            lows.append(low)
            highs.append(high)
    for belt in belts:
        for location, _, _ in belt.points:
            lows.append(
                (
                    location[0] - BELT_CLEARANCE_HALF_WIDTH_CM,
                    location[1] - BELT_CLEARANCE_HALF_WIDTH_CM,
                    location[2] - BELT_CLEARANCE_HALF_HEIGHT_CM,
                )
            )
            highs.append(
                (
                    location[0] + BELT_CLEARANCE_HALF_WIDTH_CM,
                    location[1] + BELT_CLEARANCE_HALF_WIDTH_CM,
                    location[2] + BELT_CLEARANCE_HALF_HEIGHT_CM,
                )
            )
    return (
        (min(v[0] for v in lows), min(v[1] for v in lows), min(v[2] for v in lows)),
        (max(v[0] for v in highs), max(v[1] for v in highs), max(v[2] for v in highs)),
    )


def _fits(low: Vector, high: Vector, designer: Designer) -> None:
    """Refuse a row that does not stand inside the designer, by the axis it leaves.

    ``geom.bounds`` refuses a placement that reaches outside the Blueprint
    Designer's own volume, so a row that reaches outside it is a row this module
    must not hand back.  The row is measured in its own frame, which is where
    the validator will see it; Task 8 moves rows about the floor, so a row
    refused here may still fit once it is centred -- :class:`RowGeometry`'s band
    is what that decision is made from.
    """
    half, height = designer.half_cm, designer.height_cm
    box = f"{2 * half:.0f} x {2 * half:.0f} x {height:.0f} cm"
    if low[1] < -half or high[1] > half:
        raise RowError(
            f"row too deep: the row runs from y = {low[1]:.0f} to y = {high[1]:.0f} cm, "
            f"{high[1] - low[1]:.0f} cm of band, outside the {designer.mark} designer's {box}"
        )
    if low[0] < -half or high[0] > half:
        raise RowError(
            f"row too wide: the row runs from x = {low[0]:.0f} to x = {high[0]:.0f} cm, "
            f"{high[0] - low[0]:.0f} cm of band, outside the {designer.mark} designer's {box}"
        )
    if low[2] < 0.0 or high[2] > height:
        raise RowError(
            f"row too tall: the row reaches z = {high[2]:.0f} cm, outside the "
            f"{designer.mark} designer's {box}"
        )
