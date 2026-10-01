"""Two opposing-column blender floors with powered peripheral output risers."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from fractions import Fraction

from flab2bp.sfy.geometry import box_bounds
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.manifold import hard_footprint_cm
from flab2bp.sfy.layout.model import FoundationObj, Pose, SfyPlacement
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.sections.compact_solids import add_coal
from flab2bp.sfy.sections.compose import _merge, _partition_group
from flab2bp.sfy.sections.fluids import build_compact_fluid_section
from flab2bp.sfy.sections.model import ProductionSection, SectionError, transform_placement
from flab2bp.sfy.sections.pipe_routes import connect_fluid_ports
from flab2bp.sfy.sections.power import add_wall_power
from flab2bp.sfy.sections.powered_outputs import (
    _support_tiles,
    add_compact_frame,
    add_powered_outputs,
)
from flab2bp.sfy.sections.signs import add_connection_signs
from flab2bp.sfy.sections.stacking import _Builder, _choose_lanes, _pipe_lane
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
