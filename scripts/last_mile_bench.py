"""Replay captured last-mile cluster searches outside a whole cell run.

    uv run python scripts/route_bench.py --capture universe-matrix \
        --policy output-products --stranded --budget 30 \
        --cases /tmp/cluster-cases.pkl
    uv run python scripts/last_mile_bench.py --cases /tmp/cluster-cases.pkl --check

WHY A REPLAY.  A cluster search only happens on a pack that stranded, which on
the cells that matter is one pack in five of a thirty-second run.  Capturing
the search's real environment and replaying it turns a thirty-second
nondeterministic experiment into a millisecond deterministic one, and
``--check`` proves a candidate changed the SPEED and not the ANSWER.
"""

from __future__ import annotations

import argparse
import hashlib
import pickle
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from route_records import CanvasSnapshot, ClusterCase, snapshot_grid

from flab2bp.layout import last_mile  # noqa: E402
from flab2bp.layout.routing_domain import _geometric_search, _Grid, _PathSearchResult  # noqa: E402


def _environment(
    case: last_mile.ClusterCapture, budget: dict[str, int]
) -> last_mile.ClusterEnvironment:
    canvas = cast(CanvasSnapshot, case.canvas).restore()
    grid = snapshot_grid(cast(_Grid, case.grid))
    floor = case.budget_floor
    remaining = case.deadline_remaining
    deadline = None if remaining is None else time.monotonic() + float(remaining)

    def search(index: int, constraints: frozenset[tuple[int, int, int]]) -> _PathSearchResult:
        starts, goals, routing_ports = case.ends[index]
        canvas.routing_ports = routing_ports
        allowance = min(
            last_mile.B_LOW_LEVEL_EXPANSIONS,
            max(0, budget["left"] - floor),
        )
        private = {"left": allowance}
        found = _geometric_search(
            canvas,
            list(starts),
            set(goals),
            dict(case.history),
            case.pressure,
            case.bounds,
            private,
            deadline,
            {},
            grid,
            case.owned_starts[index],
            (),
            case.rejected[index] | constraints,
            case.blocking_owners,
        )
        budget["left"] -= allowance - private["left"]
        return found

    return last_mile.ClusterEnvironment(
        search=search,
        offers=lambda _index: ({}, {}, {}),
        budget_left=lambda: budget["left"],
        budget_floor=floor,
        expired=lambda: False,
    )


def digest(results: list[last_mile.ClusterResult]) -> str:
    hasher = hashlib.sha256()
    for result in results:
        hasher.update(result.outcome.value.encode())
        for index in sorted(result.paths):
            hasher.update(repr((index, result.paths[index])).encode())
    return hasher.hexdigest()[:16]


def _replayable(result: last_mile.ClusterResult) -> bool:
    """Whether the captured run's bound can be reproduced without a clock."""
    return result.bound is not last_mile.ClusterBound.WALL


def bench(path: Path, rounds: int, check: bool) -> int:
    cases: list[ClusterCase] = pickle.loads(path.read_bytes())
    if not cases:
        print("no cluster cases in this capture")
        return 1
    replayable = [case for case in cases if _replayable(case.result)]
    skipped = len(cases) - len(replayable)
    if not replayable:
        print(f"every one of {len(cases)} captured searches was wall-bounded")
        return 1
    best: tuple[float, list[last_mile.ClusterResult]] | None = None
    matched = True
    for r in range(rounds):
        got: list[last_mile.ClusterResult] = []
        t0 = time.perf_counter()
        for case in replayable:
            budget = {"left": case.capture.budget_left}
            got.append(
                last_mile.solve_cluster(case.capture.problem, _environment(case.capture, budget))
            )
        dt = time.perf_counter() - t0
        matched &= all(
            replace(result, seconds=0.0) == replace(case.result, seconds=0.0)
            for result, case in zip(got, replayable, strict=True)
        )
        if best is None or dt < best[0]:
            best = (dt, got)
        nodes = sum(result.nodes for result in got)
        print(f"  round {r + 1}: {dt:.3f}s  {nodes} nodes")
    assert best is not None
    dt, got = best
    counts = {outcome.value: 0 for outcome in last_mile.ClusterOutcome}
    for result in got:
        counts[result.outcome.value] += 1
    sizes: list[int] = []
    truncated = 0
    runs = {1: 0, 2: 0}
    for case in replayable:
        sizes.append(len(case.capture.problem.nets))
        truncated += bool(case.capture.problem.truncated)
        if case.capture.run in runs:
            runs[case.capture.run] += 1
    print(
        f"BEST {dt:.3f}s  {len(replayable)} clusters  "
        f"(run1={runs[1]} run2={runs[2]}, skipped {skipped} wall-bounded)  "
        f"sizes {min(sizes)}-{max(sizes)}  truncated {truncated}  "
        + "  ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        + f"  digest {digest(got)}"
    )
    if check:
        want = digest([case.result for case in replayable])
        same = digest(got)
        print(
            f"captured digest {want}   replay digest {same}   "
            f"{'MATCH' if want == same and matched else 'DIFFER'} "
            "(all rounds: outcome, paths, nodes, work, bound; seconds measured separately)"
        )
        # Say what a MATCH is worth.  `offers` is a stub here and the commit
        # path (`_stake` + `commit_once`) is outside the capture entirely, so
        # this proves the SEARCH is unchanged and says nothing about whether
        # the pass would stake and link the result.
        print(
            "  (scope: the CBS search only -- `offers` is a stub and the commit "
            "path is not captured)"
        )
        return 0 if want == same and matched else 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    return bench(args.cases, args.rounds, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
