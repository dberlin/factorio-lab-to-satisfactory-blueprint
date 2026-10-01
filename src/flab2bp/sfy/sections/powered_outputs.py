"""Native powered output collectors and the compact factory's structural frame.

Each producer feeds a real horizontal Mk2 pump at its own outlet elevation.
Occupied machine/coal boxes determine the exterior risers; native port offsets
and mesh dimensions determine every bend, junction and continuation. The caller
retains responsibility for full physical validation and the common power stage.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction

from flab2bp.layout.budget import expired
from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, world_port
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.model import (
    BeamObj,
    FoundationObj,
    Link,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    Pose,
    SfyPlacement,
    StackLane,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import (
    QUARTER_TURN_TANGENT,
    SplinePoint,
    concat,
    spline_length,
    straight,
)
from flab2bp.sfy.layout.validate import (
    WorldBox,
    _belt_chain,
    attachment_boxes,
    lift_box,
    pipe_chain,
)
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.sections.model import SectionError, SectionPort, endpoint
from flab2bp.sfy.sections.pipe_routes import _turn_radius
from flab2bp.sfy.sections.power import PAINTED_BEAM_CLASS, frame_socket_margin
from flab2bp.sfy.spec import FOUNDATION_CLASS

__all__ = ["add_powered_outputs", "add_compact_frame"]

_JUNCTION = "Build_PipelineJunction_Cross_C"
_PUMP = "Build_PipelinePumpMk2_C"
_HOLE = "Build_FoundationPassthrough_Pipe_C"
type _Ref = tuple[int, str]
type _Bounds = tuple[Vector, Vector]
type _RiserPhase = tuple[float, float, float, float, float]


def _check(deadline: float) -> None:
    if expired(deadline, time.monotonic):
        raise SectionError(
            "compact output/frame construction exceeded its deadline", cause="deadline"
        )


def _shift(point: Vector, heading: Vector, distance: float) -> Vector:
    return (
        point[0] + heading[0] * distance,
        point[1] + heading[1] * distance,
        point[2] + heading[2] * distance,
    )


def _arc(
    start: Vector, incoming: Vector, outgoing: Vector, radius: float
) -> tuple[SplinePoint, ...]:
    finish = _shift(_shift(start, incoming, radius), outgoing, radius)
    pull = radius * QUARTER_TURN_TANGENT
    before = (incoming[0] * pull, incoming[1] * pull, incoming[2] * pull)
    after = (outgoing[0] * pull, outgoing[1] * pull, outgoing[2] * pull)
    return ((start, before, before), (finish, after, after))


def _port(definition: Buildable, heading: Vector, direction: str | None = None) -> Port:
    identity = Pose(0, 0, 0, 0).transform()
    for port in definition.ports:
        if port.kind != "pipe" or (direction is not None and port.direction != direction):
            continue
        normal = port_forward(identity, port)
        if sum(normal[i] * heading[i] for i in range(3)) > 0.999:
            return port
    raise SectionError(
        f"{definition.class_name}: missing native {direction or 'pipe'} port {heading}",
        cause="data",
    )


def _bounds(box: WorldBox) -> _Bounds:
    corners = box.corners()
    return (
        (min(p[0] for p in corners), min(p[1] for p in corners), min(p[2] for p in corners)),
        (max(p[0] for p in corners), max(p[1] for p in corners), max(p[2] for p in corners)),
    )


def _machine_bounds(placement: SfyPlacement, registry: Registry) -> tuple[_Bounds, ...]:
    return tuple(
        box_bounds(box, machine.pose.transform())
        for machine in placement.machines
        for box in registry.buildables[machine.class_name].clearance
        if not box.soft
    )


def _transport_bounds(placement: SfyPlacement, registry: Registry) -> tuple[_Bounds, ...]:
    boxes: list[WorldBox] = []
    for attachment in placement.attachments:
        boxes.extend(attachment_boxes(attachment, registry))
    boxes.extend(lift_box(lift, registry) for lift in placement.lifts)
    for belt in placement.belts:
        boxes.extend(_belt_chain(belt))
    for pipe in placement.pipes:
        boxes.extend(pipe_chain(pipe, registry))
    native_bounds = tuple(
        box_bounds(box, attachment.pose.transform())
        for attachment in placement.pipe_attachments
        for box in registry.buildables[attachment.class_name].clearance
        if not box.soft
    )
    return (*(_bounds(box) for box in boxes), *native_bounds)


def _intersects_riser(
    bounds: _Bounds, x: float, y: float, low_z: float, high_z: float, radius: float
) -> bool:
    low, high = bounds
    return (
        low[0] < x + radius
        and high[0] > x - radius
        and low[1] < y + radius
        and high[1] > y - radius
        and low[2] < high_z + radius
        and high[2] > low_z - radius
    )


def _clear_riser_y(
    y: float,
    sign: int,
    grid: float,
    obstacles: Sequence[_Bounds],
    phases: Sequence[_RiserPhase],
) -> float:
    """Project each native riser/cap/junction phase into one occupied-Y union."""
    padding = grid / 20
    intervals = sorted(
        (low[1] - half_y - padding, high[1] + half_y + padding)
        for low, high in obstacles
        for low_x, high_x, low_z, high_z, half_y in phases
        if low[0] < high_x and high[0] > low_x and low[2] < high_z and high[2] > low_z
    )
    merged: list[tuple[float, float]] = []
    for low, high in intervals:
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    for low, high in merged:
        if low <= y <= high:
            boundary = low if sign < 0 else high
            phase = grid / 20
            return sign * math.ceil(sign * boundary / phase) * phase
    return y


def _support_centres(
    low: float, high: float, side: float, half: float, grid: float
) -> tuple[float, ...]:
    # Physically cover rotation tails at grid edges, instead of suppressing them.
    padding = grid / 1000
    low = max(-half, math.floor((low - padding) / grid) * grid)
    high = min(half, math.ceil((high + padding) / grid) * grid)
    if high - low <= side:
        return (max(-half + side / 2, min(half - side / 2, (low + high) / 2)),)
    count = math.ceil((high - low) / side)
    first, last = low + side / 2, high - side / 2
    return tuple(first + (last - first) * index / (count - 1) for index in range(count))


def _support_tiles(placement: SfyPlacement, registry: Registry) -> set[tuple[float, float, float]]:
    grid = registry.limits.hologram_grid_cm
    if grid is None or grid <= 0:
        raise SectionError("compact supports require native grid dimensions", cause="data")
    definition = registry.buildables[FOUNDATION_CLASS]
    if definition.width_cm is None or definition.depth_cm is None:
        raise SectionError("compact supports require native slab dimensions", cause="data")
    top = definition.clearance[0].max[2]
    half = placement.designer.half_cm
    tiles: set[tuple[float, float, float]] = set()
    for machine in placement.machines:
        bounds = [
            box_bounds(box, machine.pose.transform())
            for box in registry.buildables[machine.class_name].clearance
            if not box.soft
        ]
        # One supported footprint per actor. Re-tiling every overlapping
        # gearbox/body projection creates redundant native foundation actors.
        xs = _support_centres(
            min(low[0] for low, _ in bounds),
            max(high[0] for _, high in bounds),
            definition.width_cm,
            half,
            grid,
        )
        ys = _support_centres(
            min(low[1] for low, _ in bounds),
            max(high[1] for _, high in bounds),
            definition.depth_cm,
            half,
            grid,
        )
        tiles.update((x, y, machine.pose.z - top) for x in xs for y in ys)
    return tiles


@dataclass(frozen=True, slots=True)
class _Source:
    interface: SectionPort
    pipe: PipeRun
    machine: MachineObj
    point: Vector
    normal: Vector
    side: int


class _Outputs:
    def __init__(
        self,
        placement: SfyPlacement,
        registry: Registry,
        ids: Iterator[int],
        deadline: float,
        definition: Buildable,
        item: str,
    ) -> None:
        self.placement: SfyPlacement = placement
        self.registry: Registry = registry
        self.ids: Iterator[int] = ids
        self.deadline: float = deadline
        self.definition: Buildable = definition
        self.item: str = item
        self.nodes: list[PipeAttachmentObj] = []
        self.pipes: list[PipeRun] = []
        self.links: list[Link] = []
        self.leads: dict[int, PipeRun] = {}
        self.by_id: dict[int, PipeAttachmentObj] = {}
        self.entry: str
        self.exit: str
        self.entry, self.exit = pipe_ends(registry, definition.class_name)

    def node(self, cls: str, pose: Pose) -> PipeAttachmentObj:
        _check(self.deadline)
        node = PipeAttachmentObj(next(self.ids), cls, pose)
        self.nodes.append(node)
        self.by_id[node.id] = node
        return node

    def at(self, ref: _Ref) -> tuple[Vector, Vector]:
        node = self.by_id.get(ref[0])
        if node is None:
            return endpoint(self.placement, ref[0], ref[1], self.registry)
        port = next(p for p in self.registry.buildables[node.class_name].ports if p.name == ref[1])
        return world_port(node.pose.transform(), port), port_forward(node.pose.transform(), port)

    def pipe(
        self,
        points: tuple[SplinePoint, ...],
        rate: Fraction,
        source: _Ref | None = None,
        target: _Ref | None = None,
        *,
        boundary_start: bool = False,
        boundary_end: bool = False,
        holes: tuple[int | None, int | None] = (None, None),
    ) -> tuple[_Ref, _Ref]:
        _check(self.deadline)
        mesh_length = self.definition.mesh_length_cm
        if mesh_length is None or math.dist(points[0][0], points[-1][0]) <= mesh_length / 2:
            raise SectionError(f"{self.item}: output pipe is below native minimum chord")
        if spline_length(points) > self.registry.limits.pipe_max_spline_cm:
            raise SectionError(f"{self.item}: output pipe exceeds native spline cap")
        run = PipeRun(
            next(self.ids),
            self.definition.class_name,
            points,
            self.item,
            rate,
            boundary_start=boundary_start,
            boundary_end=boundary_end,
            snapped_passthroughs=holes,
        )
        self.pipes.append(run)
        begin, end = (run.id, self.entry), (run.id, self.exit)
        if source is not None:
            self.links.append(Link(source, begin))
        if target is not None:
            self.links.append(Link(end, target))
        return begin, end

    def line(self, source: _Ref, target: _Ref, rate: Fraction) -> None:
        start, facing = self.at(source)
        finish, normal = self.at(target)
        length = math.dist(start, finish)
        if length == 0:
            raise SectionError(f"{self.item}: output line has no native connector lead")
        direction: Vector = (
            (finish[0] - start[0]) / length,
            (finish[1] - start[1]) / length,
            (finish[2] - start[2]) / length,
        )
        if (
            sum(direction[i] * facing[i] for i in range(3)) < 0.999
            or sum(-direction[i] * normal[i] for i in range(3)) < 0.999
        ):
            raise SectionError(f"{self.item}: output line is not aligned to actual native ports")
        count = math.ceil(length / self.registry.limits.pipe_max_spline_cm)
        previous = source
        for index in range(count):
            points = straight(
                _shift(start, direction, length * index / count), direction, length / count
            )
            _, previous = self.pipe(points, rate, previous, target if index == count - 1 else None)


def add_powered_outputs(
    placement: SfyPlacement,
    source_ports: Sequence[SectionPort],
    registry: Registry,
    lab_map: LabMap,
    *,
    ids: Iterator[int],
    deadline: float,
    base_plane: float,
    roof_plane: float,
    stack_height_cm: float,
    stack_gap_cm: float,
    inward_pumps: bool = False,
) -> SfyPlacement:
    """Join one output material through source-level pumps and a real rated stack lane.

    Trunk funding is evidenced by already placed native pipe classes; each source
    lead keeps its original class. This family requires opposing X-facing columns.
    Corridors derive from actual ports, occupied boxes and native support slabs.
    Inward pumps turn toward the Y midpoint and use exterior risers with a
    separate, natively snapped export shaft; the default preserves coal returns.
    """
    _check(deadline)
    if not source_ports or any(p.kind != "pipe" or p.direction != "output" for p in source_ports):
        raise SectionError(
            "compact powered outputs require real fluid source interfaces", cause="unsupported"
        )
    items = {p.item_id for p in source_ports}
    if len(items) != 1:
        raise SectionError(
            "compact powered outputs require one independent output material", cause="unsupported"
        )
    item = next(iter(items))
    if any(lane.item_id == item for lane in placement.stack_lanes):
        raise SectionError(f"{item}: compact output already has a stack boundary")
    owners: dict[int, MachineObj] = {}
    for link in placement.links:
        for machine_ref, pipe_ref in ((link.a, link.b), (link.b, link.a)):
            obj = placement.by_id(machine_ref[0])
            if isinstance(obj, MachineObj):
                owners[pipe_ref[0]] = obj
    sources: list[_Source] = []
    for interface in source_ports:
        obj = placement.by_id(interface.object_id)
        owner = owners.get(interface.object_id)
        if not isinstance(obj, PipeRun) or owner is None:
            raise SectionError(
                "compact output must be an actual machine-connected native lead pipe"
            )
        _, exit_ = pipe_ends(registry, obj.class_name)
        point, normal = endpoint(placement, obj.id, interface.port, registry)
        if interface.port != exit_ or abs(normal[0]) < 0.999:
            raise SectionError(
                "compact output requires an outward X-facing lead pipe", cause="unsupported"
            )
        sources.append(_Source(interface, obj, owner, point, normal, 1 if normal[0] > 0 else -1))
    total = sum((source.interface.items_per_second for source in sources), Fraction())
    # Already placed native pipe classes are evidence of funded availability.
    # Registry membership alone is not permission to invent a higher tier.
    available = [
        registry.buildables[cls]
        for cls in {pipe.class_name for pipe in placement.pipes}
        if registry.buildables[cls].pipe_flow_limit_m3s is not None
        and Fraction(str(registry.buildables[cls].pipe_flow_limit_m3s)) >= total
    ]
    if not available:
        raise SectionError(
            f"{item}: no already funded pipe carries aggregate output", cause="capacity"
        )
    definition = min(
        available,
        key=lambda candidate: (Fraction(str(candidate.pipe_flow_limit_m3s)), candidate.class_name),
    )
    if (
        definition.pipe_flow_limit_m3s is None
        or definition.mesh_bounds_cm is None
        or definition.mesh_length_cm is None
    ):
        raise SectionError("compact output pipe lacks native mesh/capacity data", cause="data")
    capacity = Fraction(str(definition.pipe_flow_limit_m3s))
    radius = max(abs(point[axis]) for point in definition.mesh_bounds_cm for axis in (1, 2))
    turn = _turn_radius(registry)
    grid = registry.limits.hologram_grid_cm
    if grid is None or grid <= 0:
        raise SectionError("compact outputs require native grid data", cause="data")
    lead = definition.mesh_length_cm / 2 + grid / 5
    junction = registry.buildables[_JUNCTION]
    left, right = _port(junction, (-1, 0, 0)), _port(junction, (1, 0, 0))
    back, forward = _port(junction, (0, -1, 0)), _port(junction, (0, 1, 0))
    node_boxes = [box_bounds(box, Pose(0, 0, 0, 0).transform()) for box in junction.clearance]
    node_half_x = max(max(abs(low[0]), abs(high[0])) for low, high in node_boxes)
    node_half_y = max(max(abs(low[1]), abs(high[1])) for low, high in node_boxes)
    node_half_z = max(max(abs(low[2]), abs(high[2])) for low, high in node_boxes)
    pump = registry.buildables[_PUMP]
    pump_in, pump_out = _port(pump, (-1, 0, 0), "input"), _port(pump, (1, 0, 0), "output")
    if pump.pump_design_head_m is None or pump.pump_design_head_m <= 0:
        raise SectionError("compact output pump lacks native design head", cause="data")
    machine_boxes = _machine_bounds(placement, registry)
    transport_boxes = _transport_bounds(placement, registry)
    obstacles = (*machine_boxes, *transport_boxes)
    transverse_obstacles = (
        tuple(
            ((low[1], low[0], low[2]), (high[1], high[0], high[2]))
            for low, high in obstacles
        )
        if not inward_pumps
        else ()
    )
    floors = sorted({source.machine.pose.z for source in sources})
    minimum_chord = definition.mesh_length_cm / 2 + grid / 20
    levels: dict[float, float] = {}
    for foot in floors:
        machine_top = max(
            high[2]
            for machine in placement.machines
            if machine.pose.z == foot
            for box in registry.buildables[machine.class_name].clearance
            if not box.soft
            for _, high in (box_bounds(box, machine.pose.transform()),)
        )
        ceilings = [
            low[2]
            for foundation in placement.foundations
            for box in registry.buildables[foundation.class_name].clearance
            for low, high in (box_bounds(box, foundation.pose.transform()),)
            if low[2] > machine_top
        ]
        ceiling = min(ceilings, default=roof_plane)
        # Flat collectors use the available inter-floor service space. Their
        # native 75cm height, unlike a pitched cross's 120cm, fits above the coal
        # deck and below the actual upper support slabs.
        level = min(
            ceiling - node_half_z - grid / 20, roof_plane - right.translation[0] - minimum_chord
        )
        levels[foot] = math.floor(level / (grid / 20)) * (grid / 20)
        if levels[foot] - radius <= machine_top:
            raise SectionError(
                f"{item}: no native collector plane clears its factory", cause="height"
            )
    axes: dict[int, float] = {}
    for side in (-1, 1):
        xs = [source.point[0] for source in sources if source.side == side]
        if not xs or max(xs) - min(xs) > 0.01:
            raise SectionError(
                "compact outputs require two aligned opposing source columns", cause="unsupported"
            )
        if inward_pumps:
            # Exterior source risers return inward above the native hard casing.
            axes[side] = xs[0] - side * right.translation[0]
        else:
            # The right collector's source rises inside the coal transfer and
            # returns to the outer service bay only above it.
            axes[side] = xs[0] + right.translation[0]
    builder = _Outputs(placement, registry, ids, deadline, definition, item)
    floor_feeds: dict[float, list[tuple[int, PipeAttachmentObj, Fraction]]] = {
        foot: [] for foot in floors
    }
    for source in sources:
        _check(deadline)
        level = levels[source.machine.pose.z]
        along_sign = (1 if source.point[1] < 0 else -1) if inward_pumps else source.side
        along: Vector = (0, float(along_sign), 0)
        inward: Vector = (-float(source.side), 0, 0)
        upward: Vector = (0, 0, 1)
        initial_radius = turn
        wall = False
        exterior_x = source.point[0]
        if not inward_pumps:
            span = pump_out.translation[0] - pump_in.translation[0]
            initial_y = source.point[1] + source.side * (2 * turn + span)
            wall = any(
                _intersects_riser(
                    box, axes[source.side], initial_y, source.point[2] + turn, level, radius
                )
                for box in machine_boxes
            )
            exterior_x = source.side * (placement.designer.half_cm - radius)
            initial_radius = abs(exterior_x - source.point[0]) if wall else turn
            if initial_radius < turn:
                raise SectionError(f"{item}: native first output bend cannot fit within designer")
        first = _arc(source.point, source.normal, along, initial_radius)
        inlet = first[-1][0]
        rotation = Pose(0, 0, 0, along_sign * 90).transform()
        offset = world_port(rotation, pump_in)
        powered = builder.node(
            _PUMP,
            Pose(
                inlet[0] - offset[0], inlet[1] - offset[1], inlet[2] - offset[2], along_sign * 90
            ),
        )
        input_ref, output_ref = (powered.id, pump_in.name), (powered.id, pump_out.name)
        actual_inlet = builder.at(input_ref)[0]
        if actual_inlet[2] > source.point[2] + 0.001 or math.dist(actual_inlet, inlet) > 0.01:
            raise SectionError(
                f"{item}: pump inlet is above/disconnected from its producer", cause="hydraulic"
            )
        if roof_plane - actual_inlet[2] > pump.pump_design_head_m * 100:
            raise SectionError(
                f"{item}: complete output apex exceeds actual Mk2 pump head", cause="hydraulic"
            )
        points = concat(source.pipe.points, first)
        if spline_length(points) > registry.limits.pipe_max_spline_cm:
            raise SectionError(f"{item}: extended native output lead exceeds spline cap")
        builder.leads[source.pipe.id] = replace(source.pipe, points=points)
        builder.links.append(Link((source.pipe.id, source.interface.port), input_ref))
        outlet = builder.at(output_ref)[0]
        returning = not inward_pumps and source.side > 0 and not wall
        riser_x = outlet[0] - 2 * turn if returning else exterior_x if wall else outlet[0]
        if returning:
            # Project the actual coal transfer ribbons inward at the original
            # source Y, instead of pushing a pump-height lead through their
            # full-height header shaft.
            riser_y = outlet[1] + turn
            riser_x = _clear_riser_y(
                riser_x,
                -1,
                grid,
                transverse_obstacles,
                (
                    (
                        riser_y - radius,
                        riser_y + radius,
                        source.point[2] + turn - radius,
                        level + radius,
                        radius,
                    ),
                ),
            )
        mouth_x = axes[source.side] + (-1 if returning else source.side) * right.translation[0]
        cap_heading = source.normal if returning else inward
        cap_radius = abs(riser_x - mouth_x)
        if cap_radius < turn - 0.01:
            raise SectionError(f"{item}: native inward output elbow cannot fit")
        low_z = source.point[2] + turn
        high_z = level - cap_radius
        phases: list[_RiserPhase] = [
            (riser_x - radius, riser_x + radius, low_z - radius, high_z + radius, radius),
            (
                axes[source.side] - node_half_x,
                axes[source.side] + node_half_x,
                level - node_half_z,
                level + node_half_z,
                node_half_y,
            ),
            (
                min(riser_x, outlet[0]) - radius,
                max(riser_x, outlet[0]) + radius,
                source.point[2] - radius,
                low_z + radius,
                turn + radius,
            ),
        ]
        # The final elbow enters a horizontal mouth above every lower casing
        # and coal transfer, from the side chosen by its clear vertical corridor.
        phases.append(
            (
                min(riser_x, mouth_x) - radius,
                max(riser_x, mouth_x) + radius,
                high_z - radius,
                level + radius,
                radius,
            )
        )
        y = _clear_riser_y(
            outlet[1] + along_sign * turn,
            along_sign,
            grid,
            obstacles,
            phases,
        )
        distance = along_sign * (y - outlet[1]) - turn
        if distance < -0.01:
            raise SectionError(f"{item}: output riser is behind its native pump outlet")
        parts: list[tuple[SplinePoint, ...]] = []
        begin = outlet
        if distance > 0.01:
            distance = max(distance, definition.mesh_length_cm / 2 + grid / 20)
            parts.append(straight(begin, along, distance))
            begin = parts[-1][-1][0]
            if any(
                low[0] < outlet[0] + radius
                and high[0] > outlet[0] - radius
                and low[1] < max(outlet[1], begin[1]) + radius
                and high[1] > min(outlet[1], begin[1]) - radius
                and low[2] < outlet[2] + radius
                and high[2] > outlet[2] - radius
                for low, high in obstacles
            ):
                raise SectionError(
                    f"{item}: extended pump outlet crosses occupied transport", cause="collision"
                )
        if returning:
            a = _arc(begin, along, inward, turn)
            parts.append(a)
            inward_distance = a[-1][0][0] - riser_x - turn
            if inward_distance > 0.01:
                if inward_distance <= definition.mesh_length_cm / 2:
                    raise SectionError(f"{item}: inner return lead is below native spline minimum")
                parts.append(straight(a[-1][0], inward, inward_distance))
            a2 = _arc(parts[-1][-1][0], inward, upward, turn)
            parts.append(a2)
            rise_start = a2[-1][0]
        else:
            a = _arc(begin, along, upward, turn)
            parts.append(a)
            rise_start = a[-1][0]
        rise = high_z - rise_start[2]
        if rise <= definition.mesh_length_cm / 2:
            raise SectionError(f"{item}: exterior output riser has insufficient native rise")
        b = straight(rise_start, upward, rise)
        c = _arc(b[-1][0], upward, cap_heading, cap_radius)
        parts.extend((b, c))
        terminal = c[-1][0]
        feed = builder.node(_JUNCTION, Pose(axes[source.side], terminal[1], level, 0))
        _ = builder.pipe(
            concat(*parts),
            source.interface.items_per_second,
            output_ref,
            (feed.id, left.name if returning or source.side < 0 else right.name),
        )
        floor_feeds[source.machine.pose.z].append(
            (source.side, feed, source.interface.items_per_second)
        )
    left_ys = [node.pose.y for feeds in floor_feeds.values() for side, node, _ in feeds if side < 0]
    separation = forward.translation[1] - back.translation[1] + lead
    cross_y = (
        (min(left_ys) + max(left_ys)) / 2
        if max(left_ys) - min(left_ys) >= 2 * separation
        else max(left_ys) + separation
    )
    export_y = cross_y - separation
    export_x = axes[1] + (right.translation[0] - left.translation[0] if inward_pumps else 0)
    # Derive a shaft outside every real intermediate support tile at its X axis.
    slab = registry.buildables[FOUNDATION_CLASS]
    support_bounds: list[_Bounds] = []
    for x, y, z in _support_tiles(placement, registry):
        if z + slab.clearance[0].max[2] <= min(floors):
            continue
        support_bounds.extend(
            box_bounds(box, Pose(x, y, z, 0).transform()) for box in slab.clearance
        )
    for obj in placement.foundations:
        if base_plane < obj.pose.z < roof_plane:
            support_bounds.extend(box_bounds(box, obj.pose.transform()) for box in slab.clearance)
    for low, high in support_bounds:
        if low[0] < export_x + radius and high[0] > export_x - radius:
            export_y = min(export_y, low[1] - max(radius, node_half_y) - grid / 5)
    if export_y - node_half_y < -placement.designer.half_cm:
        raise SectionError(f"{item}: no native output continuation shaft fits", cause="bounds")
    exports: list[PipeAttachmentObj] = []
    for foot in floors:
        _check(deadline)
        feeds = floor_feeds[foot]
        level = levels[foot]
        left_node = builder.node(_JUNCTION, Pose(axes[-1], cross_y, level, 0))
        right_node = builder.node(_JUNCTION, Pose(axes[1], cross_y, level, 0))
        local = sum((rate for _, _, rate in feeds), Fraction())
        left_rate = sum((rate for side, _, rate in feeds if side < 0), Fraction())
        if inward_pumps:
            export_feed = builder.node(_JUNCTION, Pose(axes[1], export_y, level, 0))
            export = builder.node(_JUNCTION, Pose(export_x, export_y, level, 90, 90))
            # Opposing native mouths coincide: no undersized pipeline is needed.
            builder.links.append(Link((export_feed.id, right.name), (export.id, forward.name)))
        else:
            export = builder.node(_JUNCTION, Pose(export_x, export_y, level, 0, 90))
            export_feed = export
        for side, cross_node in ((-1, left_node), (1, right_node)):
            entries = [(node, rate) for s, node, rate in feeds if s == side]
            entries.append((cross_node, -left_rate if side < 0 else left_rate))
            if side > 0:
                entries.append((export_feed, -local))
            entries.sort(key=lambda pair: pair[0].pose.y)
            running = Fraction()
            for (node, rate), (following, _) in zip(entries, entries[1:], strict=False):
                running += rate
                builder.line((node.id, forward.name), (following.id, back.name), abs(running))
        builder.line((left_node.id, right.name), (right_node.id, left.name), left_rate)
        exports.append(export)
    for upper, lower in zip(exports[1:], exports, strict=False):
        builder.line((upper.id, left.name), (lower.id, right.name), capacity)
    lower, upper = exports[0], exports[-1]
    bottom_hole, top_hole = next(ids), next(ids)
    lower_start = builder.at((lower.id, left.name))[0]
    _, bottom = builder.pipe(
        straight(lower_start, (0, 0, -1), lower_start[2] - base_plane),
        capacity,
        (lower.id, left.name),
        boundary_end=True,
        holes=(None, bottom_hole),
    )
    top_start = (export_x, export_y, roof_plane)
    top, _ = builder.pipe(
        straight(top_start, (0, 0, -1), roof_plane - builder.at((upper.id, right.name))[0][2]),
        capacity,
        target=(upper.id, right.name),
        boundary_start=True,
        holes=(top_hole, None),
    )
    thickness = slab.clearance[0].max[2] - slab.clearance[0].min[2]
    holes = (
        PassthroughObj(
            bottom_hole,
            _HOLE,
            Pose(export_x, export_y, base_plane, 0),
            thickness,
            top_connection=bottom,
        ),
        PassthroughObj(
            top_hole,
            _HOLE,
            Pose(export_x, export_y, roof_plane, 0),
            thickness,
            bottom_connection=top,
        ),
    )
    lane = StackLane(item, "pipe", bottom, top, Fraction(), total, capacity)
    return replace(
        placement,
        pipes=(*(builder.leads.get(p.id, p) for p in placement.pipes), *builder.pipes),
        pipe_attachments=(*placement.pipe_attachments, *builder.nodes),
        links=(*placement.links, *builder.links),
        passthroughs=(*placement.passthroughs, *holes),
        stack_lanes=(*placement.stack_lanes, lane),
        stack_height_cm=stack_height_cm,
        stack_connection_gap_cm=stack_gap_cm,
    )


def add_compact_frame(
    placement: SfyPlacement,
    registry: Registry,
    *,
    ids: Iterator[int],
    deadline: float,
    base_plane: float,
    roof_plane: float,
    stack_height_cm: float,
) -> SfyPlacement:
    """Complete native supports and an asymmetric four-sided diamond frame."""
    _check(deadline)
    if placement.beams:
        raise SectionError("compact frame already contains structural beams")
    grid = registry.limits.hologram_grid_cm
    slab = registry.buildables[FOUNDATION_CLASS]
    beam = registry.buildables[PAINTED_BEAM_CLASS]
    if (
        grid is None
        or grid <= 0
        or slab.width_cm is None
        or slab.depth_cm is None
        or beam.beam_max_length_cm is None
        or beam.beam_size_cm is None
    ):
        raise SectionError("compact frame lacks native slab/beam/grid dimensions", cause="data")
    reserve = placement.designer.half_cm - frame_socket_margin(registry)
    output_ys = [
        world_port(machine.pose.transform(), port)[1]
        for machine in placement.machines
        for port in registry.buildables[machine.class_name].ports
        if port.kind == "pipe" and port.direction == "output"
    ]
    if not output_ys:
        raise SectionError(
            "compact frame lacks genuine outward fluid source ports", cause="unsupported"
        )
    side_y = math.floor((min(output_ys) + 2 * _turn_radius(registry)) / (grid / 2)) * (grid / 2)
    vertices = ((0.0, -reserve), (-reserve, side_y), (0.0, reserve), (reserve, side_y))
    beams: list[BeamObj] = []
    slab_box = slab.clearance[0]
    offset = (slab_box.min[2] + slab_box.max[2]) / 2
    thickness = slab_box.max[2] - slab_box.min[2]
    beam_limit = beam.beam_max_length_cm

    def member(start: Vector, finish: Vector) -> None:
        _check(deadline)
        length = math.dist(start, finish)
        count = math.ceil(length / beam_limit)
        direction: Vector = (
            (finish[0] - start[0]) / length,
            (finish[1] - start[1]) / length,
            (finish[2] - start[2]) / length,
        )
        yaw = math.degrees(math.atan2(direction[1], direction[0]))
        pitch = math.degrees(math.asin(direction[2]))
        for index in range(count):
            beams.append(
                BeamObj(
                    next(ids),
                    PAINTED_BEAM_CLASS,
                    Pose(*_shift(start, direction, length * index / count), yaw, pitch),
                    length / count,
                )
            )

    for x, y in vertices:
        member((x, y, base_plane + thickness / 2), (x, y, stack_height_cm))
    for z in (base_plane, roof_plane):
        for (x, y), (nx, ny) in zip(vertices, vertices[1:] + vertices[:1], strict=True):
            member((x, y, z), (nx, ny, z))
        # Native paired signs mount on a straight +X-facing rail. Keep the
        # collision-free diamond posts, with real base/roof mounting members.
        member((reserve, side_y, z), (reserve, reserve, z))
    foundations = list(placement.foundations)
    existing = {(obj.class_name, obj.pose) for obj in foundations}
    tiles = {tile for tile in _support_tiles(placement, registry) if tile[2] != base_plane}
    half = placement.designer.half_cm
    nx, ny = round(2 * half / slab.width_cm), round(2 * half / slab.depth_cm)
    for plane in (base_plane, roof_plane):
        for ix in range(nx):
            for iy in range(ny):
                tiles.add(
                    (
                        -half + (ix + 0.5) * slab.width_cm,
                        -half + (iy + 0.5) * slab.depth_cm,
                        plane - offset,
                    )
                )
    for x, y, z in sorted(tiles):
        _check(deadline)
        pose = Pose(x, y, z, 0)
        if (FOUNDATION_CLASS, pose) not in existing:
            foundations.append(FoundationObj(next(ids), FOUNDATION_CLASS, pose))
            existing.add((FOUNDATION_CLASS, pose))
    return replace(
        placement,
        beams=tuple(beams),
        foundations=tuple(foundations),
        stack_height_cm=stack_height_cm,
    )
