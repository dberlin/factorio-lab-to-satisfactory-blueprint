"""Measure the hologram limits the game assets do not carry, from the fixtures.

Seven of :class:`flab2bp.sfy.registry.Limits`' fields are native C++ constructor
values: ``AFGConveyorBeltHologram::mBendRadius`` and ``mMaxIncline``, the four
``AFGConveyorLiftHologram`` heights, and ``AFGBuildableHologram::mGridSnapSize``.
Task 10 established that none of them is in a cooked asset, a shipped header or
Docs.json (see ``UNFILLABLE`` in ``scripts/sfy_registry.py``), so the only
honest source left is the corpus: blueprints the game itself wrote, from
geometry the game itself accepted.

What this measures is therefore an *envelope*, not the limit. A tightest bend of
111 cm across the corpus says the game allows at least 111 cm, not that it
forbids 110; a placer that stays inside the envelope is building things players
have already built. Every value is written with the fixture and object that set
it, and the two spline limits with the percentiles of their population as well,
so a number that is really one blueprint's outlier reads as one.

Run it after adding fixtures::

    uv run python scripts/sfy_measure_limits.py

It writes ``src/flab2bp/sfy/data/measured.json`` and prints what it found;
``scripts/sfy_registry.py`` then merges it for any limit the assets and the
headers leave unset.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.geometry import quat_rotate
from flab2bp.sfy.objects import ObjectHeader
from flab2bp.sfy.properties import Struct, Vector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import Registry, load_registry

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "sfy"
OUT = ROOT / "src" / "flab2bp" / "sfy" / "data" / "measured.json"

# Samples per spline segment. The curvature of a cubic Hermite segment varies
# along it, so the extreme is found by sampling rather than at the ends; 64 puts
# the sample spacing under 2 cm on the longest belt in the corpus.
SAMPLES = 64

# A segment counts as straight -- no measurable radius -- when the cross product
# that drives the curvature is this close to zero. Belts the player laid in a
# straight line have collinear tangents and a cross product at rounding noise.
STRAIGHT_EPSILON = 1e-6

# Segments shorter than this carry no geometry. 179 of the corpus' spline
# segments have two coincident points, which the game writes where a belt was
# split or joined; on such a segment the Hermite curve is a cusp that doubles
# back on itself, and its "radius" is a fraction of a centimetre. Measuring
# those would put belt_bend_radius_cm at 0.01 cm and mean nothing.
MIN_SEGMENT_CM = 1.0

# Likewise within a segment: where the parametric speed collapses relative to
# the segment's own scale the curve is at a cusp, not a bend.
MIN_SPEED_FRACTION = 0.05

# The buildable families whose actor origin is on the build grid, named by the
# native class Docs.json gives them. Everything else snaps to something the
# player already placed rather than to the world, and so says nothing about the
# hologram grid: a belt, lift or pipe is dragged between two connections; a
# splitter or merger snaps onto a belt (four of them in power-generation-13 sit
# at x=1529.579, 1740.113, 1931.890 and 1992.433); a pole snaps under a belt, a
# wire between two poles, a sign or a ladder onto a wall. The brief for this
# task named splitters and mergers as grid buildings; the corpus says otherwise
# and the corpus wins.
GRID_NATIVE_CLASSES = frozenset(
    {
        "FGBuildableBeam",
        "FGBuildableFactoryBuilding",
        "FGBuildableFoundationLightweight",
        "FGBuildableGeneratorFuel",
        "FGBuildableManufacturer",
        "FGBuildablePassthrough",
        "FGBuildablePillarLightweight",
        "FGBuildablePowerStorage",
        "FGBuildableRampLightweight",
        "FGBuildableStorage",
        "FGBuildableWallLightweight",
    }
)

# Only a building whose yaw is a whole multiple of the hologram rotation step is
# on the grid at all; the corpus has 502 actors at a free angle (rotated
# foundations at 848.527 = 1200/sqrt(2), diagonal walls at 1131.37 = 800*sqrt(2))
# whose coordinates would drag the divisor to 1 cm.
QUARTER_TURNS = ((0.0, 1.0), (0.5**0.5, 0.5**0.5), (1.0, 0.0), (0.5**0.5, -(0.5**0.5)))
ALIGNMENT_EPSILON = 1e-3

BELT = "Build_ConveyorBelt"
LIFT = "Build_ConveyorLift"


@dataclass(frozen=True, slots=True)
class Extreme:
    """One measured value and the blueprint object it came from."""

    value: float
    fixture: str
    obj: str
    detail: str

    def payload(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 4),
            "source": "measured",
            "fixture": self.fixture,
            "object": self.obj,
            "detail": self.detail,
        }


def _hermite(
    p0: tuple[float, float, float],
    t0: tuple[float, float, float],
    p1: tuple[float, float, float],
    t1: tuple[float, float, float],
    s: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The first and second derivative of one cubic Hermite segment at ``s``.

    ``p0``/``p1`` are the segment's endpoints, ``t0`` the leave tangent of the
    first point and ``t1`` the arrive tangent of the second, which is how
    ``FInterpCurve`` -- and therefore ``mSplineData`` -- stores a spline.
    """
    d = (
        6 * s * s - 6 * s,
        3 * s * s - 4 * s + 1,
        -6 * s * s + 6 * s,
        3 * s * s - 2 * s,
    )
    dd = (12 * s - 6, 6 * s - 4, -12 * s + 6, 6 * s - 2)
    first = tuple(d[0] * p0[i] + d[1] * t0[i] + d[2] * p1[i] + d[3] * t1[i] for i in range(3))
    second = tuple(dd[0] * p0[i] + dd[1] * t0[i] + dd[2] * p1[i] + dd[3] * t1[i] for i in range(3))
    return first, second  # type: ignore[return-value]


def _xyz(v: Vector) -> tuple[float, float, float]:
    return (v.x, v.y, v.z)


def _belt_samples(
    header: ObjectHeader, pts: tuple[tuple[Vector, Vector, Vector], ...]
) -> Iterator[tuple[int, float, float | None, float]]:
    """``(segment, s, radius_cm, incline_deg)`` along one belt's spline.

    Both numbers are taken in world space, and the radius is the curvature of
    the curve's **horizontal** projection. The game keeps the two constraints
    apart -- ``mBendRadius`` governs how tightly a belt may turn and
    ``mMaxIncline`` how steeply it may climb -- and a full 3D curvature
    conflates them: on the two steepest belts in the corpus the vertical S-bend
    reads as a 27 cm radius, well under the 177 cm the tightest real turn shows,
    which would put a nonsense number in the registry.

    ``radius_cm`` is ``None`` on a straight stretch.
    """
    rotation = header.transform.rotation if header.transform else (0.0, 0.0, 0.0, 1.0)
    for segment in range(len(pts) - 1):
        p0, t0 = _xyz(pts[segment][0]), _xyz(pts[segment][2])
        p1, t1 = _xyz(pts[segment + 1][0]), _xyz(pts[segment + 1][1])
        if math.dist(p0, p1) < MIN_SEGMENT_CM:
            continue
        floor = MIN_SPEED_FRACTION * max(math.dist(p0, p1), math.hypot(*t0), math.hypot(*t1))
        for step in range(SAMPLES + 1):
            s = step / SAMPLES
            first, second = _hermite(p0, t0, p1, t1, s)
            fx, fy, fz = quat_rotate(rotation, first)
            sx, sy, _sz = quat_rotate(rotation, second)
            if math.hypot(fx, fy, fz) <= floor:
                continue
            incline = math.degrees(math.atan2(abs(fz), math.hypot(fx, fy)))
            flat = math.hypot(fx, fy)
            twist = abs(fx * sy - fy * sx)
            radius = None if flat <= floor or twist <= STRAIGHT_EPSILON else flat**3 / twist
            yield segment, s, radius, incline


def _belt_extremes(
    fixture: str, header: ObjectHeader, pts: tuple[tuple[Vector, Vector, Vector], ...]
) -> tuple[Extreme | None, Extreme | None]:
    """This belt's tightest horizontal bend and its steepest climb."""
    bend: Extreme | None = None
    incline: Extreme | None = None
    for segment, s, radius, slope in _belt_samples(header, pts):
        where = f"segment {segment} of {len(pts) - 1} at s={s:.3f}"
        if radius is not None and (bend is None or radius < bend.value):
            bend = Extreme(radius, fixture, header.name, where)
        if incline is None or slope > incline.value:
            incline = Extreme(slope, fixture, header.name, where)
    return bend, incline


def _lift_height(properties: Any) -> float | None:
    """A conveyor lift's height: the Z of its ``mTopTransform`` translation.

    The lift stores its top as an offset from its own actor transform, negative
    when it runs downwards, so the height is that Z's magnitude. There is no
    fallback path in this corpus: all 87 lifts carry ``mTopTransform``.
    """
    top = find(properties, "mTopTransform")
    if not isinstance(top, Struct):
        return None
    translation = next((p.value for p in top.fields if p.tag.name == "Translation"), None)
    return None if not isinstance(translation, Vector) else abs(translation.z)


def _gcd_cm(samples: list[tuple[float, str, str]]) -> tuple[int, Extreme | None]:
    """The gcd of ``samples`` in whole centimetres, and the sample that set it.

    ``samples`` are ``(value, fixture, label)``. The witness is the last sample
    that pushed the running gcd down, which is the one to look at when the
    answer is not the number you expected.
    """
    out = 0
    witness: Extreme | None = None
    for value, fixture, label in samples:
        step = math.gcd(out, abs(round(value)))
        if step != out:
            out = step
            witness = Extreme(float(step), fixture, label, f"reduced the gcd to {step} cm")
    return out, witness


def measure() -> dict[str, Any]:
    """Walk every fixture and return the ``measured.json`` payload."""
    paths = sorted(FIXTURES.glob("*.sbp"))
    reg = load_registry()
    objects = belts = 0
    bends: list[Extreme] = []
    inclines: list[Extreme] = []
    heights: list[Extreme] = []
    vertical: list[Extreme] = []
    grid: list[tuple[float, str, str]] = []
    grid_actors = 0

    for path in paths:
        bp = read_sbp(path.read_bytes())
        index = object_index(bp)
        objects += len(bp.objects)
        for h, d in bp.objects:
            if h.class_name.startswith(BELT):
                pts = spline_points(d)
                if len(pts) >= 2:
                    belts += 1
                    bend, incline = _belt_extremes(path.name, h, pts)
                    if bend is not None:
                        bends.append(bend)
                    if incline is not None:
                        inclines.append(incline)
            elif h.class_name.startswith(LIFT):
                height = _lift_height(d.properties)
                if height is not None and height > 0:
                    peers = sorted(_peer_classes(index, d))
                    where = f"wired to {', '.join(peers)}"
                    heights.append(Extreme(height, path.name, h.name, where))
                    if any(not p.startswith((BELT, LIFT)) for p in peers):
                        vertical.append(heights[-1])
            elif _is_grid_actor(reg, h):
                grid_actors += 1
                assert h.transform is not None
                for axis, v in zip("xyz", h.transform.translation, strict=True):
                    grid.append((v, path.name, f"{h.name}.{axis}"))

    limits: dict[str, Any] = {}
    limits["belt_bend_radius_cm"] = _spread_payload(
        _pick(bends, min), bends, "belts", "no belt in the corpus has a curved segment"
    )
    limits["belt_max_incline_deg"] = _spread_payload(
        _pick(inclines, max), inclines, "belts", "no belt in the corpus has a spline"
    )
    limits["lift_min_cm"] = _extreme_payload(
        _pick(heights, min), "the corpus has no conveyor lifts"
    )
    limits["lift_max_cm"] = _extreme_payload(
        _pick(heights, max), "the corpus has no conveyor lifts"
    )
    limits["lift_min_vertical_cm"] = _extreme_payload(
        _pick(vertical, min),
        "no conveyor lift in the corpus has an end wired straight to a non-belt port",
    )
    step, step_at = _gcd_cm([(x.value, x.fixture, x.obj) for x in heights])
    limits["lift_step_cm"] = _gcd_payload(
        step,
        step_at,
        f"gcd of {len(heights)} lift heights, {len({round(x.value) for x in heights})} distinct",
        "the corpus has no conveyor lifts",
    )
    snap, snap_at = _gcd_cm(grid)
    limits["hologram_grid_cm"] = _gcd_payload(
        snap,
        snap_at,
        f"gcd of {len(grid)} translation coordinates on {grid_actors} on-grid actors",
        "no on-grid actor in the corpus",
    )

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "provenance": {
            "measured_at_commit": sha,
            "method": "envelope over the committed blueprint corpus; see the module docstring",
            "samples_per_segment": SAMPLES,
        },
        "corpus": {
            "fixtures": len(paths),
            "objects": objects,
            "belts_with_a_spline": belts,
            "conveyor_lifts": len(heights),
            "on_grid_actors": grid_actors,
        },
        "limits": limits,
    }


def _peer_classes(index: dict[str, tuple[ObjectHeader, Any]], data: Any) -> set[str]:
    """The classes of the actors this object's connections are wired to."""
    out: set[str] = set()
    for ref in data.components or ():
        ch, cd = index[ref.path]
        if not ch.class_path.endswith("FGFactoryConnectionComponent"):
            continue
        peer = connected(cd)
        if peer is None or peer.path not in index:
            continue
        parent = index[peer.path][0].parent
        if parent is not None and parent in index:
            out.add(index[parent][0].class_name)
    return out


def _is_grid_actor(reg: Registry, header: ObjectHeader) -> bool:
    """Whether this actor was placed on the world grid rather than snapped to something."""
    if header.transform is None:
        return False
    buildable = reg.buildables.get(header.class_name)
    if buildable is None:
        return False
    if buildable.native_class.rsplit(".", 1)[-1] not in GRID_NATIVE_CLASSES:
        return False
    x, y, z, w = header.transform.rotation
    if abs(x) > ALIGNMENT_EPSILON or abs(y) > ALIGNMENT_EPSILON:
        return False  # tilted out of the horizontal plane
    return any(
        abs(abs(z) - a) < ALIGNMENT_EPSILON and abs(abs(w) - b) < ALIGNMENT_EPSILON
        for a, b in QUARTER_TURNS
    )


def _pick(candidates: list[Extreme], which: Any) -> Extreme | None:
    """``min``/``max`` of ``candidates`` by value, or ``None`` when there are none."""
    return which(candidates, key=lambda x: x.value, default=None)


def _extreme_payload(extreme: Extreme | None, reason: str) -> dict[str, Any]:
    if extreme is None:
        return {"value": None, "source": "measured", "reason": reason}
    return extreme.payload()


def _spread_payload(
    extreme: Extreme | None, population: list[Extreme], unit: str, reason: str
) -> dict[str, Any]:
    """An extreme, plus where the rest of the population sits.

    The belt limits have a long thin tail -- the tightest bend in the corpus is
    111 cm and the fifth percentile is 196 -- so the extreme on its own reads as
    a much looser limit than the corpus actually supports. The percentiles say
    so in the file instead of only in a commit message.
    """
    if extreme is None:
        return {"value": None, "source": "measured", "reason": reason}
    ordered = sorted(x.value for x in population)

    def at(q: float) -> float:
        return round(ordered[int(q * (len(ordered) - 1))], 4)

    return extreme.payload() | {
        "population": f"{len(ordered)} {unit}",
        "p05": at(0.05),
        "p50": at(0.5),
        "p95": at(0.95),
    }


def _gcd_payload(step: int, at: Extreme | None, detail: str, reason: str) -> dict[str, Any]:
    if not step or at is None:
        return {"value": None, "source": "measured", "reason": reason}
    return {
        "value": float(step),
        "source": "measured",
        "fixture": at.fixture,
        "object": at.obj,
        "detail": f"{detail}; {at.detail}",
    }


def main() -> int:
    payload = measure()
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    corpus = payload["corpus"]
    print(
        f"corpus: {corpus['fixtures']} fixtures, {corpus['objects']} objects, "
        f"{corpus['belts_with_a_spline']} belts, {corpus['conveyor_lifts']} lifts, "
        f"{corpus['on_grid_actors']} on-grid actors"
    )
    for key, entry in sorted(payload["limits"].items()):
        tail = entry.get("reason") or f"{entry['fixture']} {entry['object']} -- {entry['detail']}"
        print(f"  {key:<24} {str(entry['value']):>10}  {tail}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
