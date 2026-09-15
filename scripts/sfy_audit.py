"""Does every Satisfactory corpus URL build, cleanly, right now?

    uv run python scripts/sfy_audit.py                     # every entry, every mark
    uv run python scripts/sfy_audit.py --designer mk1      # one mark
    uv run python scripts/sfy_audit.py --budget 15         # seconds per cell
    uv run python scripts/sfy_audit.py --only plastic-20   # one entry
    uv run python scripts/sfy_audit.py --strict            # also pin every outcome
    uv run python scripts/sfy_audit.py --no-report         # do not write evidence

Exits non-zero if any cell misses, so it works as a gate.

WHERE THE REPORT LANDS
----------------------
The committed evidence under ``docs/`` is a claim about the whole matrix, so
only a run that covered the whole matrix writes it.  A narrower run -- one mark,
one entry, or one the ``--max-seconds`` cap stopped partway -- writes an
untracked ``out/sfy/audit-<date>-<marks>-<entries>.md`` and says on stdout that
it left the evidence alone.  ``--report PATH`` overrides either.

TWO GATES OVER ONE RUN
----------------------
The default gate asks only whether each cell is CLEAN or refuses for a reason a
ruling allows.  It is deliberately blind to WHICH ruled reason: a build that
starts fitting where it used to refuse, or refuses on width where it used to
refuse on depth, is progress or a wash, and a gate that went red on either
would be a gate against improvement.

``--strict`` asks the other question -- did each cell do what the corpus says it
does -- against a per-mark pin that is a measurement rather than a wish.  That
makes the same run a regression pin, and the two together are what a change
should be read against: the default one says nothing broke, the strict one says
what moved.

WHY THIS IS A SCRIPT AND NOT A TEST
-----------------------------------
Every cell decompresses the blueprint corpus, parses a 1.2 MB registry and lays
a whole build out; the matrix is minutes and the suite is seconds.  What IS a
test is everything in here that does not need a build: ``tests/sfy/test_corpus.py``
drives :func:`run_cell` with outcomes it constructs directly, so the four
verdicts and the gate rule are covered without the matrix.

THE FIVE THINGS A CELL CAN BE, AND WHY THE DIFFERENCE MATTERS
-------------------------------------------------------------
* ``CLEAN`` -- laid out, validated with no ``ERROR`` finding, and written.
* ``REFUSED`` -- the strategy or the rate model raised, with a named cause.
  Honest, and the better failure: nothing broken is emitted.  It is a RESULT
  rather than a miss when the cause is one the plan ruled on
  (:data:`~flab2bp.bench.sfy_corpus.RULED_CAUSES`) -- M2 lays one level of rows
  in one Blueprint Designer and does not carry fluids, and a chain that wants
  more than that has been refused for a reason somebody already agreed to.  A
  refusal with any OTHER cause fails the gate: the point of the ruled list is
  that "it refused" is not on its own a defence.
* ``INVALID`` -- a placement the validator rejected.  Worse than refusing,
  because a blueprint that pastes and then does not run is the one outcome
  nobody discovers until they are standing in front of it in game.
* ``CRASH`` -- an unexpected exception.  Always a bug here.
* ``NOT RUN`` -- the wall-clock cap expired first.  Not a verdict on the cell, a
  verdict on this run, and counted as a miss so a truncated audit can never be
  mistaken for a clean one.

WHY IT IS SERIAL
----------------
The DSP audit fans out over processes because CP-SAT saturates a core for tens
of seconds per cell.  Nothing here solves: the manifold strategy is arithmetic
over rows and the expensive part is the validator, which is CPU-bound but
short.  One process keeps the wall times in the report comparable with each
other, which is the only reason they are recorded.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Final, Protocol

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from flab2bp.bench.sfy_corpus import (  # noqa: E402
    CLEAN,
    DESIGNER_MARKS,
    EVIDENCE_DIR,
    RULED_CAUSES,
    SFY_CORPUS,
    SfyCorpusEntry,
    is_ruled_cause,
)
from flab2bp.layout.base import LayoutAttemptFailure, NoValidLayout  # noqa: E402
from flab2bp.sfy import pipeline  # noqa: E402
from flab2bp.sfy.layout.model import SfyPlacement  # noqa: E402
from flab2bp.sfy.layout.validate import Report  # noqa: E402

#: Seconds one cell may spend laying out, matching the plan's own
#: ``--budget 15``.  Nothing in this corpus comes near it -- the manifold
#: strategy refuses on arithmetic or lays the rows out in well under a second --
#: so a cell that ever reached this number would itself be the finding.
DEFAULT_BUDGET_S: Final = 15.0


class BuiltBlueprint(Protocol):
    """The part of :class:`~flab2bp.sfy.pipeline.SfyBuild` a verdict reads.

    A protocol rather than the class itself so ``tests/sfy/test_corpus.py`` can
    hand :func:`run_cell` a validator report and a placement directly, without
    solving a spec to get at the three attributes that decide the verdict.
    """

    @property
    def report(self) -> Report: ...

    @property
    def placement(self) -> SfyPlacement: ...

    @property
    def refused(self) -> tuple[LayoutAttemptFailure, ...]: ...


class BuildFn(Protocol):
    """How :func:`run_cell` reaches a build.  :func:`flab2bp.sfy.pipeline.build`
    satisfies it, and so does a test's stand-in."""

    def __call__(
        self, url: str, *, designer: str, time_budget_s: float, flow: Path
    ) -> BuiltBlueprint: ...


@dataclass(frozen=True, slots=True)
class Cell:
    """One entry built in one designer mark, and what came of it."""

    url_id: str
    designer: str
    verdict: str
    #: The refusal cause, or the exception type on a ``CRASH``.  Empty on a
    #: ``CLEAN`` or ``INVALID`` cell, which have nothing of the kind to name.
    cause: str = ""
    #: What the strategy said beyond the cause -- the centimetres a refusal
    #: measured, or the exception's message.  This is the part a reader needs to
    #: tell "5 rows want 11000 cm" from "2 rows want 4200 cm".
    detail: str = ""
    #: ``<check id> xN`` for every check with an ``ERROR`` finding, on INVALID.
    checks: tuple[str, ...] = ()
    machines: int = 0
    belts: int = 0
    poles: int = 0
    wires: int = 0
    seconds: float = 0.0
    #: What the corpus pins THIS mark to do: ``CLEAN`` or a refusal cause.
    #: Empty when the entry pins nothing for it, which makes ``--strict`` say
    #: nothing rather than guess.
    expected: str = ""

    @property
    def gate_ok(self) -> bool:
        """Whether this cell leaves the default gate green.

        The plan's rule, verbatim: CLEAN, or REFUSED with a cause a ruling
        allows.  ``INVALID``, ``CRASH``, ``NOT RUN`` and an unruled refusal all
        miss.
        """
        if self.verdict == "CLEAN":
            return True
        return self.verdict == "REFUSED" and is_ruled_cause(self.cause)

    @property
    def as_pinned(self) -> bool:
        """Whether this cell did what the corpus pins it to do.

        Deliberately not part of :attr:`gate_ok`: an entry that starts BUILDING
        where it used to refuse is progress, and a gate that went red on it
        would be a gate against improvement.  ``--strict`` asks this question
        instead, which turns the same corpus into a regression pin -- a cause
        that changed, a clean build that started refusing, a refusal that
        started building.
        """
        if not self.expected:
            return True
        if self.expected == CLEAN:
            return self.verdict == "CLEAN"
        return self.verdict == "REFUSED" and self.cause == self.expected

    @property
    def strict_ok(self) -> bool:
        """Both gates at once: ``--strict`` narrows, it never widens."""
        return self.gate_ok and self.as_pinned

    def ok(self, *, strict: bool) -> bool:
        """Whether this cell passes the gate the run was asked for."""
        return self.strict_ok if strict else self.gate_ok

    @property
    def summary(self) -> str:
        """The verdict as a report cell: ``REFUSED(fluids are M5)``."""
        if self.verdict == "REFUSED":
            return f"REFUSED({self.cause})"
        if self.verdict == "CRASH":
            return f"CRASH({self.cause})"
        if self.verdict == "INVALID":
            return f"INVALID({', '.join(self.checks)})"
        return self.verdict


def run_cell(
    entry: SfyCorpusEntry,
    designer: str,
    budget_s: float,
    *,
    build: BuildFn = pipeline.build,
) -> Cell:
    """Build one entry in one mark and classify what happened.

    Every failure mode of ``build`` is a verdict rather than a traceback, which
    is what lets the gate distinguish the refusal it licenses from the crash it
    does not.
    """
    started = perf_counter()
    try:
        built = build(entry.url, designer=designer, time_budget_s=budget_s, flow=entry.flow_path)
    except NoValidLayout as exc:
        # `SpecInfeasible` is a subclass, so a fluid chain arrives here too:
        # the rate model refusing before any layout is the same shape of result
        # as the strategy refusing after one.
        return _cell(
            entry,
            designer,
            "REFUSED",
            started,
            cause=exc.reason,
            detail="; ".join(exc.attempt_reasons),
        )
    except Exception as exc:  # noqa: BLE001 - a gate that let one through is not a gate
        return _cell(entry, designer, "CRASH", started, cause=type(exc).__name__, detail=str(exc))

    if built.refused:
        # `build` returns rather than raises when the placement was judged and
        # then could not be WRITTEN.  The report can be spotless and there still
        # be no blueprint, so reading `report.ok` alone would call this clean.
        return _cell(
            entry,
            designer,
            "REFUSED",
            started,
            cause="; ".join(f.reason for f in built.refused),
            placement=built.placement,
        )

    errors = Counter(finding.check for finding in built.report.errors)
    if errors:
        return _cell(
            entry,
            designer,
            "INVALID",
            started,
            checks=tuple(f"{check} x{count}" for check, count in sorted(errors.items())),
            detail=built.report.errors[0].message,
            placement=built.placement,
        )
    return _cell(entry, designer, "CLEAN", started, placement=built.placement)


def not_run(entry: SfyCorpusEntry, designer: str, why: str) -> Cell:
    """A cell this run never reached.  Counted as a miss, never as a pass."""
    return Cell(
        url_id=entry.url_id,
        designer=designer,
        verdict="NOT RUN",
        detail=why,
        expected=_expected(entry, designer),
    )


def exit_code(cells: Sequence[Cell], *, strict: bool = False) -> int:
    """0 only when there is at least one cell and every one of them passes."""
    if not cells:
        return 1
    return 0 if all(cell.ok(strict=strict) for cell in cells) else 1


def render_report(
    cells: Sequence[Cell],
    *,
    head: str,
    budget_s: float,
    dirty: bool,
    cpu_load: float | None = None,
    marks: Sequence[str] = DESIGNER_MARKS,
    strict: bool = False,
) -> str:
    """The evidence report: every cell, the gate verdict, and what refuses."""
    passed = exit_code(cells, strict=strict) == 0
    tally = Counter(cell.verdict for cell in cells)
    ruled = sum(1 for cell in cells if cell.verdict == "REFUSED" and cell.gate_ok)
    unruled = tally["REFUSED"] - ruled
    drifted = [cell for cell in cells if not cell.as_pinned]

    lines = [
        f"# Satisfactory M2 corpus audit, {date.today().isoformat()}",
        "",
        f"**Gate{' (--strict)' if strict else ''}: {'PASS' if passed else 'FAIL'}** -- "
        f"{tally['CLEAN']} CLEAN, {ruled} expected REFUSED, "
        f"{unruled + tally['INVALID'] + tally['CRASH'] + tally['NOT RUN']} other "
        f"({unruled} unruled refusal, {tally['INVALID']} INVALID, "
        f"{tally['CRASH']} CRASH, {tally['NOT RUN']} NOT RUN) over "
        f"{len(cells)} cells; {len(drifted)} off their pin.",
        "",
        f"* head: `{head}`{' (working tree dirty)' if dirty else ''}",
        f"* budget: {budget_s:g} s per cell; marks: {', '.join(marks)}",
        f"* command: `uv run python scripts/sfy_audit.py --budget {budget_s:g}"
        f"{' --strict' if strict else ''}`",
    ]
    if cpu_load is not None:
        lines.append(f"* CPU load while timing: {cpu_load:.1f} mean runnable procs")
    lines += [
        "",
        "A cell passes the gate when it is CLEAN, or REFUSED with a cause one of",
        "the plan's R-rulings allows: " + ", ".join(f"`{c}`" for c in RULED_CAUSES) + ".",
        "`--strict` also asks whether the cell still does what the corpus pins it",
        "to do, which makes the same run a regression pin.",
        "",
        "## Every cell",
        "",
        "| entry | mark | verdict | cause or checks | pinned | machines | belts | poles "
        "| wires | s |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cell in cells:
        detail = cell.cause if cell.verdict in {"REFUSED", "CRASH"} else ", ".join(cell.checks)
        mark = "" if cell.ok(strict=strict) else "**"
        pin = cell.expected or "--"
        lines.append(
            f"| {cell.url_id} | {cell.designer} | {mark}{cell.verdict}{mark} "
            f"| {detail or '--'} | {'as pinned' if cell.as_pinned else f'**{pin}**'} "
            f"| {cell.machines} | {cell.belts} | {cell.poles} | {cell.wires} "
            f"| {cell.seconds:.2f} |"
        )

    lines += ["", "## what refuses and why", ""]
    refusals = [cell for cell in cells if cell.verdict == "REFUSED"]
    if not refusals:
        lines.append("Nothing refused.")
    for cause, group in _by_cause(refusals):
        lines += [
            f"### `{cause}` -- {len(group)} cells"
            + ("" if is_ruled_cause(cause) else "  **no ruling allows this cause**"),
            "",
        ]
        lines += [
            f"* `{cell.url_id}` in {cell.designer}: {cell.detail or 'no further detail'}"
            for cell in group
        ]
        lines.append("")

    missed = [cell for cell in cells if not cell.gate_ok]
    if missed:
        lines += ["## What fails the gate", ""]
        lines += [
            f"* `{cell.url_id}` in {cell.designer}: {cell.summary}"
            + (f" -- {cell.detail}" if cell.detail else "")
            for cell in missed
        ]
        lines.append("")

    lines += ["## What moved off its pin", ""]
    if not drifted:
        lines += ["Nothing: every cell did what the corpus says it does.", ""]
    else:
        lines += [
            f"* `{cell.url_id}` in {cell.designer}: pinned "
            f"{'CLEAN' if cell.expected == CLEAN else f'REFUSED({cell.expected})'}, "
            f"got {cell.summary}"
            for cell in drifted
        ]
        lines += [
            "",
            "A pin moving is not by itself a defect -- a cell that starts BUILDING "
            "where it used to refuse is progress -- but it is always a thing to "
            "look at, and under `--strict` it fails the gate.",
            "",
        ]
    return "\n".join(lines) + "\n"


# --- internals --------------------------------------------------------------


def _cell(
    entry: SfyCorpusEntry,
    designer: str,
    verdict: str,
    started: float,
    *,
    cause: str = "",
    detail: str = "",
    checks: tuple[str, ...] = (),
    placement: SfyPlacement | None = None,
) -> Cell:
    return Cell(
        url_id=entry.url_id,
        designer=designer,
        verdict=verdict,
        cause=cause,
        detail=detail,
        checks=checks,
        machines=len(placement.machines) if placement else 0,
        belts=len(placement.belts) if placement else 0,
        poles=len(placement.poles) if placement else 0,
        wires=len(placement.wires) if placement else 0,
        seconds=perf_counter() - started,
        expected=_expected(entry, designer),
    )


def _expected(entry: SfyCorpusEntry, designer: str) -> str:
    """What THIS mark is pinned to do, or empty when the entry does not say."""
    try:
        return entry.expectation(designer)
    except KeyError:  # a mark asked for on the command line the entry does not pin
        return ""


def _by_cause(cells: Iterable[Cell]) -> list[tuple[str, list[Cell]]]:
    grouped: dict[str, list[Cell]] = {}
    for cell in cells:
        grouped.setdefault(cell.cause, []).append(cell)
    return sorted(grouped.items())


def _head() -> tuple[str, bool]:
    """The commit this run measures, and whether anything is uncommitted.

    Both matter in the report and the second one more than the first: a gate
    result taken over a working tree somebody was still editing is a result
    about a tree that no longer exists.  A run from an exported tree has no
    repository to ask, which is what ``--head`` is for.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return git("rev-parse", "--short", "HEAD") or "unknown", bool(git("status", "--porcelain"))


def _cpu_load() -> float | None:
    """Mean runnable processes over five seconds, the way this repo records it."""
    try:
        out = subprocess.run(
            ["vmstat", "1", "6"], capture_output=True, text=True, check=True, timeout=30
        ).stdout
    except OSError, subprocess.SubprocessError:
        return None
    samples = [line.split()[0] for line in out.strip().splitlines()[-5:]]
    values = [float(value) for value in samples if value.isdigit()]
    return sum(values) / len(values) if values else None


def _marks(entry: SfyCorpusEntry, asked: Sequence[str]) -> tuple[str, ...]:
    return tuple(mark for mark in entry.designers if not asked or mark in asked)


def covers_matrix(cells: Sequence[Cell]) -> bool:
    """Whether these cells are a verdict on every entry in every mark it pins.

    Asked of the cells rather than of the command line on purpose, so that the
    two ways a run can come up short -- ``--only``/``--designer`` narrowing it,
    and ``--max-seconds`` cutting it off partway -- answer the same question.
    A ``NOT RUN`` cell is the absence of a verdict, so it does not cover its
    square.
    """
    ran = {(cell.url_id, cell.designer) for cell in cells if cell.verdict != "NOT RUN"}
    whole = {(entry.url_id, mark) for entry in SFY_CORPUS for mark in entry.designers}
    return ran >= whole


def report_path(
    cells: Sequence[Cell],
    *,
    requested: Path | None = None,
    marks: Sequence[str] = (),
    only: Sequence[str] = (),
    today: date | None = None,
) -> tuple[Path, bool]:
    """Where this run's report goes, and whether that is the committed evidence.

    The committed evidence is a claim about the whole matrix, so only a run that
    covered the whole matrix may write it.  Anything narrower -- one mark, one
    entry, a run the wall-clock cap stopped -- lands under ``out/``, which this
    repository does not track, named for what it actually measured.  An explicit
    ``--report`` beats both: somebody who names a path has said where they want
    it.
    """
    if requested is not None:
        return requested, False
    stamp = (today or date.today()).isoformat()
    if covers_matrix(cells):
        return EVIDENCE_DIR / f"sfy-m2-audit-{stamp}.md", True
    mark_part = "+".join(marks) if marks else "all-marks"
    only_part = "+".join(only) if only else "all-entries"
    return _ROOT / "out" / "sfy" / f"audit-{stamp}-{mark_part}-{only_part}.md", False


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--designer",
        action="append",
        default=[],
        choices=DESIGNER_MARKS,
        help="a Blueprint Designer mark to build in; repeatable, default all three",
    )
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_S, help="seconds per cell")
    ap.add_argument("--only", action="append", default=[], help="a corpus url_id; repeatable")
    ap.add_argument(
        "--max-seconds",
        type=float,
        default=0.0,
        help="cap the whole run; the cells left over are NOT RUN",
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        help="where to write the report; the default depends on whether the run was whole",
    )
    ap.add_argument(
        "--head",
        default="",
        help=(
            "name the commit this run measures, for a run from an exported tree "
            "that has no repository of its own to ask"
        ),
    )
    ap.add_argument("--no-report", action="store_true", help="do not write a report")
    ap.add_argument("--no-cpu-load", action="store_true", help="skip the five-second vmstat sample")
    ap.add_argument(
        "--strict",
        action="store_true",
        help=(
            "also fail on any cell that did not do what the corpus pins it to do, "
            "which turns the run into a regression pin"
        ),
    )
    args = ap.parse_args(argv)

    wanted = tuple(args.only) or tuple(e.url_id for e in SFY_CORPUS)
    unknown = tuple(name for name in wanted if name not in {e.url_id for e in SFY_CORPUS})
    if unknown:
        ap.error(f"no corpus entry {', '.join(unknown)}")
    entries = [e for e in SFY_CORPUS if e.url_id in set(wanted)]

    cpu_load = None if args.no_cpu_load else _cpu_load()
    cells: list[Cell] = []
    started = perf_counter()
    stopped = ""
    for entry in entries:
        for mark in _marks(entry, args.designer):
            if stopped:
                cells.append(not_run(entry, mark, stopped))
                continue
            cell = run_cell(entry, mark, args.budget)
            cells.append(cell)
            flag = "  " if cell.ok(strict=args.strict) else "<-"
            pin = "" if cell.as_pinned else f"  (pinned {cell.expected or 'nothing'})"
            print(
                f"{flag} {cell.url_id:26} {cell.designer} {cell.summary} "
                f"[{cell.seconds:5.2f}s]{pin}"
            )
            if args.max_seconds and perf_counter() - started > args.max_seconds:
                stopped = f"the {args.max_seconds:g}s wall-clock cap expired"

    head, dirty = _head()
    if args.head:
        head, dirty = args.head, False
    text = render_report(
        cells,
        head=head,
        budget_s=args.budget,
        dirty=dirty,
        cpu_load=cpu_load,
        marks=args.designer or DESIGNER_MARKS,
        strict=args.strict,
    )
    if not args.no_report:
        path, committed = report_path(
            cells, requested=args.report, marks=args.designer, only=args.only
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"\nreport: {path}")
        if args.report is None and not committed:
            print(
                "this run did not cover the whole matrix, so it left the committed "
                "evidence alone -- only a full run writes that"
            )

    code = exit_code(cells, strict=args.strict)
    tally = Counter(cell.verdict for cell in cells)
    ruled = sum(1 for cell in cells if cell.verdict == "REFUSED" and cell.gate_ok)
    drifted = sum(1 for cell in cells if not cell.as_pinned)
    print(
        f"\n=== gate{' (--strict)' if args.strict else ''} "
        f"{'PASS' if code == 0 else 'FAIL'}: {tally['CLEAN']} CLEAN, "
        f"{ruled} expected REFUSED, {len(cells) - tally['CLEAN'] - ruled} other, "
        f"{drifted} off their pin, of {len(cells)} cells"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
