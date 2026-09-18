"""Stack seams and material trunks are geometry/connectivity contracts."""

import itertools
import time
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction

import pytest

from flab2bp.sfy.geometry import box_bounds, quat_rotate
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    PipeAttachmentObj,
    Pose,
    SfyPlacement,
    belt_ends,
)
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.sections.compose import _arrange, _floors, _merge, _sections_for
from flab2bp.sfy.sections.model import ProductionSection, SectionPort, endpoint
from flab2bp.sfy.sections.pipe_routes import _Routes
from flab2bp.sfy.sections.stacking import build_stack_boundaries
from flab2bp.sfy.spec import PipeTier, SfyBuildSpec, SfyMachineGroup, designer
from tests.sfy.conftest import flow_spec, sfy_registry


def _stack():
    registry = sfy_registry()
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
        inputs_per_machine={"iron-ingot": Fraction(1, 2)},
        outputs_per_machine={"iron-plate": Fraction(1, 3)},
        power_mw_per_machine=4,
        last_power_mw=4,
    )
    box = designer("mk3", registry)
    source = AttachmentObj(1, "Build_ConveyorAttachmentSplitter_C", Pose(0, 0, 600, 0))
    placement = SfyPlacement(designer=box, attachments=(source,))
    section = ProductionSection(
        group,
        placement,
        (SectionPort("iron-ingot", Fraction(1, 2), source.id, "Input1", "input"),),
        (SectionPort("iron-plate", Fraction(1, 3), source.id, "Output1", "output"),),
        ((-250, -250, 100), (250, 250, 900)),
    )
    spec = SfyBuildSpec(
        groups=(group,),
        external_inputs={"iron-ingot": Fraction(1, 2)},
        outputs={"iron-plate": Fraction(1, 3)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
    )
    result, branches = build_stack_boundaries(
        spec,
        placement,
        (section,),
        registry,
        load_lab_map(),
        ids=itertools.count(2),
        deadline=time.monotonic() + 30,
    )
    return registry, result, branches


def test_repeated_lanes_have_matching_xy_and_one_manual_seam_gap():
    registry, placement, _ = _stack()
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


def test_ingredient_feed_reaches_local_branch_and_continuing_trunk():
    registry, placement, branches = _stack()
    graph = defaultdict(set)
    for link in placement.links:
        graph[link.a].add(link.b)
    for actor in placement.objects:
        if isinstance(actor, (BeltRun, LiftObj)):
            entry, exit_ = belt_ends(registry, actor.class_name)
            graph[(actor.id, entry)].add((actor.id, exit_))
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


def test_perimeter_rings_cover_all_four_slab_edges_with_legal_beams():
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


def test_dense_refinery_stack_branch_clears_the_collector_belts():
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


def test_long_residue_connection_keeps_pipe_actor_seams_clear_of_bends():
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
