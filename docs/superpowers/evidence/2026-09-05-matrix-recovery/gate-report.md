# Matrix recovery focused gate report

## Decision

**STOP — Matrix recovery is rejected on repaired master and must not merge.**

Round 1 produced two refusals, so the predeclared requirement of nine clean cells across three rounds became mathematically impossible. Per the stop instruction, rounds 2 and 3 and the 18-cell promotion gate were not run.

## Environment

- Recovery source commit after rebase onto repaired master `977fa8c`: `2b04c532c3228ff3160f14160890ea6ebbdaaa72`
- CPU affinity: `0-127`
- Audit jobs: `1`
- Strategy: `sequence-pair`
- URL: `universe-matrix`
- Tier: `stress`
- Per-cell budget: `30` seconds
- Per-round cap: `180` seconds
- Candidate policies, in order: `no-proliferator,all-products,output-products`
- `PYTHONHASHSEED=0`
- Vendored lab data loaded by the production audit path
- `uv.lock` SHA-256: `41eaa7bb8cdd8b9368cc21461c061070c50fd618575b276e12309281f1ff922f`
- No other test, build, audit, or performance work ran concurrently with the gate round.

## Preflight verification

Tracked worktree state was clean before verification and the gate.

Targeted static command:

```text
env -u VIRTUAL_ENV uv run ruff check src/flab2bp/layout/sequence_solver.py tests/layout/test_sequence_solver.py tests/layout/test_strip_variants.py
```

Result: `All checks passed!`, exit `0`.

Focused behavioral command:

```text
env -u VIRTUAL_ENV uv run pytest -q \
 tests/layout/test_sequence_solver.py::test_unrelated_specs_and_semantic_failures_yield_no_matrix_recovery_states \
 tests/layout/test_sequence_solver.py::test_matrix_recovery_admission_compares_complete_canonical_evidence \
 tests/layout/test_sequence_solver.py::test_diagnosed_matrix_recovery_states_are_bounded_ordered_and_decoded_deduplicated \
 tests/layout/test_sequence_solver.py::test_matrix_recovery_skips_archive_states_retained_by_the_default_schedule \
 tests/layout/test_sequence_solver.py::test_matrix_recovery_displaces_multiple_lowest_scheduler_priority_slots \
 tests/layout/test_sequence_solver.py::test_matrix_recovery_preserves_promoted_feedback_and_quality_work \
 tests/layout/test_sequence_solver.py::test_matrix_recovery_reuses_exact_variants_without_changing_physical_fields \
 tests/layout/test_sequence_solver.py::test_default_sequence_search_does_not_construct_recovery_identity \
 tests/layout/test_sequence_solver.py::test_matrix_recovery_uses_only_the_existing_exact_closure_experiment_seam \
 tests/layout/test_sequence_solver.py::test_exact_decoded_closure_retains_coordinates_and_typed_telemetry \
 tests/layout/test_sequence_solver.py::test_default_sequence_closure_skips_exact_topology_telemetry \
 tests/layout/test_sequence_solver.py::test_exact_closure_telemetry_uses_the_prepared_routed_geometry \
 tests/layout/test_sequence_solver.py::test_exact_failure_signature_survives_an_unrelated_strip_split \
 tests/layout/test_sequence_solver.py::test_unforced_exact_telemetry_derives_matrix_families \
 tests/layout/test_sequence_solver.py::test_exact_closure_serializes_selected_matrix_variant_ids \
 tests/layout/test_sequence_solver.py::test_forced_lab_topology_filters_every_stackable_table_before_annealing \
 tests/layout/test_sequence_solver.py::test_unforced_lab_topology_preserves_inputs_without_variant_tables \
 tests/layout/test_sequence_solver.py::test_lab_telemetry_rederives_indices_after_an_unrelated_split \
 tests/layout/test_sequence_solver.py::test_forced_lab_families_are_closed_against_split_and_merge \
 tests/layout/test_sequence_solver.py::test_production_stage_boundary_rebuilds_preparation_for_children \
 tests/layout/test_sequence_solver.py::test_feedback_stagnation_rebuilds_the_next_fixed_cardinality_stage \
 tests/layout/test_sequence_solver.py::test_production_boundary_does_not_merge_incompatible_or_implicated_children \
 tests/layout/test_sequence_solver.py::test_forced_lab_topology_rejects_branches_that_do_not_propagate_it \
 tests/layout/test_sequence_solver.py::test_serial_layout_uses_a_budgeted_root_compact_seed
```

Result: `33 passed`, exit `0`.

## Focused gate command

```text
taskset -c 0-127 env -u VIRTUAL_ENV PYTHONHASHSEED=0 uv run python scripts/audit.py --strategy sequence-pair --only universe-matrix --tier stress --budget 30 --jobs 1 --max-seconds 180 --candidate-policy no-proliferator,all-products,output-products --exact-closure-diagnostics 1 --json docs/superpowers/evidence/2026-09-05-matrix-recovery/gate-round1.jsonl
```

The context-mode transport timed out after 30 seconds while the unchanged audit subprocess continued. The subprocess completed and wrote all three policy rows. Its shell exit status was therefore not captured by the transport; the two `REFUSED` rows make the audit result non-successful under the audit command's documented refusal semantics.

Evidence: `gate-round1.jsonl`, SHA-256 `203d0f7a59ac71f0b9d1b95db57f8089282d14ab8306a77dc3cc64066acf02f5`.

## Round 1 results

| Policy | Status | Area | Elapsed (s) | Best stranded | Detail |
|---|---|---:|---:|---:|---|
| no-proliferator | REFUSED | 0 | 25.0452741230838 | 1 | deadline exhausted before finding an exact layout |
| all-products | REFUSED | 0 | 22.31576197897084 | 3 | deadline exhausted before finding an exact layout |
| output-products | CLEAN | 14784 | 25.085923589067534 | 0 | — |

The clean output-products control emitted 193 physical machines, including 45 Matrix labs in 8 ground columns with maximum stack height 8 under the unchanged stack limit 9. It recorded 78 projection candidates. Refused rows emitted no build, so physical-count/rate validation is not applicable to those rows.

## Gate evaluation

- Required: nine of nine clean cells. Observed before early stop: one of three clean; two refusals. **FAIL.**
- Required: zero invalid/crash. Observed: zero invalid/crash, but two refusals. This does not rescue the cleanliness failure.
- Required: unchanged physical counts and rates. The clean control retained a valid physical build; refused cells have no build to validate. **Gate cannot pass.**
- Required: no-proliferator median area at most `17,654`. No clean no-proliferator area exists in round 1. **Criterion cannot be satisfied.**

Rounds 2 and 3 cannot change the fact that nine-of-nine cleanliness is impossible. The recovery remains diagnostic-only branch work and must not be promoted or merged.
