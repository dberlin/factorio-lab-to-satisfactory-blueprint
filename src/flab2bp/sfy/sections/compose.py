"""Compose connected production units, preserving their internal geometry.

Only the rated section interfaces are routed.  The placement is a single,
flow-ordered arrangement, not a portfolio of arbitrary machine packings.
Boundary conveyors are real, single-use ports before routing starts, so the
local router cannot invent another wall crossing when a join is difficult.
"""

from __future__ import annotations

import itertools
import math
import time
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import replace
from fractions import Fraction

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import BudgetExhausted, WorkBudget
from flab2bp.sfy.geometry import Vector, box_bounds
from flab2bp.sfy.labmap import LabMap, load_lab_map, machine_class
from flab2bp.sfy.layout.corridors import CorridorError
from flab2bp.sfy.layout.floor import foundations
from flab2bp.sfy.layout.grid_nets import (
    GridNet,
    NetError,
    _node_on_ray,
    _reach,
    belt_class_for,
    facing_for,
    snapped,
)
from flab2bp.sfy.layout.lattice import Lattice, occupancy_for
from flab2bp.sfy.layout.manifold import RowError, shortest_belt_cm
from flab2bp.sfy.layout.model import (
    FoundationObj,
    Pose,
    SfyPlacement,
)
from flab2bp.sfy.layout.power import PowerError
from flab2bp.sfy.layout.realise import RealiseError, Terminal
from flab2bp.sfy.layout.rrr import route_all
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import LIFT_NATIVE_CLASS, Registry, load_registry
from flab2bp.sfy.sections.construction import build_section
from flab2bp.sfy.sections.fluids import build_fluid_section
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionError,
    SectionPort,
    endpoint,
    interface_bounds,
    transform_placement,
)
from flab2bp.sfy.sections.pipe_routes import (
    _pipe_envelope_radius,
    _turn_radius,
    connect_fluid_ports,
)
from flab2bp.sfy.sections.power import add_wall_power
from flab2bp.sfy.sections.signs import add_connection_signs
from flab2bp.sfy.sections.stacking import (
    _approach_node,
    _overlap,
    _physical_obstacles,
    build_stack_boundaries,
)
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, SfyBuildSpec, SfyMachineGroup

__all__ = ["SectionLayout"]


class SectionLayout:
    """Connected opposing-row sections, with downstream production upstairs."""

    name = "sections"

    def lay_out(
        self,
        spec: SfyBuildSpec,
        designer: Designer,
        *,
        time_budget_s: float = 15.0,
        absolute_deadline: float | None = None,
        registry: Registry | None = None,
        lab_map: LabMap | None = None,
    ) -> SfyPlacement:
        deadline = time.monotonic() + time_budget_s
        if absolute_deadline is not None:
            deadline = min(deadline, absolute_deadline)
        try:
            _check_deadline(deadline)
            return _compose(
                spec,
                designer,
                load_registry() if registry is None else registry,
                load_lab_map() if lab_map is None else lab_map,
                deadline,
            )
        except (
            SectionError,
            NetError,
            PowerError,
            CorridorError,
            RealiseError,
            RowError,
            BudgetExhausted,
        ) as exc:
            raise NoValidLayout(
                f"connected sections: {exc}",
                spec_label=spec.label,
                budget_s=time_budget_s,
            ) from exc


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SectionError("the original layout deadline expired", cause="deadline")


def _partition_group(group: SfyMachineGroup, maximum: int) -> tuple[SfyMachineGroup, ...]:
    """Split counts, retaining the odd clock and inventory on exactly one machine."""
    if maximum < 1:
        raise SectionError("a section must accommodate at least one machine", cause="capacity")
    pieces: list[SfyMachineGroup] = []
    remaining = group.count
    while remaining:
        count = min(maximum, remaining)
        remaining -= count
        changes: dict[str, object] = {"count": count}
        if remaining:
            changes.update(
                last_clock=group.clock,
                last_power_shards=group.power_shards_per_machine,
                last_power_mw=group.power_mw_per_machine,
            )
        pieces.append(group.model_copy(update=changes))
    return tuple(pieces)


def _production_layers(spec: SfyBuildSpec) -> tuple[tuple[SfyMachineGroup, ...], ...]:
    """Material dependencies, not URL order, define the strictly ascending stages."""
    producers: dict[str, list[int]] = defaultdict(list)
    supply: dict[str, Fraction] = defaultdict(Fraction, spec.external_inputs)
    demand: dict[str, Fraction] = defaultdict(Fraction)
    for index, group in enumerate(spec.groups):
        for item, rate in group.row_outputs.items():
            supply[item] += rate
            producers[item].append(index)
        for item, rate in group.row_inputs.items():
            demand[item] += rate
    for rates in (spec.outputs, spec.surplus_outputs):
        for item, rate in rates.items():
            demand[item] += rate
    for item in sorted(supply.keys() | demand.keys()):
        if supply[item] != demand[item]:
            raise SectionError(
                f"{item}: exact supply {supply[item]}/s differs from demand {demand[item]}/s",
                cause="balance",
            )
    parents = [
        {producer for item in group.row_inputs for producer in producers[item]}
        for group in spec.groups
    ]
    remaining = set(range(len(spec.groups)))
    layers: list[tuple[SfyMachineGroup, ...]] = []
    while remaining:
        ready = sorted(index for index in remaining if not parents[index] & remaining)
        if not ready:
            recipes = ", ".join(spec.groups[index].recipe_id for index in sorted(remaining))
            raise SectionError(
                f"cyclic material dependencies require a supported recycle section: {recipes}",
                cause="cycle",
            )
        layers.append(tuple(spec.groups[index] for index in ready))
        remaining.difference_update(ready)
    return tuple(layers)


def _ceil(value: float, grid: float) -> float:
    return math.ceil((value - 1e-6) / grid) * grid


def _section_frame(
    section: ProductionSection, registry: Registry
) -> tuple[tuple[Vector, Vector], tuple[float, float]]:
    """Reserve only genuine port approaches, not a border around every side."""
    grid = registry.limits.hologram_grid_cm
    lift_half = registry.limits.lift_clearance_half_extent_cm
    if grid is None or lift_half is None:
        raise SectionError("the registry lacks section approach geometry", cause="data")
    ports = (*section.inputs, *section.outputs)
    reference = next((port for port in ports if port.kind == "belt"), ports[0])
    anchor_y = endpoint(section.placement, reference.object_id, reference.port, registry)[0][1]
    anchor_x = section.placement.machines[0].pose.x
    anchors = (anchor_x, anchor_y)
    low, high = map(list, section.bounds)
    measures = _measure(registry, section.placement.designer)
    # Reserve the swept ribbon or lift shaft at the interface's lattice turn.
    # A turning splitter is optional, not the minimum escape geometry.
    turning_half = max(BELT_CLEARANCE_HALF_WIDTH_CM, lift_half)
    wall_space = (
        _ceil(max(measures.lead_in, 2 * grid), grid)
        + _ceil(measures.lead_in, grid)
        + measures.reach
    )
    for port in (*section.inputs, *section.outputs):
        point, normal = endpoint(section.placement, port.object_id, port.port, registry)
        if port.kind == "pipe":
            radius = max(
                (
                    _pipe_envelope_radius(registry.buildables[run.class_name])
                    for run in section.placement.pipes
                    if run.item_id == port.item_id
                ),
                default=0.0,
            )
            if not radius:
                raise SectionError("pipe interface lacks native mesh geometry", cause="data")
            # Pipe routes curve directly from their interface; they need
            # neither a belt lead nor a lift beyond the whole machine row.
            turn_radius = _turn_radius(registry)
            for axis in (0, 1):
                turn = point[axis] + normal[axis] * turn_radius
                low[axis] = min(low[axis], turn - radius - 1)
                high[axis] = max(high[axis], turn + radius + 1)
            continue
        axis = 0 if abs(normal[0]) > abs(normal[1]) else 1
        sign = 1 if normal[axis] > 0 else -1
        bounds = interface_bounds(section.placement, port.object_id, port.port, registry)
        edge = bounds[1 if sign > 0 else 0][axis]
        lead = max(shortest_belt_cm(registry.limits), (edge - point[axis]) * sign + lift_half)
        # All eventual translations preserve this frame's lattice phase.
        target = point[axis] + sign * lead - anchors[axis]
        node = sign * _ceil(sign * target, grid) + anchors[axis]
        low[axis] = min(low[axis], node - turning_half)
        high[axis] = max(high[axis], node + turning_half)
        transverse = 1 - axis
        low[transverse] = min(low[transverse], point[transverse] - turning_half)
        high[transverse] = max(high[transverse], point[transverse] + turning_half)
        # A wall conveyor and its first turn need a real lead before reaching
        # the interface. This affects a shallow one-row unit, not the long
        # machine sides of a full-depth opposing-row section.
        if port.direction == "input":
            low[1] = min(low[1], point[1] - wall_space)
        else:
            high[1] = max(high[1], point[1] + wall_space)
    return (
        ((low[0], low[1], low[2]), (high[0], high[1], high[2])),
        anchors,
    )


def _sections_for(
    group: SfyMachineGroup,
    spec: SfyBuildSpec,
    designer: Designer,
    registry: Registry,
    lab_map: LabMap,
    ids: Iterator[int],
    deadline: float,
) -> tuple[ProductionSection, ...]:
    """Bisect only an oversized unit, never retry a packed factory arrangement."""
    _check_deadline(deadline)
    try:
        if spec.fluid_items.intersection((*group.row_inputs, *group.row_outputs)):
            section = build_fluid_section(
                group,
                designer,
                registry,
                lab_map,
                belt_tiers=spec.belt_tiers,
                pipe_tiers=spec.pipe_tiers,
                fluid_items=spec.fluid_items,
                ids=ids,
                deadline=deadline,
            )
        else:
            section = build_section(
                group,
                designer,
                registry,
                lab_map,
                belt_tiers=spec.belt_tiers,
                ids=ids,
                deadline=deadline,
            )
        (low, high), anchors = _section_frame(section, registry)
        grid = registry.limits.hologram_grid_cm
        assert grid is not None  # _section_frame requires the registry grid.
        offsets = tuple(
            _ceil(-designer.half_cm - low[axis] + anchors[axis], grid) - anchors[axis]
            for axis in (0, 1)
        )
        if all(high[axis] + offsets[axis] <= designer.half_cm + 1e-6 for axis in (0, 1)):
            return (section,)
        failure = SectionError(
            f"{group.recipe_id}: section and actual interface approaches need "
            f"{high[0] - low[0]:g} x {high[1] - low[1]:g} cm in the {designer.mark} designer",
            cause="bounds",
        )
    except SectionError as exc:
        if exc.cause not in {"bounds", "capacity"}:
            raise
        failure = exc
    if group.count == 1:
        raise failure
    return tuple(
        section
        for piece in _partition_group(group, (group.count + 1) // 2)
        for section in _sections_for(piece, spec, designer, registry, lab_map, ids, deadline)
    )


def _arrange(
    stages: Sequence[Sequence[ProductionSection]],
    designer: Designer,
    registry: Registry,
    deadline: float,
) -> tuple[ProductionSection, ...]:
    """Pack whole frames bottom-left, never below any of their producers."""
    grid = registry.limits.hologram_grid_cm
    if grid is None or grid <= 0:
        raise SectionError("the registry has no positive hologram grid", cause="data")
    slab = registry.buildables[FOUNDATION_CLASS].clearance[0]
    thickness = slab.max[2] - slab.min[2]
    bottom, edge = -designer.half_cm, designer.half_cm
    gap = _ceil(2 * BELT_CLEARANCE_HALF_WIDTH_CM + grid, grid)
    # Floors retain complete approach envelopes, not just machine footprints.
    # Their Z positions are assigned after packing so a taller section filling
    # a lower floor cannot intersect an already positioned upper floor.
    floors: list[list[tuple[float, float, float, float]]] = []
    heights: list[float] = []
    producers: dict[str, int] = {}
    assignments: list[tuple[ProductionSection, int, float, float, float]] = []
    for stage in stages:
        for section in stage:
            _check_deadline(deadline)
            (low, high), (anchor_x, anchor_y) = _section_frame(section, registry)
            minimum_floor = max(
                (producers[item] for item in section.group.row_inputs if item in producers),
                default=0,
            )
            chosen: tuple[int, float, float] | None = None
            for index in range(minimum_floor, len(floors) + 1):
                rectangles = floors[index] if index < len(floors) else []
                # Existing right/top edges are sufficient bottom-left candidates
                # for this deterministic pass. No rotations or factory retries.
                xs = sorted({bottom, *(rectangle[2] + gap for rectangle in rectangles)})
                ys = sorted({bottom, *(rectangle[3] + gap for rectangle in rectangles)})
                for y in ys:
                    dy = _ceil(y - low[1] + anchor_y, grid) - anchor_y
                    if high[1] + dy > edge + 1e-6:
                        continue
                    for x in xs:
                        _check_deadline(deadline)
                        dx = _ceil(x - low[0] + anchor_x, grid) - anchor_x
                        if high[0] + dx > edge + 1e-6:
                            continue
                        if any(
                            not (
                                high[0] + dx + gap <= left + 1e-6
                                or right + gap <= low[0] + dx + 1e-6
                                or high[1] + dy + gap <= front + 1e-6
                                or back + gap <= low[1] + dy + 1e-6
                            )
                            for left, front, right, back in rectangles
                        ):
                            continue
                        chosen = index, dx, dy
                        break
                    if chosen is not None:
                        break
                if chosen is not None:
                    break
            if chosen is None:
                raise SectionError(
                    f"{section.group.recipe_id}: complete section approaches do not fit "
                    f"the {designer.mark} designer",
                    cause="bounds",
                )
            index, dx, dy = chosen
            if index == len(floors):
                floors.append([])
                heights.append(0.0)
            floors[index].append((low[0] + dx, low[1] + dy, high[0] + dx, high[1] + dy))
            # Preserve deliberately elevated machine stands, including refinery
            # outlets raised to feed a pump without assuming producer head.
            local_floor = min(
                thickness, min(machine.pose.z for machine in section.placement.machines)
            )
            heights[index] = max(heights[index], high[2] - local_floor)
            assignments.append((section, index, dx, dy, local_floor))
            for item in section.group.row_outputs:
                producers[item] = max(producers.get(item, 0), index)
    floor_z: list[float] = []
    floor = _ceil(thickness, grid)
    for height in heights:
        _check_deadline(deadline)
        floor_z.append(floor)
        # The measured height already includes physical transport envelopes.
        # Separate floors by their actual slab thickness, not another aisle.
        floor = _ceil(floor + height + thickness, grid)
    required_height = floor_z[-1] + heights[-1] if heights else 0.0
    if required_height > designer.height_cm + 1e-6:
        raise SectionError(
            f"{designer.mark} designer height {designer.height_cm:g} cm cannot contain "
            f"{len(floors)} production floors; complete sections need "
            f"{required_height:g} cm including clearance",
            cause="height",
        )
    placed: list[ProductionSection] = []
    for section, index, dx, dy, local_floor in assignments:
        _check_deadline(deadline)
        dz = floor_z[index] - local_floor
        moved = transform_placement(section.placement, (dx, dy, dz))
        actual_low, actual_high = section.bounds
        bounds = (
            (actual_low[0] + dx, actual_low[1] + dy, actual_low[2] + dz),
            (actual_high[0] + dx, actual_high[1] + dy, actual_high[2] + dz),
        )
        placed.append(replace(section, placement=moved, bounds=bounds))
    return tuple(placed)


def _merge(designer: Designer, sections: Sequence[ProductionSection]) -> SfyPlacement:
    return SfyPlacement(
        designer=designer,
        machines=tuple(obj for section in sections for obj in section.placement.machines),
        attachments=tuple(obj for section in sections for obj in section.placement.attachments),
        belts=tuple(obj for section in sections for obj in section.placement.belts),
        lifts=tuple(obj for section in sections for obj in section.placement.lifts),
        pipes=tuple(obj for section in sections for obj in section.placement.pipes),
        pipe_attachments=tuple(
            obj for section in sections for obj in section.placement.pipe_attachments
        ),
        links=tuple(link for section in sections for link in section.placement.links),
        signs=tuple(obj for section in sections for obj in section.placement.signs),
    )


def _floors(
    placement: SfyPlacement, registry: Registry, ids: Iterator[int]
) -> tuple[FoundationObj, ...]:
    """Upper floors cover machine feet, leaving the peripheral lift shafts open."""
    designer = placement.designer
    base = foundations(designer, registry, ids)
    definition = registry.buildables[FOUNDATION_CLASS].clearance[0]
    base_top = definition.max[2] - definition.min[2]
    side, half = designer.foundation_cm, designer.half_cm
    grid = registry.limits.hologram_grid_cm
    if grid is None:
        raise SectionError("the registry lacks the support placement grid", cause="data")
    tiles: set[tuple[float, float, float]] = set()

    def centres(low: float, high: float) -> tuple[float, ...]:
        # Align the outside edge to the supported footprint, not the designer's
        # global tiling: global tiles can otherwise pave over a reserved shaft.
        # Foundation poses are stored as float32. Cover outward-rounded grid
        # extents so that quantising a centre cannot uncover a thin foot edge.
        low = max(-half, math.floor(low / grid) * grid)
        high = min(half, math.ceil(high / grid) * grid)
        if high - low <= side:
            return (max(-half + side / 2, min(half - side / 2, (low + high) / 2)),)
        count = math.ceil((high - low) / side)
        first, last = low + side / 2, high - side / 2
        return tuple(first + (last - first) * index / (count - 1) for index in range(count))

    for machine in placement.machines:
        z = machine.pose.z
        if abs(z - base_top) < 1e-6:
            continue
        for box in registry.buildables[machine.class_name].clearance:
            if box.soft:
                continue
            low, high = box_bounds(box, machine.pose.transform())
            xs = centres(low[0], high[0])
            ys = centres(low[1], high[1])
            tiles.update((x, y, z) for x in xs for y in ys)
    return (
        *base,
        *(
            FoundationObj(next(ids), FOUNDATION_CLASS, Pose(x, y, z - definition.max[2], 0.0))
            for x, y, z in sorted(tiles)
        ),
    )


def _terminal(
    placement: SfyPlacement,
    port: SectionPort,
    registry: Registry,
    lattice: Lattice,
    obstacles: Sequence[tuple[Vector, Vector]],
) -> Terminal:
    world, normal = endpoint(placement, port.object_id, port.port, registry)
    facing = facing_for(normal, lattice, port.port)
    node = _node_on_ray(world, facing, lattice, port.port)
    first_node = node
    low, high = interface_bounds(placement, port.object_id, port.port, registry)
    # A connected section owns a flat lead before any turn or lift. Choosing
    # the nearest lattice node can leave a 20 cm connector-to-lift belt,
    # although the realiser (correctly) requires a complete authored belt.
    axis = 0 if abs(facing[0]) > abs(facing[1]) else 1
    sign = 1 if facing[axis] > 0 else -1
    node = _approach_node(world, facing, first_node, registry, lattice, (low, high))
    # Native ingredient bodies below and a support slab above can rule out
    # both lift directions. Keep the small shaft lead where it works; only a
    # trapped mouth needs the additional clearance for a turning attachment.
    lift_half, lift_min = (
        registry.limits.lift_clearance_half_extent_cm,
        registry.limits.lift_min_cm,
    )
    thickness = registry.buildables[FOUNDATION_CLASS].height_cm
    if lift_half is None or lift_min is None or thickness is None:
        raise SectionError("the registry lacks interface escape geometry", cause="data")
    x, y, z = lattice.world(node)
    shafts = (
        ((x - lift_half, y - lift_half, min(z, end)), (x + lift_half, y + lift_half, max(z, end)))
        for end in (z - lift_min, z + lift_min)
        if thickness <= end <= placement.designer.height_cm
    )
    if not any(not any(_overlap(shaft, obstacle) for obstacle in obstacles) for shaft in shafts):
        turn_half = _measure(registry, placement.designer).box
        edge = (high if sign > 0 else low)[axis]
        lead = max(shortest_belt_cm(registry.limits), (edge - world[axis]) * sign + turn_half)
        distance = (lattice.world(node)[axis] - world[axis]) * sign
        extra = max(0, math.ceil((lead - distance) / lattice.grid_cm))
        moved = list(node)
        moved[axis] += sign * extra
        candidate = (moved[0], moved[1], moved[2])
        if lattice.holds(candidate):
            node = candidate
    steps = abs(node[axis] - first_node[axis])
    if not lattice.holds(node):
        raise SectionError(
            f"{port.item_id}: the section interface has no room for its flat lead", cause="bounds"
        )
    reach = set(_reach(first_node, facing, low, high, lattice))
    transverse = 1 - axis
    for step in range(steps + 1):
        for side in (-1, 0, 1):
            reached = list(first_node)
            reached[axis] += sign * step
            reached[transverse] += side
            reserved = (reached[0], reached[1], reached[2])
            if lattice.holds(reserved):
                reach.add(reserved)
    return Terminal(
        node,
        snapped(world, node, lattice),
        facing,
        (port.object_id, port.port),
        "port",
        tuple(sorted(reach)),
    )


def _nets(
    placement: SfyPlacement,
    sections: Sequence[ProductionSection],
    boundaries: Sequence[SectionPort],
    registry: Registry,
    lattice: Lattice,
) -> tuple[GridNet, ...]:
    by_item: dict[str, list[SectionPort]] = defaultdict(list)
    for section in sections:
        for port in (*section.inputs, *section.outputs):
            if port.kind == "belt":
                by_item[port.item_id].append(port)
    for port in boundaries:
        if port.kind == "belt":
            by_item[port.item_id].append(port)
    thickness = registry.buildables[FOUNDATION_CLASS].height_cm
    if thickness is None:
        raise SectionError("the registry lacks interface escape geometry", cause="data")
    obstacles = tuple(_physical_obstacles(placement, registry, thickness))
    nets: list[GridNet] = []
    for item, ports in sorted(by_item.items()):
        sources = [port for port in ports if port.direction == "output"]
        sinks = [port for port in ports if port.direction == "input"]
        supplied = sum((port.items_per_second for port in sources), Fraction())
        consumed = sum((port.items_per_second for port in sinks), Fraction())
        if not sources or not sinks or supplied != consumed:
            raise SectionError(
                f"{item}: section interfaces do not balance "
                f"({supplied}/s supplied, {consumed}/s consumed)",
                cause="balance",
            )
        source_terminals = tuple(
            _terminal(placement, port, registry, lattice, obstacles) for port in sources
        )
        sink_terminals = [
            (
                _terminal(placement, port, registry, lattice, obstacles),
                port.items_per_second,
            )
            for port in sinks
        ]
        # Section fan-out needs a trunk long enough to accommodate later taps.
        # Keep this placement policy out of the router: other layouts supply
        # their own intentional branch order.
        sink_terminals.sort(
            key=lambda pair: min(
                sum(abs(a - b) for a, b in zip(source.node, pair[0].node, strict=True))
                for source in source_terminals
            ),
            reverse=True,
        )
        remaining_sources = list(
            zip(source_terminals, (port.items_per_second for port in sources), strict=True)
        )
        remaining_sinks = []
        # A native boundary body may already expose one side for each complete
        # section flow. Join equal obligations directly instead of rebuilding
        # that fan-out with extra taps in a narrow routing corridor.
        for sink, rate in sink_terminals:
            matches = [pair for pair in remaining_sources if pair[1] == rate]
            if not matches:
                remaining_sinks.append((sink, rate))
                continue
            source, _ = min(
                matches,
                key=lambda pair: sum(
                    abs(a - b) for a, b in zip(pair[0].node, sink.node, strict=True)
                ),
            )
            remaining_sources.remove((source, rate))
            nets.append(GridNet(len(nets) + 1, item, (source,), (sink,), rate, (rate,), (rate,)))
        if remaining_sinks:
            nets.append(
                GridNet(
                    len(nets) + 1,
                    item,
                    tuple(terminal for terminal, _ in remaining_sources),
                    tuple(terminal for terminal, _ in remaining_sinks),
                    sum((rate for _, rate in remaining_sources), Fraction()),
                    tuple(rate for _, rate in remaining_sinks),
                    tuple(rate for _, rate in remaining_sources),
                )
            )
    return tuple(nets)


def _lift_class(spec: SfyBuildSpec, registry: Registry, lab_map: LabMap, required: Fraction) -> str:
    # The shared routing profile must carry the busiest joined stream, not the
    # maximum tier merely available to the factory.
    tier = next((tier for tier in spec.belt_tiers if tier.items_per_second >= required), None)
    if tier is None:
        raise SectionError(
            f"joined conveyor streams need {required}/s above available capacity",
            cause="capacity",
        )
    belt = machine_class(lab_map, tier.item_id)
    speed = registry.buildables[belt].belt_speed_per_min
    choices = sorted(
        buildable.class_name
        for buildable in registry.buildables.values()
        if buildable.native_class == LIFT_NATIVE_CLASS
        and buildable.lift is not None
        and buildable.belt_speed_per_min == speed
    )
    if not choices:
        raise SectionError(
            f"no funded conveyor lift with capacity {tier.items_per_second}/s", cause="data"
        )
    return choices[0]


def _compose(
    spec: SfyBuildSpec, designer: Designer, registry: Registry, lab_map: LabMap, deadline: float
) -> SfyPlacement:
    stages = _production_layers(spec)
    if not stages:
        raise SectionError("a factory needs a production section", cause="unsupported")
    _check_deadline(deadline)
    ids = itertools.count(1)
    measures = _measure(registry, designer)
    lattice = Lattice.over(designer, registry)
    built = tuple(
        tuple(
            section
            for group in stage
            for section in _sections_for(group, spec, designer, registry, lab_map, ids, deadline)
        )
        for stage in stages
    )
    sections = _arrange(built, designer, registry, deadline)
    placement = _merge(designer, sections)
    placement = replace(placement, foundations=_floors(placement, registry, ids))
    placement, boundary_ports = build_stack_boundaries(
        spec,
        placement,
        sections,
        registry,
        lab_map,
        ids=ids,
        deadline=deadline,
    )
    placement = connect_fluid_ports(
        spec, placement, sections, boundary_ports, registry, lab_map, ids=ids, deadline=deadline
    )
    nets = _nets(placement, sections, boundary_ports, registry, lattice)
    _check_deadline(deadline)
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
    # Protect each known approach from other item nets before any join is laid.
    # The router opens only the current net's own Terminal.reach reservation.
    for net in nets:
        for terminal in (*net.sources, *net.sinks):
            for reserved in (*terminal.reach, terminal.node):
                occupancy.flags[lattice.index(reserved)] = 0
    occupancy.base = bytes(occupancy.flags)
    routed = route_all(
        nets,
        occupancy,
        measures=measures,
        registry=registry,
        budget=WorkBudget(deadline=deadline),
        deadline=deadline,
        ids=ids,
        belt_class_for=belt_class_for(spec, lab_map),
        lift_class=_lift_class(
            spec, registry, lab_map, max((net.rate for net in nets), default=Fraction())
        ),
    )
    _check_deadline(deadline)
    if routed.stranded:
        failures = ", ".join(
            f"{net.item_id} ({failure.kind.value if failure.kind else 'unconnected'})"
            for net, failure in routed.stranded
        )
        raise SectionError(
            f"the reserved section logistics could not connect {failures}", cause="routing"
        )
    result = replace(
        placement,
        attachments=(
            *placement.attachments,
            *(obj for part in routed.realised for obj in part.attachments),
        ),
        belts=(*placement.belts, *(obj for part in routed.realised for obj in part.belts)),
        lifts=(*placement.lifts, *(obj for part in routed.realised for obj in part.lifts)),
        links=(*placement.links, *(link for part in routed.realised for link in part.links)),
        description=(
            f"{spec.label or 'Satisfactory factory'}: {len(sections)} connected production "
            f"sections in {len(stages)} material stages; aligned vertical pass-through "
            "lanes with manual connections between stacked modules."
        ),
        short_desc=f"{spec.machine_count} machines in connected sections",
    )
    exports = defaultdict(Fraction, spec.outputs)
    for item, rate in spec.surplus_outputs.items():
        exports[item] += rate
    lanes = {lane.item_id: lane for lane in result.stack_lanes}
    for item in spec.external_inputs.keys() | exports.keys():
        lane = lanes.get(item)
        if (
            lane is None
            or lane.input_per_second != spec.external_inputs.get(item, Fraction())
            or lane.output_per_second != exports.get(item, Fraction())
        ):
            raise SectionError(
                f"{item}: the rated stack boundary contract was not preserved", cause="routing"
            )
    _check_deadline(deadline)
    result = add_wall_power(result, registry, ids=ids, deadline=deadline)
    return add_connection_signs(result, registry, ids=ids, deadline=deadline)
