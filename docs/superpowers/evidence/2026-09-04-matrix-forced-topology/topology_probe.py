from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any

root = Path(os.environ.get("PROBE_ROOT", Path(__file__).parents[4]))
sys.path.insert(0, str(root))

from flab2bp.layout.band_policy import BandPolicy  # noqa: E402
from flab2bp.layout.sequence_solver import (  # noqa: E402
    SequenceSolverConfig,
    _production_run,
)
from flab2bp.rates import DEFAULT_CANDIDATE_POLICIES  # noqa: E402
from scripts import audit  # noqa: E402


def normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: normalize(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalize(item) for item in value]
    return value


def selected_structure(
    problem: Any,
    variant_indices: tuple[int, ...],
    baseline_strips: list[Any] | None,
) -> list[dict[str, Any]]:
    structures = []
    for index, selected in enumerate(variant_indices):
        if problem.variant_tables:
            variant = problem.variant(index, selected)
            family = problem.instance_ids[index].family_id
            machine_count = problem.instance_ids[index].machine_count
            box = [problem.sizes[index][0], problem.sizes[index][1]]
            yaw = variant.yaw
            column_heights = getattr(variant, "column_heights", (1,) * machine_count)
            max_stack_height = getattr(variant, "max_stack_height", 1)
            lane_rows = variant.lane_plan.lane_rows
            sorter_anchors = variant.attachment_plan
            endpoints = getattr(variant, "port_dock_plan", ())
        else:
            assert baseline_strips is not None
            strip = baseline_strips[index]
            variant = strip.physical_variant
            family = strip.family_id
            machine_count = strip.machines
            box = [problem.sizes[index][0], problem.sizes[index][1]]
            yaw = strip.yaw
            column_heights = (
                getattr(variant, "column_heights", (1,) * machine_count)
                if variant is not None
                else (1,) * machine_count
            )
            max_stack_height = (
                getattr(variant, "max_stack_height", 1) if variant is not None else 1
            )
            lane_rows = strip.lane_plan.lane_rows if strip.lane_plan is not None else ()
            sorter_anchors = strip.attachment_plan
            endpoints = strip.port_dock_plan
        structures.append(
            {
                "strip": index,
                "family": normalize(family),
                "machine_count": machine_count,
                "box": box,
                "yaw": yaw,
                "column_heights": normalize(column_heights),
                "max_stack_height": max_stack_height,
                "lane_rows": normalize(lane_rows),
                "sorter_anchors": normalize(sorter_anchors),
                "endpoints": normalize(endpoints),
            }
        )
    return structures


def probe(mode: str, *, source_commit: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("source commit must be a full lowercase Git hash")
    entry = next(entry for entry in audit.URL_CORPUS if entry.url_id == "universe-matrix")
    specs = audit._specs_for(entry.url, DEFAULT_CANDIDATE_POLICIES)
    rows = []
    for spec in specs:
        kwargs: dict[str, Any] = {}
        if mode != "baseline":
            from flab2bp.layout.sequence_solver import LabTopologyMode

            kwargs["forced_lab_topology"] = LabTopologyMode(mode)
        run = _production_run(
            spec,
            time_budget_s=30.0,
            power=True,
            band_policy=BandPolicy("portable"),
            strip_len=6,
            config=SequenceSolverConfig(),
            **kwargs,
        )
        baseline_strips = None
        if mode == "baseline":
            from flab2bp.layout.freeform import _box, plan_strips
            from flab2bp.layout.sequence_solver import _sequence_reservation_strips
            from flab2bp.layout.strip_variants import generate_strip_families

            target_sizes = run.solver._heights[0].problem.sizes
            families = tuple(generate_strip_families(spec))
            for candidate_len in range(1, spec.machine_count + 1):
                candidate_strips = _sequence_reservation_strips(
                    plan_strips(
                        spec,
                        strip_len=candidate_len,
                        band_policy=BandPolicy("portable"),
                        families=families,
                    )
                )
                if tuple(_box(strip) for strip in candidate_strips) == target_sizes:
                    baseline_strips = candidate_strips
                    break
            if baseline_strips is None:
                raise RuntimeError("could not recover baseline's realized strip plan")
        initial = []
        selections: dict[tuple[int, ...], Any] = {}
        for height_state in run.solver._heights:
            state = height_state.restarts[0].anneal
            selections.setdefault(
                state.variant_indices,
                selected_structure(
                    height_state.problem,
                    state.variant_indices,
                    baseline_strips,
                ),
            )
            initial.append(
                {
                    "height": height_state.height,
                    "positive": normalize(state.pair.positive),
                    "negative": normalize(state.pair.negative),
                    "east_gaps": normalize(state.gaps.east),
                    "north_gaps": normalize(state.gaps.north),
                    "variant_indices": normalize(state.variant_indices),
                    "sizes": normalize(height_state.problem.selected_sizes(state.variant_indices)),
                }
            )
        rows.append(
            {
                "candidate": spec.label,
                "machine_count": spec.machine_count,
                "initial_states": initial,
                "selected_structures": [
                    {
                        "variant_indices": normalize(indices),
                        "strips": structure,
                    }
                    for indices, structure in selections.items()
                ],
            }
        )
    return {
        "source_root": str(root),
        "commit": source_commit,
        "mode": mode,
        "candidates": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "compact", "unstacked"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(
            probe(args.mode, source_commit=args.source_commit),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
