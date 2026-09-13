# OR-Tools 9.15 CP-SAT UNSAT guard

A three-hunk patch for OR-Tools v9.15 that stops a shared-tree CP-SAT worker
from propagating after it has already been marked UNSAT, the recipe that
builds a Python 3.14 wheel with it, and the harness that reproduces the
abort. Evidence and the full narrative are in
`docs/superpowers/evidence/2026-09-13-native-abort/`.

## The abort

`CHECK(!sat_solver_->ModelIsUnsat())` at the top of
`LinearProgrammingConstraint::Propagate()` kills the whole process. It is
reached through `SharedTreeWorker::SyncWithLocalTrail ->
IntegerSearchHelper::BeforeTakingDecision -> SatSolver::Propagate`. The
worker's level-zero import callbacks enqueue bounds without propagating and
return true; the shared-tree sync callback then decodes a tree literal, the
integer encoder adds level-zero implication clauses, the pending propagation
conflicts, the solver is marked UNSAT, the encoder discards that status, and
the callback still returns true.

It only occurs with automatic worker allocation (`num_workers = 0`, which on
this box means 128 workers with 56 shared-tree subsolvers), never at an
explicit 8 or 16. Production builds go through `pipeline.build`, which caps
at 16 workers; the exposure is the test suite and direct library use, where
`FreeformLayout` built without `workers=` still defaults to automatic
allocation (`DEFAULT_SEARCH_WORKERS = 0` in `flab2bp.layout.base`).

## Status (2026-09-13)

- Reproduced on the stock `ortools 9.15.6755` wheel: 1 abort in 20 runs of
  the whole `tests/layout/test_freeform.py` under `pytest -n 8`, same test
  and same C stack as the original 2026-09-12 crash. The captured model
  (11 variables, 17 constraints) replays clean in isolation on both wheels,
  so it is a load race, not an input.
- Patched wheel: all 266 captured solves replay OPTIMAL with automatic
  workers; the same 30 stress iterations produced no abort; the full suite
  gates identically to the stock wheel (4,550 passed, the same 4
  pre-existing `tests/test_pipeline.py` failures on both).
- One abort against zero is not statistical proof. The traced source chain
  is the primary evidence; the hunks are inert unless a worker is already
  UNSAT.
- Not adopted in the project venv. `uv.lock` still pins the PyPI wheel.

## Files

| File | Purpose |
| --- | --- |
| `unsat-callback-guard.patch` | The patch against tag v9.15 (`git apply --check` passes). |
| `build.sh <work-dir>` | Clone v9.15, apply the patch, build the wheel. Includes the three GCC 16 toolchain adjustments. |
| `flab_cpsat_capture.py` | pytest plugin (`-p flab_cpsat_capture` with this directory on `PYTHONPATH`) that writes every CP-SAT model and parameter set before the native solve and deletes them on normal return, so an abort leaves exactly its inputs behind. `python flab_cpsat_capture.py replay <stem>` replays one. |
| `replay_all.py <label> <paths...>` | Replays captures or the evidence's `pack-NNN` directories under the running interpreter's ortools, forcing `--workers 0` if asked. |
| `stress_loop.sh <python> <root> <n> <cores> [subset\|full]` | The reproduction loop; `full` is the mode that reproduced. |

## Verifying a wheel without touching `.venv`

```
UV_PROJECT_ENVIRONMENT=/path/venv-patched uv sync --frozen
uv pip install --python /path/venv-patched/bin/python --force-reinstall --no-deps <wheel>
/path/venv-patched/bin/python -m compileall -q /path/venv-patched/lib/python3.14/site-packages
/path/venv-patched/bin/python tools/ortools-unsat-guard/replay_all.py patched <captures...> --workers 0
tools/ortools-unsat-guard/stress_loop.sh /path/venv-patched/bin/python /path/captures 20 32-63 full
```

The `compileall` step matters: agent shells here export
`PYTHONDONTWRITEBYTECODE=1`, and a venv with no cached bytecode fails the
two-second strategy-race tests spuriously.

## Adopting the wheel

The wheel is 31 MB and does not belong in git. Adoption means a durable
location for it plus a `[tool.uv.sources]` override in `pyproject.toml`,
which makes the environment depend on a locally built artifact compiled with
GCC 16 rather than the GCC 14 manylinux toolchain PyPI uses. The version
string is unchanged (`OR_TOOLS_PATCH=6755`); the library's Build ID and
hashes are not, so evidence tooling that hashes `libortools.so.9` will see a
new value.
