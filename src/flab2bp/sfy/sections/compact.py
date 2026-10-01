"""Opposing-column blender factories with powered peripheral output risers."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from fractions import Fraction

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.geometry import box_bounds
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.grid_nets import belt_class_for
from flab2bp.sfy.layout.lattice import Lattice, occupancy_for
from flab2bp.sfy.layout.manifold import grid_ceil, hard_footprint_cm
from flab2bp.sfy.layout.model import FoundationObj, Pose, SfyPlacement
from flab2bp.sfy.layout.rrr import route_all
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.sections.compact_solids import add_coal, add_inputs
from flab2bp.sfy.sections.compose import _lift_class, _merge, _nets, _partition_group
from flab2bp.sfy.sections.fluids import build_compact_fluid_section
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionError,
    placement_bounds,
    transform_placement,
)
from flab2bp.sfy.sections.pipe_routes import connect_fluid_ports
from flab2bp.sfy.sections.power import add_wall_power
from flab2bp.sfy.sections.powered_outputs import (
    _support_tiles,
    add_compact_frame,
    add_powered_outputs,
)
from flab2bp.sfy.sections.signs import add_connection_signs
from flab2bp.sfy.sections.stacking import _belt_lane, _Builder, _choose_lanes, _pipe_lane
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, SfyBuildSpec


def compact_factory(
    spec: SfyBuildSpec, designer: Designer, registry: Registry, lab_map: LabMap, deadline: float
) -> SfyPlacement:
    """Compose two complete three-machine floors; retain exact solved clocks."""
    group = spec.groups[0]
    machine = registry.buildables[group.machine_class]
    slab = registry.buildables[FOUNDATION_CLASS].clearance[0]
    grid = registry.limits.hologram_grid_cm
    lift_half = registry.limits.lift_clearance_half_extent_cm
    gap = registry.limits.lift_min_cm
    if grid is None or lift_half is None or gap is None:
        raise SectionError("compact factory requires native snap and lift dimensions", cause="data")

    x0, y0, x1, y1 = hard_footprint_cm(machine)
    row_pitch = x1 - x0
    column_pitch = math.ceil((y1 - y0 + 2 * grid) / grid) * grid
    inlet_x = [
        p.translation[0] for p in machine.ports if p.kind == "pipe" and p.direction == "input"
    ]
    centres = (
        (-column_pitch / 2, -row_pitch / 2 + 2 * lift_half),
        (column_pitch / 2, -(max(inlet_x) - min(inlet_x) + grid)),
        (-column_pitch / 2, row_pitch / 2 + 2 * lift_half),
    )
    thickness = slab.max[2] - slab.min[2]
    # Reserve a pipe diameter and a real slab above the transformed hard bodies.
    pipe_radius = registry.limits.pipe_min_bend_radius_cm
    hard_height = max(
        box_bounds(box, Pose(0, 0, 0, 0).transform())[1][2]
        for box in machine.clearance if not box.soft
    )
    floor_pitch = math.ceil((hard_height + 2 * pipe_radius + thickness) / grid) * grid
    ids = itertools.count(1)
    sections: list[ProductionSection] = []
    for floor, part in enumerate(_partition_group(group, 3)):
        section = build_compact_fluid_section(
            part, designer, registry, lab_map, belt_tiers=spec.belt_tiers,
            pipe_tiers=spec.pipe_tiers, fluid_items=spec.fluid_items, ids=ids,
            deadline=deadline, centres=centres, include_solids=False, junction_outputs=False,
        )
        sections.append(replace(
            section, placement=transform_placement(section.placement, (0, 0, floor * floor_pitch)),
        ))
    placement = _merge(designer, sections)
    base_plane = min(actor.pose.z for actor in placement.machines) - slab.max[2]
    # Keep genuinely containing native slabs under every machine. Boundary
    # slabs/frame are added only after reserving the actual hole shafts.
    placement = replace(placement, foundations=tuple(
        FoundationObj(next(ids), FOUNDATION_CLASS, Pose(x, y, z, 0))
        for x, y, z in sorted(_support_tiles(placement, registry)) if z > base_plane
    ))
    top_foot = max(actor.pose.z for actor in placement.machines)
    roof_plane = math.ceil((top_foot + hard_height + 3 * grid) / grid) * grid
    stack_height = roof_plane - base_plane + gap
    if stack_height > designer.height_cm:
        raise SectionError(
            "compact factory and native manual seam exceed designer height", cause="height"
        )
    input_spec = spec.model_copy(update={"outputs": {}, "surplus_outputs": {}})
    input_sections = tuple(replace(section, outputs=()) for section in sections)
    lanes = _choose_lanes(input_spec, placement, input_sections, registry, deadline)
    builder = _Builder(placement, registry, ids, deadline)
    for lane in lanes:
        _pipe_lane(builder, input_spec, lab_map, lane, base_plane, roof_plane, thickness)
    placement = replace(
        builder.view(), stack_height_cm=stack_height, stack_connection_gap_cm=gap,
    )
    source_ports = tuple(port for section in sections for port in section.outputs)
    placement = add_coal(placement, spec, registry, lab_map, ids=ids, deadline=deadline)
    placement = add_compact_frame(
        placement, registry, ids=ids, deadline=deadline, base_plane=base_plane,
        roof_plane=roof_plane, stack_height_cm=stack_height,
    )
    placement = add_powered_outputs(
        placement, source_ports, registry, lab_map, ids=ids, deadline=deadline,
        base_plane=base_plane, roof_plane=roof_plane,
        stack_height_cm=stack_height, stack_gap_cm=gap,
    )
    placement = connect_fluid_ports(
        input_spec, placement, input_sections, builder.branches, registry, lab_map,
        ids=ids, deadline=deadline,
    )
    placement = add_wall_power(placement, registry, ids=ids, deadline=deadline)
    placement = add_connection_signs(placement, registry, ids=ids, deadline=deadline)
    exports = dict(spec.outputs)
    for item, rate in spec.surplus_outputs.items():
        exports[item] = exports.get(item, Fraction()) + rate
    actual_inputs = {
        lane.item_id: lane.input_per_second
        for lane in placement.stack_lanes if lane.input_per_second
    }
    actual_outputs = {
        lane.item_id: lane.output_per_second
        for lane in placement.stack_lanes if lane.output_per_second
    }
    if actual_inputs != spec.external_inputs or actual_outputs != exports:
        raise SectionError(
            "compact material boundaries do not preserve the solved flow", cause="balance"
        )
    return replace(
        placement, short_desc=f"{spec.machine_count} machines in connected sections",
        description=(
            f"{spec.label}: {len(sections)} connected three-blender floors; "
            "powered fluid output collectors and aligned vertical pass-through lanes "
            "with native manual seams."
        ),
    )


def mixed_input_factory(
    spec: SfyBuildSpec, designer: Designer, registry: Registry, lab_map: LabMap, deadline: float
) -> SfyPlacement:
    """Four opposing blenders, with solid input decks above the fluid manifolds."""
    group = spec.groups[0]
    machine = registry.buildables[group.machine_class]
    slab = registry.buildables[FOUNDATION_CLASS].clearance[0]
    grid = registry.limits.hologram_grid_cm
    gap = registry.limits.lift_min_cm
    if grid is None or gap is None:
        raise SectionError("mixed-input factory requires native snap dimensions", cause="data")
    x0, y0, x1, y1 = hard_footprint_cm(machine)
    column = grid_ceil((y1 - y0 + 2 * grid) / 2, grid)
    row_pitch = grid_ceil(x1 - x0, grid)
    centres = (
        (-column, -row_pitch / 2 - grid),
        (column, -row_pitch / 2 + grid),
        (-column, row_pitch / 2 - grid),
        (column, row_pitch / 2 + grid),
    )
    inlet_x = [
        port.translation[0]
        for port in machine.ports
        if port.kind == "pipe" and port.direction == "input"
    ]
    ids = itertools.count(1)
    section = build_compact_fluid_section(
        group,
        designer,
        registry,
        lab_map,
        belt_tiers=spec.belt_tiers,
        pipe_tiers=spec.pipe_tiers,
        fluid_items=spec.fluid_items,
        ids=ids,
        deadline=deadline,
        centres=centres,
        include_solids=False,
        junction_outputs=False,
        feed_offset_cm=max(inlet_x) - min(inlet_x) + 2 * grid,
    )
    thickness = slab.max[2] - slab.min[2]
    feet = min(actor.pose.z for actor in section.placement.machines)
    # Match the native base slab phase before deriving any stepped solid lifts.
    section = replace(
        section,
        placement=transform_placement(section.placement, (0, 0, thickness - feet)),
    )
    section = add_inputs(section, spec, registry, lab_map, ids=ids, deadline=deadline)
    sections = (section,)
    placement = _merge(designer, sections)
    base_plane = min(actor.pose.z for actor in placement.machines) - slab.max[2]
    placement = replace(
        placement,
        foundations=tuple(
            FoundationObj(next(ids), FOUNDATION_CLASS, Pose(x, y, z, 0))
            for x, y, z in sorted(_support_tiles(placement, registry))
            if z > base_plane
        ),
    )
    _, high = placement_bounds(placement, registry)
    # Three native lift-length reserves leave room for input collectors,
    # powered output collectors, and the actual roof's floor-hole phase.
    roof_plane = grid_ceil(high[2] + 3 * gap, grid) + thickness / 2
    stack_height = roof_plane - base_plane + gap
    if stack_height > designer.height_cm:
        raise SectionError("mixed-input factory exceeds the native stack envelope", cause="height")
    placement = add_powered_outputs(
        placement,
        section.outputs,
        registry,
        lab_map,
        ids=ids,
        deadline=deadline,
        base_plane=base_plane,
        roof_plane=roof_plane,
        stack_height_cm=stack_height,
        stack_gap_cm=gap,
        inward_pumps=True,
    )
    placement = add_compact_frame(
        placement,
        registry,
        ids=ids,
        deadline=deadline,
        base_plane=base_plane,
        roof_plane=roof_plane,
        stack_height_cm=stack_height,
    )
    input_spec = spec.model_copy(update={"outputs": {}, "surplus_outputs": {}})
    input_sections = (replace(section, outputs=()),)
    # These two slabs receive genuine native holes. Every intermediate support,
    # frame member and powered output remains a physical lane obstacle.
    shaft_view = replace(
        placement,
        foundations=tuple(
            foundation
            for foundation in placement.foundations
            if abs(foundation.pose.z - base_plane) > 1e-6
            and abs(foundation.pose.z - roof_plane) > 1e-6
        ),
    )
    lanes = _choose_lanes(input_spec, shaft_view, input_sections, registry, deadline)
    if any(
        lane.level + (7 * grid if lane.kind == "pipe" else 2 * gap) > roof_plane for lane in lanes
    ):
        raise SectionError("mixed-input lane exceeds the reserved roof", cause="height")
    builder = _Builder(placement, registry, ids, deadline)
    for lane in lanes:
        build_lane = _belt_lane if lane.kind == "belt" else _pipe_lane
        build_lane(builder, input_spec, lab_map, lane, base_plane, roof_plane, thickness)
    placement = replace(
        builder.view(),
        stack_lanes=(*placement.stack_lanes, *builder.lanes),
        stack_height_cm=stack_height,
        stack_connection_gap_cm=gap,
    )
    placement = connect_fluid_ports(
        input_spec,
        placement,
        input_sections,
        builder.branches,
        registry,
        lab_map,
        ids=ids,
        deadline=deadline,
    )
    lattice = Lattice.over(designer, registry)
    nets = _nets(placement, input_sections, builder.branches, registry, lattice)
    occupancy = occupancy_for(
        lattice,
        placement.machines,
        placement.attachments,
        placement.lifts,
        placement.belts,
        registry,
        foundations=placement.foundations,
        pipes=placement.pipes,
        pipe_attachments=placement.pipe_attachments,
        beams=placement.beams,
        passthroughs=placement.passthroughs,
    )
    for net in nets:
        for terminal in (*net.sources, *net.sinks):
            for reserved in (*terminal.reach, terminal.node):
                occupancy.flags[lattice.index(reserved)] = 0
    occupancy.base = bytes(occupancy.flags)
    routed = route_all(
        nets,
        occupancy,
        measures=_measure(registry, designer),
        registry=registry,
        budget=WorkBudget(deadline=deadline),
        deadline=deadline,
        ids=ids,
        belt_class_for=belt_class_for(spec, lab_map),
        lift_class=_lift_class(
            spec, registry, lab_map, max((net.rate for net in nets), default=Fraction())
        ),
    )
    if routed.stranded:
        raise SectionError("mixed-input solid feeds could not all connect", cause="routing")
    placement = replace(
        placement,
        attachments=(
            *placement.attachments,
            *(obj for part in routed.realised for obj in part.attachments),
        ),
        belts=(*placement.belts, *(obj for part in routed.realised for obj in part.belts)),
        lifts=(*placement.lifts, *(obj for part in routed.realised for obj in part.lifts)),
        links=(*placement.links, *(link for part in routed.realised for link in part.links)),
    )
    exports = dict(spec.outputs)
    for item, rate in spec.surplus_outputs.items():
        exports[item] = exports.get(item, Fraction()) + rate
    actual_inputs = {
        lane.item_id: lane.input_per_second
        for lane in placement.stack_lanes
        if lane.input_per_second
    }
    actual_outputs = {
        lane.item_id: lane.output_per_second
        for lane in placement.stack_lanes
        if lane.output_per_second
    }
    if actual_inputs != spec.external_inputs or actual_outputs != exports:
        raise SectionError("mixed-input boundaries changed the solved flow", cause="balance")
    placement = add_wall_power(placement, registry, ids=ids, deadline=deadline)
    placement = add_connection_signs(placement, registry, ids=ids, deadline=deadline)
    return replace(
        placement,
        short_desc=f"{spec.machine_count} machines in connected sections",
        description=(
            f"{spec.label}: four opposing-column machines; native solid ingredient lifts, "
            "source-level Mk2 pumps, floor holes and aligned manual stack seams."
        ),
    )
