"""The Satisfactory corpus gate: what it holds, and how it judges one cell.

The corpus RUN is ``scripts/sfy_audit.py`` rather than a test -- it builds every
entry in every designer mark and that is minutes, not the seconds this suite
budgets.  What is tested here is everything about the gate that does not need a
build: that every entry's flow is a committed FactorioLab capture the manifest
describes and the URL's own export, and that a cell is classified into the four
verdicts the plan names on outcomes this file hands it directly.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from flab2bp.bench.corpus import Tier
from flab2bp.bench.sfy_corpus import (
    CLEAN,
    DESIGNER_MARKS,
    EVIDENCE_DIR,
    FLOWS_DIR,
    RULED_CAUSES,
    SFY_CORPUS,
    SfyCorpusEntry,
    entry,
    is_ruled_cause,
)
from flab2bp.lab.flow import load_flow
from flab2bp.layout.base import LayoutAttemptFailure, NoValidLayout, SpecInfeasible
from flab2bp.sfy.layout.model import MachineObj, PoleObj, Pose, SfyPlacement
from flab2bp.sfy.layout.strategy import REFUSALS
from flab2bp.sfy.layout.validate import Finding, Report, Severity
from flab2bp.sfy.spec import Designer
from scripts import sfy_audit

# --- the corpus itself ------------------------------------------------------


def test_the_corpus_spans_the_tiers_the_plan_asked_for() -> None:
    """Early to late, and one fluid URL that is there to be refused."""
    assert len(SFY_CORPUS) == 12
    assert len({e.url_id for e in SFY_CORPUS}) == len(SFY_CORPUS)
    assert {e.tier for e in SFY_CORPUS} >= {Tier.TRIVIAL, Tier.SMALL, Tier.MID}
    fluid = entry("plastic-20")
    assert fluid.expected == "refuse"
    assert fluid.expected_cause == "fluids are M4"


@pytest.mark.parametrize("item", SFY_CORPUS, ids=lambda e: e.url_id)
def test_every_entry_names_a_committed_capture_the_manifest_describes(
    item: SfyCorpusEntry,
) -> None:
    """A corpus entry whose flow is not the manifest's file is not evidence.

    The flow is FactorioLab's own answer for this URL and the gate's whole claim
    rests on it being unedited, so this checks the same two numbers
    ``tests/sfy/test_rates.py`` checks over the whole fixture directory -- but
    from the corpus side, where an entry could otherwise point at a CSV nobody
    ever captured.
    """
    manifest = json.loads((FLOWS_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    rows = {row["file"]: row for row in manifest["flows"]}
    assert item.flow_file in rows, f"{item.url_id} names a flow with no manifest entry"
    raw = item.flow_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == rows[item.flow_file]["sha256"]
    assert len(raw) == rows[item.flow_file]["bytes"]


@pytest.mark.parametrize("item", SFY_CORPUS, ids=lambda e: e.url_id)
def test_every_entrys_flow_was_exported_from_that_entrys_own_url(item: SfyCorpusEntry) -> None:
    """``load_flow`` runs ``verify_provenance``, so this is the provenance check.

    A flow wired to the wrong entry parses perfectly and answers a different
    question -- the silent-wrong-answer the provenance check exists to stop --
    and the gate would report it as that entry's verdict.
    """
    load_flow(item.flow_path, url=item.url)


def test_every_ruled_cause_is_one_the_strategy_can_actually_raise() -> None:
    """A ruled cause the strategy never emits would green the gate on nothing."""
    assert set(RULED_CAUSES) <= set(REFUSALS)


def test_an_entry_that_expects_a_refusal_must_name_a_cause_the_rulings_allow() -> None:
    with pytest.raises(ValueError, match="no ruling allows"):
        _made(expects=(("mk3", "the solver did not feel like it"),), designers=("mk3",))


def test_an_entry_must_pin_exactly_the_marks_it_is_built_in() -> None:
    """``--strict`` compares a cell with its pin, so a mark with none is silent."""
    with pytest.raises(ValueError, match="one expectation per mark"):
        _made(expects=(("mk1", CLEAN),), designers=("mk1", "mk3"))
    with pytest.raises(ValueError, match="one expectation per mark"):
        _made(expects=(("mk1", CLEAN), ("mk3", CLEAN)), designers=("mk1",))


def test_an_entry_may_not_pin_the_same_mark_twice() -> None:
    with pytest.raises(ValueError, match="same mark twice"):
        _made(expects=(("mk1", CLEAN), ("mk1", "fluids are M4")), designers=("mk1",))


def test_an_entry_may_only_ask_for_designer_marks_that_exist() -> None:
    with pytest.raises(ValueError, match="mk9"):
        _made(expects=(("mk9", CLEAN),), designers=("mk9",))


def test_the_summary_expectation_is_the_largest_marks_pin() -> None:
    """``expected`` / ``expected_cause`` read the best chance the entry has."""
    built = _made(expects=(("mk1", "fluids are M4"), ("mk3", CLEAN)), designers=("mk1", "mk3"))
    assert built.largest == "mk3"
    assert (built.expected, built.expected_cause) == ("clean", None)
    refusing = _made(
        expects=(("mk1", "fluids are M4"), ("mk3", "row too deep")), designers=("mk1", "mk3")
    )
    assert (refusing.expected, refusing.expected_cause) == ("refuse", "row too deep")


def test_every_corpus_entry_pins_every_mark_it_is_run_in() -> None:
    """The regression pin only pins what it names, so it must name all of them."""
    for item in SFY_CORPUS:
        assert {mark for mark, _ in item.expects} == set(item.designers), item.url_id


def _made(*, expects: tuple[tuple[str, str], ...], designers: tuple[str, ...]) -> SfyCorpusEntry:
    return SfyCorpusEntry(
        "invented",
        "https://factoriolab.github.io/sfy/list?v=11&o=iron-plate*60",
        "iron-plate-60.csv",
        Tier.TRIVIAL,
        expects=expects,
        designers=designers,
    )


def test_a_cause_no_ruling_names_is_not_ruled() -> None:
    assert is_ruled_cause("fluids are M4")
    assert not is_ruled_cause("corridor assignment exceeded the budget")


# --- how one cell is judged -------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Built:
    """The part of an ``SfyBuild`` a verdict reads, without building one."""

    report: Report
    placement: SfyPlacement
    refused: tuple[LayoutAttemptFailure, ...] = ()


def _placement() -> SfyPlacement:
    return SfyPlacement(
        designer=Designer(mark="mk3", dims=(12, 12, 6), foundation_cm=800.0),
        machines=(
            MachineObj(1, "Desc_SmelterMk1_C", Pose(0.0, 0.0, 0.0, 0.0), "Recipe_IngotIron_C"),
            MachineObj(
                2, "Desc_ConstructorMk1_C", Pose(100.0, 0.0, 0.0, 0.0), "Recipe_IronPlate_C"
            ),
        ),
        poles=(PoleObj(3, "Desc_PowerPoleMk1_C", Pose(0.0, 100.0, 0.0, 0.0)),),
    )


def _raises(exc: Exception) -> sfy_audit.BuildFn:
    def build(url: str, *, designer: str, time_budget_s: float, flow: Path) -> _Built:
        raise exc

    return build


def _returns(built: _Built) -> sfy_audit.BuildFn:
    def build(url: str, *, designer: str, time_budget_s: float, flow: Path) -> _Built:
        return built

    return build


def _entry() -> SfyCorpusEntry:
    return entry("iron-plate-60")


def test_a_refusal_with_a_ruled_cause_is_reported_as_refused_and_keeps_the_gate_green() -> None:
    cell = sfy_audit.run_cell(
        _entry(),
        "mk1",
        15.0,
        build=_raises(
            NoValidLayout(
                "rows exceed the designer depth",
                spec_label="iron-plate*60",
                attempt_reasons=("2 rows want 4200 cm of band and the mk1 designer is 3200 cm",),
            )
        ),
    )
    assert cell.verdict == "REFUSED"
    assert cell.cause == "rows exceed the designer depth"
    assert "4200 cm" in cell.detail
    assert cell.gate_ok


def test_a_refusal_with_a_cause_no_ruling_names_is_still_refused_and_fails_the_gate() -> None:
    """The distinction the gate exists for: refusing is honest, but not licensed.

    ``corridor assignment exceeded the budget`` is a real cause the strategy can
    raise and it is deliberately NOT in the ruled set: a cell that refuses
    because it ran out of seconds is a cell nobody has shown fits.
    """
    cell = sfy_audit.run_cell(
        _entry(),
        "mk1",
        15.0,
        build=_raises(NoValidLayout("corridor assignment exceeded the budget")),
    )
    assert cell.verdict == "REFUSED"
    assert not cell.gate_ok


def test_a_spec_the_rate_model_refuses_is_a_refusal_carrying_its_own_cause() -> None:
    """``SpecInfeasible`` arrives before any layout and is a result, not a crash."""
    cell = sfy_audit.run_cell(
        entry("plastic-20"),
        "mk1",
        15.0,
        build=_raises(SpecInfeasible("fluids are M4", item="plastic*20")),
    )
    assert cell.verdict == "REFUSED"
    assert cell.cause == "fluids are M4"
    assert cell.gate_ok


def test_a_build_the_validator_rejects_is_invalid_and_counts_the_failing_checks() -> None:
    cell = sfy_audit.run_cell(
        _entry(),
        "mk3",
        15.0,
        build=_returns(
            _Built(
                report=Report(
                    findings=(
                        Finding("flow.capacity", Severity.ERROR, "belt over its rate"),
                        Finding("flow.capacity", Severity.ERROR, "belt over its rate"),
                        Finding("geom.bounds", Severity.ERROR, "outside the designer"),
                        Finding("belt.capsule", Severity.INFO, "partial rule"),
                    ),
                    checks_run=("flow.capacity", "geom.bounds"),
                    skipped=("belt.capsule",),
                ),
                placement=_placement(),
            )
        ),
    )
    assert cell.verdict == "INVALID"
    assert cell.checks == ("flow.capacity x2", "geom.bounds x1")
    assert not cell.gate_ok


def test_a_clean_build_records_what_it_placed() -> None:
    cell = sfy_audit.run_cell(
        _entry(),
        "mk3",
        15.0,
        build=_returns(_Built(report=Report(findings=()), placement=_placement())),
    )
    assert cell.verdict == "CLEAN"
    assert (cell.machines, cell.belts, cell.poles, cell.wires) == (2, 0, 1, 0)
    assert cell.gate_ok


def test_a_placement_that_could_not_be_written_is_a_refusal_the_gate_fails_on() -> None:
    """``build`` returns rather than raises when emit refuses; it is not CLEAN.

    The report can be spotless and there still be no blueprint -- a class the
    corpus has no template of.  Reading only ``report.ok`` would call that a
    clean cell and hand over nothing.
    """
    cell = sfy_audit.run_cell(
        _entry(),
        "mk3",
        15.0,
        build=_returns(
            _Built(
                report=Report(findings=()),
                placement=_placement(),
                refused=(
                    LayoutAttemptFailure(
                        candidate="iron-plate*60",
                        strategy="manifold-rows",
                        reason="blueprint encoding failed: no template for Desc_Foo_C",
                    ),
                ),
            )
        ),
    )
    assert cell.verdict == "REFUSED"
    assert "no template" in cell.cause
    assert not cell.gate_ok


def test_an_unexpected_exception_is_a_crash_that_names_its_type() -> None:
    cell = sfy_audit.run_cell(_entry(), "mk3", 15.0, build=_raises(KeyError("Desc_Foo_C")))
    assert cell.verdict == "CRASH"
    assert cell.cause == "KeyError"
    assert "Desc_Foo_C" in cell.detail
    assert not cell.gate_ok


def test_a_cell_that_never_ran_is_never_mistaken_for_a_clean_one() -> None:
    cell = sfy_audit.not_run(_entry(), "mk2", "the wall-clock cap expired")
    assert cell.verdict == "NOT RUN"
    assert not cell.gate_ok


# --- the strict gate: did the cell do what the corpus pins it to do? ---------


def _clean_cell(url_id: str, mark: str) -> sfy_audit.Cell:
    return sfy_audit.run_cell(
        entry(url_id),
        mark,
        15.0,
        build=_returns(_Built(report=Report(findings=()), placement=_placement())),
    )


def _refusing_cell(url_id: str, mark: str, cause: str) -> sfy_audit.Cell:
    return sfy_audit.run_cell(entry(url_id), mark, 15.0, build=_raises(NoValidLayout(cause)))


def test_a_cell_that_did_what_the_corpus_pins_it_to_do_passes_both_gates() -> None:
    clean = _clean_cell("iron-plate-60", "mk3")  # pinned clean
    refused = _refusing_cell("plastic-20", "mk1", "fluids are M4")  # pinned that cause
    for cell in (clean, refused):
        assert cell.as_pinned, cell
        assert cell.gate_ok and cell.strict_ok


def test_a_cell_that_starts_building_where_the_corpus_pins_a_refusal_only_fails_strict() -> None:
    """Progress must not turn the default gate red, and must not go unnoticed."""
    cell = _clean_cell("screw-120", "mk3")  # pinned `rows exceed the designer depth`
    assert cell.verdict == "CLEAN"
    assert cell.gate_ok
    assert not cell.as_pinned
    assert not cell.strict_ok
    assert sfy_audit.exit_code([cell]) == 0
    assert sfy_audit.exit_code([cell], strict=True) != 0


def test_a_refusal_whose_cause_changed_to_another_ruled_one_only_fails_strict() -> None:
    cell = _refusing_cell("screw-120", "mk3", "rows exceed the designer width")
    assert cell.gate_ok and not cell.as_pinned
    assert sfy_audit.exit_code([cell]) == 0
    assert sfy_audit.exit_code([cell], strict=True) != 0


def test_a_cell_that_starts_refusing_where_the_corpus_pins_a_clean_build_fails_strict() -> None:
    cell = _refusing_cell("iron-plate-60", "mk3", "rows exceed the designer depth")
    assert cell.gate_ok and not cell.as_pinned
    assert sfy_audit.exit_code([cell], strict=True) != 0


def test_strict_only_ever_narrows_the_gate() -> None:
    """A cell the default gate already fails cannot be rescued by a pin."""
    crashed = sfy_audit.run_cell(_entry(), "mk3", 15.0, build=_raises(KeyError("x")))
    assert not crashed.gate_ok and not crashed.strict_ok
    for cell in (_clean_cell("iron-plate-60", "mk3"), crashed):
        assert cell.strict_ok <= cell.gate_ok


def test_a_cell_with_nothing_pinned_is_not_reported_as_drift() -> None:
    """``--designer`` can ask for a mark an entry does not pin; say nothing."""
    cell = sfy_audit.Cell(url_id="x", designer="mk3", verdict="CLEAN")
    assert cell.expected == ""
    assert cell.as_pinned


def test_the_report_names_the_cells_that_moved_off_their_pin() -> None:
    moved = _clean_cell("screw-120", "mk3")
    text = sfy_audit.render_report([moved], head="abc1234", budget_s=15.0, dirty=False)
    assert "What moved off its pin" in text
    assert "screw-120" in text
    assert "PASS" in text, "the default gate is blind to a pin moving"
    strict = sfy_audit.render_report(
        [moved], head="abc1234", budget_s=15.0, dirty=False, strict=True
    )
    assert "FAIL" in strict
    assert "--strict" in strict


def test_the_exit_code_is_zero_only_when_every_cell_passes_the_gate() -> None:
    clean = sfy_audit.run_cell(
        _entry(),
        "mk3",
        15.0,
        build=_returns(_Built(report=Report(findings=()), placement=_placement())),
    )
    crashed = sfy_audit.run_cell(_entry(), "mk3", 15.0, build=_raises(KeyError("x")))
    assert sfy_audit.exit_code([clean]) == 0
    assert sfy_audit.exit_code([clean, crashed]) != 0
    assert sfy_audit.exit_code([]) != 0, "an audit of nothing is not a clean audit"


def test_the_report_states_the_gate_verdict_the_commit_and_every_cell() -> None:
    clean = sfy_audit.run_cell(
        _entry(),
        "mk3",
        15.0,
        build=_returns(_Built(report=Report(findings=()), placement=_placement())),
    )
    refused = sfy_audit.run_cell(
        entry("plastic-20"),
        "mk1",
        15.0,
        build=_raises(SpecInfeasible("fluids are M4", item="plastic*20")),
    )
    text = sfy_audit.render_report([clean, refused], head="abc1234", budget_s=15.0, dirty=False)
    assert "PASS" in text
    assert "abc1234" in text
    assert "iron-plate-60" in text and "plastic-20" in text
    assert "fluids are M4" in text
    assert "what refuses and why" in text


def test_the_report_says_FAIL_and_names_the_cell_when_one_misses() -> None:
    crashed = sfy_audit.run_cell(_entry(), "mk3", 15.0, build=_raises(KeyError("Desc_Foo_C")))
    text = sfy_audit.render_report([crashed], head="abc1234", budget_s=15.0, dirty=True)
    assert "FAIL" in text
    assert "CRASH" in text
    assert "dirty" in text


# --- where the report lands -------------------------------------------------


def _whole_matrix() -> list[sfy_audit.Cell]:
    """A verdict on every corpus square, without building any of them."""
    return [
        sfy_audit.Cell(url_id=item.url_id, designer=mark, verdict="CLEAN")
        for item in SFY_CORPUS
        for mark in item.designers
    ]


def test_a_run_over_the_whole_matrix_writes_the_committed_evidence() -> None:
    path, committed = sfy_audit.report_path(_whole_matrix(), today=date(2026, 9, 14))
    assert committed
    assert path == EVIDENCE_DIR / "sfy-m2-audit-2026-09-14.md"


def test_a_run_over_one_mark_leaves_the_committed_evidence_alone() -> None:
    """A partial report is not a claim about the matrix, so it writes untracked."""
    cells = [cell for cell in _whole_matrix() if cell.designer == "mk1"]
    path, committed = sfy_audit.report_path(cells, marks=["mk1"], today=date(2026, 9, 14))
    assert not committed
    assert path.parent.parts[-2:] == ("out", "sfy")
    assert path.name == "audit-2026-09-14-mk1-all-entries.md"
    assert EVIDENCE_DIR not in path.parents


def test_a_run_over_one_entry_names_the_entry_in_the_untracked_file() -> None:
    cells = [cell for cell in _whole_matrix() if cell.url_id == "plastic-20"]
    path, committed = sfy_audit.report_path(cells, only=["plastic-20"], today=date(2026, 9, 14))
    assert not committed
    assert path.name == "audit-2026-09-14-all-marks-plastic-20.md"


def test_a_run_the_wall_clock_cap_cut_off_does_not_count_as_the_whole_matrix() -> None:
    """The overwrite this rule exists to stop: NOT RUN cells over the evidence."""
    cells = _whole_matrix()
    cells[-1] = sfy_audit.Cell(
        url_id=cells[-1].url_id, designer=cells[-1].designer, verdict="NOT RUN"
    )
    assert not sfy_audit.covers_matrix(cells)
    path, committed = sfy_audit.report_path(cells, today=date(2026, 9, 14))
    assert not committed
    assert path.name == "audit-2026-09-14-all-marks-all-entries.md"


def test_an_explicit_report_path_wins_over_either_default() -> None:
    asked = Path("somewhere/else.md")
    for cells in (_whole_matrix(), []):
        path, committed = sfy_audit.report_path(cells, requested=asked)
        assert path == asked
        assert not committed, "a named path is never the committed evidence by default"


def test_the_marks_a_cell_may_be_run_in_are_the_designers_the_spec_can_size() -> None:
    assert DESIGNER_MARKS == ("mk1", "mk2", "mk3")


# --- what importing the corpus costs ----------------------------------------


def _import_probe() -> dict[str, list[str]]:
    """Import the corpus in a fresh interpreter and report what came with it.

    A fresh one is the only honest check: inside this interpreter the DSP
    modules are long since imported by other tests.  Two questions are asked at
    once -- what the bake-off package costs, and what ``sfy_corpus`` adds on top
    of the one module it genuinely needs (``bench.corpus``, for ``Tier``).
    """
    probe = """
import importlib, json, sys
importlib.import_module("flab2bp.bench.corpus")
before = {m for m in sys.modules if m.startswith("flab2bp.dsp")}
importlib.import_module("flab2bp.bench.sfy_corpus")
after = {m for m in sys.modules if m.startswith("flab2bp.dsp")}
print(json.dumps({
    "added": sorted(after - before),
    "bench": sorted(m for m in sys.modules if m.startswith("flab2bp.bench.")),
    "layout": sorted(m for m in sys.modules if m.startswith("flab2bp.layout")),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=FLOWS_DIR.parents[2],
    )
    parsed: dict[str, list[str]] = json.loads(result.stdout.strip().splitlines()[-1])
    return parsed


def test_importing_the_satisfactory_corpus_adds_no_dsp_module_of_its_own() -> None:
    """The Satisfactory corpus must not drag MORE of the DSP stack behind it.

    Said plainly, because this test is easy to read as more than it is:
    importing ``flab2bp.bench.sfy_corpus`` today loads NINE ``flab2bp.dsp``
    modules.  It reuses exactly one thing from the DSP side -- ``Tier``, out of
    ``flab2bp.bench.corpus`` -- and THAT module imports
    ``flab2bp.rates.CandidatePolicy``, the DSP rate solver, which brings
    ``dsp.catalog``, ``registry``, ``rules``, ``colliders``, ``provenance``,
    ``quaternion`` and the two geometry kernels with it.

    This test pins that seam where it is; it does not close it.  After
    ``bench.corpus`` is in, reading ``sfy_corpus`` must add no FURTHER DSP
    module -- so a new import here is caught, while the nine already on the
    other side of ``Tier`` stay.  Closing it means moving ``Tier`` into a leaf
    module that imports nothing, which is an M3 chore: ``Tier`` is a name the
    DSP corpus reads too, so moving it is a change to the other game's gate and
    does not belong in a Satisfactory fix round.
    """
    probe = _import_probe()
    assert probe["added"] == [], (
        "importing flab2bp.bench.sfy_corpus pulled DSP modules that "
        f"flab2bp.bench.corpus had not already pulled: {probe['added']}"
    )


def test_importing_the_satisfactory_corpus_loads_no_placer_or_router() -> None:
    """Shared layout primitives are fine; the DSP search is not.

    ``flab2bp.layout.budget`` and ``friends`` are the vocabulary both games
    share and the Satisfactory strategy genuinely uses.  The placer, the router
    and their Cython kernels are the DSP bake-off, which a list of URLs has no
    use for and which used to arrive through this package's ``__init__``.
    """
    loaded = set(_import_probe()["layout"])
    for heavy in (
        "flab2bp.layout.freeform",
        "flab2bp.layout.routing_domain",
        "flab2bp.layout.geometric_router",
        "flab2bp.layout.finalize",
        "flab2bp.layout._geometric_kernel",
        "flab2bp.layout._sequence_kernel",
    ):
        assert heavy not in loaded, f"{heavy} was imported by reading the corpus"


def test_importing_the_satisfactory_corpus_does_not_run_the_bake_off_package() -> None:
    """``flab2bp/bench/__init__.py`` re-exports lazily, and must keep doing so.

    Eagerly, that ``__init__`` imported ``runner`` -> ``layout.freeform`` ->
    ``routing_domain`` and the Cython kernels: 44 modules, none of which a list
    of Satisfactory URLs has any use for.
    """
    loaded = set(_import_probe()["bench"])
    for heavy in (
        "flab2bp.bench.runner",
        "flab2bp.bench.crossvalidate",
        "flab2bp.bench.metrics",
        "flab2bp.bench.regression",
        "flab2bp.bench.report",
        "flab2bp.bench.scoring",
    ):
        assert heavy not in loaded, f"{heavy} was imported by reading the corpus"


def test_the_bake_off_package_still_re_exports_every_name_it_used_to() -> None:
    """Lazy must be invisible: the same names, from the same place."""
    import flab2bp.bench as bench

    assert set(bench.__all__) == set(dir(bench))
    assert bench.Tier is Tier
    assert callable(bench.measure)
    with pytest.raises(AttributeError, match="no attribute 'not_a_thing'"):
        bench.not_a_thing  # noqa: B018 - the point is the attribute access
