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
from fractions import Fraction
from pathlib import Path

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.sfy import pipeline
from flab2bp.sfy.codec import read_sbp_file, read_sbpcfg
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.emit import decode
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
        strategy="sections",
        designer="mk3",
        flow=FLOWS / "iron-plate-60.csv",
    )


def test_a_pinned_flow_builds_a_clean_blueprint_in_a_mk3_designer(
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    """The headline: FactorioLab's own flow, laid out and judged clean."""
    build = iron_plate_mk3
    assert build.strategy == "sections"
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


def test_plastic_build_preserves_crude_input_and_residue_export() -> None:
    build = pipeline.build(
        flow_url("plastic-10"),
        designer="mk3",
        flow=FLOWS / "plastic-10.csv",
    )
    assert build.report.ok
    assert build.blueprint is not None
    lanes = {lane.item_id: lane for lane in build.placement.stack_lanes}
    assert lanes["crude-oil"].input_per_second == build.spec.external_inputs["crude-oil"]
    assert (
        lanes["heavy-oil-residue"].output_per_second
        == build.spec.surplus_outputs["heavy-oil-residue"]
    )
    assert lanes["plastic"].output_per_second == build.spec.outputs["plastic"]
    assert decode(build.blueprint, pipeline.registry()) == build.placement


def test_reinforced_plates_keep_the_complete_mixed_floor_factory_connected() -> None:
    build = pipeline.build(
        flow_url("reinforced-iron-plate-10"),
        designer="mk3",
        flow=FLOWS / "reinforced-iron-plate-10.csv",
    )
    assert build.report.ok
    assert build.blueprint is not None
    assert len(build.placement.machines) == build.spec.machine_count == 14
    assert len({machine.pose.z for machine in build.placement.machines}) == 3
    assert build.placement.stack_height_cm <= build.designer.height_cm
    assert decode(build.blueprint, pipeline.registry()) == build.placement


def test_copper_alloy_keeps_full_flow_when_a_later_net_needs_routing_priority(
    tmp_path: Path,
) -> None:
    """A lower-rate ore branch must not starve behind the same earlier routes."""
    build = pipeline.build(
        flow_url("copper-ingot-alloy-480"),
        designer="mk2",
        flow=FLOWS / "copper-ingot-alloy-480.csv",
        time_budget_s=30,
    )
    assert build.report.ok
    assert build.spec.belt_item_id == "conveyor-belt-mk4"
    assert len(build.placement.machines) == build.spec.machine_count == 5
    assert {machine.recipe_class for machine in build.placement.machines} == {
        "Recipe_Alternate_CopperAlloyIngot_C"
    }
    assert sorted(machine.clock for machine in build.placement.machines) == [
        Fraction(4, 5),
        Fraction(1),
        Fraction(1),
        Fraction(1),
        Fraction(1),
    ]
    lanes = {lane.item_id: lane for lane in build.placement.stack_lanes}
    assert lanes["copper-ore"].input_per_second == Fraction(4)
    assert lanes["iron-ore"].input_per_second == Fraction(4)
    assert lanes["copper-ingot"].output_per_second == Fraction(8)
    registry = pipeline.registry()
    capacities = [
        registry.buildables[f"Build_ConveyorBeltMk{mark}_C"].belt_speed_per_min
        for mark in range(1, 6)
    ]
    for belt in build.placement.belts:
        required = belt.items_per_second * 120  # Native mSpeed is twice items/min.
        sufficient = [
            capacity for capacity in capacities if capacity is not None and capacity >= required
        ]
        assert registry.buildables[belt.class_name].belt_speed_per_min == min(sufficient)
    for item, lane in lanes.items():
        expected = (
            "Build_ConveyorLiftMk4_C" if item == "copper-ingot" else "Build_ConveyorLiftMk3_C"
        )
        for object_id, _ in (lane.bottom, lane.top):
            terminal = build.placement.by_id(object_id)
            assert terminal.class_name == expected
    sbp, cfg = pipeline.write(build, tmp_path)
    assert decode(read_sbp_file(sbp), pipeline.registry()) == build.placement
    assert read_sbpcfg(cfg.read_bytes()).description == build.placement.description


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


@pytest.mark.parametrize("strategy", ["best", "manifold-rows", "grid-routed"])
def test_retired_strategies_are_refused_before_building(strategy: str) -> None:
    with pytest.raises(ValueError, match="strategy"):
        pipeline.build(flow_url("iron-plate-60"), strategy=strategy)  # type: ignore[arg-type]


class _Planner:
    name = "sections"

    def __init__(self, placement: SfyPlacement, clock: list[float], duration: float = 0) -> None:
        self.placement = placement
        self.clock = clock
        self.duration = duration

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
        self.clock[0] += self.duration
        return self.placement


@pytest.mark.parametrize("duration", [10.0, 11.0])
def test_a_result_at_or_after_the_deadline_is_not_emitted(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
    duration: float,
) -> None:
    clock = [100.0]
    planner = _Planner(iron_plate_mk3.placement, clock, duration)
    monkeypatch.setattr(pipeline, "STRATEGIES", {"sections": planner})
    monkeypatch.setattr("flab2bp.sfy.pipeline.time.monotonic", lambda: clock[0])
    with pytest.raises(NoValidLayout) as caught:
        pipeline.build(
            flow_url("iron-plate-60"),
            designer="mk3",
            flow=FLOWS / "iron-plate-60.csv",
            time_budget_s=10,
        )
    assert caught.value.reason == "layout exceeded the budget"
    assert caught.value.attempt_failures[0].strategy == "sections"


def test_a_validator_error_refuses_an_otherwise_returned_layout(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    planner = _Planner(iron_plate_mk3.placement, [100.0])
    monkeypatch.setattr(pipeline, "STRATEGIES", {"sections": planner})
    failed = replace(
        iron_plate_mk3.report,
        findings=(Finding("geom.bounds", Severity.ERROR, "outside designer"),),
    )
    monkeypatch.setattr(pipeline, "validate", lambda *args, **kwargs: failed)
    with pytest.raises(NoValidLayout) as caught:
        pipeline.build(flow_url("iron-plate-60"), designer="mk3", flow=FLOWS / "iron-plate-60.csv")
    assert "geom.bounds" in caught.value.reason


def test_encoding_failure_returns_no_artifact_and_preserves_the_cause(
    monkeypatch: pytest.MonkeyPatch,
    iron_plate_mk3: pipeline.SfyBuild,
) -> None:
    planner = _Planner(iron_plate_mk3.placement, [100.0])
    monkeypatch.setattr(pipeline, "STRATEGIES", {"sections": planner})

    def cannot_emit(*args: object, **kwargs: object) -> None:
        raise ValueError("missing format template")

    monkeypatch.setattr(pipeline, "emit", cannot_emit)
    result = pipeline.build(
        flow_url("iron-plate-60"), designer="mk3", flow=FLOWS / "iron-plate-60.csv"
    )
    assert result.blueprint is None and result.record is None
    assert result.refused[0].reason == "blueprint encoding failed: missing format template"
