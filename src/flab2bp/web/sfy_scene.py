"""Schematic scenes from raw Satisfactory actors, never generated placement IDs.

Coordinates are Y-up metres: (UE.x, UE.z, -UE.y) / 100. Cooked component
mesh bounds take precedence over clearance envelopes; neither is a triangle mesh.
"""

from __future__ import annotations

import json
import math
import re
import struct
import zlib
from collections import defaultdict
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypedDict

from flab2bp.sfy.archive import ArchiveError, Reader
from flab2bp.sfy.chunks import CHUNK_MARK, CHUNK_TAG, COMPRESSION_ZLIB, MAX_CHUNK
from flab2bp.sfy.codec import Blueprint, read_sbp
from flab2bp.sfy.geometry import Quaternion, Vector, placed_box, quat_rotate, world_port
from flab2bp.sfy.header import read_header
from flab2bp.sfy.layout.splines import hermite, segment_length
from flab2bp.sfy.objects import ACTOR, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import (
    Array,
    Bool,
    Byte,
    Double,
    Enum,
    Float,
    Int,
    Name,
    Object,
    PropertyList,
    Quat,
    Str,
    Struct,
    Value,
)
from flab2bp.sfy.properties import Vector as PropertyVector
from flab2bp.sfy.query import connected, find, spline_points
from flab2bp.sfy.registry import Buildable, ClearanceBox, Registry, load_registry
from flab2bp.sfy.trailers import PowerLineTrailer

MAX_SBP_BYTES = 16 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_SCENE_OBJECTS = 25_000
MAX_SCENE_POINTS = 500_000
_CHUNK_HEADER = struct.Struct("<IiqBqqqq")
_ZERO: Vector = (0.0, 0.0, 0.0)
_ONE: Vector = (1.0, 1.0, 1.0)
_IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), _ZERO, _ONE)

Kind = Literal[
    "machine",
    "belt",
    "lift",
    "splitter",
    "merger",
    "foundation",
    "power",
    "storage",
    "pipe",
    "other",
]
Direction = Literal["input", "output", "any", "unknown"]


class SceneBox(TypedDict):
    center: Vector
    size: Vector
    quaternion: Quaternion


class ScenePort(TypedDict):
    name: str
    position: Vector
    direction: Direction
    connectedTo: str | None


class Detail(TypedDict):
    label: str
    value: str


class SceneObject(TypedDict):
    id: str
    className: str
    name: str
    kind: Kind
    position: Vector
    boxes: list[SceneBox]
    points: list[Vector]
    pathWidth: float | None
    pathHeight: float | None
    flowDirection: Literal["forward", "reverse", "unknown"]
    color: int
    ports: list[ScenePort]
    details: list[Detail]
    recipe: str | None
    clockPercent: float | None
    item: str | None
    ratePerMinute: float | None


class Bounds(TypedDict):
    min: Vector
    max: Vector


class Material(TypedDict):
    name: str
    count: int


class SatisfactoryScene(TypedDict):
    game: Literal["sfy"]
    title: str
    saveVersion: int
    bounds: Bounds
    center: Vector
    radius: float
    objects: list[SceneObject]
    materials: list[Material]
    warnings: list[str]


class SceneError(ValueError):
    """An invalid or unsupported binary scene request."""


class SceneTooLarge(SceneError):
    """A binary or scene exceeds a bounded web resource limit."""


def _check_compression(data: bytes) -> None:
    """Bound actual zlib output before the codec's whole-stream allocation.

    Declared sizes alone are not a decompression-bomb defence. Validate each
    chunk using a capped decompressor, including EOF and trailing-data checks,
    then let the existing codec own all object/property decoding.
    """
    if len(data) > MAX_SBP_BYTES:
        raise SceneTooLarge(f"Blueprint exceeds {MAX_SBP_BYTES // 1024 // 1024} MiB")
    reader = Reader(data)
    _ = read_header(reader)
    pos, total = reader.pos, 0
    if pos == len(data):
        raise SceneError("Blueprint has no compressed object stream")
    while pos < len(data):
        if len(data) - pos < _CHUNK_HEADER.size:
            raise SceneError("Truncated blueprint compression header")
        reader.pos = pos
        tag, mark = reader.u32(), reader.i32()
        maximum, method = reader.i64(), reader.u8()
        c1, u1, c2, u2 = reader.i64(), reader.i64(), reader.i64(), reader.i64()
        if (tag, mark, maximum, method) != (CHUNK_TAG, CHUNK_MARK, MAX_CHUNK, COMPRESSION_ZLIB):
            raise SceneError("Invalid blueprint compression header")
        if (c1, u1) != (c2, u2) or c1 <= 0 or not 0 <= u1 <= MAX_CHUNK:
            raise SceneError("Invalid blueprint compression sizes")
        total += u1
        if total > MAX_DECOMPRESSED_BYTES:
            raise SceneTooLarge("Blueprint expands beyond the 64 MiB scene limit")
        pos += _CHUNK_HEADER.size
        if c1 > len(data) - pos:
            raise SceneError("Truncated compressed blueprint data")
        inflater = zlib.decompressobj()
        raw = inflater.decompress(data[pos : pos + c1], u1 + 1)
        if len(raw) != u1 or not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:
            raise SceneError("Compressed blueprint size or stream terminator is invalid")
        pos += c1


@lru_cache(maxsize=1)
def _registry() -> Registry:
    return load_registry()


@lru_cache(maxsize=1)
def _viewer_geometry() -> dict[str, tuple[ClearanceBox, ...]]:
    """Load extracted actor-local component envelopes, separate from placement rules."""
    path = Path(__file__).parents[1] / "sfy" / "data" / "viewer_geometry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: tuple(
            ClearanceBox(
                (part["min"][0], part["min"][1], part["min"][2]),
                (part["max"][0], part["max"][1], part["max"][2]),
                False,
                _ZERO,
            )
            for part in parts
        )
        for name, parts in data["buildables"].items()
    }


def _body_boxes(buildable: Buildable) -> tuple[ClearanceBox, ...]:
    physical = _viewer_geometry().get(buildable.class_name)
    if physical is not None:
        return physical
    # A spline's mMesh/mMidMesh is only one repeated segment, not its body.
    if (
        buildable.mesh_bounds_property == "mInstanceDataCDO.Instances"
        and buildable.mesh_bounds_cm is not None
    ):
        return (ClearanceBox(*buildable.mesh_bounds_cm, False, _ZERO),)
    return buildable.clearance


def scene_from_sbp(data: bytes, *, title: str = "Satisfactory blueprint") -> SatisfactoryScene:
    """Decode a bounded raw upload or a retained build artifact."""
    try:
        _check_compression(data)
        return scene_from_blueprint(read_sbp(data), title=title)
    except SceneError:
        raise
    except (
        ArchiveError,
        zlib.error,
        struct.error,
        UnicodeError,
        RecursionError,
        OverflowError,
    ) as exc:
        raise SceneError(f"Invalid Satisfactory blueprint: {exc}") from exc


def _vector(value: PropertyVector) -> Vector:
    return (value.x, value.y, value.z)


def _finite(values: tuple[float, ...]) -> None:
    if not all(math.isfinite(value) for value in values):
        raise SceneError("Blueprint contains non-finite geometry")


def _unit(vector: Vector) -> Vector:
    length = math.hypot(*vector)
    if length == 0:
        raise SceneError("Blueprint contains a degenerate geometry axis")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _normal_quat(q: Quaternion) -> Quaternion:
    _finite(q)
    length = math.hypot(*q)
    if length < 1e-12:
        raise SceneError("Blueprint contains a zero rotation quaternion")
    return (q[0] / length, q[1] / length, q[2] / length, q[3] / length)


def _transform(transform: Transform) -> Transform:
    _finite(transform.translation + transform.scale)
    return replace(transform, rotation=_normal_quat(transform.rotation))


def _point(transform: Transform, point: Vector) -> Vector:
    _finite(point)
    sx, sy, sz = transform.scale
    turned = quat_rotate(transform.rotation, (point[0] * sx, point[1] * sy, point[2] * sz))
    tx, ty, tz = transform.translation
    return (turned[0] + tx, turned[1] + ty, turned[2] + tz)


def _three(point: Vector) -> Vector:
    _finite(point)
    return (point[0] / 100.0, point[2] / 100.0, -point[1] / 100.0)


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _axes_quat(axes: tuple[Vector, Vector, Vector]) -> Quaternion:
    """Quaternion of a right-handed orthonormal column basis."""
    x, y, z = axes
    trace = x[0] + y[1] + z[2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = ((y[2] - z[1]) / s, (z[0] - x[2]) / s, (x[1] - y[0]) / s, s / 4)
    elif x[0] > y[1] and x[0] > z[2]:
        s = math.sqrt(1.0 + x[0] - y[1] - z[2]) * 2
        q = (s / 4, (y[0] + x[1]) / s, (z[0] + x[2]) / s, (y[2] - z[1]) / s)
    elif y[1] > z[2]:
        s = math.sqrt(1.0 + y[1] - x[0] - z[2]) * 2
        q = ((y[0] + x[1]) / s, s / 4, (z[1] + y[2]) / s, (z[0] - x[2]) / s)
    else:
        s = math.sqrt(1.0 + z[2] - x[0] - y[1]) * 2
        q = ((z[0] + x[2]) / s, (z[1] + y[2]) / s, s / 4, (x[1] - y[0]) / s)
    return _normal_quat(q)


def _box(box: ClearanceBox, transform: Transform, warnings: list[str], label: str) -> SceneBox:
    # placed_box is the canonical local RelativeTransform composition. Apply
    # actor scale to both centre and complete axes (it omits actor scale).
    center, axes, half = placed_box(box, _IDENTITY)
    center = _three(_point(transform, center))
    scaled: list[Vector] = []
    sizes: list[float] = []
    for axis, extent in zip(axes, half, strict=True):
        local = (
            axis[0] * transform.scale[0],
            axis[1] * transform.scale[1],
            axis[2] * transform.scale[2],
        )
        world = quat_rotate(transform.rotation, local)
        scaled.append((world[0], world[2], -world[1]))
        sizes.append(math.hypot(*world) * extent * 2 / 100)
    _finite((sizes[0], sizes[1], sizes[2]))
    if any(math.hypot(*axis) < 1e-12 for axis in scaled):
        warnings.append(f"{label}: zero-scale geometry shown as its degenerate enclosing bounds")
        normalized = None
    else:
        normalized = (_unit(scaled[0]), _unit(scaled[1]), _unit(scaled[2]))
    if normalized is None or any(
        abs(_dot(normalized[a], normalized[b])) > 1e-6 for a, b in ((0, 1), (0, 2), (1, 2))
    ):
        if normalized is not None:
            warnings.append(
                f"{label}: nonuniform scale shears its geometry; "
                "showing a conservative world-aligned envelope"
            )
        reach = [sum(abs(scaled[k][i]) * half[k] / 100 for k in range(3)) for i in range(3)]
        _finite((reach[0] * 2, reach[1] * 2, reach[2] * 2))
        return {
            "center": center,
            "size": (reach[0] * 2, reach[1] * 2, reach[2] * 2),
            "quaternion": _IDENTITY.rotation,
        }
    a, b, c = normalized
    if _dot(_cross(a, b), c) < 0:
        c = (-c[0], -c[1], -c[2])
    return {
        "center": center,
        "size": (sizes[0], sizes[1], sizes[2]),
        "quaternion": _axes_quat((a, b, c)),
    }


def _top(data: ObjectData) -> Transform | None:
    value = find(data.properties, "mTopTransform")
    if not isinstance(value, Struct):
        return None
    # FTransform struct members omit identity/default values in save properties.
    translation = find(value.fields, "Translation")
    rotation = find(value.fields, "Rotation")
    scale = find(value.fields, "Scale3D")
    if translation is not None and not isinstance(translation, PropertyVector):
        raise SceneError("Lift top translation is not a vector")
    if rotation is not None and not isinstance(rotation, Quat):
        raise SceneError("Lift top rotation is not a quaternion")
    if scale is not None and not isinstance(scale, PropertyVector):
        raise SceneError("Lift top scale is not a vector")
    return _transform(
        Transform(
            (rotation.x, rotation.y, rotation.z, rotation.w)
            if rotation is not None
            else _IDENTITY.rotation,
            _vector(translation) if translation is not None else _ZERO,
            _vector(scale) if scale is not None else _ONE,
        )
    )


def _kind(buildable: Buildable | None) -> Kind:
    if buildable is None:
        return "other"
    native = buildable.native_class
    if native == "FGBuildableConveyorBelt":
        return "belt"
    if native == "FGBuildableConveyorLift":
        return "lift"
    if "Splitter" in native:
        return "splitter"
    if "Merger" in native:
        return "merger"
    if "Foundation" in native:
        return "foundation"
    if "Storage" in native and "Power" not in native:
        return "storage"
    if "Pipeline" in native or native == "FGBuildablePipeHyper":
        return "pipe"
    if "Power" in native or "Wire" in native or "CircuitSwitch" in native:
        return "power"
    if any(term in native for term in ("Manufacturer", "Extractor", "Generator", "Fracking")):
        return "machine"
    return "other"


_COLORS: dict[Kind, int] = {
    "machine": 0xDB954F,
    "belt": 0x54B4CF,
    "lift": 0xD5BC52,
    "splitter": 0x73AF76,
    "merger": 0xC391CC,
    "foundation": 0x718092,
    "power": 0xEDC35A,
    "storage": 0xAC956F,
    "pipe": 0xB97969,
    "other": 0xBD9FCE,
}


def _label(path: str) -> str:
    name = path.rsplit(".", 1)[-1]
    name = re.sub(r"^(?:Desc_|Build_|Recipe_)", "", name).removesuffix("_C")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ")


def _number(value: Value | None) -> float | None:
    if isinstance(value, (Float, Double, Int)):
        if math.isfinite(value.v):
            return float(value.v)
        raise SceneError("Blueprint contains a non-finite configuration value")
    return None


def _scalar(value: Value) -> str | None:
    if isinstance(value, (Float, Double, Int)):
        return f"{value.v:g}"
    if isinstance(value, (Str, Name, Enum, Bool, Byte)):
        return str(value.v)
    return None


def _direction(value: Value | None, fallback: str) -> Direction:
    if isinstance(value, (Enum, Byte)):
        name = str(value.v).rsplit("::", 1)[-1]
        directions: dict[str, Direction] = {
            "FCD_INPUT": "input",
            "FCD_OUTPUT": "output",
            "FCD_ANY": "any",
            "0": "input",
            "1": "output",
            "2": "any",
        }
        return directions.get(name, "unknown")
    if fallback == "input":
        return "input"
    if fallback == "output":
        return "output"
    if fallback == "any":
        return "any"
    return "unknown"


def _sample(data: ObjectData, transform: Transform) -> list[Vector]:
    raw = spline_points(data)
    if len(raw) < 2:
        return []
    points: list[Vector] = []
    for first, second in zip(raw, raw[1:], strict=False):
        p0, t0 = _vector(first[0]), _vector(first[2])
        p1, t1 = _vector(second[0]), _vector(second[1])
        _finite(p0 + t0 + p1 + t1)
        length = segment_length(p0, t0, p1, t1) * max(abs(v) for v in transform.scale)
        if not math.isfinite(length):
            raise SceneError("Spline length is not finite")
        steps = max(8, math.ceil(length / 25.0))
        if len(points) + steps + 1 > MAX_SCENE_POINTS:
            raise SceneTooLarge("Blueprint spline exceeds the scene sample limit")
        points.extend(
            _three(_point(transform, hermite(p0, t0, p1, t1, i / steps))) for i in range(steps)
        )
    points.append(_three(_point(transform, _vector(raw[-1][0]))))
    return points


def _profile(
    buildable: Buildable | None, transform: Transform
) -> tuple[float | None, float | None]:
    if buildable is None or buildable.mesh_bounds_cm is None:
        return (None, None)
    low, high = buildable.mesh_bounds_cm
    # Profiles remain schematic under nonuniform scale; the path itself always
    # receives the complete transform. A warning names that limitation below.
    width = (high[1] - low[1]) * abs(transform.scale[1]) / 100
    height = (high[2] - low[2]) * abs(transform.scale[2]) / 100
    _finite((width, height))
    return (width if width > 0 else None, height if height > 0 else None)


def scene_from_blueprint(
    blueprint: Blueprint,
    *,
    title: str = "Satisfactory blueprint",
    registry: Registry | None = None,
) -> SatisfactoryScene:
    """Keep every actor, its raw identity, saved configuration and real geometry."""
    registry = _registry() if registry is None else registry
    actors = [(header, data) for header, data in blueprint.objects if header.kind == ACTOR]
    if len(actors) > MAX_SCENE_OBJECTS:
        raise SceneTooLarge(f"Blueprint exceeds {MAX_SCENE_OBJECTS} scene actors")
    warnings: list[str] = []
    objects: list[SceneObject] = []
    components: dict[str, list[tuple[ObjectHeader, ObjectData]]] = defaultdict(list)
    for header, data in blueprint.objects:
        if header.kind != ACTOR and header.parent is not None:
            components[header.parent].append((header, data))
    # Every recorded component path is kept verbatim; registry names identify
    # archetype geometry, never generated IDs or numeric actor suffixes.
    port_positions: dict[str, Vector] = {}
    deferred: list[tuple[SceneObject, ObjectHeader, ObjectData, Direction]] = []
    wires: list[tuple[SceneObject, PowerLineTrailer]] = []
    samples = 0
    for header, data in actors:
        buildable = registry.buildables.get(header.class_name)
        kind = _kind(buildable)
        transform = _transform(header.transform) if header.transform is not None else _IDENTITY
        if header.transform is None:
            warnings.append(
                f"{header.path}: no saved transform; diagnostic marker is at the origin"
            )
        name = buildable.display_name if buildable is not None else _label(header.class_name)
        details: list[Detail] = [
            {"label": "Actor", "value": header.path},
            {"label": "Class", "value": header.class_path},
        ]
        details.append({"label": "Actor scale", "value": str(transform.scale)})
        if buildable is not None and buildable.native_class == "FGBuildableWidgetSign":
            elements = find(data.properties, "mPrefabTextElementSaveData")
            if isinstance(elements, Array):
                for element in elements.items:
                    if not isinstance(element, Struct):
                        continue
                    element_name = find(element.fields, "ElementName")
                    text = find(element.fields, "Text")
                    if isinstance(element_name, Str) and isinstance(text, Str):
                        details.append({"label": f"Sign {element_name.v}", "value": text.v})
                        if element_name.v == "Name" and text.v:
                            name = text.v
        recipe_value = find(data.properties, "mCurrentRecipe")
        recipe: str | None = None
        if isinstance(recipe_value, Object) and not recipe_value.ref.is_null:
            recipe_class = recipe_value.ref.path.rsplit(".", 1)[-1]
            recipe_data = registry.recipes.get(recipe_class)
            recipe = recipe_data.display_name if recipe_data is not None else _label(recipe_class)
            details.append({"label": "Recipe asset", "value": recipe_value.ref.path})
        clock = _number(find(data.properties, "mCurrentPotential"))
        clock_percent = clock * 100 if clock is not None else None
        if clock_percent is not None and not math.isfinite(clock_percent):
            raise SceneError("Blueprint clock percentage overflows the supported numeric range")
        for prop in data.properties:
            scalar = _scalar(prop.value)
            if scalar is not None:
                details.append({"label": prop.tag.name, "value": scalar})
        boxes = (
            [_box(box, transform, warnings, header.path) for box in _body_boxes(buildable)]
            if buildable is not None
            else []
        )
        if buildable is not None and buildable.native_class == "FGBuildableBeam":
            length = _number(find(data.properties, "mLength"))
            size = buildable.beam_size_cm
            if length is not None and length > 0 and size is not None:
                boxes = [
                    _box(
                        ClearanceBox(
                            (0.0, -size / 2, -size / 2),
                            (length, size / 2, size / 2),
                            True,
                            _ZERO,
                        ),
                        transform,
                        warnings,
                        header.path,
                    )
                ]
        thickness = _number(find(data.properties, "mSnappedBuildingThickness"))
        if thickness is not None and thickness > 0 and buildable is not None:
            mesh = buildable.mesh_bounds_cm
            if mesh is not None:
                axes = (0, 1) if "Lift" in header.class_name else (1, 2)
                half = tuple(max(abs(mesh[0][axis]), abs(mesh[1][axis])) for axis in axes)
                boxes = [
                    _box(
                        ClearanceBox(
                            (-half[0], -half[1], -thickness / 2),
                            (half[0], half[1], thickness / 2),
                            True,
                            _ZERO,
                        ),
                        transform,
                        warnings,
                        header.path,
                    )
                ]
                warnings.append(
                    f"{header.path}: passthrough middle envelope shown; cap meshes unavailable"
                )
        points = _sample(data, transform)
        top = _top(data) if kind == "lift" else None
        if top is not None and buildable is not None and buildable.lift is not None:
            bottom = buildable.lift.bottom_offset
            points = [_three(_point(transform, bottom)), _three(_point(transform, top.translation))]
            details.append({"label": "Top relative rotation", "value": str(top.rotation)})
            details.append({"label": "Top relative scale", "value": str(top.scale)})
            width = registry.limits.lift_clearance_half_extent_cm
            if width is not None:
                low, high = min(bottom[2], top.translation[2]), max(bottom[2], top.translation[2])
                if top.translation[:2] == bottom[:2]:
                    envelope = ClearanceBox(
                        (-width, -width, low),
                        (width, width, high),
                        False,
                        (bottom[0], bottom[1], 0.0),
                    )
                    boxes.append(_box(envelope, transform, warnings, header.path))
                else:
                    warnings.append(
                        f"{header.path}: nonvertical lift top; only its actual path is drawn"
                    )
            details.append(
                {
                    "label": "Geometry",
                    "value": (
                        "Runtime endpoints; partial registry clearance span, not housing meshes"
                    ),
                }
            )
            warnings.append(
                f"{header.path}: lift envelope width is authoritative; axial clearance is partial; "
                "endpoint housings unavailable"
            )
        path_width, path_height = (
            _profile(buildable, transform) if kind in ("belt", "pipe") else (None, None)
        )
        if kind in ("belt", "pipe") and len(set(abs(v) for v in transform.scale)) > 1:
            warnings.append(
                f"{header.path}: nonuniformly scaled path has a schematic constant cross-section"
            )
        obj: SceneObject = {
            "id": header.path,
            "className": header.class_name,
            "name": name,
            "kind": kind,
            "position": _three(transform.translation),
            "boxes": boxes,
            "points": points,
            "pathWidth": path_width,
            "pathHeight": path_height,
            "color": _COLORS[kind],
            "flowDirection": "forward" if kind == "lift" and top is not None else "unknown",
            "ports": [],
            "details": details,
            "recipe": recipe,
            "clockPercent": clock_percent,
            "item": None,
            "ratePerMinute": None,
        }
        objects.append(obj)
        saved = {
            component.name: (component, payload) for component, payload in components[header.path]
        }
        registry_ports = (
            {port.name: port for port in buildable.ports} if buildable is not None else {}
        )
        for port_name in dict.fromkeys([*registry_ports, *saved]):
            port = registry_ports.get(port_name)
            pair = saved.get(port_name)
            if port is None and (pair is None or "Connection" not in pair[0].class_name):
                continue
            component_header, component_data = pair if pair is not None else (None, None)
            if component_header is not None:
                details.append({"label": f"Port {port_name}", "value": component_header.path})
            properties: PropertyList = (
                component_data.properties if component_data is not None else ()
            )
            direction = _direction(
                find(properties, "mDirection"), port.direction if port is not None else "unknown"
            )
            relative = find(properties, "RelativeLocation")
            world: Vector | None = None
            if isinstance(relative, PropertyVector):
                world = _three(_point(transform, _vector(relative)))
            elif (
                kind == "lift"
                and top is not None
                and buildable is not None
                and buildable.flow is not None
                and buildable.lift is not None
            ):
                if port_name == buildable.flow.entry:
                    world, direction = points[0], "input"
                elif port_name == buildable.flow.exit:
                    world, direction = points[-1], "output"
            elif kind == "pipe" and points and buildable is not None:
                # The cooked CDO's mConnection0/1 members identify these
                # components; PipeBase.SetupConnections selects spline ends.
                if port_name == "PipelineConnection0":
                    world = points[0]
                elif port_name == "PipelineConnection1":
                    world = points[-1]
            elif kind not in ("belt", "pipe", "lift") and port is not None:
                world = _three(world_port(transform, port))
            if component_data is not None:
                link = connected(component_data)
                target = link.path if link is not None else None
                if target is not None:
                    connection_name = component_header.path if component_header else port_name
                    details.append(
                        {
                            "label": f"Connection {connection_name}",
                            "value": target,
                        }
                    )
                wire_list = find(properties, "mWires")
                if isinstance(wire_list, Array):
                    for wire in wire_list.items:
                        if isinstance(wire, Object) and not wire.ref.is_null:
                            details.append(
                                {"label": f"Wire at {port_name}", "value": wire.ref.path}
                            )
            else:
                target = None
            if world is not None:
                obj["ports"].append(
                    {
                        "name": port_name,
                        "position": world,
                        "direction": direction,
                        "connectedTo": target,
                    }
                )
                if component_header is not None:
                    port_positions[component_header.path] = world
            elif component_header is not None and component_data is not None:
                deferred.append((obj, component_header, component_data, direction))
        if isinstance(data.trailer, PowerLineTrailer):
            wires.append((obj, data.trailer))
        elif not boxes and len(points) < 2:
            warnings.append(
                f"{header.path} ({header.class_name}): geometry unavailable; diagnostic marker only"
            )
            obj["color"] = 0xFF44CC
        if buildable is None:
            warnings.append(f"{header.path}: unknown actor class {header.class_name}")
        samples += len(points)
        if samples > MAX_SCENE_POINTS:
            raise SceneTooLarge(f"Blueprint exceeds {MAX_SCENE_POINTS} total path samples")
    # Saved links are evidence for the physical endpoint, unlike component name
    # conventions. Propagate through chains; never invent an unconnected end.
    while deferred:
        unresolved: list[tuple[SceneObject, ObjectHeader, ObjectData, Direction]] = []
        for obj, header, data, direction in deferred:
            link = connected(data)
            world = port_positions.get(link.path) if link is not None else None
            if world is None and obj["kind"] == "belt" and len(obj["points"]) >= 2:
                # Once one of this conveyor's two ports is located by a saved
                # link, the other is at the remaining geometric endpoint. This
                # does not assign start/end from an arbitrary component name.
                buildable = registry.buildables.get(obj["className"])
                flow = buildable.flow if buildable is not None else None
                if flow is not None and header.name in (flow.entry, flow.exit):
                    other_name = flow.exit if header.name == flow.entry else flow.entry
                    other = next((p for p in obj["ports"] if p["name"] == other_name), None)
                    if other is not None:
                        first, last = obj["points"][0], obj["points"][-1]
                        if math.dist(other["position"], first) <= 0.05:
                            world = last
                        elif math.dist(other["position"], last) <= 0.05:
                            world = first
            if world is None:
                unresolved.append((obj, header, data, direction))
                continue
            if obj["points"]:
                endpoints = (obj["points"][0], obj["points"][-1])
                nearest = (
                    endpoints[0]
                    if math.dist(endpoints[0], world) <= math.dist(endpoints[1], world)
                    else endpoints[1]
                )
                if math.dist(nearest, world) > 0.05:
                    unresolved.append((obj, header, data, direction))
                    continue
                world = nearest
            port_positions[header.path] = world
            obj["ports"].append(
                {
                    "name": header.name,
                    "position": world,
                    "direction": direction,
                    "connectedTo": link.path if link is not None else None,
                }
            )
        if len(unresolved) == len(deferred):
            break
        deferred = unresolved
    for _, header, _, _ in deferred:
        warnings.append(
            f"{header.path}: connection location unavailable; saved link retained in object details"
        )
    for obj in objects:
        if obj["kind"] != "belt" or len(obj["points"]) < 2:
            continue
        buildable = registry.buildables.get(obj["className"])
        flow = buildable.flow if buildable is not None else None
        if flow is None:
            continue
        entry = next((port for port in obj["ports"] if port["name"] == flow.entry), None)
        if entry is not None:
            at_first = math.dist(entry["position"], obj["points"][0]) <= 0.05
            at_last = math.dist(entry["position"], obj["points"][-1]) <= 0.05
            if at_first != at_last:
                obj["flowDirection"] = "forward" if at_first else "reverse"
    for obj, trailer in wires:
        for index, ref in enumerate(trailer.connections):
            endpoint = port_positions.get(ref.path)
            obj["details"].append({"label": f"Wire endpoint {index + 1}", "value": ref.path})
            if endpoint is None:
                warnings.append(f"{obj['id']}: wire endpoint {ref.path} has no known geometry")
            else:
                obj["points"].append(endpoint)
                obj["ports"].append(
                    {
                        "name": f"Endpoint {index + 1}",
                        "position": endpoint,
                        "direction": "any",
                        "connectedTo": ref.path,
                    }
                )
        if len(obj["points"]) != 2:
            obj["points"] = []
            obj["color"] = 0xFF44CC
    bounds = _bounds(objects)
    center = tuple((bounds["min"][i] + bounds["max"][i]) / 2 for i in range(3))
    radius = max(math.dist(bounds["min"], bounds["max"]) / 2, 1.0)
    _finite(center + (radius,))
    materials: list[Material] = []
    for cost in blueprint.header.cost:
        if cost.amount < 0:
            raise SceneError("Blueprint header contains a negative material count")
        materials.append({"name": _label(cost.item.path), "count": cost.amount})
    return {
        "game": "sfy",
        "title": title,
        "saveVersion": blueprint.header.save_version,
        "bounds": bounds,
        "center": (center[0], center[1], center[2]),
        "radius": radius,
        "objects": objects,
        "materials": materials,
        "warnings": warnings,
    }


def _bounds(objects: list[SceneObject]) -> Bounds:
    low, high = [math.inf] * 3, [-math.inf] * 3

    def include(point: Vector, padding: float = 0) -> None:
        for axis in range(3):
            low[axis] = min(low[axis], point[axis] - padding)
            high[axis] = max(high[axis], point[axis] + padding)

    for obj in objects:
        # Wire actor origins can be far from their actual endpoints.
        if not obj["boxes"] and not obj["points"]:
            include(obj["position"])
        for point in obj["points"]:
            include(point, max(obj["pathWidth"] or 0, obj["pathHeight"] or 0) / 2)
        for port in obj["ports"]:
            include(port["position"])
        for box in obj["boxes"]:
            axes = (
                quat_rotate(box["quaternion"], (1, 0, 0)),
                quat_rotate(box["quaternion"], (0, 1, 0)),
                quat_rotate(box["quaternion"], (0, 0, 1)),
            )
            reach = [sum(abs(axes[k][i]) * box["size"][k] / 2 for k in range(3)) for i in range(3)]
            include(
                (
                    box["center"][0] - reach[0],
                    box["center"][1] - reach[1],
                    box["center"][2] - reach[2],
                )
            )
            include(
                (
                    box["center"][0] + reach[0],
                    box["center"][1] + reach[1],
                    box["center"][2] + reach[2],
                )
            )
    if not objects:
        return {"min": _ZERO, "max": _ZERO}
    return {"min": (low[0], low[1], low[2]), "max": (high[0], high[1], high[2])}
