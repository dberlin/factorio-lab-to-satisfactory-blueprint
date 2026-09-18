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

* :func:`crossing_gap_cm`, the clearance between crossing belts; attachment
  layers additionally separate their full cooked mesh bodies;
* straight mouth approaches long enough to clear the machine or attachment
  envelope before a feeder changes height;
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

from flab2bp.sfy.geometry import box_bounds, world_port
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
    attachment_boxes,
)
from flab2bp.sfy.registry import Buildable, ClearanceBox, Limits, Port, Registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, DirectPair, SfyMachineGroup
from flab2bp.spec import BeltTier

__all__ = [
    "MERGER_CLASS",
    "SPLITTER_CLASS",
    "ChainEnd",
    "RowError",
    "RowGeometry",
    "build_pair",
    "build_row",
    "crossing_gap_cm",
    "grid_ceil",
    "grid_floor",
    "hard_footprint_cm",
    "machine_pitch_cm",
    "shortest_belt_cm",
    "slab_top_cm",
]

#: The two conveyor attachments a manifold is built from.  Named here the way
#: :data:`flab2bp.sfy.spec.FOUNDATION_CLASS` is named there: the registry has no
#: way to be asked for "the splitter", so the class is stated once and every
#: number about it -- where its ports are, how big its box is -- is read.
SPLITTER_CLASS = "Build_ConveyorAttachmentSplitter_C"
MERGER_CLASS = "Build_ConveyorAttachmentMerger_C"

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


def _row_layer_cm(registry: Registry) -> float:
    """Separate cooked attachment bodies as well as the belts between them."""
    height = max(
        2.0 * box.reach[2]
        for class_name in (SPLITTER_CLASS, MERGER_CLASS)
        for box in attachment_boxes(AttachmentObj(0, class_name, Pose(0, 0, 0, 0)), registry)
    )
    return grid_ceil(max(crossing_gap_cm(registry), height), _grid(registry.limits))


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


def _box_bounds(box: ClearanceBox, pose: Pose) -> tuple[Vector, Vector]:
    """An axis-aligned ``(min, max)`` around one clearance box on an actor.

    :func:`flab2bp.sfy.geometry.box_bounds` does the arithmetic -- the same
    composition :mod:`flab2bp.sfy.layout.validate` places a box with, so that the
    extents this module measures are the extents ``geom.bounds`` will judge --
    and this takes the :class:`~flab2bp.sfy.layout.model.Pose` a placed object
    carries rather than its transform.
    """
    return box_bounds(box, pose.transform())


@dataclass(frozen=True, slots=True)
class _Band:
    """One box's reach across ``Y`` and ``Z``: what a belt lane has to miss."""

    y0: float
    y1: float
    z0: float
    z1: float

    def hits(self, y: float, z: float, half_y: float, half_z: float) -> bool:
        """Whether a belt box centred at ``(y, z)`` reaches this box AT ALL.

        Touching counts.  ``belt.capsule`` reports a lap of ``0.0 cm`` as a
        fault -- two boxes that share a face are two boxes that overlap -- so a
        lane that just grazes the machine line is a lane this module must not
        lay.  The comparison was strict while every chain stood on the hologram
        grid and no lane could land exactly on a box's face; a chain placed at
        the centimetre lands on one the moment the box's own face is a whole
        number of centimetres out, which every hard box in the registry is.
        """
        return (
            y + half_y >= self.y0
            and y - half_y <= self.y1
            and z + half_z >= self.z0
            and z - half_z <= self.z1
        )


def _hard_bands(buildable: Buildable, pose: Pose) -> tuple[_Band, ...]:
    return tuple(
        _Band(low[1], high[1], low[2], high[2])
        for low, high in (_box_bounds(box, pose) for box in buildable.clearance if not box.soft)
    )


def _attachment_band(registry: Registry, class_name: str) -> _Band:
    boxes = attachment_boxes(AttachmentObj(0, class_name, Pose(0, 0, 0, 0)), registry)
    return _Band(
        min(box.centre[1] - box.reach[1] for box in boxes),
        max(box.centre[1] + box.reach[1] for box in boxes),
        min(box.centre[2] - box.reach[2] for box in boxes),
        max(box.centre[2] + box.reach[2] for box in boxes),
    )


def _lane_is_clear(y: float, z: float, bands: Sequence[_Band], body: _Band) -> bool:
    """Keep both the through belt and the actual attachment body off machines."""
    return not any(
        band.hits(y, z, BELT_CLEARANCE_HALF_WIDTH_CM, BELT_CLEARANCE_HALF_HEIGHT_CM)
        or band.hits(
            y + (body.y0 + body.y1) / 2,
            z + (body.z0 + body.z1) / 2,
            (body.y1 - body.y0) / 2,
            (body.z1 - body.z0) / 2,
        )
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

    machine = _buildable(registry, group.machine_class)
    tiers = tuple(sorted(belt_tiers, key=lambda tier: tier.items_per_second))
    if not tiers:
        raise RowError("this spec names no belt, so a row has nothing to carry items on")

    inputs, out_port = _ports_for(group, machine)
    pitch = machine_pitch_cm(machine, limits)
    stand = slab_top_cm(registry)
    gap = _row_layer_cm(registry)
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
    feet = _footprint(registry, machines)
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


def build_pair(
    pair: DirectPair,
    registry: Registry,
    *,
    designer: Designer,
    belt_tiers: Sequence[BeltTier],
    flip: bool = False,
    next_id: Iterator[int] | None = None,
    limits: Limits | None = None,
    lab_map: LabMap | None = None,
) -> RowGeometry:
    """Two groups whose machines match one for one, laid as one row facing itself.

    :func:`~flab2bp.sfy.spec.direct_pairs` has already found, from the rates
    alone, that each producer machine makes exactly what one consumer machine
    eats.  So the two lines stand facing each other and one straight belt joins
    each pair of machines: no merger chain draining the producers, no splitter
    chain feeding the consumers, and no trunk up a corridor between them.  That
    is three attachments and three belts where a manifold wants twelve
    attachments, eleven belts, two turns and a column -- and, because the ends of
    a row are what a designer's depth is spent on, it is most of a Blueprint
    Designer.

    **Nothing is re-solved.**  The belts carry the rates the spec already states,
    machine for machine, the odd last machine included.

    The two lines share ONE pitch, the wider of the two machines', so that
    machine *i* of each stands at the same ``X`` and the belt between them is
    straight.  How far apart the lines stand is the smallest whole centimetre
    that leaves the belt at least its shortest legal length AND keeps the two
    hard clearance boxes off each other, and it is usually the boxes that decide.

    What comes back is ONE :class:`RowGeometry`: the producers' own input chains
    are its ``chain_in`` and the consumers' merger chain is its ``chain_out``, so
    every stage above this one composes it exactly as it composes a single row.
    """
    limits = registry.limits if limits is None else limits
    lab_map = load_lab_map() if lab_map is None else lab_map
    ids = itertools.count(1) if next_id is None else next_id
    grid = _grid(limits)
    tiers = tuple(sorted(belt_tiers, key=lambda tier: tier.items_per_second))
    if not tiers:
        raise RowError("this spec names no belt, so a row has nothing to carry items on")

    maker = _buildable(registry, pair.producer.machine_class)
    eater = _buildable(registry, pair.consumer.machine_class)
    maker_inputs, maker_out = _ports_for(pair.producer, maker)
    eater_inputs, eater_out = _ports_for(pair.consumer, eater)
    eater_in = dict(eater_inputs)[pair.item]

    pitch = max(machine_pitch_cm(maker, limits), machine_pitch_cm(eater, limits))
    stand = slab_top_cm(registry)
    gap = _row_layer_cm(registry)
    sign = -1.0 if flip else 1.0
    yaw = 180.0 if flip else 0.0
    line = _pair_line_cm(maker, eater, maker_out, eater_in, limits)

    machines = tuple(
        MachineObj(
            id=next(ids),
            class_name=group.machine_class,
            pose=Pose(sign * index * pitch, y, stand, 0.0),
            recipe_class=group.recipe_class,
            clock=group.clock if index < group.count - 1 else group.last_clock,
            somersloops=group.somersloops,
        )
        for group, y in ((pair.producer, 0.0), (pair.consumer, line))
        for index in range(group.count)
    )
    makers = machines[: pair.producer.count]
    eaters = machines[pair.producer.count :]
    belt_z = stand + maker_inputs[0][1].translation[2]

    attachments: list[AttachmentObj] = []
    belts: list[BeltRun] = []
    links: list[Link] = []
    chain_in: list[ChainEnd] = []
    placed: list[tuple[float, float]] = []
    for depth, (item_id, port) in enumerate(maker_inputs):
        chain_in.append(
            _lay_input_chain(
                registry,
                lab_map,
                limits,
                pair.producer,
                item_id,
                port,
                depth=depth,
                gap=gap,
                grid=grid,
                belt_z=belt_z,
                bands=_hard_bands(maker, makers[0].pose),
                placed=placed,
                designer=designer,
                machines=makers,
                tiers=tiers,
                yaw=yaw,
                ids=ids,
                attachments=attachments,
                belts=belts,
                links=links,
            )
        )
    chain_out = _lay_output_chain(
        registry,
        lab_map,
        limits,
        pair.consumer,
        eater_out,
        grid=grid,
        belt_z=belt_z,
        bands=_hard_bands(eater, eaters[0].pose),
        machines=eaters,
        tiers=tiers,
        yaw=yaw,
        ids=ids,
        attachments=attachments,
        belts=belts,
        links=links,
        line_y=line,
    )
    per_machine = _per_machine(pair.producer.outputs_per_machine[pair.item], pair.producer)
    for index, (from_machine, to_machine) in enumerate(zip(makers, eaters, strict=True)):
        _run(
            registry,
            lab_map,
            _port_at(from_machine, maker_out),
            _port_at(to_machine, eater_in),
            item_id=pair.item,
            rate=per_machine[index],
            tiers=tiers,
            what=f"the pairing belt from machine {index} to machine {index}",
            ids=ids,
            belts=belts,
            links=links,
            upstream=(from_machine.id, maker_out.name),
            downstream=(to_machine.id, eater_in.name),
        )

    low, high = _extents(registry, machines, tuple(attachments), tuple(belts))
    _fits(low, high, designer)
    feet = _footprint(registry, machines)
    return RowGeometry(
        machine_class=pair.producer.machine_class,
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


def _pair_line_cm(
    maker: Buildable, eater: Buildable, out_port: Port, in_port: Port, limits: Limits
) -> float:
    """How far the consumer line stands from the producer line, in whole centimetres.

    Two bounds and the larger wins.  The belt between the two ports may not be
    shorter than :func:`shortest_belt_cm`, and the two machines' HARD clearance
    boxes may not reach each other -- ``geom.hard_clearance`` is what the build
    gun refuses on, and two boxes that share a face share it by 0.0 cm, which
    counts.  With the machines the game ships it is always the boxes: a Smelter's
    box reaches 500 cm past its own centre and its output port only 200.
    """
    maker_y1 = hard_footprint_cm(maker)[3]
    eater_y0 = hard_footprint_cm(eater)[1]
    belt = out_port.translation[1] - in_port.translation[1] + shortest_belt_cm(limits)
    boxes = maker_y1 - eater_y0 + 1.0
    return float(math.ceil(max(belt, boxes)))


def _buildable(registry: Registry, class_name: str) -> Buildable:
    machine = registry.buildables.get(class_name)
    if machine is None:
        raise RowError(f"the registry has no buildable called {class_name!r}")
    return machine


def _footprint(
    registry: Registry, machines: Sequence[MachineObj]
) -> tuple[float, float, float, float]:
    """What every machine in a row covers on the ground, together."""
    spans = [
        (machine.pose.x, machine.pose.y, hard_footprint_cm(registry.buildables[machine.class_name]))
        for machine in machines
    ]
    return (
        min(x + box[0] for x, _, box in spans),
        min(y + box[1] for _, y, box in spans),
        max(x + box[2] for x, _, box in spans),
        max(y + box[3] for _, y, box in spans),
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
    line_y: float = 0.0,
) -> ChainEnd:
    """One splitter chain and its feeders, at the shallowest depth that works."""
    splitter = registry.buildables[SPLITTER_CLASS]
    through_in, through_out, side = _attachment_ports(splitter, "output", flip=yaw != 0.0)
    port_y = line_y + port.translation[1]
    chain_z = belt_z + depth * gap

    drop = _descent_run(depth * gap, limits, grid)
    body = _attachment_band(registry, SPLITTER_CLASS)
    lead = max(
        shortest_belt_cm(limits),
        body.y1 - abs(side.translation[1]) + BELT_CLEARANCE_HALF_WIDTH_CM + 1,
    )
    # A slope must finish outside the machine envelope. Its final straight is
    # the only part allowed to enter the connected machine's recessed mouth.
    arrival = (
        max(
            shortest_belt_cm(limits),
            port_y - min(band.y0 for band in bands) + BELT_CLEARANCE_HALF_WIDTH_CM + 1,
        )
        if len(group.inputs_per_machine) > 1
        else 0.0
    )
    # Moving a chain outwards lengthens only its flat departure. The descent
    # ends at the machine's approach straight, so its crossing height is fixed.
    crossing = _crossing_below(
        chain_z, drop, port_y - arrival, belt_z, crossing_gap_cm(registry), placed
    )
    if crossing is not None:
        lower_y, lower_z, here = crossing
        raise RowError(
            f"a feeder crosses the chain inside it: the feeder of chain {depth} for "
            f"{item_id!r} passes the chain at y = {lower_y:.0f}, z = {lower_z:.0f} at "
            f"z = {here:.0f}, {here - lower_z:.0f} cm above it where "
            f"{crossing_gap_cm(registry):.0f} is wanted. Standing this chain further "
            "out does not change its descent or its machine-mouth approach."
        )
    # Reserve the departure, descent and machine approach before rounding the
    # chain outwards to a whole centimetre.
    y = float(math.floor(port_y - abs(side.translation[1]) - drop - lead - arrival))
    if placed:
        y = min(y, placed[-1][0] - grid)
    while True:
        back_y = y + body.y0
        if back_y < -designer.half_cm:
            raise RowError(
                f"row too deep: chain {depth} for {item_id!r} would reach y = {back_y:.0f} "
                f"cm, outside the {designer.mark} designer's "
                f"{2 * designer.half_cm:.0f} x {2 * designer.half_cm:.0f} cm floor"
            )
        if _lane_is_clear(y, chain_z, bands, body):
            break
        # The body clearance and connector offsets need not be grid multiples.
        y -= 1.0

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
            arrival=arrival,
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
    line_y: float = 0.0,
) -> ChainEnd:
    """The merger chain every machine's output drains into."""
    merger = registry.buildables[MERGER_CLASS]
    through_in, through_out, side = _attachment_ports(merger, "input", flip=yaw != 0.0)
    item_id = sorted(group.outputs_per_machine)[0]
    port_y = line_y + port.translation[1]

    # The mirror of the input chain's: the machine's output port drains into the
    # merger's side port, one ``side`` out of it towards the machines, over the
    # shortest belt the game allows.
    body = _attachment_band(registry, MERGER_CLASS)
    y = float(math.ceil(port_y + abs(side.translation[1]) + shortest_belt_cm(limits)))
    while not _lane_is_clear(y, belt_z, bands, body):
        y += 1.0

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

    The through axis is the pair of ports on ``+-X``. A splitter stands before
    the machines and feeds towards ``+Y``; a merger stands after them and takes
    its feed from ``-Y``. Flipping the chain reverses both local choices.
    """
    belt_ports = [port for port in attachment.ports if port.kind == "belt"]
    through = [port for port in belt_ports if abs(port.translation[0]) > abs(port.translation[1])]
    entry = next(port for port in through if port.direction == "input")
    exit_ = next(port for port in through if port.direction == "output")
    wanted = (-1.0 if flip else 1.0) * (1.0 if side_kind == "output" else -1.0)
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


def shortest_belt_cm(limits: Limits) -> float:
    """The shortest belt this project will author, in whole centimetres.

    ``belt.min_length`` refuses a belt at or under ``belt_min_length_cm`` --
    ``AFGConveyorBeltHologram::ValidateMinLength`` compares strictly -- so the
    shortest legal run is the next length above it.  **Not rounded to the
    hologram grid**: the grid is where an ATTACHMENT snaps, and a belt is a
    spline between two ports rather than a hologram on a cell, so rounding a belt
    up to the grid spends up to a metre of floor the game never asked for.  Whole
    centimetres, because that is the resolution every other distance in this
    project is stated at and because a belt a hundredth of a centimetre over the
    bound is a belt nobody can reproduce by hand.

    Flat mouth approaches use at least this much run, and extend further when
    needed to leave the connected actor's actual body before turning.
    """
    floor = limits.belt_min_length_cm
    if floor is None:
        raise RowError("the registry states no minimum belt length, so no run can be sized")
    return math.floor(floor) + 1.0


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


def _crossing_below(
    chain_z: float,
    drop: float,
    port_y: float,
    belt_z: float,
    gap: float,
    placed: Sequence[tuple[float, float]],
) -> tuple[float, float, float] | None:
    """The first chain inside this one that its feeder does not clear, if any.

    ``(y, z, height)`` of that chain and of the feeder where it passes over it, or
    ``None`` when every crossing has its room.

    ``port_y`` is where the descent ends, before the machine's straight mouth
    approach. Its position fixes the height at every lower chain regardless of
    where this chain stands. The crossing clearance is :func:`crossing_gap_cm`,
    distinct from the larger layer spacing needed by attachment bodies.
    """
    seam = port_y - drop
    for lower_y, lower_z in placed:
        here = (
            chain_z if lower_y <= seam else belt_z + (port_y - lower_y) / drop * (chain_z - belt_z)
        )
        if here + _EPS < lower_z + gap:
            return (lower_y, lower_z, here)
    return None


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
    arrival: float = 0.0,
) -> None:
    """One belt from ``start`` to ``finish``, wired at both ends."""
    tier = _tier(rate, tiers, item_id, what)
    class_name = _belt_class(lab_map, tier)
    points = _shape(start, finish, descend, arrival)
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


def _shape(
    start: Vector, finish: Vector, descend: float, arrival: float
) -> tuple[SplinePoint, ...]:
    """A straight mouth approach at both ends, with any descent between them."""
    span = math.dist(start[:2], finish[:2])
    if abs(finish[2] - start[2]) <= _EPS or descend <= 0.0:
        return straight(start, _towards(start, finish), math.dist(start, finish))
    lead = straight(start, _towards(start, finish), span - descend - arrival)
    seam = lead[-1][0]
    fall = incline(seam, _towards(seam, finish), descend, finish[2] - seam[2])
    tail = straight(fall[-1][0], _towards(start, finish), arrival)
    return concat(lead, fall, tail)


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
        if isinstance(placed, AttachmentObj):
            for body in attachment_boxes(placed, registry):
                lows.append(
                    (
                        body.centre[0] - body.reach[0],
                        body.centre[1] - body.reach[1],
                        body.centre[2] - body.reach[2],
                    )
                )
                highs.append(
                    (
                        body.centre[0] + body.reach[0],
                        body.centre[1] + body.reach[1],
                        body.centre[2] + body.reach[2],
                    )
                )
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
