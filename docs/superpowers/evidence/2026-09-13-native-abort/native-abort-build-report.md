# OR-Tools 9.15 CP-SAT native abort: patched build and verification

Date: 2026-09-13 (05:00-06:30 UTC). Executes section E of `native-abort-scope.md` (same directory). Scratch dir `$S` = `/tmp/claude-839601109/-home-dannyb-sources-factorio-lab-to-blueprint/c950ce94-0e3e-4854-a244-ea8916bf39cd/scratchpad/ortools/`. Nothing under the repository working tree, `.venv`, `pyproject.toml` or `uv.lock` was modified; the patched wheel was installed only into the throwaway venv `$S/venv-patched`.

## Verdict

* **The abort reproduced on the current wheel** (section 1): one worker of a 20-iteration, 8-process stress loop over `tests/layout/test_freeform.py` died in the same test as the original evidence with a native stack identical frame for frame to the original (`libortools.so.9+0x57d5d7` = `LinearProgrammingConstraint::Propagate() [clone .cold]` under `GenericLiteralWatcher::Propagate` <- `SatSolver::Propagate` <- `IntegerSearchHelper::BeforeTakingDecision` <- `SharedTreeWorker::SyncWithLocalTrail`). The harness left the aborting model behind; it is a race, not a deterministic input (30/30 OPTIMAL when replayed alone on the current wheel).
* **The 3-hunk patch applies, compiles and does not change behaviour when the solver is not UNSAT** (section 2).
* **A patched `ortools-9.15.6755` wheel was built on this box with GCC 16** after three toolchain fixes, none of them source changes (section 3).
* **Verification on the patched wheel** (section 4): all 266 replays OPTIMAL (15 evidence captures, 51 smoke captures, 200 repeats of the aborting model, all with `num_search_workers: 0`); the same two stress loops (10 x 15 tests and 20 x 763 tests, 8 processes each) ran with zero aborts and zero leftover captures; `pytest -n 8 tests/layout/test_freeform.py` passed 763/763.
* **Full-suite gate** (section 5): see the table there. Four `tests/test_pipeline.py` failures are pre-existing at HEAD `9c958bc9` (they fail identically on the current wheel); the two `test_strategy_race.py` failures seen on the first patched run were caused by the throwaway venv having no cached bytecode (this session exports `PYTHONDONTWRITEBYTECODE=1`), not by the wheel, and go away once the venv is byte-compiled.
* **What this does and does not prove.** The race reproduced once in 20 full-file iterations on the current wheel (roughly 1 in 15,000 test executions) and zero times in 20 on the patched wheel. That is consistent with the fix and with the source-level chain in the scope report, but one hit versus zero is not a statistical proof; the strong evidence remains the traced call chain plus the fact that every hunk only fires when `model_is_unsat_` is already set. The evidence's `native_and_source_hashes` must be told about the new `libortools.so.9` hash and Build ID (section 3).

## 1. Reproduction on the current wheel

Load discipline: every iteration ran `vmstat 1 6 | tail -n 5 | awk '{sum+=$1} END {print sum/5}'` and waited while the mean runnable count was 64 or more; the recorded values per run are in `$S/captures/*/summary.txt` (2.4 to 59.8 while the OR-Tools build was running on the other cores, 0.6 to 15 otherwise). All loops were pinned with `taskset -c 32-63`; `-n 8` xdist workers, each spawning 128 CP-SAT threads (`num_search_workers: 0`), so the 32 cores were heavily oversubscribed, as in the original run.

Harness: `$S/flab_cpsat_capture.py` loaded with `-p flab_cpsat_capture` (every xdist worker printed `[flab_cpsat_capture] active`: 9 lines per run = controller + 8 workers). Without `FLAB_CPSAT_CAPTURE_KEEP`, only an aborting solve leaves `solve-NNNN.{model.pb,params.pb,json}` behind.

| Loop | Command core | Iterations | Result |
|---|---|---|---|
| `stress-before` (section B of the scope report, verbatim) | `pytest -p flab_cpsat_capture -q -p no:cacheprovider -n 8 tests/layout/test_freeform.py -k "portable or telemetry or plastic"` | 10 x 15 tests, 11-13 s each | all exit 0, no abort, no leftover |
| `stress-full-before` | same without `-k` | 20 x 763 tests, 22-49 s each | **run 5 aborted** (exit 1, 1 leftover capture, `worker 'gw2' crashed while running 'tests/layout/test_freeform.py::test_freeform_placement_stats_carry_the_operator_telemetry'`); runs 9, 10, 11 exit 1 with `NameError: name '_group_by' is not defined` raised from repo source, which is a concurrent edit of the working tree by another agent (HEAD moved from `460f3777` to `01b9cdf4` and then to a rewritten `9c958bc9` during this session), not an OR-Tools symptom; 16 runs clean |

The run-5 log (`$S/captures/stress-full-before/run-5.log`) has `Fatal Python error: Aborted`, the Python stack through `cp_model.py:1771 solve` <- `flab_cpsat_capture.py:103 _solve` <- `freeform.py:3072 _pack_result` <- `freeform.py:3373 _pack`, and the faulthandler C stack:

```
abort+0x26
libabsl_log_internal_message.so.2508.0.0  absl::lts_20250814::log_internal::LogMessage::FailWithoutStackTrace()+0x22
libortools.so.9 +0x57d5d7                                   (LinearProgrammingConstraint::Propagate() [clone .cold])
libortools.so.9 GenericLiteralWatcher::Propagate(Trail*)+0x229
libortools.so.9 SatSolver::Propagate()+0xa9
libortools.so.9 IntegerSearchHelper::BeforeTakingDecision()+0x182
libortools.so.9 SharedTreeWorker::SyncWithLocalTrail()+0xd8
libortools.so.9 SharedTreeWorker::Search(std::function<void()> const&)+0x1b5
libortools.so.9 SolveLoadedCpModel(CpModelProto const&, Model*)+0xf4a
libortools.so.9 ThreadPool::RunWorker()+0x49
```

Same offsets and same frames as `root-v23-adopted-suite.txt:104-113` in the 2026-09-12 evidence. The aborting solve is `$S/captures/stress-full-before/run-5/pid-1602394/solve-0217.{model.pb,params.pb,json}`: 11 variables, 17 constraints, `num_search_workers: 0`, `random_seed: 20276660`, `max_time_in_seconds: 1.0478`. Replayed alone on the current wheel with `num_search_workers: 0` it is OPTIMAL 30/30 in about 10 ms each (`$S/replay_all.py`), so the race needs the concurrent load, exactly as section A of the scope report predicts.

Absl's fatal message text does not appear in the pytest log (the wheel's absl stderr sink is silent); the C stack is what identifies the CHECK.

## 2. Patch as applied

* Source: `$S/unsat-callback-guard-UNVERIFIED.patch`, sha256 `abdc17fc438315484e8afd93b92e45e26e97d6fde93d5395418f1fe996d7a017`, applied with `GIT_EDITOR=true git apply` on tag `v9.15` (`551ad10d94835c99e5e1e684500d3db398c0e345`). Resulting `git diff` (no external diff): 2 files, +15 -1, sha256 of the diff text `055f79f74f3149627f348d11f6b0c66d5d96e8e9cdf88032c7eaf2ce21179144`.
* Hunk 1 (`integer_search.cc`, first line of `IntegerSearchHelper::BeforeTakingDecision()`): `if (sat_solver_->ModelIsUnsat()) return false;`
* Hunk 2 (same function, callback loop): `if (!cb() || sat_solver_->ModelIsUnsat()) { NotifyThatModelIsUnsat(); return false; }`
* Hunk 3 (`work_assignment.cc`, end of `SharedTreeWorker::SyncWithSharedTree()`): `if (sat_solver_->ModelIsUnsat()) return false;` before `return true;`

Review against the scope report's call chain: `ModelIsUnsat()` is the pure getter `return model_is_unsat_;` (`sat_solver.h:169`). When the solver is not UNSAT hunk 1's condition is false, hunk 2's `||` is only evaluated after `cb()` returned true and is then false so the loop continues unchanged, and hunk 3 falls through to the original `return true`. So no code path changes unless `model_is_unsat_` is already set, and in that state every caller of `BeforeTakingDecision` already handles `false` (`integer_search.cc:1577` returns `UnsatStatus()`, whose DCHECK is satisfied; `integer_search.cc:1671`, `lb_tree_search.cc:537`, `work_assignment.cc:1162`). The LP `CHECK` and the `DCHECK` in `SatSolver::Propagate` are untouched. Nothing else in the tree was modified.

## 3. Build

Recipe (`$S/build.sh`, four attempts, all logs kept in `$S`):

```
uv venv $S/venv-build --python 3.14.7
uv pip install --python $S/venv-build/bin/python swig==4.3.0 absl-py numpy pandas protobuf \
    mypy-protobuf mypy setuptools wheel typing-extensions virtualenv
export OR_TOOLS_PATCH=6755 PATH=$S/venv-build/bin:$PATH
cd $S/or-tools   # tag v9.15 + the patch
cmake -S. -Bbuild -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_DEPS=ON -DBUILD_PYTHON=ON \
      -DBUILD_SAMPLES=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DBUILD_VENV=OFF \
      -DPython3_EXECUTABLE=$S/venv-build/bin/python -DSWIG_EXECUTABLE=$S/venv-build/bin/swig \
      -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
      -DCMAKE_C_FLAGS="-include stdlib.h"
cmake --build build -j 64 --target python_package
```

| Item | Value |
|---|---|
| Compiler | gcc/g++ 16.2.1 20260819 (Red Hat 16.2.1-2) via ccache; cmake 4.4.3; ninja 1.13.2; SWIG 4.3.0 (PyPI wheel in the build venv); Python 3.14.7 |
| C++ flags (from `build.ninja` for `integer_search.cc.o`) | `-O3 -DNDEBUG -fPIC -fwrapv -std=c++17` plus `-DUSE_BOP -DUSE_CBC -DUSE_CLP -DUSE_GLOP -DUSE_HIGHS -DUSE_MATH_OPT -DUSE_PDLP -DUSE_SCIP` |
| C flags | `-include stdlib.h` (see fix 1) |
| Version stamp | `OR_TOOLS_PATCH=6755` -> `ortools.__version__ == '9.15.6755'` (cmake printed `ortools version: 9.15.6755`) |
| Dependencies | `BUILD_DEPS=ON`: the tag's pinned abseil 20250814.1, protobuf 33.1, re2, HiGHS 1.12, SCIP 10.0.0 + SoPlex 8, Cbc/Clp/Cgl/Osi/CoinUtils, zlib, bz2, Eigen, Boost (for SoPlex), pybind11 2.13.6 |
| Wall time | configure 572 s (network fetch of all dependencies, Boost submodules dominate); compile 205 s + 165 s + 74 s + 54 s across the four attempts (ninja resumed incrementally); 19 min total |
| Wheel | `$S/or-tools/build/python/dist/ortools-9.15.6755-cp314-cp314-linux_x86_64.whl`, 31 MB, sha256 `a688aa1ae24d5f598b6076aa28efb45d1c4559af8708c06d1c505969178167ae` |
| `ortools/.libs/libortools.so.9` before (current wheel) | 33,175,344 bytes, sha256 `0ccccfdeff2158e8d044bfd7c5b9c4dbad1746ecb89bdf20f32a7dac0b643914`, Build ID `7a12389fbb42bd8b8f5bb5947aa5113e28af6b36`, `GCC: (GNU) 14.2.1 20250110 (Red Hat 14.2.1-10)` |
| `ortools/.libs/libortools.so.9` after (patched wheel) | 32,909,408 bytes, sha256 `9c1a22af92d691db3b9f7eade4a846127cada2364f0bc59d780d3c6198304410`, Build ID `f0d82bd58e613f89c71a56a56a3a006e55bf32d1`, `GCC: (GNU) 16.2.1 20260819 (Red Hat 16.2.1-2)` |
| `.libs` contents | 99 files in both wheels, identical set of sonames; the patched dependency libraries are within 1-5 % of the release sizes |
| Patched objects | `integer_search.cc.o`, `work_assignment.cc.o` (and `linear_programming_constraint.cc.o`) compiled in attempt 2's log; both symbols exported from the new library |

GCC 16 fixes, in the order they were hit (each attempt's full log is `$S/build-attempt{1,2,3}-*.log`, the successful one `$S/build.log`):

1. **SCIP `tinycthread.c` failed to compile** (`conflicting types for 'pthread_once_t'; have '__once_flag'`). Not a C-standard issue (the file already compiles with `-std=c99`): current glibc's `stdlib.h` line 1191 includes `bits/types/once_flag.h` under its ISO C23 feature gate, and tinycthread's `#define once_flag pthread_once_t` (line 453) runs before that header. Pre-including `stdlib.h` (`-DCMAKE_C_FLAGS="-include stdlib.h"`) puts the glibc typedef first; verified on the failing command line before rebuilding. SCIP never uses `once_flag`/`call_once` outside tinycthread itself. `-include threads.h` does not work (`threads.h` is empty under `-std=c99`). The report's `-Wno-error` suggestion did not apply (hard error, not a warning).
2. **`protoc-gen-mypy: program not found`** for all 54 `_pb2.py` targets: `cmake/python.cmake` runs protoc with `--mypy_out`, which needs the `mypy-protobuf` plugin on `PATH`; fixed by exporting `PATH=$S/venv-build/bin:$PATH` for the build.
3. **SWIG wrappers failed to compile** (`'PyInt_AsLong' was not declared in this scope` in `ortools/base/python-swig.h:108,120` and the generated `*PYTHON_wrap.cxx`): SWIG 4.5.0 (the wheel downloaded during scoping) dropped the Python 2 compatibility macros that OR-Tools' SWIG header still uses; the release toolchain uses SWIG 4.3.0 (`tools/release/amd64.Dockerfile:6`). Fixed by `uv pip install swig==4.3.0` into the build venv and deleting the two generated `*PYTHON_wrap.cxx` files so they were regenerated.

The podman manylinux_2_28 route was not needed. A `podman pull quay.io/pypa/manylinux_2_28_x86_64` was started as a fallback and failed on this box with `initializing destination container` (exit 125, rootless storage), so that route would need storage set up first if a GCC 14 build is ever wanted.

## 4. Verification of the patched wheel

Throwaway venv: `UV_PROJECT_ENVIRONMENT=$S/venv-patched uv sync --frozen` from the repo root, then `uv pip install --python $S/venv-patched/bin/python --force-reinstall --no-deps <wheel>`. Checked: `ortools.__version__ == '9.15.6755'`, `ortools.__file__` and `libortools.so.9` resolve inside `$S/venv-patched`, `flab2bp.__file__` resolves to `/home/dannyb/sources/factorio-lab-to-blueprint/src/flab2bp/__init__.py` (the main checkout, no worktree), a one-variable CP-SAT smoke solve is OPTIMAL.

Replays (`$S/replay_all.py`, forces `num_workers: 0`, keeps each capture's own time limit; pinned to cores 32-63):

| Set | Current wheel | Patched wheel |
|---|---|---|
| 15 evidence captures (`.local-evidence/2026-09-12-architecture/NativeAbortExperiment/capture-complete/pack-001..015`) | 15/15 OPTIMAL, 0.2 s | 15/15 OPTIMAL, 0.2 s |
| 51 smoke captures (`$S/captures/pid-1533020/solve-0001..0051`) | 51/51 OPTIMAL, 0.7 s | 51/51 OPTIMAL, 0.6 s |
| the aborting `solve-0217` | 30/30 OPTIMAL | 200/200 OPTIMAL, 1.9 s |

Stress loops on the patched wheel (same scripts, same cores, `$S/captures/stress-after` and `$S/captures/stress-full-after`):

| Loop | Iterations | Result |
|---|---|---|
| `stress-after` (section B loop) | 10 x 15 tests, 19-24 s each | all exit 0, no abort, no leftover, 9 `active` lines per run |
| `stress-full-after` | 20 x 763 tests, 31-36 s each | no abort, no leftover, no `crashed` line in any run; run 12 exit 1 on `TestProducerWithManyConsumers::test_it_lays_out_and_validates` with a `NoValidLayout` refusal (`the 0.5s deadline passed with no completed packing of 8 strips`), a budget flake under the 8 x 128-thread oversubscription, not a native failure; 19 runs exit 0 |

The per-iteration wall times on the patched wheel are 5-8 s higher than on the current wheel for the reason found in section 5 (no cached bytecode in the fresh venv), not because the solver is slower: the replay timings above are equal.

## 5. Gate

Gate runs use `$S/venv-patched/bin/python -m pytest ... -p no:cacheprovider`, pinned to cores 32-63; the pyproject's 120 s per-test timeout applies; exit code is the verdict, and on this run the summary line did print.

| Stage | HEAD | Exit | Wall | Result |
|---|---|---|---|---|
| `pytest -n 8 tests/layout/test_freeform.py` | `9c958bc9` (clean tree) | 0 | 29 s | `763 passed, 27 warnings` |
| `pytest -n 8` (full suite), first run, venv without `.pyc` | `9c958bc9` (clean tree) | 1 | 191 s | `6 failed, 4548 passed, 2 skipped`; no crash, no abort |

The six first-run failures, and what they turned out to be:

* `tests/test_pipeline.py::test_unfunded_strategy_race_falls_back_to_serial_strategies[1-1,2-1,3-1]` and `tests/test_pipeline.py::test_blueprint_encoding_failure_does_not_abort_later_strategy` (`assert isinstance(layout, TransportRoutingLayout)`): **pre-existing at this HEAD.** The same four fail with the current wheel from `.venv` under the same pin (control A). Not related to OR-Tools.
* `tests/layout/test_strategy_race.py::test_the_real_pool_races_all_four_arms_end_to_end` and `::test_a_raced_build_delivers_events_from_both_arms_to_the_parent` (`assert all(outcome.status == "completed" ...)`; `--showlocals` shows the freeform arm `refused` with `the 2s deadline passed`): failed 3/3 on the patched venv and passed 3/3 on `.venv` (8 s vs 15 s per pair). Cause: this session exports `PYTHONDONTWRITEBYTECODE=1`, the fresh throwaway venv had **zero** `.pyc` files (the repo venv has 5,760), and `python -X importtime` showed `import ortools.sat.python.cp_model` at 1.45 s versus 0.55 s and `pandas` at 1.04 s versus 0.28 s in the same interpreter binary. Each spawned race arm paid about 1 s of bytecode compilation inside its 2 s budget. `LD_DEBUG=statistics` shows equal relocation counts for both wheels, and the solver replays are equal, so the native library is not slower.

Re-gate after `python -m compileall` of the throwaway venv, plus a same-HEAD full-suite baseline on the current wheel for a like-for-like comparison (`$S/chain2.sh`, `$S/gate/summary2.txt`):

| Step | Result |
|---|---|
| `compileall` of `$S/venv-patched/.../site-packages` | exit 0, 1 s, 5,584 `.pyc` written |
| `import ortools.sat.python.cp_model` / `import flab2bp.layout.freeform`, 3 runs each | current wheel 0.41-0.46 s / 0.54-0.73 s; patched wheel 0.31-0.43 s / 0.34-0.51 s (equal within noise) |
| the two `test_strategy_race.py` tests, patched wheel, 3 runs | exit 0 each, 7 s per pair (was 15 s and failing before byte-compilation) |
| `pytest -n 8` full suite, **patched wheel**, HEAD `9c958bc9`, clean tree | exit 1, 180 s, `4 failed, 4550 passed, 2 skipped, 27 warnings`; 0 crashed workers, 0 aborts |
| `pytest -n 8` full suite, **current wheel** (`.venv`), same HEAD, same pin | exit 1, 196 s, `4 failed, 4550 passed, 2 skipped`; 0 crashed workers, 0 aborts |

The four failures are the same four `tests/test_pipeline.py` tests on both wheels (`test_blueprint_encoding_failure_does_not_abort_later_strategy` and `test_unfunded_strategy_race_falls_back_to_serial_strategies[1-1,2-1,3-1]`); they also fail with the patched wheel without any CPU pin (control B), so they are a property of HEAD `9c958bc9`, not of the affinity mask or of either wheel. **Gate verdict: the patched wheel is indistinguishable from the current wheel on the full suite (4550 passed, the same 4 pre-existing failures), with zero native aborts across the two gates and the 30 stress iterations.** Whoever owns the `frontier-admission-memo` branch should look at those four pipeline failures separately.

## 6. Files

| Path | What |
|---|---|
| `$S/stress_loop.sh`, `$S/stress_loop_full.sh` | the two stress loops (load check, taskset, capture plugin, per-run summary) |
| `$S/build.sh` | configure + build with markers (`EXTRA_C`, `EXTRA_CXX` env) |
| `$S/replay_all.py` | replays evidence `pack-NNN/` dirs or harness stems with optional worker/time-limit overrides |
| `$S/chain_after.sh`, `$S/chain2.sh` | detached verification chains; results in `$S/captures/*/summary.txt`, `$S/gate/summary.txt`, `$S/gate/summary2.txt` |
| `$S/captures/stress-full-before/run-5/pid-1602394/solve-0217.*` | the aborting model and parameters from the reproduction |
| `$S/captures/*/run-N.log` | every pytest log |
| `$S/build-attempt1-gcc16-tinycthread-fail.log`, `-attempt2-protoc-gen-mypy-fail.log`, `-attempt3-swig45-pyint-fail.log`, `build.log` | build logs |
| `$S/or-tools/build/python/dist/ortools-9.15.6755-cp314-cp314-linux_x86_64.whl` | the patched wheel |
| `$S/venv-build`, `$S/venv-patched` | build venv; throwaway venv with the patched wheel (byte-compiled by chain 2) |

`$S` is under `/tmp` (tmpfs) and disappears on reboot; copy the wheel, the patch and `solve-0217.*` somewhere durable if they are to be kept.

## 7. Exact commands (in order)

```
S=/tmp/claude-839601109/-home-dannyb-sources-factorio-lab-to-blueprint/c950ce94-0e3e-4854-a244-ea8916bf39cd/scratchpad/ortools
cd /home/dannyb/sources/factorio-lab-to-blueprint
# 1. stress loops on the current wheel (detached, cores 32-63)
setsid nohup $S/stress_loop.sh .venv/bin/python $S/captures/stress-before 10 32-63 &
setsid nohup $S/stress_loop_full.sh .venv/bin/python $S/captures/stress-full-before 20 32-63 &
# 2. patch
cd $S/or-tools && GIT_EDITOR=true git apply --check $S/unsat-callback-guard-UNVERIFIED.patch && GIT_EDITOR=true git apply $S/unsat-callback-guard-UNVERIFIED.patch
# 3. build (see section 3 for the cmake line; attempts 1-3 differed only by the fixes listed there)
uv venv $S/venv-build --python 3.14.7
uv pip install --python $S/venv-build/bin/python $S/swigdl/swig-4.5.0-*.whl absl-py numpy pandas protobuf mypy-protobuf mypy setuptools wheel typing-extensions virtualenv
uv pip install --python $S/venv-build/bin/python swig==4.3.0
find $S/or-tools/build -name '*PYTHON_wrap.cxx' -delete
EXTRA_C="-include stdlib.h" setsid nohup $S/build.sh &
# 4. throwaway venv + replays
UV_PROJECT_ENVIRONMENT=$S/venv-patched uv sync --frozen
uv pip install --python $S/venv-patched/bin/python --force-reinstall --no-deps $S/or-tools/build/python/dist/ortools-9.15.6755-cp314-cp314-linux_x86_64.whl
taskset -c 32-63 $S/venv-patched/bin/python $S/replay_all.py patched-evidence .local-evidence/2026-09-12-architecture/NativeAbortExperiment/capture-complete/pack-0* --workers 0
taskset -c 32-63 $S/venv-patched/bin/python $S/replay_all.py patched-smoke $S/captures/pid-1533020/solve-00{01..51} --workers 0
taskset -c 32-63 $S/venv-patched/bin/python $S/replay_all.py patched-abort-0217 $S/captures/stress-full-before/run-5/pid-1602394/solve-0217 --workers 0 --repeat 200
# 5. stress loops + gates on the patched wheel
setsid nohup $S/chain_after.sh &      # stress-after, stress-full-after, pytest -n 8 tests/layout/test_freeform.py, pytest -n 8
# 6. controls for the six failures, then byte-compile and re-gate
taskset -c 32-63 .venv/bin/python -m pytest -n 8 -p no:cacheprovider -q <the six test ids>
$S/venv-patched/bin/python -m pytest -p no:cacheprovider -q --showlocals tests/layout/test_strategy_race.py::test_the_real_pool_races_all_four_arms_end_to_end
setsid nohup $S/chain2.sh &           # compileall venv-patched; race tests x3; pytest -n 8 on patched and on current wheel
```
