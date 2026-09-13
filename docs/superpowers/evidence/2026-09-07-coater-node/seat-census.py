"""Which seat index does each Spray Coater take today, and is the head covered?

Read-only.  For every candidate spec of every audit-corpus URL, plan strips,
greedily pack them, prepare the routing problem, and report for each committed
coater the offset of its seat from its lane head and whether its 1x3 body covers
that head.  The head is where producer nets merge, so a body that covers it is
the defect `docs/superpowers/specs/2026-09-07-coater-node-design.md` removes.
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, "src")

from flab2bp.bench.corpus import URL_CORPUS
from flab2bp.dsp import catalog
from flab2bp.lab.data import load_vendored
from flab2bp.lab.url import parse_url
from flab2bp.layout.band_policy import BandPolicy
from flab2bp.layout.freeform import (
    _greedy_pack,
    _height_seed,
    _prepare_routing_problem,
    plan_strips,
)
from flab2bp.rates.candidates import build_candidates

data = load_vendored()
rows = []
for entry in URL_CORPUS:
    for spec in build_candidates(data, parse_url(entry.url)).candidates:
        if not spec.spray_lanes:
            continue
        started = time.time()
        try:
            strips = plan_strips(spec)
            pack = _greedy_pack(strips, _height_seed(strips))
            prepared = _prepare_routing_problem(
                spec, strips, pack, policy=BandPolicy("portable"), power=False
            )
        except Exception as exc:  # noqa: BLE001 -- a refusal is a datum here
            rows.append({"url": entry.url_id, "label": spec.label, "refused": str(exc)[:200]})
            continue
        width = catalog.oriented_footprint(catalog.SPRAY_COATER_ID, 90.0)[0]
        half = (width - 1) // 2
        for port in prepared.coater_supply_ports:
            rows.append(
                {
                    "url": entry.url_id,
                    "label": spec.label,
                    "item": port.item,
                    "seat": [port.host_x, port.host_y],
                    "body_x": [port.host_x - half, port.host_x + half],
                    "seconds": round(time.time() - started, 1),
                }
            )
print(json.dumps(rows, indent=1))
