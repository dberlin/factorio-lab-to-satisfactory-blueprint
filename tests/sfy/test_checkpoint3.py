"""Written checkpoint pairs, including the explicitly separate transport witness."""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

from flab2bp.sfy import pipeline
from flab2bp.sfy.codec import read_pair
from flab2bp.sfy.layout.emit import decode
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


def test_factory_boundary_instructions_preserve_split_flow_rates(tmp_path: Path) -> None:
    written = sfy_checkpoint2.build_case(
        sfy_checkpoint3.CASES[1], tmp_path, time_budget_s=15, strategy="grid-routed"
    )
    lines = sfy_checkpoint2._pair_section(written, pipeline.registry())
    inlet, outlet = "\n".join(lines).split("Belts out,", maxsplit=1)
    for section, totals in (
        (inlet, written.build.spec.external_inputs),
        (outlet, written.build.spec.outputs),
    ):
        advertised: dict[str, Fraction] = {}
        for rate, item in re.findall(r"carrying ([\d.]+) ([\w-]+)/min", section):
            per_minute = Fraction(rate)
            assert per_minute <= 60  # Every boundary in this case is a Mk.1 belt.
            advertised[item] = advertised.get(item, Fraction()) + per_minute
        assert advertised == {item: rate * 60 for item, rate in totals.items()}
