# Abstractions and cruft review: src/flab2bp and scripts/

Date: 2026-09-13. Surveyed tree: master 46350425 plus the working-tree rename of `routing_domain._astar` to `_geometric_search` (since landed on master 9c958bc9 with a junction-admission memo module; neither changes the findings). Read-only survey by a review agent; no tests or builds were run. CPU load during measurement: 4.6 mean runnable procs (vmstat), well under the 64 threshold. Line numbers refer to that tree.

Sections 1 to 10 are the review agent's text verbatim (delivered in three parts; the agent's harness does not let it write files). Section 11 and the addendum at the end of section 4 were written by the session lead.

## 1. Verdict in one paragraph

The discipline held where it was mechanically enforced and slipped where it depended on judgment. The two recorded bars are intact: every production search still goes through one `geometric_router.route` entry point (exactly two callers, no other module imports the kernel), and no module outside `flab2bp.indexed` imports littletable or networkx. The public surface is clean: code outside `flab2bp.layout` imports four strategy classes, the base types, and the observe channel, and nothing else of substance. What went backwards is (a) the deadline/budget concept, which now has 4 copies of the same one-line helper, 3 "clock ran out" exception types, 7 budget/ledger classes and an untyped `budget["left"]` dict at 35 sites; (b) private-name coupling, which grew with every new package (transport_routing is built on 17 private routing_domain names; freeform reaches into routing_domain at 88 sites; tests reference 122 private routing_domain names at 883 sites and monkeypatch 295 private attributes); (c) function size, with `_route_all` at 3,493 lines and 61 closures sharing 22 nonlocal names; and (d) naming debt left behind by the geometric-router migration ("expansions" at 270 sites, two unrelated classes both named `GeometricWorld`, an 8-line `route_kernel.py` whose only function returns a constant). Dead code is small: 12 top-level symbols and about 15 methods.

## 2. Module size and shape

Totals: src 98,118 lines in 128 files; src+scripts 107,989. Ten modules exceed 1,500 lines, five exceed 3,000.

| Module | Lines | Top-level funcs | Classes | Methods | Private funcs+classes |
|---|---|---|---|---|---|
| layout/routing_domain.py | 18,708 | 150 | 52 | 99 | 183 of 202 |
| layout/freeform.py | 7,166 | 78 | 16 | 16 | 84 of 94 |
| layout/validate.py | 7,034 | 147 | 14 | 25 | 145 of 161 |
| layout/sequence_solver.py | 6,839 | 67 | 30 | 64 | 87 of 97 |
| layout/finalize.py | 4,748 | 66 | 29 | 53 | 61 of 95 |
| layout/strip_variants.py | 2,469 | 41 | 20 | 28 | 31 |
| layout/sequence_pair.py | 2,380 | 48 | 19 | 30 | 32 |
| dsp/colliders.py | 2,215 | 47 | 8 | 5 | 27 |
| layout/hierarchy/compose.py | 1,919 | 26 | 7 | 5 | 26 |
| dsp/catalog.py | 1,919 | 71 | 10 | 6 | 44 |

Context on routing_domain: it was extracted from freeform.py on 2026-09-08 (commit 701945af, "separate shared routing domain from search strategies"). freeform.py was 23,494 lines before the split; routing_domain + freeform today total 25,874. So the 18.7k-line module is a move, not fresh growth, and the pair grew 10 percent (2,380 lines) since. Inside routing_domain the post-split churn was +5,196/-2,832 lines over 68 commits.

### 2.1 routing_domain.py seams (symbol-name clusters and cross-cluster references)

| Cluster | Lines | Symbols | Largest members |
|---|---|---|---|
| route_all / commit / repair | 5,654 | 63 | `_route_all` 5919-9411, `_commit_paths` 10441-10909, `_route_boundary_nets` 11796-12032, `_merge_frontier` 5405-5583 |
| prepared problem / nets / inventory | 2,224 | 27 | `_prepare_routing_problem` 16299-16941, `_prepare_transport_inventory` 15855-16296, `_tap_source` 11476-11767, `_source_for` 10924-11062 |
| staged-static / projection frames | 2,156 | 34 | `_CompositionProjection` 13499-14011, `_projected_coater_junction_bans_by_frame` 13148-13464, `_junction_projection_frames` 12693-12967 |
| coater / proliferator | 1,654 | 16 | `_place_coaters` 17586-18390, `_proliferator_supply_tree` 18423-18669, `_coater_seats` 17327-17415 |
| strip / lane / piler / emit | 1,593 | 21 | `Strip` 533-974, `_emit_strip` 3337-3637, `_dock_input_lane` 3826-3940 |
| canvas / grid / search | 1,269 | 25 | `_Canvas` 2350-2795, `_geometric_search` 4671-4916, `_Grid` 4420-4503, `_make_grid` 4559-4642 |
| power | 1,244 | 7 | `_power_plan` 14169-14897, `plan_power_infill` 14955-15332, `_place_power` 14900-14949 |
| port access / corridor | 979 | 12 | `_reserve_port_access` 10022-10420, `_match_access_corridors` 9801-10019, `_CorridorReservations` 9567-9780 |
| junction | 366 | 10 | `_prepared_junction_ban` 2198-2289, `_cancellable_junction_ban_offsets` 2118-2195 |
| other | 648 | 27 | `_prospective_static_failure` 12970-13070, `_StagedStaticCache` 12492-12572 |

Cross-cluster reference counts (distinct symbols): route_all to canvas/grid 33, route_all to prepared 26, prepared to route_all 23 (bidirectional, so those two are one unit), coater to canvas 17, power to staged-static 8, port-access to canvas 9, staged-static to junction 10. Power (7 symbols, 1,244 lines, 8 outbound refs) and coater/proliferator (1,654 lines) are the cleanest cuts. Staged-static/projection in routing_domain (2,156 lines) and the projection cluster in finalize (1,537 lines: `first_projected_static_failure`, `independent_projection_pair`, `_certify_frame`, `_ProjectionCache`) share one vocabulary, and routing_domain reaches into finalize for 23 names at 81 sites, 12 of them private (`finalize._ProjectionCache`, `_CleanupSurvivorGraph`, `_power_nodes`, `_addon_projection_context`, `_frame_candidates_for_extent`, `_projected_coater_splitter_candidates`, `_ProjectedPowerIndex`, `_ProjectionCounters`, `_CleanupOperations`, `_cleanup_survivor_bounds`, `_building_centre`, `_projected_addon_failure_from_context`). finalize does not import routing_domain, so the dependency is one-way, but the projection concept is split across two files through a private seam.

Biggest single symbols: `_route_all` 3,493 lines with 61 nested closures totalling 3,057 lines (`_repair` 490, `_ends` 311, `_search` 261, `_last_mile` 153, `_prebuilt_source_starts` 124, `_grouped_overcap_alternative` 124, `_commit_selection` 113, `_finish` 108), 18 `nonlocal` statements over 22 names. Then `_place_coaters` 805, `_power_plan` 729, `_prepare_routing_problem` 643, `_CompositionProjection` 513, `_commit_paths` 469, `_Canvas` 446, `Strip` 442, `_prepare_transport_inventory` 442, `_reserve_port_access` 399.

### 2.2 freeform.py seams

`FreeformLayout` (4415-6899) is 2,485 lines; its `_sweep` method (freeform.py:4899) is 2,001 lines and `lay_out` (4467) is 431. Separable clusters: direct insertion (`_direct_*`, 14 functions plus `_DirectCandidate`, `DirectCandidateMemo`, `_DirectCandidateSnapshot`, `DirectAlignmentMemo`, roughly lines 1180-1900), CP-SAT packing (`_pack_model` 2546-3057 at 512 lines, `_pack_result`, `_pack_window` 17 params, `_pack` 15 params, `_PackModel`, `_PackCpProfile`, `_PackSolveOutcome`, 2518-3400), strip planning (`plan_strips` 861-1097, `_seat_inputs`, `_merge_lanes`, `_allocate_machines`), portfolio deadline arithmetic (`_portfolio_soft_deadline` 4383 through `_window_candidate_seconds`), and no-good bookkeeping (`ExactPackNoGood`, `_DirectRelationNoGood`, `_add_*_no_good`). Direct insertion and CP-SAT packing are each self-contained enough to be modules.

### 2.3 sequence_solver.py, validate.py, finalize.py

- sequence_solver: `SequenceSolver` 1008-3144 (2,137 lines; `__init__` takes 26 parameters; `search` 393 lines; `_complete_routing_stage` 549 lines, 12 params), `_production_run` 4841-6289 (1,449 lines, 16 params, closures `transform_stage` 174, `window_pack` 111, `prepare_candidate` 97), `_record_routing_observation` 24 params.
- validate: 147 `@check`-registered rule functions in prefix families (geom, addon, junction, coater, lane, power). Largest symbol is `Context` at 328 lines. Shape is fine; only the file length argues for splitting by family.
- finalize: `_CleanupSurvivorGraph` 706 lines, `_ProjectionCache` 311 lines, projection cluster 1,537 lines.
- Tests, for context: tests/layout/test_freeform.py is 25,510 lines and test_sequence_solver.py 11,458.

Other parameter-heavy signatures (50 functions exceed 8 parameters): `pipeline.build` 25, `_merge_frontier` 17 (routing_domain.py:5405), `_geometric_search` 15 (routing_domain.py:4671), `_commit_paths` 14, `_route_all>_search` 14, `run_strategy_race` 13, `strategy_race.refused` 13.

## 3. Repetition

Method: AST token-normalised bodies (names and constants abstracted), exact structural hash for bodies with 60+ tokens, Jaccard on 6-shingles for bodies with 100+ tokens.

**R1. Quaternion and rotation math, 12 bodies for 5 functions.**
- `_qmul`: dsp/colliders.py:201, dsp/planet.py:439 (Jaccard 0.84), scripts/extract_dsp_colliders.py:77 and scripts/extract_dsp_slot_poses.py:83 (exact copies of colliders).
- `_qrot`: dsp/colliders.py:212, dsp/splitter_ports.py:193 `_rotate_vector` (0.97), scripts/extract_dsp_colliders.py:88 (exact), scripts/extract_dsp_slot_poses.py:94 (0.57).
- `_cross`: dsp/colliders.py:230 and dsp/planet.py:427 (exact).
- `_look_rotation`: dsp/colliders.py:242 and dsp/planet.py:450 (0.62); `_spherical_rotation` colliders.py:265 vs planet.py:477 `spherical_rotation` (0.58).
Proposal: one `dsp/quaternion.py` (about 60 lines) imported by colliders, planet, splitter_ports and both scripts. Payoff: one place to fix numeric drift between the collider extractor and the runtime. Cost: about 150 lines removed, pure functions; the exact-hash groups are byte-equivalent, the 0.58-0.84 pairs need a diff before merging.

**R2. Deadline one-liner, 4 named copies plus 3 closures plus 33 inline comparisons.** `return deadline is not None and time.monotonic() >= deadline` appears as routing_domain.py:431 `_expired`, compact_seed.py:1377 `_deadline_reached`, finalize.py:4523 `_completion_expired`, routing_proposals.py:146 `check_deadline` (raising form); as closures pipeline.py:1154 `attempt_expired`, sequence_solver.py:4869 `deadline_reached`, sequence_solver.py:5160 `compact_deadline_reached`; and inline 9 times in sequence_solver, 7 in freeform, 4 each in routing_domain and compact_seed. There are 157 `time.monotonic()`/`perf_counter()` call sites in src (sequence_solver 37, freeform 32, pipeline 16, hierarchy/strategy 13, finalize 12). See P1.

**R3. Same-file structural twins.** dsp/catalog.py:1104 `_recipe_ids` and :1201 `_item_ids` (exact structure, 172 tokens); layout/slots.py:228 `slot_offset` and :874 `port_offset` (exact structure); lab/url.py:371 `_parse_recipes` and :393 `_parse_machines` (0.66); routing_domain.py:2697 `_Canvas.free` and :2742 `free_owned_guard` (0.64); validate.py:2136 `_belt_in_addon_area` and :5123 `_coater_supply_area_candidates` (0.60); finalize.py:1357 `_addon_grid_reach` and :1995 `of` (0.56). Each pair collapses to one parameterised helper; catalog and slots are trivial, the validate pair needs a shared "belt cells within an addon's area" helper.

**R4. Cell index codec, three encoders and two identical decoders.** Encode: routing_domain.py:4475 `_Grid.index`; global_router.py:464 `_live_index` repeats the same bounds check and then calls `grid.index`, which checks again. Decode: geometric_world.py:60 `GeometricWorld.cell` and global_router.py:574 `_decode_cell` are the same three lines (divmod by levels, then by row count). transport_routing/paths.py:251 `_key` and :273 `_decode` are a fourth encoding for that package's own `Domain`. Proposal: one `GridIndex` value (origin, ny, nz) with `encode`/`decode`, owned by geometric_world and used by `_Grid`, global_router and paths.

**R5. Hierarchy flow loops.** hierarchy/partition.py `_flow_between` (88), `derive_cuts` (170), `boundary_balances` (219), `sub_spec` (257) and hierarchy/pressure.py `cut_pressure` (70) each contain the same nested loop over `u.group.outputs_per_machine` / `inputs_per_machine`. One `group_flows(unit)` helper removes five copies; `boundary_balances` and `cut_pressure` are also dead (section 6).

**R6. Inventory preparation is layered, not duplicated.** routing_domain.py:15855 `_prepare_transport_inventory` (442 lines, "Share physical emission and directed producer allocation across routers") and transport_routing/inventory.py:73 `prepare_inventory` (189 lines, "Reuse physical emission and allocation, without reserving router access") have near-identical docstrings, but the latter calls the former (2 sites). No duplication; the cost is that a new package depends on a private function. Likewise junction.py (483 lines: `keepout_cells`, `make_splitter`, `splitter_route_candidates`, ...) and transport_routing/junctions.py (55 lines: `tree_extent`, `terminals`) do not overlap.

**R7. Race-winner selection has two paths.** strategy_race.py:1171 `RacingLayout` documents that `pipeline.build` does not use it and "picks by min(area) itself"; `RacingLayout` exists for the audit's `best` cell (scripts/audit.py, 3 references; 13 in tests). Two selection rules for one decision can drift; the 2026-09-08 general-abstraction spec explicitly defers normalising serial vs raced behavior, so this is recorded, not new.

## 4. Scans in loops

Method: for every `for`/`while` in src and scripts, inner loops and comprehensions whose iterable name ends in building/cell/net/strip/sorter/belt/port/path. 180 sites; 74 in routing_domain, 17 freeform, 16 validate, 15 hierarchy/partition, 8 transport_routing/solver.

Hot spots by function: `_route_all` 24 (inner iterables are `paths`/`through_path`, cells of the path being committed, linear in path length not buildings); `FreeformLayout._sweep` 10 (`strips` inside the height loop, tens of strips); `_prepare_transport_inventory` 9 (`_pair_lanes` called inside a loop over strips); `_route_all>_repair` 7; junction.py:404 `splitter_route_candidates` 5; transport_routing/solver.py:587 `select` 5 (`fixed_owners_by_cell[cell]`, dict lookups); validate.py:5790 `_physical_flow` 5; the hierarchy quartet from R5, 4 each.

Hand-rolled filters over a `Buildings` index that already has the query:
- routing_domain.py:13561 `for index, building in enumerate(self.buildings) if building.item_id == catalog.SPRAY_COATER_ID` (index has `by_item`).
- routing_domain.py:13594 `enumerate(self.buildings) if not catalog.is_belt(...) and not catalog.is_sorter(...)` (index has `by_kind`).
- routing_domain.py:17502 `for b in canvas.buildings: if not catalog.is_belt(b.item_id): continue` (index has `belts()`).
- routing_domain.py:15015 and :15066 in `_power_plan` scan every building once per plan to bucket by kind (a `by_kind` union would serve; not inside a loop).
`Buildings.in_box` is called at 2 sites (both routing_domain) though the class exposes 39 query methods; `for ... in X.buildings` loops number 15 in routing_domain, 14 in validate, 12 in finalize, 5 in compose, 3 in transport_routing/composition; most are single passes at phase boundaries.

Verdict: the "no linear or quadratic building scan inside a loop" rule held. No building-collection scan is nested inside a per-building or per-net loop. Backend discipline held: zero littletable/networkx imports outside `flab2bp.indexed`; twelve src modules consume indexed types (routing_domain, colliders, validate, sequence_solver, sequence_pair, route_feedback, projection_world, hierarchy/partition, hierarchy/contracts, provenance, bench/scoring, bench/regression). Unverified: 12 `.index(` calls in routing_domain outside `grid.index`, not checked for list-index-in-loop.

Session lead's addendum: the routing profile taken the same night found one scan the survey's iterable-name filter could not see, `_prebuilt_source_starts` walking about 70 siblings per call (633,000 path lookups per pass) to find the two or three whose selected tap matched; it was replaced with a reverse index of source hints plus the fixed declared-source map (cbdabd59). Scans keyed on nets rather than buildings deserve the same rule.

## 5. Parallel abstractions for one concept

**P1. Deadlines and budgets (must fix soon).**
- Exceptions for "clock ran out": routing_proposals.py:142 `Deadline(Exception)` (empty body), routing_domain.py:12077 `_PreparationDeadline` (carries `expansions`, `net_index`, `failures`), hierarchy/compose.py:248 `_PackingDeadline` (carries `packing`), transport_routing/budget.py:44 `TransportRefusal("DEADLINE")`.
- Deadline bundles: finalize.py:4444 `PlacementCompletionDeadlines(cleanup, projection, acceptance)`.
- Work ledgers: sequence_solver.py:430 `ExpansionBudget` (141 lines, deterministic ledger with reserve), transport_routing/budget.py:35 `WorkBudget` (deadline + injectable clock + per-kind limits + RSS check, raises typed `TransportRefusal`), freeform.py:3383 `_BuildBudgetStage`, sequence_solver.py:683 `_DeferredFeedbackBudget`, global_router.py:71 `_CapacityLedger`, sequence_alns.py:366 `_Ledger`, sequence_solver.py:4671 `_RelationNoGoodLedger`.
- Untyped ledger: routing_domain passes `budget: dict[str, int]` and mutates `budget["left"]` at 30 sites plus `search_budget["left"]` at 5; `_ROUTING_BUDGET = 2_000_000` (routing_domain.py:274) is a module constant that freeform.py:4531 rewrites at runtime through `routing_domain._ROUTING_BUDGET`.
- Helpers: the four one-liners from R2. `_geometric_search` (routing_domain.py:4671) takes `budget` dict + `deadline` + `history` + `pressure` + `blame` + `grid` + owned/released/forbidden + `blocking_owners` + `extra_edges`, 15 parameters.
Which absorbs which: `WorkBudget` is already the right shape. Make it layout-wide: `_geometric_search`, `_route_all`, compact_seed, finalize completion and hierarchy packing take a `WorkBudget` (or a read-only deadline view of it) instead of `deadline: float | None` plus `budget: dict`. The three exception types become one `BudgetExhausted` with an optional payload; `ExpansionBudget` keeps its reserve semantics but reads the same clock. Payoff: removes 4 helpers, 35 dict sites, the runtime rewrite of a module constant, and makes every deadline injectable in tests (today's 295 monkeypatches include per-module time patches). Cost: about 60 call sites in routing_domain and freeform, behavior-neutral if `left` semantics are preserved; test churn where tests build `budget={"left": n}` dicts.

**P2. Two classes named `GeometricWorld`.** layout/geometric_world.py:24 (kernel input: `nx, ny, nz, gx0, gy0, flags, history, transitions`, `from_grid`) and layout/projection_world.py:63 (canvas clearance oracle: `canvas, history, pressure, forbidden, owned_starts, released_starts, projection, counters, costs`, methods `source_clear`, `cell_clear`). Different concepts, same name, same package; global_router imports the first, routing_proposals the second. Rename the second (`ProjectionWorld` or `ClearanceOracle`). Cost: one rename, 3 importing modules.

**P3. Three search-result records for one kernel result.** geometric_router.py:73 `GeometricResult(kind, path, cost, reachable, co_reachable, metrics)` is adapted twice: routing_domain.py:4662 `_PathSearchResult(path, kind, wall, expansions)` and global_router.py:61 `_SearchResult(path, expansions, exhausted_budget, cancelled)`. Each adapter re-derives the outcome flags from `GeometricResult.kind`. One adapter removes one type and one mapping.

**P4. Canvas and grid representations crossing the router boundary.** `_Canvas` (routing_domain.py:2350, 21 fields; `clone` at :2764 copies 18 fields by hand, so every new field is a silent clone bug), `_Grid` (routing_domain.py:4420, flattened tile arrays `occ/base/hist` built by `_make_grid` per query), `GeometricWorld` (kernel view built from `_Grid` by `from_grid`), `PackedCanvas` (compose.py:210, wraps `canvas` plus buildings/nets/reservation), `Buildings`/`MutableBuildings` (buildings.py), and in scripts `CanvasSnapshot` (route_bench.py:66) and `snapshot_grid` (route_records.py:79). The chain canvas to `_Grid` to `GeometricWorld` is two conversions per query; scripts snapshot `_Grid` because it is the only serialisable form. Proposal: give `_Canvas` a `to_world()` producing the kernel view directly, make `_Grid` an implementation detail of it, and make `clone` field-drift-proof (`copy.copy` plus container refresh). Cost: `_Grid` is referenced by global_router (3 functions), three scripts and tests (`_make_grid` is monkeypatched in tests/layout/test_freeform.py at 20409, 20663, 24283).

**P5. Two `Verdict` classes** in bench/scoring.py:38 (A/B verdict) and bench/snaporacle.py:197 (snap-oracle record). Different concepts; rename one.

**P6. Prepared-problem objects.** `_PreparedRoutingProblem` (routing_domain.py:5038), `TemplateProblem` (transport_routing/paths.py:50), `PlacementProblem` (sequence_pair.py:81), `ClusterProblem` (last_mile.py:97) are genuinely different problems (detailed routing, template routing, placement, last-mile cluster); no merge proposed. The 2026-09-08 general-abstraction spec already names "strategy-neutral prepared routing" as a workstream.

## 6. Dead code and stale shims

Method: AST count of every use as Name, Attribute or string token across src, scripts and tests (string tokens catch `monkeypatch.setattr` names and registry strings), then decorator filtering (the `@check` rules in validate.py and pydantic validators in spec.py are registered by decorator, not by name, and were excluded; so were HTTP handler hooks and the `BinaryReader`/`BinaryWriter` `u16`/`u32` API). Six survivors were confirmed with Serena `find_referencing_symbols` returning no references.

Top-level, zero references, undecorated (12):
- src/flab2bp/__init__.py:1 `hello` (uv template leftover).
- bench/snaporacle.py:517 `to_tiles`, :526 `tile_gap`, :532 `Disagreement`.
- lab/schema.py:148 `Number`.
- layout/finalize.py:652 `_extent_fits` (Serena-confirmed).
- layout/freeform.py:200 `OUTER_MAX` (only a docs mention).
- layout/hierarchy/partition.py:206 `boundary_balances`, hierarchy/pressure.py:60 `cut_pressure` (docs mention only).
- layout/routing_domain.py:11460 `_belt_keepout_clear` (Serena-confirmed).
- layout/slots.py:499 `direct_anchors` (Serena-confirmed).
- layout/transport_routing/allocation.py:20 `ORDERS`.
- layout/validate.py:5964 `_belt_run_rate` (the one undecorated helper in that file with no caller).

Methods, zero references: routing_domain.py `Strip.input_is_shared` :820, `Strip.slot_of_input` :896 (Serena-confirmed), `Strip.east_of_input` :968; sequence_solver.py `ExpansionBudget.searchable_total` :457 (Serena-confirmed), `SequenceSearchResult.exact_energy` :892; strategy_race.py `RaceChannels.publish_no_good` :235; validate.py `Context.junctions_feeding` :386 (Serena-confirmed), `Context.runs_drawing_from_junction` :409; bench/types.py `CellResult.verified` :90; dsp/catalog.py `Building.has_explicit_slots` :1430; lab/schema.py `Module.is_productivity` :276, `Module.is_speed` :280, `Dataset.get_machine` :566, `Dataset.belt_ids` :602; rates/adjust.py `AdjustedRecipe.net_rate` :70; rates/solve.py `SolvedGroup.utilisation` :100.

Stale naming that describes the removed tile-A* design:
- "expansion": 270 hits in 12 files, while geometric_router.py:22 states "geometric work is not A* expansion count". Carriers: `_MAX_EXPANSIONS` (routing_domain.py:269), `_ROUTING_EXPANSIONS_PER_SECOND` (freeform.py:270, imported by sequence_solver), `ExpansionBudget` (sequence_solver.py:430), `B_LOW_LEVEL_EXPANSIONS` (last_mile.py:67), the `expansions` field on `_PathSearchResult`, `_SearchResult`, `_PreparationDeadline` and `ClusterResult`, and `_route_all>pending_expansion` (routing_domain.py:10245). The `_astar` rename is done; this is the other half of the same job.
- layout/route_kernel.py is 8 lines; its only function `selected_backend()` returns the literal `"geometric"` and is called at 3 sites (freeform.py:4682, sequence_solver.py:6539, :6654) to fill `stats["route_backend"]`. A backend switch with one position.
- "legacy": 34 hits in 10 files, including live symbols `_retain_legacy_blended_elite` (sequence_pair.py:2160), `_legacy_side_lane_caps` (strip_variants.py:1708), fields `legacy_source_strip/legacy_destination_strip/legacy_ordinal` (route_feedback.py:45-47), and docstrings calling Freeform "the legacy planner" (freeform.py:876, strip_variants.py:482, :2164). Either the old form is still a contract (then name it for what it is) or it is a compatibility path to retire.
- `RacingLayout` (strategy_race.py:1171) self-describes as a "shim"; used by scripts/audit.py and tests only.

Feature flags: `FLAB2BP_COATER_NODE` now has only `OFF` and `PLACED` (coater_mode.py:52-53); the seat and packed experiment arms were removed and `_coater_seats` is live in the PLACED path (called from `_place_coaters` at routing_domain.py:17728). No stale both-arms flag found. `FLAB2BP_COATER_TRACE` and `FLAB2BP_GEOMETRY_KERNEL` are diagnostics. Strategy names (pipeline.py:68-69) list four explicit strategies plus `best`; hierarchical is explicit-opt-in by design. TODO/FIXME/XXX/HACK count in src and scripts: 0.

One layering oddity: dsp/registry.py (1,274 lines) holds a `LintException` table naming layout symbols by string (`"flab2bp.layout.routing_domain"`, `"_geometric_search"`, `"_route_all"`, `"flab2bp.layout.transport_routing.composition"`, around lines 1097-1266). It is lint configuration living under `dsp/`, keyed on private names, so a rename in layout silently invalidates it.

## 7. Private-helper leakage

Counts (AST `from X import _name` plus attribute-style `module._name`):
- 308 private-name imports repo-wide: src 102, scripts 14, tests 192.
- routing_domain exports 50 distinct private names to other src modules by import (importers: strip_variants, route_primitives, projection_world, routing_proposals, sequence_solver, last_mile, hierarchy/compose, global_router, geometry_memo, geometric_world), plus freeform reads `routing_domain._x` at 88 sites (32 names) and transport_routing reads `rd._x` at 41 sites (17 private names, 1 public: `rd._Port` x17, `_lane_stacks_for` x3, `_adapt` x3, `_prepare_transport_inventory` x2, `_PreparationDeadline` x2, `_Pack` x2, `_Unpowerable`, `_sorter_tiers_for`, `_sorter_stacks_for`, `_relink`, `_proliferator_item`, `_power_reservation`, `_power_plan`, `_place_power`, `_place_coaters`, `_core_bounds`, `_Canvas`). The newest package is built almost entirely on private names of the module it was meant to be independent of.
- routing_domain reads 12 private finalize names at 81 sites (section 2.1).
- sequence_solver imports 19 private names from freeform (`_pack`, `_pack_window`, `_greedy_pack`, `_build_prepared`, `_direct_net_candidates`, `_ROUTING_EXPANSIONS_PER_SECOND`, ...) and 10 from routing_domain (`_ROUTING_BUDGET`, `_ENTRY_RING`, `_PreparationDeadline`, `_prepare_routing_problem`, ...).
- pipeline.py:718 imports `_validate_sequence_islands` from sequence_solver: the one private import across the layout boundary.
- Tests: 122 distinct `routing_domain._x` names at 883 sites; 59 `freeform._x` names at 235 sites; tests/layout/test_freeform.py alone imports 35 private names from routing_domain and 19 from freeform; test_strategy_race imports 12 from strategy_race; test_routing_height_domain 11 from routing_domain; test_sequence_solver 10 from sequence_solver. `monkeypatch.setattr` on private attributes: 295 (freeform 72, routing_domain 43, finalize 28, hierarchy/strategy 26, sequence_solver 26, compose 21, pipeline 13, cli 9, geometry_kernel 8).
- Scripts: route_profile.py rebinds `routing_domain._make_grid`, `_reserve_port_access` and `_Grid.refresh_history` via `type.__setattr__` (lines 481-493); last_mile_bench.py:32 imports `_geometric_search, _Grid, _PathSearchResult`; prepare_parity.py:26 imports `_greedy_pack, _height_seed`; web_smoke.py:57 imports `_ARGS, _await_devtools, _free_port` from lab.capture.

Most-shared private names inside src (promotion candidates): `_Canvas` (3 importers), then with 2 each `_StagedStaticCache`, `_Grid`, `_PreparedRoutingProblem`, `_routing_transitions`, `_ROUTING_BUDGET`, `_PreparationDeadline`, `_sorter_stacks_for`, `_sorter_tiers_for`, `_Unpowerable`, `_altitude_profile`, `_dests`, `_adapt`, `_Port`, `_Pack`, `_prepare_transport_inventory`, `_exact_key` and `_validate_sequence_islands` (sequence_solver).

## 8. Public surface

layout/__init__.py is empty (0 lines). Outside the layout package, src code imports: base (11 names: `LayoutStrategy`, `Placement`, `PlacedBuilding`, `PlacementStats`, `PlacementCompletion`, `NoValidLayout`, `SpecInfeasible`, `LayoutAttemptFailure`, `AreaFrame`, `ProjectionFailureRecord`, `ATOMIC_COMPLETION_GRACE_S`), observe_channel (6), observe (4), band_policy (3), buildings (`Buildings`, `Kind`), freeform (`FreeformLayout`), sequence_solver (`SequencePairLayout` and the private `_validate_sequence_islands`), hierarchy (`HierarchicalLayout`), transport_routing.strategy (`TransportRoutingLayout`), strategy_race (`RACE_COMPLETION_GRACE_S`), and the modules finalize, markers, strategy_race, validate. That is a coherent facade: four strategies, base types, observation. The discipline held here. The gap is internal: tests and scripts see 57 names from routing_domain, 35 from sequence_pair, 30 from freeform, 28 each from strategy_race and strip_variants, 24 from route_feedback, 22 from sequence_alns, nearly all underscore-prefixed, so "private" carries no information inside this package. Modules with a single src importer (a hint of where seams already exist): sequence_islands (sequence_solver), route_primitives (routing_domain), global_router (sequence_solver), transport_routing/runtime (strategy_race), hierarchy/compose and hierarchy/dispatch (hierarchy/strategy), transport_routing/composition (dsp/registry, by string only). global_router.py (578 lines) is the second Python driver of the geometric kernel besides `_geometric_search`, used only from sequence_solver.

## 9. Ranked findings (payoff-to-cost)

Must fix soon:
1. **P1 deadline/budget contract.** Adopt `WorkBudget` layout-wide; delete 4 helpers, 3 exception types, 35 dict sites and the runtime rewrite of `_ROUTING_BUDGET`. Payoff: the most-copied concept in the tree becomes one typed, injectable object. Cost ~60 sites, behavior-neutral if `left` semantics are preserved; medium test churn.
2. **Dead code (section 6).** 12 symbols and ~15 methods, ~400 lines. Cost near zero; re-run reference search including string references before deleting the six not Serena-confirmed.
3. **`_route_all` decomposition.** Lift the 61 closures into a `_RouteAllRun` object grouped by the existing clusters (search, commit, repair, last-mile), turning 22 nonlocal names into fields. Payoff: the largest testability win available (tests reach the closures today only through 883 private references). Cost high (~3,500 lines); do it in phases behind the route_bench MATCH line and the paired 72-cell guard, byte-identical on the audit.
4. **Finish the geometric rename.** "expansions" to "work"/"charges" across 270 sites; delete route_kernel.py and the `route_backend` stat or have it read geometric_router; rename projection_world's `GeometricWorld`. Mechanical with Serena `rename_symbol`; low risk.
5. **Promote the ~20 shared private names** to public, rewrite transport_routing, freeform and sequence_solver imports, then forbid new `from routing_domain import _x` outside the module with the same import-grep guard used for littletable. ~130 src sites; large but mechanical test churn (tests keep working through the renamed names).

Nice to have:
6. **R1 quaternion module.** ~150 lines removed, pure math, five functions in one file.
7. **R4 grid index codec** and **P3 one result adapter**: small, removes two duplicated decoders and one record type.
8. **routing_domain file split** along the measured seams: power (1,244 lines, 7 symbols) and coater/proliferator (1,654) first, port-access/corridor (979) second, then a `projection` module absorbing the staged-static cluster and finalize's projection cluster (3,700 lines together, currently coupled through 12 private finalize names). Wait for item 5 so the moves do not change 122 test import paths twice.
9. **R3 and R5 same-file twins** and the three hand-rolled Buildings filters (section 4). Trivial each.
10. **Parameter objects** for `SequenceSolver.__init__` (26), `pipeline.build` (25), `_record_routing_observation` (24), `_merge_frontier` (17), `_pack_window` (17): readability only; `_geometric_search` (15) shrinks by itself once P1 and P4 land.

Where the discipline held, with evidence: single kernel entry point (2 callers of `geometric_router.route` at routing_domain.py:4808 and global_router.py:545, 0 direct `_geometric_kernel` imports outside geometric_router); indexed backend guard (0 littletable/networkx imports outside `flab2bp.indexed`); no building scans nested in per-building loops; clean external facade for `flab2bp.layout`; no TODO/FIXME debt; the routing_domain split was an extraction with only 10 percent net growth of the pair; the coater experiment arms were removed rather than left behind a flag.

## 10. Commands and tools used

- Hindsight: `hindsight_list_knowledge_pages`; `hindsight_read_knowledge_page` for "Indexed scans", "Conventions and patterns", "Key decisions and rationale", "Component map" (the last three are empty, "I don't have information"). No CLAUDE.md exists in the repo; the two 2026-09-08 abstraction-repair specs were read for their headings and deferred-findings section.
- Serena: `get_current_config`; `get_symbols_overview` on routing_domain.py, freeform.py, transport_routing/{geometry,junctions,paths}.py, layout/geometry.py; `find_symbol` (body or depth 1) for `Deadline`, `_PreparationDeadline`, `_PackingDeadline`, `PlacementCompletionDeadlines`, `WorkBudget`, `ExpansionBudget`, both `GeometricWorld`, `_Canvas`, `PackedCanvas`, `_Grid`, `_Grid/index`, `GeometricWorld/cell`, `_decode_cell`, `_geometric_search`, `_expired`, `_deadline_reached`, `_completion_expired`, `_check_budget`, `check_deadline`, `_PathSearchResult`, `GeometricResult`, `_SearchResult`, `ClusterOutcome`, `ClusterResult`, `_prepare_transport_inventory`, `prepare_inventory`; `find_referencing_symbols` for `_belt_keepout_clear`, `Strip/slot_of_input`, `_extent_fits`, `direct_anchors`, `ExpansionBudget/searchable_total`, `Context/junctions_feeding`, `_coater_seats`, `_Grid`.
- Scratchpad scripts run with `.venv/bin/python` (read-only AST analysis): `survey.py` (module shape, private imports, zero-reference symbols and methods, exact and near-duplicate function detection, nested-scan detection, `FLAB2BP_*` env flags, stale-word counts) and `seams.py` (symbol clusters and cross-cluster references for the five largest modules, public-surface map, nested-scan detail), plus inline Python for decorator filtering of dead candidates, functions with more than 8 parameters, hand-rolled Buildings filters, `_route_all` closure sizes and nonlocal counts, per-module importer counts, and transport_routing import analysis.
- Shell via context-mode batch/execute: `wc -l` over src, scripts and tests; `git log`, `git show`, `git diff --numstat`, `git rev-list --count` against 249333917637 (2026-09-07) and 701945af (the routing_domain split) to attribute growth; `grep -rn`/`-c`/`-o` for class-name families (Budget, Deadline, Ledger, Canvas, Grid, World, Result, Outcome, Prepared, Problem, Verdict), `time.monotonic`/`perf_counter` sites, deadline helper definitions, `budget["left"]` keys, `FLAB2BP_*` names, stale words (astar, A*, expansion, legacy, compat, shim), `.buildings` loops, `.in_box(` callers, `Buildings(` constructions, `from flab2bp.indexed` importers, littletable/networkx imports, quaternion helper definitions, `monkeypatch.setattr` on private names, `routing_domain._x`/`freeform._x`/`finalize._x`/`rd._x` attribute references, `geometric_router.route` and `_geometric_kernel` callers, `route_kernel` users, `RacingLayout` references, strategy literals in pipeline.py, coater_mode values, TODO/FIXME counts; `sed -n` for short context windows; `vmstat 1 6 | tail -n 5 | awk` for the CPU load figure (4.6).
- No tests, builds, edits, commits or Serena editing tools were used by the review.

## 11. What this session already did in this direction (session lead)

- Renamed the misnamed search wrapper and its profiler labels (d6eb038d).
- Added `JunctionAdmissionMemo` as a small focused module rather than another closure inside `_route_all`, with version counters on the indexed types it reads (fab4af41).
- Replaced a per-call 70-way sibling scan with two indexes (cbdabd59).
- Preserved gate verdicts that lived only on retired branches and deleted 94 stale branches.
- Two of the ranked items are touched by that night's routing work and should be sequenced with it: the deadline/budget contract (item 1) is the code the routing-clock profile lives inside, and the `_route_all` decomposition (item 3) would absorb the new memo as a field rather than a closure-local.
