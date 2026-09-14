"""The spline shapes the game builds, in the game's own arithmetic.

A conveyor is a spline, not a line: ``mSplineData`` is a list of
``SplinePointData``, each a location and the two tangents the curve arrives on
and leaves by, and the game interpolates between consecutive points with a cubic
Hermite. Everything here builds or measures that, and every shape is one the
game's own builder makes:

* a straight run is ``FSplineUtils::BuildStraightSpline2D`` (and its 3-D twin)
  through ``FSplineBuilder``, which is what ``AFGConveyorBeltHologram::AutoRouteSpline``
  calls -- the ``belt.straight_tangents`` rule in ``data/hologram_rules.json``
  records the disassembly and :func:`flab2bp.sfy.templates.straight_spline`
  builds the two points of one;
* a quarter turn is ``FSplineBuilder::AddSegment``'s shape, with the tangent
  length that makes a cubic Hermite a quarter circle (see
  :data:`QUARTER_TURN_TANGENT`);
* the arc length is ``FSplineCurves::GetSegmentLength``, Unreal's own five-point
  Legendre-Gauss quadrature of the segment's speed
  (``Engine/Source/Runtime/Engine/Private/Components/SplineComponent.cpp``),
  which is what ``USplineComponent::GetSplineLength`` sums and what
  ``belt.curvature``, ``belt.max_length`` and ``AFGBuildable::GetCostMultiplierForLength``
  all measure a belt with.

Nothing here decides whether a shape may be built. A turn tighter than the
hologram accepts is built and measured just the same; refusing it is the
validator's job, and the numbers it refuses by come from ``registry.json``'s
``limits``, not from this module.

Distances are centimetres and the axes are Unreal's, left-handed, with ``+Z`` up.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from flab2bp.sfy.geometry import snap_zeros
from flab2bp.sfy.templates import straight_spline

__all__ = [
    "GAUSS_LEGENDRE_5",
    "QUARTER_TURN_TANGENT",
    "Quaternion",
    "SplinePoint",
    "Vector",
    "concat",
    "hermite",
    "hermite_tangent",
    "incline",
    "quarter_turn",
    "segment_length",
    "spline_length",
    "straight",
    "yaw_quaternion",
]

Vector = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]
SplinePoint = tuple[Vector, Vector, Vector]
"""One ``SplinePointData``: location, arrive tangent, leave tangent."""

JOIN_TOLERANCE_CM = 1e-6
"""How close two runs' shared point must be for :func:`concat` to join them."""

QUARTER_TURN_TANGENT = 4.0 * (math.sqrt(2.0) - 1.0)
"""The tangent length, as a multiple of the radius, that bends a cubic segment
into a quarter circle.

A cubic Bezier approximates a quarter circle when its control points sit
``c = 4/3 * (sqrt(2) - 1)`` radii along the two headings; a Hermite's tangent is
three times that control offset, so the tangent is ``4 * (sqrt(2) - 1)``
radii, 1.65685. The approximation is the standard one: its radius wanders by
under 0.03 % of the nominal, which is far inside anything ``belt.curvature``
measures.
"""

GAUSS_LEGENDRE_5: tuple[tuple[float, float], ...] = (
    (0.0, 128.0 / 225.0),
    (-math.sqrt(5.0 - 2.0 * math.sqrt(10.0 / 7.0)) / 3.0, (322.0 + 13.0 * math.sqrt(70.0)) / 900.0),
    (math.sqrt(5.0 - 2.0 * math.sqrt(10.0 / 7.0)) / 3.0, (322.0 + 13.0 * math.sqrt(70.0)) / 900.0),
    (-math.sqrt(5.0 + 2.0 * math.sqrt(10.0 / 7.0)) / 3.0, (322.0 - 13.0 * math.sqrt(70.0)) / 900.0),
    (math.sqrt(5.0 + 2.0 * math.sqrt(10.0 / 7.0)) / 3.0, (322.0 - 13.0 * math.sqrt(70.0)) / 900.0),
)
"""The five ``(sample, weight)`` pairs of ``GetSegmentLength``'s quadrature.

``FSplineCurves::GetSegmentLength`` ships them rounded to seven digits, which is
all a ``float`` carries; they are computed here at full precision so that a
straight run's arc length comes out as its chord exactly rather than to a part
in ten million.
"""


def yaw_quaternion(yaw_deg: float) -> Quaternion:
    """The rotation of an object turned ``yaw_deg`` degrees about ``+Z``.

    An actor's rotation in the object table is an ``FQuat`` written ``(x, y, z, w)``
    (``objects.read_toc``), and a buildable that stands on the ground is turned
    about ``Z`` alone -- the build gun rotates a hologram in steps about the up
    axis, which is the ``buildable.rotation_step`` rule.

    The components go through :func:`~flab2bp.sfy.geometry.snap_zeros` for the
    reason that function gives: a quarter turn through a cosine leaves 6.1e-17
    where the game writes 0.0, and this file is meant to be comparable with one
    the game wrote. ``snap_zeros`` takes a three-vector, so ``w`` rides through
    it in the first slot.
    """
    half = math.radians(yaw_deg) / 2.0
    x, y, z = snap_zeros((0.0, 0.0, math.sin(half)))
    w = snap_zeros((math.cos(half), 0.0, 0.0))[0]
    return (x, y, z, w)


def hermite(p0: Vector, t0: Vector, p1: Vector, t1: Vector, t: float) -> Vector:
    """A point on the cubic Hermite segment between two spline points.

    ``P(t) = (2t^3 - 3t^2 + 1) P0 + (t^3 - 2t^2 + t) T0 + (-2t^3 + 3t^2) P1 + (t^3 - t^2) T1``
    -- Unreal's ``FInterpCurve``/``USplineComponent`` interpolation, which is what
    a conveyor's ``mSplineData`` is read as. ``T0`` is the *leave* tangent of the
    first point and ``T1`` the *arrive* tangent of the second, which is how
    ``FSplineBuilder`` fills the pair in (see the ``belt.straight_tangents``
    rule).
    """
    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2
    return (
        h00 * p0[0] + h10 * t0[0] + h01 * p1[0] + h11 * t1[0],
        h00 * p0[1] + h10 * t0[1] + h01 * p1[1] + h11 * t1[1],
        h00 * p0[2] + h10 * t0[2] + h01 * p1[2] + h11 * t1[2],
    )


def hermite_tangent(p0: Vector, t0: Vector, p1: Vector, t1: Vector, t: float) -> Vector:
    """The derivative of :func:`hermite` at ``t``: the segment's velocity there.

    ``P'(t) = (6t^2 - 6t) P0 + (3t^2 - 4t + 1) T0 + (-6t^2 + 6t) P1 + (3t^2 - 2t) T1``,
    which is what ``USplineComponent::GetTangentAtDistanceAlongSpline`` returns
    at the matching distance and what ``belt.curvature`` measures the bend with.
    """
    t2 = t * t
    d00 = 6.0 * t2 - 6.0 * t
    d10 = 3.0 * t2 - 4.0 * t + 1.0
    d01 = -6.0 * t2 + 6.0 * t
    d11 = 3.0 * t2 - 2.0 * t
    return (
        d00 * p0[0] + d10 * t0[0] + d01 * p1[0] + d11 * t1[0],
        d00 * p0[1] + d10 * t0[1] + d01 * p1[1] + d11 * t1[1],
        d00 * p0[2] + d10 * t0[2] + d01 * p1[2] + d11 * t1[2],
    )


def segment_length(p0: Vector, t0: Vector, p1: Vector, t1: Vector) -> float:
    """How long one segment is, the way the engine computes it.

    ``FSplineCurves::GetSegmentLength``
    (``Engine/Source/Runtime/Engine/Private/Components/SplineComponent.cpp``)
    integrates the speed ``|P'(t)|`` over the segment with a five-point
    Legendre-Gauss quadrature, and ``USplineComponent::GetSplineLength`` is the
    sum of those. Five points integrate a polynomial of degree nine exactly, and
    a straight run's speed is a quadratic, so a straight segment comes out at
    its chord to the last bit.
    """
    total = 0.0
    for sample, weight in GAUSS_LEGENDRE_5:
        velocity = hermite_tangent(p0, t0, p1, t1, 0.5 * (1.0 + sample))
        total += weight * math.sqrt(
            velocity[0] * velocity[0] + velocity[1] * velocity[1] + velocity[2] * velocity[2]
        )
    return 0.5 * total


def spline_length(points: Sequence[SplinePoint]) -> float:
    """The arc length of a whole spline: every segment, summed.

    This is ``GetSplineLength``, which is the length ``belt.max_length``
    compares against and the one ``AFGBuildable::GetCostMultiplierForLength``
    divides. It is not the polyline ``belt.min_length`` sums -- that rule adds
    chords -- and the two differ on a curve.
    """
    return sum(
        segment_length(points[i][0], points[i][2], points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )


def straight(start: Vector, direction: Vector, length: float) -> tuple[SplinePoint, ...]:
    """A straight run of ``length`` from ``start``, in world coordinates.

    The shape is :func:`flab2bp.sfy.templates.straight_spline`'s, which is
    ``AutoRouteSpline``'s through ``FSplineUtils::BuildStraightSpline2D``: unit
    tangents on the outside, inner tangents of ``clamp(length / 2, 50, 600)`` --
    see the ``belt.straight_tangents`` rule. The only difference is the frame:
    the points here are where the run actually is, and
    :meth:`~flab2bp.sfy.layout.model.BeltRun.local_points` puts them back into
    the belt actor's own frame for ``mSplineData``.

    ``direction`` need not arrive as a unit vector: ``FSplineBuilder::Start``
    normalises the tangent it is handed (``0xb22257``), so this does too. A zero
    direction is refused rather than divided by.

    The tangents are not recomputed here. ``straight_spline`` is where this
    project states what the game's straight builder does, clamp and all, and
    this moves its two points to where the run is.
    """
    unit = _unit(direction)
    return tuple(
        (
            (start[0] + loc.x, start[1] + loc.y, start[2] + loc.z),
            (arrive.x, arrive.y, arrive.z),
            (leave.x, leave.y, leave.z),
        )
        for loc, arrive, leave in straight_spline(unit, length)
    )


def quarter_turn(
    start: Vector, heading: Vector, left: bool, radius: float
) -> tuple[SplinePoint, ...]:
    """A horizontal quarter circle of ``radius``, entered along ``heading``.

    Two points, the way ``FSplineBuilder::AddSegment`` (``0xaf1e80``) writes a
    segment: the first point leaves along the incoming heading and the second
    arrives along the outgoing one, both tangents
    :data:`QUARTER_TURN_TANGENT` radii long, with the unit headings on the
    outside -- ``Start`` puts a unit vector in point 0's arrive tangent and
    ``AddSegment`` gives the new point its unit direction to leave by (the
    ``belt.straight_tangents`` rule records both).

    The turn is horizontal, which is the plane ``belt.curvature`` measures in:
    it normalises every tangent with ``GetSafeNormal2D`` before taking the angle
    between two of them. ``left`` turns towards ``+Z`` cross the heading, which
    in Unreal's left-handed axes is the heading rotated by a positive yaw.
    """
    unit = _unit((heading[0], heading[1], 0.0))
    side: Vector = (-unit[1], unit[0], 0.0) if left else (unit[1], -unit[0], 0.0)
    end = _offset(_offset(start, unit, radius), side, radius)
    pull = QUARTER_TURN_TANGENT * radius
    return ((start, unit, _scale(unit, pull)), (end, _scale(side, pull), side))


def incline(start: Vector, heading: Vector, run: float, rise: float) -> tuple[SplinePoint, ...]:
    """A straight run that climbs ``rise`` over a horizontal ``run``.

    The same shape as :func:`straight` -- ``FSplineUtils::BuildStraightSpline3D``
    is the 2-D builder's twin and clamps the same way (``0xafd2a5`` ``mulsd 0.5``,
    ``0xafd2b5`` ``maxsd 50.0``), and the tangent length is taken on the full
    three-dimensional distance, which is why ``STRAIGHT_TANGENT_HALF`` is halved
    against ``sqrt(dz^2 + dxy^2)``.

    How steep a belt may be is ``belt.incline``'s business
    (``limits.belt_max_incline_deg``), and this does not check it: the caller
    keeps ``rise / run`` inside the limit and the validator holds it to that.
    """
    horizontal = _unit((heading[0], heading[1], 0.0))
    direction = (horizontal[0] * run, horizontal[1] * run, rise)
    return straight(start, direction, math.hypot(run, rise))


def concat(*runs: Sequence[SplinePoint]) -> tuple[SplinePoint, ...]:
    """Join runs that share an end point into one spline.

    The seam keeps the arrive tangent of the run that ends there and the leave
    tangent of the one that starts there, which is what a spline point is: one
    location with the curve's two sides written beside it. The shared points
    must be within :data:`JOIN_TOLERANCE_CM`; a gap is refused rather than
    bridged with a segment nobody asked for.
    """
    if not runs:
        return ()
    joined: list[SplinePoint] = list(runs[0])
    for run in runs[1:]:
        if not run:
            continue
        if not joined:
            joined = list(run)
            continue
        gap = math.dist(joined[-1][0], run[0][0])
        if gap > JOIN_TOLERANCE_CM:
            raise ValueError(
                f"these runs do not meet: {joined[-1][0]} and {run[0][0]} are {gap} cm apart"
            )
        joined[-1] = (joined[-1][0], joined[-1][1], run[0][2])
        joined.extend(run[1:])
    return tuple(joined)


def _unit(v: Vector) -> Vector:
    scale = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if scale == 0.0:
        raise ValueError("a run needs a direction; this one has no length to normalise")
    return snap_zeros((v[0] / scale, v[1] / scale, v[2] / scale))


def _scale(v: Vector, by: float) -> Vector:
    return (v[0] * by, v[1] * by, v[2] * by)


def _offset(point: Vector, direction: Vector, distance: float) -> Vector:
    return (
        point[0] + direction[0] * distance,
        point[1] + direction[1] * distance,
        point[2] + direction[2] * distance,
    )
