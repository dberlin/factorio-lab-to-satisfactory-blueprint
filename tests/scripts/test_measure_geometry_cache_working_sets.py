from pathlib import Path

from scripts.measure_geometry_cache_working_sets import (
    build_report,
    collect_case_traces,
    lru_hits,
    recommended_maxsize,
)


def test_lru_hits_respects_recency_not_insertion_order() -> None:
    trace = [("a",), ("b",), ("a",), ("c",), ("a",), ("b",)]
    assert lru_hits(trace, 2) == 2


def test_recommendation_holds_peak_case_and_retains_observed_hits() -> None:
    cases: list[list[tuple[object, ...]]] = [
        [("a",), ("b",), ("a",)],
        [("c",), ("d",), ("c",)],
    ]
    assert recommended_maxsize(cases, [key for case in cases for key in case]) == 2


def test_no_repeat_trace_still_holds_one_complete_case() -> None:
    cases: list[list[tuple[object, ...]]] = [[("a",), ("b",), ("c",)], [("d",)]]
    assert recommended_maxsize(cases, [key for case in cases for key in case]) == 4


def test_keepout_trace_uses_the_real_cache_keys_of_both_callers() -> None:
    """Two callers, two key shapes, and the trace has to carry both.

    A Splitter's keepout takes the defaults and so asks a ONE-argument key; a
    machine's takes a reach, a level count and the two paste arcs, and asks two
    six-argument keys per (model, yaw) because either lattice axis can be the
    one a paste compresses. A trace that recorded only the first would
    recommend a bound of 1, which is what it used to do.
    """
    cases = collect_case_traces(Path(__file__).parents[2])
    keys = [key for case in cases.values() for key in case["colliders.belt_keepout_offsets"]]
    assert keys
    splitter = [key for key in keys if len(key) == 1]
    machine = [key for key in keys if len(key) == 6]
    assert splitter
    assert machine
    assert len(splitter) + len(machine) == len(keys)
    assert all(isinstance(key[0], int) for key in keys)
    for model_index, yaw, reach, levels, column, row in machine:
        assert isinstance(model_index, int)
        assert isinstance(yaw, float)
        assert isinstance(reach, int) and reach > 0
        assert isinstance(levels, int)
        # the two arcs are the same pair swapped, one orientation each
        assert isinstance(column, float) and isinstance(row, float)
        assert column != row
    # every machine key is matched by its mirror, so both orientations are traced
    assert {(k[0], k[1], k[2], k[3], k[5], k[4]) for k in machine} == set(machine)


def test_report_separates_recommendation_from_applied_rollback_policy() -> None:
    functions = build_report(Path(__file__).parents[2])["functions"]
    for name in (
        "catalog.collider_span",
        "colliders.belt_keepout_offsets",
    ):
        finite = functions[name]
        assert finite["applied_maxsize"] == finite["recommended_maxsize"]
        assert finite["rollback_reason"] is None

    for name in (
        "catalog.clearance",
        "colliders.own_centre_extent",
        "planet.collider_radius",
    ):
        assert functions[name]["recommended_maxsize"] > 0
        assert functions[name]["applied_maxsize"] is None
        assert functions[name]["rollback_reason"]
