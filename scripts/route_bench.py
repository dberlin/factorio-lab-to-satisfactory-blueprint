"""Replay real geometric routing queries without regenerating their factory.

    uv run python scripts/route_bench.py --capture universe-matrix
    uv run python scripts/route_bench.py --cases /tmp/route-cases-universe-matrix.pkl

WHY A REPLAY AND NOT A CELL RUN

A whole-cell A/B measures the router through CP-SAT, which is multi-worker and
nondeterministic, so two runs route DIFFERENT packs and the seconds are not
comparable. Capturing real search arguments lets replay compare the same
admitted geometry, endpoints and work policy without regenerating the pack.
Results remain directly comparable, including cell paths and charged work;
elapsed deadlines are retained as limits, not claimed to be deterministic.

THE CORRECTNESS CHECK IS THE POINT. ``--check`` compares every returned path,
failure kind, blame wall and charged work count, plus the caller-visible budget
and blame mutations, in EVERY round. Endpoint iteration order is part of that
work contract: rebuilding a set during serialization can change native work
even when the path cells do not change.

OWNED REPLAY INPUTS

Canvas containers and grid buffers are detached before the live call, using
the existing canvas clone boundary. Fixed projection geometry is retained as
typed constructor input, not as a live closure-bearing preparation object.
Endpoint order, original shared allowance and remaining deadline are captured.
Replay re-anchors that deadline, never widens the work allowance. Wall-bound
queries can still differ on a different machine; they are reported as DIFFER,
never silently excluded or replaced by the best round. Zero queries are not
parity evidence. These diagnostics do not prove a complete factory validates.
"""

from __future__ import annotations

import argparse
import hashlib
import pickle
import statistics
import sys
import time
from collections import Counter, deque
from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from route_records import CanvasSnapshot, ClusterCase, RouteCase, RouteQuerySnapshot, snapshot_grid

from flab2bp.bench.corpus import entry as corpus_entry  # noqa: E402
from flab2bp.lab.data import load_vendored  # noqa: E402
from flab2bp.lab.techs import belt_rules_for_url  # noqa: E402
from flab2bp.lab.url import parse_url  # noqa: E402
from flab2bp.layout import freeform, last_mile, routing_domain  # noqa: E402
from flab2bp.layout.band_policy import BandPolicy  # noqa: E402
from flab2bp.layout.base import NoValidLayout  # noqa: E402
from flab2bp.rates import CandidatePolicy, build_candidates  # noqa: E402


def _snapshot(
    canvas: routing_domain._Canvas,
    grid: routing_domain._Grid | None,
    history: dict[tuple[int, int, int], float],
) -> tuple[
    CanvasSnapshot,
    routing_domain._Grid | None,
    dict[tuple[int, int, int], float],
]:
    """Own the exact inputs, retaining physical projection data, not callbacks."""
    return CanvasSnapshot.capture(canvas), snapshot_grid(grid), dict(history)


def capture(
    url_id: str,
    budget: float,
    every: int,
    cap: int,
    out: Path,
    policy: CandidatePolicy = CandidatePolicy.NO_PROLIFERATOR,
) -> None:
    entry = corpus_entry(url_id)
    spec = build_candidates(
        load_vendored(),
        parse_url(entry.url),
        candidate_policies=(policy,),
    ).candidates[0]

    orig = routing_domain._astar
    cases: list[RouteCase] = []
    seen = 0

    def spy(
        canvas: routing_domain._Canvas,
        starts: list[tuple[int, int, int]],
        goals: set[tuple[int, int, int]],
        history: dict[tuple[int, int, int], float],
        pressure: float,
        bounds: tuple[int, int, int, int],
        budget: dict[str, int] | None = None,
        deadline: float | None = None,
        blame: dict[tuple[int, int, int], float] | None = None,
        grid: routing_domain._Grid | None = None,
        owned_starts: Collection[tuple[int, int, int]] = (),
        released_starts: Collection[tuple[int, int, int]] = (),
        forbidden: Collection[tuple[int, int, int]] = (),
        blocking_owners: Mapping[tuple[int, int, int], int] | None = None,
        *,
        extra_edges: dict[int, tuple[tuple[int, float], ...]] | None = None,
    ) -> routing_domain._PathSearchResult:
        nonlocal seen
        want = seen % every == 0 and len(cases) < cap
        if want:
            shot_canvas, shot_grid, shot_hist = _snapshot(canvas, grid, history)
            query = RouteQuerySnapshot(
                seen,
                shot_canvas,
                shot_grid,
                shot_hist,
                tuple(starts),
                tuple(goals),
                pressure,
                bounds,
                tuple(owned_starts),
                tuple(released_starts),
                tuple(forbidden),
                None if blocking_owners is None else dict(blocking_owners),
                None if extra_edges is None else dict(extra_edges),
                None if budget is None else budget["left"],
                None if deadline is None else max(0.0, deadline - time.monotonic()),
                None if blame is None else dict(blame),
            )
        seen += 1
        out_path = orig(
            canvas,
            starts,
            goals,
            history,
            pressure,
            bounds,
            budget,
            deadline,
            blame,
            grid,
            owned_starts,
            released_starts,
            forbidden,
            blocking_owners,
            extra_edges=extra_edges,
        )
        if want:
            cases.append(
                RouteCase(
                    query,
                    out_path,
                    None if budget is None else budget["left"],
                    None if blame is None else dict(blame),
                )
            )
        return out_path

    routing_domain._astar = spy
    try:
        freeform.FreeformLayout(
            band_policy=BandPolicy("portable"),
            belt_rules=belt_rules_for_url(entry.url),
            workers=1,
        ).lay_out(spec, time_budget_s=budget)
    except NoValidLayout:
        pass
    finally:
        routing_domain._astar = orig
    out.write_bytes(pickle.dumps(cases, protocol=5))
    # `_astar` returns a `_PathSearchResult`, whose `path` is the tuple of cells.
    lens = [0 if c.result.path is None else len(c.result.path) for c in cases]
    print(
        f"captured {len(cases)} of {seen} searches -> {out} "
        f"({out.stat().st_size / 1e6:.1f} MB); "
        f"{sum(1 for n in lens if n)} found, "
        f"{sum(lens):,} path cells"
    )


def capture_clusters(
    url_id: str,
    budget: float,
    cap: int,
    out: Path,
    policy: CandidatePolicy = CandidatePolicy.NO_PROLIFERATOR,
) -> None:
    """Snapshot every last-mile invocation of one real cell run.

    The hook is `last_mile.CAPTURE` rather than `_astar`, because the stranded
    state Phase B cares about only exists at the moment `_route_all` would give
    up, and that is the one call that sees it.
    """
    entry = corpus_entry(url_id)
    spec = build_candidates(
        load_vendored(),
        parse_url(entry.url),
        candidate_policies=(policy,),
    ).candidates[0]

    cases: list[ClusterCase] = []
    pending: deque[last_mile.ClusterCapture] = deque()

    def sink(shot: last_mile.ClusterCapture) -> None:
        if len(cases) + len(pending) >= cap:
            return
        canvas = cast(routing_domain._Canvas, shot.canvas)
        grid = cast(routing_domain._Grid, shot.grid)
        shot_canvas, shot_grid, shot_hist = _snapshot(canvas, grid, dict(shot.history))
        pending.append(
            replace(
                shot,
                canvas=shot_canvas,
                grid=shot_grid,
                history=shot_hist,
                ends={
                    index: (list(starts), set(goals), ports)
                    for index, (starts, goals, ports) in shot.ends.items()
                },
                owned_starts=dict(shot.owned_starts),
                rejected=dict(shot.rejected),
                blocking_owners=dict(shot.blocking_owners),
            )
        )

    orig_solve = last_mile.solve_cluster

    def spy(
        problem: last_mile.ClusterProblem,
        environment: last_mile.ClusterEnvironment,
    ) -> last_mile.ClusterResult:
        result = orig_solve(problem, environment)
        # `CAPTURE` fires before the solve, so the pending shot is this one's.
        while pending:
            cases.append(ClusterCase(pending.popleft(), result))
        return result

    last_mile.CAPTURE = sink
    last_mile.solve_cluster = spy
    try:
        freeform.FreeformLayout(
            band_policy=BandPolicy("portable"),
            belt_rules=belt_rules_for_url(entry.url),
            workers=1,
        ).lay_out(spec, time_budget_s=budget)
    except NoValidLayout:
        pass
    finally:
        last_mile.CAPTURE = None
        last_mile.solve_cluster = orig_solve
    out.write_bytes(pickle.dumps(cases, protocol=5))
    outcomes = Counter(case.result.outcome.value for case in cases)
    bounds_hit = Counter(case.result.bound.value for case in cases)
    print(
        f"captured {len(cases)} cluster searches -> {out} "
        f"({out.stat().st_size / 1e6:.1f} MB); "
        + ", ".join(f"{value}={outcomes[value]}" for value in sorted(outcomes))
        + "; bounds "
        + ", ".join(f"{value or 'none'}={bounds_hit[value]}" for value in sorted(bounds_hit))
    )


def digest(paths: Iterable[Any]) -> str:
    hasher = hashlib.sha256()
    for p in paths:
        hasher.update(b"-" if p is None else repr(p).encode())
    return hasher.hexdigest()[:16]


def bench(path: Path, rounds: int, check: bool) -> int:
    cases: list[RouteCase] = pickle.loads(path.read_bytes())
    if not cases:
        print("NO_CAPTURE: no route queries; an empty digest is not a parity proof")
        return 1
    if rounds <= 0:
        raise ValueError("rounds must be positive")
    elapsed: list[float] = []
    matched = True
    want = digest(case.result for case in cases)
    for r in range(rounds):
        got: list[routing_domain._PathSearchResult] = []
        side_effects_match = True
        # Restoration is outside timing. Each invocation receives fresh owned
        # containers, including the exact original shared-ledger allowance.
        restored = [(case.query.canvas.restore(), snapshot_grid(case.query.grid)) for case in cases]
        t0 = time.perf_counter()
        for case, (canvas, grid) in zip(cases, restored, strict=True):
            query = case.query
            budget = None if query.budget_left is None else {"left": query.budget_left}
            blame = None if query.blame is None else dict(query.blame)
            deadline = (
                None
                if query.deadline_remaining is None
                else time.monotonic() + query.deadline_remaining
            )
            result = routing_domain._astar(
                canvas,
                list(query.starts),
                query.goals,
                dict(query.history),
                query.pressure,
                query.bounds,
                budget,
                deadline,
                blame,
                grid,
                query.owned_starts,
                query.released_starts,
                query.forbidden,
                query.blocking_owners,
                extra_edges=query.extra_edges,
            )
            got.append(result)
            effects_same = (None if budget is None else budget["left"]) == case.budget_left and (
                None if blame is None else tuple(blame.items())
            ) == (None if case.blame is None else tuple(case.blame.items()))
            side_effects_match &= effects_same
            if check and (result != case.result or not effects_same):
                print(
                    f"  search {query.ordinal} DIFFER: "
                    f"captured {case.result!r}; replay {result!r}; "
                    f"ledger/blame {'MATCH' if effects_same else 'DIFFER'}"
                )
        dt = time.perf_counter() - t0
        elapsed.append(dt)
        spent = sum(result.expansions for result in got)
        same = digest(got)
        round_match = same == want and side_effects_match
        matched &= round_match
        print(
            f"  round {r + 1}: {dt:.3f}s  {spent:,} charged work units  "
            f"digest {same}  {'MATCH' if round_match else 'DIFFER'}"
        )
    print(
        f"MEDIAN {statistics.median(elapsed):.3f}s  "
        f"range {min(elapsed):.3f}-{max(elapsed):.3f}s; "
        f"captured digest {want}; all {rounds} rounds "
        f"{'MATCH' if matched else 'DIFFER'}"
    )
    return 0 if not check or matched else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture")
    ap.add_argument("--budget", type=float, default=4.0)
    ap.add_argument("--every", type=int, default=8)
    ap.add_argument("--cap", type=int, default=64)
    ap.add_argument("--cases", type=Path)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stranded", action="store_true")
    ap.add_argument(
        "--policy",
        type=CandidatePolicy,
        choices=tuple(CandidatePolicy),
        default=CandidatePolicy.NO_PROLIFERATOR,
    )
    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if args.capture:
        out = args.cases or Path(f"/tmp/route-cases-{args.capture}-{args.policy.value}.pkl")
        if args.stranded:
            capture_clusters(args.capture, args.budget, args.cap, out, args.policy)
        else:
            capture(args.capture, args.budget, args.every, args.cap, out, args.policy)
        return 0
    if not args.cases:
        ap.error("--cases or --capture required")
    return bench(args.cases, args.rounds, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
