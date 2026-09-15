"""Small vector helpers in the game's units (cm, Unreal left-handed axes)."""

from __future__ import annotations

import math

from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import ClearanceBox, Port

__all__ = [
    "box_bounds",
    "distance",
    "placed_box",
    "port_forward",
    "quat_rotate",
    "rotator_axes",
    "snap_zeros",
    "world_port",
]

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


def rotator_axes(rotation: Vector) -> tuple[Vector, Vector, Vector]:
    """``FRotationMatrix``'s three axes for an ``FRotator`` in degrees.

    :attr:`~flab2bp.sfy.registry.ClearanceBox.rotation` is a ``(pitch, yaw,
    roll)`` rotator -- ``flab2bp.sfy.docs.quaternion_to_rotator`` converts the
    game's exported quaternion into one, so that a box's rotation is spelled the
    same way a port's ``RelativeRotation`` is.
    """
    pitch, yaw, roll = (math.radians(angle) for angle in rotation)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    return (
        (cp * cy, cp * sy, sp),
        (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp),
        (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp),
    )


def placed_box(
    box: ClearanceBox, transform: Transform
) -> tuple[Vector, tuple[Vector, Vector, Vector], Vector]:
    """``(centre, axes, half)`` of one clearance box on an actor, in world space.

    **The one place this composition is written.**  An ``FFGClearanceData`` is a
    ``Min``/``Max`` box in the frame of its own ``RelativeTransform``, which is
    itself relative to the actor, so the two transforms compose: the box's scale,
    then its ``(pitch, yaw, roll)`` rotator, then its offset, then the actor's
    rotation and translation.  Getting any step of that in a different order puts
    a box somewhere the game does not, which is why the validator that judges a
    build, the pole placer that dodges boxes and the row builder that measures a
    band all ask here rather than each doing it again.

    ``axes`` are the box's own three unit axes in world space and ``half`` its
    half extent along each of them, so a corner is
    ``centre + sum(+-half[k] * axes[k])``.
    """
    rel = rotator_axes(box.rotation)
    half = tuple((box.max[i] - box.min[i]) / 2.0 * abs(box.scale[i]) for i in range(3))
    mid = tuple((box.max[i] + box.min[i]) / 2.0 * box.scale[i] for i in range(3))
    local = tuple(box.translation[i] + sum(mid[k] * rel[k][i] for k in range(3)) for i in range(3))
    turned = quat_rotate(transform.rotation, (local[0], local[1], local[2]))
    centre = tuple(turned[i] + transform.translation[i] for i in range(3))
    axes = tuple(quat_rotate(transform.rotation, axis) for axis in rel)
    return (
        (centre[0], centre[1], centre[2]),
        (axes[0], axes[1], axes[2]),
        (half[0], half[1], half[2]),
    )


def box_bounds(box: ClearanceBox, transform: Transform) -> tuple[Vector, Vector]:
    """An axis-aligned ``(min, max)`` around one clearance box on an actor.

    :func:`placed_box`'s oriented box, projected onto the world axes -- which is
    what a caller wants when it is measuring a band or asking whether two things
    are anywhere near each other, and never what it wants when it is judging an
    overlap.
    """
    centre, axes, half = placed_box(box, transform)
    reach = [sum(half[k] * abs(axes[k][i]) for k in range(3)) for i in range(3)]
    return (
        (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
        (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
    )
