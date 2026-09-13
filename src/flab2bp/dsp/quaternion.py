"""Unity quaternion arithmetic shared by the collider extractor and the runtime.

One port of ``Quaternion.LookRotation``, ``Maths.SphericalRotation`` and the
vector helpers around them. Twelve copies of these lived in colliders,
planet, splitter_ports and two extractor scripts; numeric drift between the
extractor and the runtime was invisible because each carried its own.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


def normalize(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return v if n == 0.0 else (v[0] / n, v[1] / n, v[2] / n)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def multiply(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def rotate(q: Quat, v: Vec3) -> Vec3:
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def look_rotation(forward: Vec3, up: Vec3) -> Quat:
    """``Quaternion.LookRotation``; a degenerate right axis falls back to +X."""
    f = normalize(forward)
    r = cross(up, f)
    r = normalize(r) if dot(r, r) > 1e-12 else (1.0, 0.0, 0.0)
    u = cross(f, r)
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


def spherical_rotation(direction: Vec3, yaw_deg: float) -> Quat:
    """``Maths.SphericalRotation``: upright at ``direction``, turned by ``yaw``."""
    p = normalize(direction)
    r = cross(p, (0.0, 1.0, 0.0))
    if dot(r, r) < 1e-4:
        sign = 1.0 if p[1] >= 0.0 else -1.0
        forward: Vec3 = (0.0, 0.0, sign)
    else:
        forward = normalize(cross(normalize(r), p))
    q = look_rotation(forward, p)
    if yaw_deg == 0.0:
        return q
    half = math.radians(yaw_deg) * 0.5
    return multiply(q, (0.0, math.sin(half), 0.0, math.cos(half)))


def angle_deg(a: Quat, b: Quat) -> float:
    """``Quaternion.Angle(a, b)`` in degrees: ``2 * acos(min(|a·b|, 1))``."""
    d = min(abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]), 1.0)
    return math.degrees(2.0 * math.acos(d))
