"""Merge the game-data sources into the committed ``registry.json``.

``docs.json`` (Task 9) is the game's own Docs.json dump: buildables, recipes and
descriptors. ``assets.json`` (``tools/sfy-extract``) is what the cooked assets
add: the connection ports of every buildable and whatever limits the hologram
Blueprints override. ``native.json`` (``tools/sfy-native``) is what the shipped
DLL's machine code states for the hologram limits no asset carries.
``measured.json`` (``scripts/sfy_measure_limits.py``) is what the blueprint
corpus shows. This script joins the four and writes the registry that
:func:`flab2bp.sfy.registry.load_registry` reads.

**Every limit in the registry comes from game data.** The corpus fills none of
them: it corroborates them, through ``registry.json``'s ``limits_measured``,
and it resolves port directions the cooked asset leaves to a component
archetype. A measured number is an envelope -- what the game was seen to accept
-- and an envelope must never become a constraint.

Run it after re-running the extractors for a new game build::

    uv run python scripts/sfy_registry.py

It prints where each limit came from, and any it could not fill at all. It
refuses to write a registry when the sources contradict each other: a header
against the binary, or an asset's stated port direction against the corpus or
the naming convention.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import fields
from pathlib import Path
from typing import Any

from flab2bp.sfy.registry import Limits

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

# ``AFGBuildableConveyorBase``'s two connections, which the cooked asset leaves
# to the component archetype and ``tools/sfy-extract`` therefore reports as
# ``"unknown"``. The header states the order outright:
#
#   Source/FactoryGame/Public/Buildables/FGBuildableConveyorBase.h:380
#       /** First connection on conveyor belt, Connections are always in the
#           same order, mConnection0 is the input, mConnection1 is the output. */
#
# Both belts and lifts derive from that class and name the components in that
# order, and the corpus agrees on every wired conveyor end in the fixtures.
CONVEYOR_ENDS: dict[str, str] = {"ConveyorAny0": "input", "ConveyorAny1": "output"}
CONVEYOR_END_HEADER = "Buildables/FGBuildableConveyorBase.h:380"

# The last resort for a port the asset, the header and the corpus all leave
# open: the component's own name. Every one of the 63 belt ports in the content
# that *does* spell its direction agrees with this, so it is a convention the
# game's own data corroborates rather than a guess -- and ``_check_directions``
# refuses the merge if a future build breaks it.
NAME_DIRECTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Output",), "output"),
    (("SnapOnly",), "snap_only"),
    # "InPut" is the Space Elevator's own typo; "FuelInput" is the truck and
    # fluid stations' fuel belt; "ConveyorInput" is the Portal's.
    (("Input", "InPut", "ConveyorInput", "FuelInput"), "input"),
)


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


def _limits(
    docs: dict[str, Any], assets: dict[str, Any], native: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """The merged limits, where each came from, and the provenance of the odd ones.

    The order is the order of authority: what a cooked asset says, then what the
    shipped binary was compiled with, then a formula the binary applies to a
    Docs.json input, then Docs.json itself, then a header, and last this
    project's own constants. Nothing is filled from the blueprint corpus any
    more -- every limit has a game-data source, and
    ``scripts/sfy_measure_limits.py``'s numbers are corroboration beside them.
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
    return limits, sources, provenance


def _by_name(port_name: str) -> str | None:
    """The direction the component's own name implies, or ``None``."""
    for prefixes, direction in NAME_DIRECTIONS:
        if port_name.startswith(prefixes):
            return direction
    return None


def _check_directions(ports: dict[str, list[dict[str, Any]]], corpus: dict[str, Any]) -> None:
    """Refuse the merge if the two fallbacks contradict the game's own data.

    The name convention and the corpus are only usable because everything that
    *is* stated agrees with them. If a game update breaks either, that has to
    stop the merge, not quietly reshape the registry.
    """
    wrong = []
    for class_name, entries in sorted(ports.items()):
        for port in entries:
            if port["kind"] != "belt" or port["direction"] == "unknown":
                continue
            stated = port["direction"]
            guess = _by_name(port["name"])
            if guess is not None and guess != stated:
                wrong.append(f"{class_name}.{port['name']}: asset says {stated}, name says {guess}")
            seen = corpus.get(f"{class_name}.{port['name']}")
            # A port the asset calls "any" really does take either, so the one
            # direction players happened to wire it is not a disagreement. The
            # conveyor-lift splitter and merger are the four such ports.
            if stated in ("any", "snap_only"):
                continue
            if seen is not None and not seen["conflict"] and seen["direction"] != stated:
                wrong.append(
                    f"{class_name}.{port['name']}: asset says {stated}, "
                    f"the corpus wires it as {seen['direction']} on {seen['links']} links"
                )
    if wrong:
        raise SystemExit("port directions contradict each other:\n  " + "\n  ".join(wrong))


def _resolve_directions(
    ports: dict[str, list[dict[str, Any]]], corpus: dict[str, Any]
) -> dict[str, int]:
    """Give every port a direction and a ``direction_source``, in place.

    ``tools/sfy-extract`` reports ``"unknown"`` for a belt connection whose
    ``mDirection`` the cooked asset omits, because the value then comes from the
    component's archetype -- a native constructor the pak does not carry. Three
    things resolve those, strongest first: the conveyor header, the corpus, and
    the naming convention. A port that none of them resolves stops the merge.
    """
    counts = {source: 0 for source in ("asset", "header", "corpus", "name")}
    unresolved = []
    for class_name, entries in sorted(ports.items()):
        conveyor = class_name.startswith(("Build_ConveyorBelt", LIFT_CLASS_PREFIX))
        for port in entries:
            if port["direction"] != "unknown":
                source = "asset"
            elif conveyor and port["name"] in CONVEYOR_ENDS:
                port["direction"], source = CONVEYOR_ENDS[port["name"]], "header"
            elif (seen := corpus.get(f"{class_name}.{port['name']}")) and not seen["conflict"]:
                port["direction"], source = seen["direction"], "corpus"
            elif (guess := _by_name(port["name"])) is not None:
                port["direction"], source = guess, "name"
            else:
                unresolved.append(f"{class_name}.{port['name']}")
                continue
            port["direction_source"] = source
            counts[source] += 1
    if unresolved:
        raise SystemExit(
            "no source gives these ports a direction; add a rule rather than "
            f"shipping a port nobody can route:\n  {unresolved}"
        )
    return counts


def main(out: Path | None = None) -> int:
    """Write the registry, by default over the committed ``data/registry.json``.

    ``out`` is for the drift test, which re-runs the merge into a temporary file
    and diffs it against what is committed.
    """
    docs = json.loads((DATA / "docs.json").read_text(encoding="utf-8"))
    assets = json.loads((DATA / "assets.json").read_text(encoding="utf-8"))
    native = json.loads((DATA / "native.json").read_text(encoding="utf-8"))
    measured = json.loads((DATA / "measured.json").read_text(encoding="utf-8"))

    corpus_directions = measured["port_directions"]
    _check_directions(assets["ports"], corpus_directions)
    direction_counts = _resolve_directions(assets["ports"], corpus_directions)

    for class_name, buildable in docs["buildables"].items():
        buildable["ports"] = assets["ports"].get(class_name, [])
        # A hologram that overrides AFGBuildableHologram::mGridSnapSize snaps on
        # its own grid rather than the global one; only power poles, power towers
        # and street lights do, all to 50.
        hologram = assets["holograms"].get(class_name) or {}
        buildable["grid_snap_cm"] = hologram.get("mGridSnapSize")
    missing_ports = sorted(set(docs["buildables"]) - set(assets["ports"]))

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    limits, sources, limit_provenance = _limits(docs, assets, native)
    registry = {
        "provenance": {
            "docs": docs["provenance"],
            "assets": assets["provenance"],
            "native": native["provenance"],
            "measured": measured["provenance"] | {"corpus": measured["corpus"]},
            "limits": limit_provenance,
            "port_directions": {
                "header": CONVEYOR_END_HEADER,
                "resolved_from": direction_counts,
            },
            "merged_at_commit": sha,
        },
        "buildables": docs["buildables"],
        "recipes": docs["recipes"],
        "descriptors": docs["descriptors"],
        "build_recipes": docs["build_recipes"],
        "limits": limits,
        "limits_sources": sources,
        "limits_measured": measured["limits"],
    }
    target = DATA / "registry.json" if out is None else Path(out)
    target.write_text(json.dumps(registry, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    if missing_ports:
        print("buildables with no entry in assets.json:", missing_ports)
    for source in sorted(set(sources.values())):
        print(f"limits from {source}:", sorted(k for k, v in sources.items() if v == source))
    print("limits still None:", [k for k, v in limits.items() if v is None])
    print("port directions resolved from:", direction_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
