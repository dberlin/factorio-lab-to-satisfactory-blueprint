"""Small vector helpers in the game's units (cm, Unreal left-handed axes)."""

from __future__ import annotations

import math

from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import Port

__all__ = ["distance", "port_forward", "quat_rotate", "world_port"]

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

    A port faces out of the machine, so a belt wired to it runs away along this
    direction whichever way the items flow -- measured over 1119 belt-to-machine
    links in the corpus, inputs and outputs alike, by
    ``tests/sfy/test_geometry.py``.

    :attr:`Port.rotation` is an ``FRotator`` in degrees, in Unreal's own field
    order -- pitch, yaw, roll -- and this is ``FRotator``'s
    ``Vector()``: the unit vector the rotation turns ``+X`` into. Every belt and
    pipe port in the registry is a pure yaw, so the corpus exercises the yaw
    term alone; pitch is here because a conveyor lift's ports will need it.
    """
    pitch, yaw = math.radians(port.rotation[0]), math.radians(port.rotation[1])
    local = (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))
    return quat_rotate(transform.rotation, local)


def distance(a: Vector, b: Vector) -> float:
    """Euclidean distance in centimetres."""
    return math.dist(a, b)
