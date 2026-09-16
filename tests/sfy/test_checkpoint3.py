"""Written checkpoint pairs, including the explicitly separate transport witness."""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

import pytest

from flab2bp.sfy import pipeline
from flab2bp.sfy.codec import read_pair
from flab2bp.sfy.layout.emit import decode
from flab2bp.sfy.sections.model import endpoint
from scripts import sfy_checkpoint2, sfy_checkpoint3


def test_transport_witness_writes_connected_lift_and_attachment_geometry(tmp_path: Path) -> None:
    written = sfy_checkpoint3.build_witness(tmp_path)
    blueprint, _record = read_pair(written.sbp, written.cfg)
    placement = decode(blueprint, pipeline.registry())

    assert placement == written.placement
    assert len(placement.lifts) == 1
    assert len(placement.attachments) == 1
    assert not placement.machines
    assert written.report.ok
    assert set(written.report.skipped) == {"belt.capsule", "geom.hard_clearance", "power.wires"}
    lift_id = placement.lifts[0].id
    assert sum(lift_id in (link.a[0], link.b[0]) for link in placement.links) == 2


def test_factory_boundary_instructions_preserve_one_full_rate_per_item(tmp_path: Path) -> None:
    written = sfy_checkpoint2.build_case(
        sfy_checkpoint3.CASES[1], tmp_path, time_budget_s=15, strategy="sections"
    )
    assert written.mark == "mk1"
    placement = written.build.placement
    registry = pipeline.registry()
    lines = sfy_checkpoint2._pair_section(written, registry)
    inlet, outlet = "\n".join(lines).split("Material outputs, at the top", maxsplit=1)
    exports = dict(written.build.spec.outputs)
    for item, rate in written.build.spec.surplus_outputs.items():
        exports[item] = exports.get(item, Fraction()) + rate
    for section, totals, incoming in (
        (inlet, written.build.spec.external_inputs, True),
        (outlet, exports, False),
    ):
        advertised: dict[str, Fraction] = {}
        positions = {
            item: tuple(map(float, (x, y, z)))
            for item, x, y, z in re.findall(
                r"\*\*([\w-]+)\*\* at x = ([-\d.]+), y = ([-\d.]+), z = ([-\d.]+) cm",
                section,
            )
        }
        for rate, item in re.findall(r"carrying ([\d./]+) ([\w-]+)/min", section):
            assert item not in advertised
            advertised[item] = Fraction(rate)
        assert advertised == {item: rate * 60 for item, rate in totals.items()}
        assert positions.keys() == totals.keys()
        for lane in placement.stack_lanes:
            if lane.item_id not in totals:
                continue
            node = lane.bottom if incoming else lane.top
            point, normal = endpoint(placement, *node, registry)
            assert positions[lane.item_id] == pytest.approx(point, abs=0.01)
            assert normal == pytest.approx((0, 0, -1 if incoming else 1), abs=1e-6)
