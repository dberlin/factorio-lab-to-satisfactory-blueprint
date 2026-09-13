# Phase 0 — Cache eligibility measurement

## Result

**Status: Phase 0 complete. Gate 0: STOP. Separate fragment-harvest capture pending required baseline rebase and idle-machine clearance.**

The authoritative whole-demand capture measured **2.625445 s / 30.016765 s = 8.7466%**. This is below the fixed 10% stop threshold, so Gate 0 remains **STOP** and no dependent persistent-cache phase is authorized.

Independent review motivated a separate, expanded fragment-harvest experiment because refused parent rows can contain independently certifiable compound fragments. That experiment is additive research only: it does not supersede, reopen, or replace the approved Phase 0 Gate decision. Its pending captures will be reported separately from the authoritative whole-demand evidence.

The opt-in experimental harvester recognizes either a multi-machine window from one recipe group or at least two connected recipe groups. It certifies each positive fragment through independent plan, route, finalization, and validation replay; excludes typed logical, strip-instance, or inexact-boundary evidence; and counts only exact original-request strip-instance planning spans selected by a deterministic vertex-and-edge-disjoint maximum cover. Replay time is recorded but excluded from the experimental numerator.

No cache, cache lookup, persistence, mining, specialized planner, candidate-order change, or ordinary request-behavior change was added. The semantic fingerprint remains provisional, deterministic, and measurement-only.

## Files changed

- `src/flab2bp/bench/metrics.py`
  - Added canonical SHA-256 semantic demand measurement fingerprints.
  - Canonicalization uses reduced rational numerator/denominator pairs, sorted mappings/sets/proofs, and semantic `BuildSpec` fields; it excludes labels and process-local identity.
  - Added typed `CacheEligibilityMetrics` serialization and workload classification.
- `src/flab2bp/bench/fragment_harvest.py`
  - Added bounded deterministic within-group windows and connected cross-group fragment candidates for the separate experiment.
  - Added exact residual boundary rates, cargo domains, boundary roles, stable peer/logical topology with multiplicity, selected spray supply, surplus-output preservation, failure exclusion, independent replay certificates, and exact disjoint-cover accounting.
- `src/flab2bp/layout/base.py`
  - Extended the existing `PlacementStats` schema with the raw timing/counter fields used by the typed attribution layer.
- `src/flab2bp/layout/freeform.py`
  - Preserved the original Phase 0 timing categories.
  - Added opt-in exact `StripInstanceId` planning spans plus prepared/detailed routing seam evidence; ordinary planning still produces the same strips in the same order.
- `src/flab2bp/layout/route_feedback.py`
  - Added a typed logical-net observation carrying exact stable strip-instance endpoints for measurement-only topology evidence.
- `src/flab2bp/layout/sequence_solver.py`
  - Propagated the same timing categories through stage observations and separated power, finalization, and validation.
- `src/flab2bp/pipeline.py`
  - Attached the semantic fingerprint and typed eligibility metrics to each existing attempt without changing attempt order or winner selection.
- `scripts/audit.py`
  - Preserved opt-in `--gate-0` interleaved controls and added the further opt-in `--fragment-harvest` mode.
  - Records positive certificates from CLEAN or REFUSED parents, replays them independently, and computes the unchanged PASS/HOLD/STOP thresholds from repeated disjoint actual spans.
  - Normal audit job generation, fingerprint allocation, and JSON schema remain unchanged; measurement objects exist only for `gate0_control` jobs.
- `tests/test_pipeline.py`
  - Added fingerprint, hash-seed, insertion-order, typed serialization, and unchanged-result coverage.
- `tests/scripts/test_audit.py`
  - Added controls/order, typed JSONL, fixed-threshold, rejected-demand attribution, ordinary-schema isolation, refused-parent harvest, stable machine-window serialization, and disjoint Gate accounting coverage.
  - `tests/bench/test_fragment_harvest.py` covers within-group and cross-group eligibility, single-machine/disconnected exclusions, semantic deduplication, stable failure exclusion, exact original spans, replay-time exclusion, and deterministic disjoint cover.

- `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/*.jsonl`
  - Persisted all five byte-identical cold source captures for independent recomputation.
- `.superpowers/sdd/2026-09-05-background-compound-block-cache/progress.md`
  - Retains the authoritative original Phase 0 STOP and records the expanded fragment experiment as separate, non-gating work.

## TDD evidence

### RED

Before implementation, the eight initially specified Phase 0 nodes were run directly:

```bash
uv run pytest -q \
  tests/test_pipeline.py::test_measurement_fingerprint_is_semantic_and_mapping_order_independent \
  tests/test_pipeline.py::test_measurement_fingerprint_is_stable_across_python_hash_seeds \
  tests/test_pipeline.py::test_typed_cache_eligibility_metrics_serialize_zero_and_nonzero_attribution \
  tests/test_pipeline.py::test_pipeline_measurement_is_typed_and_does_not_change_attempt_order_or_result \
  tests/scripts/test_audit.py::test_gate_zero_jobs_cover_each_workload_without_changing_normal_job_order \
  tests/scripts/test_audit.py::test_audit_record_serializes_typed_demand_and_attribution \
  tests/scripts/test_audit.py::test_gate_zero_summary_reports_recurrence_controls_and_unweakened_decision \
  tests/scripts/test_audit.py::test_gate_zero_summary_stops_below_ten_percent_without_weakening_thresholds
```

Result: **RED**, exit 4 during collection. Both modules failed to import the intentionally missing `CacheEligibilityMetrics`/measurement API.

The subsequently clarified rejected-demand rule was also pinned by `test_rejected_exact_demand_is_measurement_signal_not_negative_cache_savings`: a repeated exact rejection remains a measurement signal but contributes zero claimed reusable-positive work.

### GREEN

The nine Phase 0 nodes were rerun after implementation:

```bash
uv run pytest -q \
  tests/test_pipeline.py::test_measurement_fingerprint_is_semantic_and_mapping_order_independent \
  tests/test_pipeline.py::test_measurement_fingerprint_is_stable_across_python_hash_seeds \
  tests/test_pipeline.py::test_typed_cache_eligibility_metrics_serialize_zero_and_nonzero_attribution \
  tests/test_pipeline.py::test_pipeline_measurement_is_typed_and_does_not_change_attempt_order_or_result \
  tests/scripts/test_audit.py::test_gate_zero_jobs_cover_each_workload_without_changing_normal_job_order \
  tests/scripts/test_audit.py::test_audit_record_serializes_typed_demand_and_attribution \
  tests/scripts/test_audit.py::test_gate_zero_summary_reports_recurrence_controls_and_unweakened_decision \
  tests/scripts/test_audit.py::test_gate_zero_summary_stops_below_ten_percent_without_weakening_thresholds \
  tests/scripts/test_audit.py::test_rejected_exact_demand_is_measurement_signal_not_negative_cache_savings
```

Result: **9 passed**, exit 0.

### Review correction RED/GREEN

Independent review found that ordinary audit cells still constructed and serialized the opt-in measurement objects. Two focused tests pinned the boundary:

```bash
uv run pytest -q \
  tests/scripts/test_audit.py::test_ordinary_run_cell_omits_gate_zero_measurement_work_and_json \
  tests/scripts/test_audit.py::test_gate_zero_run_cell_constructs_and_serializes_measurement_work
```

RED result: **1 failed, 1 passed**, exit 1. The ordinary cell reached the patched fingerprint function and failed with `ordinary audit constructed a Gate 0 fingerprint`.

After restricting fingerprint construction, metric allocation, and serialization to `gate0_control` jobs, the same command passed: **2 passed**, exit 0, 1.99 s.

Required focused regression command:

```bash
uv run pytest -q tests/test_pipeline.py tests/scripts/test_audit.py
```

Result after the review correction: **PASS, 100%, exit 0, 83.46 s**.

The controller retains ownership of project-wide pytest, Ruff, formatter, MyPy, frontend, and builds, per the task brief.

Review-requested static cleanup was verified directly:

```bash
uv run pytest -q \
  tests/scripts/test_audit.py::test_rejected_exact_demand_is_measurement_signal_not_negative_cache_savings
uv run ruff check \
  src/flab2bp/bench/metrics.py \
  src/flab2bp/layout/sequence_solver.py \
  tests/scripts/test_audit.py
```

Result: **1 passed** and **All checks passed**, both exit 0. The test now explicitly narrows the optional Gate 0 metric before field access.

### Fragment-harvest correction RED/GREEN

The first focused harvest test failed during collection with `ModuleNotFoundError: flab2bp.bench.fragment_harvest`. After the initial implementation, the corrected within-group semantics were pinned before production changes:

```bash
uv run pytest -q \
  tests/bench/test_fragment_harvest.py::test_enumerator_emits_repeated_multi_machine_windows_with_typed_boundaries \
  tests/bench/test_fragment_harvest.py::test_enumerator_rejects_single_machine_and_disconnected_single_machine_pairs \
  tests/bench/test_fragment_harvest.py::test_opt_in_trace_records_stable_selected_strip_planning_spans
```

RED result: **2 failed, 1 passed**. The enumerator did not accept stable strip instances, and planning spans were still keyed by family rather than exact machine window.

The JSON certificate boundary was separately run RED: **1 failed**, because deserialization replaced a recorded `(start=6, count=6)` window with a synthetic `(start=0, count=1)` identity.

After implementation, the comprehensive focused command covered fragment eligibility, seam evidence, exact spans, typed exclusions, shadow certification, refused parents, JSON identity, disjoint cover, opt-in behavior, ordinary schema isolation, and unchanged Gate thresholds:

```bash
uv run pytest -q \
  tests/bench/test_fragment_harvest.py \
  tests/scripts/test_audit.py::test_ordinary_run_cell_omits_gate_zero_measurement_work_and_json \
  tests/scripts/test_audit.py::test_gate_zero_run_cell_constructs_and_serializes_measurement_work \
  tests/scripts/test_audit.py::test_refused_gate_cell_harvests_real_freeform_window_and_replays_it \
  tests/scripts/test_audit.py::test_fragment_harvest_is_opt_in_and_gate_zero_only \
  tests/scripts/test_audit.py::test_recorded_fragment_certificates_preserve_machine_window_identity \
  tests/scripts/test_audit.py::test_gate_zero_harvest_uses_repeated_disjoint_actual_spans_from_refused_parents \
  tests/scripts/test_audit.py::test_gate_zero_summary_reports_recurrence_controls_and_unweakened_decision \
  tests/scripts/test_audit.py::test_gate_zero_summary_stops_below_ten_percent_without_weakening_thresholds
```

Result: **17 passed**, exit 0.

The broader affected-file check at that stage passed **43 tests**, exit 0.

### Boundary-integrity review correction RED/GREEN

Independent review found four remaining risks in the experimental path: boundary rates could collapse total production/consumption instead of exact residual flow, peer topology could collide across occurrences, sprayed/surplus boundaries were incomplete, and ordinary Freeform imported or allocated measurement machinery. Review also identified the synthetic refused-parent hook test and the raced `best`/`all` strategies as invalid evidence sources.

Focused RED runs reproduced each defect: the new typed observation initially failed import; semantic topology and exact-window seam tests exposed occurrence aliasing; the shared-flow, selected-spray, partial-trace, and lazy-allocation tests failed their new assertions; and the raced-strategy guard initially accepted `best`.

The corrected implementation now:

- derives one exact residual `Fraction` only when cargo domain, direction, and boundary role are unambiguous;
- preserves requested outputs separately from surplus outputs;
- carries exact original peer/logical topology and multiplicity while normalizing only the semantic digest's candidate-local endpoint;
- retains exact selected spray lanes and their attributable proliferator supply, excluding candidates without selected supply topology;
- skips cross-group shapes missing an observed strip instance;
- uses a lazy lightweight sink in already-loaded Freeform code, so ordinary planning neither loads the bench module nor allocates a measurement `ContextVar`;
- rejects fragment harvest for raced `best` and `all` strategies; and
- exercises a real nonempty Freeform planning/preparation/detailed-routing path, forces only the completed parent to refuse, then independently replays and validates the surviving fragment.

The final affected-file command:

```bash
uv run pytest -q tests/bench/test_fragment_harvest.py tests/scripts/test_audit.py
```

Result: **48 passed**, exit 0.

### Concrete-attempt and replay-topology review correction

A second boundary review found that separately prepared packs were accumulated into one evidence set, bidirectional crossings could still be reduced to a signed port, semantic boundary identities retained positional selected/outside endpoint details, and a replay `BuildSpec` could collapse multiple physical peer ports into one aggregate lane.

The attempt-correlation test was run RED before implementation and failed with `AttributeError: 'FragmentHarvestTrace' object has no attribute 'begin_attempt'`.

The corrected measurement path now:

- assigns an occurrence-only typed `FragmentHarvestAttemptId` to each concrete prepared pack and correlates preparation, detailed routing, failures, and topology only within that attempt;
- deduplicates equivalent candidate evidence across alternative attempts, so attempt count cannot inflate candidate, seam, boundary-multiplicity, or replay counts;
- rejects any item observed crossing the selected boundary in both directions because exact edge rates are unavailable, including zero-residual cases;
- canonicalizes semantic boundary peers by recipe identity and machine count while stripping recipe position suffixes, family shards, and machine starts from both selected and outside endpoints; and
- marks every multi-port boundary uncertifiable rather than replaying an aggregate one-lane `BuildSpec`.

Concrete topology remains in occurrence identity and certificate evidence. The semantic digest retains direction, item, cargo domain, boundary role, exact rate, peer recipe/family semantics, and multiplicity. Production planning and request behavior remain unchanged.

The first correction verification passed **49 tests**, exit 0. Follow-up review then found that the digest-scoped enumeration ordinal inside `occurrence_id` could shift when an earlier equal-digest window was absent from one attempt. The regression ran RED with **4 candidates instead of 2**. Occurrence identity now hashes only stable concrete selected group, strip-instance, and internal-edge identity; semantic digest and enumeration position cannot split the same occurrence across attempts. The final focused command `uv run pytest -q tests/bench/test_fragment_harvest.py tests/scripts/test_audit.py` passed **50 tests**, exit 0. Targeted Ruff and MyPy both passed for the three changed source modules and the focused fragment test module.

## Expanded experiment attribution boundary

The separate experimental measurement boundary is a stable compound fragment, not the parent `BuildSpec`:

- a within-group candidate is one selected `StripInstanceId` containing at least two machine instances and typed input/output boundary ports;
- a cross-group candidate contains at least two connected recipe groups and at least one typed internal logical edge;
- single-machine fragments and disconnected single-machine pairs are ineligible;
- planning time is tagged around the exact original-request strip-instance work; shared preparation and routing without exact fragment attribution are excluded rather than estimated;
- candidates intersecting stable `LogicalNetId` or `StripInstanceId` failures are excluded, including candidates harvested from refused parents;
- every positive certificate is independently planned, routed, finalized, and validated under the same strategy and budget;
- certification replay time is recorded separately and never enters the Gate numerator;
- a deterministic maximum actual-time cover prevents overlapping machine instances or logical edges from being counted twice;
- power, outer routing, finalization, validation, and all unattributable work remain outside the numerator.

This remains opt-in measurement instrumentation only. It is not a production cache key, cache lookup, stored artifact, planner specialization, request behavior, or replacement for the authoritative Gate 0 result.

## Authoritative whole-demand cold measurement procedure

After the controller granted a final idle-machine window, five fresh processes were run serially. Every run used:

- commit `747526b6ba582e7f774ca4c333cfb35767698da6`;
- the Cython route backend;
- fixed CPU affinity `0-15`;
- `--jobs 1`, yielding 16 CP-SAT workers per cell;
- vendored assets from the same checkout;
- both explicit strategies;
- a 2-second per-cell budget and 300-second run cap;
- the same interleaved order: Matrix-heavy, Fractionator-heavy, mixed, noncompound, with freeform then sequence-pair inside each workload;
- a fresh Python process and a distinct JSONL file for each run.

Exact command shape, with `N` replaced by 1 through 5:

```bash
taskset -c 0-15 uv run python scripts/audit.py \
  --gate-0 --strategy both --budget 2 --jobs 1 --max-seconds 300 --quiet \
  --json /tmp/compound-cache-gate0-runN.jsonl
```

All five runs completed all 8/8 cells. Each returned exit 1 because the audit was intentionally not clean and/or Gate 0 did not pass: both Fractionator-heavy cells refused in every run. There were no INVALID, CRASH, or NOT RUN cells.

### Persisted source evidence

The five original `/tmp` captures were copied byte-for-byte into the Phase 0 evidence directory. Each file contains every one of its eight source rows. They are the reproducible, authoritative evidence for the original Gate 0 calculation; they do not contain certificates for the separate fragment experiment.

| Run | Repository evidence path | SHA-256 |
|---:|---|---|
| 1 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-cache-gate0-run1.jsonl` | `f9ff0f0a421c0eaa20cf60a73d34906e29656895c1a7c9c6175874b4c1026839` |
| 2 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-cache-gate0-run2.jsonl` | `f4438c57125302a42050ee537edbce54c9f8dc87019bc01c46b17004379e20a8` |
| 3 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-cache-gate0-run3.jsonl` | `5903ea57ba834f0eb305789a9a89140af31e7ae4a65279cda9f461be86aa5c68` |
| 4 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-cache-gate0-run4.jsonl` | `8334d6c8756205da4e3735529905e692936c72d16d3741125f53d3c445430400` |
| 5 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-cache-gate0-run5.jsonl` | `e0eed0091a55c14ff266956cc5f666b2012fef2485dff3c5cfd500fc7a794cfa` |

The persisted and `/tmp` checksums matched for all five files after the copy.


## Authoritative whole-demand cold measurement results

The denominator was wall time for eligible demands whose exact fingerprint recurred. Its numerator attributed whole-demand local planning plus internal routing under the approved Phase 0 boundary.

| Run | Repeated fingerprints | Repeated-demand wall | Eligible local work | Share | Decision | Cell status |
|---:|---:|---:|---:|---:|---|---|
| 1 | 3 | 6.556632 s | 0.692682 s | 10.5646% | HOLD | 6 CLEAN, 2 REFUSED |
| 2 | 3 | 5.919736 s | 0.477034 s | 8.0584% | STOP | 6 CLEAN, 2 REFUSED |
| 3 | 3 | 6.082158 s | 0.540855 s | 8.8925% | STOP | 6 CLEAN, 2 REFUSED |
| 4 | 3 | 5.750876 s | 0.459043 s | 7.9821% | STOP | 6 CLEAN, 2 REFUSED |
| 5 | 3 | 5.707362 s | 0.455830 s | 7.9867% | STOP | 6 CLEAN, 2 REFUSED |
| **Aggregate** | **3/run** | **30.016765 s** | **2.625445 s** | **8.7466%** | **STOP** | **30 CLEAN, 10 REFUSED** |

Share range across cold runs: **7.9821% minimum, 8.0584% median, 10.5646% maximum**.

### Workload evidence across all five runs

| Workload | Stable fingerprint prefix | Rows | Status | Eligible work | Stable `(freeform, sequence-pair)` areas |
|---|---|---:|---|---:|---|
| Matrix-heavy | `b638b99b3199` | 10 | 10 CLEAN | 1.279930 s | `(306, 448)` |
| Fractionator-heavy | `1bf92f4fde8d` | 10 | 10 REFUSED | 0.000000 s | no placement |
| Mixed | `be61e23a3db3` | 10 | 10 CLEAN | 1.345515 s | `(702, 704)` |
| Noncompound | `66dd541a806e` | 10 | 10 CLEAN | 0.000000 s | `(63, 63)` |

The noncompound controls reported zero eligible local planning time, zero eligible internal routing time, and zero eligible internal routing expansions in all ten rows. Local and final areas were equal and stable for every successful control row.

## Authoritative Gate calculation

- Exact recurring eligible fingerprints required: at least 2. The calculation observed 3 per run.
- PASS threshold: eligible repeated work at least 20% of repeated-demand wall. The calculation observed 8.7466%.
- STOP threshold: below 10%. The calculation fell below 10%.

This is the binding Phase 0 outcome. The separate expanded experiment has no authority to supersede or reopen it.

## Separate expanded fragment capture procedure and result

After the final behavior-preserving Freeform baseline repair was merged, the cache branch was rebased onto `faed2852850c3b8775ec537e5cb520c8e4b87d95`. The post-rebase focused suite passed 50 tests, and targeted Ruff and MyPy passed. The controller then granted the capture window.

Five fresh audit processes were run serially. Each used capture commit `f1f68e2bdf353797757ea95fe4a5edbd00d92dc7`, the Cython route backend, CPU affinity 0–15, one worker, the unchanged interleaved eight-cell corpus and strategy order, the unchanged 2-second budget and 300-second cap, and only the explicit fragment opt-in:

```bash
taskset -c 0-15 uv run python scripts/audit.py \
  --gate-0 --fragment-harvest --strategy both --budget 2 --jobs 1 \
  --max-seconds 300 --quiet \
  --json /tmp/compound-fragment-gate0-runN.jsonl
```

Every capture produced all eight rows: six CLEAN and two REFUSED per run. The ten Fractionator-heavy cells were the only refusals. Each repository evidence file is byte-identical to its `/tmp` source.

| Run | Repository evidence path | SHA-256 |
|---:|---|---|
| 1 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-fragment-gate0-run1.jsonl` | `14814bd2ecf782b8c7844d77deb676875d14fd2ed116b4cd3477165054f2951a` |
| 2 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-fragment-gate0-run2.jsonl` | `78ff3c0b0541699f6cdb556b854915e6b42077a1159937c9791dce1e8924192b` |
| 3 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-fragment-gate0-run3.jsonl` | `25acacf7cbd5917864eaa686305d96379592944ecfaf7a7048e450252e08494d` |
| 4 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-fragment-gate0-run4.jsonl` | `5dab62a197b9c39d0a7cfc92045cadd3f743266bfba656964845b71205a9ae7e` |
| 5 | `.superpowers/sdd/2026-09-05-background-compound-block-cache/evidence/compound-fragment-gate0-run5.jsonl` | `0217462e79c9c0a96c3aad7d85afb98c5361d4d3a57d139757fb6d22690a2b6b` |

### Expanded fragment gate calculation

The denominator remains repeated eligible parent-demand wall time. The numerator is only the deterministic disjoint cover of independently replayed, routed, finalized, and validated recurring fragment certificates; shadow replay time is excluded.

| Run | Recurring fragment fingerprints | Repeated-demand wall | Disjoint eligible work | Share | Selected fragments | Overlap deducted | Decision |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | 6.664415340 s | 0.000158189 s | 0.002374% | 8 | 0.000000000 s | STOP |
| 2 | 1 | 6.515074327 s | 0.000184255 s | 0.002828% | 8 | 0.000000000 s | STOP |
| 3 | 1 | 6.414442875 s | 0.000150109 s | 0.002340% | 8 | 0.000000000 s | STOP |
| 4 | 1 | 6.243991519 s | 0.000159639 s | 0.002557% | 8 | 0.000000000 s | STOP |
| 5 | 1 | 6.260756843 s | 0.000161319 s | 0.002577% | 8 | 0.000000000 s | STOP |
| **Aggregate** | **1** | **32.098680905 s** | **0.000813511 s** | **0.002534%** | **40** | **0.000000000 s** | **STOP** |

The expanded experiment fails both predeclared promotion requirements: only one exact recurring semantic fragment fingerprint was observed, below the required two, and disjoint attributable work was 0.002534% of repeated-demand wall, far below the 20% pass threshold and the 10% stop threshold. The result is STOP without reinterpretation.

### Expanded workload and seam evidence

| Workload | Rows/status | Candidates | Certificates | Selected | Disjoint actual work | Replay rejections | Exclusions | Refused-parent certificates |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Matrix-heavy | 10 CLEAN | 20 | 20 | 20 | 0.000460024 s | 0 | 0 | 0 |
| Fractionator-heavy | 10 REFUSED | 0 | 0 | 0 | 0.000000000 s | 0 | 0 | 0 |
| Mixed | 10 CLEAN | 30 | 20 | 20 | 0.000353487 s | 10 | 0 | 0 |
| Noncompound | 10 CLEAN | 0 | 0 | 0 | 0.000000000 s | 0 | 0 | 0 |

All four exact evidence seams recorded 50 candidate occurrences across the five runs: input planning, selected strip, prepared routing, and detailed routed. Forty certificates survived shadow replay and all forty shared one semantic fragment digest; the ten additional mixed candidates were rejected by replay. The disjoint selector chose all forty surviving certificates and deducted no overlap. No positive fragment was recovered from a refused Fractionator-heavy parent.

This expanded result is separate and non-gating. It reinforces but does not supersede or reopen the authoritative whole-demand Gate 0 STOP at 8.7466%.

## Compatibility notes

- Candidate enumeration, attempt order, selected strategy, and selected placement identity remain unchanged; the opt-in trace test compares selected strips with and without capture.
- Ordinary audit jobs do not construct semantic fingerprints or allocate `CacheEligibilityMetrics`, their JSON rows omit `semantic_demand`, `cache_eligibility`, and `fragment_harvest`, and `--fragment-harvest` is rejected without `--gate-0`.
- No fingerprint or certificate is consulted during production planning, routing, validation, or result selection.
- No negative-cache behavior was introduced. Refused parents can contribute only independently replayed, validated positive fragments that do not intersect typed failures.
- Measurement JSON preserves stable machine-window identities, exact attributed spans, excluded work, replay cost, and overlap deductions separately.

## Concerns

1. The authoritative Phase 0 evidence is complete and Gate 0 remains STOP. The separate expanded fragment experiment also returns STOP: one recurring fingerprint and 0.002534% eligible share.
2. All ten Fractionator-heavy parents refused under the unchanged 2-second budget, but none produced a certifiable intermediate window. The expanded path therefore supplies no evidence that cacheable positive work survives those refusals.
3. All forty positive certificates shared one semantic fragment digest across Matrix-heavy and mixed workloads, so the required recurrence diversity was not present.
4. The fingerprint and workload classification remain provisional measurement constructs, not a production cache-key contract.
5. The experimental numerator is intentionally conservative: only exact original strip-instance planning spans are counted; shared preparation, unattributable routing, and all shadow replay time are excluded. The measured spans are tiny enough to be noisy, but they are orders of magnitude below the fixed stop threshold and cannot alter the decision.
