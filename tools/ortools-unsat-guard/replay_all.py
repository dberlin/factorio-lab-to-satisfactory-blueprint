"""Replay captured CP-SAT models under the running interpreter's ortools.

Usage: replay_all.py <label> <path>... [--workers N] [--time-limit S] [--repeat R]

Each path is either a directory holding model.pb + parameters.pb (evidence
capture-complete/pack-NNN layout) or a capture stem <stem> with
<stem>.model.pb / <stem>.params.pb (flab_cpsat_capture layout). Prints one
line per solve and a final summary line; never prints whole models.
"""

from __future__ import annotations

import argparse
import collections
import sys
import time
from pathlib import Path

import ortools
from ortools.sat import cp_model_pb2, sat_parameters_pb2
from ortools.sat.python import cp_model


def _load(path: str) -> tuple[str, bytes, bytes]:
    p = Path(path)
    if p.is_dir():
        return p.name, (p / "model.pb").read_bytes(), (p / "parameters.pb").read_bytes()
    stem = str(p)
    return p.name, Path(stem + ".model.pb").read_bytes(), Path(stem + ".params.pb").read_bytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--time-limit", type=float, default=None)
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    print(f"[{args.label}] ortools {ortools.__version__} from {ortools.__file__}")
    statuses: collections.Counter[str] = collections.Counter()
    t_all = time.time()
    n = 0
    for path in args.paths:
        name, mbytes, pbytes = _load(path)
        m = cp_model_pb2.CpModelProto()
        m.ParseFromString(mbytes)
        prm = sat_parameters_pb2.SatParameters()
        prm.ParseFromString(pbytes)
        if args.workers is not None:
            # CP-SAT rejects a model when both fields are set; keep only num_workers.
            prm.ClearField("num_search_workers")
            prm.num_workers = args.workers
        if args.time_limit is not None:
            prm.max_time_in_seconds = args.time_limit
        for r in range(args.repeat):
            solver = cp_model.CpSolver()
            solver.parameters.parse_text_format(str(prm))
            model = cp_model.CpModel()
            model.proto.parse_text_format(str(m))
            t0 = time.time()
            status = solver.solve(model)
            dt = time.time() - t0
            sname = solver.status_name(status)
            statuses[sname] += 1
            n += 1
            print(
                f"{name} rep {r} vars {len(m.variables)} workers {prm.num_search_workers}"
                f" limit {prm.max_time_in_seconds:.2f} -> {sname} {dt:.2f}s"
            )
    wall = time.time() - t_all
    print(f"[{args.label}] SUMMARY solves {n} wall {wall:.1f}s statuses {dict(statuses)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
