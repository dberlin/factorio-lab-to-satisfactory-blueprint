"""The Satisfactory pipeline: a FactorioLab URL and its flow in, two files out.

Every build here is driven by one of the committed flow exports in
``tests/fixtures/sfy_flows``, read through the same ``--flow`` door the CLI
offers, so nothing about a rate, a machine count or a belt tier is written
down in this file.  The URL each build is given is the export's own line 1,
which is what ``verify_provenance`` compares against.

Where the pipeline refuses, the test names the refusal.  A refusal is a result:
``build`` either hands back a blueprint the validator passed or says why it
will not, and the exit code the CLI turns each one into is the subject of
``tests/test_cli_sfy.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from flab2bp.layout.base import NoValidLayout, SpecInfeasible
from flab2bp.sfy import pipeline
from flab2bp.sfy.codec import read_sbp_file, read_sbpcfg
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.emit import decode
from flab2bp.sfy.layout.measure import Measure
from flab2bp.sfy.layout.model import SfyPlacement
from flab2bp.sfy.layout.validate import Finding, Severity
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec

FLOWS = Path(__file__).resolve().parents[1] / "fixtures" / "sfy_flows"

#: A DSP URL, to prove the sfy pipeline refuses one rather than mis-reading it.
DSP_URL = "https://factoriolab.github.io/dsp/list?o=iron-ingot*60&v=11"


def flow_url(name: str) -> str:
    """The URL a committed export was generated from: its own line 1."""
    text = (FLOWS / f"{name}.csv").read_text(encoding="utf-8")
    return text.splitlines()[0].strip().strip('"')


@pytest.fixture(scope="module")
def iron_plate_mk3() -> pipeline.SfyBuild:
    """One real Mk.3 build, shared by every test that needs a finished one."""
    return pipeline.build(
        flow_url("iron-plate-60"),
        strategy="manifold-rows",
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
    )


def test_a_pinned_flow_builds_a_clean_blueprint_in_a_mk3_designer(
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    """The headline: FactorioLab's own flow, laid out and judged clean."""
    build = iron_plate_mk3
    assert build.strategy == "manifold-rows"
    assert build.designer.mark == "mk3"
    assert build.flow_pinned is True
    assert build.refused == ()
    assert [finding.message for finding in build.report.errors] == []
    assert build.report.ok
    assert build.blueprint is not None
    assert build.spec.machine_count == len(build.placement.machines)


def test_write_leaves_a_sbp_and_a_sbpcfg_the_game_can_be_handed(
    iron_plate_mk3: pipeline.SfyBuild, tmp_path: Path
) -> None:
    """Both halves of a blueprint, named alike, in a directory that is created."""
    out_dir = tmp_path / "blueprints" / "session"
    sbp, cfg = pipeline.write(iron_plate_mk3, out_dir)

    assert sbp.parent == out_dir and cfg.parent == out_dir
    assert sbp.suffix == ".sbp" and cfg.suffix == ".sbpcfg"
    assert sbp.stem == cfg.stem
    assert sbp.exists() and cfg.exists()


def test_the_written_blueprint_decodes_to_the_placement_that_was_laid_out(
    iron_plate_mk3: pipeline.SfyBuild, tmp_path: Path
) -> None:
    """What lands on disk is the build, not a re-encoding that drifted from it."""
    sbp, cfg = pipeline.write(iron_plate_mk3, tmp_path)
    assert decode(read_sbp_file(sbp), pipeline.registry()) == iron_plate_mk3.placement
    # The config's own colour is four 32-bit floats and does not survive the
    # trip as the literal it was written from, so the description is what is
    # compared: it is the whole reason the .sbpcfg carries anything of ours.
    written = read_sbpcfg(cfg.read_bytes())
    assert written.description == iron_plate_mk3.placement.description
    assert "entry: iron-ore at x=" in written.description


def test_the_default_file_name_is_the_objective_and_the_designer_mark(
    iron_plate_mk3: pipeline.SfyBuild, tmp_path: Path
) -> None:
    """No ``--name``: the build names itself after what it makes and what it fits."""
    sbp, _ = pipeline.write(iron_plate_mk3, tmp_path)
    assert sbp.stem == "iron-plate-mk3"


def test_a_given_name_is_the_file_name_and_the_blueprint_short_description(
    tmp_path: Path,
) -> None:
    build = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
        name="plates for the hub",
    )
    sbp, cfg = pipeline.write(build, tmp_path)
    assert build.placement.short_desc == "plates for the hub"
    assert sbp.stem == "plates-for-the-hub"
    assert cfg.stem == "plates-for-the-hub"


def test_a_build_that_does_not_fit_the_requested_designer_refuses_with_its_cause() -> None:
    """``iron-rod*60``'s two rows want more band than a Mk.1 has.  It says so.

    Not ``iron-plate*60``, which fits a Mk.1 since Task 8d paired its two groups
    machine to machine; ``iron-rod*60``'s rates do not pair, so it is still a
    manifold and still too deep for the two smaller designers.
    """
    with pytest.raises(NoValidLayout) as caught:
        pipeline.build(
            flow_url("iron-rod-60"),
            designer="mk1",
            strategy="manifold-rows",
            flow=FLOWS / "iron-rod-60.csv",
        )
    assert caught.value.reason == "rows exceed the designer depth"


def test_a_flow_that_moves_a_fluid_is_refused_before_any_geometry() -> None:
    """``plastic-10`` pipes crude oil in and leaves heavy oil residue behind."""
    with pytest.raises(SpecInfeasible) as caught:
        pipeline.build(
            flow_url("plastic-10"),
            designer="mk3",
            flow=FLOWS / "plastic-10.csv",
        )
    assert caught.value.reason == "fluids are M5"


def test_a_build_without_a_flow_refuses_and_names_both_ways_to_supply_one() -> None:
    """R1: FactorioLab's chosen flow is authoritative, so there is no build without one."""
    with pytest.raises(ValueError, match="--flow") as caught:
        pipeline.build(flow_url("iron-plate-60"), designer="mk3")
    assert "--fetch-flow" in str(caught.value)
    assert not isinstance(caught.value, NoValidLayout)


def test_a_dyson_sphere_program_url_is_refused_rather_than_built_as_satisfactory() -> None:
    with pytest.raises(ValueError, match="dsp"):
        pipeline.build(DSP_URL, designer="mk3", flow=FLOWS / "iron-plate-60.csv")


def test_the_game_tables_are_read_once_per_process() -> None:
    """Ruling 3: the 1.2 MB registry parse and the 49-file library scan are cached."""
    assert pipeline.registry() is pipeline.registry()
    assert pipeline.template_library() is pipeline.template_library()
    assert pipeline.lab_map() is pipeline.lab_map()
    assert pipeline.newest_fixture_header() is pipeline.newest_fixture_header()


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        ({"flow_text": "x"}, "flow text"),
        ({"fetch_flow": True}, "--fetch-flow"),
    ],
)
def test_two_ways_of_supplying_a_flow_at_once_are_refused(
    extra: dict[str, object], named: str
) -> None:
    """Each door is a different recipe selection, so there is no right guess.

    ``--fetch-flow`` is the one the DSP pipeline does not have to think about,
    because there a missing flow simply means "derive it"; here it is a third
    selection and is refused alongside the other two rather than losing a
    silent precedence contest.
    """
    with pytest.raises(ValueError, match="no right guess") as caught:
        pipeline.build(
            flow_url("iron-plate-60"),
            designer="mk3",
            flow=FLOWS / "iron-plate-60.csv",
            **extra,  # type: ignore[arg-type]
        )
    assert named in str(caught.value)
    assert "a flow file" in str(caught.value)


class _RaceArm:
    def __init__(
        self,
        name: str,
        placement: SfyPlacement,
        clock: list[float],
        *,
        duration: float = 0.0,
        refusal: NoValidLayout | None = None,
    ) -> None:
        self.name = name
        self.placement = placement
        self.clock = clock
        self.duration = duration
        self.refusal = refusal
        self.calls: list[tuple[float, float | None]] = []

    def lay_out(
        self,
        spec: SfyBuildSpec,
        designer: Designer,
        *,
        time_budget_s: float = 15.0,
        absolute_deadline: float | None = None,
        registry: Registry | None = None,
        lab_map: LabMap | None = None,
    ) -> SfyPlacement:
        self.calls.append((time_budget_s, absolute_deadline))
        self.clock[0] += self.duration
        if self.refusal is not None:
            raise self.refusal
        return self.placement


def _race(
    monkeypatch: pytest.MonkeyPatch,
    clean: pipeline.SfyBuild,
    *,
    first_duration: float = 1.0,
    first_refusal: NoValidLayout | None = None,
    second_refusal: NoValidLayout | None = None,
) -> tuple[_RaceArm, _RaceArm]:
    clock = [100.0]
    first = _RaceArm(
        "manifold-rows",
        clean.placement,
        clock,
        duration=first_duration,
        refusal=first_refusal,
    )
    second = _RaceArm(
        "grid-routed",
        replace(clean.placement, short_desc="grid"),
        clock,
        refusal=second_refusal,
    )
    monkeypatch.setattr("flab2bp.sfy.pipeline.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(pipeline, "STRATEGIES", {first.name: first, second.name: second})
    return first, second


@pytest.mark.parametrize(
    ("first_measure", "second_measure", "winner"),
    [
        (Measure(1, 20, 10, 0, 0), Measure(2, 10, 5, 0, 0), "manifold-rows"),
        (Measure(1, 20, 10, 0, 0), Measure(1, 10, 50, 0, 0), "grid-routed"),
        (Measure(1, 20, 10, 0, 0), Measure(1, 20, 5, 0, 0), "grid-routed"),
        (Measure(1, 20, 10, 0, 0), Measure(1, 20, 10, 0, 0), "manifold-rows"),
    ],
)
def test_best_runs_both_strategies_in_one_clock_frame_and_picks_by_the_race_key(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
    first_measure: Measure,
    second_measure: Measure,
    winner: str,
) -> None:
    first, second = _race(monkeypatch, iron_plate_mk3)
    monkeypatch.setattr(
        pipeline,
        "measure",
        lambda placement, registry: (
            first_measure if placement is first.placement else second_measure
        ),
    )
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
        time_budget_s=10.0,
    )
    assert result.strategy == winner
    assert result.measure == (first_measure if winner == first.name else second_measure)
    assert first.calls == [(5.0, 105.0)]
    assert second.calls == [(5.0, 106.0)]
    assert result.report.ok and result.blueprint is not None


def test_an_overrunning_arm_cannot_win_or_extend_the_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    first, second = _race(monkeypatch, iron_plate_mk3, first_duration=8.0)
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
        time_budget_s=10.0,
    )
    assert first.calls == [(5.0, 105.0)]
    assert second.calls == [(2.0, 110.0)]
    assert result.strategy == "grid-routed"
    assert result.refused[0].reason == "layout exceeded the budget"


def test_a_result_at_the_arm_deadline_is_expired(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    _race(monkeypatch, iron_plate_mk3, first_duration=5.0)
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
        time_budget_s=10.0,
    )
    assert result.strategy == "grid-routed"
    assert result.refused[0].strategy == "manifold-rows"
    assert result.refused[0].reason == "layout exceeded the budget"


def test_a_strategy_that_refuses_is_reported_beside_the_winner(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
    tmp_path: Path,
) -> None:
    _race(
        monkeypatch,
        iron_plate_mk3,
        first_refusal=NoValidLayout(
            "rows exceed the designer depth",
            attempt_reasons=("needs another row",),
        ),
    )
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
    )
    assert result.strategy == "grid-routed"
    assert [(f.strategy, f.reason) for f in result.refused] == [
        ("manifold-rows", "rows exceed the designer depth"),
    ]
    assert result.refused[0].children[0].reason == "needs another row"
    sbp, cfg = pipeline.write(result, tmp_path)
    assert decode(read_sbp_file(sbp), pipeline.registry()) == result.placement
    assert read_sbpcfg(cfg.read_bytes()).description == result.placement.description


def test_both_refusing_raises_no_valid_layout_naming_both_causes(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    _race(
        monkeypatch,
        iron_plate_mk3,
        first_refusal=NoValidLayout("rows exceed the designer depth"),
        second_refusal=NoValidLayout("a belt could not be routed"),
    )
    with pytest.raises(NoValidLayout) as caught:
        pipeline.build(
            flow_url("iron-plate-60"),
            designer="mk3",
            flow=FLOWS / "iron-plate-60.csv",
        )
    assert [(f.strategy, f.reason) for f in caught.value.attempt_failures] == [
        ("manifold-rows", "rows exceed the designer depth"),
        ("grid-routed", "a belt could not be routed"),
    ]


def test_a_validator_error_disqualifies_an_otherwise_returned_layout(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    first, _ = _race(monkeypatch, iron_plate_mk3)
    failed = replace(
        iron_plate_mk3.report,
        findings=(Finding("geom.bounds", Severity.ERROR, "outside designer"),),
    )
    monkeypatch.setattr(
        pipeline,
        "validate",
        lambda placement, *args, **kwargs: (
            failed if placement is first.placement else iron_plate_mk3.report
        ),
    )
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
    )
    assert result.strategy == "grid-routed"
    assert "geom.bounds" in result.refused[0].reason


def test_explicit_strategy_runs_alone_with_the_whole_budget(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    first, second = _race(monkeypatch, iron_plate_mk3)
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
        strategy="grid-routed",
        time_budget_s=10,
    )
    assert result.strategy == "grid-routed"
    assert first.calls == [] and second.calls == [(10.0, 110.0)]


def test_encoding_failure_keeps_the_winners_identity_and_the_loser_cause(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    _race(
        monkeypatch,
        iron_plate_mk3,
        first_refusal=NoValidLayout("rows exceed the designer depth"),
    )

    def cannot_emit(*args: object, **kwargs: object) -> None:
        raise ValueError("missing format template")

    monkeypatch.setattr(pipeline, "emit", cannot_emit)
    result = pipeline.build(
        flow_url("iron-plate-60"),
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
    )
    assert result.strategy == "grid-routed"
    assert result.blueprint is None and result.record is None
    assert [failure.reason for failure in result.refused] == [
        "rows exceed the designer depth",
        "blueprint encoding failed: missing format template",
    ]


def test_an_exhausted_total_budget_does_not_start_the_second_strategy(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    first, second = _race(monkeypatch, iron_plate_mk3, first_duration=12.0)
    with pytest.raises(NoValidLayout) as caught:
        pipeline.build(
            flow_url("iron-plate-60"),
            designer="mk3",
            flow=FLOWS / "iron-plate-60.csv",
            time_budget_s=10,
        )
    assert first.calls == [(5.0, 105.0)]
    assert second.calls == []
    assert [(failure.strategy, failure.reason) for failure in caught.value.attempt_failures] == [
        ("manifold-rows", "layout exceeded the budget"),
        ("grid-routed", "layout exceeded the budget"),
    ]
