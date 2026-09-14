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
    "LIMIT_SOURCES",
    "PIPE_BEND_RADIUS_2D_CM",
    "PIPE_MAX_SPLINE_CM",
    "PIPE_MIN_BEND_RADIUS_CM",
    "PORT_DIRECTION_SOURCES",
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

# Where a limit's value came from:
#
# ``assets``          a cooked asset states it (a hologram Blueprint's override)
# ``binary``          a constructor immediate ``tools/sfy-native`` read out of the
#                     shipped DLL through its PDB
# ``binary-derived``  a formula the DLL's machine code applies, evaluated here
#                     against a game-data input; ``provenance`` carries both
# ``docs``            the game's own Docs.json class-default dump
# ``header``          an in-class initialiser in ``CommunityResources/Headers.zip``
# ``constant``        not a game value at all, but a constant this project chose;
#                     ``provenance`` carries the reason
#
# There is deliberately no source for "what a blueprint corpus contains". A
# community blueprint can carry clipped geometry, a hacked save or an older game
# version, so what one holds is not a fact about the game: it is not a source,
# not a cross-check and not evidence, here or anywhere else in the registry.
# What the game *refuses* is in :mod:`flab2bp.sfy.rules`, and
# ``provenance["limits"][key]["enforced_by"]`` names the rule for each limit.
LIMIT_SOURCES = (
    "assets",
    "binary",
    "binary-derived",
    "constant",
    "docs",
    "header",
)

# Where a port's direction came from. A cooked asset omits ``mDirection``
# whenever it equals the component **archetype**'s value, so an absent property
# means "whatever my archetype has" rather than the enum's zero, and the chain of
# archetypes runs from the buildable's own Blueprint up to a native constructor
# the pak does not carry. ``tools/sfy-extract`` walks all of it:
#
# ``asset``            the buildable's own Blueprint states the direction
# ``asset-inherited``  a parent Blueprint's template of the same name states it
# ``native``           the archetype's C++ constructor does, read out of the
#                      shipped DLL; ``provenance["assets"]
#                      ["native_direction_defaults"]`` carries the class, the
#                      member, the value and the instructions it was read at
# ``unknown``          none of the three answered, and the port ships saying so
#
# There is deliberately no source for "what a blueprint corpus wires this port
# to" and none for "what the component is called". A corpus says what somebody
# once built, which is not a fact about the game, and a naming convention is not
# something the game reads. A port whose direction is ``"unknown"`` is one no
# caller may route to.
PORT_DIRECTION_SOURCES = ("asset", "asset-inherited", "native", "unknown")

_HEADER_DEFAULTED = (
    "belt_max_spline_cm",
    "pipe_max_spline_cm",
    "pipe_bend_radius_2d_cm",
    "pipe_min_bend_radius_cm",
)

DATA_DIR = Path(__file__).resolve().parent / "data"
REGISTRY_PATH = DATA_DIR / "registry.json"

Vector = tuple[float, float, float]

_ZERO: Vector = (0.0, 0.0, 0.0)
_ONE: Vector = (1.0, 1.0, 1.0)


class RegistryError(ValueError):
    """A registry payload was missing a section or carried an unknown key."""


@dataclass(frozen=True, slots=True)
class ClearanceBox:
    """One entry of a buildable's ``mClearanceData``.

    ``min``/``max`` are the box corners in the box's own frame, which the
    ``RelativeTransform`` places on the buildable: ``translation`` offsets it,
    ``rotation`` turns it and ``scale`` stretches it. Reading ``min``/``max`` as
    if they were axis-aligned on the buildable is wrong for the 37 boxes that
    carry a rotation -- a barrier's box is a quarter turn round, and a beam's
    two quarter turns -- so a collision test has to apply all three.

    ``rotation`` is an Unreal rotator in degrees, ``(pitch, yaw, roll)``, which
    is how :attr:`Port.rotation` is spelled as well; Docs.json exports it as a
    quaternion and :func:`flab2bp.sfy.docs.parse_clearance` converts it.

    ``soft`` marks ``CT_Soft`` boxes, which block placement but let belts and
    pipes pass. ``exclude_for_snapping`` marks boxes the game ignores when it
    snaps a hologram to a neighbour, so a snap may legitimately overlap one.
    """

    min: Vector
    max: Vector
    soft: bool
    translation: Vector
    rotation: Vector = _ZERO
    scale: Vector = _ONE
    exclude_for_snapping: bool = False


@dataclass(frozen=True, slots=True)
class Port:
    """A belt, pipe or power connection on a buildable, in its local frame.

    ``direction_source`` says where in the game ``direction`` was read, because
    the cooked asset only answers for the ports that spell the property out; see
    :data:`PORT_DIRECTION_SOURCES`. ``direction`` is ``"unknown"`` exactly when
    the source is, which means no part of the game gave this port a direction:
    a caller must refuse to route to it rather than assume one.

    ``max_connections`` is how many wires may end on a power connection, from
    ``FGCircuitConnectionComponent::mMaxNumConnectionLinks``. It is ``None`` on
    every belt and pipe port, which have no such property, and on a power port
    whose Blueprint does not override the native default -- 53 of the 74 power
    ports in the content, all of them machine power inputs. The three pole marks
    say 4, 7 and 10, and their wall variants say the same.
    """

    name: str
    kind: str  # "belt" | "pipe" | "power"
    direction: str  # "input" | "output" | "any" | "snap_only" | "unknown"
    direction_source: str  # one of PORT_DIRECTION_SOURCES
    translation: Vector
    rotation: Vector
    clearance: float | None
    max_connections: int | None = None


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
    # This class's hologram overrides ``mGridSnapSize``; ``None`` means it uses
    # the global :attr:`Limits.hologram_grid_cm`. Only power poles, power towers
    # and street lights override it, all to 50.
    grid_snap_cm: float | None = None
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
    other field stays ``None`` until the merge fills it from the game assets or
    from the measured envelope. :attr:`Registry.limits_sources` says which of
    the three each field in a loaded registry came from.
    """

    belt_max_spline_cm: float = BELT_MAX_SPLINE_CM
    # AFGConveyorBeltHologram::mBendRadius, read from the shipped DLL (source
    # ``binary``): the radius the hologram lays its own arcs on when it
    # auto-routes a belt. **It is not the tightest turn the game accepts.**
    # ``AFGConveyorBeltHologram::ValidateCurvature`` refuses a horizontal radius
    # of curvature below ``mBendRadius * 1.5 - 15`` -- 283.5 cm at this field's
    # 199.0 -- and only checks it at all in the curve build mode. Use this as
    # the radius to lay turns on and the ``belt.curvature`` rule in
    # :mod:`flab2bp.sfy.rules` as the bound.
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
    # The full asset path of every item descriptor a recipe names and of every
    # recipe, e.g. ``Desc_IronPlate_C`` ->
    # ``/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C``.
    # A blueprint's cost list, a machine's inventory filter and its
    # ``mCurrentRecipe`` all name the class that way, and the path is not
    # derivable from the class name -- the folder is not in it. Docs.json states
    # these paths and ``tools/sfy-extract`` collects them.
    item_paths: dict[str, str]
    recipe_paths: dict[str, str]
    limits: Limits
    limits_sources: dict[str, str]  # Limits field -> one of LIMIT_SOURCES

    @classmethod
    def from_docs_only(cls, data: Mapping[str, Any]) -> Registry:
        """Build a registry from a ``docs.json`` payload alone.

        Ports are empty and the limits are the header defaults, because neither
        can be read out of Docs.json; :func:`load_registry` supplies both. Only
        the four header defaults have a source here -- the rest are still
        ``None``, and a ``None`` has no provenance to report.
        """
        return cls(
            provenance=dict(_require(data, "provenance")),
            buildables=_buildables(_require(data, "buildables")),
            recipes=_recipes(_require(data, "recipes")),
            descriptors=dict(_require(data, "descriptors")),
            build_recipes=dict(_require(data, "build_recipes")),
            item_paths={},
            recipe_paths={},
            limits=Limits(),
            limits_sources=dict.fromkeys(_HEADER_DEFAULTED, "header"),
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
            rotation=_vector(box["rotation"]),
            scale=_vector(box["scale"]),
            exclude_for_snapping=bool(box["exclude_for_snapping"]),
        )
        for box in raw
    )


def _ports(raw: Iterable[Mapping[str, Any]]) -> tuple[Port, ...]:
    """Read a buildable's ports, refusing a direction no game source backs.

    ``direction_source`` is a claim about where in the game a direction was
    read, so a value outside :data:`PORT_DIRECTION_SOURCES` -- the ``"corpus"``
    and ``"name"`` a Milestone 1 registry carried, say -- is refused rather than
    loaded. So is a direction and a source that disagree about being unknown.
    """
    ports = tuple(
        Port(
            name=str(port["name"]),
            kind=str(port["kind"]),
            direction=str(port["direction"]),
            direction_source=str(port["direction_source"]),
            translation=_vector(port["translation"]),
            rotation=_vector(port["rotation"]),
            clearance=None if port.get("clearance") is None else float(port["clearance"]),
            max_connections=_opt_int(port.get("max_connections")),
        )
        for port in raw
    )
    wrong = [
        f"{p.name}: direction {p.direction!r} from {p.direction_source!r}"
        for p in ports
        if p.direction_source not in PORT_DIRECTION_SOURCES
        or (p.direction == "unknown") != (p.direction_source == "unknown")
    ]
    if wrong:
        raise RegistryError(f"port directions come from no game source: {wrong}")
    return ports


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
                grid_snap_cm=_opt_float(entry.get("grid_snap_cm")),
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


def _paths(raw: Mapping[str, Any], section: str) -> dict[str, str]:
    """Check that every asset path names the class it is keyed by.

    An Unreal class path ends ``<package>.<ClassName>``, so a path whose tail is
    not the key is a mismatched entry and would put the wrong asset into a
    blueprint, where it fails only in the game.
    """
    wrong = sorted(k for k, v in raw.items() if not str(v).endswith("." + k))
    if wrong:
        raise RegistryError(f"registry {section} has paths that name another class: {wrong}")
    return {str(k): str(v) for k, v in raw.items()}


def _limits(raw: Mapping[str, Any]) -> Limits:
    unknown = sorted(set(raw) - {f.name for f in fields(Limits)})
    if unknown:
        raise RegistryError(f"registry limits carries unknown keys: {unknown}")
    return Limits(**raw)


def _limits_sources(raw: Mapping[str, Any], limits: Limits) -> dict[str, str]:
    """Check that every limit that has a value says where the value came from."""
    unknown = sorted(set(raw) - {f.name for f in fields(Limits)})
    if unknown:
        raise RegistryError(f"registry limits_sources names unknown limits: {unknown}")
    bad = sorted(k for k, v in raw.items() if v not in LIMIT_SOURCES)
    if bad:
        raise RegistryError(f"registry limits_sources has an unknown source for: {bad}")
    filled = {f.name for f in fields(Limits) if getattr(limits, f.name) not in (None, {})}
    missing = sorted(filled - set(raw))
    if missing:
        raise RegistryError(
            f"registry limits_sources does not say where these came from: {missing}"
        )
    return {str(k): str(v) for k, v in raw.items()}


def _enforcement(provenance: Mapping[str, Any], limits: Limits) -> None:
    """Check that every limit says what turns it away, or why nothing does.

    A number with no ``enforced_by`` is a value somebody found in the game's
    data, not a limit. ``scripts/sfy_registry.py`` fills these from its
    ``ENFORCED_BY`` and ``NOT_ENFORCED`` tables and holds the rule ids against
    ``data/hologram_rules.json``; this is the same claim, checked on load.
    """
    entries = provenance.get("limits", {})
    silent = sorted(
        f.name
        for f in fields(Limits)
        if not (entry := entries.get(f.name, {})).get("enforced_by")
        and not entry.get("reason")
    )
    if silent:
        raise RegistryError(f"registry limits do not say what enforces them: {silent}")


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
    limits = _limits(_require(data, "limits"))
    sources = _limits_sources(_require(data, "limits_sources"), limits)
    provenance = dict(_require(data, "provenance"))
    _enforcement(provenance, limits)
    return Registry(
        provenance=provenance,
        buildables=_buildables(_require(data, "buildables")),
        recipes=_recipes(_require(data, "recipes")),
        descriptors=dict(_require(data, "descriptors")),
        build_recipes=dict(_require(data, "build_recipes")),
        item_paths=_paths(_require(data, "item_paths"), "item_paths"),
        recipe_paths=_paths(_require(data, "recipe_paths"), "recipe_paths"),
        limits=limits,
        limits_sources=sources,
    )
