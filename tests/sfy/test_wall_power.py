"""Native frame-mounted power stays connected without filling floor space."""

import itertools
import math
import time
from collections import Counter, defaultdict
from dataclasses import replace

import pytest

from flab2bp.sfy.codec import read_sbp, read_sbp_file, write_sbp
from flab2bp.sfy.geometry import box_bounds, world_port
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.model import BeamObj, MachineObj, PipeAttachmentObj, Pose, SfyPlacement
from flab2bp.sfy.properties import Array, Object
from flab2bp.sfy.query import find
from flab2bp.sfy.sections.model import SectionError
from flab2bp.sfy.sections.power import add_wall_power
from flab2bp.sfy.spec import designer
from flab2bp.sfy.templates import TemplateLibrary
from tests.sfy.conftest import fixture_paths, sfy_registry


def _frame(*, loads=1):
    registry = sfy_registry()
    ids = itertools.count(1)
    beams = []
    corners = ((-1300, -1300), (1300, -1300), (1300, 1300), (-1300, 1300))
    for z in (50, 1600):
        for index, (x, y) in enumerate(corners):
            nx, ny = corners[(index + 1) % 4]
            yaw = math.degrees(math.atan2(ny - y, nx - x))
            beams.append(BeamObj(next(ids), "Build_Beam_Painted_C", Pose(x, y, z, yaw), 2600))
    for x, y in corners:
        beams.append(BeamObj(next(ids), "Build_Beam_Painted_C", Pose(x, y, 100, 0, 90), 1500))
    # A stack of consumers exercises electrical fanout independently of factory packing.
    machines = tuple(
        MachineObj(next(ids), "Build_ConstructorMk1_C", Pose(0, 0, 100, 0), "Recipe_IronPlate_C")
        for _ in range(loads)
    )
    pump = PipeAttachmentObj(next(ids), "Build_PipelinePumpMk2_C", Pose(0, 800, 500, 0))
    return (
        registry,
        SfyPlacement(
            designer=designer("mk1", registry),
            beams=tuple(beams),
            machines=machines,
            pipe_attachments=(pump,),
            stack_height_cm=1700,
        ),
        ids,
    )


def _powered(loads=1):
    registry, placement, ids = _frame(loads=loads)
    return registry, add_wall_power(placement, registry, ids=ids, deadline=time.monotonic() + 30)


def test_each_side_has_spare_external_socket_and_all_consumers_share_one_circuit():
    registry, placement = _powered(loads=25)
    assert all(p.class_name == "Build_PowerPoleWallDouble_Mk2_C" for p in placement.poles)
    assert {round(p.pose.yaw_deg) for p in placement.poles} == {-90, 0, 90, 180}
    counts = Counter(side for wire in placement.wires for side in (wire.link.a, wire.link.b))
    graph = defaultdict(set)
    for wire in placement.wires:
        a, b = wire.link.a, wire.link.b
        graph[a[0]].add(b[0])
        graph[b[0]].add(a[0])
        points = []
        for object_id, name in (a, b):
            obj = placement.by_id(object_id)
            port = next(p for p in registry.buildables[obj.class_name].ports if p.name == name)
            assert port.max_connections is not None
            assert counts[(object_id, name)] <= port.max_connections
            points.append(world_port(obj.pose.transform(), port))
        assert math.dist(*points) <= registry.limits.wire_max_cm[wire.class_name]
    visited = set()
    pending = [placement.poles[0].id]
    while pending:
        here = pending.pop()
        if here not in visited:
            visited.add(here)
            pending.extend(graph[here] - visited)
    assert visited == {
        obj.id for obj in (*placement.poles, *placement.machines, *placement.pipe_attachments)
    }
    for socket in placement.poles:
        assert counts[(socket.id, "PowerConnection1")] == 0
        if socket.pose.yaw_deg == 0:
            assert abs(socket.pose.y) == 1300  # no outlet in the center-front sign band
        for box in registry.buildables[socket.class_name].clearance:
            low, high = box_bounds(box, socket.pose.transform())
            assert all(
                -placement.designer.half_cm <= low[i] <= high[i] <= placement.designer.half_cm
                for i in (0, 1)
            )
            assert 0 <= low[2] <= high[2] <= placement.designer.height_cm


def test_painted_frame_and_double_socket_circuit_survive_native_binary_roundtrip():
    registry, placement = _powered()
    paths = fixture_paths()
    library = TemplateLibrary.from_fixtures(paths)
    newest = max(
        (read_sbp_file(p).header for p in paths), key=lambda h: (h.save_version, h.build_version)
    )
    blueprint = emit(
        placement,
        registry,
        library,
        load_lab_map(),
        build_version=newest.build_version,
        version_data=newest.version_data,
    )
    restored = read_sbp(write_sbp(blueprint))
    assert decode(restored, registry) == placement
    index = {h.path: (h, d) for h, d in restored.objects}
    for header, data in restored.objects:
        if header.class_name != "Build_PowerPoleWallDouble_Mk2_C":
            continue
        assert data.components is not None
        for ref in data.components:
            ch, cd = index[ref.path]
            hidden = find(cd.properties, "mHiddenConnections")
            assert isinstance(hidden, Array)
            peer = "PowerConnection2" if ch.name == "PowerConnection1" else "PowerConnection1"
            assert tuple(v.ref.path for v in hidden.items if isinstance(v, Object)) == (
                f"{header.path}.{peer}",
            )
    cost = {entry.item.name: entry.amount for entry in restored.header.cost}
    assert cost["Desc_SteelPlate_C"] == 72  # 8x2600cm rails + 4x1500cm native painted beams
    assert cost["Desc_HighSpeedWire_C"] == 16 * len(placement.poles)
    assert cost["Desc_IronRod_C"] == 4 * len(placement.poles)
    assert "Recipe_Beam_Painted_C" in {recipe.name for recipe in restored.header.recipes}
    assert "Recipe_PowerPoleWallDoubleMk2_C" in {recipe.name for recipe in restored.header.recipes}


def test_double_wall_socket_wire_endpoints_keep_inherited_face_offsets() -> None:
    registry, placement = _powered()
    socket = placement.poles[0]
    ports = {port.name: port for port in registry.buildables[socket.class_name].ports}
    outside = world_port(socket.pose.transform(), ports["PowerConnection1"])
    inside = world_port(socket.pose.transform(), ports["PowerConnection2"])
    assert math.dist(outside, inside) == pytest.approx(140)
    assert outside[0] > socket.pose.x > inside[0]


def test_wall_power_refuses_unreachable_load_instead_of_adding_floor_poles():
    registry, placement, ids = _frame()
    registry = replace(
        registry, limits=replace(registry.limits, wire_max_cm={"Build_PowerLine_C": 10.0})
    )
    with pytest.raises(SectionError, match="wire"):
        add_wall_power(placement, registry, ids=ids, deadline=time.monotonic() + 30)
