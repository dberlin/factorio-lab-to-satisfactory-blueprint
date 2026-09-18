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

from flab2bp.sfy.geometry import Vector, port_forward, world_port
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
    Link,
    MachineObj,
    PipeAttachmentObj,
    PipeRun,
    Pose,
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
from flab2bp.sfy.sections.construction import _attachment_port, _Construction
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
