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

from flab2bp.sfy.rules import RULE_EFFECTS, RULE_STATUSES

__all__ = [
    "BELT_MAX_SPLINE_CM",
    "COST_SEGMENT_SOURCES",
    "FLOW_NAME_SOURCES",
    "FLOW_SOURCES",
    "LIMIT_SOURCES",
    "MAX_CONNECTIONS_SOURCES",
    "MESH_BOUNDS_SOURCES",
    "PIPE_BEND_RADIUS_2D_CM",
    "PIPE_MAX_SPLINE_CM",
    "PIPE_MIN_BEND_RADIUS_CM",
    "PORT_DIRECTION_SOURCES",
    "Buildable",
    "ClearanceBox",
    "ConveyorFlow",
    "Limits",
    "Port",
    "Recipe",
    "Registry",
    "RegistryError",
    "load_registry",
]

# Defaults transcribed from the game's public hologram headers. The merge script
# (``scripts/sfy_registry.py``) reads the rest from the install and overrides
# these in ``registry.json``.
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
# What the game *does* with each number is in :mod:`flab2bp.sfy.rules`, and
# ``provenance["limits"][key]["governed_by"]`` names the rule for each limit and
# copies that rule's effect, so a clamp or a snap is never read as a refusal.
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

# Where a power port's ``max_connections`` came from. The same four links as a
# direction, for the same reason: ``mMaxNumConnectionLinks`` is a UPROPERTY on
# ``UFGCircuitConnectionComponent``, a cooked asset omits it wherever it equals
# the archetype's value, and the archetype at the end of the chain is that
# class's C++ constructor.
#
# ``asset``            this buildable's own Blueprint states the count
# ``asset-inherited``  a parent Blueprint's template of the same name states it
# ``native``           none of the assets do, so it is the constructor's, read
#                      out of the shipped DLL into ``data/native.json``
# ``unknown``          the port has no such property at all, which is every belt
#                      and pipe connection
#
# A port's name is not a source here either: a connection called ``PowerInput``
# is not thereby a connection that takes one wire.
MAX_CONNECTIONS_SOURCES = ("asset", "asset-inherited", "native", "unknown")

# Where a buildable's ``mesh_bounds_cm`` came from. There is one entry, and it is
# the cooked ``UStaticMesh``'s own ``RenderData.Bounds``:
#
# ``assets``  the mesh the class's ``mMesh`` (belt) or ``mMidMesh`` (lift)
#             UPROPERTY points at, read through CUE4Parse;
#             ``provenance["mesh_bounds"]`` names the property and the asset path
#
# A box measured off a blueprint is not a mesh bound, and Docs.json states no
# mesh geometry at all -- only ``mMeshLength``/``mMeshHeight``, which are the
# repeat pitch and not the cross-section.
MESH_BOUNDS_SOURCES = ("assets",)

# Where a conveyor's item-flow order was read. Both of a conveyor's connections
# are ``FCD_ANY``, so ``mDirection`` does not say which end items enter by, and
# that is a fact a router needs:
#
# ``header``  the declaration comment in ``CommunityResources/Headers.zip``
#             (``Buildables/FGBuildableConveyorBase.h:380``) states the order
# ``native``  the shipped DLL's machine code makes it explicit as well, and
#             ``provenance["conveyor_flow"]`` carries the functions, their RVAs
#             and the instructions
#
# There is no third source. A blueprint corpus says what somebody once wired up,
# which is not a fact about the game.
FLOW_SOURCES = ("header", "native")

# Where the *names* of the two ends came from. The order above is about the two
# C++ members; which component sits in each member is a different fact, and the
# only place the game states it is the cooked class default object, whose
# ``mConnection0`` object property refers to one of its own subobject exports.
# A port name is otherwise a convention and never evidence, which is why there is
# no second entry here.
FLOW_NAME_SOURCES = ("asset",)

# Where a buildable's cost segment came from. The game's own Docs.json states
# it as a class default -- ``mMeshLength`` on a conveyor belt, ``mMeshHeight``
# on a lift -- and ``provenance["cost_segment"]`` names the property per native
# class beside the ``belt.cost`` rule whose machine code reads it. There is no
# second entry: this number is never measured off a blueprint's cost list.
COST_SEGMENT_SOURCES = ("docs",)

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
    ``FGCircuitConnectionComponent::mMaxNumConnectionLinks``, and
    ``max_connections_source`` says where it was read -- see
    :data:`MAX_CONNECTIONS_SOURCES`. It is ``None`` (source ``"unknown"``) on
    every belt and pipe port, which have no such property. The three pole marks
    state 4, 7 and 10 in their own Blueprints, and their wall variants the same;
    a machine's power input states nothing anywhere in its asset chain and takes
    the native constructor's **1**, which is the value the shipped game runs on
    and not a number this project chose.
    """

    name: str
    kind: str  # "belt" | "pipe" | "power"
    direction: str  # "input" | "output" | "any" | "snap_only" | "unknown"
    direction_source: str  # one of PORT_DIRECTION_SOURCES
    translation: Vector
    rotation: Vector
    clearance: float | None
    max_connections: int | None = None
    max_connections_source: str = "unknown"  # one of MAX_CONNECTIONS_SOURCES


@dataclass(frozen=True, slots=True)
class ConveyorFlow:
    """Which end of a conveyor items enter by, and which they leave by.

    ``entry`` and ``exit`` are port names on the same buildable. This is not
    :attr:`Port.direction`: a conveyor's two connections are both ``FCD_ANY``
    (the constructor sets them so, and the hologram assigns the pair's
    directions from whatever the belt snapped to), so the direction says nothing
    about which way items travel along the belt itself. ``source`` is where the
    order over the two C++ members was read -- see :data:`FLOW_SOURCES` -- and
    ``name_source`` where the pairing of each member with a *named* port was
    read, which is a separate fact: see :data:`FLOW_NAME_SOURCES`.
    """

    entry: str
    exit: str
    source: str
    name_source: str


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
    mesh_length_cm: float | None
    width_cm: float | None
    depth_cm: float | None
    height_cm: float | None
    designer_dims: tuple[int, int, int] | None
    max_potential: float | None
    min_potential: float | None
    # ``mPotentialShardSlots`` / ``mProductionShardSlotSize`` as the class default
    # object states them, **which is not what the buildable runs on unless the
    # matching ``*_override`` flag is true**: ``AFGBuildableFactory::BeginPlay``
    # copies :attr:`Limits.potential_shard_slots_default` and
    # :attr:`Limits.production_boost_slots_default` over them otherwise. Every
    # shipped class leaves ``potential_shard_slots_override`` false, so the three
    # overclock slots are the subsystem's; the somersloop slot size is overridden
    # by the Assembler, the Foundry and the Manufacturer and nothing else. The
    # ``factory.potential`` rule quotes the two branches.
    potential_shard_slots: int | None
    production_boost_slots: int | None
    potential_shard_slots_override: bool | None
    production_boost_slots_override: bool | None
    # ``mBaseProductionBoost`` and ``mProductionShardBoostMultiplier``: the
    # output multiplier with no somersloop in, and what one somersloop adds to
    # it once :attr:`Limits.production_boost_per_slot` is scaled by it. See the
    # ``manufacturer.production_boost`` rule.
    base_production_boost: float | None
    production_boost_multiplier: float | None
    # ``mPowerConsumptionExponent`` and
    # ``mProductionBoostPowerConsumptionExponent``, per class: the game states no
    # global exponent, which is why neither is a limit. 1.321929 on every
    # manufacturer and extractor and 1.6 on everything else; the boost exponent
    # is 2.0 on all 62 classes that carry one. ``factory.potential`` quotes the
    # ``powf`` both feed.
    power_exponent: float | None
    production_boost_power_exponent: float | None
    # The local-space axis-aligned box of the static mesh a spline buildable
    # repeats along itself, as ``(min, max)`` in centimetres:
    # ``Origin - BoxExtent`` and ``Origin + BoxExtent`` of the cooked
    # ``UStaticMesh``'s ``RenderData.Bounds``. ``None`` on every class whose
    # class default object names no such mesh, which is everything but the belt
    # and lift marks, the pipelines, the hypertube, the railway and the three
    # foundation passthroughs. ``mesh_bounds_property`` names the UPROPERTY that
    # was read (``mMesh`` on a belt, ``mMidMesh`` on a lift) and
    # ``provenance["mesh_bounds"]`` carries the asset path per class.
    #
    # **This is the mesh, not the clearance.** What the game refuses a placement
    # over is the box ``AFGBuildableConveyorBelt::CreateClearanceData`` lays
    # along the spline, which the ``belt.clearance`` rule states and which is
    # narrower than the Mk1 mesh.
    mesh_bounds_cm: tuple[Vector, Vector] | None
    mesh_bounds_property: str | None
    # This class's hologram overrides ``mGridSnapSize``; ``None`` means it uses
    # the global :attr:`Limits.hologram_grid_cm`. Only power poles, power towers
    # and street lights override it, all to 50.
    grid_snap_cm: float | None = None
    ports: tuple[Port, ...] = ()
    # The item-flow order of the two conveyor ends, on the belt and lift marks
    # and on nothing else. ``None`` means this class carries no such order.
    flow: ConveyorFlow | None = None
    # How much of a spline buildable one unit of its build recipe pays for, and
    # where that number was read -- see :data:`COST_SEGMENT_SOURCES`. ``None``
    # for everything the game charges its recipe exactly once, which is
    # everything that is not costed by length. The ``belt.cost`` rule in
    # ``data/hologram_rules.json`` is the machine code that divides by it.
    length_per_cost_cm: float | None = None
    length_per_cost_source: str | None = None


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
    other field stays ``None`` until the merge fills it from a game source --
    the cooked assets, the shipped binary, a formula read out of that binary,
    Docs.json, a header, or this project's own stated constant.
    :attr:`Registry.limits_sources` says which of those six
    (:data:`LIMIT_SOURCES`) each field in a loaded registry came from. There is
    no "measured envelope": nothing here is measured off a blueprint corpus.
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
    # The floor ``AFGConveyorBeltHologram::ValidateMinLength`` compares a belt's
    # polyline against. It is not a constructor immediate: the instruction is
    # ``mulss xmm7, 0.5001`` against the belt mark's own ``mMeshLength``
    # (0xaa58b2), so this is that product -- source ``binary-derived``, the same
    # standing as the three lift heights, with the multiplier, the mesh length
    # and the instruction in ``provenance["limits"]["belt_min_length_cm"]``. The
    # comparison is strict, so a belt of exactly this length is still too short.
    belt_min_length_cm: float | None = None
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
    # What one power shard in a potential slot unlocks, and what one somersloop
    # in a production-boost slot is worth before the class's own
    # :attr:`Buildable.production_boost_multiplier` scales it. Both are the
    # shard descriptor's own ``mExtraPotential``/``mExtraProductionBoost``, which
    # ``UFGPowerShardDescriptor::GetBoostValue`` returns and
    # ``AFGBuildableFactory::GetCurrentMaxPotentialForType`` adds once per shard.
    potential_per_shard: float | None = None
    production_boost_per_slot: float | None = None
    # What ``AFGBuildableFactory::BeginPlay`` puts on a buildable that does not
    # override its own slot counts, from ``AFGBuildableSubsystem``. Every shipped
    # class takes the potential one, so a machine has three overclock slots and
    # a maximum potential of ``max_potential + 3 * potential_per_shard``.
    potential_shard_slots_default: int | None = None
    production_boost_slots_default: int | None = None


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

    ``max_connections_source`` is the same claim about the wire count, checked
    the same way against :data:`MAX_CONNECTIONS_SOURCES`: a count with no source
    would be a number nothing stands behind, and a source with no count would
    name a link that answered nothing.
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
            max_connections_source=str(port.get("max_connections_source", "unknown")),
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
    unbacked = [
        f"{p.name}: max_connections {p.max_connections!r} from {p.max_connections_source!r}"
        for p in ports
        if p.max_connections_source not in MAX_CONNECTIONS_SOURCES
        or (p.max_connections is None) != (p.max_connections_source == "unknown")
    ]
    if unbacked:
        raise RegistryError(f"port connection counts come from no game source: {unbacked}")
    return ports


def _cost_segment(class_name: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """``length_per_cost_cm`` and its source, refusing a number with no source.

    A buildable costed by length carries both or neither: a length with no
    source would be a number nothing stands behind, and a source with no length
    would be a claim about a value that is not there. The source must be one of
    :data:`COST_SEGMENT_SOURCES`, and the length must be positive -- the game
    treats a segment of 1e-4 or less as "not costed by length" and charges the
    recipe once (the ``belt.cost`` rule quotes the branch).
    """
    length = _opt_float(entry.get("length_per_cost_cm"))
    source = entry.get("length_per_cost_source")
    source = None if source is None else str(source)
    if (length is None) != (source is None):
        raise RegistryError(
            f"{class_name} states a cost segment of {length!r} from {source!r}: "
            "a length and its source travel together"
        )
    if source is not None and source not in COST_SEGMENT_SOURCES:
        raise RegistryError(f"{class_name}'s cost segment comes from no game source: {source!r}")
    if length is not None and length <= 0.0:
        raise RegistryError(
            f"{class_name} states a cost segment of {length}, which is not a length"
        )
    return {"length_per_cost_cm": length, "length_per_cost_source": source}


def _mesh_bounds(class_name: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """``mesh_bounds_cm`` and the UPROPERTY it was read from, or neither.

    A box and the property that states it travel together for the same reason a
    cost segment and its source do: a box with no property named would be
    geometry nothing stands behind, and a property with no box would name a read
    that produced nothing. The box has to be a box -- every ``min`` component at
    or below its ``max`` -- because ``Origin +- BoxExtent`` cannot be otherwise
    and a pair that is would mean the two were swapped on the way here.
    """
    raw = entry.get("mesh_bounds_cm")
    prop = entry.get("mesh_bounds_property")
    prop = None if prop is None else str(prop)
    if (raw is None) != (prop is None):
        raise RegistryError(
            f"{class_name} states mesh bounds {raw!r} from {prop!r}: "
            "a box and the property it was read from travel together"
        )
    if raw is None:
        return {"mesh_bounds_cm": None, "mesh_bounds_property": None}
    low, high = _vector(raw[0]), _vector(raw[1])
    if any(a > b for a, b in zip(low, high, strict=True)):
        raise RegistryError(f"{class_name}'s mesh bounds are inside out: {low} .. {high}")
    return {"mesh_bounds_cm": (low, high), "mesh_bounds_property": prop}


def _flow(raw: Mapping[str, Any] | None, ports: tuple[Port, ...]) -> ConveyorFlow | None:
    """Read a conveyor's item-flow order, refusing one no game source backs.

    A ``source`` outside :data:`FLOW_SOURCES` or a ``name_source`` outside
    :data:`FLOW_NAME_SOURCES` is refused rather than loaded, and so is an end
    that names a port this buildable does not have or names the same port twice:
    a flow order is a claim about two of *these* ports.
    """
    if raw is None:
        return None
    flow = ConveyorFlow(
        entry=str(raw["entry"]),
        exit=str(raw["exit"]),
        source=str(raw["source"]),
        name_source=str(raw["name_source"]),
    )
    if flow.source not in FLOW_SOURCES:
        raise RegistryError(f"conveyor flow comes from no game source: {flow.source!r}")
    if flow.name_source not in FLOW_NAME_SOURCES:
        raise RegistryError(f"conveyor flow names come from no game source: {flow.name_source!r}")
    names = {port.name for port in ports}
    if flow.entry == flow.exit or not {flow.entry, flow.exit} <= names:
        raise RegistryError(
            f"conveyor flow names ends this buildable has no port for: "
            f"{flow.entry!r} -> {flow.exit!r}"
        )
    return flow


def _buildables(raw: Mapping[str, Mapping[str, Any]]) -> dict[str, Buildable]:
    out: dict[str, Buildable] = {}
    for class_name, entry in raw.items():
        try:
            dims = entry["designer_dims"]
            ports = _ports(entry.get("ports", ()))
            out[class_name] = Buildable(
                class_name=class_name,
                display_name=entry["display_name"],
                native_class=entry["native_class"],
                clearance=_clearance(entry["clearance"]),
                power_mw=float(entry["power_mw"]),
                manufacturing_speed=_opt_float(entry["manufacturing_speed"]),
                belt_speed_per_min=_opt_float(entry["belt_speed_per_min"]),
                mesh_height_cm=_opt_float(entry["mesh_height_cm"]),
                mesh_length_cm=_opt_float(entry["mesh_length_cm"]),
                width_cm=_opt_float(entry["width_cm"]),
                depth_cm=_opt_float(entry["depth_cm"]),
                height_cm=_opt_float(entry["height_cm"]),
                designer_dims=None if dims is None else (int(dims[0]), int(dims[1]), int(dims[2])),
                max_potential=_opt_float(entry["max_potential"]),
                min_potential=_opt_float(entry["min_potential"]),
                potential_shard_slots=_opt_int(entry["potential_shard_slots"]),
                production_boost_slots=_opt_int(entry["production_boost_slots"]),
                potential_shard_slots_override=_opt_bool(entry["potential_shard_slots_override"]),
                production_boost_slots_override=_opt_bool(entry["production_boost_slots_override"]),
                base_production_boost=_opt_float(entry["base_production_boost"]),
                production_boost_multiplier=_opt_float(entry["production_boost_multiplier"]),
                power_exponent=_opt_float(entry["power_exponent"]),
                production_boost_power_exponent=_opt_float(
                    entry["production_boost_power_exponent"]
                ),
                **_mesh_bounds(class_name, entry),
                **_cost_segment(class_name, entry),
                grid_snap_cm=_opt_float(entry.get("grid_snap_cm")),
                ports=ports,
                flow=_flow(entry.get("flow"), ports),
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


def _opt_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


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


def _governance(provenance: Mapping[str, Any], limits: Limits) -> None:
    """Check that every limit says which rule governs it, or why none does.

    A number with no ``governed_by`` and no ``ungoverned`` reason is a value
    somebody found in the game's data, not a limit. ``governed_by`` is
    ``{"rule": <id>, "effect": <effect>, "status": <status>}`` with both the
    effect and the status copied from the rule, and each has to be one
    :mod:`flab2bp.sfy.rules` knows -- a limit that claimed to be ``enforced`` by
    a rule that only clamps or snaps would tell a validator to refuse a
    placement the game accepts, and one whose rule is ``partial`` is a bound read
    out of a function the tool could not finish, which the reader should see
    without opening the rules file.
    ``scripts/sfy_registry.py`` fills these from its ``GOVERNED_BY`` and
    ``NOT_GOVERNED`` tables and holds both against ``data/hologram_rules.json``;
    this re-checks the shape of the claim on load, without reading the rules.
    """
    entries = provenance.get("limits", {})
    silent, malformed = [], []
    for f in fields(Limits):
        entry = entries.get(f.name, {})
        governed = entry.get("governed_by")
        if bool(governed) == bool(entry.get("ungoverned")):
            silent.append(f.name)
        elif governed is not None and (
            set(governed) != {"rule", "effect", "status"}
            or governed["effect"] not in RULE_EFFECTS
            or governed["status"] not in RULE_STATUSES
        ):
            malformed.append(f"{f.name}: {governed}")
    if silent:
        raise RegistryError(
            "registry limits must each name the rule that governs them or say why none "
            f"does, and never both: {sorted(silent)}"
        )
    if malformed:
        raise RegistryError(f"registry limits name a governing rule badly: {sorted(malformed)}")


def load_registry(path: Path | None = None) -> Registry:
    """Read the full registry from ``data/registry.json``.

    That file is generated, never hand-edited: ``scripts/sfy_registry.py`` merges
    Docs.json, the cooked assets, the shipped binary and the hologram rules into
    it, and ``docs/sfy-regenerating-game-data.md`` is the order the seven steps
    behind it run in.

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
    _governance(provenance, limits)
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
