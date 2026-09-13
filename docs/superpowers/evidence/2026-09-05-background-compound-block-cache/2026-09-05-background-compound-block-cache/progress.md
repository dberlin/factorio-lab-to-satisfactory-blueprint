# SDD ledger — plan: docs/superpowers/plans/2026-09-05-background-compound-block-cache.md

Authority: `/home/dannyb/report.md` is the approved assessment; the checked-in plan is the executable authority. No separate spec file exists.

## Preflight interface scan

| Work items | Producer → consumer contract | Finding / ruling |
|---|---|---|
| Phase 0 self-check | measurement fields, semantic demand fingerprint, interleaved cold evidence | Internally consistent. Instrumentation only; no cache, candidate-order, or request-behavior change. |
| Phase 1 self-check | immutable local records and typed boundary ports | Internally consistent. Translation only initially; whole-placement validation remains authoritative. |
| Phase 2 self-check | `ForegroundGate`, bounded queue, supervised child lifecycle | Internally consistent. HTTP submission remains immediate while CPU solve admission waits for child exit. |
| Phase 3 self-check | successful immutable `Build` reference → `OpportunitySink` | Internally consistent. Request thread performs no traversal, canonicalization, mining, or storage. |
| Phase 4 self-check | successful build evidence → canonical exact `CompoundRequest` | Internally consistent. Equality must survive hash seed and insertion order. |
| Phase 5 self-check | canonical request and fragment → bounded in-memory frontier and shadow replay | Internally consistent. No user-solve injection. |
| Phase 6 self-check | validated frontier → atomic content-addressed positive store | Internally consistent. Corruption/staleness is a miss. |
| Phase 7 self-check | bounded read-only source → outer candidates | Internally consistent. Existing candidates remain and final validation is unchanged. |
| Phase 8 self-check | specialized/generic planners → shared immutable fragments | Conditional on each planner’s independent gates; Matrix deadline evidence cannot promote or reject a fragment. |
| Phase 9 self-check | cold/warm arms → production enablement decision | Internally consistent. Correctness and density gates dominate speed. |
| Phase 10 self-check | exhaustive fact → separately flagged negative artifact | Internally consistent. Ordinary refusal, timeout, UNKNOWN, and BUDGET are forbidden inputs. |
| Phase 11 self-check | validated feature → observable production rollout | Internally consistent. Maintenance remains outside request progress/deadline. |
| Phase 0 → Phase 4 | provisional semantic fingerprint → production canonical request | No conflict: Phase 0 explicitly treats its fingerprint as measurement-only; Phase 4 owns the production schema. |
| Phase 1 → Phases 5–8 | sealed fragment types → storage, replay, lookup, planners | No conflict: later phases consume the same data-only boundary and may not add callbacks or routing behavior to it. |
| Phase 2 → Phases 3–6, 11 | idle gate/worker lifecycle → opportunity handoff, child work, persistence, telemetry | No conflict: background generation/persistence is always subordinate to foreground demand. |
| Phase 3 → Phase 4 | immutable build reference → background decomposition | No conflict: object handoff is O(1); all traversal begins behind the coordinator/child boundary. |
| Phase 4 → Phases 5–7, 10 | exact request identity → positive/negative namespaces and lookup | No conflict: negatives remain a distinct later schema/feature flag. |
| Phase 5 → Phase 6 | in-memory validated frontier → persistent envelope | No conflict: persistence adds no selection authority and remains shadow-only. |
| Phase 6 → Phase 7 | persistent shadow reads → bounded solve-path reads | No conflict: only positive reads graduate, and every failure is a miss. |
| Phase 7 → Phase 9 | cache-hit candidates → paired bake-off | No conflict: from-scratch candidates remain until evidence supports promotion. |
| Phase 8 → Phase 9 | specialized/generic candidate sources → measured arms | No conflict: Fractionator may graduate before Matrix; no planner is promoted merely because it exists. |
| Phase 9 → Phases 10–11 | positive evidence → negatives/rollout | No conflict: positive rollout does not imply negative rollout. |

Ruling: Execute the plan gate-by-gate, beginning with Phase 0 only. A failed gate stops dependent phases rather than weakening its threshold. Cost if wrong: a conservative stop may defer a useful cache, but cannot regress foreground correctness.

Ruling: Phase 5’s reference to intentional specialized output is conditional; absent a production planner, use fixtures/generic opportunities until Phase 8. Cost if wrong: specialized speed evidence arrives later, avoiding fabricated planner output.

## Execution

Phase 0: complete — Gate 0 STOP

Baseline at `a1afec5`: failed (19 failures). Captured at `baseline-pytest.out`; failures are 16 Freeform test-helper contract mismatches, one strategy-race failure, and two stale refusal payload expectations.

Ruling: Repair the already-proven master baseline drift as a prerequisite before Phase 0 rather than attributing those failures to cache work. Reuse reviewed commit `7a81d57` only as reference and exclude Matrix-specific changes. Cost if wrong: this adds a narrow prerequisite commit to the cache branch; leaving the baseline red would make every Phase 0 regression ambiguous.

Baseline repair: in progress

Baseline repair: complete (commits `32b4052..50e16cc`, review clean).

Repaired baseline verification: `uv run pytest -q` exited 0 at `50e16cc`.

Phase 0 instrumentation and initial report: commits `747526b..3d7af73`.

Independent review correction: ordinary audit jobs no longer construct or serialize Gate 0 fingerprints/metrics; only `gate0_control` jobs allocate and emit those fields. RED was 1 failed/1 passed; GREEN was 2 passed. Required focused `tests/test_pipeline.py tests/scripts/test_audit.py` verification passed 100% in 83.46 s.

Static-check cleanup: the rejected-demand attribution node passed, and targeted Ruff passed for `metrics.py`, `sequence_solver.py`, and `test_audit.py`. The optional Gate 0 metric is explicitly narrowed before field access.

Cold evidence: five original eight-row JSONL captures are persisted under `evidence/compound-cache-gate0-run1.jsonl` through `run5.jsonl`; persisted SHA-256 checksums match the `/tmp` sources.

Gate 0 result: three eligible exact fingerprints recurred per run, but aggregate repeated eligible work was `2.625445 / 30.016765 = 8.7466%`. This is below the fixed 10% stop threshold.

Ruling: stop after Phase 0. No dependent persistent-cache phase is authorized. Cost if wrong: the conservative stop defers cache work rather than weakening a pre-approved evidence gate.

Expanded fragment-harvest experiment: complete, separate, opt-in, and non-gating. It does not supersede or reopen the authoritative 8.7466% Gate 0 STOP. Boundary-integrity review corrections are implemented with exact residual flow, cargo domain/role, stable peer topology and multiplicity, exact selected spray supply, surplus preservation, lazy disabled instrumentation, explicit-strategy-only capture, real Freeform refused-parent replay coverage, concrete-attempt correlation, positional semantic normalization, conservative multi-port replay exclusion, and attempt-invariant concrete occurrence identity. Post-rebase focused verification is 50 passed; targeted Ruff and MyPy pass for the changed modules. Five eight-row cold captures at `f1f68e2` found one recurring fragment fingerprint and `0.000813511 / 32.098680905 = 0.002534%` disjoint eligible work, below both recurrence and share gates. Expanded result: STOP; no persistent-cache phase is authorized.
