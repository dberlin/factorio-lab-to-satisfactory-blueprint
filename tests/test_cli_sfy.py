"""The one ``flab2bp`` entry point, driven at a Satisfactory URL.

The exit codes are the DSP CLI's, which is the whole point of keeping one
command: ``0`` a blueprint was written, ``1`` the validator found errors and
they were not waived, ``2`` the URL, the spec or the flow was refused, ``3`` no
layout.  Every build here is pinned to a committed FactorioLab export, so
nothing about a rate or a machine count is written down in this file.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from flab2bp import cli
from flab2bp.layout.base import LayoutAttemptFailure
from flab2bp.sfy import pipeline as sfy_pipeline
from flab2bp.sfy.codec import read_pair
from flab2bp.sfy.layout.emit import decode
from flab2bp.sfy.layout.model import pipe_ends
from flab2bp.sfy.layout.validate import Finding, Report, Severity
from flab2bp.sfy.sections.model import endpoint
from flab2bp.sfy.strategy_names import SFY_STRATEGY_CHOICES
from flab2bp.strategy_names import STRATEGY_CHOICES

FLOWS = Path(__file__).resolve().parent / "fixtures" / "sfy_flows"
FLOW = FLOWS / "iron-plate-60.csv"

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
        [
            sfy_url(),
            "--flow",
            str(FLOW),
            "--designer",
            "mk3",
            "--strategy",
            "sections",
            "-o",
            str(tmp_path / "out"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err

    written = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert written == ["iron-plate-mk3.sbp", "iron-plate-mk3.sbpcfg"]

    assert "VALIDATION ERRORS" not in captured.out


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
            "sections",
            "--band",
            "5x40",
        ]
    )
    assert code == 0
    ignored = [line for line in capsys.readouterr().err.splitlines() if "ignoring them" in line]
    assert len(ignored) == 1
    assert "--band" in ignored[0] and "--strategy" not in ignored[0]


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


def test_a_fluid_flow_writes_a_factory_with_an_accessible_residue_drain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flow = FLOWS / "plastic-10.csv"
    code = cli.main([sfy_url(flow), "--flow", str(flow), "--designer", "mk3", "-o", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    blueprint, _record = read_pair(tmp_path / "plastic-mk3.sbp", tmp_path / "plastic-mk3.sbpcfg")
    registry = sfy_pipeline.registry()
    placement = decode(blueprint, registry)
    assert len(placement.machines) == 1
    refinery = placement.machines[0]
    assert refinery.recipe_class == "Recipe_Plastic_C"
    assert refinery.clock == Fraction(1, 2)  # 10 plastic/min and 5 residue m³/min.
    ports = registry.buildables[refinery.class_name].ports
    residue = next(
        port.name for port in ports if port.kind == "pipe" and port.direction == "output"
    )
    supply = next(port.name for port in ports if port.kind == "pipe" and port.direction == "input")

    graph: dict[tuple[int, str], set[tuple[int, str]]] = defaultdict(set)
    linked = {end for link in placement.links for end in (link.a, link.b)}
    for link in placement.links:
        graph[link.a].add(link.b)
        graph[link.b].add(link.a)
    for actor in (*placement.pipes, *placement.pipe_attachments):
        nodes = [
            (actor.id, port.name)
            for port in registry.buildables[actor.class_name].ports
            if port.kind == "pipe"
        ]
        for node in nodes:
            graph[node].update(other for other in nodes if other != node)
    pending, reachable = [(refinery.id, residue)], set()
    while pending:
        node = pending.pop()
        if node not in reachable:
            reachable.add(node)
            pending.extend(graph[node] - reachable)
    assert (refinery.id, supply) not in reachable
    drains = []
    for pipe in placement.pipes:
        for index, port in enumerate(pipe_ends(registry, pipe.class_name)):
            node = (pipe.id, port)
            if node not in reachable or node in linked:
                continue
            _point, normal = endpoint(placement, *node, registry)
            if normal[2] > 0.999:
                assert pipe.snapped_passthroughs[index] is not None
                drains.append(node)
    assert len(drains) == 1


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
# The three arms below -- validation errors withheld, validation errors waived,
# a placement that cannot be WRITTEN -- are reached by doctoring a real clean
# build, so every field the report and the exit code read is a real one.


@pytest.fixture(scope="module")
def clean_build() -> sfy_pipeline.SfyBuild:
    return sfy_pipeline.build(
        sfy_url(),
        strategy="sections",
        designer="mk3",
        flow=FLOW,
    )


def _failing(report: Report) -> Report:
    """``report`` with one ERROR grafted on, keeping everything else it says."""
    finding = Finding("geom.bounds", Severity.ERROR, "a machine reaches outside the designer")
    return replace(report, findings=(*report.findings, finding))


def _serve(monkeypatch: pytest.MonkeyPatch, build: sfy_pipeline.SfyBuild) -> None:
    """Hand the CLI this build instead of solving one."""
    monkeypatch.setattr(sfy_pipeline, "build", lambda *a, **k: build)


def test_invalid_candidates_remain_refused_with_the_dsp_override(
    clean_build: sfy_pipeline.SfyBuild,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sfy_pipeline, "validate", lambda *args, **kwargs: _failing(clean_build.report)
    )
    code = cli.main(
        [
            sfy_url(),
            "--flow",
            str(FLOW),
            "--strategy",
            "sections",
            "--designer",
            "mk3",
            "-o",
            str(tmp_path),
            "--allow-invalid",
        ]
    )
    assert code == 3
    error = capsys.readouterr().err
    assert "--allow-invalid" in error and "ignoring" in error
    assert "geom.bounds" in error
    assert list(tmp_path.iterdir()) == []


def test_a_placement_that_cannot_be_written_exits_three_and_reports_the_refusal(
    clean_build: sfy_pipeline.SfyBuild,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An encoding refusal withholds both files and retains its diagnostics."""
    failure = LayoutAttemptFailure(
        candidate="iron-plate*60",
        strategy="sections",
        reason="blueprint encoding failed: the registry has no asset path for Recipe_Nope_C",
    )
    _serve(monkeypatch, replace(clean_build, blueprint=None, record=None, refused=(failure,)))
    assert cli.main([sfy_url(), "--flow", str(FLOW), "--designer", "mk3", "-o", str(tmp_path)]) == 3

    captured = capsys.readouterr()
    assert "blueprint encoding failed" in captured.out
    assert "this build produced no blueprint" in captured.err
    assert list(tmp_path.iterdir()) == []


def test_writing_a_build_with_no_blueprint_refuses_rather_than_writing_half_a_pair(
    clean_build: sfy_pipeline.SfyBuild, tmp_path: Path
) -> None:
    """`write` is the other side of the same arm, and says why."""
    failure = LayoutAttemptFailure(
        candidate="iron-plate*60", strategy="sections", reason="blueprint encoding failed: x"
    )
    build = replace(clean_build, blueprint=None, record=None, refused=(failure,))
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
        "strategy",
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
    }
    parser = cli.build_parser()
    dests = {action.dest for action in parser._actions}
    assert set(cli._DSP_ONLY_FLAGS) == dests - neutral

    # And each one is spelled the way the parser spells it, so the stderr line
    # names a flag a reader can actually pass.
    spellings = {action.dest: set(action.option_strings) for action in parser._actions}
    for dest, flag in cli._DSP_ONLY_FLAGS.items():
        assert flag in spellings[dest], f"{dest} is not spelled {flag} by the parser"


@pytest.mark.parametrize("strategy", SFY_STRATEGY_CHOICES)
def test_the_cli_accepts_sfy_strategy_choices_for_an_sfy_url(
    strategy: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Reaches the real flow boundary rather than rejecting the strategy.
    assert cli.main([sfy_url(), "--strategy", strategy]) == 2
    error = capsys.readouterr().err
    assert "--flow" in error and "--fetch-flow" in error


@pytest.mark.parametrize("strategy", STRATEGY_CHOICES)
def test_dsp_strategies_are_not_silently_ignored_for_sfy(
    strategy: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([sfy_url(), "--strategy", strategy]) == 2
    assert "--strategy" in capsys.readouterr().err


@pytest.mark.parametrize("strategy", ["sections"])
def test_sfy_strategies_are_refused_for_dsp(
    strategy: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([DSP_URL, "--strategy", strategy]) == 2
    assert "--strategy" in capsys.readouterr().err
