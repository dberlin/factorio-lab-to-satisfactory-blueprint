"""Command line entry point, for both games the URL can name.

    flab2bp 'https://factoriolab.github.io/dsp/flow?o=super-magnetic-ring*60&...'
    flab2bp 'https://factoriolab.github.io/sfy/list?o=iron-plate*60&v=11' \\
        --flow plates.csv --designer mk3 -o blueprints/

For a Dyson Sphere Program URL the blueprint is a string: it goes to stdout, so
it pipes and redirects cleanly, and all diagnostics go to stderr.  A
Satisfactory blueprint is two binary files that the game reads out of a folder,
so ``-o`` is a DIRECTORY there, the report takes stdout instead, and the two
paths written are named on stderr.  The exit codes are the same either way.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from flab2bp.build_choices import (
    DEFAULT_CANDIDATE_POLICIES,
    POWER_TOWER_CHOICES,
    CandidatePolicy,
    MachineRank,
)
from flab2bp.lab.games import Game
from flab2bp.lab.url import parse_url
from flab2bp.layout.band_names import BAND_SELECTIONS, canonical_selection
from flab2bp.layout.base import NoValidLayout, SpecInfeasible
from flab2bp.layout.observe import (
    TRACE_SAMPLE_INTERVAL_S,
    SampledObserver,
    SearchEvent,
)
from flab2bp.sfy import pipeline as sfy_pipeline
from flab2bp.strategy_names import STRATEGY_CHOICES

if TYPE_CHECKING:
    from flab2bp import pipeline

#: Every module named below loads the Dyson Sphere Program layers behind it --
#: `flab2bp.pipeline` alone brings in eighteen of them -- so none of them is
#: imported until a DSP build is actually happening.  A Satisfactory build runs
#: `flab2bp.sfy.pipeline`, which loads no DSP module at all, and
#: `tests/sfy/test_rates.py` holds this file to that in a fresh interpreter.
#: The vocabularies argparse needs at parser-construction time come from the
#: leaf modules above instead, which the heavy ones import and re-export.

#: The Blueprint Designer a Satisfactory build is laid out in when ``--designer``
#: is not given -- the pipeline's own default, not a second copy of it.  The
#: flag itself defaults to ``None`` rather than to this, so the DSP path can
#: tell "the user asked for a designer" -- which is meaningless there and is
#: refused -- from "the user said nothing".
DEFAULT_DESIGNER_MARK = sfy_pipeline.DEFAULT_DESIGNER_MARK

#: Flags that only mean something for a Dyson Sphere Program build, by the
#: attribute argparse stores each one under.  A Satisfactory URL carrying one
#: gets a single line on stderr naming them, rather than eleven, and rather than
#: silence -- a band or a strategy that was asked for and ignored is exactly the
#: kind of thing a player would otherwise believe had taken effect.
_DSP_ONLY_FLAGS: dict[str, str] = {
    "strategy": "--strategy",
    "band": "--band",
    "sequence_islands": "--sequence-islands",
    "candidate_policy": "--candidate-policy",
    "machine_rank": "--machine-rank",
    "no_proliferator": "--no-proliferator",
    "power_tower": "--power-tower",
    "workers": "--workers",
    "race": "--race",
    "share": "--no-share",
    "trace_jsonl": "--trace-jsonl",
}


class _CliTraceWriter:
    """Buffers frozen ``SearchEvent``s off the search thread; a daemon thread
    of its own turns them into JSONL.

    Mirrors ``TraceCollector`` (web/trace.py) for exactly the reason that
    class exists: ``frame_json``'s own docstring says it "runs on the
    parent's trace thread, never on a search thread" -- before this class,
    the CLI's sink called it, plus ``json.dumps`` and the file write,
    SYNCHRONOUSLY on the thread the search was trying to spend its core on
    (fix round, Important 1). ``offer`` is the O(1) sink ``SampledObserver``
    calls; projection and I/O happen only in ``_drain_once``, off that
    thread.

    Unlike ``TraceCollector``'s stage-1 deque, ``_pending`` is unbounded: this
    is one CLI process tracing one build, not a long-lived server bounding
    memory across many jobs, and "no frames are lost" is exactly the
    guarantee a debugging JSONL file promises.
    """

    def __init__(self, trace_file: TextIO, started_at: float) -> None:
        self._trace_file = trace_file
        self._started_at = started_at
        self._pending: deque[SearchEvent] = deque()
        self._lock = threading.Lock()
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def offer(self, event: SearchEvent) -> None:
        """The sink. One deque append -- O(1), never touches ``placement``."""
        with self._lock:
            self._pending.append(event)

    def start(self) -> None:
        thread = threading.Thread(target=self._run, name="flab2bp-cli-trace", daemon=True)
        # `start()` BEFORE the assignment -- the same ordering
        # `TraceCollector.start()` was fixed to use (Critical 1): a failed
        # `thread.start()` must leave `self._thread` `None` rather than
        # referencing a Thread that was constructed but never actually
        # started, so `stop()` below drains directly instead of joining one.
        thread.start()
        self._thread = thread

    def _drain_once(self) -> None:
        # Swap the deque object out under the lock rather than copying it
        # (Minor, re-review round): `list(self._pending)` was an O(n) copy
        # held under the same lock `offer()` needs for its O(1) append, so a
        # long backlog could make the search thread's "O(1)" sink block
        # behind it. Reassignment is O(1); the old deque is drained below,
        # outside the lock, and nothing else holds a reference to it.
        with self._lock:
            pending, self._pending = self._pending, deque()
        from flab2bp.web.trace import frame_json

        for event in pending:
            frame = frame_json(self._seq, round(event.monotonic_s - self._started_at, 3), event)
            self._seq += 1
            try:
                self._trace_file.write(json.dumps(frame))
                self._trace_file.write("\n")
            except OSError:
                # R3: a debugging artefact must never take a real build down
                # with it. Re-review round: this used to propagate straight
                # out of `_run`/`stop()` -- on the writer thread that is a
                # silent thread death (tolerable), but `stop()`'s own final
                # drain runs on the MAIN thread once the writer thread is
                # gone, so an `OSError` here (disk full, a broken pipe, an
                # NFS hiccup) used to reach `cli_main`'s `finally` and kill a
                # build that had already succeeded -- precisely what R3
                # forbids, and the same "raise inside a `finally` skips the
                # resource release" shape as Critical 1, reintroduced here.
                # Whatever wrote before this point is on disk; a trace file
                # with a silently truncated tail is the correct trade, same
                # as the tolerance `trace_file.close()` already gets in
                # `main()`.
                return

    def _run(self) -> None:
        from flab2bp.web.trace import TRACE_DRAIN_INTERVAL_S

        while not self._stop.is_set():
            self._drain_once()
            self._stop.wait(TRACE_DRAIN_INTERVAL_S)
        # One last pass after the stop flag is observed: a burst offered
        # between the previous periodic drain and `stop()` being called must
        # still reach the file, not be lost (mirrors `TraceCollector._run`).
        self._drain_once()

    def stop(self) -> None:
        """Join the writer thread and do one final drain, so every frame
        offered before this call is on disk before the caller closes the
        file."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        # Only drain here if the daemon thread is gone -- the same race
        # `TraceCollector.stop()` guards against: a wedged thread could still
        # be mid-drain, and draining again concurrently from this thread
        # would race on `self._seq`.
        if thread is None or not thread.is_alive():
            self._drain_once()


def _report(build: pipeline.Build, *, verbose: bool = False, out: TextIO | None = None) -> None:
    """Everything except the blueprint itself goes to stderr by default.

    ``out`` defaults to ``None`` rather than ``sys.stderr`` directly: a default
    argument binds once, at function-definition time, and ``capsys`` swaps in
    a fresh ``sys.stderr`` object per test -- binding the real stream eagerly
    would keep pointing at whatever object existed at import time and silently
    stop being captured.
    """
    from flab2bp.layout import markers

    if out is None:
        out = sys.stderr
    frame = build.placement.frame
    if frame is None:
        raise ValueError("successful build placement has no area frame")
    print(
        f"{build.strategy} / {build.spec.label}: {build.spec.machine_count} machines, "
        f"{build.placement.area} tiles, {len(build.placement.buildings)} buildings",
        file=out,
    )
    print(f"primary_band: {frame.primary_band}", file=out)
    print(
        f"certified_bands: {', '.join(map(str, frame.certified_bands))}",
        file=out,
    )

    unmarked = markers.unmarked_external_inputs(build.placement, build.spec)
    marked = int(build.placement.stats.get("input_markers", 0))
    print(
        f"inputs to belt in: {', '.join(sorted(build.spec.external_inputs)) or 'none'}"
        f"  ({marked} marked with icons)",
        file=out,
    )
    if unmarked:
        # Say it rather than let someone discover it while staring at an
        # unlabelled belt in game.
        print(f"  WARNING: no icon placed for {sorted(unmarked)}", file=out)

    # A self-loop recipe's block is dead on paste until a player hand-fills its
    # loop lane once (design §9 R3, "prime once and warn" -- never a permanent
    # external input, since the loop is steady-state correct on its own).
    prime_heads = markers.self_loop_prime_heads(build.placement, build.spec)
    for seed in build.spec.self_loop_seeds:
        head_index = prime_heads.get(seed.item_id)
        if head_index is not None:
            tile = build.placement.buildings[head_index]
            where = f" at ({tile.x},{tile.y})"
        else:
            # Honest rather than a crash: the loop's own placement should
            # always be locatable, but a report is a bad place to raise.
            where = ""
        print(
            f"prime once (self-loop): {seed.item_id} {seed.seed_items} items"
            f" onto the marked belt{where}"
            f" -- {seed.recipe_id} consumes what it produces, so the block will"
            f" not start until the loop has items in it",
            file=out,
        )

    # Say whether the selection was pinned. "No findings" and "nothing was
    # checked" read identically in silence, and only one of them is reassuring.
    if build.flow_pinned:
        if build.flow_findings:
            print(
                f"  {len(build.flow_findings)} difference(s) from the pinned flow:",
                file=out,
            )
            for finding in build.flow_findings:
                print(f"    {finding}", file=out)
        else:
            print("  recipe selection pinned to the supplied flow (no differences)", file=out)
    else:
        print(
            "  recipe selection DERIVED, not pinned -- pass --flow or --fetch-flow "
            "to build FactorioLab's own selection",
            file=out,
        )

    rules = build.belt_rules
    if rules is not None:
        if rules.from_url:
            print(
                f"  belt altitude ceiling {float(rules.max_z)} (lab level "
                f"{rules.lab_level}), vertical belt construction "
                f"{'YES' if rules.vertical_construction else 'no'} -- read from "
                f"the URL's researched technologies",
                file=out,
            )
        else:
            print(
                f"  WARNING: this URL carried no technology set, so a "
                f"FULLY-RESEARCHED save is ASSUMED: belt ceiling "
                f"{float(rules.max_z)} (lab level {rules.lab_level}), vertical "
                f"belt construction "
                f"{'YES' if rules.vertical_construction else 'no'}. A URL "
                f"exported from FactorioLab normally does carry one; if yours "
                f"did, the belts here may climb higher than your save allows.",
                file=out,
            )

    tiers = build.spec.belt_tiers
    floor = tiers[0]
    # Always report the effective cargo stack.  Only a stacked URL needs the
    # source-field suffix; stack one is the ordinary, explicit default.
    stack_note = f"; stack {build.spec.belt_stack}"
    if build.spec.belt_stack > 1:
        stack_note += f" (URL ist={build.spec.belt_stack})"
    piler_count = int(build.placement.stats.get("pilers", 0))
    piler_note = f"; {piler_count} piler(s)"
    if len(tiers) == 1:
        print(
            f"  belts: {floor.item_id} ({float(floor.items_per_second)}/s); it is "
            f"the fastest belt this save can build, so a lane over that rate is "
            f"refused{stack_note}{piler_note}",
            file=out,
        )
    else:
        ceiling = tiers[-1]
        raised = int(build.placement.stats.get("belt_runs_upgraded", 0))
        used = ", ".join(build.placement.stats.get("belt_upgrade_tiers", [])) or "none"
        if raised == 0:
            upgrade_note = "no run needed more than the floor"
        else:
            upgrade_note = f"{raised} run(s) raised to {used}"
        print(
            f"  belts: {floor.item_id} ({float(floor.items_per_second)}/s) floor, "
            f"{ceiling.item_id} ({float(ceiling.items_per_second)}/s) ceiling; "
            f"{upgrade_note}{stack_note}{piler_note}",
            file=out,
        )

    for lane_finding in build.report.by_check("flow.external_entry_points"):
        print(
            f"  entry lanes: {lane_finding.detail['item']} "
            f"{lane_finding.detail['entry_lanes']} "
            f"(needs {lane_finding.detail['lanes_needed']} at "
            f"{lane_finding.detail['capacity']}/s)",
            file=out,
        )

    if build.refused:
        # A strategy that produced NO layout is invisible in `attempts`, so say
        # so. Silence here would read as "that combination was simply not the
        # best", which is a different and much more reassuring claim.
        print(f"  {len(build.refused)} strategy/candidate pair(s) produced no layout:", file=out)
        for r in build.refused[:5]:
            print(f"    {r}", file=out)
            for failure in r.projection_failures[:5]:
                print(
                    f"      band {failure.band} {failure.check} buildings "
                    f"{failure.buildings}: {failure.detail}",
                    file=out,
                )

    if build.report.skipped:
        print(
            f"  {len(build.report.skipped)} check(s) could not run: "
            f"{', '.join(sorted(build.report.skipped))}",
            file=out,
        )
    if build.report.errors:
        counts = Counter(f.check for f in build.report.errors)
        print(f"  {len(build.report.errors)} VALIDATION ERRORS: {dict(counts)}", file=out)
        for f in build.report.errors[:5]:
            print(f"    {f.check}: {f.message}", file=out)
        print(
            "  This blueprint will paste but may not run correctly.",
            file=out,
        )

    if verbose:
        print(f"\n{'candidate':<20}{'strategy':<10}{'area':>8}{'errors':>8}", file=out)
        for a in sorted(build.attempts, key=lambda a: (not a.ok, a.area)):
            print(
                f"{a.candidate:<20}{a.strategy:<10}{a.area:>8}{len(a.report.errors):>8}",
                file=out,
            )


def _sfy_report(build: sfy_pipeline.SfyBuild, *, out: TextIO | None = None) -> None:
    """What a Satisfactory build is, in the shape :func:`_report` says a build in.

    To stdout, not stderr: a Satisfactory blueprint is two binary files that go
    to a directory, so nothing else is competing for the stream and the report
    is what the caller actually asked to see.  ``out`` is late-bound for the
    same reason it is in :func:`_report` -- ``capsys`` swaps the stream per test.
    """
    if out is None:
        out = sys.stdout
    placement, spec, report = build.placement, build.spec, build.report
    entries = ", ".join(
        f"{run.item_id} at x={run.start[0]:.0f}"
        for run in sorted(placement.belts, key=lambda r: (r.item_id, r.start[0]))
        if run.boundary_start
    )
    exits = ", ".join(
        f"{run.item_id} at x={run.end[0]:.0f}"
        for run in sorted(placement.belts, key=lambda r: (r.item_id, r.end[0]))
        if run.boundary_end
    )
    print(
        f"{build.strategy}: {len(placement.machines)} machines in "
        f"{len(spec.groups)} rows, {len(placement.belts)} belts, "
        f"{len(placement.poles)} poles, {len(placement.wires)} wires; "
        f"power {spec.power_mw:.1f} MW (report figure); "
        f"shards {spec.power_shards}; entries [{entries}]; exits [{exits}]",
        file=out,
    )
    # Same line `_report` prints, and for the same reason: "no findings" and
    # "nothing was checked" read identically in silence.  On this path the pin
    # is a condition of building at all (R1), so it is always the first arm.
    if build.flow_pinned:
        print("  recipe selection pinned to the supplied flow", file=out)
    else:
        print("  recipe selection NOT pinned to a flow", file=out)

    if build.refused:
        print(f"  {len(build.refused)} refusal(s) with no layout to show:", file=out)
        for failure in build.refused[:5]:
            print(f"    {failure}", file=out)

    if report.skipped:
        print(
            f"  {len(report.skipped)} check(s) could not run: {', '.join(sorted(report.skipped))}",
            file=out,
        )
    if report.errors:
        counts = Counter(finding.check for finding in report.errors)
        print(f"  {len(report.errors)} VALIDATION ERRORS: {dict(counts)}", file=out)
        for finding in report.errors[:5]:
            print(f"    {finding.check}: {finding.message}", file=out)
        print("  This blueprint will paste but may not run correctly.", file=out)


def _band_selection(value: str) -> str:
    """``--band``'s own canonicalization, the same one ``BandPolicy`` applies.

    Deliberately `canonical_selection` rather than `BandPolicy.parse`: argparse
    runs this on the DEFAULT as well as on a given value, so calling into
    `band_policy` here would load DSP's planet geometry on every Satisfactory
    build. `BandPolicy.__post_init__` calls the same function and raises the
    same message, and the DSP arm still builds a real `BandPolicy` from it.
    """
    canonical = canonical_selection(value)
    if canonical is None:
        raise argparse.ArgumentTypeError(f"unknown latitude band {value!r}")
    return canonical


def add_candidate_policy_argument(ap: argparse.ArgumentParser) -> None:
    """Add the shared named-policy selection surface to a CLI parser."""
    labels = ", ".join(policy.value for policy in DEFAULT_CANDIDATE_POLICIES)
    ap.add_argument(
        "--candidate-policy",
        action="append",
        default=None,
        metavar="POLICY[,POLICY...]",
        help=f"candidate policies to run; repeat or comma-delimit from: {labels} "
        "(default: all three)",
    )


def candidate_policies_from_args(
    ap: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[CandidatePolicy, ...]:
    """Validate repeated/comma-delimited labels and restore canonical order."""
    selections: list[str] | None = args.candidate_policy
    if selections is None:
        return DEFAULT_CANDIDATE_POLICIES

    selected: set[CandidatePolicy] = set()
    for selection in selections:
        for raw_label in selection.split(","):
            label = raw_label.strip()
            if not label:
                ap.error("candidate policy must not be empty")
            try:
                policy = CandidatePolicy(label)
            except ValueError:
                ap.error(f"unknown candidate policy: {label!r}")
            if policy in selected:
                ap.error(f"duplicate candidate policy: {policy.value}")
            selected.add(policy)
    return tuple(policy for policy in DEFAULT_CANDIDATE_POLICIES if policy in selected)


def build_parser() -> argparse.ArgumentParser:
    """Every flag this CLI accepts, in one place a test can parse without main.

    Extracted from :func:`main` so the argument surface can be asserted on
    directly: a default that silently changes -- ``--race`` becoming opt-OUT
    before the flip commit intends it, say -- is a behaviour change no
    end-to-end test of ``main`` distinguishes from the pipeline's own default.
    """
    ap = argparse.ArgumentParser(
        prog="flab2bp",
        description="Turn a FactorioLab URL into a pasteable blueprint: a "
        "Dyson Sphere Program string on stdout for a /dsp/ URL, or a "
        "Satisfactory .sbp and .sbpcfg pair in a directory for a /sfy/ one.",
    )
    ap.add_argument("url", help="a factoriolab.github.io/dsp/... or /sfy/... URL")
    ap.add_argument(
        "--strategy",
        choices=STRATEGY_CHOICES,
        default="best",
        help="layout backend; best runs freeform, sequence-pair, transport-routing "
        "and hierarchical and keeps the smallest valid result (default). "
        "transport-routing constructs interfaces with bounded native SAT routing. "
        "hierarchical decomposes the spec into blocks, solves them apart and "
        "composes them; it automatically competes in best. Racing shares one "
        "deadline across all four strategies; explicit hierarchical may overshoot "
        "--budget while settling, routing cut lanes and certifying",
    )
    ap.add_argument(
        "--band",
        type=_band_selection,
        choices=BAND_SELECTIONS,
        default="portable",
        help="latitude-band policy (default: portable, the smallest fitting "
        "band plus up to two wider bands)",
    )
    ap.add_argument(
        "--sequence-islands",
        type=int,
        metavar="N",
        help="whole-solve process islands for sequence-pair or best (range 1..16). "
        "By default, use up to four islands, capped by CPU affinity and the "
        "sequence-pair worker share: three with a single 16-worker best portfolio, "
        "or 2/1/1 islands in its default three-candidate batch",
    )
    add_candidate_policy_argument(ap)
    ap.add_argument(
        "--machine-rank",
        choices=[rank.value for rank in MachineRank],
        default=MachineRank.EXACT.value,
        help=(
            "how to read the URL's machine rank: 'exact' always uses the "
            "ranked producer (FactorioLab's behaviour, the default); 'up-to' "
            "treats it as a speed ceiling and uses the slowest unlocked "
            "producer that needs the same number of machines"
        ),
    )
    ap.add_argument(
        "--flow",
        type=Path,
        help="FactorioLab CSV export (the list view's 'download as CSV'). Pins "
        "the recipe selection to the one FactorioLab solved instead of "
        "re-deriving it. Refuses on a file that cannot be this URL's flow; "
        "there is no fallback to re-deriving.",
    )
    ap.add_argument(
        "--fetch-flow",
        action="store_true",
        help="fetch the CSV export ourselves by driving a headless browser to the "
        "URL. FactorioLab solves in the page, so this runs it and waits for the "
        "solve to finish. Off by default: a build should not silently need a "
        "browser or the network.",
    )
    ap.add_argument(
        "--fetch-timeout",
        type=float,
        default=90.0,
        metavar="SECONDS",
        help="how long to wait for FactorioLab to finish solving (default 90)",
    )
    ap.add_argument(
        "--browser",
        help="path to the Chromium or Chrome executable --fetch-flow should drive "
        "(default: search the usual locations, or $FLAB2BP_BROWSER)",
    )
    ap.add_argument(
        "--no-proliferator",
        action="store_true",
        help="build only from candidates that spray nothing, so no Spray Coaters "
        "are emitted. Refuses rather than falling back if every candidate this "
        "URL produces is proliferated.",
    )
    ap.add_argument(
        "--power-tower",
        choices=tuple(POWER_TOWER_CHOICES),
        default=None,
        help="power building (default: first power tower in the URL's machine "
        "rank, otherwise Tesla Tower)",
    )
    ap.add_argument("--budget", type=float, default=15.0, help="solver seconds per layout")
    ap.add_argument(
        "--workers",
        type=int,
        default=None,
        help="aggregate solver workers (default: up to 16 available CPUs; "
        "divided across candidate races, with at least four per raced candidate). "
        "Explicit hierarchical defaults to all available CPUs",
    )
    ap.add_argument(
        "--race",
        action="store_true",
        help="run --strategy best as a concurrent race for ONE budget instead of "
        "four serial solves for one budget each; fewer than four workers falls back to serial",
    )
    ap.add_argument(
        "--no-share",
        dest="share",
        action="store_false",
        help="race without exchanging incumbents or no-goods",
    )
    ap.add_argument(
        "--designer",
        choices=sfy_pipeline.DESIGNER_MARKS,
        default=None,
        help=f"Satisfactory only: which Blueprint Designer to build inside "
        f"(default: {DEFAULT_DESIGNER_MARK}). Its floor and height are read "
        f"from the game's own designer buildable. A DSP URL has no designer, "
        f"so passing this with one is refused rather than ignored",
    )
    ap.add_argument(
        "-o",
        "--out",
        type=Path,
        help="DSP: write the blueprint string to this file instead of stdout. "
        "Satisfactory: write <name>.sbp and <name>.sbpcfg into this DIRECTORY, "
        "creating it if needed (default: the current directory)",
    )
    ap.add_argument(
        "--trace-jsonl",
        type=Path,
        metavar="PATH",
        help="write every search snapshot as one JSON object per line to PATH "
        "-- the same frames the web transport carries (design §4), the input "
        "scripts/trace_overhead.py uses, and the offline-analysis path when "
        "there is no browser. Off by default: no path, no observer, no cost.",
    )
    ap.add_argument("-n", "--name", default="", help="blueprint short description")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every attempt")
    ap.add_argument(
        "--allow-invalid",
        action="store_true",
        help="emit even when validation fails (default: exit non-zero instead, since "
        "an invalid blueprint pastes cleanly and then does not run)",
    )
    return ap


def _game_of(url: str) -> Game | None:
    """Which game this URL asks for a blueprint of, or ``None`` if it cannot say.

    ``None`` deliberately falls THROUGH to the Dyson Sphere Program arm rather
    than exiting here.  ``pipeline.build`` parses the URL itself and already
    reports an unreadable one as exit 2 with its own message; refusing here
    instead would change what a DSP user is told about their own URL, and would
    do it in the one place that cannot know which game they meant.
    """
    try:
        return parse_url(url).game
    except ValueError, KeyError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # Which game, before anything else is validated: every check below this
    # point is about a DSP flag, and `--trace-jsonl` even opens a file before
    # the build starts. The URL is the only thing that says which build this is.
    game = _game_of(args.url)
    if game is Game.SFY:
        return _sfy_main(args)
    if args.designer is not None:
        print(
            "flab2bp: --designer names a Satisfactory Blueprint Designer and this "
            "is not a Satisfactory URL; drop the flag",
            file=sys.stderr,
        )
        return 2

    # The DSP branch, and the first place this process needs the DSP layers.
    from flab2bp import pipeline

    candidate_policies = candidate_policies_from_args(ap, args)
    if args.sequence_islands is not None and args.strategy not in (
        "sequence-pair",
        "best",
    ):
        ap.error("--sequence-islands requires --strategy sequence-pair or best")
    if args.sequence_islands is not None and not 1 <= args.sequence_islands <= 16:
        ap.error("--sequence-islands must be from 1 to 16")
    if args.workers is not None and args.workers < 1:
        ap.error("--workers must be a positive integer")
    if args.trace_jsonl is not None and args.race:
        # Task 13 fix round 1: a raced arm runs in a spawned child with no
        # in-process observer to call, and this CLI has no `trace_queue` to
        # give it one -- so before this check existed, `--trace-jsonl
        # --race` silently produced a valid-looking, permanently empty
        # file. That reads as "the search produced nothing," which is a much
        # worse failure than a build refusing to start. Threading a queue and
        # a drain thread through here (mirroring `web/jobs.py`'s
        # `Builder._run`) would fix it properly; refusing the combination
        # outright is the smaller, safer fix that removes the silent-empty-
        # file failure mode today. Tracing a raced build is not unsupported
        # forever, just not wired through this flag yet.
        ap.error(
            "--trace-jsonl is not yet supported together with --race: a raced "
            "arm has no channel to report search events through, so the "
            "combination would silently write an empty trace file. Drop "
            "--race, or omit --trace-jsonl."
        )
    # Keep None unresolved: only pipeline.build knows each candidate's worker
    # share. Resolving here would turn the build-wide default into an explicit
    # request and oversubscribe the sequence-pair arms in a concurrent batch.
    sequence_islands = args.sequence_islands

    # --trace-jsonl opens its output file here, before any solve starts, so a
    # bad path (missing directory, no permission, full disk) fails fast at
    # argument time -- the same place `--sequence-islands` and `--workers`
    # already fail on a bad value -- rather than surfacing five minutes into a
    # real build. `ap.error` never returns (argparse types it `NoReturn`), so
    # `trace_file` is a real, open file for the rest of `main` whenever it is
    # not `None`.
    trace_file = None
    search_observer = None
    writer: _CliTraceWriter | None = None
    if args.trace_jsonl is not None:
        try:
            trace_file = args.trace_jsonl.open("w", encoding="utf-8")
        except OSError as exc:
            ap.error(f"--trace-jsonl {args.trace_jsonl}: {exc}")
        # `frame_json` is the ONE place, web or CLI, that projects a
        # SearchEvent into the wire shape (web/trace.py, design §4), and its
        # own docstring says it must never run on a search thread. `writer`
        # is the CLI's side of that: `.offer` (the sink below) is an O(1)
        # deque append on the search thread, and `frame_json` plus the actual
        # file write happen only on the writer's own daemon thread (fix
        # round, Important 1). `event.monotonic_s` is captured where the
        # event was constructed (observe.py's default factory), never at
        # whatever later moment the writer thread gets around to it -- the
        # same relationship `TraceCollector.drain_once` uses against its own
        # `started_at`, so a CLI trace and a web trace measure `t` the same
        # way relative to their own start.
        writer = _CliTraceWriter(trace_file, time.monotonic())
        writer.start()
        search_observer = SampledObserver(sink=writer.offer, min_interval_s=TRACE_SAMPLE_INTERVAL_S)

    try:
        try:
            build = pipeline.build(
                args.url,
                strategy=args.strategy,
                band=args.band,
                candidate_policies=candidate_policies,
                machine_rank=MachineRank(args.machine_rank),
                time_budget_s=args.budget,
                sequence_islands=sequence_islands,
                name=args.name,
                flow=args.flow,
                fetch_flow=args.fetch_flow,
                fetch_timeout_s=args.fetch_timeout,
                browser=args.browser,
                no_proliferator=args.no_proliferator,
                power_tower=args.power_tower,
                workers=args.workers,
                race=args.race,
                share=args.share,
                search_observer=search_observer,
            )
            _report(build, verbose=args.verbose)
        except NoValidLayout as exc:
            # Distinct exit code: "no layout exists" is a different outcome from
            # "the URL was bad", and per the user a spec that cannot be laid out in
            # the retry budget is our bug until shown otherwise.
            print(f"flab2bp: {exc}", file=sys.stderr)
            for failure in exc.projection_failures[:5]:
                print(
                    f"  band {failure.band} {failure.check} buildings "
                    f"{failure.buildings}: {failure.detail}",
                    file=sys.stderr,
                )
            for attempt_failure in exc.attempt_failures:
                if not attempt_failure.stats:
                    continue
                numbers = " ".join(
                    f"{key}={value:g}" if isinstance(value, int | float) else f"{key}={value}"
                    for key, value in sorted(attempt_failure.stats.items())
                )
                pair = "/".join(
                    part for part in (attempt_failure.strategy, attempt_failure.candidate) if part
                )
                print(f"  stats {pair}: {numbers}", file=sys.stderr)
            return 3
        except (ValueError, KeyError) as exc:
            print(f"flab2bp: {exc}", file=sys.stderr)
            return 2
    finally:
        # `writer.stop()` FIRST: it joins the writer's daemon thread and
        # performs its final drain, so every frame `offer`ed during the build
        # is actually on disk before the file below is closed -- the
        # buffering that makes the sink O(1) (fix round, Important 1) must
        # never cost a frame at shutdown.
        #
        # Nested in its own `finally` (re-review round): `_drain_once`'s own
        # `OSError` guard should already keep `stop()` from raising, but the
        # file release below must run even if it somehow still does -- the
        # same "raise inside a `finally` skips the resource release" shape
        # Critical 1 fixed in `jobs.py`, reintroduced here by this same wave.
        try:
            if writer is not None:
                writer.stop()
        finally:
            # Every exit path -- success, NoValidLayout, ValueError/KeyError,
            # or any other exception propagating out of `pipeline.build` --
            # closes the file, so a raised build still leaves a complete,
            # readable trace instead of one truncated by a buffered write
            # that never flushed. `close()` itself is guarded: an OS-level
            # flush failure here (disk filled during the build, permission
            # revoked, an NFS hiccup) would otherwise raise AFTER a build
            # that already succeeded, discarding a finished blueprint over a
            # debugging artefact -- exactly what R3 ("a view must never kill
            # a build") forbids. A trace file with a silently truncated tail
            # is the correct trade.
            if trace_file is not None:
                with contextlib.suppress(OSError):
                    trace_file.close()

    if build.report.errors and not args.allow_invalid:
        print(
            "flab2bp: refusing to emit an invalid blueprint; pass --allow-invalid to override",
            file=sys.stderr,
        )
        return 1

    if args.out:
        args.out.write_text(build.blueprint)
        print(f"written to {args.out}", file=sys.stderr)
    else:
        print(build.blueprint)
    return 0


def _sfy_main(args: argparse.Namespace) -> int:
    """The Satisfactory arm of :func:`main`, with :func:`main`'s exit codes.

    ``0`` a blueprint was written, ``1`` the validator found errors and
    ``--allow-invalid`` was not passed, ``2`` the URL, the spec or the flow was
    refused, ``3`` no layout -- the same four the DSP arm returns, so a script
    that already reads them needs no change for a second game.

    ``SpecInfeasible`` is caught before ``NoValidLayout`` deliberately: it is a
    subclass of it, but "this flow moves a fluid" is a bad SPEC (2), not a
    search that found no layout (3).
    """
    # Compared against the parser's OWN defaults rather than against a copy of
    # them written here: a default that moves stays in one place, and a flag
    # left alone is never reported as ignored.
    defaults = build_parser()
    ignored = sorted(
        flag
        for dest, flag in _DSP_ONLY_FLAGS.items()
        if getattr(args, dest) != defaults.get_default(dest)
    )
    if ignored:
        print(
            f"flab2bp: {', '.join(ignored)} shape a Dyson Sphere Program search and "
            f"have no meaning for a Satisfactory build; ignoring them",
            file=sys.stderr,
        )

    try:
        build = sfy_pipeline.build(
            args.url,
            designer=args.designer or DEFAULT_DESIGNER_MARK,
            time_budget_s=args.budget,
            flow=args.flow,
            fetch_flow=args.fetch_flow,
            fetch_timeout_s=args.fetch_timeout,
            browser=args.browser,
            name=args.name,
        )
    except SpecInfeasible as exc:
        print(f"flab2bp: {exc}", file=sys.stderr)
        return 2
    except NoValidLayout as exc:
        print(f"flab2bp: {exc}", file=sys.stderr)
        for reason in exc.attempt_reasons[:5]:
            print(f"  {reason}", file=sys.stderr)
        return 3
    except (ValueError, KeyError) as exc:
        print(f"flab2bp: {exc}", file=sys.stderr)
        return 2

    _sfy_report(build)

    if build.blueprint is None:
        # Laid out and judged, then unwritable: a refusal with a reason, which
        # `_sfy_report` has already printed in full. Same exit code as any other
        # "there is no blueprint at the end of this".
        print("flab2bp: this build produced no blueprint", file=sys.stderr)
        return 3

    if build.report.errors and not args.allow_invalid:
        print(
            "flab2bp: refusing to emit an invalid blueprint; pass --allow-invalid to override",
            file=sys.stderr,
        )
        return 1

    # A directory, not a file: the game reads a blueprint as a PAIR of files
    # sharing one stem out of a save's `blueprints/<session>` folder.
    out_dir = args.out if args.out is not None else Path.cwd()
    sbp, cfg = sfy_pipeline.write(build, out_dir)
    print(f"written to {sbp} and {cfg.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
