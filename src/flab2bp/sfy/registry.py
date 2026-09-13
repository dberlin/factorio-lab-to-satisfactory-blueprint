"""In-memory shape of the Satisfactory game-data registry.

The registry has two halves. This module holds the dataclasses and the readers;
:mod:`flab2bp.sfy.docs` fills the first half from the game's own ``Docs.json``
dump (buildables, recipes, descriptors) and writes it to ``data/docs.json``.
The second half -- connection ports and the hologram/spline limits harvested
from the installed game -- lands in ``data/registry.json``, which
:func:`load_registry` reads.

Every distance is in Unreal centimetres and every angle in degrees, matching the
numbers the game itself stores; nothing here is converted to foundation tiles.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = [
    "BELT_MAX_SPLINE_CM",
    "PIPE_BEND_RADIUS_2D_CM",
    "PIPE_MAX_SPLINE_CM",
    "PIPE_MIN_BEND_RADIUS_CM",
    "Buildable",
    "ClearanceBox",
    "Limits",
    "Port",
    "Recipe",
    "Registry",
    "RegistryError",
    "load_registry",
]

# Defaults transcribed from the game's public hologram headers. Task 10 measures
# the rest from the install and overrides these in ``registry.json``.
BELT_MAX_SPLINE_CM = 5600.1
PIPE_MAX_SPLINE_CM = 5600.1
PIPE_BEND_RADIUS_2D_CM = 199.0
PIPE_MIN_BEND_RADIUS_CM = 75.0

DATA_DIR = Path(__file__).resolve().parent / "data"
REGISTRY_PATH = DATA_DIR / "registry.json"

Vector = tuple[float, float, float]


class RegistryError(ValueError):
    """A registry payload was missing a section or carried an unknown key."""


@dataclass(frozen=True, slots=True)
class ClearanceBox:
    """One entry of a buildable's ``mClearanceData``.

    ``min``/``max`` are the box corners in the buildable's local frame and
    ``translation`` offsets the box from the buildable's origin. ``soft`` marks
    ``CT_Soft`` boxes, which block placement but let belts and pipes pass.
    """

    min: Vector
    max: Vector
    soft: bool
    translation: Vector


@dataclass(frozen=True, slots=True)
class Port:
    """A belt, pipe or power connection on a buildable, in its local frame."""

    name: str
    kind: str  # "belt" | "pipe" | "power"
    direction: str  # "input" | "output" | "any" | "snap_only"
    translation: Vector
    rotation: Vector
    clearance: float | None


@dataclass(frozen=True, slots=True)
class Buildable:
    """A placeable building, with whatever of its stats Docs.json carries.

    A field is ``None`` when the game data has no such key for this class -- a
    conveyor belt has no ``manufacturing_speed`` and a constructor has no
    ``belt_speed_per_min``. ``power_mw`` is 0.0 rather than ``None`` for the many
    buildings that draw no power at all.
    """

    class_name: str
    display_name: str
    native_class: str
    clearance: tuple[ClearanceBox, ...]
    power_mw: float
    manufacturing_speed: float | None
    belt_speed_per_min: float | None
    mesh_height_cm: float | None
    width_cm: float | None
    depth_cm: float | None
    height_cm: float | None
    designer_dims: tuple[int, int, int] | None
    max_potential: float | None
    potential_shard_slots: int | None
    production_boost_slots: int | None
    ports: tuple[Port, ...] = ()


@dataclass(frozen=True, slots=True)
class Recipe:
    """A production recipe. Amounts are whole items (fluids are in centilitres)."""

    class_name: str
    display_name: str
    ingredients: tuple[tuple[str, int], ...]
    products: tuple[tuple[str, int], ...]
    duration_s: float
    producers: tuple[str, ...]
    variable_power_constant: float
    variable_power_factor: float


@dataclass(frozen=True, slots=True)
class Limits:
    """Spline, lift and hologram limits the placer must respect.

    Only the four values the public headers state outright have defaults; every
    other field stays ``None`` until Task 10 measures it from the install.
    """

    belt_max_spline_cm: float = BELT_MAX_SPLINE_CM
    belt_bend_radius_cm: float | None = None
    belt_max_incline_deg: float | None = None
    lift_step_cm: float | None = None
    lift_min_cm: float | None = None
    lift_max_cm: float | None = None
    lift_min_vertical_cm: float | None = None
    pipe_max_spline_cm: float = PIPE_MAX_SPLINE_CM
    pipe_bend_radius_cm: float | None = None
    pipe_bend_radius_2d_cm: float = PIPE_BEND_RADIUS_2D_CM
    pipe_min_bend_radius_cm: float = PIPE_MIN_BEND_RADIUS_CM
    wire_max_cm: dict[str, float] = field(default_factory=dict)
    hologram_grid_cm: float | None = None
    hologram_rotation_step_deg: float | None = None


@dataclass(frozen=True, slots=True)
class Registry:
    """The whole game-data registry: what can be built, and out of what."""

    provenance: dict[str, Any]
    buildables: dict[str, Buildable]
    recipes: dict[str, Recipe]
    descriptors: dict[str, str]  # Desc_X_C -> Build_X_C
    build_recipes: dict[str, str]  # Build_X_C -> Recipe_X_C
    limits: Limits

    @classmethod
    def from_docs_only(cls, data: Mapping[str, Any]) -> Registry:
        """Build a registry from a ``docs.json`` payload alone.

        Ports are empty and the limits are the header defaults, because neither
        can be read out of Docs.json; :func:`load_registry` supplies both.
        """
        return cls(
            provenance=dict(_require(data, "provenance")),
            buildables=_buildables(_require(data, "buildables")),
            recipes=_recipes(_require(data, "recipes")),
            descriptors=dict(_require(data, "descriptors")),
            build_recipes=dict(_require(data, "build_recipes")),
            limits=Limits(),
        )


def _require(data: Mapping[str, Any], key: str) -> Any:
    """Return ``data[key]``, or raise ``RegistryError`` naming the missing key."""
    try:
        return data[key]
    except KeyError:
        raise RegistryError(f"registry payload is missing the {key!r} section") from None


def _vector(raw: Sequence[float]) -> Vector:
    x, y, z = (float(v) for v in raw)
    return (x, y, z)


def _clearance(raw: Iterable[Mapping[str, Any]]) -> tuple[ClearanceBox, ...]:
    return tuple(
        ClearanceBox(
            min=_vector(box["min"]),
            max=_vector(box["max"]),
            soft=bool(box["soft"]),
            translation=_vector(box["translation"]),
        )
        for box in raw
    )


def _ports(raw: Iterable[Mapping[str, Any]]) -> tuple[Port, ...]:
    return tuple(
        Port(
            name=str(port["name"]),
            kind=str(port["kind"]),
            direction=str(port["direction"]),
            translation=_vector(port["translation"]),
            rotation=_vector(port["rotation"]),
            clearance=None if port.get("clearance") is None else float(port["clearance"]),
        )
        for port in raw
    )


def _buildables(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, Buildable]:
    out: dict[str, Buildable] = {}
    for class_name, entry in raw.items():
        try:
            dims = entry["designer_dims"]
            out[class_name] = Buildable(
                class_name=class_name,
                display_name=entry["display_name"],
                native_class=entry["native_class"],
                clearance=_clearance(entry["clearance"]),
                power_mw=float(entry["power_mw"]),
                manufacturing_speed=_opt_float(entry["manufacturing_speed"]),
                belt_speed_per_min=_opt_float(entry["belt_speed_per_min"]),
                mesh_height_cm=_opt_float(entry["mesh_height_cm"]),
                width_cm=_opt_float(entry["width_cm"]),
                depth_cm=_opt_float(entry["depth_cm"]),
                height_cm=_opt_float(entry["height_cm"]),
                designer_dims=None if dims is None else (int(dims[0]), int(dims[1]), int(dims[2])),
                max_potential=_opt_float(entry["max_potential"]),
                potential_shard_slots=_opt_int(entry["potential_shard_slots"]),
                production_boost_slots=_opt_int(entry["production_boost_slots"]),
                ports=_ports(entry.get("ports", ())),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RegistryError(f"buildable {class_name!r} is malformed: {exc}") from exc
    return out


def _recipes(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, Recipe]:
    out: dict[str, Recipe] = {}
    for class_name, entry in raw.items():
        try:
            out[class_name] = Recipe(
                class_name=class_name,
                display_name=entry["display_name"],
                ingredients=_item_amounts(entry["ingredients"]),
                products=_item_amounts(entry["products"]),
                duration_s=float(entry["duration_s"]),
                producers=tuple(str(p) for p in entry["producers"]),
                variable_power_constant=float(entry["variable_power_constant"]),
                variable_power_factor=float(entry["variable_power_factor"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError(f"recipe {class_name!r} is malformed: {exc}") from exc
    return out


def _item_amounts(raw: Iterable[Sequence[Any]]) -> tuple[tuple[str, int], ...]:
    return tuple((str(item), int(amount)) for item, amount in raw)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _limits(raw: Mapping[str, Any]) -> Limits:
    known = {f.name for f in fields(Limits)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise RegistryError(f"registry limits carries unknown keys: {unknown}")
    return Limits(**raw)


def load_registry(path: Path | None = None) -> Registry:
    """Read the full registry from ``data/registry.json`` (written by Task 10).

    Raises :class:`RegistryError` if the file is missing a top-level section or
    a malformed entry; the caller gets no half-built registry.
    """
    path = REGISTRY_PATH if path is None else Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegistryError(f"no registry at {path}") from None
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry at {path} is not JSON: {exc}") from exc
    return Registry(
        provenance=dict(_require(data, "provenance")),
        buildables=_buildables(_require(data, "buildables")),
        recipes=_recipes(_require(data, "recipes")),
        descriptors=dict(_require(data, "descriptors")),
        build_recipes=dict(_require(data, "build_recipes")),
        limits=_limits(_require(data, "limits")),
    )
