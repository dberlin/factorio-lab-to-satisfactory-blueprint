# Local branch triage — 2026-09-13

Repository: /home/dannyb/sources/factorio-lab-to-blueprint
Master at start: 46350425. Master moved to b7d0bd4a (two commits) while this ran; that only adds to master and changes no verdict.
Branches examined: 104 local branches other than master. Read-only git throughout; nothing deleted, checked out, or modified.

## Method

1. `git rev-list --count master..B` and `git cherry master B` for every branch. Ahead counts on pre-rewrite branches are history noise (up to 754 "ahead"); `git cherry` reduces almost all of them to zero or a handful of `+` commits.
2. Three `+` commits recur on nearly every post-2026-09-02 branch: f022b02d, 19c169c4, 253eb623 ("bench: record phase B baselines…", "bench: capture and replay last-mile cluster searches", "bench: re-capture the cluster corpora after the Ruling V fixes"). Their patch-ids differ from master only because of the history rewrite; the files they touch (scripts/last_mile_bench.py, tests/scripts/test_last_mile_bench.py, tests/scripts/test_route_bench_policy.py, src/flab2bp/layout/last_mile.py) are byte-identical on master and the subject "capture and replay last-mile cluster searches" exists on master as cc66c66a. A branch whose only `+` commits are these three has nothing novel.
3. For every remaining `+` commit, the subject was checked against the full master log (rewritten SHAs keep their subjects). Unmatched subjects were then checked by file existence, `git grep` for the distinctive symbols on master, and by docs/superpowers/{specs,plans,evidence}, docs/speedup-idea-backlog.md, docs/BACKLOG.md and the on-disk (gitignored) .local-evidence/.
4. For archive/backup/safety branches, evidence directories present on the branch were compared against master's docs/superpowers/evidence and .local-evidence by name and file count, and the preserved source files were diffed against master.

Master evidence context used: docs/superpowers/evidence/2026-09-08-general-abstraction-repairs/worktree-retirement.json records that nine stale worktrees (abstraction-general, abstraction-original, design-solver-portfolio, exp-channelized, exp-lifetime-order, exp-sink-coalesced, exp-sink-lifetime, flab-topology-experiments, hierarchical-v5) were hash-verified and archived to /home/dannyb/sources/flab-worktree-archives/2026-09-08 before removal; the branches were left intact on purpose.

## E. WORKTREE-ATTACHED (7) — leave alone

| branch | worktree | note |
|---|---|---|
| coater-full9 | .claude/worktrees/coater-full9 | tip f93f84b6; only the common-3 `+` commits, otherwise patch-equivalent to master |
| hierarchical-v6 | .claude/worktrees/hierarchical-v6 | tip c05d7f52 = coater-full9 tip + "preserve frozen hierarchy v6 source and evidence"; holds 1007-file evidence dir 2026-09-08-hierarchical-v6 that master lacks; 69 src files differ from master |
| experiment/hierarchy-constructive-composition | .claude/worktrees/hierarchy-constructive-spike | 0 ahead of master (at 446c2296) |
| plan-feasibility-first | .claude/worktrees/plan-feasibility-first | 16 unlanded commits (feasibility-first construction, bench/feasibility.py, layout/construction.py, transport_capacity.py, scripts/feasibility_audit.py) plus 130 evidence files not on master; see backup/ entry in D |
| hierarchy-continuation | .claude/worktrees/technology-routing | 1 unique commit ce4dc0b3 (2026-09-12) "Preserve infill collision ownership and index broad-phase rows" touching dsp/planet.py, hierarchy/compose.py, routing_domain.py; merged with master b895485f |
| fix-corpus-and-coater-failures | .claude/worktrees/transport-first-current | 0 ahead of master (at 037328f3) |
| fix/exact-dsp-url-refusal | .claude/worktrees/url-refusal-fix | only the common-3 `+` commits; evidence 2026-09-10-url-refusal is on master (211 files) |

## A. DELETE-SAFE (67)

### A1. Zero commits ahead of master (14)
bl-crossrule, bl-footprints, bl-gamerules-rebased, bl-lateral, bl-poseless, freeform-fixes, game-rules, organic-crystal-refusal, spine-elevated, validator-blindspot, web-clientside — August Spine/game-rules era, fully merged.
fix-quantum-chip-freeform, transport-spray-compact — 2026-09-10, fully merged.
typed-pack-outcomes — points at 46350425, the master tip at triage start.

### A2. Every ahead commit is patch-equivalent to master (`git cherry` shows only `-`) (13)
agent/complete-splitter-rules-fix (1), agent/fractionator-port-input (3), agent/freeform-output-completion (4), bl-gamerules (3), cache-decorator-cleanup-plan (1), cleanup-linear-replacement (1), fix/audit-completion-ownership (1), fix/freeform-completion-regression (1), fix/freeform-magnetic-alternative (2), fix/sequence-plastic-deadline (1), fix/universe-cargo-fanout (1), reliability-proof-scoped-routing-verify-v3 (5), sequence-deadline-repair-final2 (2).

### A3. Only the common-3 rewritten bench commits; all content already on master (27)
Each of these shows hundreds "ahead" but `git cherry` leaves only f022b02d/19c169c4/253eb623, whose files are byte-identical on master. Outcome records on master for reference:
- abstraction-general, abstraction-original — evidence 2026-09-08-abstraction-repairs, -abstraction-review, -general-abstraction-repairs; worktrees archived per worktree-retirement.json.
- broke7-packer-fix, broke8-flow-order, packer-matrix-regression, optimize-concurrent-layout, proof-bound-experiments, proof-route-bound — 2026-09-04/05 fixes whose commits all landed (last subjects "pass sequence deadline into preparation", "Keep direct bridges straight…", "prune proof-dominated route candidates" all on master); evidence 2026-09-04-additional-lower-bounds, 2026-09-05-scale-levers.
- buildings-index — evidence 2026-09-07-buildings-index. indexed-scans — evidence 2026-09-07-indexed-scans. machine-upto — evidence 2026-09-07-machine-upto. power-tower — evidence 2026-09-07-power-tower.
- energy-exchanger — evidence 2026-09-03-energy-exchanger. multibelt-docs — evidence 2026-09-03-multiple-belts, 2026-09-04-pilers.
- exp-direct-insert, exp-features, exp-hierarchical, exp-lifetime-order, exp-sink-coalesced, exp-sink-lifetime — evidence 2026-09-06-exp-direct-insert / exp-features / exp-hierarchical / exp-trunk (the three sink/lifetime branches end on the same "trunk pre-assignment" evidence commit that master has as 9765b227).
- hierarchical-v4, hierarchical-v5 — evidence 2026-09-07-hierarchical-v4, 2026-09-08-hierarchical-v5.
- phase-b-last-mile, phase-c-alns, phase-d-portfolio — evidence 2026-09-02-phase-b-last-mile / phase-c-alns / phase-d-portfolio.
- technology-routing — evidence 2026-09-08-technology-routing (its tip f93f84b6 is also coater-full9's tip). topology-experiments — evidence 2026-09-08-general-abstraction-repairs (archived worktree flab-topology-experiments).

### A4. Unique commits whose subjects landed on master under rewritten SHAs, or docs-only commits whose files are on master (13)
- dark-fog-explicit-flow — "Canonicalize Dark Fog flow identities" on master; tests/rates/test_dark_fog_flow_policy.py and fixture present.
- fix/freeform-corpus-deadlines-continued, fix/sequence-corpus-continuation, perf/junction-ban-hotpath — both subjects ("reuse exact freeform preparation evidence" a4bd0f05, "keep projection pitch evidence scoped" 780495a5) on master.
- fix/freeform-quantum-pitch — same two plus "prepare candidate-specific projection pitch" and its own revert (33c5cd9f); net zero.
- fix/survivor-bounds-complexity — four subjects on master (incl. "complete cooperative deadline cancellation" bf635ec7); the two unmatched are `wip:` drafts of the landed "Optimize prospective cleanup and frame enumeration".
- sequence-coater-routing — all 15 unique subjects (2026-08-24) on master; bench/ab.py, bench/promotion.py, layout/junction.py, layout/slots.py, scripts/ab_compare.py all present.
- sequence-deadline-repair, sequence-deadline-repair-final — "bound boundary belt compaction" on master as f1aefedf.
- transport-routing — "certify physical flow and preserve transport geometry" on master as 7929d2ce; layout/physical_flow.py present.
- verify/band160-final4, verify/band160-final5 — "preclear proved coater projection risks" on master (3da3095c line).
- web-ui — one docs/BACKLOG.md commit whose subject is on master; docs/WEB_UI.md exists.
- exp-channelized — one docs commit; docs/superpowers/specs/2026-09-06-channelized-dataflow-layout-design.md is on master.
- design-solver-portfolio — one docs commit; both files (plans/2026-09-06-hierarchical-v1.md, specs/2026-09-06-multi-solver-orchestrator-design.md) are on master.

## B. SUPERSEDED-EXPERIMENT (24)

Branches marked **(record only on branch)** hold evidence or verdicts that master does not carry; copy those files somewhere before deleting if the numbers matter.

- **agent/freeform-output-taper** — one 31-line 2026-08-31 tweak ("taper repeated packing solves": retry budget = per_solve / candidate_index). Never merged; freeform.py's pack-retry budgeting was rewritten since (branch is 943 behind). No written record anywhere; the idea is one line if it belongs in docs/speedup-idea-backlog.md.
- **coater-node** — the "Spray Coater body may never cover its lane head" seat fix plus fixture re-records; the seat variant (48/72) lost to the placed node (72/72). Record: docs/superpowers/evidence/2026-09-07-exp-coater-node, 2026-09-07-coater-placed-gate, specs/2026-09-07-coater-node-design.md, plans/2026-09-07-coater-placed.md. The branch's own 12-file evidence dir 2026-09-07-coater-node (pre-change baseline census) is not on master.
- **compound-block-cache** — Gate 0 STOP: recurring-work fraction 8.75% below the 10% threshold; fragment-harvest expansion recorded as non-gating. Record: plans/2026-09-05-background-compound-block-cache.md and docs/speedup-idea-backlog.md line 235 on master; the STOP verdict itself lives only in the branch's .superpowers/sdd/2026-09-05-background-compound-block-cache/task-0-report.md **(record only on branch)**.
- **design-a-conflict-search, design-a-port-constraints, design-a-pose-substitution** — 19 shared 2026-09-05 commits (static-access relation cuts, build-phase telemetry, window-repair keying, conflict-search telemetry), each ending in "chore: storing work in progress before removing the worktree" on 2026-09-06; tips differ by 2-3 files. Direction superseded: window repair measured inert (evidence 2026-09-02-phase-c-alns), conflict search moved to CaDiCaL (specs/2026-09-09-cadical-conflict-family-design.md), static-access diagnosis in specs/2026-09-07-lane-fanout-design.md. The telemetry commits have no outcome doc.
- **design-b-adaptive-dispatch** — the three telemetry commits from the design-a series only. Same record.
- **new-url-100s-fix** — "Learn exact static-access relation cuts" + "Close access cuts over all net endpoints" (2026-09-05). Superseded by the lane fan-out fix (specs/2026-09-07-lane-fanout-design.md, evidence 2026-09-07-lane-fanout).
- **exp-pressure** — default-off pressure-aware corridors (layout/pressure.py, not on master). Record: evidence 2026-09-06-exp-pressure (194 files) and 2026-09-06-exp-pressure-gate.
- **exp-trunk** — default-off trunk pre-assignment. Record: evidence 2026-09-06-exp-trunk (148 files); commit 9765b227.
- **fix/sequence-quantum-deadline** — carries "prepare candidate-specific projection pitch" un-reverted; the sibling fix/freeform-quantum-pitch reverted it (33c5cd9f) and master landed "index projection pitch candidates" (65833c49) instead.
- **freeform-arrangement-exploration** — 2026-08-24 measurement probes (scripts/backward_probe.py, eval_cost_probe.py, routability_probe.py). Spine-era; record: docs/BACKLOG.md (routability/arrangement entries); its spec section-strategy-a.md was removed on master with the Spine backend (5072a938).
- **pathfinder-router** — 2026-08-24 negotiated-congestion router behind a switch. Record: docs/BACKLOG.md lines 1041-1048 ("real negotiated congestion was built, measured at parity") and the freeform.py module header on master.
- **seqpair-option3, seqpair-router** — 2026-08-24 seqpair.py prototype and A/B scripts. Landed in another form as src/flab2bp/layout/sequence_pair.py; record: specs/2026-08-24-sequence-pair-routing-solver-design.md (blame-wall feedback is in that spec).
- **matrix-lab-recovery** — superset of all six matrix-* branches (35 unique commits; every other matrix branch's subjects are contained in it). Matrix lab stacking regressed the corpus (baseline 18/18 CLEAN vs stacked 16 CLEAN / 2 REFUSED per round) and Matrix recovery hit "STOP — rejected on repaired master and must not merge". Record on master: plans/2026-09-04-matrix-lab-stacking.md only. Evidence dirs 2026-09-04-matrix-lab-stacking (comparison.md), 2026-09-04-matrix-forced-topology, 2026-09-05-matrix-recovery (gate-report.md) and specs/plans 2026-09-05-matrix-sequencepair-recovery exist **only on this branch**. Keep until copied, or delete last.
- **matrix-capped-final-attempt, matrix-routing-efficiency** (11 unique, identical sets), **matrix-unstacked-fallback** (15), **matrix-topology-experiment** (22), **matrix-lab-stacking** (25) — strict subsets of matrix-lab-recovery. Record: as above.
- **prepared-geometry-cache-spike** — spike failed its own gate (end_to_end_wall_reduction = -0.2%, listed in failed_gates). Record only on branch: plans/2026-09-05-prepared-geometry-cache-spike.md and artifacts/prepared_geometry_cache/summary.json **(record only on branch)**; master's docs/speedup-idea-backlog.md does not mention it.

## C. HAS-NOVEL-WORK (1)

- **cold-proof-prioritization** — two 2026-09-05 commits (8ca0b1f2 "fall back from self-blocking family priority", e8a1036e "preserve fallback evidence and limits"): ~270 lines in src/flab2bp/layout/freeform.py plus tests/layout/test_freeform_priority.py (tests: self_blocked_source_family_yields_to_longest_first_order, …_does_not_retry_after_deadline, …_does_not_retry_without_expansion_budget). Master's freeform.py still has family priority (45 mentions) but no self-blocked fallback and no longest-first fallback, and no doc records a verdict. The same two commits are embedded in the design-a branches. Small enough to re-evaluate by running its three tests against master before deciding; if the packer rewrite made it moot, it moves to B.

Not placed in C despite unlanded source: plan-feasibility-first (worktree, E) and its backup (D) hold the largest unlanded module set (bench/feasibility.py, layout/construction.py, layout/transport_capacity.py, scripts/feasibility_audit.py); the feasibility-first construction design was overtaken by CaDiCaL transport routing on master, and .local-evidence/2026-09-06-feasibility-first holds only the later cadical-* runs.

## D. DELIBERATE-SNAPSHOT (5)

| branch | what it preserves | captured on master? |
|---|---|---|
| archive/coater-full9-before-routing-rebase | 1478ee08 "preserve original coater repair before integrated routing rebase" (dsp/catalog.py, layout/coater_mode.py, finalize.py, routing_domain.py, two tests) | **Yes.** All six preserved files diff empty against master; every evidence dir on the branch exists on master. |
| archive/hierarchical-v6-before-routing-rebase | 2b733401 "preserve frozen hierarchy v6 source and evidence" on the 62b6f3db lineage; 1011-file evidence dir 2026-09-08-hierarchical-v6 | **No.** master has no hierarchical-v6 evidence (v1-v5 only) and no v6 mention beyond worktree-retirement.json. The hierarchical-v6 worktree branch holds a sibling copy (1007 files, different base: 87 src/test files differ, neither is an ancestor of the other). |
| archive/mall-completion-before-master-20260911 | ffb89969 + 51de538f, the pre-rebase mall-completion pair | **Yes, in rebased/reviewed form.** Both subjects landed as 7929d2ce and a1a2d4a1; 33 code files differ from the landed pair because master's transport_routing/composition.py, inventory.py, solver.py were reworked further. Evidence: docs/superpowers/evidence/2026-09-11-mall-completion and .local-evidence/2026-09-11-mall-completion on disk. |
| backup/plan-feasibility-first-before-master-03e2d74f | 16 feasibility-first commits (2026-09-06) and 130 evidence files under docs/superpowers/evidence/2026-09-06-feasibility-first/construction-experiments | **No.** master's dir of the same name has 515 different files and no README; none of the 130 files exist on master; .local-evidence/2026-09-06-feasibility-first holds only cadical-physical-flow and cadical-settlement-progress. Content is duplicated on the plan-feasibility-first worktree branch (rebased, same 16 subjects). |
| safety/transport-geometric-pre-master-20260912 | 53d544f0 "Replace tile A-star routing with certified geometric search…" as it stood before review | **Yes, in reviewed form.** Landed as 765156c7 then revised in 446c2296; 62 files modified and 17 added on the master side, none deleted. All evidence dirs present on master; .local-evidence/2026-09-12-architecture holds the AccessCompilerExperiment runs. |

## Delete list for bucket A (67 names)

```
git branch -D bl-crossrule bl-footprints bl-gamerules-rebased bl-lateral bl-poseless freeform-fixes game-rules organic-crystal-refusal spine-elevated validator-blindspot web-clientside fix-quantum-chip-freeform transport-spray-compact typed-pack-outcomes
git branch -D agent/complete-splitter-rules-fix agent/fractionator-port-input agent/freeform-output-completion bl-gamerules cache-decorator-cleanup-plan cleanup-linear-replacement fix/audit-completion-ownership fix/freeform-completion-regression fix/freeform-magnetic-alternative fix/sequence-plastic-deadline fix/universe-cargo-fanout reliability-proof-scoped-routing-verify-v3 sequence-deadline-repair-final2
git branch -D abstraction-general abstraction-original broke7-packer-fix broke8-flow-order buildings-index energy-exchanger exp-direct-insert exp-features exp-hierarchical exp-lifetime-order exp-sink-coalesced exp-sink-lifetime hierarchical-v4 hierarchical-v5 indexed-scans machine-upto multibelt-docs optimize-concurrent-layout packer-matrix-regression phase-b-last-mile phase-c-alns phase-d-portfolio power-tower proof-bound-experiments proof-route-bound technology-routing topology-experiments
git branch -D dark-fog-explicit-flow fix/freeform-corpus-deadlines-continued fix/sequence-corpus-continuation perf/junction-ban-hotpath fix/freeform-quantum-pitch fix/survivor-bounds-complexity sequence-coater-routing sequence-deadline-repair sequence-deadline-repair-final transport-routing verify/band160-final4 verify/band160-final5 web-ui exp-channelized design-solver-portfolio
```

## Bucket B names with record pointers (24)

- agent/freeform-output-taper — no record; superseded by freeform.py's rewritten per_solve/candidate_index retry budget
- coater-node — docs/superpowers/evidence/2026-09-07-exp-coater-node, 2026-09-07-coater-placed-gate; specs/2026-09-07-coater-node-design.md
- compound-block-cache — plans/2026-09-05-background-compound-block-cache.md + docs/speedup-idea-backlog.md:235; STOP verdict only in branch's .superpowers/sdd task-0-report.md
- design-a-conflict-search — evidence 2026-09-02-phase-c-alns; specs/2026-09-09-cadical-conflict-family-design.md; specs/2026-09-07-lane-fanout-design.md
- design-a-port-constraints — same as above
- design-a-pose-substitution — same as above
- design-b-adaptive-dispatch — same as above (telemetry commits only)
- new-url-100s-fix — specs/2026-09-07-lane-fanout-design.md; evidence 2026-09-07-lane-fanout
- exp-pressure — evidence 2026-09-06-exp-pressure, 2026-09-06-exp-pressure-gate
- exp-trunk — evidence 2026-09-06-exp-trunk
- fix/sequence-quantum-deadline — reverted in fix/freeform-quantum-pitch 33c5cd9f; master landed 65833c49 instead
- freeform-arrangement-exploration — docs/BACKLOG.md; spec removed with the Spine backend (5072a938)
- pathfinder-router — docs/BACKLOG.md lines 1041-1048; freeform.py module header
- seqpair-option3 — specs/2026-08-24-sequence-pair-routing-solver-design.md; landed as layout/sequence_pair.py
- seqpair-router — same as above
- matrix-lab-recovery — plans/2026-09-04-matrix-lab-stacking.md on master; evidence 2026-09-04-matrix-lab-stacking, 2026-09-04-matrix-forced-topology, 2026-09-05-matrix-recovery ONLY on this branch (copy first, delete last)
- matrix-lab-stacking — subset of matrix-lab-recovery; same record
- matrix-topology-experiment — subset of matrix-lab-recovery; same record
- matrix-unstacked-fallback — subset of matrix-lab-recovery; same record
- matrix-capped-final-attempt — subset of matrix-lab-recovery; same record
- matrix-routing-efficiency — subset of matrix-lab-recovery; same record
- prepared-geometry-cache-spike — failed gate recorded only on branch (plans/2026-09-05-prepared-geometry-cache-spike.md, artifacts/prepared_geometry_cache/summary.json)

## Loose ends for the lead

1. hierarchical-v6 evidence (about 1000 files) exists only on archive/hierarchical-v6-before-routing-rebase and the hierarchical-v6 worktree branch. Decide whether it should land under docs/superpowers/evidence before either goes.
2. The feasibility-first construction-experiments evidence (130 files) and its four source modules exist only on backup/plan-feasibility-first-before-master-03e2d74f and the plan-feasibility-first worktree branch.
3. matrix-lab-recovery, compound-block-cache, prepared-geometry-cache-spike carry gate verdicts (two STOPs, one failed gate) that master's docs do not record.
4. cold-proof-prioritization is the only candidate for still-useful unlanded source; three tests decide it.
