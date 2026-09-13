# Frontier admission memo: design

Status: Phase 1 implemented 2026-09-13 on branch `frontier-admission-memo`
(`JunctionAdmissionMemo` in `flab2bp/layout/junction_admission.py`, wired
into `_can_junction`), plus the sibling index in `_prebuilt_source_starts`.

## Outcome so far (2026-09-13)

- Validation instead of listeners: an entry pins the inputs it read and a
  lookup re-reads the cheap ones; `PortReservations.version` and
  `StakedPaths.version` plus a log of changed taps make the common case a
  few integer comparisons. Denied-by-reservation answers are recomputed.
- The net's own routing ports had to leave the key: with them in it, no
  cross-net hit ever happened (predicate time unchanged at 2.86 s). With
  ports moved into validation the hit rate is 85 %.
- Measured on output-products at 15 s: frontier 4.77 s to 4.04 s, predicate
  2.93 s to 2.50 s, whole `_route_all` 11.2 s to 10.7 s. Both sprayed
  Universe cells still refuse at 15 s; the 180-cell audit shows 167/13 with
  no status change other than the two typed-pack improvements.
- The residual inside the predicate is the projected-frame proof
  (`_CompositionProjection.allows`), a whole-selection property with its
  own verdict cache; it cannot be made cell-local and is not memoised here.
- Exact repeats: 172 of 1,037 frontier calls per pass have identical
  inputs and cost 1.9 s, all on the source side, produced by `_ends`
  recursing with a widened `project_taps`. Per-cell answers already hit the
  memo; the walk repeats. A walk-level memo is the next lever, worth about
  as much again as Phase 1.

## Why

The two remaining sprayed Universe Freeform refusals at 15 s are routing-clock
refusals. Tonight's profile of `universe-matrix` output-products (master
d6eb038d, `scripts/route_profile.py --budget 15 --workers 8`) puts the time
here:

| Stage                                   | Seconds |
| --------------------------------------- | ------: |
| Wall                                    |    15.0 |
| `_route_all`                            |    11.2 |
| Geometric kernel, 732 searches          |    3.45 |
| `_merge_frontier` (source-side frontier)|    4.60 |
| `_prebuilt_source_starts`               |   ~1.9 |
| `_reserve_port_access`                  |    1.11 |
| Preparation before routing              |    2.91 |

The kernel is not the cost. The Python that computes each net's start cells
is: for every net, `_ends` walks every already-routed sibling's path and asks
`_can_junction` whether a splitter could stand on each cell. Output-products
was 3 nets short at the deadline, all-products 17.

## What was measured

Probe: wrap `_merge_frontier` and its `junctionable` predicate
(`scratchpad/probe_frontier.py`, `probe_epochs.py`; same cell, same settings,
`PYTHONHASHSEED=0`).

| Cell            | Frontier calls | Sibling cells walked | Predicate calls | Distinct cells | Repeat | Cells whose answer ever flips |
| --------------- | -------------: | -------------------: | --------------: | -------------: | -----: | ----------------------------: |
| output-products |          1,269 |              313,883 |         230,485 |          6,336 |  36.4x |                    681 (10.7 %) |
| all-products    |          1,261 |              555,312 |         492,010 |          5,762 |  85.4x |                    830 (14.4 %) |

Seconds inside the predicate: 2.93 (output-products), 3.43 (all-products).
The walk itself is the remainder of `_merge_frontier`: about 1.8 s and 2.4 s.

Routing state changes (a staked or unstaked path, a reservation, a guard
cell) 427 times across the 1,269 frontier calls on output-products, so about
three frontier calls happen between consecutive state changes. A memo that
forgets everything on every state change would still answer 47.4 % of
predicate calls from cache and skip 62.6 % of sibling walks. A memo that
forgets only entries whose inputs actually changed would answer close to
90 %, because only 10 to 14 % of cells ever change their answer in a pass.

Expected saving on output-products: about 2.5 s with whole invalidation,
about 4 s with exact invalidation, out of a 15 s wall where the cell was 3
nets short. All-products scales the same way and was 17 nets short.

## What `_can_junction` reads

From `routing_domain._route_all/_can_junction` (line 6540 on d6eb038d):

1. `planned_taps` at the cell (two or more taps refuse; the guard exception
   reads it too).
2. `canvas.guard` membership of the cell, unless `relaxed_junctions`.
3. Peer taps within Chebyshev distance 3 of the cell, from
   `JunctionNeighborhood(planned_taps).nearby(cell)`, each tested with
   `_junction_stacks_collide(peer, cell)`.
4. `junction_ok[cell]`: physical clearance and power; already memoised for
   the whole pass, correct because it depends only on static geometry.
5. When `project` is set and `canvas.junction_projection` exists:
   `primitives.selection(paths, (*planned_taps, cell))` and
   `canvas.projected_buildings_are_clear(...)`. Depends on every staked path
   and every planned tap.
6. `junction_frame_bans` (constant per pass) against the cell and
   `planned_taps`.
7. For each keep-out cell of the splitter stack at the cell:
   `canvas.reserved.get(guard_cell)` compared with `canvas.routing_ports`
   (the current net's own ports), and on denial the corridor lookup in
   `canvas.port_corridors` that feeds the side effect
   `junction_reservation_blockers.update(...)`.

So the answer for a cell is a function of: the cell, `project`,
`routing_ports`, `planned_taps` restricted to distance 3, `canvas.guard` at
the cell, `canvas.reserved` at the cell's keep-out cells, `port_corridors`
for those reservations, and (for `project`) all staked paths and taps.

## Design

### Where the state changes

All of the inputs above mutate through a small number of sites:

- `StakedPaths.stake`, `unstake`, `restore` (`flab2bp/indexed/staked_paths.py`).
- `PortReservations.__setitem__`, `__delitem__`, `clear`, `popitem`
  (`flab2bp/indexed/port_reservations.py`).
- `canvas.guard`: five sites in `routing_domain.py` (6725, 6769, 6799, 11719,
  18634).
- `planned_taps`: four sites in `routing_domain.py` (6704, 8663, 8669, 8670).
- `canvas.port_corridors`: six sites, all inside the port-access reservation
  owner (9600 to 9760).

### The memo

A `FrontierAdmissionMemo` owned by `_route_all` (one per routing pass, like
`junction_ok`), keyed by `(cell, project, routing_ports)`, storing the boolean
answer and the frozenset of blockers the call contributed, so a hit replays
the side effect exactly.

Invalidation is by exact dependency, not by radius:

- `PortReservations` gains a `version` counter and an optional observer;
  every mutation calls `memo.reservation_changed(cell)`, which drops entries
  whose keep-out set contains `cell`. Keep-out sets per cell are pure
  geometry and are computed once per cell (`_splitter_stack_geometry` plus
  `junction.keepout_cells`), which also removes that recomputation from the
  hot path.
- The four `planned_taps` sites call `memo.tap_changed(tap)`, which drops
  entries for cells within Chebyshev distance 3 of `tap` on every level, the
  exact window the peer test uses, and drops every `project=True` entry.
- The five `canvas.guard` sites call `memo.guard_changed(cell)`, which drops
  the entries for that cell.
- `StakedPaths.stake`, `unstake` and `restore` bump a version that drops
  every `project=True` entry, since the projected selection reads all paths.
- Any `port_corridors` mutation drops every entry that recorded blockers.

`project=True` entries are therefore short-lived; the measured 439 to 1,500
projection calls per pass may stay mostly uncached. Whether that matters is
the first thing Phase 2 measures.

### The walk

`_merge_frontier` walks every sibling path cell before asking the predicate.
Its per-sibling output depends on the same state plus the call's own
arguments (`path_ranges`, `merged_cells`, `protected_sinks`, `owned_guard`,
`witness`, `admit_tap`, `tentative_ok`, `source_feeds`). Phase 1 leaves the
walk alone. Phase 2 may memoise the per-sibling candidate list before the
predicate (the `free` neighbour computation and `_junction_belt_clear`) keyed
by the sibling's path version, if the predicate memo alone does not clear
output-products.

## Phases and gates

1. Phase 1: predicate memo with exact invalidation as above, behind no flag
   (it is a pure cache; a flag would only hide a correctness bug). Gate:
   `tests/layout/test_freeform.py`, `test_routing_lifecycle.py`,
   `test_routing_integration.py`, `test_route_witnesses.py` green; the
   route_profile tally on output-products shows predicate seconds down by at
   least 60 %; the 180-cell stress audit at 15 s against a master control arm
   shows no status change other than Universe cells improving and no area
   change outside run-to-run variance (super-magnetic-ring freeform moved
   between 2,146 and 2,220 on identical code tonight).
2. Phase 2: measure real seconds inside `projected_buildings_are_clear` from
   the predicate and inside the sibling walk; memoise whichever is larger.
3. Phase 3, only if still short: move the peer-collision test into
   `JunctionNeighborhood` (Cython) so `nearby` returns only colliding peers.

## Correctness notes

- A stale "admitted" answer would offer a cell `_tap_source` later refuses,
  which discards the whole pack. Exact invalidation, driven from the
  mutators rather than from a radius, is the reason this design is safe. Any
  new mutation site for the five inputs must notify the memo; a test should
  monkeypatch each mutator and assert the memo forgets.
- Determinism: the memo never changes an answer, only when it is recomputed,
  so routes are byte-identical. The gate compares areas to check that.
- The blockers side effect must be replayed on hits, or repair loses the
  reservation blame it uses to pick which net to move.

## Not in scope

Parallel routing, kernel changes, any deadline or worker change.
