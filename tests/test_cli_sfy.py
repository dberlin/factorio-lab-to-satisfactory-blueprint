"""The one ``flab2bp`` entry point, driven at a Satisfactory URL.

The exit codes are the DSP CLI's, which is the whole point of keeping one
command: ``0`` a blueprint was written, ``1`` the validator found errors and
they were not waived, ``2`` the URL, the spec or the flow was refused, ``3`` no
layout.  Every build here is pinned to a committed FactorioLab export, so
nothing about a rate or a machine count is written down in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flab2bp import cli

FLOWS = Path(__file__).resolve().parent / "fixtures" / "sfy_flows"
FLOW = FLOWS / "iron-plate-60.csv"

DSP_URL = "https://factoriolab.github.io/dsp/list?o=iron-ingot*60&v=11"


def sfy_url() -> str:
    """The URL the committed export was generated from: its own line 1."""
    return FLOW.read_text(encoding="utf-8").splitlines()[0].strip().strip('"')


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
    code = cli.main([sfy_url(), "--flow", str(FLOW), "--designer", "mk1", "-o", str(tmp_path)])
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
