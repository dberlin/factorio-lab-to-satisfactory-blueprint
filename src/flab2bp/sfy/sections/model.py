"""Connected production geometry and its genuine, rated open connections."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from fractions import Fraction
from itertools import chain
from typing import Literal

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, quat_rotate, world_port
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeamObj,
    BeltRun,
    FoundationObj,
    LiftObj,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    PoleObj,
    Pose,
    SfyPlacement,
    SignObj,
    WireObj,
    belt_ends,
    lift_geometry,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import yaw_quaternion
from flab2bp.sfy.layout.validate import (
    BELT_CLEARANCE_HALF_HEIGHT_CM,
    BELT_CLEARANCE_HALF_WIDTH_CM,
    attachment_boxes,
    beam_box,
    lift_box,
    passthrough_box,
    pipe_chain,
)
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import SfyMachineGroup


class SectionError(ValueError):
    """An unsupported or physically impossible section, with a stable cause."""

    def __init__(self, detail: str, *, cause: str = "geometry") -> None:
        super().__init__(detail)
        self.cause: str = cause


@dataclass(frozen=True, slots=True)
class SectionPort:
    """Rated material port: items/s for belts, m³/s for pipes; no litres conversion."""

    item_id: str
    items_per_second: Fraction
    object_id: int
    port: str
    direction: Literal["input", "output"]
    kind: Literal["belt", "pipe"] = "belt"


@dataclass(frozen=True, slots=True)
class ProductionSection:
    group: SfyMachineGroup
    placement: SfyPlacement
    inputs: tuple[SectionPort, ...]
    outputs: tuple[SectionPort, ...]
    bounds: tuple[Vector, Vector]


def endpoint(
    placement: SfyPlacement, object_id: int, port: str, registry: Registry
) -> tuple[Vector, Vector]:
    """World position and outward normal; conveyor flow comes from the registry."""
    obj = placement.by_id(object_id)
    if isinstance(obj, (BeltRun, LiftObj, PipeRun)):
        entry, exit_ = (
            pipe_ends(registry, obj.class_name)
            if isinstance(obj, PipeRun)
            else belt_ends(registry, obj.class_name)
        )
        if port not in (entry, exit_):
            raise KeyError(f"{obj.class_name} has no conveyor connection {port!r}")
        if isinstance(obj, LiftObj):
            geometry = lift_geometry(registry, obj.class_name)
            return obj.bottom_end(geometry) if port == entry else obj.top_end(geometry)
        tangent = obj.points[0][2] if port == entry else obj.points[-1][1]
        length = math.sqrt(sum(value * value for value in tangent))
        if length == 0.0:
            raise ValueError(f"transport {object_id}.{port} has a zero endpoint tangent")
        sign = -1.0 if port == entry else 1.0
        position = obj.start if port == entry else obj.end
        if isinstance(obj, PipeRun):
            hole_id = obj.snapped_passthroughs[0 if port == entry else 1]
            if hole_id is not None:
                hole = placement.by_id(hole_id)
                if not isinstance(hole, PassthroughObj):
                    raise ValueError(f"pipe {object_id} snaps to non-passthrough {hole_id}")
                position = hole.pose.location
        return (
            position,
            (sign * tangent[0] / length, sign * tangent[1] / length, sign * tangent[2] / length),
        )
    if isinstance(obj, WireObj):
        raise KeyError(f"power wire {object_id} has no material connection")
    definition = next(p for p in registry.buildables[obj.class_name].ports if p.name == port)
    transform = obj.pose.transform()
    return world_port(transform, definition), port_forward(transform, definition)


def interface_bounds(
    placement: SfyPlacement, object_id: int, port: str, registry: Registry
) -> tuple[Vector, Vector]:
    """Bound the connected mouth's body, not unrelated geometry in its section."""
    obj = placement.by_id(object_id)
    world, _ = endpoint(placement, object_id, port, registry)
    if isinstance(obj, BeltRun):
        half = BELT_CLEARANCE_HALF_WIDTH_CM
        return (
            (world[0] - half, world[1] - half, world[2]),
            (world[0] + half, world[1] + half, world[2]),
        )
    if isinstance(obj, (LiftObj, AttachmentObj)):
        dynamic = (
            (lift_box(obj, registry),)
            if isinstance(obj, LiftObj)
            else attachment_boxes(obj, registry)
        )
        corners = [point for box in dynamic for point in box.corners()]
        low = [min(point[axis] for point in corners) for axis in range(3)]
        high = [max(point[axis] for point in corners) for axis in range(3)]
        return (low[0], low[1], low[2]), (high[0], high[1], high[2])
    if isinstance(obj, (WireObj, PipeRun)):
        raise SectionError("a conveyor interface requires a conveyor mouth", cause="data")
    boxes = [
        box_bounds(box, obj.pose.transform())
        for box in registry.buildables[obj.class_name].clearance
        if not box.soft
    ]
    low = [min((box[0][axis] for box in boxes), default=world[axis]) for axis in range(3)]
    high = [max((box[1][axis] for box in boxes), default=world[axis]) for axis in range(3)]
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def transform_placement(
    placement: SfyPlacement, offset: Vector, yaw_deg: float = 0.0
) -> SfyPlacement:
    """Rigidly move complete geometry without changing IDs, rates or connectivity."""
    rotation = yaw_quaternion(yaw_deg)

    def point(value: Vector) -> Vector:
        turned = quat_rotate(rotation, value)
        return (turned[0] + offset[0], turned[1] + offset[1], turned[2] + offset[2])

    def actor[
        T: (
            MachineObj,
            AttachmentObj,
            LiftObj,
            PoleObj,
            FoundationObj,
            PipeAttachmentObj,
            BeamObj,
            PassthroughObj,
            SignObj,
        )
    ](obj: T) -> T:
        x, y, z, w = obj.pose.transform().rotation
        s, c = rotation[2], rotation[3]
        turn = (c * x - s * y, c * y + s * x, c * z + s * w, c * w - s * z)
        return replace(
            obj,
            pose=Pose.from_transform(Transform(turn, point(obj.pose.location), (1.0, 1.0, 1.0))),
        )

    return replace(
        placement,
        machines=tuple(actor(obj) for obj in placement.machines),
        attachments=tuple(actor(obj) for obj in placement.attachments),
        lifts=tuple(actor(obj) for obj in placement.lifts),
        poles=tuple(actor(obj) for obj in placement.poles),
        foundations=tuple(actor(obj) for obj in placement.foundations),
        pipe_attachments=tuple(actor(obj) for obj in placement.pipe_attachments),
        beams=tuple(actor(obj) for obj in placement.beams),
        passthroughs=tuple(actor(obj) for obj in placement.passthroughs),
        signs=tuple(actor(obj) for obj in placement.signs),
        pipes=tuple(
            replace(
                run,
                points=tuple(
                    (point(at), quat_rotate(rotation, arrive), quat_rotate(rotation, leave))
                    for at, arrive, leave in run.points
                ),
            )
            for run in placement.pipes
        ),
        belts=tuple(
            replace(
                belt,
                points=tuple(
                    (point(at), quat_rotate(rotation, arrive), quat_rotate(rotation, leave))
                    for at, arrive, leave in belt.points
                ),
            )
            for belt in placement.belts
        ),
    )


def placement_bounds(placement: SfyPlacement, registry: Registry) -> tuple[Vector, Vector]:
    """Bound machines, attachments, belts and lifts, including soft clearance."""
    lows: list[Vector] = []
    highs: list[Vector] = []
    for obj in chain[
        MachineObj | AttachmentObj | PoleObj | FoundationObj | PipeAttachmentObj | SignObj
    ](
        placement.machines,
        placement.attachments,
        placement.poles,
        placement.foundations,
        placement.pipe_attachments,
        placement.signs,
    ):
        for box in registry.buildables[obj.class_name].clearance:
            low, high = box_bounds(box, obj.pose.transform())
            lows.append(low)
            highs.append(high)
    # Our internal splines are straight or quarter circles: their endpoint AABB
    # contains their centreline. Include the swept belt width, not only actors.
    half = (
        BELT_CLEARANCE_HALF_WIDTH_CM,
        BELT_CLEARANCE_HALF_WIDTH_CM,
        BELT_CLEARANCE_HALF_HEIGHT_CM,
    )
    for belt in placement.belts:
        for at, _, _ in belt.points:
            lows.append((at[0] - half[0], at[1] - half[1], at[2] - half[2]))
            highs.append((at[0] + half[0], at[1] + half[1], at[2] + half[2]))
    for lift in placement.lifts:
        corners = lift_box(lift, registry).corners()
        lows.append(
            (min(p[0] for p in corners), min(p[1] for p in corners), min(p[2] for p in corners))
        )
        highs.append(
            (max(p[0] for p in corners), max(p[1] for p in corners), max(p[2] for p in corners))
        )
    dynamic_boxes = chain(
        chain.from_iterable(attachment_boxes(obj, registry) for obj in placement.attachments),
        (beam_box(beam, registry) for beam in placement.beams),
        (passthrough_box(hole, registry) for hole in placement.passthroughs),
        chain.from_iterable(pipe_chain(run, registry) for run in placement.pipes),
    )
    for dynamic_box in dynamic_boxes:
        corners = dynamic_box.corners()
        lows.append(
            (min(p[0] for p in corners), min(p[1] for p in corners), min(p[2] for p in corners))
        )
        highs.append(
            (max(p[0] for p in corners), max(p[1] for p in corners), max(p[2] for p in corners))
        )
    if not lows:
        raise SectionError("a production section cannot contain no geometry")
    return (
        (min(p[0] for p in lows), min(p[1] for p in lows), min(p[2] for p in lows)),
        (max(p[0] for p in highs), max(p[1] for p in highs), max(p[2] for p in highs)),
    )
