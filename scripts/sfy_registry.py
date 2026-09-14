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
governs it and copies that rule's effect -- ``refuse``, ``clamp``, ``snap`` or
``none`` -- or is ``null``, with ``ungoverned`` carrying the reason no rule does.
Only a ``refuse`` turns a placement away; a clamp or a snap moves it.

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

import json
import subprocess
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from flab2bp.sfy.registry import (
    FLOW_NAME_SOURCES,
    FLOW_SOURCES,
    PORT_DIRECTION_SOURCES,
    Limits,
)
from flab2bp.sfy.rules import load_rules

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
}

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

# The one source a port *name* may come from. ``flab2bp.sfy.registry`` re-checks
# it on load: which component sits in ``mConnection0`` is stated by the cooked
# class default object and nowhere else, and what a component happens to be
# called is otherwise a convention rather than a fact about the game.
FLOW_NAME_SOURCE = "asset"
assert FLOW_NAME_SOURCE in FLOW_NAME_SOURCES

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
# ``provenance.limits[key].governed_by`` is ``{"rule": id, "effect": effect}``
# with the effect copied from the rule itself, so a reader of the registry
# cannot take a clamp or a snap for a refusal -- which is what the old
# ``enforced_by`` invited: it named ``lift.height_range`` (a clamp),
# ``buildable.grid_snap`` and ``buildable.rotation_step`` (snaps) as things that
# "turn the value away". It also named ``lift.step``, where nothing was found to
# be enforced at all; that limit is in :data:`NOT_GOVERNED` now, because a rule
# whose effect is ``none`` governs nothing.
#
# Every key of :class:`Limits` is in this table or in :data:`NOT_GOVERNED`, and
# ``_check_rules`` holds the named ids and the copied effects against
# ``data/hologram_rules.json``, so a limit whose rule was renamed, dropped or
# re-read fails the merge rather than shipping a stale claim.
GOVERNED_BY: dict[str, str] = {
    "belt_max_spline_cm": "belt.max_length",
    "belt_bend_radius_cm": "belt.curvature",
    "belt_max_incline_deg": "belt.incline",
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
    "lift_step_cm": (
        "AFGConveyorLiftHologram compares mStepHeight (lift.step evidence) but "
        "never quantises a height to it; the multiple is this project's own "
        "stricter rule (spec section 10)."
    ),
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


def _from_native(native: dict[str, Any]) -> dict[str, float]:
    """Every limit the binary states a constant for, by registry key."""
    values: dict[str, float] = {}
    for key, (class_name, member_name) in NATIVE_LIMITS.items():
        try:
            member = native["classes"][class_name]["members"][member_name]
        except KeyError:
            raise SystemExit(
                f"native.json has no {class_name}::{member_name} for {key!r}; "
                "re-run tools/sfy-native"
            ) from None
        if member["value"] is not None:
            values[key] = float(member["value"])
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


def _check_rules(entries: Mapping[str, Any]) -> None:
    """Hold the governance the registry is about to claim to the rules themselves.

    Four checks, and nothing else: every governed rule id exists in
    ``data/hologram_rules.json``; the effect copied beside it is that rule's own
    effect; no limit is in both :data:`GOVERNED_BY` and :data:`NOT_GOVERNED`; and
    every :class:`Limits` key is in exactly one of the two.
    """
    rules = load_rules()
    unknown = sorted({rule_id for rule_id in GOVERNED_BY.values() if rule_id not in rules})
    if unknown:
        raise SystemExit(
            f"registry limits name hologram rules that do not exist: {unknown}; "
            "re-run scripts/sfy_native_rules.py"
        )
    mismatched = sorted(
        f"{key}: {governed['effect']!r} beside {governed['rule']}, "
        f"which states {rules[governed['rule']].effect!r}"
        for key, entry in entries.items()
        if (governed := entry.get("governed_by"))
        and governed["effect"] != rules[governed["rule"]].effect
    )
    if mismatched:
        raise SystemExit(
            "limits claim an effect their rule does not state:\n  " + "\n  ".join(mismatched)
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

    Every limit also says which hologram rule governs it and what that rule
    does with it -- refuse, clamp, snap or nothing -- or that no rule governs it
    at all, and why. That is what makes a number in here a limit rather than a
    value somebody found in the game's data.
    """
    holograms = assets["holograms"]
    limits: dict[str, Any] = dict.fromkeys(f.name for f in fields(Limits))
    sources: dict[str, str] = {}
    provenance: dict[str, Any] = {}
    from_binary = _from_native(native)
    _check_headers(from_binary)
    _fill(
        limits,
        sources,
        {"pipe_bend_radius_cm": _first(holograms, "Build_Pipeline", "mBendRadius")},
        "assets",
    )
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
    _fill(limits, sources, derived, "binary-derived")

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
            else {"rule": rule_id, "effect": None if rule is None else rule.effect}
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
                wrong.append(
                    f"{where}: direction {port['direction']!r} with source {source!r}"
                )
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


def main(out: Path | None = None) -> int:
    """Write the registry, by default over the committed ``data/registry.json``.

    ``out`` is for the drift test, which re-runs the merge into a temporary file
    and diffs it against what is committed.
    """
    docs = json.loads((DATA / "docs.json").read_text(encoding="utf-8"))
    assets = json.loads((DATA / "assets.json").read_text(encoding="utf-8"))
    native = json.loads((DATA / "native.json").read_text(encoding="utf-8"))
    directions = json.loads((DATA / "native_directions.json").read_text(encoding="utf-8"))
    rules_provenance = json.loads(
        (DATA / "hologram_rules.json").read_text(encoding="utf-8")
    )["provenance"]

    extracted_counts = _check_directions(assets["ports"])

    for class_name, buildable in docs["buildables"].items():
        buildable["ports"] = assets["ports"].get(class_name, [])
        # A hologram that overrides AFGBuildableHologram::mGridSnapSize snaps on
        # its own grid rather than the global one; only power poles, power towers
        # and street lights do, all to 50.
        hologram = assets["holograms"].get(class_name) or {}
        buildable["grid_snap_cm"] = hologram.get("mGridSnapSize")
    missing_ports = sorted(set(docs["buildables"]) - set(assets["ports"]))
    conveyor_flow = _attach_flow(
        directions, assets.get("conveyor_connections", {}), docs["buildables"]
    )
    direction_counts = _shipped_direction_counts(docs["buildables"])
    item_paths = _asset_paths(assets["class_paths"], _item_classes(docs), "item descriptor")
    recipe_paths = _asset_paths(assets["class_paths"], set(docs["recipes"]), "recipe")

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    limits, sources, limit_provenance = _limits(docs, assets, native)
    registry = {
        "provenance": {
            "docs": docs["provenance"],
            "assets": assets["provenance"],
            "native": native["provenance"],
            "hologram_rules": rules_provenance,
            "conveyor_flow": conveyor_flow,
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
            "merged_at_commit": sha,
        },
        "buildables": docs["buildables"],
        "recipes": docs["recipes"],
        "descriptors": docs["descriptors"],
        "build_recipes": docs["build_recipes"],
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
    print("port directions resolved from:", direction_counts)
    print("over every extracted class:", extracted_counts)
    print(f"asset paths: {len(item_paths)} item descriptors, {len(recipe_paths)} recipes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
