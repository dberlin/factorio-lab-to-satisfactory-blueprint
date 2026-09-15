"""The one ``flab2bp`` entry point, driven at a Satisfactory URL.

The exit codes are the DSP CLI's, which is the whole point of keeping one
command: ``0`` a blueprint was written, ``1`` the validator found errors and
they were not waived, ``2`` the URL, the spec or the flow was refused, ``3`` no
layout.  Every build here is pinned to a committed FactorioLab export, so
nothing about a rate or a machine count is written down in this file.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from flab2bp import cli
from flab2bp.layout.base import LayoutAttemptFailure
from flab2bp.sfy import pipeline as sfy_pipeline
from flab2bp.sfy.layout.validate import Finding, Report, Severity

FLOWS = Path(__file__).resolve().parent / "fixtures" / "sfy_flows"
FLOW = FLOWS / "iron-plate-60.csv"
#: A chain whose two rows do NOT pair machine for machine (its smelters and
#: constructors are not 1:1), so it is laid as two rows with a trunk between
#: them and wants more band than a Mk.1 has.  ``iron-plate*60`` stopped being
#: that example when Task 8d paired its rows into one; the corpus pins
#: ``iron-rod*60`` as ``rows exceed the designer depth`` in mk1 and mk2.
FLOW_TWO_ROWS = FLOWS / "iron-rod-60.csv"

DSP_URL = "https://factoriolab.github.io/dsp/list?o=iron-ingot*60&v=11"


def sfy_url(flow: Path = FLOW) -> str:
    """The URL the committed export was generated from: its own line 1."""
    return flow.read_text(encoding="utf-8").splitlines()[0].strip().strip('"')


def test_the_parser_takes_a_designer_mark_and_defaults_to_mk1() -> None:
    """``--designer`` is on the one parser, for both games.

    Its argparse default is ``None`` rather than ``mk1`` on purpose: the DSP
    arm refuses a designer that was ASKED for, and a stored ``mk1`` would make
    "said nothing" and "said mk1" indistinguishable.  ``mk1`` is what an sfy
    build without the flag is laid out in, which the run below asserts.
    """
    ap = cli.build_parser()
    assert ap.parse_args([DSP_URL]).designer is None
    assert ap.parse_args([DSP_URL, "--designer", "mk3"]).designer == "mk3"
    assert cli.DEFAULT_DESIGNER_MARK == "mk1"
    with pytest.raises(SystemExit):
        ap.parse_args([DSP_URL, "--designer", "mk4"])


def test_a_satisfactory_url_without_a_flow_exits_two_naming_both_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R1: FactorioLab's chosen flow is authoritative, so there is no build without one."""
    assert cli.main([sfy_url()]) == 2
    err = capsys.readouterr().err
    assert "--flow" in err and "--fetch-flow" in err


def test_a_designer_on_a_dyson_sphere_program_url_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A DSP build has no Blueprint Designer, so the flag is a mistake, not noise."""
    assert cli.main([DSP_URL, "--designer", "mk3"]) == 2
    assert "--designer" in capsys.readouterr().err


def test_a_pinned_satisfactory_build_writes_both_files_and_reports_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The headline run: one command, one directory, two files, exit 0."""
    code = cli.main(
        [sfy_url(), "--flow", str(FLOW), "--designer", "mk3", "-o", str(tmp_path / "out")]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err

    written = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert written == ["iron-plate-mk3.sbp", "iron-plate-mk3.sbpcfg"]

    head = captured.out.splitlines()[0]
    assert head.startswith("manifold-rows: ")
    for part in ("machines in ", " rows, ", " belts, ", " poles, ", " wires; "):
        assert part in head
    assert "power " in head and "MW (report figure)" in head
    assert "shards " in head
    assert "entries [iron-ore at x=" in head
    assert "exits [iron-plate at x=" in head
    assert "recipe selection pinned to the supplied flow" in captured.out
    assert "check(s) could not run:" in captured.out
    assert "VALIDATION ERRORS" not in captured.out


def test_a_build_that_does_not_fit_the_designer_exits_three_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two rows want more band than a Mk.1 has; the reason goes to stderr."""
    code = cli.main(
        [
            sfy_url(FLOW_TWO_ROWS),
            "--flow",
            str(FLOW_TWO_ROWS),
            "--designer",
            "mk1",
            "-o",
            str(tmp_path),
        ]
    )
    assert code == 3
    assert "rows exceed the designer depth" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_dsp_only_flags_are_ignored_on_the_satisfactory_path_with_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Said once, naming them, rather than eleven times or not at all."""
    code = cli.main(
        [
            sfy_url(),
            "--flow",
            str(FLOW),
            "--designer",
            "mk3",
            "-o",
            str(tmp_path),
            "--strategy",
            "freeform",
            "--band",
            "5x40",
        ]
    )
    assert code == 0
    ignored = [line for line in capsys.readouterr().err.splitlines() if "ignoring them" in line]
    assert len(ignored) == 1
    assert "--strategy" in ignored[0] and "--band" in ignored[0]


def test_a_named_build_is_written_under_that_name(tmp_path: Path) -> None:
    code = cli.main(
        [
            sfy_url(),
            "--flow",
            str(FLOW),
            "--designer",
            "mk3",
            "-o",
            str(tmp_path),
            "-n",
            "plates for the hub",
        ]
    )
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "plates-for-the-hub.sbp",
        "plates-for-the-hub.sbpcfg",
    ]


def test_a_flow_that_moves_a_fluid_exits_two_rather_than_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad spec is exit 2 even though ``SpecInfeasible`` is a ``NoValidLayout``."""
    flow = FLOWS / "plastic-10.csv"
    url = flow.read_text(encoding="utf-8").splitlines()[0].strip().strip('"')
    code = cli.main([url, "--flow", str(flow), "--designer", "mk3", "-o", str(tmp_path)])
    assert code == 2
    assert "fluids are M4" in capsys.readouterr().err


def test_a_url_naming_neither_game_falls_through_to_the_dsp_arm_and_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A URL the dispatcher cannot read is left to say so in the DSP arm's words.

    Refusing it at the dispatch would change what a DSP user is told about
    their own URL, in the one place that cannot know which game they meant.
    """
    assert cli.main(["https://factoriolab.github.io/nope/list?o=x&v=11"]) == 2
    assert "not supported" in capsys.readouterr().err


# --- the arms a real build does not reach ------------------------------------
#
# A clean `iron-plate-60` in a Mk.3 is the only end-to-end build this project
# can make today, and it passes the validator and encodes.  The three arms below
# -- validation errors withheld, validation errors waived, a placement that
# cannot be WRITTEN -- are reached by doctoring that one real build, so every
# field the report and the exit code read is a real one.


@pytest.fixture(scope="module")
def clean_build() -> sfy_pipeline.SfyBuild:
    return sfy_pipeline.build(sfy_url(), designer="mk3", flow=FLOW)


def _as(build: sfy_pipeline.SfyBuild, **changes: object) -> sfy_pipeline.SfyBuild:
    return replace(build, **changes)  # type: ignore[arg-type]


def _failing(report: Report) -> Report:
    """``report`` with one ERROR grafted on, keeping everything else it says."""
    finding = Finding("geom.bounds", Severity.ERROR, "a machine reaches outside the designer")
    return replace(report, findings=(*report.findings, finding))


def _serve(monkeypatch: pytest.MonkeyPatch, build: sfy_pipeline.SfyBuild) -> None:
    """Hand the CLI this build instead of solving one."""
    monkeypatch.setattr(sfy_pipeline, "build", lambda *a, **k: build)


def test_validation_errors_withhold_the_blueprint_with_exit_one(
    clean_build: sfy_pipeline.SfyBuild,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 1 and NO files: a blueprint that pastes and then does not run is the
    worst outcome available, so it is not written at all."""
    _serve(monkeypatch, _as(clean_build, report=_failing(clean_build.report)))
    assert cli.main([sfy_url(), "--flow", str(FLOW), "--designer", "mk3", "-o", str(tmp_path)]) == 1

    captured = capsys.readouterr()
    assert "refusing to emit an invalid blueprint" in captured.err
    assert "1 VALIDATION ERRORS: {'geom.bounds': 1}" in captured.out
    assert "geom.bounds: a machine reaches outside the designer" in captured.out
    assert list(tmp_path.iterdir()) == []


def test_allow_invalid_writes_the_files_anyway_and_exits_zero(
    clean_build: sfy_pipeline.SfyBuild,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The override is the DSP flag's, with the DSP flag's consequence."""
    _serve(monkeypatch, _as(clean_build, report=_failing(clean_build.report)))
    code = cli.main(
        [
            sfy_url(),
            "--flow",
            str(FLOW),
            "--designer",
            "mk3",
            "-o",
            str(tmp_path),
            "--allow-invalid",
        ]
    )
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "iron-plate-mk3.sbp",
        "iron-plate-mk3.sbpcfg",
    ]
    assert "VALIDATION ERRORS" in capsys.readouterr().out


def test_a_placement_that_cannot_be_written_exits_three_and_reports_the_refusal(
    clean_build: sfy_pipeline.SfyBuild,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``blueprint is None``: laid out and judged, then unwritable.

    This is the arm `flab2bp.pipeline`'s encoding-failure refusal mirrors, and
    it is also the only way `_sfy_report`'s refusals branch is reached today.
    """
    failure = LayoutAttemptFailure(
        candidate="iron-plate*60",
        strategy="manifold-rows",
        reason="blueprint encoding failed: the registry has no asset path for Recipe_Nope_C",
    )
    _serve(monkeypatch, _as(clean_build, blueprint=None, record=None, refused=(failure,)))
    assert cli.main([sfy_url(), "--flow", str(FLOW), "--designer", "mk3", "-o", str(tmp_path)]) == 3

    captured = capsys.readouterr()
    assert "1 refusal(s) with no layout to show:" in captured.out
    assert "manifold-rows/iron-plate*60: blueprint encoding failed" in captured.out
    assert "this build produced no blueprint" in captured.err
    assert list(tmp_path.iterdir()) == []


def test_writing_a_build_with_no_blueprint_refuses_rather_than_writing_half_a_pair(
    clean_build: sfy_pipeline.SfyBuild, tmp_path: Path
) -> None:
    """`write` is the other side of the same arm, and says why."""
    failure = LayoutAttemptFailure(
        candidate="iron-plate*60", strategy="manifold-rows", reason="blueprint encoding failed: x"
    )
    build = _as(clean_build, blueprint=None, record=None, refused=(failure,))
    with pytest.raises(ValueError, match="nothing to write"):
        sfy_pipeline.write(build, tmp_path)
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_every_dsp_only_flag_is_listed_so_a_new_one_cannot_be_ignored_silently() -> None:
    """`_DSP_ONLY_FLAGS` must account for every option that is not game-neutral.

    Derived from the parser rather than typed twice: a flag added to the DSP
    surface fails this test until someone CLASSIFIES it -- either as DSP-only,
    where the sfy arm then names it in its one "ignoring them" line, or as
    game-neutral here, which is a claim the sfy arm actually honours it.
    """
    neutral = {
        "help",
        "url",
        "flow",
        "fetch_flow",
        "fetch_timeout",
        "browser",
        "budget",
        "designer",
        "out",
        "name",
        "verbose",
        "allow_invalid",
    }
    parser = cli.build_parser()
    dests = {action.dest for action in parser._actions}
    assert set(cli._DSP_ONLY_FLAGS) == dests - neutral

    # And each one is spelled the way the parser spells it, so the stderr line
    # names a flag a reader can actually pass.
    spellings = {action.dest: set(action.option_strings) for action in parser._actions}
    for dest, flag in cli._DSP_ONLY_FLAGS.items():
        assert flag in spellings[dest], f"{dest} is not spelled {flag} by the parser"
