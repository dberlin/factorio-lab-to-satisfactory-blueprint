"""Port facing: the arithmetic of ``geometry.port_forward``, on synthetic values.

Both tests are the arithmetic: an ``FRotator``'s ``Vector()`` composed with an
actor's quaternion. Nothing here reads a blueprint -- what a blueprint contains
is never evidence about the game.

What a belt does at the port it leaves is a game fact, and it is read from the
game rather than from files: ``data/hologram_rules.json``'s ``belt.straight_tangents``
records what ``AFGConveyorBeltHologram::AutoRouteSpline`` builds.
"""

from __future__ import annotations

import math
from dataclasses import replace

from flab2bp.sfy.geometry import port_forward
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import Port, load_registry

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def _port(yaw: float, pitch: float = 0.0) -> Port:
    """A real registry port turned to face a given way.

    Varying one the registry loaded, rather than constructing one field by
    field, keeps this test off the `Port` constructor as the extractor grows it.
    """
    template = load_registry().buildables["Build_ConstructorMk1_C"].ports[0]
    return replace(template, translation=(0.0, 0.0, 0.0), rotation=(pitch, yaw, 0.0))


def test_port_forward_turns_x_by_the_ports_yaw() -> None:
    for yaw, expected in (
        (0.0, (1.0, 0.0, 0.0)),
        (90.0, (0.0, 1.0, 0.0)),
        (180.0, (-1.0, 0.0, 0.0)),
        (-90.0, (0.0, -1.0, 0.0)),
    ):
        # Exactly, not approximately: a quarter turn's 6.1e-17 is snapped away so
        # an axis-aligned belt gets the same zeros the game writes.
        assert port_forward(IDENTITY, _port(yaw)) == expected, yaw
    assert port_forward(IDENTITY, _port(0.0, pitch=90.0)) == (0.0, 0.0, 1.0)


def test_port_forward_composes_with_the_actors_rotation() -> None:
    """An actor turned a quarter turn turns its ports with it."""
    quarter = Transform((0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)), (0.0, 0.0, 0.0), (1, 1, 1))
    got = port_forward(quarter, _port(0.0))
    assert all(abs(a - b) < 1e-6 for a, b in zip(got, (0.0, 1.0, 0.0), strict=True)), got
