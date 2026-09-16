"""Repeatable vertical material trunks, with explicit manual stack seams.

Native lift SetupConnections keeps each connection at its actor/top-transform
translation, even when snapped to a floor hole: hole thickness does not move
it to the slab face. Consequently two complete slabs must NOT share a hole
centre. We leave one legal lift length between successive modules' terminal
planes. Base/roof slabs remain separate and corner posts establish the complete
repeat pitch. Join matching open holes with a lift (or a short pipeline) after
placing the next module; the native belt-only blueprint source gate does not
promise automatic lift joining.

Fluid supply must provide head to the local junction and pump inlet, whose
actual elevation is recorded by the geometry. Each upward pipe trunk includes
an upright Mk2 pump after its collector. The compositor must wire those pumps
to its real power network; placing a pump alone is not a claim of powered flow.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import chain
from typing import Literal

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, quat_rotate
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.grid_nets import _node_on_ray, facing_for
from flab2bp.sfy.layout.lattice import Lattice, Node, _belt_boxes
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS, shortest_belt_cm
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeamObj,
    BeltRun,
    FoundationObj,
    LiftObj,
    Link,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    PoleObj,
    Pose,
    SfyPlacement,
    StackLane,
    belt_ends,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import straight
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.validate import beam_box, lift_box, passthrough_box, pipe_chain
from flab2bp.sfy.registry import Port, Registry
from flab2bp.sfy.sections.fluids import _NO_INDICATOR
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionError,
    SectionPort,
    endpoint,
    placement_bounds,
)
from flab2bp.sfy.spec import FOUNDATION_CLASS, SfyBuildSpec

__all__ = ["build_stack_boundaries"]

_BEAM = "Build_Beam_C"
_LIFT_HOLE = "Build_FoundationPassthrough_Lift_C"
_PIPE_HOLE = "Build_FoundationPassthrough_Pipe_C"
_JUNCTION = "Build_PipelineJunction_Cross_C"
_PUMP = "Build_PipelinePumpMk2_C"
_EPS = 0.01


def _ceil(value: float, grid: float) -> float:
    return math.ceil((value - 1e-6) / grid) * grid


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SectionError("stack boundary construction exceeded its deadline", cause="deadline")


def _overlap(a: tuple[Vector, Vector], b: tuple[Vector, Vector]) -> bool:
    return all(a[0][i] < b[1][i] - _EPS and b[0][i] < a[1][i] - _EPS for i in range(3))


def _box(pose: Pose, low: Vector, high: Vector) -> tuple[Vector, Vector]:
    corners = [
        _point(pose, (x, y, z))
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]
    return (
        (min(p[0] for p in corners), min(p[1] for p in corners), min(p[2] for p in corners)),
        (max(p[0] for p in corners), max(p[1] for p in corners), max(p[2] for p in corners)),
    )


def _point(pose: Pose, local: Vector) -> Vector:
    turned = quat_rotate(pose.transform().rotation, local)
    return (pose.x + turned[0], pose.y + turned[1], pose.z + turned[2])


def _direction(start: Vector, finish: Vector, length: float) -> Vector:
    return (
        (finish[0] - start[0]) / length,
        (finish[1] - start[1]) / length,
        (finish[2] - start[2]) / length,
    )


def _advance(start: Vector, direction: Vector, distance: float) -> Vector:
    return (
        start[0] + direction[0] * distance,
        start[1] + direction[1] * distance,
        start[2] + direction[2] * distance,
    )


def _port(registry: Registry, cls: str, heading: Vector, direction: str | None = None) -> Port:
    pose = Pose(0, 0, 0, 0).transform()
    choices = [
        port
        for port in registry.buildables[cls].ports
        if port.kind in ("belt", "pipe") and (direction is None or port.direction == direction)
    ]
    ranked = sorted(
        choices,
        key=lambda port: (
            -sum(a * b for a, b in zip(port_forward(pose, port), heading, strict=True))
        ),
    )
    if (
        not ranked
        or sum(a * b for a, b in zip(port_forward(pose, ranked[0]), heading, strict=True)) < 0.999
    ):
        raise SectionError(
            f"{cls} has no source-backed {direction or 'material'} port facing {heading}",
            cause="data",
        )
    return ranked[0]


@dataclass(frozen=True, slots=True)
class _Lane:
    item: str
    kind: Literal["belt", "pipe"]
    consumed: Fraction
    produced: Fraction
    pose: Pose
    level: float
    span: float
    bounds: tuple[Vector, Vector]
    highest_local_input_cm: float
    lowest_local_output_cm: float | None


class _Builder:
    def __init__(
        self, placement: SfyPlacement, registry: Registry, ids: Iterator[int], deadline: float
    ):
        self.original = placement
        self.registry = registry
        self.ids = ids
        self.deadline = deadline
        self.attachments: list[AttachmentObj] = []
        self.belts: list[BeltRun] = []
        self.lifts: list[LiftObj] = []
        self.pipes: list[PipeRun] = []
        self.pipe_attachments: list[PipeAttachmentObj] = []
        self.holes: list[PassthroughObj] = []
        self.links: list[Link] = []
        self.branches: list[SectionPort] = []
        self.lanes: list[StackLane] = []

    def view(self) -> SfyPlacement:
        return replace(
            self.original,
            attachments=(*self.original.attachments, *self.attachments),
            belts=(*self.original.belts, *self.belts),
            lifts=(*self.original.lifts, *self.lifts),
            pipes=(*self.original.pipes, *self.pipes),
            pipe_attachments=(*self.original.pipe_attachments, *self.pipe_attachments),
            passthroughs=(*self.original.passthroughs, *self.holes),
            links=(*self.original.links, *self.links),
            stack_lanes=tuple(self.lanes),
        )

    def at(self, end: tuple[int, str]) -> tuple[Vector, Vector]:
        return endpoint(self.view(), end[0], end[1], self.registry)

    def belt(
        self,
        source: tuple[int, str],
        target: tuple[int, str],
        cls: str,
        lane: _Lane,
        rate: Fraction,
    ) -> None:
        start, facing = self.at(source)
        finish, normal = self.at(target)
        length = math.dist(start, finish)
        if length < shortest_belt_cm(self.registry.limits) - _EPS:
            raise SectionError(
                f"{lane.item}: stack belt has only {length:g}cm of connector lead", cause="bounds"
            )
        vector = _direction(start, finish, length)
        if (
            sum(vector[i] * facing[i] for i in range(3)) < 0.999
            or sum(-vector[i] * normal[i] for i in range(3)) < 0.999
        ):
            raise SectionError(
                f"{lane.item}: stack belt is not aligned with its real end normals",
                cause="geometry",
            )
        run = BeltRun(next(self.ids), cls, straight(start, vector, length), lane.item, rate)
        entry, exit_ = belt_ends(self.registry, cls)
        self.belts.append(run)
        self.links.extend((Link(source, (run.id, entry)), Link((run.id, exit_), target)))

    def lift(
        self,
        cls: str,
        pose: Pose,
        height: float,
        *,
        top_yaw: float = 0,
        holes: tuple[int | None, int | None] = (None, None),
        boundary_start: bool = False,
        boundary_end: bool = False,
    ) -> tuple[tuple[int, str], tuple[int, str]]:
        low, high = self.registry.limits.lift_min_cm, self.registry.limits.lift_max_cm
        if low is None or high is None or not low <= height <= high:
            raise SectionError(
                f"stack lift height {height:g}cm is outside native {low}..{high}cm", cause="height"
            )
        actor = LiftObj(
            next(self.ids),
            cls,
            pose,
            height,
            top_yaw,
            snapped_passthroughs=holes,
            boundary_start=boundary_start,
            boundary_end=boundary_end,
        )
        self.lifts.append(actor)
        entry, exit_ = belt_ends(self.registry, cls)
        return (actor.id, entry), (actor.id, exit_)


def _approach_node(
    world: Vector,
    facing: Vector,
    node: Node,
    registry: Registry,
    lattice: Lattice,
    bounds: tuple[Vector, Vector] | None = None,
) -> Node:
    """Advance a port's first lattice node by its actual flat routing lead."""
    lead = shortest_belt_cm(registry.limits)
    axis = 0 if abs(facing[0]) > abs(facing[1]) else 1
    sign = 1 if facing[axis] > 0 else -1
    if bounds is not None:
        lift_half = registry.limits.lift_clearance_half_extent_cm
        if lift_half is None:
            raise SectionError("the registry has no lift shaft clearance", cause="data")
        edge = bounds[1 if sign > 0 else 0][axis]
        lead = max(lead, (edge - world[axis]) * sign + lift_half)
    distance = (lattice.world(node)[axis] - world[axis]) * sign
    steps = max(0, math.ceil((lead - distance) / lattice.grid_cm))
    moved = list(node)
    moved[axis] += sign * steps
    return (moved[0], moved[1], moved[2])


def _physical_obstacles(
    placement: SfyPlacement, registry: Registry, thickness: float
) -> Iterator[tuple[Vector, Vector]]:
    """Keep individual occupied volumes, not the empty space between actors."""
    for obj in chain[MachineObj | AttachmentObj | PipeAttachmentObj | PoleObj | FoundationObj](
        placement.machines,
        placement.attachments,
        placement.pipe_attachments,
        placement.poles,
        placement.foundations,
    ):
        # Only the base receives the intended stack holes. Intermediate support
        # slabs remain solid, including their registry's soft clearance volume.
        if (
            isinstance(obj, FoundationObj)
            and obj.class_name == FOUNDATION_CLASS
            and abs(obj.pose.z - thickness / 2) <= _EPS
        ):
            continue
        for box in registry.buildables[obj.class_name].clearance:
            if not box.soft or isinstance(obj, FoundationObj):
                yield box_bounds(box, obj.pose.transform())
    for belt in placement.belts:
        yield from _belt_boxes(belt)
    dynamic_boxes = chain(
        (lift_box(lift, registry) for lift in placement.lifts),
        (beam_box(beam, registry) for beam in placement.beams),
        (passthrough_box(hole, registry) for hole in placement.passthroughs),
        chain.from_iterable(pipe_chain(run, registry) for run in placement.pipes),
    )
    for dynamic_box in dynamic_boxes:
        centre, reach = dynamic_box.centre, dynamic_box.reach
        yield (
            (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
            (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
        )


def _choose_lanes(
    spec: SfyBuildSpec,
    placement: SfyPlacement,
    sections: Sequence[ProductionSection],
    registry: Registry,
    deadline: float,
) -> tuple[_Lane, ...]:
    grid = registry.limits.hologram_grid_cm
    lift_min = registry.limits.lift_min_cm
    lift_half = registry.limits.lift_clearance_half_extent_cm
    if grid is None or lift_min is None or lift_half is None:
        raise SectionError("stack lanes require native grid and lift bounds", cause="data")
    exports = dict(spec.outputs)
    for item, rate in spec.surplus_outputs.items():
        exports[item] = exports.get(item, Fraction()) + rate
    slab = registry.buildables[FOUNDATION_CLASS]
    thickness = slab.height_cm
    if thickness is None:
        raise SectionError("stack foundation has no source-backed thickness", cause="data")
    obstacles = list(_physical_obstacles(placement, registry, thickness))
    occupied: list[tuple[Vector, Vector]] = []
    result = []
    edge = placement.designer.half_cm
    lead = _ceil(shortest_belt_cm(registry.limits), grid)
    measures = _measure(registry, placement.designer)
    turning_half = max(measures.box, lift_half)
    approach_lead = _ceil(max(shortest_belt_cm(registry.limits), measures.lead), grid)
    lattice = Lattice.over(placement.designer, registry)
    for section in sections:
        for port in (*section.inputs, *section.outputs):
            point, normal = endpoint(placement, port.object_id, port.port, registry)
            if port.kind == "belt":
                facing = facing_for(normal, lattice, port.port)
                first = _node_on_ray(point, facing, lattice, port.port)
                node = _approach_node(point, facing, first, registry, lattice, section.bounds)
                finish = lattice.world(node)
            else:
                finish = _advance(point, normal, approach_lead)
            # Preserve just this lead and its first dynamic turn. Expanding a
            # whole section rectangle also forbids unrelated, genuinely empty
            # corners between opposing rows and beside the machines.
            obstacles.append(
                (
                    (
                        min(point[0], finish[0]) - turning_half,
                        min(point[1], finish[1]) - turning_half,
                        min(point[2], finish[2]) - turning_half,
                    ),
                    (
                        max(point[0], finish[0]) + turning_half,
                        max(point[1], finish[1]) + turning_half,
                        max(point[2], finish[2]) + turning_half,
                    ),
                )
            )
    split_in = _port(registry, SPLITTER_CLASS, (-1, 0, 0), "input")
    split_out = _port(registry, SPLITTER_CLASS, (1, 0, 0), "output")
    centre = _ceil(max(abs(split_in.translation[0]), abs(split_out.translation[0])) + lead, grid)
    span = 2 * centre
    clearance = max(200.0, lift_half)
    for item in sorted(set(spec.external_inputs) | set(exports)):
        _check(deadline)
        kind: Literal["belt", "pipe"] = "pipe" if item in spec.fluid_items else "belt"
        peers = [
            port
            for section in sections
            for port in (*section.inputs, *section.outputs)
            if port.item_id == item
        ]
        points = [endpoint(placement, port.object_id, port.port, registry)[0] for port in peers]
        target = tuple(
            sum(point[i] for point in points) / len(points) if points else 0.0 for i in range(3)
        )
        output_heights = [
            point[2]
            for port, point in zip(peers, points, strict=True)
            if port.direction == "output"
        ]
        lowest_output = min(output_heights) if output_heights else None
        level = _ceil(max(thickness / 2 + lift_min, min((p[2] for p in points), default=500)), grid)
        if kind == "pipe":
            level = max(level, 600.0)
            if item in exports:
                if lowest_output is None:
                    raise SectionError(
                        f"{item}: no local fluid producer reaches the stack collector",
                        cause="balance",
                    )
                junction_rise = _port(registry, _JUNCTION, (1, 0, 0)).translation[0]
                level = math.floor((lowest_output - junction_rise) / grid) * grid
        local_span = span if kind == "belt" else 0.0
        branch_class = (
            _JUNCTION
            if kind == "pipe"
            else (SPLITTER_CLASS if item in spec.external_inputs else MERGER_CLASS)
        )
        side_offset = _port(registry, branch_class, (0, 1, 0)).translation[1]
        outward = max(clearance, side_offset + approach_lead + turning_half)
        inward = (
            outward
            if kind == "pipe" and item in exports and item in spec.external_inputs
            else clearance
        )
        candidates = []
        for ix in range(
            math.ceil((-edge + clearance) / grid), math.floor((edge - clearance) / grid) + 1
        ):
            for iy in range(
                math.ceil((-edge + clearance) / grid), math.floor((edge - clearance) / grid) + 1
            ):
                for yaw in (0.0, 90.0, 180.0, 270.0):
                    _check(deadline)
                    pose = Pose(ix * grid, iy * grid, 0, yaw)
                    # A legal straight lead is not enough: the first dynamic
                    # attachment/lift turn must also fit beyond its terminal.
                    bay = _box(
                        pose,
                        (-clearance, -inward, level - 200),
                        (local_span + clearance, outward, level + max(lift_min, 900) + 200),
                    )
                    shaft = _box(
                        pose,
                        (-lift_half, -lift_half, 0),
                        (lift_half, lift_half, placement.designer.height_cm),
                    )
                    if any(bay[0][i] < -edge or bay[1][i] > edge for i in (0, 1)):
                        continue
                    if any(
                        _overlap(bay, obstacle) or _overlap(shaft, obstacle)
                        for obstacle in chain(obstacles, occupied)
                    ):
                        continue
                    branch = _point(pose, (centre if kind == "belt" else 0, 100, level))
                    facing = quat_rotate(pose.transform().rotation, (0, 1, 0))
                    # Prefer a branch facing its actual consumers, not a wall.
                    penalty = max(0.0, -sum((target[i] - branch[i]) * facing[i] for i in (0, 1)))
                    candidates.append(
                        (math.dist(branch, target) + penalty, ix, iy, yaw, pose, bay, shaft)
                    )
        if not candidates:
            raise SectionError(
                f"{item}: no clear {local_span + 2 * clearance:g}cm by "
                f"{inward + outward:g}cm vertical stack bay remains inside "
                f"the {2 * edge:g}cm designer; physical obstacles or port approaches "
                "obstruct every candidate",
                cause="bounds",
            )
        _, _, _, _, pose, bay, shaft = min(candidates, key=lambda value: value[:4])
        occupied.extend((bay, shaft))
        highest_input = max(
            (
                point[2]
                for port, point in zip(peers, points, strict=True)
                if port.direction == "input"
            ),
            default=0.0,
        )
        result.append(
            _Lane(
                item,
                kind,
                spec.external_inputs.get(item, Fraction()),
                exports.get(item, Fraction()),
                pose,
                level,
                local_span,
                bay,
                highest_input,
                lowest_output,
            )
        )
    return tuple(result)


def _belt_lane(
    builder: _Builder,
    spec: SfyBuildSpec,
    lab_map: LabMap,
    lane: _Lane,
    bottom: float,
    top: float,
    thickness: float,
) -> None:
    registry = builder.registry
    tier = spec.belt_tiers[-1]
    if max(lane.consumed, lane.produced) > tier.items_per_second:
        raise SectionError(
            f"{lane.item}: local stack rate exceeds {tier.items_per_second}/s conveyor ceiling",
            cause="capacity",
        )
    belt_cls = machine_class(lab_map, tier.item_id)
    speed = registry.buildables[belt_cls].belt_speed_per_min
    lifts = sorted(
        obj.class_name
        for obj in registry.buildables.values()
        if obj.lift is not None and obj.belt_speed_per_min == speed
    )
    if not lifts:
        raise SectionError(f"{lane.item}: no source-backed lift matches {belt_cls}", cause="data")
    lift_cls = lifts[0]
    low_hole, high_hole = next(builder.ids), next(builder.ids)
    start = _point(lane.pose, (0, 0, bottom))
    finish = _point(lane.pose, (0, 0, top))
    bottom_in, lower_out = builder.lift(
        lift_cls,
        Pose(*start, lane.pose.yaw_deg),
        lane.level - bottom,
        holes=(low_hole, None),
        boundary_start=True,
    )
    rise = registry.limits.lift_min_cm
    assert rise is not None
    upper_in, top_out = builder.lift(
        lift_cls,
        Pose(*_point(lane.pose, (0, 0, lane.level + rise)), lane.pose.yaw_deg),
        top - lane.level - rise,
        holes=(None, high_hole),
        boundary_end=True,
    )
    turn_in, turn_out = builder.lift(
        lift_cls,
        Pose(*_point(lane.pose, (lane.span, 0, lane.level)), lane.pose.yaw_deg + 180),
        rise,
    )
    cls = SPLITTER_CLASS if lane.consumed else MERGER_CLASS
    actor = AttachmentObj(
        next(builder.ids),
        cls,
        Pose(*_point(lane.pose, (lane.span / 2, 0, lane.level)), lane.pose.yaw_deg),
    )
    builder.attachments.append(actor)
    entry = _port(registry, cls, (-1, 0, 0), "input")
    exit_ = _port(registry, cls, (1, 0, 0), "output")
    side = _port(registry, cls, (0, 1, 0), "output" if lane.consumed else "input")
    builder.belt(lower_out, (actor.id, entry.name), belt_cls, lane, tier.items_per_second)
    builder.belt((actor.id, exit_.name), turn_in, belt_cls, lane, tier.items_per_second)
    builder.branches.append(
        SectionPort(
            lane.item,
            lane.consumed or lane.produced,
            actor.id,
            side.name,
            "output" if lane.consumed else "input",
        )
    )
    if lane.consumed and lane.produced:
        collector = AttachmentObj(
            next(builder.ids),
            MERGER_CLASS,
            Pose(
                *_point(lane.pose, (lane.span / 2, 0, lane.level + rise)), lane.pose.yaw_deg + 180
            ),
        )
        builder.attachments.append(collector)
        entry = _port(registry, MERGER_CLASS, (-1, 0, 0), "input")
        exit_ = _port(registry, MERGER_CLASS, (1, 0, 0), "output")
        side = _port(registry, MERGER_CLASS, (0, -1, 0), "input")
        builder.belt(turn_out, (collector.id, entry.name), belt_cls, lane, tier.items_per_second)
        builder.belt((collector.id, exit_.name), upper_in, belt_cls, lane, tier.items_per_second)
        builder.branches.append(
            SectionPort(lane.item, lane.produced, collector.id, side.name, "input")
        )
    else:
        builder.belt(turn_out, upper_in, belt_cls, lane, tier.items_per_second)
    builder.holes.extend(
        (
            PassthroughObj(
                low_hole, _LIFT_HOLE, Pose(*start, 0), thickness, top_connection=bottom_in
            ),
            PassthroughObj(
                high_hole, _LIFT_HOLE, Pose(*finish, 0), thickness, bottom_connection=top_out
            ),
        )
    )
    builder.lanes.append(
        StackLane(
            lane.item,
            "belt",
            bottom_in,
            top_out,
            lane.consumed,
            lane.produced,
            tier.items_per_second,
        )
    )


def _pipe_lane(
    builder: _Builder,
    spec: SfyBuildSpec,
    lab_map: LabMap,
    lane: _Lane,
    bottom: float,
    top: float,
    thickness: float,
) -> None:
    registry = builder.registry
    if not spec.pipe_tiers:
        raise SectionError(
            f"{lane.item}: no rated pipe is available for the stack trunk", cause="capacity"
        )
    tier = spec.pipe_tiers[-1]
    rate = tier.cubic_metres_per_second
    if max(lane.consumed, lane.produced) > rate:
        raise SectionError(
            f"{lane.item}: local flow exceeds {rate}m³/s stack pipe capacity", cause="capacity"
        )
    source_class = machine_class(lab_map, tier.item_id)
    cls = _NO_INDICATOR.get(source_class)
    if cls is None or cls not in registry.buildables:
        raise SectionError(f"unsupported pipeline family {source_class}", cause="unsupported")
    entry, exit_ = pipe_ends(registry, cls)
    junction = PipeAttachmentObj(
        next(builder.ids),
        _JUNCTION,
        Pose(*_point(lane.pose, (0, 0, lane.level)), lane.pose.yaw_deg, 90),
    )
    builder.pipe_attachments.append(junction)
    down = _port(registry, _JUNCTION, (-1, 0, 0))
    up = _port(registry, _JUNCTION, (1, 0, 0))
    side = _port(registry, _JUNCTION, (0, 1, 0))
    other_side = _port(registry, _JUNCTION, (0, -1, 0))
    pump_def = registry.buildables.get(_PUMP)
    if pump_def is None or pump_def.pump_design_head_m is None:
        raise SectionError(
            f"{lane.item}: lifting output requires a Mk2 pump with source-backed design head",
            cause="hydraulic",
        )
    pump_in = _port(registry, _PUMP, (-1, 0, 0), "input")
    pump_out = _port(registry, _PUMP, (1, 0, 0), "output")
    # Snap the pump directly onto the junction: an actual component connection,
    # not a fabricated zero-length pipeline. This avoids imposing an unsourced
    # priming rise between a byproduct collector and its first pump inlet.
    pump_z = lane.level + up.translation[0] - pump_in.translation[0]
    pump = PipeAttachmentObj(
        next(builder.ids), _PUMP, Pose(*_point(lane.pose, (0, 0, pump_z)), lane.pose.yaw_deg, 90)
    )
    builder.pipe_attachments.append(pump)
    inlet = builder.at((pump.id, pump_in.name))[0]
    if lane.produced and (
        lane.lowest_local_output_cm is None or inlet[2] > lane.lowest_local_output_cm + _EPS
    ):
        raise SectionError(
            f"{lane.item}: collector pump inlet at {inlet[2]:g}cm is above "
            f"the lowest producer at {lane.lowest_local_output_cm}cm; "
            "no producer head lift has been established",
            cause="hydraulic",
        )
    if top - inlet[2] > pump_def.pump_design_head_m * 100 + _EPS:
        raise SectionError(
            f"{lane.item}: roof rise {top - inlet[2]:g}cm exceeds Mk2 pump "
            f"design head {pump_def.pump_design_head_m * 100:g}cm",
            cause="hydraulic",
        )
    low_hole, high_hole = next(builder.ids), next(builder.ids)
    lower = _point(lane.pose, (0, 0, bottom))
    upper = _point(lane.pose, (0, 0, top))

    def pipe(
        start: Vector,
        finish: Vector,
        source: tuple[int, str] | None = None,
        target: tuple[int, str] | None = None,
        *,
        first: bool = False,
        last: bool = False,
    ) -> tuple[tuple[int, str], tuple[int, str]]:
        length = math.dist(start, finish)
        mesh_length = registry.buildables[cls].mesh_length_cm
        if mesh_length is None or mesh_length <= 0:
            raise SectionError(f"{cls}: pipe minimum requires native mesh length", cause="data")
        minimum = mesh_length / 2
        if length <= minimum:
            raise SectionError(
                f"{lane.item}: pipe segment {length:g}cm is not above native {minimum:g}cm minimum",
                cause="bounds",
            )
        count = math.ceil(length / registry.limits.pipe_max_spline_cm)
        direction = _direction(start, finish, length)
        first_end = None
        previous = source
        for index in range(count):
            at = _advance(start, direction, length * index / count)
            run = PipeRun(
                next(builder.ids),
                cls,
                straight(at, direction, length / count),
                lane.item,
                rate,
                boundary_start=first and index == 0,
                boundary_end=last and index == count - 1,
                snapped_passthroughs=(
                    low_hole if first and index == 0 else None,
                    high_hole if last and index == count - 1 else None,
                ),
            )
            builder.pipes.append(run)
            begin, end = (run.id, entry), (run.id, exit_)
            if first_end is None:
                first_end = begin
            if previous is not None:
                builder.links.append(Link(previous, begin))
            previous = end
        assert first_end is not None and previous is not None
        if target is not None:
            builder.links.append(Link(previous, target))
        return first_end, previous

    bottom_end, _ = pipe(
        lower, builder.at((junction.id, down.name))[0], target=(junction.id, down.name), first=True
    )
    builder.links.append(Link((junction.id, up.name), (pump.id, pump_in.name)))
    _, top_end = pipe(
        builder.at((pump.id, pump_out.name))[0], upper, source=(pump.id, pump_out.name), last=True
    )
    if lane.consumed:
        builder.branches.append(
            SectionPort(lane.item, lane.consumed, junction.id, side.name, "output", "pipe")
        )
    if lane.produced:
        builder.branches.append(
            SectionPort(
                lane.item,
                lane.produced,
                junction.id,
                other_side.name if lane.consumed else side.name,
                "input",
                "pipe",
            )
        )
    builder.holes.extend(
        (
            PassthroughObj(
                low_hole, _PIPE_HOLE, Pose(*lower, 0), thickness, top_connection=bottom_end
            ),
            PassthroughObj(
                high_hole, _PIPE_HOLE, Pose(*upper, 0), thickness, bottom_connection=top_end
            ),
        )
    )
    required_head = max(0.0, max(inlet[2], lane.highest_local_input_cm) - bottom) / 100
    builder.lanes.append(
        StackLane(
            lane.item,
            "pipe",
            bottom_end,
            top_end,
            lane.consumed,
            lane.produced,
            rate,
            required_input_head_m=required_head if lane.consumed else 0.0,
        )
    )


def _frame(
    placement: SfyPlacement,
    lanes: Sequence[_Lane],
    registry: Registry,
    ids: Iterator[int],
    roof_z: float,
    pitch: float,
) -> SfyPlacement:
    foundation = registry.buildables[FOUNDATION_CLASS]
    beam = registry.buildables[_BEAM]
    side, depth, thickness = foundation.width_cm, foundation.depth_cm, foundation.height_cm
    size, limit = beam.beam_size_cm, beam.beam_max_length_cm
    if side is None or depth is None or thickness is None or size is None or limit is None:
        raise SectionError("stack frame lacks native slab or beam dimensions", cause="data")
    low, high = placement_bounds(placement, registry)
    boxes = [(low, high), *(lane.bounds for lane in lanes)]
    half = placement.designer.half_cm
    x0 = -half + math.floor((min(box[0][0] for box in boxes) + half) / side) * side
    y0 = -half + math.floor((min(box[0][1] for box in boxes) + half) / depth) * depth
    x1 = -half + _ceil(max(box[1][0] for box in boxes) + half, side)
    y1 = -half + _ceil(max(box[1][1] for box in boxes) + half, depth)
    if x0 < -half - _EPS or y0 < -half - _EPS or x1 > half + _EPS or y1 > half + _EPS:
        raise SectionError(
            f"full stack slab envelope ({x0:g},{y0:g})..({x1:g},{y1:g}) "
            f"exceeds designer ±{half:g}cm",
            cause="bounds",
        )
    slabs = list(placement.foundations)
    existing = {(obj.class_name, obj.pose) for obj in slabs}
    for ix in range(round((x1 - x0) / side)):
        for iy in range(round((y1 - y0) / depth)):
            for z in (thickness / 2, roof_z):
                pose = Pose(x0 + (ix + 0.5) * side, y0 + (iy + 0.5) * depth, z, 0)
                if (FOUNDATION_CLASS, pose) not in existing:
                    slabs.append(FoundationObj(next(ids), FOUNDATION_CLASS, pose))
    beams = list(placement.beams)

    def member(start: Vector, finish: Vector) -> None:
        length = math.dist(start, finish)
        count = math.ceil(length / limit)
        delta = _direction(start, finish, length)
        yaw = math.degrees(math.atan2(delta[1], delta[0]))
        pitch_deg = math.degrees(math.asin(delta[2]))
        for index in range(count):
            point = _advance(start, delta, length * index / count)
            beams.append(BeamObj(next(ids), _BEAM, Pose(*point, yaw, pitch_deg), length / count))

    corners = (
        (x0 + size / 2, y0 + size / 2),
        (x1 - size / 2, y0 + size / 2),
        (x1 - size / 2, y1 - size / 2),
        (x0 + size / 2, y1 - size / 2),
    )
    for z in (thickness / 2, roof_z):
        for index, (x, y) in enumerate(corners):
            nx, ny = corners[(index + 1) % 4]
            member((x, y, z), (nx, ny, z))
    for x, y in corners:
        member((x, y, thickness), (x, y, pitch))
    return replace(placement, foundations=tuple(slabs), beams=tuple(beams))


def build_stack_boundaries(
    spec: SfyBuildSpec,
    placement: SfyPlacement,
    sections: Sequence[ProductionSection],
    registry: Registry,
    lab_map: LabMap,
    *,
    ids: Iterator[int],
    deadline: float,
) -> tuple[SfyPlacement, tuple[SectionPort, ...]]:
    """Add full frames and genuine pass-through trunks; return local routing ends.

    Boundary rates on StackLane are local consumption/export, not the unknown
    upstream stack load. Trunk run metadata records the funded transport ceiling.
    Consumers must limit the number of repeated modules to that common capacity.
    No cross-blueprint actor/component reference is authored.
    """
    _check(deadline)
    if placement.stack_lanes or placement.stack_height_cm:
        raise SectionError("placement already has stack boundaries", cause="geometry")
    lanes = _choose_lanes(spec, placement, sections, registry, deadline)
    thickness = registry.buildables[FOUNDATION_CLASS].height_cm
    grid = registry.limits.hologram_grid_cm
    gap = registry.limits.lift_min_cm
    if thickness is None or grid is None or gap is None:
        raise SectionError(
            "stack pitch requires native slab, grid and lift dimensions", cause="data"
        )
    _, high = placement_bounds(placement, registry)
    bottom = thickness / 2
    roof_requirements = [high[2] + 200 + thickness / 2]
    roof_requirements.extend(
        lane.level + (700 if lane.kind == "pipe" else 2 * gap) for lane in lanes
    )
    top = _ceil(max(roof_requirements), grid) + thickness / 2
    pitch = top - bottom + gap
    if pitch > placement.designer.height_cm + _EPS:
        raise SectionError(
            f"stack envelope needs {pitch:g}cm including {gap:g}cm manual seam "
            f"but designer height is {placement.designer.height_cm:g}cm",
            cause="height",
        )
    builder = _Builder(placement, registry, ids, deadline)
    for lane in lanes:
        _check(deadline)
        if lane.kind == "belt":
            _belt_lane(builder, spec, lab_map, lane, bottom, top, thickness)
        else:
            _pipe_lane(builder, spec, lab_map, lane, bottom, top, thickness)
    result = _frame(builder.view(), lanes, registry, ids, top, pitch)
    return replace(result, stack_height_cm=pitch, stack_connection_gap_cm=gap), tuple(
        builder.branches
    )
