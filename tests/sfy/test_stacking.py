"""Stack seams and material trunks are geometry/connectivity contracts."""

import itertools
import time
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from typing import Literal

import pytest

from flab2bp.sfy.geometry import box_bounds, quat_rotate
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    Link,
    PipeAttachmentObj,
    PipeRun,
    Pose,
    SfyPlacement,
    belt_ends,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import straight
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.sections.compose import _arrange, _floors, _merge, _sections_for
from flab2bp.sfy.sections.model import (
    ProductionSection,
    SectionPort,
    endpoint,
    transform_placement,
)
from flab2bp.sfy.sections.pipe_routes import _Routes
from flab2bp.sfy.sections.stacking import build_stack_boundaries
from flab2bp.sfy.spec import PipeTier, SfyBuildSpec, SfyMachineGroup, designer
from tests.sfy.conftest import flow_spec, sfy_registry


def _stack(
    *, kind: Literal["belt", "pipe"] = "belt", same_item: bool = False, start_id: int = 1
) -> tuple[Registry, SfyPlacement, tuple[SectionPort, ...]]:
    registry = sfy_registry()
    ids = itertools.count(start_id)
    ingredient = "water" if kind == "pipe" else "iron-ingot"
    product = ingredient if same_item else ("heavy-oil-residue" if kind == "pipe" else "iron-plate")
    group = SfyMachineGroup(
        recipe_id="iron-plate",
        recipe_class="Recipe_IronPlate_C",
        machine_item_id="constructor",
        machine_class="Build_ConstructorMk1_C",
        count=1,
        clock=Fraction(1),
        last_clock=Fraction(1),
        max_clock=Fraction(5, 2),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine={ingredient: Fraction(1, 2)},
        outputs_per_machine={product: Fraction(1, 3)},
        power_mw_per_machine=4,
        last_power_mw=4,
    )
    box = designer("mk3", registry)
    source: PipeAttachmentObj | AttachmentObj
    if kind == "pipe":
        source = PipeAttachmentObj(next(ids), "Build_PipelineJunction_Cross_C", Pose(0, 0, 600, 0))
        placement = SfyPlacement(designer=box, pipe_attachments=(source,))
        input_port, output_port = "Connection0", "Connection1"
    else:
        source = AttachmentObj(next(ids), "Build_ConveyorAttachmentSplitter_C", Pose(0, 0, 600, 0))
        placement = SfyPlacement(designer=box, attachments=(source,))
        input_port, output_port = "Input1", "Output1"
    section = ProductionSection(
        group,
        placement,
        (SectionPort(ingredient, Fraction(1, 2), source.id, input_port, "input", kind),),
        (SectionPort(product, Fraction(1, 3), source.id, output_port, "output", kind),),
        ((-250, -250, 100), (250, 250, 900)),
    )
    spec = SfyBuildSpec(
        groups=(group,),
        external_inputs={ingredient: Fraction(1, 2)},
        outputs={product: Fraction(1, 3)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        fluid_items=frozenset({ingredient, product}) if kind == "pipe" else frozenset(),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),)
        if kind == "pipe"
        else (),
    )
    result, branches = build_stack_boundaries(
        spec,
        placement,
        (section,),
        registry,
        load_lab_map(),
        ids=ids,
        deadline=time.monotonic() + 30,
    )
    return registry, result, branches


def test_repeated_lanes_have_matching_xy_and_one_manual_seam_gap() -> None:
    registry, placement, _ = _stack()
    assert registry.limits.lift_min_cm is not None
    assert registry.limits.lift_max_cm is not None
    gaps = []
    linked = {end for link in placement.links for end in (link.a, link.b)}
    for lane in placement.stack_lanes:
        bottom, bottom_normal = endpoint(placement, lane.bottom[0], lane.bottom[1], registry)
        top, top_normal = endpoint(placement, lane.top[0], lane.top[1], registry)
        assert bottom[:2] == pytest.approx(top[:2], abs=0.001)
        assert bottom_normal == pytest.approx((0, 0, -1), abs=0.001)
        assert top_normal == pytest.approx((0, 0, 1), abs=0.001)
        gaps.append(bottom[2] + placement.stack_height_cm - top[2])
        assert lane.bottom not in linked
        assert lane.top not in linked
    assert all(registry.limits.lift_min_cm <= gap <= registry.limits.lift_max_cm for gap in gaps)
    slabs = [
        box_bounds(registry.buildables[obj.class_name].clearance[0], obj.pose.transform())
        for obj in placement.foundations
    ]
    for _low, high in slabs:
        for other_low, _other_high in slabs:
            assert high[2] <= other_low[2] + placement.stack_height_cm


def test_ingredient_feed_reaches_local_branch_and_continuing_trunk() -> None:
    registry, placement, branches = _stack()
    graph = defaultdict(set)
    for link in placement.links:
        graph[link.a].add(link.b)
    for actor in placement.objects:
        if isinstance(actor, (BeltRun, LiftObj)):
            entry_name, exit_name = belt_ends(registry, actor.class_name)
            graph[(actor.id, entry_name)].add((actor.id, exit_name))
        elif isinstance(actor, AttachmentObj):
            ports = registry.buildables[actor.class_name].ports
            for entry in (port for port in ports if port.direction == "input"):
                graph[(actor.id, entry.name)].update(
                    (actor.id, port.name) for port in ports if port.direction == "output"
                )
    lane = next(lane for lane in placement.stack_lanes if lane.item_id == "iron-ingot")
    pending, visited = [lane.bottom], set()
    while pending:
        node = pending.pop()
        if node not in visited:
            visited.add(node)
            pending.extend(graph[node] - visited)
    branch = next(port for port in branches if port.item_id == lane.item_id)
    assert (branch.object_id, branch.port) in visited
    assert lane.top in visited
    assert branch.items_per_second == lane.input_per_second == Fraction(1, 2)


def _reachable(
    placement: SfyPlacement, registry: Registry, start: tuple[int, str]
) -> set[tuple[int, str]]:
    graph: defaultdict[tuple[int, str], set[tuple[int, str]]] = defaultdict(set)
    for link in placement.links:
        graph[link.a].add(link.b)
    for actor in placement.objects:
        if isinstance(actor, (BeltRun, LiftObj, PipeRun)):
            entry_name, exit_name = (pipe_ends if isinstance(actor, PipeRun) else belt_ends)(
                registry, actor.class_name
            )
            graph[(actor.id, entry_name)].add((actor.id, exit_name))
        elif isinstance(actor, (AttachmentObj, PipeAttachmentObj)):
            ports = registry.buildables[actor.class_name].ports
            for entry in ports:
                if entry.kind in ("belt", "pipe") and entry.direction != "output":
                    graph[(actor.id, entry.name)].update(
                        (actor.id, port.name)
                        for port in ports
                        if port.kind == entry.kind and port.direction != "input"
                    )
    pending, visited = [start], set()
    while pending:
        node = pending.pop()
        if node not in visited:
            visited.add(node)
            pending.extend(graph[node] - visited)
    return visited


def _join_stack_pair(lower: SfyPlacement, upper: SfyPlacement, registry: Registry) -> SfyPlacement:
    """Place the second module and author the user's actual, reciprocal seams."""
    upper = transform_placement(upper, (0, 0, lower.stack_height_cm))
    placement = replace(
        lower,
        attachments=(*lower.attachments, *upper.attachments),
        belts=(*lower.belts, *upper.belts),
        lifts=(*lower.lifts, *upper.lifts),
        pipes=(*lower.pipes, *upper.pipes),
        pipe_attachments=(*lower.pipe_attachments, *upper.pipe_attachments),
        foundations=(*lower.foundations, *upper.foundations),
        beams=(*lower.beams, *upper.beams),
        passthroughs=(*lower.passthroughs, *upper.passthroughs),
        links=(*lower.links, *upper.links),
        stack_lanes=(),
        stack_height_cm=0,
        stack_connection_gap_cm=0,
    )
    ids = itertools.count(max(actor.id for actor in placement.objects) + 1)
    holes = {hole.id: hole for hole in placement.passthroughs}
    lifts, pipes, links = list(placement.lifts), list(placement.pipes), list(placement.links)
    connected: set[tuple[int, str]] = set()
    for low, high in zip(lower.stack_lanes, upper.stack_lanes, strict=True):
        source, target = (low.top, high.bottom) if low.upward else (high.bottom, low.top)
        start, _ = endpoint(placement, *source, registry)
        finish, _ = endpoint(placement, *target, registry)
        run = placement.by_id(source[0])
        assert isinstance(run, LiftObj | PipeRun)
        ends = (pipe_ends if low.kind == "pipe" else belt_ends)(registry, run.class_name)
        source_hole = run.snapped_passthroughs[ends.index(source[1])]
        target_run = placement.by_id(target[0])
        assert isinstance(target_run, LiftObj | PipeRun)
        target_hole = target_run.snapped_passthroughs[ends.index(target[1])]
        assert source_hole is not None and target_hole is not None
        height = finish[2] - start[2]
        seam: LiftObj | PipeRun
        if low.kind == "belt":
            seam = LiftObj(
                next(ids),
                run.class_name,
                Pose(*start, 0),
                height,
                snapped_passthroughs=(source_hole, target_hole),
            )
            lifts.append(seam)
        else:
            seam = PipeRun(
                next(ids),
                run.class_name,
                straight(start, (0, 0, 1 if height > 0 else -1), abs(height)),
                low.item_id,
                low.capacity_per_second,
                snapped_passthroughs=(source_hole, target_hole),
            )
            pipes.append(seam)
        entry, exit_ = (seam.id, ends[0]), (seam.id, ends[1])
        if low.upward:
            holes[source_hole] = replace(holes[source_hole], top_connection=entry)
            holes[target_hole] = replace(holes[target_hole], bottom_connection=exit_)
        else:
            holes[source_hole] = replace(holes[source_hole], bottom_connection=entry)
            holes[target_hole] = replace(holes[target_hole], top_connection=exit_)
        links.extend((Link(source, entry), Link(exit_, target)))
        connected.update((source, target))

    def close_boundaries[T: (LiftObj, PipeRun)](run: T) -> T:
        entry, exit_ = (pipe_ends if isinstance(run, PipeRun) else belt_ends)(
            registry, run.class_name
        )
        return replace(
            run,
            boundary_start=run.boundary_start and (run.id, entry) not in connected,
            boundary_end=run.boundary_end and (run.id, exit_) not in connected,
        )

    return replace(
        placement,
        lifts=tuple(close_boundaries(run) for run in lifts),
        pipes=tuple(close_boundaries(run) for run in pipes),
        passthroughs=tuple(holes.values()),
        links=tuple(links),
    )


@pytest.mark.parametrize("kind", ("belt", "pipe"))
@pytest.mark.parametrize("same_item", (False, True))
def test_manual_stack_seams_feed_both_modules_and_drain_both_to_ground(
    kind: Literal["belt", "pipe"], same_item: bool
) -> None:
    registry, lower, lower_branches = _stack(kind=kind, same_item=same_item)
    _, upper, upper_branches = _stack(kind=kind, same_item=same_item, start_id=10000)
    assert len(lower.stack_lanes) == 2
    incoming = next(lane for lane in lower.stack_lanes if lane.input_per_second)
    outgoing = next(lane for lane in lower.stack_lanes if lane.output_per_second)
    assert incoming.output_per_second == outgoing.input_per_second == 0
    assert incoming.capacity_per_second >= 2 * incoming.input_per_second
    assert outgoing.capacity_per_second >= 2 * outgoing.output_per_second
    in_xy = endpoint(lower, *incoming.bottom, registry)[0][:2]
    out_xy = endpoint(lower, *outgoing.bottom, registry)[0][:2]
    assert in_xy != out_xy
    joined = _join_stack_pair(lower, upper, registry)
    report = validate(
        joined,
        None,
        registry,
        only=(
            "ports.position",
            "ports.direction",
            "ports.connected_once",
            "ports.passthrough",
            "lift.height",
            "lift.step",
            "pipe.min_length",
            "pipe.max_length",
        ),
    )
    assert not report.errors, [finding.message for finding in report.errors]
    fed = _reachable(joined, registry, incoming.bottom)
    for branch in (*lower_branches, *upper_branches):
        local = branch.object_id, branch.port
        if branch.direction == "output":
            assert branch.items_per_second == incoming.input_per_second == Fraction(1, 2)
            assert local in fed
            assert outgoing.bottom not in _reachable(joined, registry, local)
        else:
            assert branch.items_per_second == outgoing.output_per_second == Fraction(1, 3)
            assert outgoing.bottom in _reachable(joined, registry, local)
            assert incoming.bottom not in _reachable(joined, registry, local)
    upper_output = next(lane for lane in upper.stack_lanes if lane.output_per_second)
    assert outgoing.bottom in _reachable(joined, registry, upper_output.top)
    assert outgoing.top not in _reachable(joined, registry, outgoing.bottom)
    if kind == "pipe":
        output_path = _reachable(joined, registry, upper_output.top)
        assert not any(
            registry.buildables[actor.class_name].pump_design_head_m is not None
            and any(node[0] == actor.id for node in output_path)
            for actor in joined.pipe_attachments
        )
        assert incoming.required_input_head_m > 0
        assert outgoing.required_input_head_m == 0


def test_perimeter_rings_cover_all_four_slab_edges_with_legal_beams() -> None:
    registry, placement, _ = _stack()
    slab_boxes = [
        box_bounds(registry.buildables[obj.class_name].clearance[0], obj.pose.transform())
        for obj in placement.foundations
    ]
    x0 = min(low[0] for low, _ in slab_boxes)
    y0 = min(low[1] for low, _ in slab_boxes)
    x1 = max(high[0] for _, high in slab_boxes)
    y1 = max(high[1] for _, high in slab_boxes)
    rings = defaultdict(list)
    for beam in placement.beams:
        assert beam.class_name == "Build_Beam_Painted_C"
        assert 0 < beam.length_cm <= 4000
        delta = quat_rotate(beam.pose.transform().rotation, (beam.length_cm, 0, 0))
        end = tuple(beam.pose.location[i] + delta[i] for i in range(3))
        if abs(delta[2]) < 0.001:
            rings[beam.pose.z].append((beam.pose.location, end))
    assert len(rings) == 2
    footprints = []
    for segments in rings.values():
        points = [point for segment in segments for point in segment]
        left, right = min(p[0] for p in points), max(p[0] for p in points)
        back, front = min(p[1] for p in points), max(p[1] for p in points)
        assert x0 - 0.001 <= left - 50 < right + 50 <= x1 + 0.001
        assert y0 - 0.001 <= back - 50 < front + 50 <= y1 + 0.001
        footprints.append((left, right, back, front))
        for axis, side in ((0, left), (0, right), (1, back), (1, front)):
            intervals = sorted(
                sorted((start[1 - axis], end[1 - axis]))
                for start, end in segments
                if abs(start[axis] - side) < 0.001 and abs(end[axis] - side) < 0.001
            )
            low, high = (back, front) if axis == 0 else (left, right)
            assert intervals[0][0] == pytest.approx(low)
            assert intervals[-1][1] == pytest.approx(high)
            assert all(a[1] == pytest.approx(b[0]) for a, b in itertools.pairwise(intervals))
    assert footprints[0] == pytest.approx(footprints[1])


def test_dense_refinery_stack_branch_clears_the_collector_belts() -> None:
    registry = sfy_registry()
    base = flow_spec("plastic-20")
    spec = base.model_copy(
        update={
            "groups": tuple(group.model_copy(update={"count": 4}) for group in base.groups),
            "external_inputs": {item: rate * 4 for item, rate in base.external_inputs.items()},
            "outputs": {item: rate * 4 for item, rate in base.outputs.items()},
            "surplus_outputs": {item: rate * 4 for item, rate in base.surplus_outputs.items()},
        }
    )
    box = designer("mk2", registry)
    mapping = load_lab_map()
    ids = itertools.count(1)
    deadline = time.monotonic() + 30
    built = tuple(
        section
        for group in spec.groups
        for section in _sections_for(group, spec, box, registry, mapping, ids, deadline)
    )
    sections = _arrange((built,), box, registry, deadline)
    placement = _merge(box, sections)
    placement = replace(placement, foundations=_floors(placement, registry, ids))
    stacked, branches = build_stack_boundaries(
        spec, placement, sections, registry, mapping, ids=ids, deadline=deadline
    )
    assert sum(machine.class_name == "Build_OilRefinery_C" for machine in stacked.machines) == 4
    assert {lane.item_id for lane in stacked.stack_lanes} == {
        "crude-oil",
        "plastic",
        "heavy-oil-residue",
    }
    report = validate(
        stacked,
        spec,
        registry,
        only=("geom.bounds", "geom.attachment_body", "belt.capsule"),
    )
    assert not report.errors, [finding.message for finding in report.errors]
    product = next(port for port in branches if port.item_id == "plastic")
    assert product.items_per_second == Fraction(4, 3)


def test_long_residue_connection_keeps_pipe_actor_seams_clear_of_bends() -> None:
    registry = sfy_registry()
    source = PipeAttachmentObj(1, "Build_PipelineJunction_Cross_C", Pose(-1501, -1800, 475, 0))
    collector = PipeAttachmentObj(2, "Build_PipelineJunction_Cross_C", Pose(1700, -1000, 300, 0))
    placement = SfyPlacement(
        designer=designer("mk2", registry), pipe_attachments=(source, collector)
    )
    spec = SfyBuildSpec(
        groups=(),
        external_inputs={},
        outputs={},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),),
    )
    routes = _Routes(
        spec, placement, registry, load_lab_map(), itertools.count(3), time.monotonic() + 30
    )
    routes.connect(
        (source.id, "Connection0"),
        (collector.id, "Connection3"),
        "heavy-oil-residue",
        Fraction(1, 2),
        (
            (-1601, -1800, 475),
            (-1766, -1800, 475),
            (-1766, -1800, 300),
            (1700, -1800, 300),
            (1700, -1100, 300),
        ),
    )
    connected = replace(placement, pipes=tuple(routes.pipes), links=tuple(routes.links))
    report = validate(
        connected,
        None,
        registry,
        only={
            "geom.bounds",
            "geom.hard_clearance",
            "pipe.capsule",
            "pipe.min_length",
            "pipe.max_length",
            "pipe.curvature",
            "ports.position",
            "ports.direction",
            "ports.connected_once",
        },
    )
    assert not report.errors, [finding.message for finding in report.errors]
    neighbours = defaultdict(set)
    for link in connected.links:
        neighbours[link.a[0]].add(link.b[0])
        neighbours[link.b[0]].add(link.a[0])
    pending, visited = [source.id], set()
    while pending:
        actor = pending.pop()
        if actor not in visited:
            visited.add(actor)
            pending.extend(neighbours[actor] - visited)
    assert collector.id in visited
