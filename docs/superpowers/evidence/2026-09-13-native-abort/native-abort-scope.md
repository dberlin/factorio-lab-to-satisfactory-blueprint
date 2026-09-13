# OR-Tools 9.15 CP-SAT native abort: scope for a patched build

Date: 2026-09-13. Scratch dir: `/tmp/claude-839601109/-home-dannyb-sources-factorio-lab-to-blueprint/c950ce94-0e3e-4854-a244-ea8916bf39cd/scratchpad/ortools/` (referred to as `$S` below). Nothing under the repository, `.venv`, or `uv.lock` was modified. No dependency was installed anywhere (one `pip download` of the swig wheel landed in `$S/swigdl/`, see C).

Files produced in `$S`:

| File | What |
|---|---|
| `native-abort-scope.md` | this report |
| `unsat-callback-guard-UNVERIFIED.patch` | candidate 3-hunk patch against tag v9.15 (`git apply --check` passes) |
| `flab_cpsat_capture.py` | pytest plugin / driver that captures every CP-SAT model+parameters before native Solve and can replay them |
| `or-tools/` | shallow clone of tag v9.15 (`551ad10d94835c99e5e1e684500d3db398c0e345`, 2026-01-09) plus `FETCH_HEAD` = upstream main `d5cf2b97` (2026-09-12) |
| `captures/`, `smoke/pytest.log` | 51 captured solves from the 11 s smoke, plus the pytest log |
| `swigdl/swig-4.5.0-...whl` | downloaded (not installed) swig wheel |

Note: another agent in this session (`native-abort`) appears to share `$S`. My clone command found `or-tools/` already populated (`clone.log` records exit 128 "already exists"); the checkout is a valid v9.15 tree (`git describe --tags` = `v9.15`, working tree clean after my patch generation). The context-mode index also contains batches from that agent with overlapping labels. Coordinate before either of us mutates `or-tools/`.

## Summary

* The CHECK that fires is `CHECK(!sat_solver_->ModelIsUnsat())` at the first line of `LinearProgrammingConstraint::Propagate()`, `ortools/sat/linear_programming_constraint.cc:2141-2142` in v9.15. The evidence's binary recovery (compiled line 2142, rodata strings, cold-clone offset `0x57d5d7`) matches the tag exactly.
* The source-level mechanism is now fully traced (A). It is not a hypothesis about "some callback"; the exact drop site of the UNSAT status is `IntegerEncoder::AddImplications` / `AssociateToIntegerLiteral` (`integer.cc:378,381,398-399,403`) calling `SatSolver::AddClauseDuringSearch` at level zero, whose `AddProblemClauseInternal` (`sat_solver.cc:316-320`) runs the pending propagation left behind by the bounds/objective import callbacks, hits a conflict, sets `model_is_unsat_`, and returns false into a `void`. `SharedTreeWorker::SyncWithSharedTree` then returns true (`work_assignment.cc:1433`), `BeforeTakingDecision` calls `SatSolver::Propagate()` (guarded only by a DCHECK), and the LP CHECK fires.
* No deterministic reproduction exists (B). The original abort was one pytest-xdist worker (`gw2`, `-n8`) in `tests/layout/test_freeform.py::test_freeform_placement_stats_carry_the_operator_telemetry` on 2026-09-12. 48 matrix replays and 8 traced runs with 128 workers did not reproduce it. A capture harness now exists and was smoke-tested (11 s, 51 solves captured, replay works).
* A patched wheel is buildable on this box (C): cmake 4.4.3, ninja 1.13.2, gcc 16.2.1, Python 3.14.7 with headers, 504 GB free on /tmp, 205 TB on home, podman available. 9.15's CMake python build supports 3.14 (CI matrix builds 3.14; `setup.py.in` lists 3.14). Two blockers to clear first: SWIG is not installed (`find_package(SWIG REQUIRED)`), and GCC 16 is two major versions newer than the compiler the wheel was built with (GCC 14.2.1 Red Hat, i.e. manylinux_2_28), so dependency compile failures are plausible. Estimated build wall time on 128 cores: 15-30 min clean, 1-2 min per patch iteration.
* Recommended sequence (E): start the stress-reproduction loop and the build in parallel; verify the patched wheel by replaying captured models and rerunning the same stress loop; install into a throwaway venv; run the gate. Total: roughly half a day of wall time if GCC 16 cooperates, one day if it does not.

## A. Exact abort site and call chain (v9.15 source)

### The CHECK

`ortools/sat/linear_programming_constraint.cc:2141-2142`:

```cpp
bool LinearProgrammingConstraint::Propagate() {
  CHECK(!sat_solver_->ModelIsUnsat());
```

The installed `libortools.so.9` exports `operations_research::sat::LinearProgrammingConstraint::Propagate()` as a `T` symbol; the evidence's rodata dump shows `/project/ortools/sat/linear_programming_constraint.cc` at `0x162a3c0` and `!sat_solver_->ModelIsUnsat()` at `0x1659c0a`, with the fatal constructor receiving line 2142. Upstream main has the same CHECK (only a `linearization_level() < 10` guard and log changes differ).

### The original stack (from `root-v23-adopted-suite.txt:104-113`, innermost first)

1. `absl::log_internal::LogMessage...` → `abort`
2. `libortools.so.9+0x57d5d7` = `LinearProgrammingConstraint::Propagate() [clone .cold]`
3. `GenericLiteralWatcher::Propagate(Trail*)`
4. `SatSolver::Propagate()+0xa9`
5. `IntegerSearchHelper::BeforeTakingDecision()`
6. `SharedTreeWorker::SyncWithLocalTrail()`
7. `SharedTreeWorker::Search(std::function<void()> const&)`
8. `SolveLoadedCpModel(...)` → subsolver thread → `ThreadPool::RunWorker`

### Source of each frame in v9.15

| Frame | File:lines | Relevant code |
|---|---|---|
| `SharedTreeWorker::Search` | `work_assignment.cc:1436-1490` | registers `level_zero_callbacks_->callbacks.push_back([this]() { return SyncWithSharedTree(); });` at 1445 (so it runs **after** the import callbacks registered at model load), loop: `FinishPropagation()` → `SyncWithLocalTrail()` → `NextDecision` → `TakeDecision` |
| `SharedTreeWorker::SyncWithLocalTrail` | `work_assignment.cc:1150-1246` | `if (!sat_solver_->FinishPropagation()) return false;` (1157, this one **does** check `model_is_unsat_`), `if (AddImplications()) continue;` (1160), `if (!helper_->BeforeTakingDecision()) return false;` (1162) |
| `IntegerSearchHelper::BeforeTakingDecision` | `integer_search.cc:1364-1410` | callback loop at 1389-1395: `if (!cb()) { NotifyThatModelIsUnsat(); return false; }`; then "propagate if needed" at 1398-1406: `if (LiteralTrail().Index() > saved_bool_index || num_enqueues() > saved_integer_index || HasPendingRootLevelDeduction()) { if (!sat_solver_->Propagate()) {...} }` |
| `SatSolver::Propagate` | `sat_solver.cc:2119-2144` | `DCHECK(!ModelIsUnsat());` only (release builds skip it), then runs every `SatPropagator` including `GenericLiteralWatcher` |
| `GenericLiteralWatcher::Propagate` | `integer.cc` | dispatches to `LinearProgrammingConstraint::Propagate()` (registered with `AlwaysCallAtLevelZero`) |

### How the local solver becomes UNSAT inside a callback that returns true

Level-zero callbacks, in registration order (`cp_model_solver_helpers.cc`):

1. `RegisterVariableBoundsLevelZeroImport` (816-908): for Booleans `trail->EnqueueWithUnitReason(clause_id, lit)` (line 858), for integers `integer_trail->Enqueue(...)` (884-897). **No propagation**, `return true` at 904. Returns false only on an immediately contradictory bound.
2. `RegisterObjectiveBoundsImport` (995-1056): `integer_trail->Enqueue(GreaterOrEqual/LowerOrEqual(objective_var, external bound))` (1012-1037), **no propagation**, `return true` at 1052.
3. `SyncWithSharedTree` (registered per worker in `Search`, 1445).

`SharedTreeWorker::SyncWithSharedTree` (`work_assignment.cc:1350-1434`) decodes every tree decision and implication:

```cpp
  for (int level = 1; level <= assigned_tree_.MaxLevel(); ++level) {
    assigned_tree_decisions_.push_back(
        DecodeDecision(assigned_tree_.Decision(level)));            // 1424-1425
    ...
      const Literal lit = DecodeDecision(impl);                      // 1428
  ...
  DCHECK(CheckLratInvariants());
  return true;                                                       // 1433
```

`DecodeDecision` (1502) → `ProtoLiteral::Decode` (95-102) → for a non-Boolean proto variable `encoder->GetOrCreateAssociatedLiteral(DecodeInteger(mapping))`.

`IntegerEncoder::GetOrCreateAssociatedLiteral` (`integer.cc:274-308`): if no literal exists yet, `sat_solver_->NewBooleanVariable()` then `AssociateToIntegerLiteral(literal, canonical_lit.first)` (300).

`IntegerEncoder::AssociateToIntegerLiteral` (`integer.cc:365-433`) discards the SatSolver status on every path:

```cpp
  if (i_lit.bound <= min) {
    return (void)sat_solver_->AddUnitClause(literal);                 // 378
  }
  if (i_lit.bound > max) {
    return (void)sat_solver_->AddUnitClause(literal.Negated());       // 381
  }
  ...
      sat_solver_->AddClauseDuringSearch({literal, associated.Negated()});   // 398
      sat_solver_->AddClauseDuringSearch({literal.Negated(), associated});   // 399
  ...
  AddImplications(var_encoding, it, literal);                         // 403, void
```

`IntegerEncoder::AddImplications` (`integer.cc:194-225`, void) calls `sat_solver_->AddClauseDuringSearch(...)` twice (relative lines 25 and 29 of the function) without checking the result.

`SatSolver::AddClauseDuringSearch` (`sat_solver.cc:162-196`): at decision level 0 (which is where callbacks run) `return AddProblemClause(literals);` → `AddProblemClauseInternal` (`sat_solver.cc:282-322`):

```cpp
  if (!PropagationIsDone() && !Propagate()) {                        // 316
    ProcessCurrentConflict();
    return SetModelUnsat();          // model_is_unsat_ = true; return false
  }
```

`PropagationIsDone()` is false precisely because callbacks 1 and 2 enqueued unit literals and integer bounds without propagating. The propagation that was deferred "just once when everything was added" (comment at `integer_search.cc:1385`) therefore runs early, inside callback 3, and if it conflicts the worker is UNSAT while callback 3 still returns true. `BeforeTakingDecision` then sees the trail advanced, calls `SatSolver::Propagate()` directly (no `model_is_unsat_` check, unlike `FinishPropagation` at `sat_solver.cc:641-642`), and `GenericLiteralWatcher` reaches the LP CHECK. This is the recorded chain frame for frame.

Two supporting observations from the existing evidence agree with this: `clause-unsat-transitions.json` shows four `AddClauseDuringSearch` returns with `before_unsat 0 → now_unsat 1, return 0` at depth 0 in the traced runs (the transition exists in practice; those runs merely did not race it with a shared-tree decode), and the 16 "normal" local-UNSAT transitions in the branch trace all returned false through the `!cb()` path.

Why it is a race: it needs (a) a worker whose imported bounds (from other workers' solutions or bound sharing) are pending propagation, and (b) in the same `BeforeTakingDecision`, a shared-tree sync that decodes a not-yet-encoded integer literal whose implication clauses conflict under those bounds. Both require the shared-tree subsolvers, which only exist with automatic allocation (`num_workers=0` → 128 workers, 56 `shared_tree` subsolvers on this box; 8 or 16 workers → 0 shared-tree subsolvers per `observed_configuration`). More workers and more concurrent processes raise the odds.

### Upstream main comparison

Fetched `origin/main` at `d5cf2b9766494816bf7acb2f6c6f449ffde726d8` (2026-09-12). `IntegerSearchHelper::BeforeTakingDecision` is byte-identical to v9.15. `SharedTreeWorker` was rewritten: `SyncWithLocalTrail` no longer exists, and `SyncWithSharedTree` now ends with `return ResetAndEnqueueAssumptions(new_leaf_id);` whose first statement `if (!sat_solver_->ResetToLevelZero()) return false;` **does** return false when `model_is_unsat_` is set. So main's design closes this hole incidentally, but the rewrite (new `SharedTreeEncoder`, node ids, LRAT plumbing, `SatSolver::Propagate(bool)` signature change, `ClausePtr`) is far too large to backport onto 9.15 and would violate the "no upgrade beyond 9.15.x" constraint in spirit. The minimal patch in D reproduces the effect on 9.15.

### Installed wheel identity

* `ortools 9.15.6755`, tags `cp314-cp314-manylinux_2_27_x86_64` / `manylinux_2_28_x86_64`, setuptools 80.9.0. The dist-info carries no git commit; 6755 is OR-Tools' build counter. `Version.txt` on tag v9.15 is `9.15` and `cmake/utils.cmake:71-72` takes the patch number from the environment variable `OR_TOOLS_PATCH`, so a local build can be stamped `9.15.6755` by exporting that before configure (needed if any pin check compares `ortools.__version__`; see `baseline-v23-current-master-native-pins.json` in the integration evidence).
* `ortools/.libs/libortools.so.9`: 33,175,344 bytes, SONAME `libortools.so.9`, Build ID `7a12389fbb42bd8b8f5bb5947aa5113e28af6b36`, compiler string `GCC: (GNU) 14.2.1 20250110 (Red Hat 14.2.1-10)` (the `quay.io/pypa/manylinux_2_28` gcc-toolset used by `tools/release/amd64.Dockerfile:5`). 99 files in `.libs` (abseil `2508.0.0` = `20250814.1`, `libprotobuf.so.33.1.0`, `libre2.so.11`, `libscip.so.10.0`, `libhighs.so.1`, Cbc/Clp/Cgl/Osi/CoinUtils, zlib, bz2). 17 Python extension `.so`s; `sat/python/cp_model_helper.cpython-314-x86_64-linux-gnu.so` NEEDs `libortools.so.9` with RUNPATH `$ORIGIN:$ORIGIN/../../../ortools/.libs`. No headers are shipped in the wheel.
* `nm -DC` confirms all functions in the chain are exported `T` symbols: `SharedTreeWorker::{Search,SyncWithLocalTrail,SyncWithSharedTree,DecodeDecision,AddImplications}`, `IntegerSearchHelper::BeforeTakingDecision`, `LinearProgrammingConstraint::{Propagate,IncrementalPropagate}`, `SatSolver::{Propagate,FinishPropagation,AddClauseDuringSearch,AddProblemClauseInternal}`, `IntegerEncoder::{AddImplications,GetOrCreateAssociatedLiteral,AssociateToIntegerLiteral}`. Dependency tags in the clone match the wheel: abseil `20250814.1`, protobuf `v33.1`, re2 `2025-08-12`, HiGHS `v1.12.0`, SCIP `v10.0.0`, pybind11 `v2.13.6` + `patches/pybind11-v2.13.6.patch`.

## B. Reproduction status

### What exists

* **No deterministic trigger.** The only abort is the one in `.claude/worktrees/transport-first-current/.local-evidence/2026-09-12-master-integration/root-v23-adopted-suite.txt` (pytest with xdist `-n8`; worker `gw2` died in `tests/layout/test_freeform.py::test_freeform_placement_stats_carry_the_operator_telemetry`, then at line 22675, now at 22651). That test calls `FreeformLayout(belt_rules=_BELT_RULES, band_policy=BandPolicy("portable")).lay_out(plastic_spec(), time_budget_s=15.0)` with `workers` omitted.
* **Captured inputs:** 15 packing models from one equivalent run (`NativeAbortExperiment/capture-complete/pack-001..015/`: `model.pb`, `model.textproto`, `parameters.pb` with `random_seed: 20260822`, `max_time_in_seconds: 1.04`, `num_search_workers: 0`) and 8 scaled models under `branch-inputs/`. All 48 matrix replays (workers 0/8/16 × callback on/off × serial/pair) and the 8 traced 128-worker runs finished FEASIBLE/OPTIMAL without an abort. These are tiny models (3 strips; the smoke below saw 11-148 variables) that solve in 10-20 ms, so the window for the race is small.
* The evidence's `native_probe.py` and my harness both wrap the Python `CpSolver.solve` entry; the LD_PRELOAD tracer (`native_transition_trace*.so`) is binary-offset specific and would need rebuilding against a patched library.

### Where automatic allocation can occur in the project

| Call site | Workers | Exposed? |
|---|---|---|
| `src/flab2bp/layout/freeform.py:3341-3345` (`_pack`, via `packing_workers(len(strips), self.workers)` at 5781) | `self.workers`; `packing_workers` returns `1` when strip_count ≥ 15, else `available` (`freeform.py:188-190`) | **Yes** when `self.workers == 0` and fewer than 15 strips |
| `FreeformLayout.__init__` (`freeform.py:4426-4446`) | `DEFAULT_SEARCH_WORKERS if workers is None else workers`; `DEFAULT_SEARCH_WORKERS = 0` (`src/flab2bp/layout/base.py:60`) | **Yes**: every construction without `workers=` |
| `freeform.py:3178-3183` (C-window) | `C_WINDOW_WORKERS = 1` (`freeform.py:2410`) | No |
| `routing_domain.py:9904-9905`, `compact_seed.py:453-454, 652-653` | hard-coded `1` | No |
| `pipeline.py:730` / `cli.py:460` | `min(_available_cpu_count(), DEFAULT_WORKER_BUDGET_CAP)` (16), split by `strategy_race.race_worker_split` with `RACE_MIN_WORKERS = 1` ("Never zero", `strategy_race.py:129-130`) | No: production builds never reach 0 |

Counting direct constructions: `src` 3 (0 with `workers=`), `tests` 94 (about 25 pass `workers=` within the call), `scripts` 6 (0). So the exposure is the test suite and direct library use, exactly where the abort was seen, and it is amplified by xdist running 8 such processes concurrently, each spawning 128 CP-SAT threads.

### Harness (smoke-tested)

`$S/flab_cpsat_capture.py` monkeypatches `ortools.sat.python.cp_model.CpSolver.solve`/`Solve` in-process (no repo edits), writes `model.pb`, `params.pb` and a JSON sidecar per solve to `$FLAB_CPSAT_CAPTURE_DIR/pid-<pid>/solve-NNNN.*` before entering native code, deletes them on normal return (unless `FLAB_CPSAT_CAPTURE_KEEP=1`), and enables `faulthandler` for all threads. In 9.15 `CpModel.proto` and `CpSolver.parameters` are native pybind objects that expose only text format, so it round-trips through `text_format` into `cp_model_pb2`/`sat_parameters_pb2` (the evidence verified text/binary round-trip equality on all 15 captures). `python flab_cpsat_capture.py replay <stem> [time_limit]` re-solves a capture.

Smoke (load check first: mean runnable procs 4, then 9.6; limit 64):

```
cd /home/dannyb/sources/factorio-lab-to-blueprint
PYTHONPATH=$S FLAB_CPSAT_CAPTURE_DIR=$S/captures FLAB_CPSAT_CAPTURE_KEEP=1 \
  timeout 290 taskset -c 64-95 .venv/bin/python -m pytest -p flab_cpsat_capture -x -q \
  -p no:cacheprovider --timeout=280 \
  "tests/layout/test_freeform.py::test_freeform_placement_stats_carry_the_operator_telemetry"
# pytest_exit=0 wall_s=11 ; 51 models captured ; num_search_workers histogram {0: 15, 1: 36} ; variables 11..148
timeout 60 taskset -c 64-95 .venv/bin/python $S/flab_cpsat_capture.py replay $S/captures/pid-*/solve-0001 2.0
# OPTIMAL, 1 s
```

(A first attempt failed with `AttributeError: 'cp_model_helper.CpModelProto' has no attribute 'SerializeToString'`; that is the text-format detail above, fixed before the successful run.) The `tests/conftest.py` `lay_out` memo is an in-process dict (`_CACHE`, line 88), so fresh processes always re-solve.

### Cheapest reproduction attempt (not run; proposed)

Mirror the original conditions rather than the matrix's single-process replays: many processes, each with 128 native threads, over the tests that build `FreeformLayout` with `workers` omitted. Any abort leaves exactly the aborting solve's `model.pb`/`params.pb` behind (run without `KEEP`).

```
cd /home/dannyb/sources/factorio-lab-to-blueprint
for i in $(seq 1 10); do
  vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print "runnable", sum/5}'   # must be < 64
  PYTHONPATH=$S FLAB_CPSAT_CAPTURE_DIR=$S/captures/run-$i \
    timeout 900 taskset -c 64-95 .venv/bin/python -m pytest -p flab_cpsat_capture -q \
    -p no:cacheprovider -n 8 tests/layout/test_freeform.py -k "portable or telemetry or plastic" \
    > $S/captures/run-$i.log 2>&1; echo "run $i exit $?"
  find $S/captures/run-$i -name '*.model.pb' | head   # non-empty => an abort (or Python exception) left inputs behind
done
```

Verify on the first iteration that every xdist worker prints `[flab_cpsat_capture] active` (the `-p` plugin propagates to workers). Note `taskset` does not change what CP-SAT thinks the core count is (`std::thread::hardware_concurrency()` still reports 128), so the subsolver composition stays 128/56 while the 32 pinned cores are heavily oversubscribed, which if anything widens the race window. Budget: 10 iterations ≈ 30-90 min depending on the `-k` subset size. If it does not reproduce, the patch is still justified by the source chain in A, and verification falls back to the replay set plus the same loop on the patched wheel (E).

A deterministic C++ reproduction is feasible once the source builds (a unit test that registers a level-zero callback enqueuing a bound and then forces `GetOrCreateAssociatedLiteral` on a conflicting literal before returning true), but that is optional and costs more than the stress loop.

## C. Build feasibility

### Toolchain and resources on this box

| Item | Value |
|---|---|
| cmake | 4.4.3 (mise) |
| ninja | 1.13.2 (mise) |
| C/C++ compiler | gcc/g++ 16.2.1 20260819 (Red Hat), via ccache (`/usr/lib64/ccache`); no clang |
| SWIG | **not installed** (`find_package(SWIG REQUIRED)` at `cmake/python.cmake:28`; `constraint_solver/_pywrapcp.so` and other legacy wrappers are SWIG); `pip download swig` fetched `swig-4.5.0-py3-none-manylinux_2_12_x86_64...whl` into `$S/swigdl/` (a pip-installable swig binary, install it into the build venv, not `.venv`) |
| Python | 3.14.7 (mise) with `include/python3.14/Python.h`, `libpython3.14.so`, SOABI `cpython-314-x86_64-linux-gnu`, GIL enabled |
| Python build modules required by `cmake/python.cmake` | `mypy-protobuf`, `mypy`, `setuptools`, `wheel`, `typing-extensions`, `virtualenv` (plus `absl-py`, `numpy`, `pandas`, `protobuf` are runtime deps of the package) |
| patchelf / auditwheel | absent; only needed to retag as manylinux, not for a local install |
| Container runtime | `podman` present, `docker` absent (enables the manylinux_2_28 route) |
| Cores / RAM | 128 / 1 TB |
| Disk | `/tmp` tmpfs 504 GB free; `/home/dannyb` 205 TB free |
| ccache | warm (935k cacheable calls, 39 % hits) |

### Python 3.14 support in 9.15

Supported by the CMake python build: `.github/workflows/amd64_linux_cmake_python.yml` matrix builds `3.9`, `3.13`, `3.14` with `cmake -S. -Bbuild -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_CXX_SAMPLES=OFF -DBUILD_CXX_EXAMPLES=OFF -DBUILD_PYTHON=ON -DCMAKE_INSTALL_PREFIX=install`; `ortools/python/setup.py.in` lists `Programming Language :: Python :: 3.14`; pybind11 is `v2.13.6` plus OR-Tools' own patch. Release wheels come from `quay.io/pypa/manylinux_2_28_x86_64` (`tools/release/amd64.Dockerfile:5`, `tools/release/build_delivery_manylinux_amd64.sh`).

### Recommended build recipe (full wheel)

```
export OR_TOOLS_PATCH=6755                      # keeps ortools.__version__ == 9.15.6755 (cmake/utils.cmake:71)
uv venv $S/venv-build --python 3.14.7
uv pip install --python $S/venv-build/bin/python $S/swigdl/swig-4.5.0-*.whl \
    absl-py numpy pandas protobuf mypy-protobuf mypy setuptools wheel typing-extensions virtualenv
cd $S/or-tools && git apply $S/unsat-callback-guard-UNVERIFIED.patch
cmake -S. -Bbuild -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_DEPS=ON -DBUILD_PYTHON=ON \
      -DBUILD_SAMPLES=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DBUILD_VENV=OFF \
      -DPython3_EXECUTABLE=$S/venv-build/bin/python -DSWIG_EXECUTABLE=$S/venv-build/bin/swig \
      -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
cmake --build build -j 96 --target python_package     # wheel lands in build/python/dist/
```

`BUILD_DEPS=ON` fetches and builds the pinned abseil/protobuf/re2/HiGHS/SCIP/Cbc set (same versions as the wheel), so the resulting package is self-consistent and bundles its own `.libs` (`cmake/python.cmake:494-640`). The wheel will be tagged `linux_x86_64`, which `uv pip install <file>` accepts.

Expected time on 128 cores: 15-30 min for the clean build (protobuf, SCIP and the 918 `.cc` files under `ortools/` dominate; ccache is cold for these paths), then 1-2 min per patch iteration (two `.cc` recompiles plus relinking the 33 MB `libortools.so.9` and re-running `setup.py bdist_wheel`).

### Blockers and risks

1. **SWIG missing.** Resolved by the pip wheel above (no system install needed), or `dnf install swig` by the user.
2. **GCC 16 vs the wheel's GCC 14.** The pinned deps (abseil 20250814.1, protobuf 33.1, SCIP 10, HiGHS 1.12) have not been compiled by GCC 16 in OR-Tools CI. Expect possible `-Werror`/new-diagnostic failures; mitigation is `-DCMAKE_CXX_FLAGS="-Wno-error"` or targeted `-Wno-...` flags, or building inside `quay.io/pypa/manylinux_2_28_x86_64` with podman (gcc-toolset-14, identical to the release compiler; Python 3.14 is present in that image as `/opt/python/cp314-cp314`). Budget an extra 30-60 min if this bites.
3. **Version pin checks.** `OR_TOOLS_PATCH=6755` keeps the string; the wheel's `Build ID` and file hashes will differ, so any evidence tooling that hashes `libortools.so.9` (e.g. `native_and_source_hashes` in result.json) must be told about the new hash rather than treated as a mismatch.
4. **Runtime version skew** if the build venv's `protobuf` Python package differs from `.venv`'s; install the patched wheel into a throwaway venv synced from `uv.lock` (E) so every other dependency is identical.

### Alternative if the Python build cannot be made to work: rebuild only `libortools.so.9`

`-DBUILD_PYTHON=OFF -DBUILD_DEPS=ON` builds the C++ library without SWIG or pybind11; then copy the installed wheel tree from `~/.cache/uv/archive-v0/<hash>/` (three cached copies of `ortools-9.15.6755` exist) into a throwaway venv and overwrite `ortools/.libs/libortools.so.9`. This works because `cp_model_helper...so` binds to `libortools.so.9` purely by mangled symbol names and RUNPATH, and the dependency sonames (`libabsl_*.so.2508.0.0`, `libprotobuf.so.33.1.0`, ...) are produced with the same names by `BUILD_DEPS`. Risks: the rebuilt library must be linked so that its NEEDED entries resolve to the wheel's `.libs` copies (check with `readelf -d` and `LD_DEBUG=libs python -c 'import ortools.sat.python.cp_model'`), and any libstdc++ ABI difference between GCC 16 and 14 in exported signatures would surface as an undefined-symbol error at import. It is faster to iterate and avoids SWIG, but the full-wheel route is the one to trust for the gate.

## D. Proposed minimal patch (UNVERIFIED)

File: `$S/unsat-callback-guard-UNVERIFIED.patch` (3 hunks, 2 files, `git apply --check` passes against the v9.15 checkout). Not compiled, not run.

Hunk 1, `ortools/sat/integer_search.cc` at the top of `IntegerSearchHelper::BeforeTakingDecision()`:

```cpp
  if (sat_solver_->ModelIsUnsat()) return false;
```

Hunk 2, same function, the callback loop (this is the evidence's `unsat-callback-candidate.patch` line, kept verbatim):

```cpp
-    if (!cb()) {
+    if (!cb() || sat_solver_->ModelIsUnsat()) {
       sat_solver_->NotifyThatModelIsUnsat();
       return false;
     }
```

Hunk 3, `ortools/sat/work_assignment.cc`, end of `SharedTreeWorker::SyncWithSharedTree()`:

```cpp
   DCHECK(CheckLratInvariants());
+  if (sat_solver_->ModelIsUnsat()) return false;
   return true;
```

Rationale and scope:

* Hunk 2 alone closes the recorded chain: after the offending callback the loop returns false, `SyncWithLocalTrail` returns false (`work_assignment.cc:1162`), and `Search` returns `sat_solver_->UnsatStatus()` (`INFEASIBLE` for a level-zero UNSAT). That is the same path the objective-import callback already takes today when `integer_trail->Enqueue` fails, so the portfolio-level semantics ("this worker proved no better solution exists under the shared bounds") are unchanged and already exercised (16 such transitions in the branch trace).
* Hunk 3 makes the shared-tree callback itself honest, which is what `next_recommended_change` asks for ("all native callback paths must return false once locally UNSAT"), and it matches what upstream main achieves via `ResetToLevelZero()`.
* Hunk 1 is a cheap entry guard for the other two callers of `BeforeTakingDecision` (`integer_search.cc:1567, 1661`) in case a future path arrives already-UNSAT; it can be dropped if reviewers want the patch strictly minimal.
* The LP `CHECK` (`linear_programming_constraint.cc:2142`) and the `DCHECK(!ModelIsUnsat())` in `SatSolver::Propagate` (`sat_solver.cc:2121`) are untouched. A broader alternative, turning that DCHECK into `if (model_is_unsat_) return false;`, would silently change every `Propagate()` caller and was rejected; the evidence's instruction is to preserve the CHECK, and the patch keeps it as the tripwire that will tell us if any other callback path is still unsound.
* Not addressed: the `(void)` casts in `IntegerEncoder::AssociateToIntegerLiteral` remain; making the encoder propagate status upward is a larger API change and not needed once `BeforeTakingDecision` checks the flag.

## E. Recommended sequence and time estimates

Constraints honored throughout: stay on 9.15 (tag v9.15 + this patch, version string `9.15.6755`), no worker-count workaround (`DEFAULT_SEARCH_WORKERS` stays 0, `packing_workers` unchanged), no validator weakening (the LP CHECK stays; no test changes).

| Step | What | Depends on | Estimate |
|---|---|---|---|
| 1 | Stress reproduction on the current wheel with the harness (B, loop above), cores 64-95, load check each iteration | nothing | 30-90 min wall, unattended; a hit yields the exact `model.pb`/`params.pb` |
| 2 | Apply patch to `$S/or-tools`, review the 3 hunks | nothing (parallel with 1) | 10 min |
| 3 | Build venv (`uv venv $S/venv-build`, swig wheel, python modules), configure, build `python_package` | 2 | 20-40 min on 128 cores; +30-60 min if GCC 16 needs flag fixes or the podman manylinux_2_28 route |
| 4 | Verify the wheel in isolation: import, `replay` all 15 evidence captures and the 51 smoke captures with `num_search_workers: 0`, then rerun the step-1 loop against the patched wheel (same iteration count) | 3 (and 1 if it reproduced: replay the aborting input first) | 10 min + same wall as step 1 |
| 5 | Throwaway venv: `UV_PROJECT_ENVIRONMENT=$S/venv-patched uv sync --frozen`, then `uv pip install --python $S/venv-patched/bin/python --force-reinstall --no-deps build/python/dist/ortools-9.15.6755-*.whl`; confirm `flab2bp.__file__` and `ortools.__file__` both resolve inside it (worktree-venv pitfall) | 3 | 10 min |
| 6 | Gate with the throwaway venv: the original `-n8` full suite (`pytest -n 8` from the throwaway venv, exit code is the verdict since the summary line does not print here; 120 s timeout backstop), plus the evidence's acceptance replay (15 s / 8-worker corpus settings) under Main's ownership | 4, 5 | full suite wall unknown from here (the original log reached 79 % after the crash); plan for 1-3 h |
| 7 | Record: new `libortools.so.9` hash and Build ID, patch SHA, build flags, compiler, and the step-4/6 results into a new evidence directory | 6 | 20 min |

Decision points: if step 1 reproduces, step 4 has a strong verification (replay the exact input under the patched wheel, expect a normal status). If it does not, verification rests on the source chain in A plus the absence of aborts in step 4's loop; say so explicitly in the evidence rather than claiming a proven fix. Either way, upstreaming the patch (or a report pointing at `integer.cc:378-403` and `integer_search.cc:1390`) is worthwhile because main has not added the generic guard.

## Commands run (all read-only with respect to the repository)

* Evidence: `python3 -c "json.load(.../NativeAbortExperiment/result.json)"` per key; `find`/`ls` of the evidence tree; `sed -n` on `/home/dannyb/handoff-agents.md` lines 153-206 and 233-268 and on `root-v23-adopted-suite.txt` lines 1-6, 78-135; `cat` of `fatal-boundary.json`, `unsat-callback-candidate.patch`, `native-proof-commands.json`, `v915-lp-check.txt`, `matrix-selection.json`, `experiment-plan.json`, `continuation-handoff.json`, `clause-unsat-transitions.json`, `native-fatal-*.txt`, `main-sharedtree-rewrite.txt`, `capture-summary.json`, `branch-inputs.json`, `capture-complete/pack-001/parameters*.textproto`, `metadata.json`; `grep` over `native_probe.py`.
* Project: `grep -rn -E 'CpSolver|num_search_workers|num_workers|packing_workers|shared_tree' src/flab2bp`; `sed -n` on `freeform.py` 180-215, 3172-3190, 3336-3350, 5772-5790, `strategy_race.py` 126-134, `tests/conftest.py` 118-150; greps for `DEFAULT_SEARCH_WORKERS`, `FreeformLayout(`, `workers=`, pytest config in `pyproject.toml`.
* Wheel: `cat` of `ortools-9.15.6755.dist-info/{METADATA,WHEEL}`; `nm -DC`, `readelf -d`, `readelf -n`, `strings -a | grep GCC` on `libortools.so.9`; `readelf -d` on `cp_model_helper...so`; `find ... -name '*.so' | wc -l`; `find ~/.cache/uv -name 'ortools-9.15*'`.
* Upstream: `git clone --depth 1 --branch v9.15 https://github.com/google/or-tools` (background, timeout 600; found the directory already populated by the sibling agent), `git describe --tags`, `git log -1`, `git fetch --depth 1 origin main` (timeout 300), `git show FETCH_HEAD:<file>` for the diffs; `sed -n`/`grep -n` over `linear_programming_constraint.cc`, `integer_search.cc`, `work_assignment.cc`, `sat_solver.cc`, `sat_solver.h`, `integer.cc`, `cp_model_solver_helpers.cc`, `CMakeLists.txt`, `cmake/python.cmake`, `cmake/dependencies/CMakeLists.txt`, `cmake/utils.cmake`, `ortools/python/setup.py.in`, `.github/workflows/amd64_linux_cmake_python.yml`, `tools/release/*`, `patches/`. All git invocations used `GIT_EDITOR=true` and `GIT_TERMINAL_PROMPT=0`.
* Patch generation: edited the two files in the scratch clone, `git -c diff.external= diff --no-ext-diff` (the user's git has an external diff tool configured, which produced a side-by-side view on the first attempt), `git checkout -- <files>` to restore, `git apply --check`.
* Toolchain: `which`, `cmake --version`, `ninja --version`, `gcc --version`, `nproc`, `df -h /tmp /home/dannyb`, `free -g`, `ccache -s`, `python3 -c 'sysconfig...'`, `which podman docker`, `.venv/bin/pip download swig --no-deps -d $S/swigdl`.
* Smoke: the two pytest commands and the replay command quoted in B, each preceded by `vmstat 1 6 | tail -n 5 | awk ...` (4 and 9.6 mean runnable processes).
