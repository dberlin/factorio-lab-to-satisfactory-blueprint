from __future__ import annotations

import math
import random

from flab2bp.dsp import quaternion as q

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


# Reference: flab2bp/dsp/colliders.py at 9c958bc9 (Unity port used by the collider extractor).
def _c_norm(v: Vec3) -> Vec3:
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m)


def _c_cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _c_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _c_qmul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _c_qrot(qq: Quat, v: Vec3) -> Vec3:
    x, y, z, w = qq
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def _c_look_rotation(forward: Vec3, up: Vec3) -> Quat:
    f = _c_norm(forward)
    r = _c_cross(up, f)
    r = _c_norm(r) if _c_dot(r, r) > 1e-12 else (1.0, 0.0, 0.0)
    u = _c_cross(f, r)
    m00, m01, m02 = r[0], u[0], f[0]
    m10, m11, m12 = r[1], u[1], f[1]
    m20, m21, m22 = r[2], u[2], f[2]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def _c_spherical_rotation(pos: Vec3, angle_deg: float) -> Quat:
    p = _c_norm(pos)
    r = _c_cross(p, (0.0, 1.0, 0.0))
    if _c_dot(r, r) < 1e-4:
        sign = 1.0 if p[1] >= 0.0 else -1.0
        r = (sign, 0.0, 0.0)
        forward = (0.0, 0.0, sign)
    else:
        r = _c_norm(r)
        forward = _c_norm(_c_cross(r, p))
    out = _c_look_rotation(forward, p)
    if angle_deg == 0.0:
        return out
    h = math.radians(angle_deg) * 0.5
    return _c_qmul(out, (0.0, math.sin(h), 0.0, math.cos(h)))


# Reference: flab2bp/dsp/planet.py at 9c958bc9 (the runtime's own port, transposed naming).
def _p_qmul(a: Quat, b: Quat) -> Quat:
    """Planet's summation order, which differs term for term from the collider port.

    On general quaternions the two disagree in the last ulp on about two thirds
    of random pairs.  They agree exactly on the only product planet.py's port
    ever forms -- a yaw about +Y, whose ``x`` and ``z`` are zero -- which is what
    :func:`test_multiply_is_bit_exact_against_planets_order_for_a_yaw_about_up`
    pins directly.  colliders.py's port forms three other products besides this
    yaw, but :mod:`flab2bp.dsp.quaternion` takes colliders' term order verbatim,
    so exactness against the collider port holds regardless of which product is
    formed.
    """
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by + ay * bw + az * bx - ax * bz,
        aw * bz + az * bw + ax * by - ay * bx,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _p_look_rotation(forward: Vec3, up: Vec3) -> Quat:
    f = _c_norm(forward)
    r = _c_norm(_c_cross(up, f))
    u = _c_cross(f, r)
    m00, m01, m02 = r
    m10, m11, m12 = u
    m20, m21, m22 = f
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return ((m12 - m21) / s, (m20 - m02) / s, (m01 - m10) / s, s * 0.25)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (s * 0.25, (m10 + m01) / s, (m20 + m02) / s, (m12 - m21) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m10 + m01) / s, s * 0.25, (m21 + m12) / s, (m20 - m02) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m20 + m02) / s, (m21 + m12) / s, s * 0.25, (m01 - m10) / s)


def _p_spherical_rotation(direction: Vec3, yaw_deg: float) -> Quat:
    p = _c_norm(direction)
    r = _c_cross(p, (0.0, 1.0, 0.0))
    if _c_dot(r, r) < 1e-4:
        sign = 1.0 if p[1] >= 0.0 else -1.0
        forward = (0.0, 0.0, sign)
    else:
        forward = _c_norm(_c_cross(_c_norm(r), p))
    out = _p_look_rotation(forward, p)
    if yaw_deg == 0.0:
        return out
    half = math.radians(yaw_deg) * 0.5
    return _p_qmul(out, (0.0, math.sin(half), 0.0, math.cos(half)))


def _close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-9) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _random_vec(rng: random.Random) -> Vec3:
    return (rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-3, 3))


def _random_quat(rng: random.Random) -> Quat:
    raw = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
    n = math.sqrt(sum(c * c for c in raw))
    return (raw[0] / n, raw[1] / n, raw[2] / n, raw[3] / n)


def test_multiply_rotate_cross_match_the_collider_port() -> None:
    rng = random.Random(20260913)
    for _ in range(500):
        a, b, v = _random_quat(rng), _random_quat(rng), _random_vec(rng)
        assert _close(q.multiply(a, b), _c_qmul(a, b))
        assert _close(q.rotate(a, v), _c_qrot(a, v))
    for _ in range(200):
        v, w = _random_vec(rng), _random_vec(rng)
        assert _close(q.cross(v, w), _c_cross(v, w))
        assert abs(q.dot(v, w) - _c_dot(v, w)) <= 1e-9


def test_multiply_is_bit_exact_against_planets_order_for_a_yaw_about_up() -> None:
    """The only product planet.py's port ever forms, so the merge loses no bit of
    planet's port.  colliders.py's port forms other products too, but the shared
    module matches colliders' term order verbatim, so exactness there does not
    depend on this."""
    rng = random.Random(4242)
    for _ in range(2000):
        a = _random_quat(rng)
        half = math.radians(rng.uniform(-360.0, 360.0)) * 0.5
        yaw = (0.0, math.sin(half), 0.0, math.cos(half))
        assert q.multiply(a, yaw) == _p_qmul(a, yaw)


def test_look_rotation_matches_both_ports_away_from_the_degenerate_axis() -> None:
    rng = random.Random(7)
    for _ in range(500):
        forward, up = _random_vec(rng), _random_vec(rng)
        r = _c_cross(up, forward)
        if _c_dot(r, r) <= 1e-6:
            continue
        got = q.look_rotation(forward, up)
        assert _close(got, _c_look_rotation(forward, up))
        assert _close(got, _p_look_rotation(forward, up))


def test_look_rotation_keeps_the_collider_guard_when_up_is_parallel_to_forward() -> None:
    forward, up = (0.0, 1.0, 0.0), (0.0, 2.0, 0.0)
    assert _close(q.look_rotation(forward, up), _c_look_rotation(forward, up))


def test_spherical_rotation_matches_both_ports_including_the_poles() -> None:
    rng = random.Random(11)
    cases = [_random_vec(rng) for _ in range(300)] + [
        (0.0, 1.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 1.0, 1e-3),
    ]
    for direction in cases:
        for yaw in (0.0, 90.0, -33.5, 180.0):
            got = q.spherical_rotation(direction, yaw)
            assert _close(got, _c_spherical_rotation(direction, yaw))
            assert _close(got, _p_spherical_rotation(direction, yaw))


def test_normalize_leaves_the_zero_vector_alone() -> None:
    assert q.normalize((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert _close(q.normalize((0.0, 3.0, 4.0)), (0.0, 0.6, 0.8))
