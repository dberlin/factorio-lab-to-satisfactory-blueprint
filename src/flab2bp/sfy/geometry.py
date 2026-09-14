"""Small vector helpers in the game's units (cm, Unreal left-handed axes)."""

from __future__ import annotations

import math

from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import Port

__all__ = ["distance", "port_forward", "quat_rotate", "snap_zeros", "world_port"]

Vector = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


def quat_rotate(q: Quaternion, v: Vector) -> Vector:
    """Rotate ``v`` by the unit quaternion ``q``, given as ``(x, y, z, w)``."""
    x, y, z, w = q
    vx, vy, vz = v
    # v' = v + 2*w*(q_xyz x v) + 2*(q_xyz x (q_xyz x v))
    cx, cy, cz = (y * vz - z * vy, z * vx - x * vz, x * vy - y * vx)
    dx, dy, dz = (y * cz - z * cy, z * cx - x * cz, x * cy - y * cx)
    return (vx + 2 * (w * cx + dx), vy + 2 * (w * cy + dy), vz + 2 * (w * cz + dz))


def world_port(transform: Transform, port: Port) -> Vector:
    """Where ``port`` sits in world space on an actor placed at ``transform``."""
    sx, sy, sz = transform.scale
    local = (port.translation[0] * sx, port.translation[1] * sy, port.translation[2] * sz)
    r = quat_rotate(transform.rotation, local)
    t = transform.translation
    return (r[0] + t[0], r[1] + t[1], r[2] + t[2])


def port_forward(transform: Transform, port: Port) -> Vector:
    """Which way ``port`` faces in world space on an actor placed at ``transform``.

    :attr:`Port.rotation` is an ``FRotator`` in degrees, in Unreal's own field
    order -- pitch, yaw, roll, straight out of the cooked asset -- and this is
    ``FRotator``'s ``Vector()``: the unit vector the rotation turns ``+X`` into.
    That much is arithmetic on game data. Every belt and pipe port in the
    registry is a pure yaw today; pitch is here because a conveyor lift's ports
    will need it.

    That a belt wired to a port must *leave* along this direction, whichever way
    the items flow, is a modelling assumption this project builds on and not a
    rule read out of the game. The game's own router takes a connection's
    facing as an input -- ``AFGConveyorBeltHologram::AutoRouteSpline`` is
    declared over ``startConnectionNormal`` and ``endConnectionNormal``
    (``Hologram/FGConveyorBeltHologram.h:97``) -- but which vector it hands the
    spline builder is inlined vector code that has not been unpicked, and
    nothing refuses a spline for leaving off-facing: ``ValidateConveyorBelt``
    checks length, minimum length, incline and curvature and none of the four
    looks at the first segment's direction. See the ``belt.straight_tangents``
    rule in ``data/hologram_rules.json``.
    """
    pitch, yaw = math.radians(port.rotation[0]), math.radians(port.rotation[1])
    local = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
    return snap_zeros(quat_rotate(transform.rotation, local))


def snap_zeros(v: Vector, epsilon: float = 1e-9) -> Vector:
    """``v`` with components under ``epsilon`` set to exactly zero.

    A quarter turn through cosine and a quaternion leaves 6.1e-17 where the game
    writes 0.0. That is harmless arithmetically and ugly in a file meant to be
    compared with one the game wrote, so a direction that is meant to be axis
    aligned is made exactly so."""
    return (
        0.0 if abs(v[0]) < epsilon else v[0],
        0.0 if abs(v[1]) < epsilon else v[1],
        0.0 if abs(v[2]) < epsilon else v[2],
    )


def distance(a: Vector, b: Vector) -> float:
    """Euclidean distance in centimetres."""
    return math.dist(a, b)
