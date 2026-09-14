"""Port facing: what ``geometry.port_forward`` says, against what the corpus does."""

from __future__ import annotations

import math
from dataclasses import replace

from flab2bp.sfy.codec import read_sbp_file
from flab2bp.sfy.geometry import port_forward, quat_rotate
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.query import connected, object_index, spline_points
from flab2bp.sfy.registry import Port, load_registry
from tests.sfy.conftest import fixture_paths

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

# Belts are only cross-checked against the class family the registry describes.
CURRENT_SAVE_VERSION = 58

# A belt's own ends and a lift's upper end are not class constants, so their
# ports carry no usable rotation either. Same exclusion as the port cross-check.
DYNAMIC_PEERS = ("Build_ConveyorBelt", "Build_ConveyorLift")


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


def test_every_wired_belt_leaves_its_port_along_the_ports_facing() -> None:
    """The law the checkpoint blueprint's belt is built on, over the whole corpus.

    At the end where a belt meets a machine, its first spline segment runs along
    that port's forward direction -- outward from the machine, for an input port
    as much as for an output one.
    """
    reg = load_registry()
    checked = 0
    for path in fixture_paths():
        bp = read_sbp_file(path)
        if bp.header.save_version < CURRENT_SAVE_VERSION:
            continue
        index = object_index(bp)
        for h, d in bp.objects:
            if not h.class_name.startswith("Build_ConveyorBelt") or h.transform is None:
                continue
            points = spline_points(d)
            if len(points) < 2:
                continue
            for ref in d.components or ():
                component_header, component = index[ref.path]
                peer = connected(component)
                if peer is None or peer.path not in index:
                    continue
                port_header, _ = index[peer.path]
                if port_header.parent is None or port_header.parent not in index:
                    continue
                machine_header, _ = index[port_header.parent]
                if machine_header.class_name.startswith(DYNAMIC_PEERS):
                    continue
                if machine_header.transform is None:
                    continue
                machine = reg.buildables.get(machine_header.class_name)
                if machine is None:
                    continue
                port = next((p for p in machine.ports if p.name == port_header.name), None)
                if port is None:
                    continue
                at_first = component_header.name.endswith("0")
                near = points[0][0] if at_first else points[-1][0]
                far = points[1][0] if at_first else points[-2][0]
                step = quat_rotate(
                    h.transform.rotation, (far.x - near.x, far.y - near.y, far.z - near.z)
                )
                length = math.dist((0.0, 0.0, 0.0), step)
                if length < 1e-6:
                    continue
                facing = port_forward(machine_header.transform, port)
                cosine = sum(step[i] * facing[i] for i in range(3)) / length
                assert cosine > 0.999, (path.stem, h.name, port_header.name, round(cosine, 4))
                checked += 1
    assert checked > 500, checked
