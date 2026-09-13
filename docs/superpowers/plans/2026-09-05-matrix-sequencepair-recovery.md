# Matrix SequencePair Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify the exact recurring Universe Matrix stranded-net signatures, implement one bounded topology-preserving candidate substitution, and promote it only if unchanged-budget correctness and area gates pass.

**Architecture:** Extend experiment-only exact-closure telemetry with stable semantic failure identities, then derive at most three recovery candidates from existing legal Matrix variants and sequence relations. Recovery candidates replace lower-value scheduled work; normal detailed routing, finalization, projection, and validation remain authoritative.

**Tech Stack:** Python 3.14, frozen dataclasses, exact `Fraction`, existing SequencePair/ALNS/route-feedback types, pytest, audit JSONL, Ruff, strict MyPy, `uv run`.

**Spec:** `docs/superpowers/specs/2026-09-05-matrix-sequencepair-recovery-design.md`

## Global Constraints

- Keep the 30-second cell budget, expansion allowance, and scheduled candidate count unchanged.
- Preserve physical Lab counts, exact rates, legal column/support topology, root-only sorters, flank-output rows, cargo domains, belt tiers/stacks, and projection rules.
- Use stable `StripInstanceId`, `StripVariantId`, and logical net identities; never persist or compare stage-local ordinals as semantic identity.
- Diagnostic identity construction/serialization occurs only when exact-closure diagnostic telemetry is enabled.
- Forced compact/unstacked modes remain experiment-only and never become production defaults.
- Do not weaken validation, catch/suppress exceptions, add retries, enlarge beams, or extend deadlines.
- Every production change follows RED/GREEN TDD.

---

### Task 1: Stable stranded-net diagnostic evidence

**Files:**
- Modify: `src/flab2bp/layout/sequence_solver.py`
- Modify: `src/flab2bp/layout/route_feedback.py` only if an existing typed semantic failure record cannot represent the evidence
- Modify: `scripts/audit.py`
- Test: `tests/layout/test_sequence_solver.py`
- Test: `tests/scripts/test_audit.py`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/round1.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/round2.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/round3.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/diagnosis.json`

**Interfaces:**
- Consumes: existing `ExactClosureObservation`, `DetailedRouteResult`, `NetFailure`, `PlacementProblem.instance_ids`, selected variant identities, and audit JSON serialization.
- Produces: an immutable semantic failure signature carried only by exact-closure telemetry and serialized by audit without changing default result schemas.

- [ ] **Step 1: Write failing tests for stable semantic diagnostics**

Add focused tests that construct the same failing logical net before and after an unrelated strip split. Assert the diagnostic signature preserves endpoint `StripInstanceId` family identity, selected `StripVariantId`, item/role, and failure kind while stage-local indices change. Add a default-mode test asserting the diagnostic field is `None` and the semantic-signature builder is not called.

- [ ] **Step 2: Run the new tests and verify RED**

Run only the new `tests/layout/test_sequence_solver.py` nodes. Expected: failure because exact-closure observations do not yet expose the stable stranded-net signature.

- [ ] **Step 3: Add the minimum immutable diagnostic record**

Reuse existing route-feedback types where they already carry semantic information. Add only the missing stable endpoint/variant identity and timing fields to exact-closure telemetry. Derive current indices from `PlacementProblem.instance_ids`; never store an index as identity. Guard all construction behind `exact_closure_telemetry`.

- [ ] **Step 4: Add audit serialization tests and implementation**

Add one audit test for exact JSON structure and one default test proving ordinary audit cells do not emit or compute diagnostic records. Use sorted deterministic tuples/lists and existing typed serialization conventions.

- [ ] **Step 5: Run focused tests GREEN**

Run the new layout and audit test nodes plus existing forced-topology telemetry tests. Expected: all pass.

- [ ] **Step 6: Capture three diagnostic rounds**

Run SequencePair only, `--only universe-matrix`, budget 30, one job, all three candidate policies, three times. Pin identical CPU affinity/workers/assets and run no unrelated performance work concurrently. Store the JSONL files named above.

- [ ] **Step 7: Write `diagnosis.json`**

For each policy and round, record status, area, elapsed time, best stranded count, semantic failure signatures, selected Matrix variant identities, pre-route dimensions, and whether an equivalent signature succeeds in another policy. Conclude exactly one of: candidate never generated; generated after practical admission; generated and detailed routing rejected it. Include source commit and exact commands.

- [ ] **Step 8: Commit**

Commit source/tests separately from captured evidence. Do not begin Task 2 until the diagnosis names the repeated semantic signatures and their admission failure.

---

### Task 2: Bounded topology-preserving substitution

**Files:**
- Modify: `src/flab2bp/layout/sequence_solver.py`
- Modify: `src/flab2bp/layout/strip_variants.py` only if Task 1 proves an existing legal variant is generated but not exposed to the sequence solver
- Test: `tests/layout/test_sequence_solver.py`
- Test: `tests/layout/test_strip_variants.py` only when `strip_variants.py` changes

**Interfaces:**
- Consumes: Task 1’s stable recurring signatures and existing legal `PlacementProblem.variant_tables`/sequence states.
- Produces: a deterministic tuple of zero to three deduplicated recovery states that replaces equal-cardinality lower-value scheduled work.

- [ ] **Step 1: Select the smallest diagnosis-backed seam**

If the useful variant is absent, expose that already-legal variant in the affected family table. If it exists but is not scheduled, derive a recovery state from the existing table. If it is scheduled but routed too late, substitute its state earlier. Record the chosen branch and evidence in the task report; do not implement the other branches.

- [ ] **Step 2: Write failing unit tests**

Use the exact semantic signatures and selected identities from `diagnosis.json`. Assert zero candidates for unrelated specs/signatures, at most three for a matching signature, deterministic ordering, decoded-state deduplication, unchanged total scheduled count, and preservation of every physical/rate/column/support field.

- [ ] **Step 3: Run tests and verify RED**

Run only the new recovery tests. Expected: failure because the diagnosis-backed candidate is not substituted.

- [ ] **Step 4: Implement the minimum candidate substitution**

Place the substitution at the existing restart/variant scheduling boundary identified by Task 1. Reuse existing state mutation and validation functions. Do not add a parallel queue, scorer, router, or retry loop.

- [ ] **Step 5: Run focused tests GREEN**

Run the new recovery tests plus all Matrix variant, exact-closure telemetry, stage-boundary split/merge, and default-mode tests. Expected: all pass with unchanged nonmatching behavior.

- [ ] **Step 6: Commit**

Commit the single recovery mechanism and its tests atomically.

---

### Task 3: Focused three-round decision gate

**Files:**
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/gate-round1.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/gate-round2.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/gate-round3.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/gate-report.md`

**Interfaces:**
- Consumes: Task 2 implementation and unchanged audit runner.
- Produces: a PASS/STOP decision under the spec’s focused gate.

- [ ] **Step 1: Run three isolated focused rounds**

Run `universe-matrix`, SequencePair, all three policies, budget 30, identical affinity/workers/assets/load, with no concurrent tests or builds.

- [ ] **Step 2: Verify correctness and area**

Require nine of nine clean cells, zero invalid/crash, unchanged physical counts/rates, and `no-proliferator` median area at most `17,654`.

- [ ] **Step 3: Write the decision report**

Report every round separately, exact areas/times, candidate use, failure signatures, and regressions. Never average away a failed cell.

- [ ] **Step 4: Commit evidence**

If the gate fails, commit the STOP evidence and make no production-default change. If it passes, proceed to Task 4.

---

### Task 4: Full promotion verification

**Files:**
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/promotion-round1.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/promotion-round2.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-matrix-recovery/promotion-round3.jsonl`
- Modify: `docs/superpowers/evidence/2026-09-05-matrix-recovery/gate-report.md`

**Interfaces:**
- Consumes: a passing focused gate.
- Produces: an evidence-backed promotion decision.

- [ ] **Step 1: Run the 18-cell Matrix gate three times**

Use the established graphene/information-matrix/universe-matrix corpus, both production strategies, all three policies, budget 30, identical affinity/workers/assets/load.

- [ ] **Step 2: Apply the promotion criteria**

Require `18/18` clean each round, zero invalid/crash/not-run, no loss of any baseline-clean cell, and no material p95 runtime regression.

- [ ] **Step 3: Run repository verification**

Run full Python pytest, Ruff, strict MyPy, frontend tests/typecheck/lint, and production build. Run two independent reviews of the complete branch.

- [ ] **Step 4: Commit the final report**

Record PASS or STOP. A STOP leaves the feature branch unmerged and retains all diagnostic evidence.
