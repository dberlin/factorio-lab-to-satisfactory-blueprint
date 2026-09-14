"""Merge the game-data sources into the committed ``registry.json``.

``docs.json`` (Task 9) is the game's own Docs.json dump: buildables, recipes and
descriptors. ``assets.json`` (``tools/sfy-extract``) is what the cooked assets
add: the connection ports of every buildable and whatever limits the hologram
Blueprints override. ``native.json`` (``tools/sfy-native``) is what the shipped
DLL's machine code states for the hologram limits no asset carries.
``measured.json`` (``scripts/sfy_measure_limits.py``) is the envelope the
blueprint corpus allows, and is only reached by a limit the game works out at
run time. This script joins the four and writes the registry that
:func:`flab2bp.sfy.registry.load_registry` reads.

Run it after re-running the extractors for a new game build::

    uv run python scripts/sfy_registry.py

It prints where each limit came from, and any it could not fill at all.
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
# computes it at run time rather than storing a constant -- falls through to the
# measured envelope, and ``registry.json``'s ``limits_sources`` says so.
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

# The three the binary does not state either. ``AFGConveyorLiftHologram`` works
# all three out in ``BeginPlay`` from the lift buildable's own mesh height, so
# there is no constant in the DLL to read; ``native.json`` carries the
# disassembly of each computing store as its reason. The measured envelope from
# ``scripts/sfy_measure_limits.py`` is what fills them.
COMPUTED_AT_RUN_TIME = ("lift_min_cm", "lift_max_cm", "lift_min_vertical_cm")


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


def _limits(
    assets: dict[str, Any], native: dict[str, Any], measured: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """The merged limits, and where each of them came from.

    The order is the order of authority: what a cooked asset says, then what
    the shipped binary was compiled with, then what a header states, then --
    only for a limit the game has no constant for at all -- what the blueprint
    corpus was measured to allow.
    """
    holograms = assets["holograms"]
    limits: dict[str, Any] = dict.fromkeys(f.name for f in fields(Limits))
    sources: dict[str, str] = {}
    from_binary = _from_native(native)
    _check_headers(from_binary)
    _fill(
        limits,
        sources,
        {
            "pipe_bend_radius_cm": _first(holograms, "Build_Pipeline", "mBendRadius"),
            "hologram_rotation_step_deg": float(assets["grid"]["rotation_step"]),
            "wire_max_cm": {cls: w["mMaxLength"] for cls, w in sorted(assets["wires"].items())},
        },
        "assets",
    )
    _fill(limits, sources, from_binary, "binary")
    _fill(limits, sources, dict(HEADER_DEFAULTS), "header")
    _fill(
        limits,
        sources,
        {key: entry["value"] for key, entry in measured["limits"].items()},
        "measured",
    )
    return limits, sources


def main() -> int:
    docs = json.loads((DATA / "docs.json").read_text(encoding="utf-8"))
    assets = json.loads((DATA / "assets.json").read_text(encoding="utf-8"))
    native = json.loads((DATA / "native.json").read_text(encoding="utf-8"))
    measured = json.loads((DATA / "measured.json").read_text(encoding="utf-8"))

    for class_name, buildable in docs["buildables"].items():
        buildable["ports"] = assets["ports"].get(class_name, [])
    missing_ports = sorted(set(docs["buildables"]) - set(assets["ports"]))

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    limits, sources = _limits(assets, native, measured)
    registry = {
        "provenance": {
            "docs": docs["provenance"],
            "assets": assets["provenance"],
            "native": native["provenance"],
            "measured": measured["provenance"] | {"corpus": measured["corpus"]},
            "merged_at_commit": sha,
        },
        "buildables": docs["buildables"],
        "recipes": docs["recipes"],
        "descriptors": docs["descriptors"],
        "build_recipes": docs["build_recipes"],
        "limits": limits,
        "limits_sources": sources,
    }
    (DATA / "registry.json").write_text(
        json.dumps(registry, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

    if missing_ports:
        print("buildables with no entry in assets.json:", missing_ports)
    for source in ("assets", "binary", "header", "measured"):
        print(f"limits from {source}:", sorted(k for k, v in sources.items() if v == source))
    print("limits still None:", [k for k, v in limits.items() if v is None])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
