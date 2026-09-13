"""Merge ``docs.json`` and ``assets.json`` into the committed ``registry.json``.

``docs.json`` (Task 9) is the game's own Docs.json dump: buildables, recipes and
descriptors. ``assets.json`` (``tools/sfy-extract``) is what the cooked assets
add: the connection ports of every buildable and whatever limits the hologram
Blueprints override. This script joins the two and writes the registry that
:func:`flab2bp.sfy.registry.load_registry` reads.

Run it after re-running the extractor for a new game build::

    uv run python scripts/sfy_registry.py

It prints the limits it could not fill, which should stay in step with the
``xfail`` cases in ``tests/sfy/test_registry.py``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "flab2bp" / "sfy" / "data"

# Limits that live only in the game's C++ headers, which ship as
# ``CommunityResources/Headers.zip``. Every one of these is a UPROPERTY with an
# in-class initialiser, so the value is right there in the header; the ones
# initialised in a constructor instead are unreachable and stay None (see
# ``UNFILLABLE`` below).
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

# Limits that are in neither the cooked assets nor the headers, with the reason.
# They are set by native constructors that the install does not ship, so the
# registry leaves them None rather than guessing a number.
UNFILLABLE: dict[str, str] = {
    "belt_bend_radius_cm": (
        "AFGConveyorBeltHologram::mBendRadius is EditDefaultsOnly with no in-class "
        "initialiser and Holo_ConveyorBelt_C does not override it"
    ),
    "belt_max_incline_deg": (
        "AFGConveyorBeltHologram::mMaxIncline is EditDefaultsOnly with no in-class "
        "initialiser and Holo_ConveyorBelt_C does not override it"
    ),
    "lift_step_cm": (
        "AFGConveyorLiftHologram::mStepHeight is a plain C++ member, computed at run time"
    ),
    "lift_min_cm": (
        "AFGConveyorLiftHologram::mMinimumHeight is a plain C++ member, computed at run time"
    ),
    "lift_max_cm": (
        "AFGConveyorLiftHologram::mMaximumHeight is a plain C++ member, computed at run time"
    ),
    "lift_min_vertical_cm": (
        "AFGConveyorLiftHologram::mMinimumHeightWithVerticalConnection is a plain C++ "
        "member, computed at run time"
    ),
    "hologram_grid_cm": (
        "AFGBuildableHologram::mGridSnapSize has no in-class initialiser; the only "
        "values in the assets are per-hologram overrides (50 on power poles and "
        "street lights), which is not a global snap size"
    ),
}


def _first(holograms: dict[str, dict[str, Any]], prefix: str, key: str) -> Any:
    """The first value of ``key`` set by a hologram of a ``prefix`` buildable."""
    for class_name in sorted(holograms):
        value = holograms[class_name].get(key)
        if class_name.startswith(prefix) and value is not None:
            return value
    return None


def _limits(assets: dict[str, Any]) -> dict[str, Any]:
    holograms = assets["holograms"]
    limits: dict[str, Any] = dict.fromkeys(UNFILLABLE)
    limits.update(HEADER_DEFAULTS)
    limits.update(
        {
            "pipe_bend_radius_cm": _first(holograms, "Build_Pipeline", "mBendRadius"),
            "hologram_rotation_step_deg": float(assets["grid"]["rotation_step"]),
            "wire_max_cm": {cls: w["mMaxLength"] for cls, w in sorted(assets["wires"].items())},
        }
    )
    return limits


def main() -> int:
    docs = json.loads((DATA / "docs.json").read_text(encoding="utf-8"))
    assets = json.loads((DATA / "assets.json").read_text(encoding="utf-8"))

    for class_name, buildable in docs["buildables"].items():
        buildable["ports"] = assets["ports"].get(class_name, [])
    missing_ports = sorted(set(docs["buildables"]) - set(assets["ports"]))

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    registry = {
        "provenance": {
            "docs": docs["provenance"],
            "assets": assets["provenance"],
            "merged_at_commit": sha,
        },
        "buildables": docs["buildables"],
        "recipes": docs["recipes"],
        "descriptors": docs["descriptors"],
        "build_recipes": docs["build_recipes"],
        "limits": _limits(assets),
    }
    (DATA / "registry.json").write_text(
        json.dumps(registry, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )

    if missing_ports:
        print("buildables with no entry in assets.json:", missing_ports)
    print("limits still None:", [k for k, v in registry["limits"].items() if v is None])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
