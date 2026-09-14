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

from pathlib import Path

import pytest

from flab2bp.layout.base import NoValidLayout, SpecInfeasible
from flab2bp.sfy import pipeline
from flab2bp.sfy.codec import read_sbp_file, read_sbpcfg
from flab2bp.sfy.layout.emit import decode

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
    """Two rows want 42 m of band; a Mk.1 has 32.  The refusal says so."""
    with pytest.raises(NoValidLayout) as caught:
        pipeline.build(
            flow_url("iron-plate-60"),
            designer="mk1",
            flow=FLOWS / "iron-plate-60.csv",
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
    assert caught.value.reason == "fluids are M4"


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
