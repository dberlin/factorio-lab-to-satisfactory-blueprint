"""Native fittings carry tight turns without curved-pipe shortcuts."""

import itertools
import time
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction

import pytest

from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.model import PipeAttachmentObj, Pose, SfyPlacement
from flab2bp.sfy.layout.splines import hermite_tangent, spline_length
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.sections.model import SectionError
from flab2bp.sfy.sections.pipe_routes import _Routes
from flab2bp.sfy.spec import PipeTier, SfyBuildSpec, designer
from tests.sfy.conftest import sfy_registry


def _routes(source: Pose, target: Pose) -> _Routes:
    registry = sfy_registry()
    placement = SfyPlacement(
        designer=designer("mk2", registry),
        pipe_attachments=(
            PipeAttachmentObj(1, "Build_PipelineJunction_Cross_C", source),
            PipeAttachmentObj(2, "Build_PipelineJunction_Cross_C", target),
        ),
    )
    spec = SfyBuildSpec(
        groups=(),
        external_inputs={},
        outputs={},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),),
    )
    return _Routes(
        spec, placement, registry, load_lab_map(), itertools.count(3), time.monotonic() + 30
    )


def _assert_connected_geometry(routes: _Routes) -> None:
    placement = replace(
        routes.placement,
        pipe_attachments=(*routes.placement.pipe_attachments, *routes.nodes),
        pipes=tuple(routes.pipes),
        links=tuple(routes.links),
    )
    report = validate(
        placement,
        None,
        routes.registry,
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
    for link in placement.links:
        neighbours[link.a[0]].add(link.b[0])
        neighbours[link.b[0]].add(link.a[0])
    pending, reached = [1], set()
    while pending:
        actor = pending.pop()
        if actor not in reached:
            reached.add(actor)
            pending.extend(neighbours[actor] - reached)
    assert 2 in reached
    assert reached == {actor.id for actor in placement.objects}


@pytest.mark.parametrize(
    "target,source_port,target_port",
    [
        (Pose(0, 600, 500, 0), "Connection1", "Connection1"),
        (Pose(800, 0, 1100, 0), "Connection1", "Connection0"),
        (Pose(0, 800, 1100, 0), "Connection2", "Connection3"),
    ],
    ids=("horizontal-uturn", "vertical-xz", "vertical-yz"),
)
def test_turns_use_connected_native_junctions_with_unused_ports(
    target: Pose, source_port: str, target_port: str
) -> None:
    routes = _routes(Pose(0, 0, 500, 0), target)
    routes.connect((1, source_port), (2, target_port), "water", Fraction(1))
    _assert_connected_geometry(routes)
    assert len(routes.nodes) == 2
    linked = {end for link in routes.links for end in (link.a, link.b)}
    for node in routes.nodes:
        ports = routes.registry.buildables[node.class_name].ports
        assert sum((node.id, port.name) in linked for port in ports) == 2
    for run in routes.pipes:
        start, finish = run.points[0][0], run.points[-1][0]
        distance = sum((finish[i] - start[i]) ** 2 for i in range(3)) ** 0.5
        assert spline_length(run.points) == pytest.approx(distance)
        assert run.cubic_metres_per_second == Fraction(1)


def test_short_gravity_descent_keeps_valid_curves_without_orphan_fittings() -> None:
    routes = _routes(Pose(0, 0, 475, 0), Pose(600, 0, 300, 0))
    routes.max_unpumped_z = 475
    routes.connect((1, "Connection1"), (2, "Connection0"), "water", Fraction(1))
    _assert_connected_geometry(routes)
    assert routes.nodes == []
    assert max(run.points[index][0][2] for run in routes.pipes for index in (0, -1)) <= 475
    for run in routes.pipes:
        for start, finish in zip(run.points, run.points[1:], strict=False):
            for sample in range(21):
                tangent = hermite_tangent(start[0], start[2], finish[0], finish[1], sample / 20)
                assert tangent[0] >= -0.001
                assert tangent[2] <= 0.001


def test_expired_junction_route_leaves_no_partial_network(monkeypatch) -> None:
    routes = _routes(Pose(0, 0, 500, 0), Pose(0, 600, 500, 0))
    obstacles = tuple(routes.obstacles)
    routes.deadline = 1
    monkeypatch.setattr(
        "flab2bp.sfy.sections.pipe_routes.time.monotonic",
        lambda: 2 if routes.nodes else 0,
    )
    with pytest.raises(SectionError) as caught:
        routes.connect((1, "Connection1"), (2, "Connection1"), "water", Fraction(1))
    assert caught.value.cause == "deadline"
    assert routes.nodes == []
    assert routes.pipes == []
    assert routes.links == []
    assert tuple(routes.obstacles) == obstacles
