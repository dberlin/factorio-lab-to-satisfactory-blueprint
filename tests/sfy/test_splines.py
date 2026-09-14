"""The spline shapes the belt hologram builds, as arithmetic on game numbers.

Nothing here reads a blueprint. Every shape is held to the rule or the engine
function it comes from: ``belt.straight_tangents`` and ``belt.curvature`` in
``data/hologram_rules.json``, the bend radius in ``registry.json``, and
``FSplineCurves::GetSegmentLength`` for the arc length.
"""

from __future__ import annotations

import math

from flab2bp.sfy.geometry import port_forward, quat_rotate, snap_zeros
from flab2bp.sfy.layout.splines import (
    QUARTER_TURN_TANGENT,
    SplinePoint,
    Vector,
    concat,
    hermite,
    hermite_tangent,
    incline,
    quarter_turn,
    segment_length,
    spline_length,
    straight,
    yaw_quaternion,
)
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.templates import straight_spline

ORIGIN: Vector = (0.0, 0.0, 0.0)
UNIT_SCALE = (1.0, 1.0, 1.0)


def _length(v: Vector) -> float:
    return math.dist(ORIGIN, v)


def test_a_yaw_quaternion_turns_x_the_way_its_yaw_says() -> None:
    """The quarter turns land exactly on the axes, which is what the game writes."""
    for yaw, expected in (
        (0.0, (1.0, 0.0, 0.0)),
        (90.0, (0.0, 1.0, 0.0)),
        (180.0, (-1.0, 0.0, 0.0)),
        (-90.0, (0.0, -1.0, 0.0)),
    ):
        assert snap_zeros(quat_rotate(yaw_quaternion(yaw), (1.0, 0.0, 0.0))) == expected, yaw


def test_a_yaw_quaternion_turns_a_registry_port_the_way_port_forward_reads_it() -> None:
    """An actor placed at a yaw faces its ports at the port's yaw plus its own.

    The Constructor's ``Output0`` is a yaw of 90 degrees in the registry, so an
    actor turned by ``yaw`` faces it along ``90 + yaw``.
    """
    port = next(
        p for p in load_registry().buildables["Build_ConstructorMk1_C"].ports if p.name == "Output0"
    )
    assert port.rotation == (0.0, 90.0, 0.0)
    for yaw in (0.0, 90.0, 180.0, -90.0):
        total = math.radians(90.0 + yaw)
        expected = (math.cos(total), math.sin(total), 0.0)
        got = port_forward(Transform(yaw_quaternion(yaw), ORIGIN, UNIT_SCALE), port)
        assert all(abs(a - b) < 1e-12 for a, b in zip(got, expected, strict=True)), (yaw, got)


def test_hermite_starts_and_ends_on_its_points_and_tangents() -> None:
    p0: Vector = (10.0, 0.0, 0.0)
    p1: Vector = (410.0, 0.0, 0.0)
    t0: Vector = (200.0, 0.0, 0.0)
    t1: Vector = (0.0, 200.0, 0.0)
    assert hermite(p0, t0, p1, t1, 0.0) == p0
    assert hermite(p0, t0, p1, t1, 1.0) == p1
    assert hermite_tangent(p0, t0, p1, t1, 0.0) == t0
    assert hermite_tangent(p0, t0, p1, t1, 1.0) == t1


def test_a_straight_run_is_the_shape_the_game_builds_moved_to_its_start() -> None:
    """``straight`` is ``templates.straight_spline`` in world coordinates."""
    start: Vector = (10.0, 20.0, 30.0)
    run = straight(start, (0.0, 1.0, 0.0), 400.0)
    expected = [
        ((loc.x, loc.y, loc.z), (arrive.x, arrive.y, arrive.z), (leave.x, leave.y, leave.z))
        for loc, arrive, leave in straight_spline((0.0, 1.0, 0.0), 400.0)
    ]
    moved = [
        ((loc[0] - start[0], loc[1] - start[1], loc[2] - start[2]), arrive, leave)
        for loc, arrive, leave in run
    ]
    assert moved == expected
    assert [round(_length(t), 9) for _, arrive, leave in run for t in (arrive, leave)] == [
        1.0,
        200.0,
        200.0,
        1.0,
    ]


def test_a_straight_run_takes_a_direction_that_is_not_yet_a_unit_vector() -> None:
    """``FSplineBuilder::Start`` normalises the tangent it is handed (0xb22257)."""
    assert straight(ORIGIN, (0.0, 3.0, 0.0), 400.0) == straight(ORIGIN, (0.0, 1.0, 0.0), 400.0)


def test_the_arc_length_of_a_straight_run_is_its_chord() -> None:
    run = straight((5.0, 5.0, 5.0), (1.0, 1.0, 0.0), 400.0)
    assert abs(spline_length(run) - 400.0) < 1e-9
    (p0, _, leave), (p1, arrive, _) = run
    assert abs(segment_length(p0, leave, p1, arrive) - math.dist(p0, p1)) < 1e-9


def test_a_quarter_turn_is_a_quarter_of_a_circle_of_its_radius() -> None:
    run = quarter_turn(ORIGIN, (1.0, 0.0, 0.0), left=True, radius=400.0)
    (start, _, _), (end, _, _) = run
    assert start == ORIGIN
    assert end == (400.0, 400.0, 0.0)
    arc = math.pi / 2 * 400.0
    assert abs(spline_length(run) - arc) < 0.005 * arc


def test_a_quarter_turn_leaves_and_arrives_on_the_headings_it_is_given() -> None:
    run = quarter_turn(ORIGIN, (1.0, 0.0, 0.0), left=False, radius=400.0)
    (start, arrive0, leave0), (end, arrive1, leave1) = run
    assert start == ORIGIN
    assert end == (400.0, -400.0, 0.0)
    assert arrive0 == (1.0, 0.0, 0.0)
    assert leave1 == (0.0, -1.0, 0.0)
    assert leave0 == (QUARTER_TURN_TANGENT * 400.0, 0.0, 0.0)
    assert arrive1 == (0.0, -QUARTER_TURN_TANGENT * 400.0, 0.0)


def test_a_quarter_turn_bends_wider_than_the_curvature_rule_refuses() -> None:
    """Sampled the way ``AFGConveyorBeltHologram::ValidateCurvature`` samples.

    ``n = RoundToInt(length * 0.02)`` samples -- one every 50 cm -- the tangent
    at each, normalised in 2-D, and the radius of curvature between consecutive
    samples is ``step / acos(A . B)``. The rule refuses below
    ``mBendRadius * 1.5 - 15``, which comes out of ``registry.json``'s
    ``belt_bend_radius_cm``; the number is not typed here.
    """
    bend = load_registry().limits.belt_bend_radius_cm
    assert bend is not None
    floor = bend * 1.5 - 15.0
    run = quarter_turn(ORIGIN, (1.0, 0.0, 0.0), left=True, radius=400.0)
    assert _minimum_radius(run) > floor


def test_an_incline_rises_at_the_slope_its_run_and_rise_ask_for() -> None:
    limit = load_registry().limits.belt_max_incline_deg
    assert limit is not None
    run = incline((0.0, 0.0, 200.0), (0.0, 1.0, 0.0), run=500.0, rise=300.0)
    (start, _, _), (end, _, _) = run
    assert start == (0.0, 0.0, 200.0)
    assert end == (0.0, 500.0, 500.0)
    dx, dy, dz = (e - s for s, e in zip(start, end, strict=True))
    slope = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    assert abs(slope - math.degrees(math.atan(300.0 / 500.0))) < 1e-12
    assert slope < limit
    assert abs(spline_length(run) - math.hypot(500.0, 300.0)) < 1e-9


def test_concat_joins_two_runs_and_keeps_the_arrive_and_leave_of_the_seam() -> None:
    first = straight(ORIGIN, (1.0, 0.0, 0.0), 400.0)
    second = quarter_turn((400.0, 0.0, 0.0), (1.0, 0.0, 0.0), left=True, radius=400.0)
    joined = concat(first, second)
    assert len(joined) == 3
    assert [point[0] for point in joined] == [ORIGIN, (400.0, 0.0, 0.0), (800.0, 400.0, 0.0)]
    assert joined[1][1] == first[-1][1]
    assert joined[1][2] == second[0][2]
    assert joined[0] == first[0]
    assert joined[2] == second[-1]


def test_concat_refuses_two_runs_that_do_not_meet() -> None:
    first = straight(ORIGIN, (1.0, 0.0, 0.0), 400.0)
    apart = straight((401.0, 0.0, 0.0), (1.0, 0.0, 0.0), 400.0)
    try:
        concat(first, apart)
    except ValueError as exc:
        assert "1.0" in str(exc) or "gap" in str(exc).lower()
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("concat joined two runs that do not share a point")


def _minimum_radius(points: tuple[SplinePoint, ...]) -> float:
    """The smallest horizontal radius of curvature ``belt.curvature`` would see.

    The rule's own sampling: ``n = RoundToInt(GetSplineLength() * 0.02)``,
    ``step = length / n``, tangents at ``i * step`` and ``(i + 1) * step``
    normalised in 2-D, and ``step / acos(clamp(A . B, -1, 1))`` as the radius.
    Sampling by distance is what ``GetTangentAtDistanceAlongSpline`` does, so
    the arc length is inverted here rather than the parameter swept.
    """
    length = spline_length(points)
    n = math.floor(length * 0.02 + 0.5)
    step = length / n
    smallest = math.inf
    for i in range(n):
        a = _unit_2d(_tangent_at_distance(points, i * step))
        b = _unit_2d(_tangent_at_distance(points, (i + 1) * step))
        dot = min(1.0, max(-1.0, a[0] * b[0] + a[1] * b[1]))
        theta = math.acos(dot)
        if theta > 0.0:
            smallest = min(smallest, step / theta)
    return smallest


def _unit_2d(v: Vector) -> Vector:
    """``FVector::GetSafeNormal2D``: the tangent flattened and normalised."""
    scale = math.hypot(v[0], v[1])
    return (v[0] / scale, v[1] / scale, 0.0)


def _tangent_at_distance(points: tuple[SplinePoint, ...], distance: float) -> Vector:
    """The tangent a given arc length along a one-segment run.

    A dense walk, because the test wants the game's distance sampling and the
    module deliberately offers only the parameter form.
    """
    (p0, _, t0), (p1, t1, _) = points
    steps = 8192
    walked = 0.0
    previous = hermite(p0, t0, p1, t1, 0.0)
    for i in range(1, steps + 1):
        t = i / steps
        current = hermite(p0, t0, p1, t1, t)
        span = math.dist(previous, current)
        if walked + span >= distance:
            fraction = 0.0 if span == 0.0 else (distance - walked) / span
            return hermite_tangent(p0, t0, p1, t1, (i - 1 + fraction) / steps)
        walked += span
        previous = current
    return hermite_tangent(p0, t0, p1, t1, 1.0)
