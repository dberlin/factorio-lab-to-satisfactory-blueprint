from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
EXPECTED_LABELS = frozenset(("no-proliferator", "all-products", "output-products"))
EXPECTED_BUDGET_S = 30.0


def load(root: Path, name: str) -> Any:
    return json.loads((root / name).read_text())


def _commit(document: dict[str, Any], *, source: str) -> str:
    commit = document.get("commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError(f"{source} must capture one full lowercase source commit")
    return commit


def candidates(
    document: dict[str, Any],
    *,
    source: str,
    expected_mode: str,
) -> dict[str, dict[str, Any]]:
    if document.get("mode") != expected_mode:
        raise ValueError(f"{source} must report mode {expected_mode!r}")
    raw_rows = document.get("candidates")
    if not isinstance(raw_rows, list):
        raise ValueError(f"{source} candidates must be a list")
    rows: list[dict[str, Any]] = []
    labels: list[str] = []
    for row in raw_rows:
        if not isinstance(row, dict) or not isinstance(row.get("candidate"), str):
            raise ValueError(f"{source} candidates must carry string labels")
        rows.append(row)
        labels.append(row["candidate"])
    duplicates = sorted(label for label, count in Counter(labels).items() if count > 1)
    if duplicates:
        raise ValueError(f"{source} contains duplicate candidates: {duplicates}")
    if set(labels) != EXPECTED_LABELS or len(rows) != len(EXPECTED_LABELS):
        raise ValueError(
            f"{source} must contain exactly the expected candidates: "
            f"{sorted(EXPECTED_LABELS)}"
        )
    return {str(candidate["candidate"]): candidate for candidate in rows}


def structures(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    selections = candidate["selected_structures"]
    if len(selections) != 1:
        raise ValueError("each topology candidate must contain exactly one selected structure")
    return selections[0]["strips"]


def first_differences(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    before_strips = structures(before)
    after_strips = structures(after)
    structural = []
    fields = (
        "family",
        "machine_count",
        "box",
        "yaw",
        "column_heights",
        "max_stack_height",
        "lane_rows",
        "sorter_anchors",
        "endpoints",
    )
    for index, (old, new) in enumerate(zip(before_strips, after_strips, strict=True)):
        changed = {
            field: {"baseline": old[field], "current": new[field]}
            for field in fields
            if old[field] != new[field]
        }
        if changed:
            structural.append({"strip": index, "changes": changed})
    initial = []
    state_fields = (
        "height",
        "positive",
        "negative",
        "east_gaps",
        "north_gaps",
        "variant_indices",
        "sizes",
    )
    if len(before["initial_states"]) != len(after["initial_states"]):
        raise ValueError("compared topology candidates must have equal initial-state counts")
    for index, (old, new) in enumerate(
        zip(before["initial_states"], after["initial_states"], strict=True)
    ):
        changed = {
            field: {"baseline": old[field], "current": new[field]}
            for field in state_fields
            if old[field] != new[field]
        }
        if changed:
            initial.append({"state": index, "changes": changed})
    return {
        "strip_count": len(before_strips),
        "structural_difference_count": len(structural),
        "initial_state_difference_count": len(initial),
        "first_structural_differences": structural[:5],
        "first_initial_state_differences": initial[:3],
    }


def audit_summary(
    root: Path,
    name: str,
    *,
    expected_mode: str,
    expected_commit: str,
) -> dict[str, Any]:
    decoded_rows = [
        json.loads(line)
        for line in (root / name).read_text().splitlines()
        if line.strip()
    ]
    rows: list[dict[str, Any]] = []
    labels: list[str] = []
    for row in decoded_rows:
        if not isinstance(row, dict) or not isinstance(row.get("spec_label"), str):
            raise ValueError(f"{name} rows must carry string spec labels")
        rows.append(row)
        labels.append(row["spec_label"])
    duplicates = sorted(label for label, count in Counter(labels).items() if count > 1)
    if duplicates:
        raise ValueError(f"{name} contains duplicate audit rows: {duplicates}")
    if set(labels) != EXPECTED_LABELS or len(rows) != len(EXPECTED_LABELS):
        raise ValueError(
            f"{name} must contain exactly one row for each expected label: "
            f"{sorted(EXPECTED_LABELS)}"
        )
    for row in rows:
        if row.get("commit") != expected_commit:
            raise ValueError(f"{name} contains a non-matching source commit")
        if row.get("strategy") != "sequence-pair":
            raise ValueError(f"{name} contains a non-sequence-pair strategy")
        if row.get("forced_lab_topology") != expected_mode:
            raise ValueError(f"{name} contains a non-matching forced topology mode")
        if row.get("budget") != EXPECTED_BUDGET_S:
            raise ValueError(f"{name} contains a non-30-second budget")
        if row.get("url_id") != "universe-matrix" or row.get("power") is not True:
            raise ValueError(f"{name} contains a non-Matrix or unpowered audit row")
    return {
        row["spec_label"]: {
            "status": row["status"],
            "area": row["area"],
            "seconds": row["seconds"],
            "physical_machine_count": row["physical_machine_count"],
            "matrix_lab_count": row["matrix_lab_count"],
            "ground_matrix_lab_columns": row["ground_matrix_lab_columns"],
            "max_lab_stack_height": row["max_lab_stack_height"],
            "exact_closures": len(json.loads(row["stats"]["exact_closures"])),
        }
        for row in rows
    }


def build_diagnosis(root: Path = HERE) -> dict[str, Any]:
    baseline_document = load(root, "topology-baseline.json")
    compact_document = load(root, "topology-compact.json")
    unstacked_document = load(root, "topology-unstacked.json")
    baseline_commit = _commit(baseline_document, source="topology-baseline.json")
    compact_commit = _commit(compact_document, source="topology-compact.json")
    unstacked_commit = _commit(unstacked_document, source="topology-unstacked.json")
    if compact_commit != unstacked_commit:
        raise ValueError("forced topology probes contain mixed source commits")
    baseline = candidates(
        baseline_document,
        source="topology-baseline.json",
        expected_mode="baseline",
    )
    compact = candidates(
        compact_document,
        source="topology-compact.json",
        expected_mode="compact",
    )
    unstacked = candidates(
        unstacked_document,
        source="topology-unstacked.json",
        expected_mode="unstacked",
    )
    labels = sorted(EXPECTED_LABELS)
    return {
        "baseline_commit": baseline_commit,
        "experiment_commit": compact_commit,
        "audit": {
            "compact": audit_summary(
                root,
                "forced-compact.jsonl",
                expected_mode="compact",
                expected_commit=compact_commit,
            ),
            "unstacked": audit_summary(
                root,
                "forced-unstacked.jsonl",
                expected_mode="unstacked",
                expected_commit=compact_commit,
            ),
        },
        "baseline_to_forced_unstacked": {
            label: first_differences(baseline[label], unstacked[label])
            for label in labels
        },
        "forced_compact_to_forced_unstacked": {
            label: first_differences(compact[label], unstacked[label])
            for label in labels
        },
    }


def main() -> None:
    result = build_diagnosis()
    (HERE / "diagnosis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
