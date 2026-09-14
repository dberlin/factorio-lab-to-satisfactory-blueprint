"""Describe the fixture corpus. Nothing reads the result.

**This output is statistics, and it is not evidence of anything.** A community
blueprint can carry clipped geometry, a hacked save or a build from an older
game version, so what one contains is not a fact about the game: it is not a
source, not a cross-check and not evidence -- for legality, for a limit, or for
any other registry datum. ``registry.json`` no longer carries these numbers,
``flab2bp.sfy.registry`` no longer loads them, and no validator consults them.

What the game actually refuses is in ``src/flab2bp/sfy/data/hologram_rules.json``
(``scripts/sfy_native_rules.py``), read out of the validators the build gun's
hologram runs. A tightest observed bend of 197 cm says the game accepted 197 cm
in one blueprint somebody built; ``belt.curvature`` says what the hologram
rejects, and only the second of those is a limit.

This is kept as a description of the fixture corpus, which is useful when
choosing fixtures and when a round-trip test disagrees with a file: it says
what shapes the fixtures contain. The corpus is restricted to save version 58
and up, the class family the registry describes, because older blueprints were
built under older rules.

Run it after adding fixtures::

    uv run python scripts/sfy_measure_limits.py

It writes ``src/flab2bp/sfy/data/measured.json`` and prints what it found.
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
from flab2bp.sfy.objects import ObjectData, ObjectHeader
from flab2bp.sfy.properties import Struct, Vector
from flab2bp.sfy.query import connected, find, object_index, spline_points

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "sfy"
OUT = ROOT / "src" / "flab2bp" / "sfy" / "data" / "measured.json"

# Only save version 58 and up: the class family the registry describes, and the
# only one whose geometry was laid down under the limits the registry carries.
CURRENT_SAVE_VERSION = 58

# Samples per spline segment. The curvature of a cubic Hermite segment varies
# along it, so the extreme is found by sampling rather than at the ends; 64 puts
# the sample spacing under 2 cm on the longest belt in the corpus.
SAMPLES = 64

# A segment counts as straight -- no measurable radius -- when the cross product
# that drives the curvature is this close to zero. Belts the player laid in a
# straight line have collinear tangents and a cross product at rounding noise.
STRAIGHT_EPSILON = 1e-6

# Segments shorter than this carry no geometry. Many of the corpus' spline
# segments have two coincident points, which the game writes where a belt was
# split or joined; on such a segment the Hermite curve is a cusp that doubles
# back on itself, and its "radius" is a fraction of a centimetre.
MIN_SEGMENT_CM = 1.0

# Likewise within a segment: where the parametric speed collapses relative to
# the segment's own scale the curve is at a cusp, not a bend.
MIN_SPEED_FRACTION = 0.05

# A belt whose tightest radius is wider than the longest belt the game will
# build is not turning at all, it is a straight run with float noise in its
# tangents. The threshold is the game's own AFGConveyorBeltHologram
# mMaxSplineLength, so that "bends" means belts that actually bend and the
# percentiles of the population mean something.
BEND_CEILING_CM = 5600.1

BELT = "Build_ConveyorBelt"
LIFT = "Build_ConveyorLift"


@dataclass(frozen=True, slots=True)
class Extreme:
    """One measured value and the blueprint object it came from."""

    value: float
    fixture: str
    obj: str
    detail: str


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
    d = (6 * s * s - 6 * s, 3 * s * s - 4 * s + 1, -6 * s * s + 6 * s, 3 * s * s - 2 * s)
    dd = (12 * s - 6, 6 * s - 4, -12 * s + 6, 6 * s - 2)
    first = tuple(d[0] * p0[i] + d[1] * t0[i] + d[2] * p1[i] + d[3] * t1[i] for i in range(3))
    second = tuple(dd[0] * p0[i] + dd[1] * t0[i] + dd[2] * p1[i] + dd[3] * t1[i] for i in range(3))
    return first, second  # type: ignore[return-value]


def _xyz(v: Vector) -> tuple[float, float, float]:
    return (v.x, v.y, v.z)


def _bend_samples(
    header: ObjectHeader, pts: tuple[tuple[Vector, Vector, Vector], ...]
) -> Iterator[tuple[int, float, float]]:
    """``(segment, s, radius_cm)`` wherever one belt's spline actually turns.

    The radius is the curvature of the curve's **horizontal** projection,
    because the game keeps the two constraints apart: ``mBendRadius`` governs
    how tightly a belt may turn and ``mMaxIncline`` how steeply it may climb. A
    full 3D curvature conflates them and reads a steep belt's vertical S-bend as
    a radius far tighter than any turn in the corpus.
    """
    rotation = header.transform.rotation if header.transform else (0.0, 0.0, 0.0, 1.0)
    for segment in range(len(pts) - 1):
        p0, t0 = _xyz(pts[segment][0]), _xyz(pts[segment][2])
        p1, t1 = _xyz(pts[segment + 1][0]), _xyz(pts[segment + 1][1])
        if math.dist(p0, p1) < MIN_SEGMENT_CM:
            continue
        floor = MIN_SPEED_FRACTION * max(math.dist(p0, p1), math.hypot(*t0), math.hypot(*t1))
        # The open interval only. At s=0 and s=1 the spline is at a joint, where
        # the previous point's leave tangent and the next one's arrive tangent
        # need not agree: the second derivative jumps there, and the one-sided
        # curvature is a property of the kink rather than of either arc. Every
        # belt in the corpus that read under 170 cm read it at a joint.
        for step in range(1, SAMPLES):
            s = step / SAMPLES
            first, second = _hermite(p0, t0, p1, t1, s)
            fx, fy, fz = quat_rotate(rotation, first)
            sx, sy, _sz = quat_rotate(rotation, second)
            if math.hypot(fx, fy, fz) <= floor:
                continue
            flat = math.hypot(fx, fy)
            twist = abs(fx * sy - fy * sx)
            if flat <= floor or twist <= STRAIGHT_EPSILON:
                continue
            yield segment, s, flat**3 / twist


def _incline_samples(
    header: ObjectHeader, pts: tuple[tuple[Vector, Vector, Vector], ...]
) -> Iterator[tuple[int, float]]:
    """``(segment, degrees)`` for each of one belt's spline segments.

    The slope of the **chord** between two consecutive spline points -- rise
    over run, in world space -- which is what the game's own incline check is
    about: how steeply the belt climbs from one guide point to the next. The
    tangent of a Hermite segment swings above and below that, so sampling it
    reports a steeper belt than the player was ever allowed to build (39.8
    degrees against a 35-degree limit, on this corpus).
    """
    rotation = header.transform.rotation if header.transform else (0.0, 0.0, 0.0, 1.0)
    for segment in range(len(pts) - 1):
        p0, p1 = _xyz(pts[segment][0]), _xyz(pts[segment + 1][0])
        chord = quat_rotate(rotation, tuple(p1[i] - p0[i] for i in range(3)))  # type: ignore[arg-type]
        run = math.hypot(chord[0], chord[1])
        if math.hypot(*chord) < MIN_SEGMENT_CM:
            continue
        yield segment, math.degrees(math.atan2(abs(chord[2]), run))


def _belt_extremes(
    fixture: str, header: ObjectHeader, pts: tuple[tuple[Vector, Vector, Vector], ...]
) -> tuple[Extreme | None, Extreme | None]:
    """This belt's tightest horizontal bend and its steepest climb."""
    bend: Extreme | None = None
    incline: Extreme | None = None
    segments = len(pts) - 1
    for segment, s, radius in _bend_samples(header, pts):
        if bend is None or radius < bend.value:
            where = f"segment {segment} of {segments} at s={s:.3f}"
            bend = Extreme(radius, fixture, header.name, where)
    for segment, slope in _incline_samples(header, pts):
        if incline is None or slope > incline.value:
            where = f"chord of segment {segment} of {segments}"
            incline = Extreme(slope, fixture, header.name, where)
    return bend, incline


def _lift_height(properties: Any) -> float | None:
    """A conveyor lift's height: the Z of its ``mTopTransform`` translation.

    The lift stores its top as an offset from its own actor transform, negative
    when it runs downwards, so the height is that Z's magnitude. There is no
    fallback path in this corpus: every lift carries ``mTopTransform``.
    """
    top = find(properties, "mTopTransform")
    if not isinstance(top, Struct):
        return None
    translation = next((p.value for p in top.fields if p.tag.name == "Translation"), None)
    return None if not isinstance(translation, Vector) else abs(translation.z)


def _port_direction_evidence(
    index: dict[str, tuple[ObjectHeader, ObjectData]],
    fixture: str,
    data: ObjectData,
    into: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """What one conveyor's wiring says about the directions of the ports it meets.

    ``Buildables/FGBuildableConveyorBase.h:380`` states that a conveyor's
    ``mConnection0`` is its input and ``mConnection1`` its output, and both a
    belt and a lift name those components ``ConveyorAny0``/``ConveyorAny1`` in
    that order. So a port feeding ``ConveyorAny0`` is an output, and one fed by
    ``ConveyorAny1`` is an input -- the direction of a machine port, read off
    the conveyor attached to it.
    """
    for ref in data.components or ():
        component, component_data = index[ref.path]
        peer = connected(component_data)
        if peer is None or peer.path not in index:
            continue
        port, _ = index[peer.path]
        if port.parent is None or port.parent not in index:
            continue
        owner, _ = index[port.parent]
        if owner.class_name.startswith((BELT, LIFT)):
            continue  # the peer's own ends are dynamic; it says nothing
        if component.name.endswith("0"):
            direction = "output"
        elif component.name.endswith("1"):
            direction = "input"
        else:
            continue
        entry = into.setdefault(
            (owner.class_name, port.name),
            {"direction": direction, "links": 0, "fixtures": set(), "conflict": False},
        )
        entry["links"] += 1
        entry["fixtures"].add(fixture)
        if entry["direction"] != direction:
            entry["conflict"] = True


def _spread(population: list[Extreme], low: bool) -> dict[str, Any]:
    """The whole distribution behind an extreme, not just the extreme.

    ``low`` picks which end is the measurement: a bend radius is a minimum and
    an incline a maximum. Both ends are reported either way -- an envelope that
    only ever shows its extreme is how an outlier becomes a limit.
    """
    ordered = sorted(x.value for x in population)
    pick = min(population, key=lambda x: x.value) if low else max(population, key=lambda x: x.value)

    def at(q: float) -> float:
        return round(ordered[int(q * (len(ordered) - 1))], 4)

    return {
        "value": round(pick.value, 4),
        # Not a source and not a cross-check: a description of the fixtures,
        # which nothing in the package reads. What the game refuses is in
        # data/hologram_rules.json.
        "role": "statistics",
        "legality_evidence": False,
        "fixture": pick.fixture,
        "object": pick.obj,
        "detail": pick.detail,
        "min": round(ordered[0], 4),
        "p05": at(0.05),
        "p50": at(0.5),
        "p95": at(0.95),
        "max": round(ordered[-1], 4),
        "n": len(ordered),
    }


def measure() -> dict[str, Any]:
    """Walk the current-family fixtures and return the ``measured.json`` payload."""
    paths = sorted(FIXTURES.glob("*.sbp"))
    used: list[str] = []
    objects = 0
    bends: list[Extreme] = []
    inclines: list[Extreme] = []
    heights: list[Extreme] = []
    vertical: list[Extreme] = []
    directions: dict[tuple[str, str], dict[str, Any]] = {}

    for path in paths:
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < CURRENT_SAVE_VERSION:
            continue
        used.append(path.name)
        index = object_index(bp)
        objects += len(bp.objects)
        for h, d in bp.objects:
            if not h.class_name.startswith((BELT, LIFT)):
                continue
            _port_direction_evidence(index, path.name, d, directions)
            if h.class_name.startswith(BELT):
                pts = spline_points(d)
                if len(pts) < 2:
                    continue
                bend, incline = _belt_extremes(path.name, h, pts)
                if bend is not None and bend.value < BEND_CEILING_CM:
                    bends.append(bend)
                if incline is not None:
                    inclines.append(incline)
            else:
                height = _lift_height(d.properties)
                if height is None or height <= 0:
                    continue
                peers = sorted(_peer_classes(index, d))
                where = f"wired to {', '.join(peers) or 'nothing'}"
                heights.append(Extreme(height, path.name, h.name, where))
                if any(not p.startswith((BELT, LIFT)) for p in peers):
                    vertical.append(heights[-1])

    limits = {
        "belt_bend_radius_cm": _spread(bends, low=True),
        "belt_max_incline_deg": _spread(inclines, low=False),
        "lift_min_cm": _spread(heights, low=True),
        "lift_max_cm": _spread(heights, low=False),
    }
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "provenance": {
            "measured_at_commit": sha,
            "method": (
                "statistics over the current-family fixture corpus. Not evidence: a "
                "blueprint can carry clipped geometry, a hacked save or an older game "
                "version, so nothing here is a source, a cross-check or evidence for "
                "legality or for any registry datum. registry.json does not carry these "
                "numbers and no validator reads them; what the game refuses is in "
                "data/hologram_rules.json"
            ),
            "min_save_version": CURRENT_SAVE_VERSION,
            "samples_per_segment": SAMPLES,
        },
        "corpus": {
            "fixtures_available": len(paths),
            "fixtures_used": len(used),
            "objects": objects,
            "belts_with_a_spline": len(inclines),
            "conveyor_lifts": len(heights),
            "conveyor_lifts_on_a_non_belt_port": len(vertical),
        },
        "limits": limits,
        "port_directions": {
            f"{cls}.{port}": {
                "direction": entry["direction"],
                "links": entry["links"],
                "fixtures": sorted(entry["fixtures"]),
                "conflict": entry["conflict"],
            }
            for (cls, port), entry in sorted(directions.items())
        },
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


def main() -> int:
    payload = measure()
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    corpus = payload["corpus"]
    print(
        f"corpus: {corpus['fixtures_used']} of {corpus['fixtures_available']} fixtures at "
        f"save version {CURRENT_SAVE_VERSION}+, {corpus['objects']} objects, "
        f"{corpus['belts_with_a_spline']} belts, {corpus['conveyor_lifts']} lifts"
    )
    for key, entry in sorted(payload["limits"].items()):
        print(
            f"  {key:<22} {entry['value']:>10}  "
            f"[min {entry['min']}, p05 {entry['p05']}, p50 {entry['p50']}, "
            f"p95 {entry['p95']}, max {entry['max']}, n={entry['n']}]  "
            f"{entry['fixture']} {entry['object']}"
        )
    conflicts = [k for k, v in payload["port_directions"].items() if v["conflict"]]
    print(f"port directions from the corpus: {len(payload['port_directions'])}")
    if conflicts:
        print("  CONFLICTING (a port wired to both belt ends):", conflicts)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
