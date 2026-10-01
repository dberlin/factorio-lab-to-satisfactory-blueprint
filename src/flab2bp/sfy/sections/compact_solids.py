"""Connected solid coproducts for the native opposing-column compact family.

Side escapes are conveyors, not large wall mergers: the native lift deck clears
all hard factory casing while remaining below the next support slab. Each floor
has one actual merger, and a separate source-backed conveyor shaft repeats at
its genuine roof/base floor-hole components.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import replace
from fractions import Fraction

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, world_port
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS, grid_ceil
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    Link,
    MachineObj,
    PassthroughObj,
    Pose,
    SfyPlacement,
    StackLane,
    belt_ends,
)
from flab2bp.sfy.layout.splines import (
    QUARTER_TURN_TANGENT,
    SplinePoint,
    concat,
    spline_length,
    straight,
)
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_HEIGHT_CM, attachment_boxes
from flab2bp.sfy.registry import Port, Registry
from flab2bp.sfy.sections.construction import (
    Connection,
    _attachment_port,
    _Construction,
    _snap_lift_clear,
)
from flab2bp.sfy.sections.fluids import _materials
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionError,
    SectionPort,
    placement_bounds,
)
from flab2bp.sfy.sections.pipe_routes import _pipe_envelope_radius
from flab2bp.sfy.spec import FOUNDATION_CLASS, SfyBuildSpec

_HOLE = "Build_FoundationPassthrough_Lift_C"


def _move(point: Vector, direction: Vector, distance: float) -> Vector:
    return (
        point[0] + direction[0] * distance,
        point[1] + direction[1] * distance,
        point[2] + direction[2] * distance,
    )


def _grade(start: Vector, finish: Vector, heading: Vector) -> tuple[SplinePoint, ...]:
    run = sum((finish[i] - start[i]) * heading[i] for i in range(3))
    if run <= 0:
        raise SectionError("compact solid grade reverses its actual endpoint headings")
    tangent: Vector = (heading[0] * run, heading[1] * run, heading[2] * run)
    return ((start, tangent, tangent), (finish, tangent, tangent))


def _arc(
    start: Vector,
    incoming: Vector,
    outgoing: Vector,
    radius: float,
    *,
    z: float | None = None,
) -> tuple[SplinePoint, ...]:
    finish = _move(_move(start, incoming, radius), outgoing, radius)
    if z is not None:
        finish = (finish[0], finish[1], z)
    pull = radius * QUARTER_TURN_TANGENT
    a: Vector = (incoming[0] * pull, incoming[1] * pull, incoming[2] * pull)
    b: Vector = (outgoing[0] * pull, outgoing[1] * pull, outgoing[2] * pull)
    return ((start, a, a), (finish, b, b))


def add_inputs(
    section: ProductionSection,
    spec: SfyBuildSpec,
    registry: Registry,
    lab_map: LabMap,
    *,
    ids: Iterator[int],
    deadline: float,
) -> ProductionSection:
    """Feed one solid ingredient through native splitter-to-lift-to-machine snaps.

    Decks clear both the full hard factory casing and the actual fluid junctions.
    Both lift heads meet real opposed device mouths, without spacer conveyors.
    """
    group = section.group
    materials = _materials(
        group, registry.buildables[group.machine_class], registry, lab_map, spec.fluid_items
    )
    solids = [
        (item, port)
        for item, port, direction in materials
        if port.kind == "belt" and direction == "input"
    ]
    if len(solids) != 1:
        raise SectionError(
            "opposing input decks require exactly one solid ingredient", cause="unsupported"
        )
    item, native = solids[0]
    grid = registry.limits.hologram_grid_cm
    step = registry.limits.lift_step_cm
    if grid is None or step is None:
        raise SectionError("native input deck dimensions are unavailable", cause="data")
    build = _Construction(registry, lab_map, spec.belt_tiers, ids, deadline)
    splitter_def = registry.buildables[SPLITTER_CLASS]
    incoming = _attachment_port(splitter_def, "input", (-1, 0, 0))
    outgoing = _attachment_port(splitter_def, "output", (1, 0, 0))
    interfaces: list[SectionPort] = []
    for machine in section.placement.machines:
        side = -1 if machine.pose.x < 0 else 1
        point = world_port(machine.pose.transform(), native)
        bounds = [
            box_bounds(box, machine.pose.transform())
            for box in registry.buildables[machine.class_name].clearance
            if not box.soft
        ]
        yaw = 0 if side < 0 else 180
        native_boxes = attachment_boxes(
            AttachmentObj(0, SPLITTER_CLASS, Pose(0, 0, 0, yaw)), registry
        )
        bottom = min(box.centre[2] - box.reach[2] for box in native_boxes)
        highest = max(high[2] for _, high in bounds)
        fluid_top = max(
            (
                box_bounds(box, node.pose.transform())[1][2]
                for node in section.placement.pipe_attachments
                for box in registry.buildables[node.class_name].clearance
            ),
            default=highest,
        )
        height = grid_ceil(
            max(
                step,
                highest - bottom - point[2] + grid / 20,
                fluid_top
                + grid
                + BELT_CLEARANCE_HALF_HEIGHT_CM
                + max(
                    (
                        _pipe_envelope_radius(registry.buildables[pipe.class_name])
                        for pipe in section.placement.pipes
                    ),
                    default=0,
                )
                - point[2],
            ),
            step,
        )
        deck = point[2] + height
        rate = group.inputs_per_machine[item] * machine.clock / group.clock
        entry, exit_ = build.lift(
            point[0], point[1], deck, point[2], 180 if side < 0 else 0, item, rate,
            snapped=True,
        )
        # Both heads face the splitter's output and the recipe-assigned
        # factory input, whose outward native normals oppose the lift's.
        assert isinstance(entry[0], LiftObj)
        lift = replace(entry[0], top_yaw_deg=0)
        build.lifts[-1] = lift
        entry = (lift, entry[1])
        exit_ = (lift, exit_[1])
        outlet = world_port(Pose(0, 0, 0, yaw).transform(), outgoing)
        splitter = AttachmentObj(
            next(ids),
            SPLITTER_CLASS,
            Pose(point[0] - outlet[0], point[1] - outlet[1], deck - outlet[2], yaw),
        )
        build.attachments.append(splitter)
        build.links.extend((
            Link((splitter.id, outgoing.name), (lift.id, entry[1])),
            Link((lift.id, exit_[1]), (machine.id, native.name)),
        ))
        interfaces.append(SectionPort(item, rate, splitter.id, incoming.name, "input", kind="belt"))
    placement = replace(
        section.placement,
        attachments=(*section.placement.attachments, *build.attachments),
        belts=(*section.placement.belts, *build.belts),
        lifts=(*section.placement.lifts, *build.lifts),
        links=(*section.placement.links, *build.links),
    )
    return replace(
        section,
        placement=placement,
        inputs=(*section.inputs, *interfaces),
        bounds=placement_bounds(placement, registry),
    )


def add_coal(
    placement: SfyPlacement,
    spec: SfyBuildSpec,
    registry: Registry,
    lab_map: LabMap,
    *,
    ids: Iterator[int],
    deadline: float,
) -> SfyPlacement:
    """Add the selected sole belt product; retain every existing fluid object.

    The family has exactly two floors of three opposing-column factories.
    Unsupported material/port arrangements are refused rather than
    inventing a coproduct or silently leaving a factory output disconnected.
    """
    if len(spec.groups) != 1:
        raise SectionError("compact solids require one native recipe group", cause="unsupported")
    group = spec.groups[0]
    products = group.outputs_per_machine.keys() - spec.fluid_items
    if len(products) != 1 or group.inputs_per_machine.keys() - spec.fluid_items:
        raise SectionError(
            "compact solids require one belt product and no belt ingredients", cause="unsupported"
        )
    item = next(iter(products))
    if any(lane.item_id == item for lane in placement.stack_lanes):
        raise SectionError(f"{item}: compact solid export already exists", cause="balance")
    grid = registry.limits.hologram_grid_cm
    step = registry.limits.lift_step_cm
    bend = registry.limits.belt_bend_radius_cm
    lift_half = registry.limits.lift_clearance_half_extent_cm
    if grid is None or step is None or bend is None or lift_half is None:
        raise SectionError("compact solids lack native conveyor snap geometry", cause="data")
    if placement.designer.half_cm < 20 * grid:
        raise SectionError(
            "native compact conveyor escapes exceed this designer floor", cause="bounds"
        )
    build = _Construction(registry, lab_map, spec.belt_tiers, ids, deadline)
    floors: dict[float, list[tuple[MachineObj, Port]]] = defaultdict(list)
    per_machine: dict[int, Fraction] = {}
    for machine in placement.machines:
        if machine.class_name != group.machine_class or machine.recipe_class != group.recipe_class:
            raise SectionError(
                "compact solids encountered another production recipe", cause="unsupported"
            )
        ports = [
            port
            for port in registry.buildables[machine.class_name].ports
            if port.kind == "belt" and port.direction == "output"
        ]
        if len(ports) != 1:
            raise SectionError(
                "compact solids require one actual native belt output", cause="unsupported"
            )
        if any((machine.id, ports[0].name) in (link.a, link.b) for link in placement.links):
            raise SectionError(
                f"{item}: native factory output is already connected", cause="balance"
            )
        floors[machine.pose.z].append((machine, ports[0]))
        per_machine[machine.id] = group.outputs_per_machine[item] * machine.clock / group.clock
    if len(floors) != 2 or any(len(machines) != 3 for machines in floors.values()):
        raise SectionError(
            "compact solids require two floors of three native factories", cause="unsupported"
        )
    total = sum(per_machine.values(), Fraction())
    exported = spec.outputs.get(item, Fraction()) + spec.surplus_outputs.get(item, Fraction())
    if total != exported:
        raise SectionError(f"{item}: compact production and export rates disagree", cause="balance")
    trunk_tier = build.tier(item, total)
    trunk_belt = registry.buildables[machine_class(lab_map, trunk_tier.item_id)]
    if trunk_belt.belt_speed_per_min is None:
        raise SectionError("compact export lacks a native conveyor speed", cause="data")
    if trunk_belt.mesh_bounds_cm is None:
        raise SectionError("compact export lacks native conveyor mesh bounds", cause="data")
    mesh_below = max(BELT_CLEARANCE_HALF_HEIGHT_CM, -trunk_belt.mesh_bounds_cm[0][2])
    capacity = min(trunk_tier.items_per_second, Fraction(str(trunk_belt.belt_speed_per_min)) / 60)
    radius = grid_ceil((bend * 1.5 - 15) * 1.05, grid)
    flat = 2 * grid
    merger_definition = registry.buildables[MERGER_CLASS]

    def face(actor: AttachmentObj, direction: str, heading: Vector) -> Connection:
        for port in merger_definition.ports:
            normal = port_forward(actor.pose.transform(), port)
            if (
                port.direction == direction
                and sum(normal[i] * heading[i] for i in range(3)) > 0.999
            ):
                return actor, port.name
        raise SectionError("compact merger lacks the selected actual native port", cause="data")

    def merger(x: float, y: float, z: float, yaw: float) -> AttachmentObj:
        build.check_deadline()
        actor = AttachmentObj(next(ids), MERGER_CLASS, Pose(x, y, z, yaw))
        build.attachments.append(actor)
        return actor

    def belt(
        points: tuple[SplinePoint, ...], source: Connection, target: Connection, rate: Fraction
    ) -> None:
        build.check_deadline()
        tier = build.tier(item, rate)
        cls = machine_class(lab_map, tier.item_id)
        length = spline_length(points)
        minimum = registry.limits.belt_min_length_cm
        maximum = registry.limits.belt_max_spline_cm
        if minimum is None or maximum is None or not minimum <= length <= maximum:
            raise SectionError(
                "compact conveyor violates its native spline length", cause="geometry"
            )
        actor = BeltRun(next(ids), cls, points, item, rate)
        first, last = belt_ends(registry, cls)
        build.belts.append(actor)
        build.links.extend(
            (
                Link((source[0].id, source[1]), (actor.id, first)),
                Link((actor.id, last), (target[0].id, target[1])),
            )
        )

    def parallel(
        source: Connection,
        target: Connection,
        rate: Fraction,
        *,
        lead: float = flat,
    ) -> None:
        start, heading = build.at(source)
        finish, normal = build.at(target)
        if sum(heading[i] * normal[i] for i in range(3)) > -0.999:
            raise SectionError("compact conveyor grade has incompatible actual port normals")
        a = _move(start, heading, lead)
        b = _move(finish, heading, -lead)
        belt(
            concat(
                straight(start, heading, lead), _grade(a, b, heading), straight(b, heading, lead)
            ),
            source,
            target,
            rate,
        )

    def lift(
        x: float,
        y: float,
        z: float,
        end_z: float,
        input_yaw: float,
        output_yaw: float,
        rate: Fraction,
        *,
        snapped: bool = False,
        holes: tuple[int | None, int | None] = (None, None),
        boundary_start: bool = False,
        boundary_end: bool = False,
    ) -> tuple[Connection, Connection]:
        build.check_deadline()
        tier = build.tier(item, rate)
        definition = registry.buildables[machine_class(lab_map, tier.item_id)]
        choices = sorted(
            (
                candidate
                for candidate in registry.buildables.values()
                if candidate.lift is not None
                and candidate.belt_speed_per_min == definition.belt_speed_per_min
            ),
            key=lambda candidate: candidate.class_name,
        )
        if not choices:
            raise SectionError("compact export has no matching native lift", cause="unsupported")
        height = end_z - z
        minimum = 0.0 if snapped else registry.limits.lift_min_cm
        maximum = registry.limits.lift_max_cm
        if holes != (None, None):
            minimum = thickness / 2 + 2 * step
        if (
            minimum is None
            or maximum is None
            or height == 0
            or not minimum <= abs(height) <= maximum
        ):
            raise SectionError("compact export lift violates its native height", cause="height")
        actor = LiftObj(
            next(ids),
            choices[0].class_name,
            Pose(x, y, z, input_yaw),
            height,
            output_yaw - input_yaw,
            holes,
            boundary_start,
            boundary_end,
        )
        build.lifts.append(actor)
        entry, exit_ = belt_ends(registry, actor.class_name)
        return (actor, entry), (actor, exit_)

    floor_hubs: list[tuple[AttachmentObj, Fraction, float]] = []
    for _foot, sources in sorted(floors.items()):
        build.check_deadline()
        left = sorted(
            (source for source in sources if source[0].pose.x < 0),
            key=lambda source: source[0].pose.y,
        )
        right = [source for source in sources if source[0].pose.x > 0]
        if len(left) != 2 or len(right) != 1:
            raise SectionError(
                "compact solid floor lacks its opposing two-plus-one columns", cause="unsupported"
            )
        highest_case = max(
            high[2]
            for machine, _ in sources
            for box in registry.buildables[machine.class_name].clearance
            if not box.soft
            for _, high in (box_bounds(box, machine.pose.transform()),)
        )
        factory_z = build.at((sources[0][0], sources[0][1].name))[0][2]
        deck = factory_z + grid_ceil(highest_case + mesh_below + 1 - factory_z, step)
        plane = highest_case + BELT_CLEARANCE_HALF_HEIGHT_CM + 1
        hub = merger(0, grid, plane, 0)
        floor_rate = sum((per_machine[machine.id] for machine, _ in sources), Fraction())
        floor_hubs.append((hub, floor_rate, deck))
        for index, (machine, port) in enumerate((*left, *right)):
            rate = per_machine[machine.id]
            start, normal = build.at((machine, port.name))
            heading: Vector = (-1, 0, 0) if machine.pose.x < 0 else (1, 0, 0)
            if sum(normal[i] * heading[i] for i in range(3)) < 0.999:
                raise SectionError(
                    "compact factory solid output does not face its side escape",
                    cause="unsupported",
                )
            direct = _snap_lift_clear(
                registry.buildables[machine.class_name], port, deck - machine.pose.z, lift_half
            )
            mouth = start
            if not direct:
                hard_edge = max(
                    abs(corner[0])
                    for box in registry.buildables[machine.class_name].clearance
                    if not box.soft
                    for bounds in (box_bounds(box, machine.pose.transform()),)
                    for corner in bounds
                )
                distance = grid_ceil(
                    max(build.lead, hard_edge - abs(start[0]) + lift_half + 0.01), grid
                )
                mouth = _move(start, heading, distance)
            top_heading: Vector = (0, 1, 0) if index < 2 else (0, -1, 0)
            entry, exit_ = lift(
                mouth[0],
                mouth[1],
                start[2],
                deck,
                0 if index < 2 else 180,
                90 if index < 2 else -90,
                rate,
                snapped=direct,
            )
            if direct:
                build.links.append(Link((machine.id, port.name), (entry[0].id, entry[1])))
            else:
                build.belt((machine, port.name), entry, item, rate)
            at, _ = build.at(exit_)
            lead = straight(at, top_heading, flat)
            inward: Vector = (1, 0, 0) if index < 2 else (-1, 0, 0)
            turn = _arc(lead[-1][0], top_heading, inward, radius)
            escaped = turn[-1][0]
            if index == 0:
                target = face(hub, "input", (-1, 0, 0))
                finish, _ = build.at(target)
                end_curve = _move(finish, inward, -flat)
                gap_start = _move(end_curve, inward, -grid)
                gap_start = (gap_start[0], gap_start[1], deck)
                # Keep the real mesh above casing until its central-gap descent.
                points = concat(
                    lead,
                    turn,
                    _grade(escaped, gap_start, inward),
                    _grade(gap_start, end_curve, inward),
                    straight(end_curve, inward, flat),
                )
            else:
                target = face(hub, "input", (0, 1 if index == 1 else -1, 0))
                finish, _ = build.at(target)
                begin_turn = (-(radius) if index == 1 else radius, escaped[1], escaped[2])
                last_heading: Vector = (0, -1, 0) if index == 1 else (0, 1, 0)
                last_turn = _arc(begin_turn, inward, last_heading, radius, z=plane)
                points = concat(
                    lead,
                    turn,
                    straight(escaped, inward, math.dist(escaped, begin_turn)),
                    last_turn,
                    straight(last_turn[-1][0], last_heading, abs(finish[1] - last_turn[-1][0][1])),
                )
            belt(points, exit_, target, rate)

    slab = registry.buildables[FOUNDATION_CLASS].clearance[0]
    thickness = slab.max[2] - slab.min[2]
    bottom = min(floors) - thickness / 2
    stack_height = placement.stack_height_cm
    stack_gap = placement.stack_connection_gap_cm
    lift_minimum = registry.limits.lift_min_cm
    if stack_height is None or stack_gap is None or lift_minimum is None:
        raise SectionError("compact conveyor stack lacks native repeat dimensions", cause="data")
    if stack_gap < lift_minimum:
        raise SectionError(
            "compact conveyor stack seam is below native lift minimum", cause="height"
        )
    roof = bottom + stack_height - stack_gap
    # These bays are front of every casing and remain independent of the native
    # side fluid pumps. Actual factory poses/rates above determine all inputs.
    common_x, common_y = 11 * grid, 17.5 * grid
    header_x, header_y = 17 * grid, 11 * grid
    low_header_z = (
        min(build.at((machine, port.name))[0][2] for machine, port in floors[min(floors)])
        + 10 * step
    )
    floor_pitch = max(floors) - min(floors)
    high_header_z = low_header_z + floor_pitch
    upper_header = merger(header_x, header_y, high_header_z, 0)
    lower_header = merger(header_x, header_y, low_header_z, 180)
    low_hole, high_hole = next(ids), next(ids)
    roof_entry, roof_exit = lift(
        common_x,
        common_y,
        roof,
        roof - (thickness / 2 + 3 * step),
        180,
        0,
        capacity,
        holes=(high_hole, None),
        boundary_start=True,
    )
    secondary_z = roof - 3 * step
    roof_drop_in, roof_drop_out = lift(
        header_x, common_y, secondary_z, secondary_z - 4 * step, 180, -90, capacity
    )
    parallel(roof_exit, roof_drop_in, capacity, lead=1.5 * grid)
    parallel(roof_drop_out, face(upper_header, "input", (0, 1, 0)), capacity, lead=1.5 * grid)
    middle_in, middle_out = lift(
        header_x + grid, header_y, high_header_z, low_header_z, 180, 180, capacity
    )
    upper_out = face(upper_header, "output", (1, 0, 0))
    lower_in = face(lower_header, "input", (1, 0, 0))
    build.links.extend(
        (
            Link((upper_out[0].id, upper_out[1]), (middle_in[0].id, middle_in[1])),
            Link((middle_out[0].id, middle_out[1]), (lower_in[0].id, lower_in[1])),
        )
    )
    bottom_in, bottom_exit = lift(
        common_x,
        common_y,
        low_header_z,
        bottom,
        -90,
        -90,
        capacity,
        holes=(None, low_hole),
        boundary_end=True,
    )
    source = face(lower_header, "output", (-1, 0, 0))
    at, heading = build.at(source)
    first = straight(at, heading, flat)
    turning = _arc(first[-1][0], heading, (0, 1, 0), radius)
    finish, _ = build.at(bottom_in)
    belt(
        concat(first, turning, straight(turning[-1][0], (0, 1, 0), finish[1] - turning[-1][0][1])),
        source,
        bottom_in,
        capacity,
    )

    for index, (hub, rate, deck) in enumerate(floor_hubs):
        source = face(hub, "output", (1, 0, 0))
        at, heading = build.at(source)
        # The merger fits below the slab; its outgoing belt gains the small
        # additional real-mesh clearance wholly inside the central casing gap.
        first = straight(at, heading, 1.25 * grid)
        raised = _move(first[-1][0], heading, 1.5 * grid)
        raised = (raised[0], raised[1], deck)
        x = header_x + 2 * grid
        z = deck
        drop_in, drop_out = lift(x, -grid, z, z - 4 * step, 180, 90, rate)
        finish, _ = build.at(drop_in)
        approach = _move(finish, heading, -flat)
        belt(
            concat(
                first,
                _grade(first[-1][0], raised, heading),
                _grade(raised, approach, heading),
                straight(approach, heading, flat),
            ),
            source,
            drop_in,
            rate,
        )
        target = face(lower_header if index == 0 else upper_header, "input", (0, -1, 0))
        start, forward = build.at(drop_out)
        end, _ = build.at(target)
        a = _move(start, forward, flat)
        b = _move(end, forward, -flat)
        belt(
            concat(
                straight(start, forward, flat), _grade(a, b, forward), straight(b, forward, flat)
            ),
            drop_out,
            target,
            rate,
        )

    holes = (
        PassthroughObj(
            low_hole,
            _HOLE,
            Pose(common_x, common_y, bottom, 0),
            thickness,
            top_connection=(bottom_exit[0].id, bottom_exit[1]),
        ),
        PassthroughObj(
            high_hole,
            _HOLE,
            Pose(common_x, common_y, roof, 0),
            thickness,
            bottom_connection=(roof_entry[0].id, roof_entry[1]),
        ),
    )
    lane = StackLane(
        item,
        "belt",
        (bottom_exit[0].id, bottom_exit[1]),
        (roof_entry[0].id, roof_entry[1]),
        Fraction(),
        total,
        capacity,
    )
    return replace(
        placement,
        attachments=(*placement.attachments, *build.attachments),
        belts=(*placement.belts, *build.belts),
        lifts=(*placement.lifts, *build.lifts),
        links=(*placement.links, *build.links),
        passthroughs=(*placement.passthroughs, *holes),
        stack_lanes=(*placement.stack_lanes, lane),
    )
