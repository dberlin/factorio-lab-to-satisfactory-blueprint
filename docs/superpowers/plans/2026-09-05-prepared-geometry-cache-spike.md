# Prepared Geometry Cache Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task-by-task. Use test-driven development for every behavior change. Steps use checkbox syntax for tracking.

**COMPLETE URL/REQUEST RESULTS ARE FORBIDDEN.** No URL string, request hash, `BuildSpec`, complete placement, encoded blueprint, whole-factory route, finalization result, validation result, or refusal may be a cache key or value. Same-URL repetition alone is ineligible evidence.

**Goal:** Measure whether immutable, origin-free local strip preparation templates safely accelerate repeated Freeform preparation primitives across distinct requests and candidate policies without changing solver decisions or normal production behavior.

**Architecture:** Extend the existing `_StagedStaticCache` with an optional audit-only prepared-geometry table. A miss emits one strip at local origin into an isolated `_Canvas`, freezes only its local buildings, stable port roles/endpoints, exact permanently occupied cells, and locally enumerable two-cell access options, then translates/index-remaps that artifact into the current candidate. Shadow mode always produces fresh local geometry and accepts replay only after canonical equivalence; bench serve mode replays certified hits and rechecks current-canvas occupancy before every replay. Disabled mode follows the pre-spike direct `_emit_strip` path and allocates no template, key, timing event, or audit collection.

**Tech stack:** Python 3.14, frozen dataclasses, exact `Fraction`, existing Freeform `_Canvas`/`_StagedStaticCache`/geometry-memo conventions, pytest, Ruff, strict MyPy, JSONL evidence.

**Spec:** This plan is the executable spike specification requested for the branch `prepared-geometry-cache-spike` at base `d81c4d9f628aa70e0357b8a336900d517dae395f`.

## Global constraints

- The reusable boundary is one deterministic `_emit_strip` result plus its `_emit_piler_tail` work, before coater placement, global net pairing, boundary construction, access matching, routing, power, finalization, and validation.
- Values are immutable and local to `(0, 0)` with local building indices. Replay translates coordinates by `(ox, oy)`, remaps every `input_obj`/`output_obj` and port tile index by the destination base index, and replaces `owner_strip` with the current candidate ordinal.
- Keys are URL-free and origin-free. They include a schema/game-data identity, complete physical strip/lane/attachment/piler topology, exact machine/group rates as reduced `Fraction`, belt identity/model/capacity semantics, sorter tiers and stack promises, lane stack promises, orientations, west/east/south channel geometry, strip variant, and every local coater-policy input that can affect emitted lane length. They exclude group/destination occurrence names unless those names affect emitted item/filter/recipe semantics.
- A cached value contains only local emitted `PlacedBuilding` records, stable input/output port role records, prelinked piler transition roles, exact local `blocked`/`solid`/`world_taken` permanent occupancy, and locally enumerable two-cell access candidates.
- Never cache boundary reachability, A* results/frontiers/walls, matched/retired reservations, dynamic routes, proliferator/coater supply trees, power sites/keepouts, final placement, projected finalization, validation, refusal/negative evidence, mutable canvas state, or any whole-request product.
- Replay checks exact current `blocked`, `solid`, and `world_taken` occupancy for every translated permanent cell. A collision is a miss-like fallback to the unchanged fresh emitter; it is never overwritten or treated as a negative cache result.
- Boundary reachability and current-canvas filtering of local access candidates are recomputed in `_reserve_port_access`; the cached candidate set only avoids local two-step enumeration.
- Shadow mismatch rejects replay, records the first canonical mismatch category, and runs the fresh path. It never updates a certified entry from mismatched data.
- The spike is activated only by an explicitly constructed audit cache in tests/bench code. No environment default, server lifecycle, pipeline hook, persistent cache, production registry, or automatic geometry-memo enablement is added.
- Producer spans are exact and disjoint: key construction, local emission/piler work, freeze/canonicalization, replay, collision check, shadow comparison, and access-option filtering are timed separately. Replay is never counted as saved producer time.
- No solver objective, candidate ordering, packing constraint, route ordering, deadline, finalizer, validator, or encoding behavior changes.

## Exact schemas

```python
PreparedGeometryMode = Literal["shadow", "serve"]

@dataclass(frozen=True, slots=True)
class _PreparedStripKey:
    schema: str                         # "flab2bp-prepared-strip/1"
    game_data_version: str              # codec/catalog geometry identity
    machine: tuple[int, int, str, int, int, int, int, float, tuple[int, ...]]
    lanes: tuple[object, ...]            # normalized lane/variant/attachment/dock topology
    pilers: tuple[tuple[int, int, int], ...]  # output side-index/count/stack
    channels: tuple[int, int, int, int]  # west, tail, east MARGIN, box height
    belt: tuple[int, int, Fraction]      # item id, model, exact items/s
    rates: tuple[
        tuple[tuple[str, Fraction], ...],
        tuple[tuple[str, Fraction], ...],
        tuple[tuple[str, Fraction], ...],
    ]
    logistics: tuple[object, ...]        # sorter ids + pick/place and lane stacks

@dataclass(frozen=True, slots=True)
class _LocalPortTemplate:
    role: tuple[str, int, str, CargoDomain]  # input/output, side index, item, domain
    belt_index: int
    x: int
    y: int
    min_x: int
    max_x: int
    tile_indices: tuple[int, ...]
    machines: int
    z: int

@dataclass(frozen=True, slots=True)
class _LocalAccessTemplate:
    port: Cell
    options: tuple[tuple[Cell, Cell], ...]

@dataclass(frozen=True, slots=True)
class _PreparedStripTemplate:
    key: _PreparedStripKey
    buildings: tuple[PlacedBuilding, ...]
    input_ports: tuple[_LocalPortTemplate, ...]
    output_ports: tuple[_LocalPortTemplate, ...]
    piler_nets: tuple[object, ...]       # local prepared endpoints and item/domain
    sorter_count: int
    blocked: tuple[tuple[Cell, int], ...]
    solid: frozenset[tuple[int, int]]
    world_taken: frozenset[tuple[int, int, Fraction]]
    local_access: tuple[_LocalAccessTemplate, ...]
    canonical_digest: str
```

`_StagedStaticCache.prepared_geometry` is `None` when disabled. In audit mode it references one explicitly supplied process-local `dict[_PreparedStripKey, _PreparedStripTemplate]`, which can be shared across different `BuildSpec` objects and requests. Its audit counters/timings live in an optional audit record object; both are absent when disabled.

## Canonical invariants

1. Local origin is exactly `(0, 0)`; no source `ox`, `oy`, URL, request hash, spec identity, candidate ordinal, wall clock, deadline, or process hash enters a key/value.
2. Every cached collection is a tuple/frozenset and every nested mapping is normalized into sorted tuples.
3. Every rate is a reduced `Fraction`; floats appear only where the existing physical yaw schema already uses a deterministic cardinal float.
4. Local building references are either `None` or in `[0, len(buildings))`.
5. Every local port index/tile index names a cached building and its endpoint coordinates equal that building.
6. Canonical occupancy is independently re-derived from replayed buildings and must equal the translated cached `blocked`, `solid`, and `world_taken` sets exactly.
7. Legal strip channels are not occupied: all template buildings remain inside the strip emission extent while `_box(strip)` still includes the unchanged west/east/south reserved channel/margin contract.
8. Cached local access options contain exactly all locally free orthogonal `(access, exit)` pairs, never boundary-reachability filtering or a selected assignment.
9. Shadow fresh canonical bytes equal template canonical bytes before serving is eligible.
10. Final prepared-problem equality and final placement/refusal/validation outcomes are compared outside the cache; equality is evidence, never stored as reusable output.

## Instrumentation and evidence schema

Each exact event records `request_hash`, `url_id` (label only), `candidate_policy`, `mode`, `key_hash`, `hit`, `cross_request_hit`, `cross_policy_hit`, `collision_fallback`, `shadow_match`, and these disjoint nanosecond spans:

- `key_ns`
- `producer_emit_ns`
- `producer_freeze_ns`
- `shadow_compare_ns`
- `collision_check_ns`
- `replay_ns`
- `access_filter_ns`

Per run record:

- request/candidate identity and distinct SHA-256 request hash;
- template occurrences, distinct keys, recurring keys, hits, cross-request hits, cross-policy hits, mismatch/fallback counts;
- preparation wall, exact eligible producer work, replay work, end-to-end wall, p95 across repeats, peak RSS;
- canonical emitted primitive digest, prepared-problem digest, final outcome/refusal kind, placement digest, validator result.

Accounting rules:

- Eligible saved work is the cold median of `producer_emit_ns + producer_freeze_ns` for hit occurrences only.
- Key, comparison, collision, filtering, and replay spans are overhead and never subtracted from or counted as eligible producer work.
- Producer spans are disjoint because local emission ends before freeze begins; all hit rows have zero producer spans.
- Preparation reduction compares complete `_prepare_routing_problem` wall between cold and warm serve runs with identical candidate inputs.
- End-to-end reduction compares identical bench driver runs; p95 and peak RSS are computed separately, never averaged into medians.

## Corpus and thresholds

The minimum corpus is three distinct request hashes and two candidate policies:

1. `magnetic-coil` from `URL_CORPUS`, policies `no-proliferator` and `all-products` where available;
2. `plastic` from `URL_CORPUS`, policies `no-proliferator` and `all-products`;
3. `super-magnetic-ring` from `URL_CORPUS`, policies `no-proliferator` and `all-products`;
4. the report URL SHA-256 from `/home/dannyb/report.md` as an optional large-request preparation-only row if candidate generation and one bounded preparation fit the spike run.

**Pre-measurement corpus amendment.** An untimed shadow scan of the three
`URL_CORPUS` rows above produced 35 strip occurrences and 32 distinct keys, but
all three hits were within one request: zero keys crossed request hashes and zero
keys crossed policies. Those rows therefore cannot exercise the contract's
cross-request replay at all. The corrected measurement run replaces the minimum
corpus with three distinct, non-equivalent request graphs that preserve one exact
60/s iron-ingot subtree while adding disjoint objectives:

1. `iron-ingot*60`;
2. `iron-ingot*60` plus `stone-brick*60`;
3. `iron-ingot*60` plus `copper-ingot*60`;

Each runs `no-proliferator` and `all-products`. This is not same-URL repetition:
the parsed objective sets, request hashes, solved specs, strip sets, and final
outcomes differ. It is the smallest declared corpus that can demonstrate the
intended primitive reuse without weakening the exact semantic key. The report
URL remains omitted because the task explicitly forbids reading
`/home/dannyb/report.md`.

Run shadow certification first with an empty table. Only zero mismatches/fallback-induced behavior changes permits serve measurement. Then run interleaved cold and warm serve arms for at least five repeats per request/policy, serially and under one recorded CPU affinity. The warm arm shares one table across all distinct requests; order rotates by repeat so one URL cannot own every producer occurrence.

Promotion requires all of:

- at least 3 distinct request hashes represented;
- at least 2 candidate policies represented;
- at least 2 recurring primitive keys, each recurring across distinct request hashes (not merely candidate copies of one URL);
- at least 20% of preparation producer time eligible for replacement;
- at least 10% median preparation-wall reduction;
- at least 5% median end-to-end wall reduction;
- no p95 end-to-end or peak-RSS regression greater than 10%;
- byte/canonical-equivalent emitted primitives and identical final placement/refusal/validation outcomes for every measured pair.

A failed safety, recurrence, density, speed, p95, or RSS gate is **STOP**. Passing is only a recommendation to design a separately reviewed production cache; this branch still does not implement one.

---

### Task 1: Add audit-only template contracts and RED tests

**Files:**

- Modify: `src/flab2bp/layout/freeform.py`
- Create: `tests/layout/test_prepared_geometry_cache.py`

- [ ] Add focused tests for origin/URL independence, exact semantic sensitivity, frozen/local values, index/owner remapping, exact permanent occupancy, channel/margin preservation, collision fallback, and explicit exclusion fields.
- [ ] Run `uv run pytest -q tests/layout/test_prepared_geometry_cache.py` and record the expected missing-symbol failure.
- [ ] Implement frozen key/value/audit types and canonicalization helpers without integrating them into `_prepare_routing_problem`.
- [ ] Re-run the focused tests for the type-level contracts.

### Task 2: Integrate shadow and serve replay

**Files:**

- Modify: `src/flab2bp/layout/freeform.py`
- Modify: `tests/layout/test_prepared_geometry_cache.py`

- [ ] Extract the unchanged direct emitter body as `_emit_strip_uncached`; `_emit_strip` keeps the public test-facing signature.
- [ ] On disabled caches call `_emit_strip_uncached` directly before any key/timer/allocation.
- [ ] On audit miss, emit at local origin into an isolated semantic-equivalent canvas, freeze, replay, and compare canonical translated geometry.
- [ ] Remap coordinates, object indices, owners, ports, and piler transitions into the current canvas.
- [ ] Filter cached local access candidates against exact current canvas occupancy inside `_reserve_port_access`; recompute boundary A* and matching unchanged.
- [ ] In shadow mode produce fresh local geometry on every occurrence and reject mismatches. In serve mode use certified hits; collision fallback invokes the unchanged direct emitter.
- [ ] Run `uv run pytest -q tests/layout/test_prepared_geometry_cache.py tests/layout/test_geometry_memo.py`.

### Task 3: Add narrow benchmark/evidence driver

**Files:**

- Create: `scripts/prepared_geometry_bench.py`
- Create: `tests/scripts/test_prepared_geometry_bench.py`

- [ ] Add a deterministic driver that hashes raw URLs for evidence labels only and never passes hashes/URLs into template lookup.
- [ ] Expose `--mode shadow|cold|warm`, `--only`, `--policies`, `--repeat`, `--output`, and optional `--report-url` arguments.
- [ ] Collect exact event spans from the explicit cache, process RSS via `resource.getrusage`, canonical prepared/emission/outcome digests, and final validator result when a bounded final build is requested.
- [ ] Write one JSON object per request/policy/repeat; include commit, command, Python/game-data/schema versions, CPU affinity, and table summary.
- [ ] Unit-test schema, cross-request-hit attribution, disjoint accounting, median/p95/RSS gate arithmetic, and STOP on any equivalence mismatch.
- [ ] Run `uv run pytest -q tests/scripts/test_prepared_geometry_bench.py`.

### Task 4: Certify, benchmark, analyze, and decide

**Files:**

- Create: `docs/superpowers/evidence/2026-09-05-prepared-geometry-cache/commands.txt`
- Create: `docs/superpowers/evidence/2026-09-05-prepared-geometry-cache/shadow.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-prepared-geometry-cache/cold-warm.jsonl`
- Create: `docs/superpowers/evidence/2026-09-05-prepared-geometry-cache/analysis.md`

- [ ] Record exact commit, file SHA-256 values, affinity, and commands before measurement.
- [ ] Run shadow certification across the minimum corpus. Stop serving on any mismatch.
- [ ] Run at least five rotated cold/warm repeats serially across at least three request hashes and two policies.
- [ ] Include the report URL as a bounded preparation-only row when practical; explicitly record why omitted if candidate creation or one preparation cannot fit the declared cap.
- [ ] Analyze per-key/request/policy counts, exact producer/replay spans, preparation/end-to-end medians and p95, RSS, and every equivalence field.
- [ ] Apply the thresholds mechanically and write `PROMOTE` or `STOP` prominently. A failure on one gate is a STOP; do not reinterpret thresholds after seeing data.

### Task 5: Focused verification, review, and commits

**Files:** all files above.

- [ ] Run focused tests only:

```bash
uv run pytest -q tests/layout/test_prepared_geometry_cache.py \
  tests/layout/test_geometry_memo.py tests/scripts/test_prepared_geometry_bench.py
```

- [ ] Run targeted static checks only:

```bash
uv run ruff check src/flab2bp/layout/freeform.py scripts/prepared_geometry_bench.py \
  tests/layout/test_prepared_geometry_cache.py tests/scripts/test_prepared_geometry_bench.py
uv run mypy src/flab2bp/layout/freeform.py scripts/prepared_geometry_bench.py \
  tests/layout/test_prepared_geometry_cache.py tests/scripts/test_prepared_geometry_bench.py
```

- [ ] Commit plan, implementation/tests, and evidence/decision as separate reviewable commits.
- [ ] Request independent review against this plan; fix every Critical or Important finding and rerun affected focused checks.
- [ ] Return commit hashes, exact test/static/measurement results, and the mechanical `PROMOTE` or `STOP` decision. Do not add a production cache even if every gate passes.

## Rollback

The implementation is audit-only and has no production enablement. If the spike is unsafe or fails promotion, retain the plan, tests, instrumentation, raw evidence, and STOP analysis for review, but do not wire prepared geometry into `geometry_memo.for_spec`, pipeline, CLI, web server, or persistent storage. Removing the spike later is one commit-level rollback of the optional fields/helpers/script/tests/evidence; the disabled `_emit_strip` path remains the unchanged source behavior throughout.