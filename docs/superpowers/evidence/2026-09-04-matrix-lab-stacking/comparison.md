# Matrix Lab Stacking Evidence

## Baseline capture

- Commit: `49824941cb2feb3e77761c83207064b2a41c6cde`
- Branch: `matrix-lab-stacking` before production stacking changes
- CPU affinity: 128 logical CPUs, IDs 0–127
- Route backend: `cython` in every JSONL row
- Solver workload: 18 cells, 3 concurrent audit jobs, 42 CP-SAT workers per job, 30-second per-cell budget, 180-second admission cap
- Preflight load: `3.08 3.26 3.85`; sampled CPU idle was 93%, 98%, 98%
- Result: 18/18 CLEAN; zero refusals, invalid results, crashes, or not-run cells; 126 seconds wall
- Completion tail: one cell completed after its requested search deadline; maximum tail 0.1 seconds
- Evidence schema: all 18 rows contain `belt_tiles`, `physical_machine_count`, `matrix_lab_count`, `ground_matrix_lab_columns`, `max_lab_stack_height`, and `lab_stack_limit`
- Unstacked control: every emitted Matrix Lab was at stack height 1; `graphene` rows contained zero Matrix Labs

Command:

```bash
uv run python scripts/audit.py --budget 30 --jobs 3 --max-seconds 180 \
  --only graphene,information-matrix,universe-matrix \
  --json docs/superpowers/evidence/2026-09-04-matrix-lab-stacking/baseline.jsonl
```

## Comparison gate

The stacked capture uses the same commit lineage, URL corpus, candidate policies, strategies, power setting, band policy, budgets, worker count, concurrency, backend, and evidence schema. It must preserve every CLEAN baseline cell, keep INVALID and CRASH at zero, preserve physical machine counts, obey each URL's lab-stack limit, avoid any comparable area regression, and strictly reduce at least one matrix-heavy area. The `graphene` control must remain structurally unchanged and contain no Matrix Labs.

## Stacked capture

- Commit: `dbcacaa87063b1f58f0fd08f140a275bfac413e9`
- CPU affinity: 128 logical CPUs, IDs 0–127
- Route backend: `cython` in every row
- Workload: the exact baseline command and 18-cell matrix, repeated three times because the first run contained an area regression
- Preflight load averages: `10.53 8.18 7.91`, `6.44 7.89 7.88`, and `7.19 7.40 7.69`; sampled CPU idle remained 89–97%
- Wall times: 126, 125, and 130 seconds
- Evidence schema: all 54 rows contain every required geometry field
- Result in every round: 16 CLEAN, 2 REFUSED, 0 INVALID, 0 CRASH, 0 NOT RUN

Artifacts:

- `stacked.jsonl`
- `stacked-round2.jsonl`
- `stacked-round3.jsonl`

## Three-round median comparison

Geometry values are medians of the three CLEAN results. A refused row has no
emitted geometry, but still records the URL-derived stack limit of 9.

| URL | Policy | Strategy | 3-round status | Baseline area | Median area | Δ | Belts | Physical machines | Labs | Ground columns | Max stack | Limit | Median seconds |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| graphene | all-products | freeform | CLEAN ×3 | 504 | 504 | 0 | 196 | 4 | 0 | 0 | 0 | 9 | 2.43 |
| graphene | all-products | sequence-pair | CLEAN ×3 | 518 | 518 | 0 | 198 | 4 | 0 | 0 | 0 | 9 | 28.73 |
| graphene | no-proliferator | freeform | CLEAN ×3 | 363 | 363 | 0 | 70 | 5 | 0 | 0 | 0 | 9 | 0.77 |
| graphene | no-proliferator | sequence-pair | CLEAN ×3 | 363 | 363 | 0 | 69 | 5 | 0 | 0 | 0 | 9 | 21.54 |
| graphene | output-products | freeform | CLEAN ×3 | 420 | 420 | 0 | 128 | 5 | 0 | 0 | 0 | 9 | 1.93 |
| graphene | output-products | sequence-pair | CLEAN ×3 | 420 | 420 | 0 | 128 | 5 | 0 | 0 | 0 | 9 | 2.29 |
| information-matrix | all-products | freeform | CLEAN ×3 | 4760 | 3900 | -860 | 2255 | 56 | 8 | 1 | 8 | 9 | 27.12 |
| information-matrix | all-products | sequence-pair | CLEAN ×3 | 4453 | 3009 | -1444 | 2200 | 56 | 8 | 1 | 8 | 9 | 24.02 |
| information-matrix | no-proliferator | freeform | CLEAN ×3 | 7917 | 7008 | -909 | 2682 | 97 | 10 | 2 | 5 | 9 | 12.88 |
| information-matrix | no-proliferator | sequence-pair | CLEAN ×3 | 8030 | 4144 | -3886 | 2468 | 97 | 10 | 2 | 5 | 9 | 11.30 |
| information-matrix | output-products | freeform | CLEAN ×3 | 5856 | 5104 | -752 | 2307 | 84 | 8 | 1 | 8 | 9 | 17.74 |
| information-matrix | output-products | sequence-pair | CLEAN ×3 | 5394 | 4590 | -804 | 2238 | 84 | 8 | 1 | 8 | 9 | 25.01 |
| universe-matrix | all-products | freeform | CLEAN ×3 | 29744 | 20306 | -9438 | 12175 | 113 | 39 | 7 | 8 | 9 | 26.24 |
| universe-matrix | all-products | sequence-pair | REFUSED ×3 | 28314 | 0 | — | 0 | 0 | 0 | 0 | 0 | 9 | 22.75 |
| universe-matrix | no-proliferator | freeform | CLEAN ×3 | 31898 | 21450 | -10448 | 11838 | 224 | 54 | 9 | 8 | 9 | 27.84 |
| universe-matrix | no-proliferator | sequence-pair | CLEAN ×3 | 17654 | 18096 | +442 | 7537 | 224 | 54 | 9 | 8 | 9 | 25.43 |
| universe-matrix | output-products | freeform | CLEAN ×3 | 22464 | 17290 | -5174 | 8587 | 193 | 45 | 8 | 8 | 9 | 30.23 |
| universe-matrix | output-products | sequence-pair | REFUSED ×3 | 20485 | 0 | — | 0 | 0 | 0 | 0 | 0 | 9 | 23.08 |

## Gate decision

**STOP — the branch is not eligible for production integration.**

The feature delivers large, repeatable footprint gains and all returned builds
remain validator-clean with exact physical machine counts. The non-lab graphene
control is structurally unchanged. It nevertheless fails two non-negotiable
conditions:

1. SequencePair refuses the universe-matrix `all-products` and
   `output-products` cells in all three rounds. Both were CLEAN at baseline.
2. The SequencePair universe-matrix `no-proliferator` median area is 18,096,
   442 tiles larger than its 17,654 baseline.

Both refusals are honest deadline exhaustion with no emitted invalid blueprint.
Their best detailed-route attempts consistently strand two and one nets,
respectively. The safe result is to retain this work on its feature branch and
not merge Matrix Lab stacking to `master` until SequencePair recovers these
cells under the unchanged gate.

## Exact pipeline smoke

The production-equivalent `build(..., strategy="best", race=True, workers=16)`
path on the `information-matrix` corpus URL returned:

- candidate `all-products`, strategy `sequence-pair`;
- validator report OK;
- area 3,009 and 2,200 belt tiles;
- 56 physical machines;
- eight Matrix Labs in one eight-high column, within the URL limit of nine;
- 2,472 decoded buildings, exactly matching the placement record count.

This proves the successful stacked path end to end, including candidate/strategy
racing, finalization, certification, blueprint encoding, and decoding. It does
not override the failed 18-cell promotion gate above.
