"""Merge the game-data sources into the committed ``registry.json``.

``docs.json`` (Task 9) is the game's own Docs.json dump: buildables, recipes and
descriptors. ``assets.json`` (``tools/sfy-extract``) is what the cooked assets
add: the connection ports of every buildable, whatever limits the hologram
Blueprints override, and the full asset path of every class the game's Docs.json
states one for -- which is how a blueprint names an item descriptor or a recipe.
``native.json`` (``tools/sfy-native``) is what the shipped
DLL's machine code states for the hologram limits no asset carries, and
``native_directions.json`` (``scripts/sfy_native_directions.py``) the same for
the connection directions no asset carries -- which the extractor, not this
script, resolves the ports with.
``hologram_rules.json`` (``scripts/sfy_native_rules.py``) is what the build
gun's hologram does with those numbers. This script joins the four and writes
the registry that :func:`flab2bp.sfy.registry.load_registry` reads.

**Every limit in the registry comes from game data, and says what the game does
with it.** ``provenance.limits[key].governed_by`` names the hologram rule that
governs it and copies that rule's effect -- ``refuse``, ``clamp``, ``snap``,
``compute`` or ``none`` -- and its status -- ``extracted``, ``partial`` or
``unextractable`` -- or is ``null``, with ``ungoverned`` carrying the reason no
rule does. Only a ``refuse`` turns a placement away: a clamp or a snap moves the
hologram, and a compute only works a number out. A ``partial`` says the rule's
function was not read to the end, so what it states about the limit is part of
the account and not all of it.

**Nothing here comes from a blueprint corpus, and nothing from a port's name.**
A community blueprint can carry clipped geometry, a hacked save or an older game
version, so what one contains is not a fact about the game: it is not a source,
not a cross-check and not evidence, for a limit, for a port direction or for
anything else in the registry. What a component happens to be called is not one
either.

Run it after re-running the extractors for a new game build::

    uv run python scripts/sfy_registry.py

It prints where each limit came from, and any it could not fill at all. It
refuses to write a registry when the sources contradict each other: a header
against the binary, a port whose ``direction_source`` is not one of the four
game sources, or a limit naming a hologram rule that does not exist.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from flab2bp.sfy.registry import (
    COST_SEGMENT_SOURCES,
    FLOW_NAME_SOURCES,
    FLOW_SOURCES,
    LIFT_GEOMETRY_FIELDS,
    LIFT_GEOMETRY_SOURCES,
    LIFT_NATIVE_CLASS,
    PORT_DIRECTION_SOURCES,
    Limits,
)
from flab2bp.sfy.rules import HologramRule, load_rules

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "flab2bp" / "sfy" / "data"

# Limits the game's C++ headers state outright, which ship as
# ``CommunityResources/Headers.zip``. Every one of these is a UPROPERTY with an
# in-class initialiser, so the value is right there in the header.
#
# The binary states all four as well, and ``_check_headers`` holds the two to
# each other: the header is how we know what ``tools/sfy-native`` is supposed
# to come back with, so a disagreement means the extraction is wrong and the
# merge refuses to write a registry. The binary is the source that wins, because
# it is the number the shipped game actually runs on.
HEADER_DEFAULTS: dict[str, float] = {
    # Source/FactoryGame/Public/Hologram/FGConveyorBeltHologram.h:174
    "belt_max_spline_cm": 5600.1,
    # Source/FactoryGame/Public/Hologram/FGPipelineHologram.h:206
    "pipe_max_spline_cm": 5600.1,
    # Source/FactoryGame/Public/Hologram/FGPipelineHologram.h:198
    "pipe_bend_radius_2d_cm": 199.0,
    # Source/FactoryGame/Public/Hologram/FGPipelineHologram.h:202
    "pipe_min_bend_radius_cm": 75.0,
}

# The hologram member each limit is read from in ``native.json``. These are the
# values that exist in no cooked asset and in no header: they are constructor
# immediates, and ``tools/sfy-native`` reads them out of the shipped DLL's
# machine code. A member ``native.json`` reports as ``null`` -- because the game
# computes it at run time rather than storing a constant -- falls through to
# ``LIFT_HEIGHT_FORMULAS`` below, which applies the formula the same machine
# code shows; ``registry.json``'s ``limits_sources`` says which happened.
NATIVE_LIMITS: dict[str, tuple[str, str]] = {
    "belt_bend_radius_cm": ("AFGConveyorBeltHologram", "mBendRadius"),
    "belt_max_incline_deg": ("AFGConveyorBeltHologram", "mMaxIncline"),
    "belt_max_spline_cm": ("AFGConveyorBeltHologram", "mMaxSplineLength"),
    "lift_step_cm": ("AFGConveyorLiftHologram", "mStepHeight"),
    "lift_min_cm": ("AFGConveyorLiftHologram", "mMinimumHeight"),
    "lift_max_cm": ("AFGConveyorLiftHologram", "mMaximumHeight"),
    "lift_min_vertical_cm": (
        "AFGConveyorLiftHologram",
        "mMinimumHeightWithVerticalConnection",
    ),
    "pipe_bend_radius_cm": ("AFGPipelineHologram", "mBendRadius"),
    "pipe_bend_radius_2d_cm": ("AFGPipelineHologram", "mBendRadius2D"),
    "pipe_min_bend_radius_cm": ("AFGPipelineHologram", "mMinBendRadius"),
    "pipe_max_spline_cm": ("AFGPipelineHologram", "mMaxSplineLength"),
    "hologram_grid_cm": ("AFGBuildableHologram", "mGridSnapSize"),
    # Not hologram members: what AFGBuildableFactory::BeginPlay copies onto a
    # buildable that does not override its own shard slot counts (0x4d40eb and
    # 0x4d40fc -- the factory.potential rule). The cooked Blueprint subclass of
    # the subsystem may override either, and ``assets.json`` carries that, so
    # these are the fallback exactly as ``hologram_grid_cm`` is.
    "potential_shard_slots_default": ("AFGBuildableSubsystem", "mDefaultPotentialShardSlots"),
    "production_boost_slots_default": (
        "AFGBuildableSubsystem",
        "mDefaultProductionShardSlotSize",
    ),
}

#: The limits above that are ``int32`` members rather than floats.
INT_LIMITS = frozenset({"potential_shard_slots_default", "production_boost_slots_default"})

# The three the binary states no constant for. ``AFGConveyorLiftHologram``
# works all three out in ``BeginPlay`` from the lift buildable's own mesh height
# H, so there is no immediate in the DLL to read -- but the *formula* is in the
# machine code, and ``native.json`` carries the disassembly of each computing
# store. Task 13 decoded it at ``0xa64e70``:
#
#     movss xmm1,[rax+760h]   ; H = the lift buildable's mesh height
#     subss xmm0,[12B8F00h]   ;   constant 50.0f
#     movss [rbx+8ECh],xmm0   ; mMinimumHeightWithVerticalConnection = H - 50
#     mulss xmm1,[1329DE8h]   ;   constant 24.0f
#     movss [rbx+8E8h],xmm1   ; mMaximumHeight = H * 24
#     movss [rbx+8E4h],xmm0   ; mMinimumHeight = H * 2
#
# H is Docs.json's ``mMeshHeight`` on the lift buildable, which this script
# reads and applies. That makes these ``"binary-derived"``: the formula is the
# game's, the input is the game's, and the arithmetic is ours.
LIFT_HEIGHT_FORMULAS: dict[str, tuple[str, float, float]] = {
    # key: (formula, multiplier, offset) -- value = multiplier * H + offset
    "lift_min_cm": ("2 * H", 2.0, 0.0),
    "lift_max_cm": ("24 * H", 24.0, 0.0),
    "lift_min_vertical_cm": ("H - 50", 1.0, -50.0),
}
LIFT_CLASS_PREFIX = "Build_ConveyorLift"
BELT_CLASS_PREFIX = "Build_ConveyorBelt"

# The belt's minimum length is the same shape as the three lift heights: a
# formula the machine code applies to a Docs.json input rather than a constant
# anything stores. ``AFGConveyorBeltHologram::ValidateMinLength`` compares the
# belt's polyline against the product of the belt mark's own ``mMeshLength`` and
# one ``.rdata`` float::
#
#     0xaa589b  movss xmm7,[rsi+8A0h]      ; mMeshLength, cached in BeginPlay
#     0xaa58b2  mulss xmm7,[132F848h]      ;   constant 0.5001f
#     0xaa59ed  comiss xmm8,xmm7           ; total > that -> long enough
#
# so the floor follows the mark being placed. All six belt marks ship 200 cm, and
# ``_belt_mesh_length`` refuses rather than pick one if that stops being true.
# ``_check_belt_min_length`` holds the 0.5001 below to the ``belt.min_length``
# rule's own evidence, so a game update that changes the multiplier fails the
# merge instead of shipping a stale floor.
BELT_MIN_LENGTH_FACTOR = 0.5001
BELT_MIN_LENGTH_EVIDENCE = "0xaa58b2"
BELT_MIN_LENGTH_RULE = "belt.min_length"

# A lift's clearance box is the third of that shape, and the input is neither a
# constructor immediate nor a Docs.json value: it is a module global's
# *initialiser*, read out of the shipped image by its PDB symbol.
# ``AFGBuildableConveyorLift::FitClearance`` reads two doubles from
# ``AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D`` and adds ``-5`` to each, and
# ``scripts/sfy_native_rules.py`` puts the tool's own read of it -- section,
# RVA, bytes, doubles and the shrunk values -- in the ``lift.clearance`` rule's
# ``data_reads``. This takes it from there rather than transcribing a number,
# which is why the merge reads the rules file raw as well as through
# :func:`load_rules`: :class:`HologramRule` carries a rule's claims, and this is
# a reading attached to one.
#
# The two components must agree. A lift's footprint is a square column in the
# shipped build, and the registry says so with one number; a build where the two
# axes differ needs two fields and the placer needs to know which is which, so
# the merge stops rather than pick one.
LIFT_CLEARANCE_RULE = "lift.clearance"
LIFT_CLEARANCE_SYMBOL = "AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D"

# The one source a port *name* may come from. ``flab2bp.sfy.registry`` re-checks
# it on load: which component sits in ``mConnection0`` is stated by the cooked
# class default object and nowhere else, and what a component happens to be
# called is otherwise a convention rather than a fact about the game.
FLOW_NAME_SOURCE = "asset"
assert FLOW_NAME_SOURCE in FLOW_NAME_SOURCES

# How much of a spline buildable one unit of its build recipe pays for, per
# native class, and which Docs.json class default states it.
#
# The conveyor refund methods pass mMeshLength/mMeshHeight to
# AFGBuildable::GetCostMultiplierForLength; belt.cost quotes that helper.
# Beams instead state mLengthPerCost directly in Docs, normalized by the Docs
# extractor with their own provenance. Pipes pass twice mMeshLength to the
# same helper, as the native evidence below shows. Neither pipe nor beam cost
# semantics are inferred from conveyor meshes.
COST_SEGMENT_PROPERTY: dict[str, tuple[str, str]] = {
    # native class -> (the docs.json key, the game's own property name)
    "FGBuildableConveyorBelt": ("mesh_length_cm", "mMeshLength"),
    "FGBuildableConveyorLift": ("mesh_height_cm", "mMeshHeight"),
}
COST_SEGMENT_SOURCE = "docs"
COST_SEGMENT_RULE = "belt.cost"
assert COST_SEGMENT_SOURCE in COST_SEGMENT_SOURCES

# AFGBuildablePipeBase::GetDismantleRefundReturnsMultiplier, all 25 bytes,
# from the same shipped DLL/PDB as native.json. The addss doubles the Docs
# mMeshLength before the tail call to the helper quoted by belt.cost.
PIPE_COST_LENGTH_MULTIPLIER = 2.0
PIPE_COST_EVIDENCE = {
    "source": "binary-derived",
    "dll_sha256": "65fc5a6bde6d44782da387976b144a41a6b94e4fa78ec6e1bc49c3dad2d47513",
    "property": "mMeshLength",
    "unit": "cm",
    "formula": "mMeshLength * PIPE_COST_LENGTH_MULTIPLIER",
    "multiplier": PIPE_COST_LENGTH_MULTIPLIER,
    "header": "Buildables/FGBuildablePipeBase.h",
    "constant": {
        "symbol": "AFGBuildablePipeBase::PIPE_COST_LENGTH_MULTIPLIER",
        "rva": "0x12bd54c",
        "section": ".rdata",
        "hex": "00000040",
        "float": PIPE_COST_LENGTH_MULTIPLIER,
    },
    "function": {
        "symbol": "AFGBuildablePipeBase::GetDismantleRefundReturnsMultiplier",
        "rva": "0x534620",
        "size": 25,
        "size_source": "pdb-procedure-length",
        "instructions": [
            "0x534620: f30f108938060000 movss xmm1,dword ptr [rcx+638h] ; mMeshLength",
            "0x534628: f30f108140060000 movss xmm0,dword ptr [rcx+640h] ; mLength",
            "0x534630: f30f58c9 addss xmm1,xmm1",
            "0x534634: e97725f7ff jmp 00000000004A6BB0h",
        ],
    },
    "cost_helper_rule": COST_SEGMENT_RULE,
    "cost_helper_rva": "0x4a6bb0",
}

# Where a conveyor lift's two ends sit once the game has placed them.
#
# A lift's cooked class default object puts both connection components at the
# actor origin with no rotation, so the registry's two ports say nothing about
# where a lift's ends are. ``AFGBuildableConveyorLift::SetupConnections`` is
# where they go, and the ``lift.connectors`` rule in
# ``data/hologram_rules.json`` quotes all of it:
#
#     0x505b0d  mov byte ptr [rax+258h],0   ; mConnection0 = FCD_INPUT
#     0x505b1b  mov byte ptr [rax+258h],1   ; mConnection1 = FCD_OUTPUT
#     0x505b96  call 0x4f9850               ;   with FTransform::Identity
#     0x505ba8  call 0x4f9850               ;   with mTopTransform
#     0x505e7e  mConnection0->SetRelativeTransform(the first)
#     0x506155  mConnection1->SetRelativeTransform(the second)
#
# and 0x4f9850 moves its input along its own forward by
# ``CONNECTION_RELATIVE_FORWARD``, which the header declares ``0.f`` and
# SetupConnections passes as a zeroed xmm2 (0x505b03, 0x505b9b). So the bottom
# is the actor transform itself and the top is ``mTopTransform``, whose
# translation ``AFGConveyorLiftHologram::UpdateTopTransform`` builds as the
# clamped height times ``FVector::UpVector`` (0xaa49a7 names the import,
# 0xaa4a0c..0xaa4a14 multiply). ``lift.top_yaw`` quotes the yaw, which comes
# out of ``ApplyScrollRotationTo(mFirstStepYaw)`` on a rotation step of 90
# (0xa7c1a2) from the second placement point onward.
#
# ``reversed_swaps_flow`` is false and that is a reading, not an assumption:
# SetupConnections is 1950 bytes over four chained ``.pdata`` chunks, all of
# them read, and it never touches ``mIsReversed`` at +810h. The header says the
# same outright, which is why this one field's source is the header.
LIFT_GEOMETRY: dict[str, tuple[Any, str]] = {
    # field -> (value, where it was read)
    "bottom_offset": ([0.0, 0.0, 0.0], "native"),
    "bottom_facing": ([1.0, 0.0, 0.0], "native"),
    "top_offset_axis": ([0.0, 0.0, 1.0], "native"),
    "top_yaw_free": (True, "native"),
    "top_yaw_step_deg": (90.0, "native"),
    "reversed_swaps_flow": (False, "header"),
}
assert tuple(LIFT_GEOMETRY) == LIFT_GEOMETRY_FIELDS
assert all(source in LIFT_GEOMETRY_SOURCES for _value, source in LIFT_GEOMETRY.values())

# The two rules the geometry above was read out of, and one instruction from
# each that has to still be there. A game update that moves either fails the
# merge here rather than shipping geometry read from a function that has gone.
LIFT_GEOMETRY_RULES: dict[str, tuple[str, str]] = {
    # rule id -> (an evidence address it must carry, what that address is)
    "lift.connectors": ("0x505b0d", "mConnection0's direction, set to FCD_INPUT"),
    "lift.top_yaw": ("0xa7c1a2", "the lift hologram's rotation step of 90 degrees"),
}
LIFT_GEOMETRY_HEADER = "Buildables/FGBuildableConveyorLift.h:269"
LIFT_GEOMETRY_HEADER_TEXT = (
    "DEPRECATED 2023-01-30 / Instead build lifts where mConnector0 is always input, "
    "and the other always output."
)

# Binary values that are not what their name suggests, with the caveat recorded
# in ``registry.json``'s provenance next to the number. ``mBendRadius`` is two
# things at once, and neither of them is "the tightest turn the game accepts":
# ``AFGConveyorBeltHologram::AutoRouteSpline`` lays its arcs on it (0xa6412d
# feeds it to ``FSplineUtils::Build90DegreeSpline2D``), and ``ValidateCurvature``
# turns it into the actual floor, ``mBendRadius * 1.5 - 15``. See the
# ``belt.curvature`` rule in ``data/hologram_rules.json``.
BINARY_NOTES: dict[str, str] = {
    "belt_bend_radius_cm": (
        "AFGConveyorBeltHologram::mBendRadius -- the radius the hologram lays "
        "its own arcs on when it auto-routes a belt. It is NOT the legality "
        "floor: ValidateCurvature rejects a horizontal radius below "
        "mBendRadius * 1.5 - 15, which is 283.5 cm here, and it only runs that "
        "check in the curve build mode. Take the bound from the belt.curvature "
        "rule, not from this number."
    ),
}

# Which hologram rule governs each limit. **Governs, not enforces**: the rule
# says what the hologram does with the number, and only some of them refuse.
# ``provenance.limits[key].governed_by`` is
# ``{"rule": id, "effect": effect, "status": status}`` with both copied from the
# rule itself, so a reader of the registry cannot take a clamp, a snap or a
# computation for a refusal, and can see a ``partial`` rule -- one whose function
# was not read to the end -- without opening the rules file. Taking a clamp for a
# refusal is what the old ``enforced_by`` invited: it named
# ``lift.height_range`` (a clamp),
# ``buildable.grid_snap`` (a snap) and ``buildable.rotation_step`` (which
# quantises nothing at all: it works a step out and returns it, effect
# ``compute``) as things that "turn the value away". It also named
# ``lift.step``, which does govern ``lift_step_cm`` -- ``UpdateTopTransform``
# rounds a lift's height onto a multiple of it -- but with the effect ``snap``:
# the game moves the height rather than refusing it. A ``compute`` rule governs
# the same way: it says where the number comes from, and it says the game never
# refuses over it.
#
# Every key of :class:`Limits` is in this table or in :data:`NOT_GOVERNED`, and
# ``_check_rules`` holds the named ids, the copied effects and the copied
# statuses against ``data/hologram_rules.json``, so a limit whose rule was
# renamed, dropped or re-read fails the merge rather than shipping a stale claim.
GOVERNED_BY: dict[str, str] = {
    "belt_max_spline_cm": "belt.max_length",
    "belt_bend_radius_cm": "belt.curvature",
    "belt_max_incline_deg": "belt.incline",
    "belt_min_length_cm": "belt.min_length",
    "potential_per_shard": "factory.potential",
    "potential_shard_slots_default": "factory.potential",
    "production_boost_per_slot": "manufacturer.production_boost",
    "production_boost_slots_default": "manufacturer.production_boost",
    "lift_step_cm": "lift.step",
    # `compute`, like the two overclocking rules: FitClearance works the box out
    # and refuses nothing over it. Whether the box overlaps is decided by
    # `buildable.clearance`, and a validator that reads this as a bound would
    # refuse builds the game accepts.
    "lift_clearance_half_extent_cm": "lift.clearance",
    "lift_min_cm": "lift.height_range",
    "lift_max_cm": "lift.height_range",
    "lift_min_vertical_cm": "lift.height_range",
    "pipe_max_spline_cm": "pipe.max_length",
    "pipe_min_bend_radius_cm": "pipe.curvature",
    "hologram_grid_cm": "buildable.grid_snap",
    "hologram_rotation_step_deg": "buildable.rotation_step",
}

# The limits no hologram rule governs at all, with the reason. A number here is
# still game data; what it is not is a bound the game applies to a placement.
NOT_GOVERNED: dict[str, str] = {
    "pipe_bend_radius_cm": (
        "AFGPipelineHologram::mBendRadius is the radius AutoRouteSpline builds "
        "its bends on, not a bound anything compares against. The bound on a "
        "pipeline's curvature is mMinBendRadius, under the pipe.curvature rule."
    ),
    "pipe_bend_radius_2d_cm": (
        "the same, for Auto2DRouteSpline's conveyor-like mode (0xaafef0 reads "
        "mBendRadius2D): a construction radius, not a limit."
    ),
    "wire_max_cm": (
        "no rule was extracted for it. AFGBuildableWire::mMaxLength is a "
        "buildable's property rather than a hologram member, and "
        "AFGWireHologram was not disassembled; what refuses an over-long wire "
        "is still unread."
    ),
}

# Limits that are not game data at all. Each is a constant this project chose,
# with the reason; ``registry.json`` tags them ``"constant"`` so that nobody
# reads them as something the game states.
PROJECT_CONSTANTS: dict[str, tuple[float, str]] = {
    "hologram_rotation_step_deg": (
        90.0,
        "the step the build gun rotates a hologram by. It is in no cooked asset, "
        "no shipped header and no constructor immediate tools/sfy-native found; "
        "90 degrees is this project's constant, not a number read from the game.",
    ),
}

# The four direction sources ``tools/sfy-extract`` may report, and the only
# ones this merge will write. There is deliberately no source for "what a
# blueprint corpus wires a port to" and none for "what the component is called":
# a corpus says what somebody once built and a name is a convention, and neither
# is a fact about the game. ``flab2bp.sfy.registry.PORT_DIRECTION_SOURCES`` is
# the same list, re-checked on load.
#
# ``asset``            the buildable's own Blueprint states the direction
# ``asset-inherited``  a parent Blueprint's template of the same name states it
# ``native``           nothing in the asset chain does, so it is the archetype's:
#                      a store ``tools/sfy-native`` read out of the shipped DLL,
#                      quoted in ``data/native_directions.json``
# ``unknown``          none of the three answered. The port ships as ``unknown``
#                      and the validator refuses to route to it.


def _first(holograms: dict[str, dict[str, Any]], prefix: str, key: str) -> Any:
    """The first value of ``key`` set by a hologram of a ``prefix`` buildable."""
    for class_name in sorted(holograms):
        value = holograms[class_name].get(key)
        if class_name.startswith(prefix) and value is not None:
            return value
    return None


def _from_native(native: dict[str, Any]) -> dict[str, float | int]:
    """Every limit the binary states a constant for, by registry key.

    ``native.json`` reports every traced store as a JSON number; the two slot
    counts are ``int32`` members and are kept as integers, because a count of
    ``3.0`` in the registry would read as a measurement rather than a number of
    slots.
    """
    values: dict[str, float | int] = {}
    for key, (class_name, member_name) in NATIVE_LIMITS.items():
        try:
            member = native["classes"][class_name]["members"][member_name]
        except KeyError:
            raise SystemExit(
                f"native.json has no {class_name}::{member_name} for {key!r}; "
                "re-run tools/sfy-native"
            ) from None
        if member["value"] is not None:
            values[key] = int(member["value"]) if key in INT_LIMITS else float(member["value"])
    return values


def _check_headers(from_binary: dict[str, float]) -> None:
    """Refuse to merge a binary extraction the headers contradict."""
    wrong = [
        f"{key}: the header says {stated}, the binary says {from_binary[key]}"
        for key, stated in sorted(HEADER_DEFAULTS.items())
        if key in from_binary and abs(from_binary[key] - stated) > 1e-3 * max(abs(stated), 1.0)
    ]
    if wrong:
        raise SystemExit("tools/sfy-native disagrees with the headers:\n  " + "\n  ".join(wrong))


def _fill(
    limits: dict[str, Any],
    sources: dict[str, str],
    values: dict[str, Any],
    source: str,
) -> None:
    """Fill the limits this source knows and no earlier source filled."""
    for key, value in sorted(values.items()):
        if key not in limits:
            raise SystemExit(f"{source} has a limit the registry does not know: {key!r}")
        if value is None or limits[key] is not None:
            continue
        limits[key], sources[key] = value, source


def _mesh_height(docs: dict[str, Any]) -> float:
    """The conveyor lifts' mesh height H, which must be the same for every mark.

    ``AFGConveyorLiftHologram`` derives its three heights from the mesh height
    of the lift it is placing, so a mark with a different mesh would need its
    own limits. All six share 200 cm today; if that ever stops being true the
    limits have to move onto the buildable and this refuses rather than picking
    one silently.
    """
    heights = {
        cls: entry["mesh_height_cm"]
        for cls, entry in sorted(docs["buildables"].items())
        if cls.startswith(LIFT_CLASS_PREFIX)
    }
    if not heights:
        raise SystemExit(
            f"docs.json has no {LIFT_CLASS_PREFIX}* buildable to take a mesh height from"
        )
    distinct = set(heights.values())
    if len(distinct) != 1 or None in distinct:
        raise SystemExit(
            "the conveyor lift marks disagree on mMeshHeight, so the lift height limits "
            f"are no longer global: {heights}"
        )
    return float(distinct.pop())


def _belt_mesh_length(docs: dict[str, Any]) -> float:
    """The conveyor belts' Docs.json ``mMeshLength``, the same for every mark.

    ``AFGConveyorBeltHologram::BeginPlay`` caches it from the belt being placed
    and ``ValidateMinLength`` multiplies it, so a mark with a different mesh
    would have a different floor and the limit would have to move onto the
    buildable. All six share 200 cm today; this refuses rather than pick one
    silently, exactly as :func:`_mesh_height` does for the lifts.
    """
    lengths = {
        cls: entry["mesh_length_cm"]
        for cls, entry in sorted(docs["buildables"].items())
        if cls.startswith(BELT_CLASS_PREFIX)
    }
    if not lengths:
        raise SystemExit(
            f"docs.json has no {BELT_CLASS_PREFIX}* buildable to take a mesh length from"
        )
    distinct = set(lengths.values())
    if len(distinct) != 1 or None in distinct:
        raise SystemExit(
            "the conveyor belt marks disagree on mMeshLength, so the minimum belt length "
            f"is no longer global: {lengths}"
        )
    return float(distinct.pop())


def _check_belt_min_length(rules: Mapping[str, HologramRule]) -> str:
    """Hold :data:`BELT_MIN_LENGTH_FACTOR` to the rule's own evidence line.

    The factor is transcribed from the machine code into this script, the way
    the lift height formulas are, and a transcription can go stale. The
    ``belt.min_length`` rule carries the instruction it was read at, copied out
    of ``sfy-native disasm`` by address, so this looks the multiplier up there
    and stops the merge if the two no longer agree. The line it found is
    returned, and travels into the registry's provenance beside the number.
    """
    rule = rules.get(BELT_MIN_LENGTH_RULE)
    if rule is None:
        raise SystemExit(
            f"hologram_rules.json has no {BELT_MIN_LENGTH_RULE} rule, so nothing states what "
            "a belt's minimum length is compared against; re-run scripts/sfy_native_rules.py"
        )
    found = [line for line in rule.evidence if line.startswith(f"{BELT_MIN_LENGTH_EVIDENCE}:")]
    wanted = f"f32={BELT_MIN_LENGTH_FACTOR}"
    if len(found) != 1 or wanted not in found[0]:
        raise SystemExit(
            f"{BELT_MIN_LENGTH_RULE} no longer multiplies mMeshLength by "
            f"{BELT_MIN_LENGTH_FACTOR} at {BELT_MIN_LENGTH_EVIDENCE}: {found}. "
            "Re-read ValidateMinLength before this limit can be written."
        )
    return found[0]


def _lift_clearance_half_extent() -> tuple[float, dict[str, Any]]:
    """What ``lift.clearance``'s own read of the image says a lift's box is wide.

    Returns the half-extent and the provenance to file beside it. Everything
    here comes out of the rule's ``data_reads`` entry, which is
    ``sfy-native data``'s output for the PDB symbol plus the arithmetic the
    rule's instructions apply; nothing is transcribed. A rule with no such
    entry, an entry for another symbol, one the tool read out of a section that
    is not initialised, or two components that disagree all stop the merge:
    each of those means the reading has changed, and a stale width is a column
    of air the placer would deny for the wrong reason.
    """
    raw = json.loads((DATA / "hologram_rules.json").read_text(encoding="utf-8"))
    rule = next((r for r in raw["rules"] if r["id"] == LIFT_CLEARANCE_RULE), None)
    if rule is None:
        raise SystemExit(
            f"hologram_rules.json has no {LIFT_CLEARANCE_RULE} rule, so nothing states how "
            "wide a conveyor lift's clearance box is; re-run scripts/sfy_native_rules.py"
        )
    reads = [r for r in rule.get("data_reads", ()) if r["symbol"] == LIFT_CLEARANCE_SYMBOL]
    if len(reads) != 1:
        raise SystemExit(
            f"{LIFT_CLEARANCE_RULE} carries {len(reads)} reads of {LIFT_CLEARANCE_SYMBOL}, "
            "wanted one: the half-extent of a lift's clearance box is that global's "
            "initialiser and nothing else states it"
        )
    read = reads[0]
    if read["section"] not in (".data", ".rdata"):
        raise SystemExit(
            f"{LIFT_CLEARANCE_RULE} read {LIFT_CLEARANCE_SYMBOL} out of "
            f"{read['section']!r}, which is not an initialised section"
        )
    values = [float(v) for v in read["values"]]
    if len(values) != 2 or len(set(values)) != 1 or values[0] <= 0:
        raise SystemExit(
            f"{LIFT_CLEARANCE_SYMBOL} now gives {values} for a lift's clearance "
            "half-extent, and the registry states one number because the footprint is "
            "square. Two different axes need two fields and a placer that knows which "
            "is which; re-read lift.clearance before this limit can be written."
        )
    return values[0], {
        "formula": f"E {read['adjustment']:+}, per component",
        "E": read["doubles"],
        "E_from": (
            f"the {read['bytes']}-byte initialiser at {read['rva']} in {read['section']}, "
            f"read by the PDB symbol {LIFT_CLEARANCE_SYMBOL} "
            f"(sfy-native data; bytes {read['hex']})"
        ),
        "evaluated_in": (
            "AFGBuildableConveyorLift::FitClearance at 0x4e5dd0, which reads the two "
            "doubles (0x4e5e52, 0x4e5e5c) and adds the -5 vector to them (0x4e5edb and "
            "the two following) -- see the lift.clearance rule's data_reads"
        ),
        "caveat": (
            "this is the INITIALISER, what the shipped image holds before the game runs. "
            "FitClearance reads the global at hologram time, so a game that stored to it "
            "would make this stale; nothing disassembled writes to it, and that is not a "
            "proof that nothing does."
        ),
    }


def _power_shard_values(docs: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    """What one shard in a potential slot unlocks, per shard type, and its evidence.

    ``UFGPowerShardDescriptor::GetBoostValue`` is what
    ``AFGBuildableFactory::GetCurrentMaxPotentialForType`` adds once per shard,
    and it returns the descriptor's own ``mExtraPotential`` or
    ``mExtraProductionBoost`` -- Docs.json class defaults, which
    ``flab2bp.sfy.docs`` keeps in its ``power_shards`` section. Exactly one
    descriptor of each type is expected: two would mean the value is not a
    single number and a caller would have to ask which shard is in the slot.
    """
    wanted = {
        "potential_per_shard": ("PST_Overclock", "extra_potential"),
        "production_boost_per_slot": ("PST_ProductionBoost", "extra_production_boost"),
    }
    shards = docs.get("power_shards") or {}
    values: dict[str, float] = {}
    provenance: dict[str, Any] = {}
    for key, (shard_type, field_name) in wanted.items():
        found = {
            cls: entry
            for cls, entry in sorted(shards.items())
            if entry.get("shard_type") == shard_type and entry.get(field_name) is not None
        }
        if len(found) != 1:
            raise SystemExit(
                f"docs.json states {len(found)} {shard_type} power shard descriptors with a "
                f"{field_name} ({sorted(found)}), wanted one; re-run flab2bp.sfy.docs"
            )
        ((cls, entry),) = found.items()
        values[key] = float(entry[field_name])
        provenance[key] = {
            "descriptor": cls,
            "display_name": entry.get("display_name"),
            "property": "mExtraPotential"
            if field_name == "extra_potential"
            else "mExtraProductionBoost",
            "shard_type": shard_type,
            "consumed_in": (
                "AFGBuildableFactory::GetCurrentMaxPotentialForType @ 0x4ed988 "
                "(UFGPowerShardDescriptor::GetBoostValue), added once per shard in a slot"
            ),
        }
    return values, provenance


def _subsystem_defaults(assets: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The shard slot counts the cooked buildable subsystem overrides, if any.

    ``tools/sfy-extract`` finds the Blueprint by its super chain reaching the
    native ``FGBuildableSubsystem`` and reads its class default object; a
    property it does not override comes back ``None`` and falls through to the
    constructor value in ``native.json``. The shipped content overrides one of
    the two, which is the same shape as the three hologram grid snap overrides.
    """
    stated = assets.get("subsystem_defaults") or {}
    values = {
        "potential_shard_slots_default": stated.get("mDefaultPotentialShardSlots"),
        "production_boost_slots_default": stated.get("mDefaultProductionShardSlotSize"),
    }
    provenance = {
        key: {
            "class": stated.get("class"),
            "package": stated.get("package"),
            "property": prop,
            "overridden_by_the_blueprint": values[key] is not None,
            "applied_in": (
                "AFGBuildableFactory::BeginPlay at 0x4d40eb / 0x4d40fc, when the buildable's "
                "own mOverridePotentialShardSlots / mOverrideProductionShardSlotSize is clear "
                "(the factory.potential rule)"
            ),
        }
        for key, prop in (
            ("potential_shard_slots_default", "mDefaultPotentialShardSlots"),
            ("production_boost_slots_default", "mDefaultProductionShardSlotSize"),
        )
    }
    return values, provenance


def _attach_grid_snap(
    holograms: Mapping[str, Mapping[str, Any]], buildables: dict[str, Any]
) -> dict[str, Any]:
    """Put each buildable's own hologram grid on it, and return the provenance.

    A hologram that overrides ``AFGBuildableHologram::mGridSnapSize`` snaps on
    its own grid rather than the global 100 the constructor stores. The value is
    the hologram Blueprint's class default object -- so its source is the cooked
    ``assets``, not the binary ``hologram_grid_cm`` beside it -- and
    ``stated_on`` is the class whose own class default object named the
    hologram. The two differ wherever a mark inherits: ``Build_PowerPoleMk2_C``
    and ``Build_PowerPoleMk3_C`` restate no ``mHologramClass``, which in a cooked
    asset means the archetype's, so all three pole marks are built by
    ``Holo_PowerPole_C`` and all three snap at 50.

    ``buildable.grid_snap`` governs this and its effect is ``snap``: the game
    MOVES a hologram onto the grid and refuses nothing, so a finer grid here
    costs a nudge rather than a build.
    """
    applied: dict[str, Any] = {}
    for class_name, entry in sorted(buildables.items()):
        hologram = holograms.get(class_name) or {}
        grid = hologram.get("mGridSnapSize")
        entry["grid_snap_cm"] = grid
        if grid is None:
            continue
        applied[class_name] = {
            "grid_snap_cm": grid,
            "hologram": hologram.get("class"),
            "stated_on": hologram.get("stated_on"),
        }
    if not applied:
        raise SystemExit(
            "assets.json states no mGridSnapSize for any buildable's hologram, so every "
            "buildable would fall back on the global grid; re-run tools/sfy-extract"
        )
    return {
        "source": "assets",
        "read_from": "mGridSnapSize on the hologram class default object",
        "property": "mHologramClass",
        "applied_to": applied,
    }


def _attach_mesh_bounds(
    mesh_bounds: Mapping[str, Mapping[str, Any]], buildables: dict[str, Any]
) -> dict[str, Any]:
    """Put each spline buildable's own static-mesh box on it, and return the provenance.

    The box is ``Origin -+ BoxExtent`` of the cooked ``UStaticMesh``'s render
    bounds, which is the game's own axis-aligned box for that mesh; the
    extractor followed the class's ``mMesh`` (a belt) or ``mMidMesh`` (a lift)
    to get there and says which. A class the extractor found no such mesh for
    keeps ``None``: nothing here falls back to ``mMeshLength``, which is the
    repeat pitch along one axis and says nothing about the other two.
    """
    applied: dict[str, Any] = {}
    for class_name, entry in sorted(buildables.items()):
        record = mesh_bounds.get(class_name)
        if record is None:
            continue
        origin, extent = record.get("origin"), record.get("box_extent")
        if origin is None or extent is None:
            raise SystemExit(
                f"{class_name}'s {record.get('property')} mesh {record.get('mesh')} carries no "
                f"render bounds ({record.get('reason')}); re-run tools/sfy-extract"
            )
        entry["mesh_bounds_cm"] = [
            [o - e for o, e in zip(origin, extent, strict=True)],
            [o + e for o, e in zip(origin, extent, strict=True)],
        ]
        entry["mesh_bounds_property"] = record["property"]
        applied[class_name] = {
            "property": record["property"],
            "mesh": record["mesh"],
            "stated_on": record.get("stated_on"),
        }
    if not applied:
        raise SystemExit(
            "assets.json states no mesh bounds for any buildable, so no conveyor would carry "
            "its own mesh box; re-run tools/sfy-extract"
        )
    return {
        "source": "assets",
        "read_from": "the cooked UStaticMesh's RenderData.Bounds (Origin -+ BoxExtent)",
        "properties": {"belt": "mMesh", "lift": "mMidMesh"},
        "applied_to": applied,
    }


def _fill_native_connection_links(
    buildables: dict[str, Any], native: dict[str, Any]
) -> dict[str, Any]:
    """Give every power port whose asset chain is silent the constructor's count.

    ``mMaxNumConnectionLinks`` is a UPROPERTY on
    ``UFGCircuitConnectionComponent``, and a cooked asset omits it wherever it
    equals the archetype's value -- so a machine's power input says nothing
    anywhere in its chain and the answer is the C++ constructor's, which
    ``tools/sfy-native`` read out of the shipped DLL. This is the same hand-off
    the grid snap size makes: the asset wins where it speaks, the binary answers
    where it does not, and a port with neither stays ``unknown`` rather than
    being given a number nobody stated.
    """
    member = native["classes"].get("UFGCircuitConnectionComponent", {}).get("members", {})
    entry = member.get("mMaxNumConnectionLinks")
    if entry is None or entry.get("value") is None:
        raise SystemExit(
            "native.json states no UFGCircuitConnectionComponent::mMaxNumConnectionLinks, so "
            "every machine's power input would ship with an unknown wire count; re-run "
            "tools/sfy-native"
        )
    value = int(entry["value"])
    counts: dict[str, int] = {}
    for buildable in buildables.values():
        for port in buildable["ports"]:
            if port["kind"] == "power" and port.get("max_connections") is None:
                port["max_connections"] = value
                port["max_connections_source"] = "native"
            source = port["max_connections_source"]
            counts[source] = counts.get(source, 0) + 1
    klass = native["classes"]["UFGCircuitConnectionComponent"]
    bodies = klass.get("ctor_rva", {}).get(
        "UFGCircuitConnectionComponent::UFGCircuitConnectionComponent", []
    )
    return {
        "native_default": value,
        "class": "UFGCircuitConnectionComponent",
        "member": "mMaxNumConnectionLinks",
        "offset": entry["offset"],
        "set_in": entry["set_in"],
        "evidence": entry["evidence"],
        "size_source": entry["size_source"],
        "ctor_bodies": list(bodies),
        "note": (
            "The constructor symbol "
            "UFGCircuitConnectionComponent::UFGCircuitConnectionComponent resolves to "
            f"{len(bodies)} bodies in the PDB ({', '.join(bodies)}), which is what the "
            "compiler emits for a complete-object and a base-object constructor. Only the "
            f"second stores mMaxNumConnectionLinks -- the evidence {entry['evidence']!r} is "
            "an address inside it -- so the value is read from that body and the first is "
            "not evidence of anything. Anyone re-reading this member must check both bodies "
            "rather than the first the symbol resolves to."
        ),
        "sources": dict(sorted(counts.items())),
    }


def _check_rules(entries: Mapping[str, Any]) -> None:
    """Hold the governance the registry is about to claim to the rules themselves.

    Four checks, and nothing else: every governed rule id exists in
    ``data/hologram_rules.json``; the effect *and the status* copied beside it
    are that rule's own; no limit is in both :data:`GOVERNED_BY` and
    :data:`NOT_GOVERNED`; and every :class:`Limits` key is in exactly one of the
    two.
    """
    rules = load_rules()
    unknown = sorted({rule_id for rule_id in GOVERNED_BY.values() if rule_id not in rules})
    if unknown:
        raise SystemExit(
            f"registry limits name hologram rules that do not exist: {unknown}; "
            "re-run scripts/sfy_native_rules.py"
        )
    mismatched = sorted(
        f"{key}: {field} {governed[field]!r} beside {governed['rule']}, "
        f"which states {getattr(rules[governed['rule']], field)!r}"
        for key, entry in entries.items()
        if (governed := entry.get("governed_by"))
        for field in ("effect", "status")
        if governed[field] != getattr(rules[governed["rule"]], field)
    )
    if mismatched:
        raise SystemExit(
            "limits claim something their rule does not state:\n  " + "\n  ".join(mismatched)
        )
    both = sorted(set(GOVERNED_BY) & set(NOT_GOVERNED))
    if both:
        raise SystemExit(f"limits are both governed and ungoverned: {both}")
    known = {f.name for f in fields(Limits)}
    unplaced = sorted(known - set(GOVERNED_BY) - set(NOT_GOVERNED))
    if unplaced:
        raise SystemExit(f"limits say nothing about what governs them: {unplaced}")


def _limits(
    docs: dict[str, Any],
    assets: dict[str, Any],
    native: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """The merged limits, where each came from, and what the game does with each.

    The order is the order of authority: what a cooked asset says, then what the
    shipped binary was compiled with, then a formula the binary applies to a
    Docs.json input, then Docs.json itself, then a header, and last this
    project's own constants. A blueprint corpus is not among them and is not
    evidence for any of them.

    Every limit also says which hologram rule governs it and what that rule does
    with it -- refuse, clamp, snap, compute or nothing -- or that no rule governs
    it at all, and why. That is what makes a number in here a limit rather than a
    value somebody found in the game's data.
    """
    holograms = assets["holograms"]
    limits: dict[str, Any] = dict.fromkeys(f.name for f in fields(Limits))
    sources: dict[str, str] = {}
    provenance: dict[str, Any] = {}
    from_binary = _from_native(native)
    _check_headers(from_binary)
    subsystem, subsystem_provenance = _subsystem_defaults(assets)
    _fill(
        limits,
        sources,
        {"pipe_bend_radius_cm": _first(holograms, "Build_Pipeline", "mBendRadius"), **subsystem},
        "assets",
    )
    provenance.update(subsystem_provenance)
    _fill(limits, sources, from_binary, "binary")
    for key, note in BINARY_NOTES.items():
        provenance[key] = {"note": note, "is_a_proven_minimum": False}

    mesh_height = _mesh_height(docs)
    derived = {}
    for key, (formula, multiplier, offset) in LIFT_HEIGHT_FORMULAS.items():
        derived[key] = multiplier * mesh_height + offset
        provenance[key] = {
            "formula": formula,
            "H": mesh_height,
            "H_from": f"Docs.json mMeshHeight on {LIFT_CLASS_PREFIX}Mk1_C and its five siblings",
            "evaluated_in": "AFGConveyorLiftHologram::BeginPlay at 0xa64e70 (see native.json)",
        }
    half_extent, half_extent_provenance = _lift_clearance_half_extent()
    derived["lift_clearance_half_extent_cm"] = half_extent
    provenance["lift_clearance_half_extent_cm"] = half_extent_provenance
    mesh_length = _belt_mesh_length(docs)
    belt_min_evidence = _check_belt_min_length(load_rules())
    derived["belt_min_length_cm"] = BELT_MIN_LENGTH_FACTOR * mesh_length
    provenance["belt_min_length_cm"] = {
        "formula": f"{BELT_MIN_LENGTH_FACTOR} * L",
        "L": mesh_length,
        "L_from": f"Docs.json mMeshLength on {BELT_CLASS_PREFIX}Mk1_C and its five siblings",
        "evaluated_in": (
            "AFGConveyorBeltHologram::ValidateMinLength at 0xaa5870 "
            f"({belt_min_evidence}); the comparison is strict, so a belt of exactly this "
            "length is still too short"
        ),
    }
    _fill(limits, sources, derived, "binary-derived")

    shard_values, shard_provenance = _power_shard_values(docs)
    _fill(limits, sources, shard_values, "docs")
    provenance.update(shard_provenance)

    # The wire lengths are AFGBuildableWire::mMaxLength, a native default that
    # the cooked Build_PowerLine_C CDO omits. tools/sfy-extract reads them out
    # of the install's own Docs.json dump and copies them into assets.json, so
    # the value travels through assets.json but it is Docs.json that states it.
    _fill(
        limits,
        sources,
        {"wire_max_cm": {cls: w["mMaxLength"] for cls, w in sorted(assets["wires"].items())}},
        "docs",
    )
    _fill(limits, sources, dict(HEADER_DEFAULTS), "header")
    _fill(limits, sources, {k: v for k, (v, _) in PROJECT_CONSTANTS.items()}, "constant")
    for key, (_, reason) in PROJECT_CONSTANTS.items():
        provenance[key] = {"reason": reason}
    rules = load_rules()
    for key in sorted(f.name for f in fields(Limits)):
        entry = provenance.setdefault(key, {})
        rule_id = GOVERNED_BY.get(key)
        # The effect is the rule's own, copied here; ``_check_rules`` holds the
        # copy to the rule and reports a rule id that has gone missing.
        rule = None if rule_id is None else rules.get(rule_id)
        entry["governed_by"] = (
            None
            if rule_id is None
            else {
                "rule": rule_id,
                "effect": None if rule is None else rule.effect,
                # The rule's own status, copied for the same reason the effect
                # is: a `partial` rule was not read to the end, so what it says
                # about this limit is part of the story, and a reader of the
                # registry should see that without opening the rules file.
                "status": None if rule is None else rule.status,
            }
        )
        if key in NOT_GOVERNED:
            entry["ungoverned"] = NOT_GOVERNED[key]
    _check_rules(provenance)
    return limits, sources, provenance


def _attach_flow(
    directions: dict[str, Any], connections: dict[str, Any], buildables: dict[str, Any]
) -> dict[str, Any]:
    """Put the conveyor item-flow order on every class it is about, and return it.

    Two facts meet here, from two places in the game.

    ``scripts/sfy_native_directions.py`` reads the *order over the two members*
    out of the binary -- ``AFGBuildableConveyorBase``'s, because that is where
    both connections are created and where ``Factory_Tick`` grabs through one of
    them -- and the header states the same. That says ``mConnection0`` is the
    end items enter by; it does not say what the component in that member is
    called, and a name that happens to end in 0 is a convention, never evidence.

    ``tools/sfy-extract`` reads the *pairing* off each class's cooked class
    default object, where ``mConnection0`` is an object property referring to
    one of the CDO's own subobject exports. That is ``conveyor_connections``,
    per class, and it is what turns the member order into two port names.

    The classes this applies to are the ones that inherit that constructor,
    which ``native_directions.json`` already lists as the conveyor connection
    default's ``owner_classes``; a class whose CDO names no such component, or
    whose ports do not carry the names it does, stops the merge rather than
    shipping a flow order pointing at nothing.
    """
    flow = directions["conveyor_flow"]
    if flow["source"] not in FLOW_SOURCES:
        raise SystemExit(f"the conveyor flow order comes from no game source: {flow['source']!r}")
    owner = next(
        entry
        for entry in directions["owner_defaults"]
        if entry["set_in"].startswith(f"{flow['class']}::")
    )
    # The PDB spells a class ``AFGBuildableConveyorBelt``; Docs.json's
    # ``native_class`` is the same name without UHT's A/U prefix.
    native_classes = {name.removeprefix("A") for name in owner["owner_classes"]}
    members = (flow["entry"]["member"], flow["exit"]["member"])
    applied = []
    components: dict[str, dict[str, str]] = {}
    for class_name, entry in sorted(buildables.items()):
        if entry["native_class"] not in native_classes:
            continue
        held = connections.get(class_name)
        if held is None or any(member not in held for member in members):
            raise SystemExit(
                f"{class_name} derives from {flow['class']} but its class default object "
                f"names no component for {list(members)}; re-run tools/sfy-extract"
            )
        ends = (held[members[0]], held[members[1]])
        missing = [name for name in ends if name not in {p["name"] for p in entry["ports"]}]
        if missing:
            raise SystemExit(
                f"{class_name} holds {missing} in {list(members)} but carries no such port: "
                "the conveyor flow order cannot be attached to it"
            )
        entry["flow"] = {
            "entry": ends[0],
            "exit": ends[1],
            "source": flow["source"],
            "name_source": FLOW_NAME_SOURCE,
        }
        applied.append(class_name)
        components[class_name] = {member: held[member] for member in members}
    if not applied:
        raise SystemExit(
            f"no buildable derives from {sorted(native_classes)}, so the conveyor flow "
            "order has nothing to attach to; re-run scripts/sfy_native_directions.py"
        )
    return {
        **flow,
        "name_source": FLOW_NAME_SOURCE,
        "components": components,
        "applied_to": applied,
    }


def _attach_lift_geometry(rules: dict[str, Any], buildables: dict[str, Any]) -> dict[str, Any]:
    """Put the lift connector geometry on every lift mark, and return it.

    The values are :data:`LIFT_GEOMETRY`, which is the same for all six marks
    because it is ``AFGBuildableConveyorLift``'s own code and not a class
    default: nothing in ``SetupConnections`` or ``UpdateTopTransform`` reads
    anything a mark could differ in. What is checked here is that the two rules
    it was read from are still the rules that were read -- present, ``extracted``
    and still carrying the instruction the geometry turns on -- so a game build
    that moved either stops the merge instead of shipping the old numbers under
    a new binary.
    """
    for rule_id, (address, what) in LIFT_GEOMETRY_RULES.items():
        rule = rules.get(rule_id)
        if rule is None:
            raise SystemExit(
                f"the lift connector geometry was read from {rule_id}, which "
                "data/hologram_rules.json does not carry; re-run scripts/sfy_native_rules.py"
            )
        if rule.status != "extracted":
            raise SystemExit(
                f"{rule_id} is {rule.status}, so the lift connector geometry it states is "
                "not a reading of the whole function and cannot be attached"
            )
        if not any(line.startswith(f"{address}:") for line in rule.evidence):
            raise SystemExit(
                f"{rule_id} no longer quotes {address} ({what}), so the lift connector "
                "geometry has to be re-read before it can be written"
            )
    geometry = {field: value for field, (value, _source) in LIFT_GEOMETRY.items()}
    geometry["sources"] = {field: source for field, (_value, source) in LIFT_GEOMETRY.items()}
    applied = []
    for class_name, entry in sorted(buildables.items()):
        if entry["native_class"] != LIFT_NATIVE_CLASS:
            continue
        entry["lift"] = json.loads(json.dumps(geometry))
        applied.append(class_name)
    if not applied:
        raise SystemExit(
            f"no buildable is a {LIFT_NATIVE_CLASS}, so the lift connector geometry has "
            "nothing to attach to; re-run tools/sfy-extract"
        )
    return {
        **geometry,
        "native_class": LIFT_NATIVE_CLASS,
        "rules": sorted(LIFT_GEOMETRY_RULES),
        "header": LIFT_GEOMETRY_HEADER,
        "header_text": LIFT_GEOMETRY_HEADER_TEXT,
        "applied_to": applied,
    }


def _attach_cost_segments(
    rules: dict[str, Any], buildables: dict[str, Any], native: dict[str, Any]
) -> dict[str, Any]:
    """Put each spline buildable's cost segment on it, and return the provenance.

    Conveyor segments come from their mesh dimensions; pipeline segments are
    twice mMeshLength, as the native refund method states. Beam segments are
    already normalized from mLengthPerCost by the Docs extractor and retain
    their separate property provenance. An unsupported class is left alone,
    never assumed to follow one of these classes' costing methods.
    """
    if COST_SEGMENT_RULE not in rules:
        raise SystemExit(
            f"hologram_rules.json has no {COST_SEGMENT_RULE} rule, so nothing states what "
            "the cost segment is divided by; re-run scripts/sfy_native_rules.py"
        )
    if native["provenance"].get("dll_sha256") != PIPE_COST_EVIDENCE["dll_sha256"]:
        raise SystemExit(
            "pipeline cost evidence belongs to a different DLL; re-extract "
            "AFGBuildablePipeBase::GetDismantleRefundReturnsMultiplier"
        )
    applied: dict[str, float] = {}
    pipelines: dict[str, float] = {}
    for class_name, entry in sorted(buildables.items()):
        if entry["native_class"] == "FGBuildablePipeline":
            mesh_length = entry.get("mesh_length_cm")
            if mesh_length is None or float(mesh_length) <= 0.0:
                raise SystemExit(
                    f"{class_name} is costed by length but Docs.json states no "
                    "positive mMeshLength; re-run flab2bp.sfy.docs"
                )
            segment = float(mesh_length) * PIPE_COST_LENGTH_MULTIPLIER
            entry["length_per_cost_cm"] = segment
            entry["length_per_cost_source"] = "binary-derived"
            pipelines[class_name] = segment
            continue
        pair = COST_SEGMENT_PROPERTY.get(entry["native_class"])
        if pair is None:
            continue
        key, prop = pair
        value = entry.get(key)
        if value is None or float(value) <= 0.0:
            raise SystemExit(
                f"{class_name} is costed by length but Docs.json states no {prop} "
                f"for it ({key}={value!r}); re-run flab2bp.sfy.docs"
            )
        entry["length_per_cost_cm"] = float(value)
        entry["length_per_cost_source"] = COST_SEGMENT_SOURCE
        applied[class_name] = float(value)
    if not applied:
        raise SystemExit(
            f"no buildable has a native class in {sorted(COST_SEGMENT_PROPERTY)}, so no "
            "conveyor would be costed by length; docs.json has moved"
        )
    return {
        "rule": COST_SEGMENT_RULE,
        "source": COST_SEGMENT_SOURCE,
        "properties": {
            native: prop for native, (_key, prop) in sorted(COST_SEGMENT_PROPERTY.items())
        },
        "applied_to": applied,
        "pipeline": {**PIPE_COST_EVIDENCE, "applied_to": pipelines},
    }


def _asset_paths(class_paths: dict[str, str], wanted: set[str], what: str) -> dict[str, str]:
    """The asset path of every class in ``wanted``, or refuse naming the gaps.

    ``tools/sfy-extract`` collects these out of the game's own Docs.json, where
    one entry refers to another by its whole path. A class the registry names
    and no path was found for would be a class nothing can author, so this stops
    rather than shipping a registry with a hole in it.
    """
    missing = sorted(name for name in wanted if name not in class_paths)
    if missing:
        raise SystemExit(
            f"assets.json states no asset path for {len(missing)} {what} classes, "
            f"starting with {missing[:10]}; re-run tools/sfy-extract"
        )
    return {name: class_paths[name] for name in sorted(wanted)}


def _item_classes(docs: dict[str, Any]) -> set[str]:
    """Every item descriptor the registry can be asked for a path to.

    That is what a recipe names -- its ingredients and its products, which is
    what a blueprint's cost list and a machine's inventory filters are built
    from -- plus the building descriptors, which the build menu's own recipes
    produce.
    """
    items = set(docs["descriptors"])
    for recipe in docs["recipes"].values():
        items.update(item for item, _ in recipe["ingredients"])
        items.update(item for item, _ in recipe["products"])
    return items


def _check_directions(ports: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Hold every port's ``direction_source`` to the vocabulary, and count them.

    ``tools/sfy-extract`` resolves directions now -- it is the only thing that
    can, because resolving one means walking the Blueprint archetype chain and
    then the native constructor at the end of it. This merge no longer has a
    rule of its own to apply: it checks that what the extractor wrote is one of
    the four sources, that an ``unknown`` source and an ``unknown`` direction go
    together, and refuses anything else. A registry that had taken a direction
    from a blueprint corpus or from a port's name would fail here.
    """
    counts = {source: 0 for source in PORT_DIRECTION_SOURCES}
    wrong = []
    for class_name, entries in sorted(ports.items()):
        for port in entries:
            where = f"{class_name}.{port['name']}"
            source = port.get("direction_source")
            if source not in counts:
                wrong.append(f"{where}: direction_source {source!r} is not a game source")
                continue
            if (port["direction"] == "unknown") != (source == "unknown"):
                wrong.append(f"{where}: direction {port['direction']!r} with source {source!r}")
                continue
            counts[source] += 1
    if wrong:
        raise SystemExit(
            "tools/sfy-extract wrote port directions this merge will not ship:\n  "
            + "\n  ".join(wrong)
        )
    return counts


def _shipped_direction_counts(buildables: dict[str, Any]) -> dict[str, int]:
    """How the directions of the ports the registry actually ships were resolved.

    ``_check_directions`` counts every class ``tools/sfy-extract`` read, and it
    reads every cooked ``Build_*`` class -- including the ones Docs.json does not
    list, which never become a buildable here. Counting those would describe a
    registry nobody loads, so the provenance counts these, over the ports that
    are on a shipped buildable.
    """
    counts = {source: 0 for source in PORT_DIRECTION_SOURCES}
    for entry in buildables.values():
        for port in entry["ports"]:
            counts[port["direction_source"]] += 1
    return counts


#: The five generated files the merge joins. Their digests go into the
#: registry's provenance, so the file says which extraction it was built from.
MERGE_INPUTS = (
    "docs.json",
    "assets.json",
    "native.json",
    "native_directions.json",
    "hologram_rules.json",
)


def _inputs_sha256() -> dict[str, str]:
    """The sha256 of each file the merge reads.

    This replaced a ``merged_at_commit`` that recorded the repository's ``HEAD``.
    A commit sha is stale the moment it is written -- the registry is committed
    *in* the commit after the one it named, and any later commit that touches
    nothing here makes it wrong again -- and it never answered the question
    somebody reading the file has, which is which extraction produced it. These
    digests do answer it, and they are stable: re-running the merge over the same
    inputs writes the same bytes, which is what lets the drift test compare the
    whole file rather than all of it but one key.
    """
    return {name: hashlib.sha256((DATA / name).read_bytes()).hexdigest() for name in MERGE_INPUTS}


def main(out: Path | None = None) -> int:
    """Write the registry, by default over the committed ``data/registry.json``.

    ``out`` is for the drift test, which re-runs the merge into a temporary file
    and diffs it against what is committed.
    """
    docs = json.loads((DATA / "docs.json").read_text(encoding="utf-8"))
    assets = json.loads((DATA / "assets.json").read_text(encoding="utf-8"))
    native = json.loads((DATA / "native.json").read_text(encoding="utf-8"))
    directions = json.loads((DATA / "native_directions.json").read_text(encoding="utf-8"))
    rules_provenance = json.loads((DATA / "hologram_rules.json").read_text(encoding="utf-8"))[
        "provenance"
    ]
    inputs = _inputs_sha256()

    extracted_counts = _check_directions(assets["ports"])

    for class_name, buildable in docs["buildables"].items():
        buildable["ports"] = assets["ports"].get(class_name, [])
    grid_snap = _attach_grid_snap(assets["holograms"], docs["buildables"])
    missing_ports = sorted(set(docs["buildables"]) - set(assets["ports"]))
    conveyor_flow = _attach_flow(
        directions, assets.get("conveyor_connections", {}), docs["buildables"]
    )
    rules = load_rules()
    cost_segments = _attach_cost_segments(rules, docs["buildables"], native)
    lift_geometry = _attach_lift_geometry(rules, docs["buildables"])
    mesh_bounds = _attach_mesh_bounds(assets.get("mesh_bounds", {}), docs["buildables"])
    connection_links = _fill_native_connection_links(docs["buildables"], native)
    direction_counts = _shipped_direction_counts(docs["buildables"])
    item_paths = _asset_paths(assets["class_paths"], _item_classes(docs), "item descriptor")
    recipe_paths = _asset_paths(assets["class_paths"], set(docs["recipes"]), "recipe")

    limits, sources, limit_provenance = _limits(docs, assets, native)
    registry = {
        "provenance": {
            "docs": docs["provenance"],
            "assets": assets["provenance"],
            "native": native["provenance"],
            "hologram_rules": rules_provenance,
            "conveyor_flow": conveyor_flow,
            "lift_geometry": lift_geometry,
            "cost_segment": cost_segments,
            "mesh_bounds": mesh_bounds,
            "grid_snap": grid_snap,
            "max_connections": connection_links,
            "limits": limit_provenance,
            "port_directions": {
                "sources": list(PORT_DIRECTION_SOURCES),
                # Over the ports this registry ships. The extractor reads ports
                # off every cooked Build_* class, Docs.json lists fewer of them
                # than that, and the wider count is kept beside this one rather
                # than in place of it.
                "resolved_from": direction_counts,
                "resolved_from_all_extracted": extracted_counts,
            },
            "inputs_sha256": inputs,
        },
        "buildables": docs["buildables"],
        "recipes": docs["recipes"],
        "descriptors": docs["descriptors"],
        "build_recipes": docs["build_recipes"],
        "fluid_items": docs["fluid_items"],
        "item_paths": item_paths,
        "recipe_paths": recipe_paths,
        "limits": limits,
        "limits_sources": sources,
    }
    target = DATA / "registry.json" if out is None else Path(out)
    target.write_text(json.dumps(registry, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    if missing_ports:
        print("buildables with no entry in assets.json:", missing_ports)
    for source in sorted(set(sources.values())):
        print(f"limits from {source}:", sorted(k for k, v in sources.items() if v == source))
    print("limits still None:", [k for k, v in limits.items() if v is None])
    print(
        "conveyor item flow:",
        f"{conveyor_flow['entry']['member']} -> {conveyor_flow['exit']['member']}",
        f"({conveyor_flow['source']}), named by the {conveyor_flow['name_source']}",
        f"on {len(conveyor_flow['applied_to'])} classes",
    )
    print(
        "lift connectors:",
        f"bottom at {lift_geometry['bottom_offset']} facing {lift_geometry['bottom_facing']},",
        f"top along {lift_geometry['top_offset_axis']} at a"
        f" {lift_geometry['top_yaw_step_deg']:g} degree yaw step,",
        f"on {len(lift_geometry['applied_to'])} classes",
    )
    print("port directions resolved from:", direction_counts)
    print("port connection counts resolved from:", connection_links["sources"])
    print(f"mesh boxes on {len(mesh_bounds['applied_to'])} classes")
    print(
        "own hologram grid on:",
        {c: e["grid_snap_cm"] for c, e in grid_snap["applied_to"].items()},
    )
    print("over every extracted class:", extracted_counts)
    print(f"asset paths: {len(item_paths)} item descriptors, {len(recipe_paths)} recipes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
