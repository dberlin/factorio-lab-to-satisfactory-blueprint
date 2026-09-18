"""Build opposing production rows around shared, ingredient-separated spines.

A column is constructed as machines plus their splitter branches, not packed
first and routed later. Mirrored input-port assignment keeps each ingredient's
branches aligned across genuinely opposing machines. Output rows drain through
one merger per machine and join outside the machine footprint, once per product.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Sequence
from fractions import Fraction

from flab2bp.sfy.geometry import Vector, box_bounds, port_forward, world_port
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.corridors import Route, attachment_box_cm, lay_path
from flab2bp.sfy.layout.manifold import (
    MERGER_CLASS,
    SPLITTER_CLASS,
    grid_ceil,
    hard_footprint_cm,
    machine_pitch_cm,
    shortest_belt_cm,
    slab_top_cm,
)
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    Link,
    MachineObj,
    Pose,
    SfyPlacement,
    belt_ends,
    lift_geometry,
)
from flab2bp.sfy.layout.validate import (
    BELT_CLEARANCE_HALF_HEIGHT_CM,
    BELT_CLEARANCE_HALF_WIDTH_CM,
    attachment_boxes,
)
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionError,
    SectionPort,
    placement_bounds,
    transform_placement,
)
from flab2bp.sfy.spec import Designer, SfyMachineGroup
from flab2bp.spec import BeltTier

Actor = MachineObj | AttachmentObj | LiftObj
Connection = tuple[Actor, str]


def _ports(buildable: Buildable, direction: str) -> tuple[Port, ...]:
    return tuple(
        sorted(
            (
                port
                for port in buildable.ports
                if port.kind == "belt" and port.direction == direction
            ),
            key=lambda port: (port.translation[0], port.name),
        )
    )


def _attachment_port(buildable: Buildable, direction: str, heading: Vector) -> Port:
    pose = Pose(0, 0, 0, 0).transform()
    choices = _ports(buildable, direction)
    for port in choices:
        normal = port_forward(pose, port)
        if sum(normal[i] * heading[i] for i in range(3)) > 0.999:
            return port
    raise SectionError(
        f"{buildable.class_name} has no {direction} facing {heading}", cause="unsupported"
    )


class _Construction:
    def __init__(
        self,
        registry: Registry,
        lab_map: LabMap,
        tiers: Sequence[BeltTier],
        ids: Iterator[int],
        deadline: float,
    ) -> None:
        self.registry = registry
        self.lab_map = lab_map
        self.tiers = tuple(sorted(tiers, key=lambda tier: tier.items_per_second))
        self.ids = ids
        self.deadline = deadline
        self.machines: list[MachineObj] = []
        self.attachments: list[AttachmentObj] = []
        self.belts: list[BeltRun] = []
        self.lifts: list[LiftObj] = []
        self.links: list[Link] = []
        self.lead = shortest_belt_cm(registry.limits)

    def check_deadline(self) -> None:
        if time.monotonic() >= self.deadline:
            raise SectionError("section construction exceeded its time budget", cause="deadline")

    def tier(self, item: str, rate: Fraction) -> BeltTier:
        for tier in self.tiers:
            if rate <= tier.items_per_second:
                return tier
        ceiling = self.tiers[-1].items_per_second if self.tiers else Fraction(0)
        raise SectionError(
            f"one section interface for {item!r} needs {rate} items/s, "
            f"above the available belt ceiling of {ceiling} items/s",
            cause="capacity",
        )

    def at(self, connection: Connection) -> tuple[Vector, Vector]:
        actor, name = connection
        if isinstance(actor, LiftObj):
            entry, exit_ = belt_ends(self.registry, actor.class_name)
            geometry = lift_geometry(self.registry, actor.class_name)
            if name == entry:
                return actor.bottom_end(geometry)
            if name == exit_:
                return actor.top_end(geometry)
            raise SectionError(f"unknown lift connection {name}")
        port = next(
            port for port in self.registry.buildables[actor.class_name].ports if port.name == name
        )
        pose = actor.pose.transform()
        return world_port(pose, port), port_forward(pose, port)

    def attachment(self, class_name: str, x: float, y: float, z: float) -> AttachmentObj:
        self.check_deadline()
        actor = AttachmentObj(next(self.ids), class_name, Pose(x, y, z, 0))
        self.attachments.append(actor)
        return actor

    def belt(self, source: Connection, target: Connection, item: str, rate: Fraction) -> None:
        self.check_deadline()
        start, facing = self.at(source)
        finish, normal = self.at(target)
        delta = (finish[0] - start[0], finish[1] - start[1], finish[2] - start[2])
        length = math.dist(start, finish)
        if length < self.lead - 0.01:
            raise SectionError(
                f"{item!r} section branch is {length:.2f} cm; needs {self.lead:.0f} cm"
            )
        direction = (delta[0] / length, delta[1] / length, delta[2] / length)
        if (
            sum(direction[i] * facing[i] for i in range(3)) < 0.999
            or sum(-direction[i] * normal[i] for i in range(3)) < 0.999
        ):
            raise SectionError(f"{item!r} section branch is not aligned to its actual connectors")
        tier = self.tier(item, rate)
        route = Route(start, direction)
        route.go(length)
        laid = lay_path(
            route,
            registry=self.registry,
            class_name=machine_class(self.lab_map, tier.item_id),
            item_id=item,
            rate=rate,
            ids=self.ids,
            upstream=(source[0].id, source[1]),
            downstream=(target[0].id, target[1]),
        )
        self.belts.extend(laid.belts)
        self.links.extend(laid.links)

    def lift(
        self,
        x: float,
        y: float,
        start_z: float,
        end_z: float,
        heading: float,
        item: str,
        rate: Fraction,
    ) -> tuple[Connection, Connection]:
        tier = self.tier(item, rate)
        belt = self.registry.buildables[machine_class(self.lab_map, tier.item_id)]
        # Belt/lift correspondence is selected by native class and actual speed,
        # never by rewriting a class-name suffix or assuming the available marks.
        choices = sorted(
            (
                obj
                for obj in self.registry.buildables.values()
                if obj.lift is not None and obj.belt_speed_per_min == belt.belt_speed_per_min
            ),
            key=lambda obj: obj.class_name,
        )
        if not choices:
            raise SectionError(f"no lift matches conveyor {belt.class_name}", cause="unsupported")
        height = end_z - start_z
        low, high = self.registry.limits.lift_min_cm, self.registry.limits.lift_max_cm
        if low is None or high is None or not low <= abs(height) <= high:
            raise SectionError(
                f"section lift height {abs(height):.0f} cm is outside {low}..{high} cm",
                cause="height",
            )
        actor = LiftObj(
            next(self.ids), choices[0].class_name, Pose(x, y, start_z, heading), height, 180
        )
        self.lifts.append(actor)
        entry, exit_ = belt_ends(self.registry, actor.class_name)
        return (actor, entry), (actor, exit_)

    def placement(self, designer: Designer) -> SfyPlacement:
        return SfyPlacement(
            designer=designer,
            machines=tuple(self.machines),
            attachments=tuple(self.attachments),
            belts=tuple(self.belts),
            lifts=tuple(self.lifts),
            links=tuple(self.links),
        )


def _snap_lift_clear(machine: Buildable, port: Port, top_z: float, width: float) -> bool:
    """A snapped lift may share its port's box, never another part of a machine.

    Foundry upper machinery covers one input column; its lift must stand outside.
    Assembler/manufacturer inputs occupy lower projecting trays, so snapping there
    avoids spending another conveyor-length of central aisle for every ingredient.
    """
    x, y, z = port.translation
    for box in machine.clearance:
        if box.soft:
            continue
        low, high = box_bounds(box, Pose(0, 0, 0, 0).transform())
        if not (low[0] - width < x < high[0] + width and low[1] - width < y < high[1] + width):
            continue
        contains_port = all(
            low[i] - 0.01 <= port.translation[i] <= high[i] + 0.01 for i in range(3)
        )
        if contains_port:
            # The belt arriving at the upper end is not attached to the machine,
            # so it must actually clear even the box the lift itself may share.
            if top_z - BELT_CLEARANCE_HALF_HEIGHT_CM <= high[2]:
                return False
        elif min(top_z, high[2]) > max(z, low[2]):
            return False
    return True


def _machine_ports(
    group: SfyMachineGroup, machine: Buildable
) -> tuple[tuple[Port, ...], tuple[Port, ...]]:
    if any(port.kind == "pipe" for port in machine.ports):
        raise SectionError(
            f"{group.recipe_id}: sections currently support solid transport only",
            cause="unsupported",
        )
    inputs, outputs = _ports(machine, "input"), _ports(machine, "output")
    if not group.inputs_per_machine or not group.outputs_per_machine:
        raise SectionError(
            f"{group.recipe_id}: production sections require inputs and outputs",
            cause="unsupported",
        )
    if len(group.inputs_per_machine) > len(inputs) or len(group.outputs_per_machine) > len(outputs):
        raise SectionError(
            f"{group.recipe_id}: {len(group.inputs_per_machine)} ingredients and "
            f"{len(group.outputs_per_machine)} products exceed {machine.class_name}'s "
            f"{len(inputs)} input and {len(outputs)} output belt ports",
            cause="unsupported",
        )
    transform = Pose(0, 0, 0, 0).transform()
    for direction, ports, expected in (("input", inputs, -1.0), ("output", outputs, 1.0)):
        if any(port_forward(transform, port)[1] * expected < 0.999 for port in ports):
            raise SectionError(
                f"{machine.class_name} has no opposing-row {direction} face", cause="unsupported"
            )
    for a, b in zip(inputs, reversed(inputs), strict=True):
        if abs(a.translation[0] + b.translation[0]) > 0.01 or any(
            abs(a.translation[i] - b.translation[i]) > 0.01 for i in (1, 2)
        ):
            raise SectionError(
                f"{machine.class_name}'s input ports cannot share a mirrored spine",
                cause="unsupported",
            )
    return inputs, outputs


def build_section(
    group: SfyMachineGroup,
    designer: Designer,
    registry: Registry,
    lab_map: LabMap,
    *,
    belt_tiers: Sequence[BeltTier],
    ids: Iterator[int],
    deadline: float,
) -> ProductionSection:
    """Construct a complete section in a centred local XY frame, on one floor."""
    build = _Construction(registry, lab_map, belt_tiers, ids, deadline)
    build.check_deadline()
    machine = registry.buildables.get(group.machine_class)
    if machine is None:
        raise SectionError(f"unknown production machine {group.machine_class}", cause="unsupported")
    in_ports, out_ports = _machine_ports(group, machine)
    ingredients, products = sorted(group.inputs_per_machine), sorted(group.outputs_per_machine)
    for item, total_rate in (*group.row_inputs.items(), *group.row_outputs.items()):
        build.tier(item, total_rate)
    limits = registry.limits
    grid, lift_min, lift_width = (
        limits.hologram_grid_cm,
        limits.lift_min_cm,
        limits.lift_clearance_half_extent_cm,
    )
    if grid is None or lift_min is None or lift_width is None:
        raise SectionError(
            "registry lacks section grid or conveyor-lift geometry", cause="unsupported"
        )
    splitter, merger = registry.buildables[SPLITTER_CLASS], registry.buildables[MERGER_CLASS]
    sin = _attachment_port(splitter, "input", (-1, 0, 0))
    sout = _attachment_port(splitter, "output", (1, 0, 0))
    split_sides = tuple(_attachment_port(splitter, "output", (0, side, 0)) for side in (1, -1))
    min_ = _attachment_port(merger, "input", (-1, 0, 0))
    mout = _attachment_port(merger, "output", (1, 0, 0))
    merge_sides = tuple(_attachment_port(merger, "input", (0, -side, 0)) for side in (1, -1))
    foot_x0, foot_y0, foot_x1, foot_y1 = hard_footprint_cm(machine)
    stand = slab_top_cm(registry)
    input_z = max(port.translation[2] for port in in_ports)
    if any(abs(port.translation[2] - input_z) > 0.01 for port in in_ports):
        raise SectionError("opposing input ports must share a conveyor height", cause="unsupported")
    attachment_height = max(
        2 * box.reach[2]
        for attachment in (splitter, merger)
        for box in attachment_boxes(
            AttachmentObj(0, attachment.class_name, Pose(0, 0, 0, 0)), registry
        )
    )
    body_half = attachment_box_cm(registry)
    layer = grid_ceil(max(lift_min, attachment_height + 2 * BELT_CLEARANCE_HALF_HEIGHT_CM), grid)
    snapped = [
        index == 0
        or all(
            _snap_lift_clear(machine, port, input_z + index * layer, lift_width)
            for port in (in_ports[index], in_ports[-1 - index])
        )
        for index in range(len(ingredients))
    ]
    side_reach = max(abs(port.translation[1]) for port in split_sides)
    # Leave one whole centimetre beyond the swept clearance, not an arbitrary
    # foundation cell. Every number otherwise follows actual connector geometry.
    aisle = max(
        -foot_y0 + max(BELT_CLEARANCE_HALF_WIDTH_CM, body_half) + 1,
        max(-port.translation[1] + side_reach + build.lead for port in in_ports),
    )
    if not all(snapped):
        aisle = max(
            aisle,
            -foot_y0 + lift_width + max(side_reach + build.lead, body_half + lift_width) + 1,
        )
    aisle = float(math.ceil(aisle))
    pitch = max(
        machine_pitch_cm(machine, limits),
        abs(sin.translation[0]) + abs(sout.translation[0]) + build.lead,
        2 * body_half,
    )
    raised_outputs = len(products) > 1
    output_reach = max(abs(side.translation[1]) for side in merge_sides)
    out_distances = []
    for port in out_ports:
        distance = max(
            foot_y1 + max(BELT_CLEARANCE_HALF_WIDTH_CM, body_half) + 1,
            port.translation[1] + output_reach + build.lead,
        )
        if raised_outputs:
            distance = max(
                distance,
                foot_y1 + lift_width + 1 + max(output_reach + build.lead, body_half + lift_width),
            )
        # Exposed X-facing interfaces retain the input spine's lattice phase.
        out_distances.append(grid_ceil(aisle + math.ceil(distance), grid) - aisle)
    opposing_depth = 2 * (aisle + max(out_distances) + body_half)
    # A shallow designer can still hold one longer row. Splitting an oversized
    # opposing pair into isolated machines needlessly repeats its interfaces.
    upper_count = group.count if opposing_depth > 2 * designer.half_cm else (group.count + 1) // 2
    row_counts = (upper_count, group.count - upper_count)
    rows: list[list[MachineObj]] = [[], []]
    for row, size in enumerate(row_counts):
        for column in range(size):
            index = column if row == 0 else upper_count + column
            actor = MachineObj(
                next(ids),
                group.machine_class,
                Pose(column * pitch, aisle if row == 0 else -aisle, stand, row * 180),
                group.recipe_class,
                group.clock if index < group.count - 1 else group.last_clock,
                group.somersloops,
            )
            rows[row].append(actor)
            build.machines.append(actor)

    def rate(actor: MachineObj, per_machine: Fraction) -> Fraction:
        return per_machine * actor.clock / group.clock

    inputs: list[SectionPort] = []
    for ingredient, item in enumerate(ingredients):
        spine_z = stand + input_z + ingredient * layer
        previous: AttachmentObj | None = None
        remaining = group.row_inputs[item]
        for column in range(upper_count):
            upper_port = in_ports[ingredient]
            x = column * pitch + upper_port.translation[0]
            node = build.attachment(SPLITTER_CLASS, x, 0, spine_z)
            if previous is None:
                inputs.append(SectionPort(item, remaining, node.id, sin.name, "input"))
            else:
                build.belt((previous, sout.name), (node, sin.name), item, remaining)
            for row, side in enumerate((1, -1)):
                if column >= len(rows[row]):
                    continue
                actor = rows[row][column]
                port = in_ports[ingredient] if row == 0 else in_ports[-1 - ingredient]
                destination = (actor, port.name)
                amount = rate(actor, group.inputs_per_machine[item])
                if ingredient == 0:
                    build.belt((node, split_sides[row].name), destination, item, amount)
                else:
                    position, _ = build.at(destination)
                    lift_y = (
                        position[1]
                        if snapped[ingredient]
                        else side * (aisle + foot_y0 - lift_width - 1)
                    )
                    entry, exit_ = build.lift(
                        position[0], lift_y, spine_z, position[2], -side * 90, item, amount
                    )
                    build.belt((node, split_sides[row].name), entry, item, amount)
                    if snapped[ingredient]:
                        build.links.append(Link((exit_[0].id, exit_[1]), (actor.id, port.name)))
                    else:
                        build.belt(exit_, destination, item, amount)
                remaining -= amount
            previous = node

    outputs: list[SectionPort] = []
    for product, item in enumerate(products):
        port = out_ports[product]
        chain_z = (
            stand + port.translation[2] + (product + 1) * layer
            if raised_outputs
            else stand + port.translation[2]
        )
        last_nodes: list[tuple[AttachmentObj, Fraction, int]] = []
        out_distance = out_distances[product]
        for row, side in enumerate((1, -1)):
            last: AttachmentObj | None = None
            running = Fraction(0)
            for actor in rows[row]:
                source: Connection = (actor, port.name)
                position, _ = build.at(source)
                node = build.attachment(
                    MERGER_CLASS, position[0], side * (aisle + out_distance), chain_z
                )
                amount = rate(actor, group.outputs_per_machine[item])
                if raised_outputs:
                    lift_y = side * (aisle + foot_y1 + lift_width + 1)
                    entry, exit_ = build.lift(
                        position[0], lift_y, position[2], chain_z, -side * 90, item, amount
                    )
                    build.belt(source, entry, item, amount)
                    source = exit_
                build.belt(source, (node, merge_sides[row].name), item, amount)
                if last is not None:
                    build.belt((last, mout.name), (node, min_.name), item, running)
                running += amount
                last = node
            if last is not None:
                last_nodes.append((last, running, side))
        if len(last_nodes) == 1:
            last, amount, _ = last_nodes[0]
            outputs.append(SectionPort(item, amount, last.id, mout.name, "output"))
            continue
        # The single product collector is outside every machine, so its two
        # transverse runs never cross input spines or production footprints.
        collector_x = float(
            math.ceil(
                max(
                    (upper_count - 1) * pitch
                    + max(foot_x1, -foot_x0)
                    + max(BELT_CLEARANCE_HALF_WIDTH_CM, body_half)
                    + 1,
                    max(
                        node.pose.x + mout.translation[0] + build.lead - sin.translation[0]
                        for node, _, _ in last_nodes
                    ),
                    max(node.pose.x + 2 * body_half for node, _, _ in last_nodes),
                )
            )
        )
        collector = build.attachment(MERGER_CLASS, collector_x, 0, chain_z)
        for last, amount, side in last_nodes:
            corner = build.attachment(SPLITTER_CLASS, collector_x, last.pose.y, chain_z)
            side_port = _attachment_port(splitter, "output", (0, -side, 0))
            collector_port = _attachment_port(merger, "input", (0, side, 0))
            build.belt((last, mout.name), (corner, sin.name), item, amount)
            build.belt((corner, side_port.name), (collector, collector_port.name), item, amount)
        outputs.append(
            SectionPort(item, group.row_outputs[item], collector.id, mout.name, "output")
        )

    placement = build.placement(designer)
    low, high = placement_bounds(placement, registry)
    width, depth = high[0] - low[0], high[1] - low[1]
    if width > 2 * designer.half_cm or depth > 2 * designer.half_cm:
        raise SectionError(
            f"{group.recipe_id}: connected {row_counts[0]}+{row_counts[1]} section needs "
            f"{width:.0f} x {depth:.0f} cm; "
            f"{designer.mark} has {2 * designer.half_cm:.0f} cm per side",
            cause="bounds",
        )
    if high[2] > designer.height_cm:
        raise SectionError(
            f"{group.recipe_id}: complete section including ingredient lifts "
            f"needs {high[2]:.0f} cm "
            f"height; {designer.mark} has {designer.height_cm:.0f} cm",
            cause="height",
        )
    offset = (-(low[0] + high[0]) / 2, -(low[1] + high[1]) / 2, 0.0)
    placement = transform_placement(placement, offset)
    bounds = ((-width / 2, -depth / 2, low[2]), (width / 2, depth / 2, high[2]))
    build.check_deadline()
    return ProductionSection(group, placement, tuple(inputs), tuple(outputs), bounds)
