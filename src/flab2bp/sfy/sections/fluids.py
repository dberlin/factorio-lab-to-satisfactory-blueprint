"""Mixed-material production rows with independent side pipe manifolds.

External fluid rates are m³/s, not the integer litres in native recipes. Pipes
establish connectivity and rated capacity only: the caller must establish the
supply pressure and power the production machines and any external pumps.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence, Set
from dataclasses import replace
from fractions import Fraction
from typing import Literal

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, world_port
from flab2bp.sfy.labmap import LabMap, item_class, machine_class
from flab2bp.sfy.layout.corridors import attachment_box_cm
from flab2bp.sfy.layout.manifold import (
    MERGER_CLASS,
    SPLITTER_CLASS,
    grid_ceil,
    hard_footprint_cm,
    slab_top_cm,
)
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    Link,
    MachineObj,
    PipeAttachmentObj,
    PipeRun,
    Pose,
    belt_ends,
)
from flab2bp.sfy.layout.splines import (
    QUARTER_TURN_TANGENT,
    SplinePoint,
    concat,
    spline_length,
    straight,
)
from flab2bp.sfy.layout.validate import attachment_boxes
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.sections.construction import Connection, _attachment_port, _Construction
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionError,
    SectionPort,
    placement_bounds,
    transform_placement,
)
from flab2bp.sfy.spec import Designer, PipeTier, SfyMachineGroup
from flab2bp.spec import BeltTier

_JUNCTION = "Build_PipelineJunction_Cross_C"
_PIPE_START = "PipelineConnection0"
_PIPE_END = "PipelineConnection1"
_NO_INDICATOR = {
    "Build_Pipeline_C": "Build_Pipeline_NoIndicator_C",
    "Build_PipelineMK2_C": "Build_PipelineMK2_NoIndicator_C",
}
type _Material = tuple[str, Port, Literal["input", "output"]]
type _PipeActor = MachineObj | PipeAttachmentObj | AttachmentObj


def _materials(
    group: SfyMachineGroup,
    machine: Buildable,
    registry: Registry,
    lab_map: LabMap,
    fluid_items: Set[str],
) -> tuple[_Material, ...]:
    recipe = registry.recipes.get(group.recipe_class)
    if recipe is None or group.machine_class not in recipe.producers:
        raise SectionError(f"{group.recipe_id}: no supported native recipe", cause="unsupported")
    result: list[_Material] = []
    directions: tuple[
        tuple[Literal["input", "output"], tuple[tuple[str, int], ...], dict[str, Fraction]], ...
    ] = (
        ("input", recipe.ingredients, group.inputs_per_machine),
        ("output", recipe.products, group.outputs_per_machine),
    )
    for direction, amounts, rates in directions:
        by_descriptor = {item_class(lab_map, item): item for item in rates}
        if set(by_descriptor) != {descriptor for descriptor, _ in amounts}:
            raise SectionError(
                f"{group.recipe_id}: {direction} rates do not retain every native recipe material",
                cause="unsupported",
            )
        # Preserve recipe order *within* each transport family. Global recipe
        # inventory indices are emitted by apply_recipe, never by sorting IDs.
        used = {"pipe": 0, "belt": 0}
        for descriptor, _ in amounts:
            item = by_descriptor[descriptor]
            kind = "pipe" if item in fluid_items else "belt"
            ports = tuple(p for p in machine.ports if p.kind == kind and p.direction == direction)
            index = used[kind]
            if index >= len(ports):
                raise SectionError(
                    f"{group.recipe_id}: {item!r} exceeds {machine.class_name}'s "
                    f"{len(ports)} {kind} {direction} ports",
                    cause="unsupported",
                )
            port = ports[index]
            normal = port_forward(Pose(0, 0, 0, 0).transform(), port)
            if abs(normal[1]) < 0.999 or abs(normal[0]) > 0.001 or abs(normal[2]) > 0.001:
                raise SectionError(
                    f"{machine.class_name}.{port.name}: side manifold requires a ±Y face",
                    cause="unsupported",
                )
            used[kind] += 1
            result.append((item, port, direction))
    if not any(port.kind == "pipe" for _, port, _ in result):
        raise SectionError(f"{group.recipe_id}: no fluid recipe ports", cause="unsupported")
    return tuple(result)


def _pipe_port(buildable: Buildable, heading: Vector) -> Port:
    for port in buildable.ports:
        if port.kind != "pipe" or port.direction != "any":
            continue
        normal = port_forward(Pose(0, 0, 0, 0).transform(), port)
        if sum(normal[i] * heading[i] for i in range(3)) > 0.999:
            return port
    raise SectionError(
        f"{buildable.class_name}: no pipe port facing {heading}", cause="unsupported"
    )


def _at(actor: _PipeActor, port: Port) -> tuple[Vector, Vector]:
    transform = actor.pose.transform()
    return world_port(transform, port), port_forward(transform, port)


def _shift(point: Vector, direction: Vector, distance: float) -> Vector:
    return (
        point[0] + direction[0] * distance,
        point[1] + direction[1] * distance,
        point[2] + direction[2] * distance,
    )


def _raised_branch(
    start: Vector,
    finish: Vector,
    heading: Vector,
    radius: float,
    lead: float,
) -> tuple[SplinePoint, ...]:
    """A planar-in-3D pair of quarter circles, tangent to horizontal ports."""
    rise = finish[2] - start[2]
    run = sum((finish[i] - start[i]) * heading[i] for i in range(2))
    if abs(rise) < 0.01:
        return straight(start, heading, run)
    vertical: Vector = (0, 0, 1 if rise > 0 else -1)
    rise = abs(rise)
    if rise < 2 * radius or run < lead + 2 * radius:
        raise SectionError("pipe branch cannot fit its native-radius vertical offset")
    pull = radius * QUARTER_TURN_TANGENT
    a = _shift(start, heading, lead)
    b = _shift(_shift(a, heading, radius), vertical, radius)
    c = _shift(b, vertical, rise - 2 * radius)
    d = _shift(_shift(c, vertical, radius), heading, radius)
    arc_a = (
        (a, heading, _shift((0, 0, 0), heading, pull)),
        (b, _shift((0, 0, 0), vertical, pull), vertical),
    )
    arc_b = (
        (c, vertical, _shift((0, 0, 0), vertical, pull)),
        (d, _shift((0, 0, 0), heading, pull), heading),
    )
    pieces = [straight(start, heading, lead), arc_a]
    if rise > 2 * radius + 0.01:
        pieces.append(straight(b, vertical, rise - 2 * radius))
    pieces.append(arc_b)
    remaining = run - lead - 2 * radius
    if remaining > 0.01:
        pieces.append(straight(d, heading, remaining))
    return concat(*pieces)


def build_compact_fluid_section(
    group: SfyMachineGroup,
    designer: Designer,
    registry: Registry,
    lab_map: LabMap,
    *,
    belt_tiers: Sequence[BeltTier],
    pipe_tiers: Sequence[PipeTier],
    fluid_items: Set[str],
    ids: Iterator[int],
    deadline: float,
    centres: Sequence[tuple[float, float]] | None = None,
    include_solids: bool = True,
    junction_outputs: bool = True,
    feed_offset_cm: float | None = None,
) -> ProductionSection:
    """Opposing columns with independently rated native local feed manifolds.

    Raised input branches join separate flat manifolds above the lower machine
    bodies. Only their aggregate interfaces are handed to the section router.
    """
    if centres is not None and len(centres) != group.count:
        raise SectionError("compact centres must match the actual machine count", cause="data")
    build = _Construction(registry, lab_map, belt_tiers, ids, deadline)
    build.check_deadline()
    machine = registry.buildables[group.machine_class]
    materials = _materials(group, machine, registry, lab_map, fluid_items)
    bodies = tuple(
        box_bounds(box, Pose(0, 0, 0, 0).transform())
        for box in machine.clearance
        if not box.soft
    )
    grid = registry.limits.hologram_grid_cm
    if grid is None:
        raise SectionError("registry lacks compact section snap geometry", cause="data")
    x0, y0, x1, y1 = hard_footprint_cm(machine)
    # Opposing columns leave a pipe-width corridor; shared junctions can rise
    # above the wider lower machine bodies. Use an even grid pitch so both
    # column anchors keep the same snap phase at the designer's walls.
    column_pitch = grid_ceil((y1 - y0 + 2 * grid) / 2, grid) * 2
    row_pitch = grid_ceil(x1 - x0 + 2 * grid, grid)
    rows = (group.count + 1) // 2
    stand = slab_top_cm(registry)
    pipes: list[PipeRun] = []
    nodes: list[PipeAttachmentObj] = []
    feeds: dict[str, list[tuple[PipeAttachmentObj, Fraction]]] = {}
    inputs: list[SectionPort] = []
    outputs: list[SectionPort] = []
    definitions: dict[str, Buildable] = {}
    lead = 0.0
    tiers = sorted(pipe_tiers, key=lambda tier: tier.cubic_metres_per_second)
    for item, port, direction in materials:
        if port.kind != "pipe":
            continue
        required = (
            group.row_inputs[item]
            if direction == "input"
            else group.outputs_per_machine[item]
        )
        tier = next(
            (
                tier
                for tier in tiers
                if tier.cubic_metres_per_second >= required
            ),
            None,
        )
        if tier is None:
            raise SectionError(f"{item}: no pipe carries the section demand", cause="capacity")
        base = machine_class(lab_map, tier.item_id)
        cls = _NO_INDICATOR.get(base)
        if cls is None or cls not in registry.buildables:
            raise SectionError(
                f"unsupported no-indicator pipeline family {base}", cause="unsupported"
            )
        definition = registry.buildables[cls]
        if definition.mesh_length_cm is None or definition.mesh_bounds_cm is None:
            raise SectionError(f"{cls}: missing native pipe geometry", cause="data")
        capacity = definition.pipe_flow_limit_m3s
        if capacity is None:
            raise SectionError(f"{cls}: missing native hydraulic capacity", cause="data")
        if required > Fraction(str(capacity)):
            raise SectionError(f"{item}: section exceeds {cls} capacity", cause="capacity")
        definitions[item] = definition
        # Leave a small native-grid fraction beyond the minimum mesh chord:
        # a following curved mesh needs real room beside the factory face.
        lead = max(lead, definition.mesh_length_cm / 2 + grid / 5)
    # A factory output supplies no assumed head. Keep its lead high enough to
    # descend into the existing stack collector's first legal pump inlet.
    junction = registry.buildables[_JUNCTION]
    left, right = _pipe_port(junction, (-1, 0, 0)), _pipe_port(junction, (1, 0, 0))
    first_inlet = grid_ceil(stand / 2 + lead + right.translation[0] - left.translation[0], grid)
    for _, port, direction in materials:
        if port.kind == "pipe" and direction == "output":
            # Support slabs are authored at the machine's actual foot plane;
            # only the pump inlet needs the logistics grid phase. Rounding the
            # stand again would add empty height without buying hydraulic head.
            stand = max(stand, first_inlet - port.translation[2])
    feed_materials = [
        item for item, port, direction in materials
        if port.kind == "pipe" and direction == "input"
    ]
    # Side-by-side flat manifolds keep every factory riser in the central
    # corridor. Layered manifolds instead cross the other material's risers.
    feed_layouts: dict[str, tuple[Pose, Port, Port, Port]] = {}
    feed_planes: dict[str, float] = {}
    for index, item in enumerate(feed_materials):
        definition = definitions[item]
        assert definition.mesh_bounds_cm is not None
        pipe_radius = max(abs(v) for bound in definition.mesh_bounds_cm for v in bound[1:])
        side = 1 if index % 2 == 0 else -1
        base_pitch = grid_ceil(2 * pipe_radius + grid / 2, grid)
        feed_distance = base_pitch if feed_offset_cm is None else feed_offset_cm
        feed_layouts[item] = (
            Pose(
                side * (feed_distance + (index // 2) * base_pitch),
                0, 0, side * 90,
            ),
            _pipe_port(junction, (0, 1, 0)),
            _pipe_port(junction, (-side, 0, 0)),
            _pipe_port(junction, (side, 0, 0)),
        )
        pose = feed_layouts[item][0]
        node_boxes = [box_bounds(box, pose.transform()) for box in junction.clearance]
        x_low = min(low[0] for low, _ in node_boxes)
        x_high = max(high[0] for _, high in node_boxes)
        under = [
            high[2]
            for column in (-1, 1)
            for box in machine.clearance if not box.soft
            for low, high in (box_bounds(
                box, Pose(column * column_pitch / 2, 0, stand, -column * 90).transform()
            ),)
            if low[0] < x_high and high[0] > x_low
        ]
        bottom = min(low[2] for low, _ in node_boxes)
        feed_planes[item] = grid_ceil(max(under, default=stand) - bottom + 1e-6, grid)
    for index in range(group.count):
        row, column = divmod(index, 2)
        x, y = (
            centres[index] if centres is not None else (
                (-1 if column == 0 else 1) * column_pitch / 2,
                (row - (rows - 1) / 2) * row_pitch,
            )
        )
        actor = MachineObj(
            next(ids),
            group.machine_class,
            Pose(
                x,
                y,
                stand,
                90 if column == 0 else -90,
            ),
            group.recipe_class,
            group.clock if index < group.count - 1 else group.last_clock,
            group.somersloops,
        )
        build.machines.append(actor)
        for item, port, direction in materials:
            if port.kind == "belt" and not include_solids:
                continue
            rate = (
                group.inputs_per_machine if direction == "input" else group.outputs_per_machine
            )[item] * actor.clock / group.clock
            object_id, name = actor.id, port.name
            position, normal = _at(actor, port)
            lower_top = max(
                (
                    high[2]
                    for low, high in bodies
                    if all(low[i] <= port.translation[i] <= high[i] for i in range(3))
                ),
                default=port.translation[2],
            )
            if port.kind == "pipe":
                definition = definitions[item]
                assert definition.mesh_length_cm is not None
                points = straight(position, normal, lead)
                if direction == "input":
                    # Bring the feed up through the clear central corridor
                    # before asking the shared router to place a junction. A
                    # junction body cannot fit between the lower factory boxes.
                    radius = registry.limits.pipe_min_bend_radius_cm * 1.1
                    vertical: Vector = (0, 0, 1)
                    start = _shift(position, normal, lead)
                    finish = _shift(_shift(start, normal, radius), vertical, radius)
                    pull = radius * QUARTER_TURN_TANGENT
                    arc = (
                        (start, normal, _shift((0, 0, 0), normal, pull)),
                        (finish, _shift((0, 0, 0), vertical, pull), vertical),
                    )
                    top = max(
                        feed_planes[item],
                        grid_ceil(finish[2] + definition.mesh_length_cm / 2 + radius, grid),
                    )
                    feed_pose, inlet, _, _ = feed_layouts[item]
                    # Rise in the central gap, then enter the flat manifold.
                    heading = (math.copysign(1, feed_pose.x), 0.0, 0.0)
                    end = (
                        world_port(feed_pose.transform(), inlet)[0],
                        finish[1],
                        top,
                    )
                    turn_start = (finish[0], finish[1], end[2] - radius)
                    turn_finish = _shift(turn_start, heading, radius)
                    turn_finish = (turn_finish[0], turn_finish[1], end[2])
                    turn = (
                        (turn_start, vertical, _shift((0, 0, 0), vertical, pull)),
                        (turn_finish, _shift((0, 0, 0), heading, pull), heading),
                    )
                    # The standalone straight builder clamps to a 50cm
                    # tangent. This short internal lead must not turn back.
                    flat_tangent = _shift((0, 0, 0), heading, math.dist(turn_finish, end) / 2)
                    points = concat(
                        points, arc,
                        straight(finish, vertical, turn_start[2] - finish[2]),
                        turn,
                        (
                            (turn_finish, heading, flat_tangent),
                            (end, flat_tangent, heading),
                        ),
                    )
                pipe = PipeRun(
                    next(ids), definition.class_name, points, item, rate
                )
                pipes.append(pipe)
                build.links.append(Link((actor.id, port.name), (pipe.id, _PIPE_START)))
                object_id, name = pipe.id, _PIPE_END
                if direction == "input":
                    end = points[-1][0]
                    feed_pose, inlet, _, _ = feed_layouts[item]
                    offset = world_port(
                        Pose(
                            0, 0, 0, feed_pose.yaw_deg, feed_pose.pitch_deg, feed_pose.roll_deg
                        ).transform(),
                        inlet,
                    )
                    node = PipeAttachmentObj(
                        next(ids), _JUNCTION,
                        Pose(
                            end[0] - offset[0], end[1] - offset[1], end[2] - offset[2],
                            feed_pose.yaw_deg, feed_pose.pitch_deg, feed_pose.roll_deg,
                        ),
                    )
                    nodes.append(node)
                    feeds.setdefault(item, []).append((node, rate))
                    build.links.append(Link((pipe.id, _PIPE_END), (node.id, inlet.name)))
                    continue
                if not junction_outputs:
                    outputs.append(SectionPort(item, rate, object_id, name, direction, kind="pipe"))
                    continue
                # Complete section interfaces end on genuine unused junction
                # ports, not disconnected internal pipeline ends.
                node_pose = Pose(0, 0, 0, 90)
                inlet = next(
                    p for p in junction.ports
                    if sum(port_forward(node_pose.transform(), p)[i] * normal[i] for i in range(3))
                    < -0.999
                )
                offset = world_port(node_pose.transform(), inlet)
                end = points[-1][0]
                node = PipeAttachmentObj(
                    next(ids), _JUNCTION,
                    Pose(end[0] - offset[0], end[1] - offset[1], end[2] - offset[2], 90),
                )
                nodes.append(node)
                build.links.append(Link((pipe.id, _PIPE_END), (node.id, inlet.name)))
                public = _pipe_port(junction, (1 if position[1] < 0 else -1, 0, 0))
                object_id, name = node.id, public.name
            else:
                lift_min = registry.limits.lift_min_cm
                lift_half = registry.limits.lift_clearance_half_extent_cm
                if lift_min is None or lift_half is None:
                    raise SectionError(
                        "compact belt interfaces lack native lift geometry", cause="data"
                    )
                # Keep lift heights on the native step. A gentle collector
                # spline reconciles its factory phase with the routing lattice.
                distance = grid_ceil(
                    max(
                        build.lead,
                        (y1 if normal[0] > 0 else -y0)
                        - abs(port.translation[1])
                        + lift_half,
                    ),
                    grid,
                )
                mouth = _shift(position, normal, distance)
                lift_step = registry.limits.lift_step_cm
                if lift_step is None:
                    raise SectionError(
                        "compact belt interfaces lack native lift step", cause="data"
                    )
                cls = MERGER_CLASS if direction == "output" else SPLITTER_CLASS
                half_height = max(
                    box.reach[2]
                    for box in attachment_boxes(AttachmentObj(0, cls, Pose(0, 0, 0, 0)), registry)
                )
                height = max(lift_min, stand + lower_top + half_height - position[2])
                deck = position[2] + grid_ceil(height, lift_step)
                plane = grid_ceil(deck, grid)
                belt_yaw = math.degrees(math.atan2(normal[1], normal[0]))
                terminal_heading = 90 if position[1] < 0 else -90
                entry, exit_ = build.lift(
                    mouth[0],
                    mouth[1],
                    position[2] if direction == "output" else deck,
                    deck if direction == "output" else position[2],
                    belt_yaw + 180 if direction == "output" else terminal_heading,
                    item,
                    rate,
                )
                assert isinstance(entry[0], LiftObj)
                lift = replace(
                    entry[0],
                    top_yaw_deg=(
                        terminal_heading if direction == "output" else belt_yaw + 180
                    ) - entry[0].pose.yaw_deg,
                )
                build.lifts[-1] = lift
                entry, exit_ = (lift, entry[1]), (lift, exit_[1])
                if direction == "output":
                    build.belt((actor, port.name), entry, item, rate)
                    fixture = exit_
                else:
                    build.belt(exit_, (actor, port.name), item, rate)
                    fixture = entry
                at, facing = build.at(fixture)
                run = max(6 * grid, build.lead)
                finish = _shift(at, facing, run)
                finish = (finish[0], finish[1], plane)
                definition = registry.buildables[cls]
                through_in = _attachment_port(definition, "input", (-1, 0, 0))
                through_out = _attachment_port(definition, "output", (1, 0, 0))
                collector_heading = terminal_heading + (180 if direction == "input" else 0)
                attached = through_in if direction == "output" else through_out
                rotation = Pose(0, 0, 0, collector_heading).transform()
                offset = world_port(rotation, attached)
                # The lift stays outside the lower machine body; the wider
                # collector sits inward above it. Reserve its actual rotated
                # native bounds, not a nominal 200cm attachment half-width.
                native = attachment_boxes(
                    AttachmentObj(0, cls, Pose(0, 0, 0, collector_heading)), registry
                )
                half_width = max(abs(corner[0]) for box in native for corner in box.corners())
                edge = math.floor((designer.half_cm - half_width) / grid) * grid
                finish = (
                    math.copysign(min(abs(finish[0]), edge), finish[0]),
                    finish[1],
                    finish[2],
                )
                collector_xy = (finish[0] - offset[0], finish[1] - offset[1])
                # The upper casing has narrower bounds, but native side
                # machinery can remain under the collector. Clear every
                # actual hard box overlapping its full mesh-instance body.
                support_top = max(
                    (
                        high[2]
                        for box in machine.clearance if not box.soft
                        for low, high in (box_bounds(box, actor.pose.transform()),)
                        if any(
                            all(
                                body.centre[i] - body.reach[i] + collector_xy[i] < high[i]
                                and body.centre[i] + body.reach[i] + collector_xy[i] > low[i]
                                for i in (0, 1)
                            )
                            for body in native
                        )
                    ),
                    default=stand,
                )
                body_bottom = min(body.centre[2] - body.reach[2] for body in native)
                needed_plane = grid_ceil(support_top - body_bottom + offset[2] + 1e-6, grid)
                if needed_plane > plane:
                    rise = grid_ceil(max(0, needed_plane - grid + 1e-6 - at[2]), lift_step)
                    height = lift.height_cm + (rise if direction == "output" else -rise)
                    if (
                        registry.limits.lift_max_cm is None
                        or abs(height) > registry.limits.lift_max_cm
                    ):
                        raise SectionError(
                            "compact collector exceeds native lift height", cause="height"
                        )
                    lift = replace(
                        lift,
                        pose=(
                            lift.pose if direction == "output"
                            else replace(lift.pose, z=lift.pose.z + rise)
                        ),
                        height_cm=height,
                    )
                    build.lifts[-1] = lift
                    fixture = (lift, fixture[1])
                    at, facing = build.at(fixture)
                    plane = grid_ceil(at[2], grid)
                    finish = (finish[0], finish[1], plane)
                collector = AttachmentObj(
                    next(ids), cls,
                    Pose(
                        finish[0] - offset[0], finish[1] - offset[1], finish[2] - offset[2],
                        collector_heading,
                    ),
                )
                build.attachments.append(collector)
                belt_tier = build.tier(item, rate)
                belt_definition = registry.buildables[machine_class(lab_map, belt_tier.item_id)]
                if belt_definition.mesh_bounds_cm is None:
                    raise SectionError("compact collector lacks native belt mesh", cause="data")
                belt_half = max(abs(bound[1]) for bound in belt_definition.mesh_bounds_cm)
                first_flat = grid_ceil(lift_half + belt_half + 1e-6, grid)
                last_flat = grid_ceil(half_width - abs(offset[1]) + belt_half + 1e-6, grid)
                middle = run - first_flat - last_flat
                if middle <= 0:
                    raise SectionError(
                        "compact collector has no clear transition chord", cause="bounds"
                    )
                start_curve = _shift(at, facing, first_flat)
                end_curve = _shift(finish, facing, -last_flat)
                tangent = _shift((0, 0, 0), facing, middle)
                points = concat(
                    straight(at, facing, first_flat),
                    ((start_curve, tangent, tangent), (end_curve, tangent, tangent)),
                    straight(end_curve, facing, last_flat),
                )
                source: Connection
                target: Connection
                if direction == "output":
                    source, target = fixture, (collector, through_in.name)
                    public = through_out
                else:
                    points = tuple(
                        (point, (-leave[0], -leave[1], -leave[2]),
                         (-arrive[0], -arrive[1], -arrive[2]))
                        for point, arrive, leave in reversed(points)
                    )
                    source, target = (collector, through_out.name), fixture
                    public = through_in
                belt = BeltRun(
                    next(ids), machine_class(lab_map, belt_tier.item_id), points, item, rate
                )
                build.belts.append(belt)
                first, last = belt_ends(registry, belt.class_name)
                build.links.extend(
                    (
                        Link((source[0].id, source[1]), (belt.id, first)),
                        Link((belt.id, last), (target[0].id, target[1])),
                    )
                )
                object_id, name = collector.id, public.name
            (inputs if direction == "input" else outputs).append(
                SectionPort(
                    item, rate, object_id, name, direction,
                    kind="pipe" if port.kind == "pipe" else "belt",
                )
            )
    for item, branches in feeds.items():
        branches.sort(key=lambda entry: entry[0].pose.y)
        total = sum((rate for _, rate in branches), Fraction())
        remaining = total
        _, _, back, forward = feed_layouts[item]
        for (node, rate), (following, _) in zip(branches, branches[1:], strict=False):
            remaining -= rate
            start, heading = _at(node, forward)
            finish, _ = _at(following, back)
            run = finish[1] - start[1]
            mesh_length = definitions[item].mesh_length_cm
            assert mesh_length is not None
            if run <= mesh_length / 2:
                raise SectionError(f"{item}: compact feed spacing is below native pipe length")
            tangent = _shift((0, 0, 0), heading, run)
            pipe = PipeRun(
                next(ids), definitions[item].class_name,
                ((start, tangent, tangent), (finish, tangent, tangent)), item, remaining,
            )
            pipes.append(pipe)
            build.links.extend((
                Link((node.id, forward.name), (pipe.id, _PIPE_START)),
                Link((pipe.id, _PIPE_END), (following.id, back.name)),
            ))
        inputs.append(SectionPort(item, total, branches[0][0].id, back.name, "input", kind="pipe"))
    placement = replace(
        build.placement(designer), pipes=tuple(pipes), pipe_attachments=tuple(nodes)
    )
    low, high = placement_bounds(placement, registry)
    if any(high[axis] - low[axis] > 2 * designer.half_cm for axis in (0, 1)):
        raise SectionError(
            f"{group.recipe_id}: compact opposing columns exceed the {designer.mark} floor",
            cause="bounds",
        )
    if high[2] > designer.height_cm:
        raise SectionError(
            f"{group.recipe_id}: compact section exceeds designer height", cause="height"
        )
    return ProductionSection(group, placement, tuple(inputs), tuple(outputs), (low, high))


def build_fluid_section(
    group: SfyMachineGroup,
    designer: Designer,
    registry: Registry,
    lab_map: LabMap,
    *,
    belt_tiers: Sequence[BeltTier],
    pipe_tiers: Sequence[PipeTier],
    fluid_items: Set[str],
    ids: Iterator[int],
    deadline: float,
) -> ProductionSection:
    """Build one complete row; refuse unsupported ports or an honest fit failure."""
    build = _Construction(registry, lab_map, belt_tiers, ids, deadline)
    build.check_deadline()
    machine = registry.buildables.get(group.machine_class)
    if machine is None:
        raise SectionError(f"unknown production machine {group.machine_class}", cause="unsupported")
    materials = _materials(group, machine, registry, lab_map, fluid_items)
    limits = registry.limits
    grid, lift_min, lift_width = (
        limits.hologram_grid_cm,
        limits.lift_min_cm,
        limits.lift_clearance_half_extent_cm,
    )
    if grid is None or lift_min is None or lift_width is None:
        raise SectionError("registry lacks fluid section placement geometry", cause="unsupported")
    junction = registry.buildables[_JUNCTION]
    left, right = _pipe_port(junction, (-1, 0, 0)), _pipe_port(junction, (1, 0, 0))
    junction_reach = max(abs(p.translation[1]) for p in junction.ports if p.kind == "pipe")
    tiers = sorted(pipe_tiers, key=lambda tier: tier.cubic_metres_per_second)
    pipes: list[PipeRun] = []
    nodes: list[PipeAttachmentObj] = []
    chosen: dict[str, Buildable] = {}
    radii: list[float] = []
    minimum_lengths: dict[str, float] = {}
    for item, port, direction in materials:
        total = (group.row_inputs if direction == "input" else group.row_outputs)[item]
        if port.kind == "belt":
            build.tier(item, total)
            continue
        total = max(group.row_inputs.get(item, Fraction()), group.row_outputs.get(item, Fraction()))
        tier = next((tier for tier in tiers if total <= tier.cubic_metres_per_second), None)
        if tier is None:
            raise SectionError(
                f"{item!r} needs {total} m³/s above available pipe capacity", cause="capacity"
            )
        source_class = machine_class(lab_map, tier.item_id)
        class_name = _NO_INDICATOR.get(source_class)
        if class_name is None or class_name not in registry.buildables:
            raise SectionError(
                f"unsupported no-indicator pipeline family {source_class}", cause="unsupported"
            )
        definition = registry.buildables[class_name]
        if definition.mesh_length_cm is None or definition.mesh_bounds_cm is None:
            raise SectionError(f"{class_name}: missing native pipe geometry", cause="unsupported")
        capacity = definition.pipe_flow_limit_m3s
        if capacity is None:
            raise SectionError(
                f"{class_name}: missing native hydraulic capacity", cause="unsupported"
            )
        if total > Fraction(str(capacity)):
            raise SectionError(
                f"{item!r}: {total} m³/s exceeds {class_name}'s native {capacity:g} m³/s",
                cause="capacity",
            )
        radii.append(max(abs(v) for bound in definition.mesh_bounds_cm for v in bound[1:]))
        minimum_lengths[item] = definition.mesh_length_cm / 2
        if not {_PIPE_START, _PIPE_END} <= {p.name for p in definition.ports if p.kind == "pipe"}:
            raise SectionError(f"{class_name}: unsupported dynamic pipe ports", cause="unsupported")
        chosen[item] = definition

    pipe_radius = max(radii)
    pipe_lead = max(minimum_lengths.values()) + 1
    bend = max(limits.pipe_bend_radius_2d_cm, limits.pipe_min_bend_radius_cm * 1.05 + 1)
    attachment_height = max(
        2 * box.reach[2]
        for cls in (SPLITTER_CLASS, MERGER_CLASS)
        for box in attachment_boxes(AttachmentObj(0, cls, Pose(0, 0, 0, 0)), registry)
    )
    attachment_half = attachment_box_cm(registry)
    layer = grid_ceil(max(lift_min, 2 * bend, attachment_height + 2 * pipe_radius), grid)
    stand = slab_top_cm(registry)
    # A factory outlet has no established pump head. Raise the production
    # floor enough for a downward-only collector to reach the first legal
    # trunk pump inlet: bottom hole centre, strictly legal lower pipe, then
    # the junction's native down-to-up connection span.
    first_pump_inlet = grid_ceil(
        stand / 2 + pipe_lead + right.translation[0] - left.translation[0],
        grid,
    )
    output_layers = {-1: 0, 1: 0}
    for _, port, direction in materials:
        if port.kind != "pipe" or direction != "output":
            continue
        side = 1 if port_forward(Pose(0, 0, 0, 0).transform(), port)[1] > 0 else -1
        stand = max(
            stand,
            grid_ceil(
                first_pump_inlet - port.translation[2] + output_layers[side] * layer,
                grid,
            ),
        )
        output_layers[side] += 1
    x0, y0, x1, y1 = hard_footprint_cm(machine)
    # Side manifolds need no walking aisle between machines. Native hard
    # clearances may share a face; reserve only the footprint and the actual
    # connector spans plus legal transport leads, rounded up to the snap grid.
    pitch = max(x1 - x0, right.translation[0] - left.translation[0] + pipe_lead)
    for _, port, direction in materials:
        if port.kind != "belt":
            continue
        definition = registry.buildables[SPLITTER_CLASS if direction == "input" else MERGER_CLASS]
        through_in = _attachment_port(definition, "input", (-1, 0, 0))
        through_out = _attachment_port(definition, "output", (1, 0, 0))
        pitch = max(
            pitch,
            through_out.translation[0] - through_in.translation[0] + build.lead,
            2 * lift_width,
        )
    pitch = grid_ceil(pitch, grid)
    for index in range(group.count):
        build.machines.append(
            MachineObj(
                next(ids),
                group.machine_class,
                Pose(index * pitch, 0, stand, 0),
                group.recipe_class,
                group.clock if index < group.count - 1 else group.last_clock,
                group.somersloops,
            )
        )

    def lay(
        points: tuple[SplinePoint, ...],
        item: str,
        rate: Fraction,
        source: tuple[int, str] | None,
        target: tuple[int, str] | None,
    ) -> PipeRun:
        build.check_deadline()
        definition = chosen[item]
        length = spline_length(points)
        floor = minimum_lengths[item]
        polyline = sum(math.dist(a[0], b[0]) for a, b in zip(points, points[1:], strict=False))
        if polyline <= floor:
            raise SectionError(f"{item!r}: pipe polyline {polyline:g} cm must exceed {floor:g} cm")
        if length > limits.pipe_max_spline_cm:
            # Split straight segments exactly; never replace a curved route by
            # a chord, nor insert zero-length junction-to-junction connections.
            delta = tuple(points[-1][0][i] - points[0][0][i] for i in range(3))
            chord = math.sqrt(sum(value * value for value in delta))
            if len(points) != 2 or abs(chord - length) > 0.01:
                raise SectionError(f"{item!r}: curved branch exceeds native pipe maximum")
            pieces = math.ceil(length / limits.pipe_max_spline_cm)
            heading = (delta[0] / chord, delta[1] / chord, delta[2] / chord)
            previous = source
            final: PipeRun | None = None
            for index in range(pieces):
                final = lay(
                    straight(
                        _shift(points[0][0], heading, index * chord / pieces),
                        heading,
                        chord / pieces,
                    ),
                    item,
                    rate,
                    previous,
                    target if index == pieces - 1 else None,
                )
                previous = (final.id, _PIPE_END)
            assert final is not None
            return final
        actor = PipeRun(next(ids), definition.class_name, points, item, rate)
        pipes.append(actor)
        if source is not None:
            build.links.append(Link(source, (actor.id, _PIPE_START)))
        if target is not None:
            build.links.append(Link((actor.id, _PIPE_END), target))
        return actor

    inputs: list[SectionPort] = []
    outputs: list[SectionPort] = []
    face_fluid_count = {
        side: sum(
            1
            for _, p, _ in materials
            if p.kind == "pipe" and port_forward(Pose(0, 0, 0, 0).transform(), p)[1] * side > 0
        )
        for side in (-1, 1)
    }
    fluid_layers = {(side, direction): 0 for side in (-1, 1) for direction in ("input", "output")}
    solid_layers = {-1: 0, 1: 0}
    max_pipe_z = stand + max(p.translation[2] for _, p, _ in materials if p.kind == "pipe")
    max_pipe_z += max(face_fluid_count.values(), default=1) * layer - layer
    for item, port, direction in materials:
        build.check_deadline()
        normal = port_forward(Pose(0, 0, 0, 0).transform(), port)
        side = 1 if normal[1] > 0 else -1
        heading: Vector = (0, side, 0)
        edge = y1 if side == 1 else -y0
        lift_y = side * math.ceil(
            max(edge + lift_width + 1, side * port.translation[1] + build.lead)
        )
        manifold_y = side * math.ceil(
            max(
                abs(lift_y) + lift_width + pipe_radius + 1 + junction_reach,
                edge + pipe_radius + pipe_lead + 2 * bend + junction_reach
                if face_fluid_count[side] > 1
                else edge + pipe_radius + pipe_lead + junction_reach,
            )
        )
        per_machine = (
            group.inputs_per_machine if direction == "input" else group.outputs_per_machine
        )[item]
        total = (group.row_inputs if direction == "input" else group.row_outputs)[item]
        external = inputs if direction == "input" else outputs
        previous_pipe: PipeAttachmentObj | None = None
        previous_solid: AttachmentObj | None = None
        running = Fraction(0)
        if port.kind == "pipe":
            layer_index = fluid_layers[side, direction]
            z = (
                stand
                + port.translation[2]
                + layer_index * layer * (1 if direction == "input" else -1)
            )
            fluid_layers[side, direction] += 1
            branch = _pipe_port(junction, (0, -side, 0))
            for actor in build.machines:
                position, _ = _at(actor, port)
                node = PipeAttachmentObj(next(ids), _JUNCTION, Pose(position[0], manifold_y, z, 0))
                nodes.append(node)
                amount = per_machine * actor.clock / group.clock
                finish, _ = _at(node, branch)
                points = _raised_branch(
                    position,
                    finish,
                    heading,
                    bend,
                    max(pipe_lead, edge + pipe_radius + 1 - side * port.translation[1]),
                )
                lay(points, item, amount, (actor.id, port.name), (node.id, branch.name))
                if previous_pipe is None:
                    external.append(
                        SectionPort(item, total, node.id, left.name, direction, kind="pipe")
                    )
                else:
                    start, _ = _at(previous_pipe, right)
                    finish, _ = _at(node, left)
                    carried = total - running
                    # The external interface is on the left for both directions:
                    # output collection flows left, supply flows right.
                    lay(
                        straight(start, (1, 0, 0), finish[0] - start[0]),
                        item,
                        carried,
                        (previous_pipe.id, right.name),
                        (node.id, left.name),
                    )
                running += amount
                previous_pipe = node
        else:
            cls = SPLITTER_CLASS if direction == "input" else MERGER_CLASS
            definition = registry.buildables[cls]
            through_in = _attachment_port(definition, "input", (-1, 0, 0))
            through_out = _attachment_port(definition, "output", (1, 0, 0))
            branch = _attachment_port(
                definition, "output" if direction == "input" else "input", (0, -side, 0)
            )
            z = grid_ceil(
                max(
                    stand + port.translation[2] + lift_min,
                    max_pipe_z + pipe_radius + attachment_height + 1,
                ),
                grid,
            )
            z += solid_layers[side] * layer
            solid_layers[side] += 1
            # A belt must have a whole minimum-length lead between the lift and
            # splitter/merger connector, beyond their own physical envelopes.
            belt_y = side * max(
                abs(manifold_y),
                abs(lift_y) + abs(branch.translation[1]) + build.lead,
                abs(lift_y) + lift_width + attachment_half,
            )
            for index, actor in enumerate(build.machines):
                position, _ = build.at((actor, port.name))
                # The final collector turns into the side logistics band,
                # rather than demanding a belt turn beyond the machine row.
                end_collector = direction == "output" and index == group.count - 1
                node_branch = through_in if end_collector else branch
                node_in = (
                    _attachment_port(definition, "input", (0, side, 0))
                    if end_collector
                    else through_in
                )
                solid_node = AttachmentObj(
                    next(ids), cls, Pose(position[0], belt_y, z, side * 90 if end_collector else 0)
                )
                build.attachments.append(solid_node)
                amount = per_machine * actor.clock / group.clock
                entry, exit_ = build.lift(
                    position[0],
                    lift_y,
                    z if direction == "input" else position[2],
                    position[2] if direction == "input" else z,
                    side * 90 if direction == "input" else -side * 90,
                    item,
                    amount,
                )
                if direction == "input":
                    build.belt((solid_node, node_branch.name), entry, item, amount)
                    build.belt(exit_, (actor, port.name), item, amount)
                else:
                    build.belt((actor, port.name), entry, item, amount)
                    build.belt(exit_, (solid_node, node_branch.name), item, amount)
                if previous_solid is not None:
                    build.belt(
                        (previous_solid, through_out.name),
                        (solid_node, node_in.name),
                        item,
                        total - running if direction == "input" else running,
                    )
                elif direction == "input":
                    external.append(
                        SectionPort(item, total, solid_node.id, through_in.name, direction)
                    )
                running += amount
                previous_solid = solid_node
            if direction == "output":
                assert previous_solid is not None
                external.append(
                    SectionPort(item, total, previous_solid.id, through_out.name, direction)
                )

    placement = replace(
        build.placement(designer), pipes=tuple(pipes), pipe_attachments=tuple(nodes)
    )
    low, high = placement_bounds(placement, registry)
    # Connection geometry can extend outside a machine's clearance, notably a
    # refinery's elevated power connector. Include it without adding fake wires.
    power_points = [
        world_port(actor.pose.transform(), p)
        for actor in build.machines
        for p in machine.ports
        if p.kind == "power"
    ]

    def envelope(axis: int, upper: bool) -> float:
        values = [(high if upper else low)[axis], *(point[axis] for point in power_points)]
        return max(values) if upper else min(values)

    low, high = (
        (envelope(0, False), envelope(1, False), envelope(2, False)),
        (envelope(0, True), envelope(1, True), envelope(2, True)),
    )
    width, depth = high[0] - low[0], high[1] - low[1]
    if max(width, depth) > 2 * designer.half_cm:
        raise SectionError(
            f"{group.recipe_id}: complete fluid section needs {width:.0f} × {depth:.0f} cm; "
            f"{designer.mark} allows {2 * designer.half_cm:.0f} cm",
            cause="bounds",
        )
    if high[2] > designer.height_cm:
        raise SectionError(
            f"{group.recipe_id}: complete refinery geometry needs {high[2]:.0f} cm; "
            f"{designer.mark} allows {designer.height_cm:.0f} cm",
            cause="height",
        )
    offset = (-(low[0] + high[0]) / 2, -(low[1] + high[1]) / 2, 0.0)
    placement = transform_placement(placement, offset)
    bounds = ((-width / 2, -depth / 2, low[2]), (width / 2, depth / 2, high[2]))
    build.check_deadline()
    return ProductionSection(group, placement, tuple(inputs), tuple(outputs), bounds)
