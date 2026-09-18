"""Four-sided, frame-mounted native wall outlets and their common power circuit."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import replace
from itertools import chain

from flab2bp.sfy.geometry import quat_rotate, world_port
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeamObj,
    FoundationObj,
    Link,
    MachineObj,
    PipeAttachmentObj,
    PoleObj,
    Pose,
    SfyPlacement,
    SignObj,
    WireObj,
)
from flab2bp.sfy.layout.power import WIRE_CLASS, power_port, wire_limit_cm
from flab2bp.sfy.layout.validate import (
    WorldBox,
    _belt_chain,
    _penetration,
    _place_box,
    attachment_boxes,
    beam_box,
    lift_box,
    passthrough_box,
    pipe_chain,
)
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.sections.model import SectionError

__all__ = ["add_wall_power"]

SOCKET_CLASS = "Build_PowerPoleWallDouble_Mk2_C"
PAINTED_BEAM_CLASS = "Build_Beam_Painted_C"
# Both references mount the socket actor 20cm from its beam's centerline.
SOCKET_MOUNT_OFFSET_CM = 20.0
_EPS = 0.01


def frame_socket_margin(registry: Registry) -> float:
    """Space from a post centerline to the socket's outermost native envelope."""
    buildable = registry.buildables[SOCKET_CLASS]
    if not buildable.clearance:
        raise SectionError("wall socket lacks native clearance data", cause="data")
    return SOCKET_MOUNT_OFFSET_CM + max(box.max[0] for box in buildable.clearance) + _EPS


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SectionError("wall power construction exceeded its deadline", cause="deadline")


def _obstacles(placement: SfyPlacement, registry: Registry) -> list[WorldBox]:
    boxes: list[WorldBox] = []
    for obj in chain[MachineObj | AttachmentObj | PipeAttachmentObj | FoundationObj | SignObj](
        placement.machines,
        placement.attachments,
        placement.pipe_attachments,
        placement.foundations,
        placement.signs,
    ):
        boxes.extend(
            _place_box(box, obj.pose.transform(), obj.id, f"power obstacle {obj.id}")
            for box in registry.buildables[obj.class_name].clearance
        )
    for obj in placement.attachments:
        boxes.extend(attachment_boxes(obj, registry))
    boxes.extend(beam_box(beam, registry) for beam in placement.beams)
    boxes.extend(lift_box(lift, registry) for lift in placement.lifts)
    boxes.extend(passthrough_box(hole, registry) for hole in placement.passthroughs)
    for belt in placement.belts:
        boxes.extend(_belt_chain(belt))
    for pipe in placement.pipes:
        boxes.extend(pipe_chain(pipe, registry))
    return boxes


def _posts(placement: SfyPlacement) -> list[tuple[BeamObj, float, float]]:
    result: list[tuple[BeamObj, float, float]] = []
    for beam in placement.beams:
        if beam.class_name != PAINTED_BEAM_CLASS:
            continue
        delta = quat_rotate(beam.pose.transform().rotation, (beam.length_cm, 0, 0))
        if abs(delta[0]) > _EPS or abs(delta[1]) > _EPS:
            continue
        bottom, top = sorted((beam.pose.z, beam.pose.z + delta[2]))
        result.append((beam, bottom, top))
    if not result:
        raise SectionError("wall power requires native painted frame posts", cause="power")
    return result


def _sockets(
    placement: SfyPlacement,
    registry: Registry,
    count: int,
    ids: Iterator[int],
    deadline: float,
) -> tuple[PoleObj, ...]:
    posts = _posts(placement)
    x0 = min(beam.pose.x for beam, _, _ in posts)
    x1 = max(beam.pose.x for beam, _, _ in posts)
    y0 = min(beam.pose.y for beam, _, _ in posts)
    y1 = max(beam.pose.y for beam, _, _ in posts)
    # One native mounting face on each side, alternating corners. +X stays out
    # of the central sign band. More capacity descends the same frame posts.
    sides = ((0, x1, y0, 0.0), (1, y1, x1, 90.0), (0, x0, y1, 180.0), (1, y0, x0, -90.0))
    grid = registry.limits.hologram_grid_cm
    if grid is None or grid <= 0:
        raise SectionError("wall power requires the native hologram grid", cause="data")
    socket = registry.buildables[SOCKET_CLASS]
    obstacles = _obstacles(placement, registry)
    output: list[PoleObj] = []
    half = placement.designer.half_cm + _EPS
    for index in range(count):
        _check(deadline)
        axis, edge, preferred, yaw = sides[index % 4]
        outward = quat_rotate(
            Pose(0, 0, 0, yaw).transform().rotation, (SOCKET_MOUNT_OFFSET_CM, 0, 0)
        )
        eligible = sorted(
            (post for post in posts if abs(post[0].pose.location[axis] - edge) <= _EPS),
            key=lambda post: (abs(post[0].pose.location[1 - axis] - preferred), -post[2]),
        )
        found = None
        for beam, bottom, top in eligible:
            z = math.floor((top - grid) / grid) * grid
            while z > bottom + _EPS:
                _check(deadline)
                pose = Pose(beam.pose.x + outward[0], beam.pose.y + outward[1], z, yaw, 0, -45)
                boxes = tuple(
                    _place_box(box, pose.transform(), -1, "wall socket") for box in socket.clearance
                )
                within = all(
                    -half <= point[0] <= half
                    and -half <= point[1] <= half
                    and -_EPS <= point[2] <= placement.designer.height_cm + _EPS
                    for box in boxes
                    for point in box.corners()
                )
                # A double wall outlet passes through its actual mounting post;
                # all other actors, including other beams, remain obstacles.
                if within and not any(
                    obstacle.owner != beam.id and _penetration(box, obstacle) > _EPS
                    for box in boxes
                    for obstacle in obstacles
                ):
                    found = PoleObj(next(ids), SOCKET_CLASS, pose)
                    obstacles.extend(replace(box, owner=found.id) for box in boxes)
                    break
                z -= grid
            if found is not None:
                break
        if found is None:
            raise SectionError(
                f"no collision-free wall socket mounting position on frame side {yaw:g} degrees",
                cause="power",
            )
        output.append(found)
    return tuple(output)


def add_wall_power(
    placement: SfyPlacement,
    registry: Registry,
    *,
    ids: Iterator[int],
    deadline: float,
) -> SfyPlacement:
    """Wire all machines and powered pumps onto four-sided native wall sockets.

    Native reciprocal mHiddenConnections connect each double outlet's two
    sides. The inner PowerConnection2 carries a closed perimeter ring and local
    consumers. PowerConnection1 remains available for the player's external
    supply/adjacent modules; no cross-blueprint references or floor poles exist.
    """
    _check(deadline)
    if placement.poles or placement.wires:
        raise SectionError("wall power requires an unwired placement", cause="power")
    ports = {port.name: port for port in registry.buildables[SOCKET_CLASS].ports}
    if set(ports) != {"PowerConnection1", "PowerConnection2"}:
        raise SectionError("wall socket lacks its native pair of power connections", cause="data")
    inner = ports["PowerConnection2"]
    if inner.max_connections is None or inner.max_connections <= 2:
        raise SectionError("wall socket has no capacity beyond its ring links", cause="data")
    budget = inner.max_connections - 2
    loads: list[tuple[MachineObj | PipeAttachmentObj, str, tuple[float, float, float]]] = []
    for obj in chain[MachineObj | PipeAttachmentObj](
        placement.machines, placement.pipe_attachments
    ):
        if any(port.kind == "power" for port in registry.buildables[obj.class_name].ports):
            port = power_port(registry, obj.class_name)
            loads.append((obj, port.name, world_port(obj.pose.transform(), port)))
    sockets = _sockets(placement, registry, max(4, math.ceil(len(loads) / budget)), ids, deadline)
    limit = wire_limit_cm(registry, WIRE_CLASS)
    points = [world_port(socket.pose.transform(), inner) for socket in sockets]
    wires: list[WireObj] = []
    # Follow perimeter order even when several sockets share one side.
    ring = sorted(range(len(sockets)), key=lambda i: (i % 4, sockets[i].pose.z))
    for index, a in enumerate(ring):
        b = ring[(index + 1) % len(ring)]
        if math.dist(points[a], points[b]) > limit:
            raise SectionError("wall socket ring exceeds the native wire length", cause="power")
        wires.append(
            WireObj(
                next(ids),
                WIRE_CLASS,
                Link(
                    (sockets[a].id, inner.name),
                    (sockets[b].id, inner.name),
                ),
            )
        )
    available = [budget] * len(sockets)
    choices = [
        sorted(
            (i for i, point in enumerate(points) if math.dist(location, point) <= limit),
            key=lambda i: (math.dist(location, points[i]), sockets[i].id),
        )
        for _, _, location in loads
    ]
    for load_index in sorted(range(len(loads)), key=lambda i: (len(choices[i]), loads[i][0].id)):
        _check(deadline)
        obj, name, _ = loads[load_index]
        selected = next((i for i in choices[load_index] if available[i]), None)
        if selected is None:
            raise SectionError(
                f"{obj.class_name} {obj.id} has no wall socket within "
                "native wire length and capacity",
                cause="power",
            )
        available[selected] -= 1
        wires.append(
            WireObj(
                next(ids),
                WIRE_CLASS,
                Link(
                    (sockets[selected].id, inner.name),
                    (obj.id, name),
                ),
            )
        )
    return replace(placement, poles=sockets, wires=tuple(wires))
