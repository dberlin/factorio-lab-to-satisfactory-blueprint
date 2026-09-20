"""Observable scene geometry and untrusted binary boundaries."""

from __future__ import annotations

import http.client
import math
import struct
import threading
import zlib
from collections.abc import Iterator
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from flab2bp.sfy.archive import ObjectRef, Reader, Writer
from flab2bp.sfy.codec import Blueprint, read_sbp_file, write_sbp
from flab2bp.sfy.geometry import quat_rotate
from flab2bp.sfy.header import BlueprintHeader
from flab2bp.sfy.objects import ACTOR, COMPONENT, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import (
    Array,
    Double,
    Object,
    Opaque,
    Property,
    Struct,
    Tag,
    Vector,
    read_property_list,
    write_property_list,
)
from flab2bp.sfy.registry import ClearanceBox, Port, load_registry
from flab2bp.sfy.templates import set_spline
from flab2bp.sfy.trailers import BuildableTrailer
from flab2bp.web.jobs import Builder, Job, parse_options
from flab2bp.web.server import serve
from flab2bp.web.sfy_scene import (
    MAX_SBP_BYTES,
    SatisfactoryScene,
    SceneError,
    scene_from_blueprint,
    scene_from_sbp,
)

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def actor(class_name: str, transform: Transform = IDENTITY) -> tuple[ObjectHeader, ObjectData]:
    return (
        ObjectHeader(
            ACTOR,
            class_name,
            "Persistent_Level",
            "arbitrary.user.actor",
            None,
            transform,
            1,
            0,
            None,
        ),
        ObjectData(ObjectRef.NULL, (), None, (), BuildableTrailer()),
    )


def blueprint(pair: tuple[ObjectHeader, ObjectData]) -> Blueprint:
    return Blueprint(BlueprintHeader(2, 46, 0, (4, 4, 4), (), (), None), (pair,))


def test_box_corners_preserve_mirrored_scale_and_normalize_rotation() -> None:
    registry = load_registry()
    class_name = "Build_ConstructorMk1_C"
    clearance = ClearanceBox((0, 0, 0), (200, 100, 50), False, (100, 0, 0))
    buildable = replace(registry.buildables[class_name], clearance=(clearance,), ports=())
    registry = replace(registry, buildables={class_name: buildable})
    q = math.sqrt(8)
    transform = Transform((0, 0, q, q), (1000, 2000, 3000), (-2, 3, 4))
    scene = scene_from_blueprint(blueprint(actor(class_name, transform)), registry=registry)
    box = scene["objects"][0]["boxes"][0]
    assert box["center"] == pytest.approx((8.5, 31.0, -16.0))
    corners = []
    for x in (-0.5, 0.5):
        for y in (-0.5, 0.5):
            for z in (-0.5, 0.5):
                offset = quat_rotate(
                    box["quaternion"], (x * box["size"][0], y * box["size"][1], z * box["size"][2])
                )
                corners.append(tuple(box["center"][i] + offset[i] for i in range(3)))
    assert tuple(min(c[i] for c in corners) for i in range(3)) == pytest.approx((7, 30, -18))
    assert tuple(max(c[i] for c in corners) for i in range(3)) == pytest.approx((10, 32, -14))


def test_nonuniform_scale_shear_is_an_explicit_conservative_envelope() -> None:
    registry = load_registry()
    class_name = "Build_ConstructorMk1_C"
    clearance = ClearanceBox(
        (-50, -50, -50),
        (50, 50, 50),
        False,
        (0, 0, 0),
        rotation=(0, 45, 0),
    )
    registry = replace(
        registry,
        buildables={
            class_name: replace(registry.buildables[class_name], clearance=(clearance,), ports=()),
        },
    )
    scene = scene_from_blueprint(
        blueprint(actor(class_name, replace(IDENTITY, scale=(2, 1, 1)))),
        registry=registry,
    )
    assert scene["bounds"]["min"] == pytest.approx((-math.sqrt(2), -0.5, -1 / math.sqrt(2)))
    assert scene["bounds"]["max"] == pytest.approx((math.sqrt(2), 0.5, 1 / math.sqrt(2)))
    assert any("shears" in warning for warning in scene["warnings"])


def test_foundry_uses_physical_mesh_extents_with_saved_rotation_and_scale() -> None:
    registry = load_registry()
    class_name = "Build_FoundryMk1_C"
    registry = replace(
        registry,
        buildables={class_name: replace(registry.buildables[class_name], ports=())},
    )
    q = math.sqrt(0.5)
    transform = Transform((0, 0, q, q), (1000, 2000, 3000), (-2, 3, 4))
    scene = scene_from_blueprint(blueprint(actor(class_name, transform)), registry=registry)
    # Cooked FoundryMk1_static bounds after its native -180-degree component
    # rotation and -40 cm Y offset, then the saved actor transform above.
    # Clearance instead reaches x=-500..500, y=-550..450 and z=0..900 cm.
    assert scene["bounds"]["min"] == pytest.approx(
        (-1.859705505371094, 30.0023779296875, -28.363016357421876)
    )
    assert scene["bounds"]["max"] == pytest.approx(
        (27.20186706542969, 65.15349853515625, -10.921987915039062)
    )
    # The vertex-animated component keeps its own envelope and inherited parent
    # transform; collapsing everything into clearance loses this placement.
    animated = scene["objects"][0]["boxes"][1]
    assert animated["center"] == pytest.approx(
        (9.871882934570312, 48.95770385742188, -19.549193115234374)
    )


def test_unknown_actor_is_retained_without_invented_geometry() -> None:
    scene = scene_from_blueprint(blueprint(actor("Modded_Garden_C")))
    obj = scene["objects"][0]
    assert obj["id"] == "arbitrary.user.actor"
    assert obj["boxes"] == []
    assert obj["recipe"] is None and obj["clockPercent"] is None
    assert any("Modded_Garden_C" in warning for warning in scene["warnings"])


def test_lift_top_translation_is_scaled_rotated_and_keeps_input_at_actor() -> None:
    header, data = actor("Build_ConveyorLiftMk2_C", replace(IDENTITY, scale=(2, 3, -4)))
    top = Struct(
        "Transform",
        (
            Property(
                Tag("Translation", "StructProperty", 0, struct_name="Vector"), Vector(0, 0, 400)
            ),
        ),
    )
    data = replace(
        data,
        properties=(
            Property(Tag("mTopTransform", "StructProperty", 0, struct_name="Transform"), top),
        ),
    )
    obj = scene_from_blueprint(blueprint((header, data)))["objects"][0]
    assert obj["points"] == [(0, 0, 0), (0, -16, 0)]
    assert [(port["direction"], port["position"]) for port in obj["ports"]] == [
        ("input", (0, 0, 0)),
        ("output", (0, -16, 0)),
    ]


def test_curve_samples_use_tangents_instead_of_chords() -> None:
    header, data = actor("Build_ConveyorBeltMk2_C")
    data = replace(
        data,
        properties=(
            Property(Tag("mSplineData", "ArrayProperty", 0), Array("StructProperty", None, ())),
        ),
    )
    points = (
        (Vector(0, 0, 0), Vector(1, 0, 0), Vector(800, 0, 0)),
        (Vector(400, 400, 0), Vector(0, 800, 0), Vector(0, 1, 0)),
    )
    data = set_spline(data, points)
    obj = scene_from_blueprint(blueprint((header, data)))["objects"][0]
    assert obj["points"][0] == (0, 0, 0)
    assert obj["points"][-1] == (4, 0, -4)
    assert any(abs(point[0] + point[2]) > 0.5 for point in obj["points"][1:-1])


def test_bounded_decompression_rejects_false_small_chunk_declaration() -> None:
    original = write_sbp(blueprint(actor("Modded_Garden_C")))
    marker = struct.pack("<I", 0x9E2A83C1)
    start = original.index(marker)
    compressed = zlib.compress(b"x" * (131072 + 1))
    chunk = struct.pack(
        "<IiqBqqqq", 0x9E2A83C1, 0x22222222, 131072, 3, len(compressed), 1, len(compressed), 1
    )
    with pytest.raises(SceneError):
        scene_from_sbp(original[:start] + chunk + compressed)


def test_zero_width_binary_array_remains_opaque_instead_of_allocating_elements() -> None:
    tag = Tag(
        "vectors",
        "ArrayProperty",
        0,
        inner_type="StructProperty",
        struct_name="Vector",
    ).as_modern()
    payload = struct.pack("<i", 1024)
    writer = Writer()
    write_property_list(writer, (Property(tag, Opaque(payload)),))
    decoded = read_property_list(Reader(writer.getvalue()), modern=True)
    assert decoded[0].value == Opaque(payload)


def test_derived_clock_overflow_is_a_meaningful_scene_error() -> None:
    header, data = actor("Build_ConstructorMk1_C")
    data = replace(
        data, properties=(Property(Tag("mCurrentPotential", "DoubleProperty", 0), Double(1e308)),)
    )
    with pytest.raises(SceneError):
        scene_from_blueprint(blueprint((header, data)))


def test_historical_fixture_keeps_every_raw_actor_id() -> None:
    path = Path(__file__).parents[1] / "fixtures" / "sfy" / "production-4.sbp"
    bp = read_sbp_file(path)
    scene = scene_from_sbp(path.read_bytes(), title=path.name)
    assert [obj["id"] for obj in scene["objects"]] == [
        h.path for h, _ in bp.objects if h.kind == ACTOR
    ]
    assert scene["materials"]


def test_belt_flow_follows_saved_endpoint_links_not_component_suffix_order() -> None:
    registry = load_registry()
    machine_class = "Build_ConstructorMk1_C"
    port = Port("Input", "belt", "input", "native", (0, 0, 0), (0, 0, 0), None)
    registry = replace(
        registry,
        buildables={
            **registry.buildables,
            machine_class: replace(registry.buildables[machine_class], ports=(port,)),
        },
    )
    machine, machine_data = actor(machine_class, replace(IDENTITY, translation=(400, 0, 0)))
    machine = replace(machine, path="user.arbitrary-machine")
    belt, belt_data = actor("Build_ConveyorBeltMk2_C")
    belt_data = replace(
        belt_data,
        properties=(
            Property(Tag("mSplineData", "ArrayProperty", 0), Array("StructProperty", None, ())),
        ),
    )
    belt_data = set_spline(
        belt_data,
        (
            (Vector(0, 0, 0), Vector(1, 0, 0), Vector(400, 0, 0)),
            (Vector(400, 0, 0), Vector(400, 0, 0), Vector(1, 0, 0)),
        ),
    )
    flow = registry.buildables[belt.class_name].flow
    assert flow is not None
    target = replace(
        machine,
        kind=COMPONENT,
        class_path="FGFactoryConnectionComponent",
        path=f"{machine.path}.Input",
        parent=machine.path,
        transform=None,
    )
    entry = replace(target, path=f"{belt.path}.{flow.entry}", parent=belt.path)
    entry_data = replace(
        machine_data,
        properties=(
            Property(
                Tag("mConnectedComponent", "ObjectProperty", 0), Object(ObjectRef("", target.path))
            ),
        ),
    )
    bp = replace(
        blueprint((belt, belt_data)),
        objects=(
            (belt, belt_data),
            (machine, machine_data),
            (target, machine_data),
            (entry, entry_data),
        ),
    )
    obj = scene_from_blueprint(bp, registry=registry)["objects"][0]
    assert obj["flowDirection"] == "reverse"
    assert obj["ports"][0]["position"] == (4, 0, 0)


@pytest.fixture
def http_scene(tmp_path: Path) -> Iterator[tuple[ThreadingHTTPServer, Builder]]:
    httpd, builder = serve(port=0, dist=tmp_path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, builder
    finally:
        httpd.shutdown()
        httpd.server_close()
        builder.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("length", "status"),
    [
        ("-1", 400),
        ("not-a-length", 400),
        (str(MAX_SBP_BYTES + 1), 413),
    ],
)
def test_upload_rejects_unsafe_length_before_reading_body(
    http_scene: tuple[ThreadingHTTPServer, Builder],
    length: str,
    status: int,
) -> None:
    httpd, _ = http_scene
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
    try:
        connection.putrequest("POST", "/api/sfy/scene")
        connection.putheader("Content-Length", length)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == status
        response.read()
    finally:
        connection.close()


def test_scene_route_uses_retained_artifact_and_preserves_binary_download(
    http_scene: tuple[ThreadingHTTPServer, Builder],
) -> None:
    httpd, builder = http_scene
    raw = write_sbp(blueprint(actor("Modded_Garden_C")))
    job = Job(
        "retained",
        parse_options(
            {
                "url": "https://factoriolab.github.io/sfy/flow?o=iron-plate*60&v=11",
            }
        ),
        0,
        state="done",
        artifacts={"factory.sbp": raw},
    )
    with builder._lock:
        builder._jobs[job.id] = job
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
    try:
        connection.request("GET", "/api/build/retained/scene")
        response = connection.getresponse()
        assert response.status == 200
        scene = TypeAdapter(SatisfactoryScene).validate_json(response.read())
        assert scene["objects"][0]["id"] == "arbitrary.user.actor"
        assert scene["title"] == "factory.sbp"
        connection.request("GET", "/api/build/retained/artifacts/factory.sbp")
        response = connection.getresponse()
        assert response.read() == raw
        with job._lock:
            job.artifacts["factory.sbp"] = b"invalid artifact"
        connection.request("GET", "/api/build/retained/scene")
        response = connection.getresponse()
        assert response.status == 422
        response.read()
        with job._lock:
            job.artifacts.clear()
        connection.request("GET", "/api/build/retained/scene")
        response = connection.getresponse()
        assert response.status == 409
        response.read()
        connection.request("GET", "/api/build/missing/scene")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
    finally:
        connection.close()
