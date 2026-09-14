"""Belt spline endpoints in the fixtures must land on the registry's port positions.

For each belt whose connection component links to a machine's connection
component, the belt's spline endpoint, in world space, must lie within
TOLERANCE_CM of the machine's port as computed from the registry.

This is the acceptance for the port translations in ``data/registry.json``: the
fixtures are blueprints the game itself wrote, so a port the extractor put in the
wrong place shows up here as a belt that ends nowhere near it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.geometry import distance, quat_rotate, world_port
from flab2bp.sfy.objects import ObjectHeader
from flab2bp.sfy.properties import Vector
from flab2bp.sfy.query import connected, object_index, spline_points
from flab2bp.sfy.registry import load_registry
from tests.sfy.conftest import fixture_paths

TOLERANCE_CM = 15.0

# Only save version 58 and up is the class family the registry describes; the
# older fixtures predate 1.0 and are here for the format tests.
CURRENT_SAVE_VERSION = 58

# Peers whose connection points are *not* class constants, so the registry's
# class-default translation says nothing about where they sit. A belt's two
# endpoints come from its own spline and a lift's upper endpoint from its
# ``mTopTransform``; both classes carry ``ConveyorAny0`` and ``ConveyorAny1`` at
# the local origin in the assets, which is only where the game starts them.
# Cross-checking a belt against one of those compares a measurement with a
# placeholder, so those links are not part of this check.
DYNAMIC_PEERS = ("Build_ConveyorBelt", "Build_ConveyorLift")

# A conveyor splitter and merger are 200 cm long, with ``Input1`` and
# ``Output1`` on their two ends, 100 cm out from the actor origin. Most belts
# stop there, at 0.000 cm. A minority -- 4.2% of the links here, in 5 of the 49
# fixtures, and in those blueprints not even for every attachment -- run on to
# the attachment's own origin instead, overshooting the port by exactly 100 cm
# and overlapping the attachment.
#
# The registry is not what is wrong. The side ports of the very same attachments
# (``Input2``, ``Input3``, ``Output2``, ``Output3``, also 100 cm out) land at
# 0.000 cm, as do the in-line ports of the neighbouring attachments in the same
# blueprint, so ``Input1`` and ``Output1`` are where the extractor says they
# are. What this is on the game's side is not established: these are files the
# game wrote, downloaded from satisfactoryblueprints.com, and the overshoot
# clusters by blueprint rather than by class, save version or belt mark.
#
# So the exception is allowed, but only in the exact shape it was measured in:
# an in-line attachment port, missed by 100 cm, with the belt ending on the
# attachment's origin, and never more than a tenth of the corpus. A port put in
# the wrong place by the extractor misses by an arbitrary distance, lands
# nowhere near the origin, and misses on every fixture at once, so it still
# fails all three of those.
INLINE_ATTACHMENTS = ("Build_ConveyorAttachmentSplitter", "Build_ConveyorAttachmentMerger")
INLINE_PORTS = ("Input1", "Output1")
OVERSHOOT_CM = 100.0
MAX_OVERSHOOT_SHARE = 0.10


@dataclass(frozen=True, slots=True)
class Link:
    """One belt endpoint wired to one static port, and how far apart they are."""

    fixture: str
    belt: str
    component: str
    machine: str
    port: str
    to_first: float
    to_last: float
    to_origin: float

    @property
    def residual(self) -> float:
        return min(self.to_first, self.to_last)


def _belt_world_point(
    header: ObjectHeader, pts: tuple[tuple[Vector, Vector, Vector], ...], *, first: bool
) -> tuple[float, float, float]:
    """One end of a belt's spline in world space."""
    loc = pts[0][0] if first else pts[-1][0]
    t = header.transform
    assert t is not None  # belts are actors, and every actor carries a transform
    r = quat_rotate(t.rotation, (loc.x, loc.y, loc.z))
    return (r[0] + t.translation[0], r[1] + t.translation[1], r[2] + t.translation[2])


@cache
def _links() -> tuple[Link, ...]:
    """Every belt-endpoint-to-static-port link in the current-family fixtures."""
    reg = load_registry()
    rows: list[Link] = []
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < CURRENT_SAVE_VERSION:
            continue
        index = object_index(bp)
        for h, d in bp.objects:
            if not h.class_name.startswith("Build_ConveyorBelt"):
                continue
            pts = spline_points(d)
            if not pts:
                continue
            for comp_ref in d.components or ():
                ch, cd = index[comp_ref.path]
                peer = connected(cd)
                if peer is None or peer.path not in index:
                    continue
                ph, _ = index[peer.path]
                if ph.parent is None or ph.parent not in index:
                    continue
                mh, _ = index[ph.parent]
                if mh.class_name.startswith(DYNAMIC_PEERS) or mh.transform is None:
                    continue
                machine = reg.buildables.get(mh.class_name)
                if machine is None:
                    continue
                port = next((p for p in machine.ports if p.name == ph.name), None)
                if port is None:
                    continue
                expected = world_port(mh.transform, port)
                ends = (
                    _belt_world_point(h, pts, first=True),
                    _belt_world_point(h, pts, first=False),
                )
                rows.append(
                    Link(
                        fixture=path.name,
                        belt=h.name,
                        component=ch.name,
                        machine=mh.class_name,
                        port=ph.name,
                        to_first=distance(expected, ends[0]),
                        to_last=distance(expected, ends[1]),
                        to_origin=min(distance(mh.transform.translation, e) for e in ends),
                    )
                )
    return tuple(rows)


def _overshoots(links: tuple[Link, ...]) -> list[Link]:
    return [x for x in links if x.residual > TOLERANCE_CM]


def test_belt_endpoints_hit_registry_ports() -> None:
    links = _links()
    assert len(links) >= 20, "too few belt-to-machine links to be meaningful"
    wrong = [
        (x.fixture, x.belt, x.machine, x.port, round(x.residual, 2))
        for x in _overshoots(links)
        if not x.machine.startswith(INLINE_ATTACHMENTS)
        or x.port not in INLINE_PORTS
        or abs(x.residual - OVERSHOOT_CM) > TOLERANCE_CM
    ]
    assert not wrong, wrong[:10]


def test_the_overshoot_stays_a_minority_of_the_corpus() -> None:
    links = _links()
    assert len(_overshoots(links)) <= MAX_OVERSHOOT_SHARE * len(links)


def test_an_overshooting_belt_ends_on_the_attachment_origin() -> None:
    """The 100 cm is the port's own offset, not a belt that stops anywhere."""
    astray = [
        (x.fixture, x.belt, x.port, round(x.to_origin, 2))
        for x in _overshoots(_links())
        if x.to_origin > TOLERANCE_CM
    ]
    assert not astray, astray[:10]


def test_no_registry_port_is_wrong_everywhere_it_is_wired() -> None:
    """Every (class, port) pair the corpus wires lands on a belt end at least once."""
    best: dict[tuple[str, str], float] = {}
    for x in _links():
        best[x.machine, x.port] = min(best.get((x.machine, x.port), float("inf")), x.residual)
    assert best, "no ports were cross-checked at all"
    assert {k: round(v, 2) for k, v in best.items() if v > TOLERANCE_CM} == {}


def test_conveyor_any_index_names_the_spline_end() -> None:
    """``ConveyorAny0`` is the belt's first spline point and ``ConveyorAny1`` its last."""
    disagree = [
        x
        for x in _links()
        if abs(x.to_first - x.to_last) > TOLERANCE_CM
        and (x.to_first < x.to_last) is not x.component.endswith("0")
    ]
    assert not disagree, disagree[:10]
