# Baseline evidence: 2026-09-07 coater node

## Before

This directory captures the "before" measurement for the DSP Spray Coater
layout-node change (`docs/superpowers/specs/2026-09-07-coater-node-design.md`).
It changes no production code; it only records the state of the gate and the
coater seat census prior to the change, for Task 7 to compare against.

Two commands were run, one at a time (this box has four other agents building
concurrently, and its load is disk I/O bound, not CPU bound, so `uptime` was
recorded around each run rather than waiting for the box to go idle):

1. `python3 scripts/audit.py --tier stress --strategy both --budget 30 --json
   docs/superpowers/evidence/2026-09-07-coater-node/baseline.jsonl`, tee'd to
   `baseline-audit.txt`. `--tier stress` runs stress and every tier below it,
   i.e. the full 72-cell suite across both the freeform and sequence-pair
   strategies. `uptime` was captured immediately before and immediately after:
   - before: `22:35:53 up 22 days, 4:21, 10 users, load average: 10.90, 10.00, 11.42`
   - after:  `22:39:14 up 22 days, 4:25, 10 users, load average: 47.08, 28.78, 18.47`
   - Result: **72/72 cells clean** (freeform 36/36 clean, sequence-pair 36/36
     clean; 0 refused, 0 invalid, 0 crashed, 0 not run). Wall time 199s.
     `audit.py` prints a per-cell "NOT CLEAN" only on a refusal, and none
     occurred here, so the run report is unambiguous: all 72 cells passed.
   - `baseline.jsonl` was deleted before this run (the flag appends) and now
     holds exactly 72 JSON records, one per cell — the input Task 7's
     `scripts/audit_compare.py` wants.

2. `python3 docs/superpowers/evidence/2026-09-07-coater-node/seat-census.py`,
   redirected to `seat-census.txt`. This is a read-only script: for every
   candidate spec of every audit-corpus URL that has spray lanes, it plans
   strips, greedily packs them, and prepares the routing problem, then
   records each committed coater's seat (host cell) and 1x3 body extent.
   `uptime` was captured immediately before and immediately after:
   - before: `22:39:28 up 22 days, 4:25, 10 users, load average: 38.21, 27.71, 18.29`
   - after:  `22:39:56 up 22 days, 4:26, 10 users, load average: 26.50, 25.86, 17.97`
   - Result: **217 coaters seated**, 0 specs refused, across 217 rows total
     (`seat-census.txt`).

Baseline clean-cell count: **72/72**.
Baseline seated-coater count: **217**.

Task 7 compares against these two numbers.

## After

