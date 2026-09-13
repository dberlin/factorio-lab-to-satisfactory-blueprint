"""Capture every CP-SAT model proto before it enters native Solve.

Load as a pytest plugin (``-p flab_cpsat_capture`` with this directory on
PYTHONPATH) or import it from any driver script before the first solve.
Nothing in the project repository is touched: the hook monkeypatches
``ortools.sat.python.cp_model.CpSolver.solve`` (and its ``Solve`` alias) in
the running interpreter only.

Behaviour
---------
* Before each solve, ``model.proto`` and ``solver.parameters`` are written as
  deterministic binary protobufs plus a small JSON sidecar into
  ``$FLAB_CPSAT_CAPTURE_DIR/pid-<pid>/solve-<n>.*``.
* When the solve returns normally the three files are deleted, unless
  ``FLAB_CPSAT_CAPTURE_KEEP=1``.  A native abort kills the process before the
  delete runs, so exactly the aborting solve's inputs are left behind.
* ``faulthandler`` is enabled for all threads so the Python and C stacks land
  on stderr, matching the original evidence log.

Replay a leftover capture with::

    from ortools.sat import cp_model_pb2, sat_parameters_pb2
    from ortools.sat.python import cp_model
    m = cp_model_pb2.CpModelProto()
    m.ParseFromString(open(p + '.model.pb', 'rb').read())
    prm = sat_parameters_pb2.SatParameters()
    prm.ParseFromString(open(p + '.params.pb', 'rb').read())
    solver = cp_model.CpSolver()
    solver.parameters.parse_text_format(str(prm))
    model = cp_model.CpModel()
    model.proto.parse_text_format(str(m))  # native protos take text only
    solver.solve(model)

or run ``python flab_cpsat_capture.py replay <stem>`` which does exactly that.
"""

from __future__ import annotations

import contextlib
import faulthandler
import itertools
import json
import os
import sys
import threading
import time
from pathlib import Path

from google.protobuf import text_format
from ortools.sat import cp_model_pb2, sat_parameters_pb2
from ortools.sat.python import cp_model

_ROOT = Path(os.environ.get("FLAB_CPSAT_CAPTURE_DIR", "cpsat-captures"))
_KEEP = os.environ.get("FLAB_CPSAT_CAPTURE_KEEP", "0") == "1"
_counter = itertools.count(1)
_lock = threading.Lock()
_original_solve = cp_model.CpSolver.solve


def _capture_dir() -> Path:
    d = _ROOT / f"pid-{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _solve(self: cp_model.CpSolver, model: cp_model.CpModel, solution_callback=None):
    with _lock:
        n = next(_counter)
    d = _capture_dir()
    stem = d / f"solve-{n:04d}"
    model_pb = stem.with_suffix(".model.pb")
    params_pb = stem.with_suffix(".params.pb")
    meta = stem.with_suffix(".json")
    # In 9.15 ``CpModel.proto`` and ``CpSolver.parameters`` are native pybind
    # objects (cp_model_helper.CpModelProto / SatParameters) that expose only
    # text-format conversion, so round-trip through the Python protobuf
    # classes exactly as the evidence's native_probe.py did.
    proto = text_format.Parse(str(model.proto), cp_model_pb2.CpModelProto())
    params = text_format.Parse(str(self.parameters), sat_parameters_pb2.SatParameters())
    model_pb.write_bytes(proto.SerializeToString(deterministic=True))
    params_pb.write_bytes(params.SerializeToString(deterministic=True))
    meta.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "thread": threading.get_ident(),
                "sequence": n,
                "monotonic_ns_before_solve": time.monotonic_ns(),
                "num_search_workers": self.parameters.num_search_workers,
                "num_workers": self.parameters.num_workers,
                "max_time_in_seconds": self.parameters.max_time_in_seconds,
                "random_seed": self.parameters.random_seed,
                "variables": len(proto.variables),
                "constraints": len(proto.constraints),
                "has_callback": solution_callback is not None,
                "argv": sys.argv,
            },
            indent=1,
        )
    )
    try:
        status = _original_solve(self, model, solution_callback)
    except BaseException:
        # A Python-level failure is not the native abort; keep the files so
        # the caller can tell the two apart, and re-raise unchanged.
        raise
    if not _KEEP:
        for p in (model_pb, params_pb, meta):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
    return status


def install() -> None:
    if getattr(cp_model.CpSolver, "_flab_capture_installed", False):
        return
    cp_model.CpSolver.solve = _solve  # type: ignore[method-assign]
    cp_model.CpSolver.Solve = _solve  # type: ignore[method-assign]
    cp_model.CpSolver._flab_capture_installed = True  # type: ignore[attr-defined]
    faulthandler.enable(all_threads=True)
    sys.stderr.write(f"[flab_cpsat_capture] active, dir={_ROOT} pid={os.getpid()} keep={_KEEP}\n")


def replay(stem: str, time_limit_override: float | None = None) -> str:
    """Re-solve one captured model with its captured parameters; returns status name."""
    m = cp_model_pb2.CpModelProto()
    m.ParseFromString(Path(stem + ".model.pb").read_bytes())
    prm = sat_parameters_pb2.SatParameters()
    prm.ParseFromString(Path(stem + ".params.pb").read_bytes())
    if time_limit_override is not None:
        prm.max_time_in_seconds = time_limit_override
    solver = cp_model.CpSolver()
    solver.parameters.parse_text_format(text_format.MessageToString(prm))
    model = cp_model.CpModel()
    model.proto.parse_text_format(text_format.MessageToString(m))
    status = _original_solve(solver, model, None)
    return solver.status_name(status)


if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "replay":
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else None
    print(replay(sys.argv[2], limit))
else:
    install()
