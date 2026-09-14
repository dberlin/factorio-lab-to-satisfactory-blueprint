from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from time import monotonic

import pytest

from flab2bp.dsp import catalog
from flab2bp.layout import finalize, junction, routing_domain, slots, validate
from flab2bp.layout.band_policy import BandPolicy
from flab2bp.layout.base import PlacedBuilding, Placement
from flab2bp.layout.budget import WorkBudget
from flab2bp.layout.route_feedback import (
    DetailedRouteStatus,
    NetId,
    NetRole,
    RouteFailureKind,
    RouteInteriorDetour,
    RouteSettlementCancelled,
    RouteSettlementCompleted,
    RouteSettlementCrashed,
    RouteSettlementRefused,
)
from flab2bp.layout.route_primitives import RouteOwnership, RoutePrimitives


def test_connector_witness_preserves_flat_sibling_merge_and_branch_docks() -> None:
    canvas = routing_domain._Canvas(
        belt_rules=replace(
            routing_domain._DEFAULT_BELT_RULES,
            max_z=Fraction(3),
            vertical_construction=False,
        )
    )
    path = (
        (0, 3, 1),
        (0, 2, 1),
        (0, 1, 2),
        (0, 1, 3),
        (0, 2, 3),
        (0, 3, 3),
        (0, 4, 3),
    )
    connector = next(
        candidate
        for candidate in junction.splitter_route_candidates(
            0, 0, 2, yaw=0.0, altitude_rules=canvas.belt_rules, carries_item="iron-ore"
        )
        if candidate.entry.dock == path[2] and candidate.exit.dock == path[3]
    )
    primitives = RoutePrimitives(canvas.belt_rules)
    primitives.witnesses[path[2], path[3]] = connector
    canvas.guard.update(path)
    canvas.guard.update(primitives.guards(path))

    merges = routing_domain._merge_frontier(
        canvas,
        {0: path},
        (0,),
        request=routing_domain._MergeFrontierRequest(primitives=primitives),
    )
    # Extend the same flat suffix beyond the approach below it: a source tap
    # at y=3 would collide with the incoming level-1 belt through its support.
    branch_path = (*path, (0, 5, 3), (0, 6, 3))
    canvas.guard.update(branch_path)
    branches = routing_domain._merge_frontier(
        canvas,
        {0: branch_path},
        (0,),
        lambda x, y, _level: junction.site_is_clear(canvas.buildings, x, y),
        routing_domain._MergeFrontierRequest(
            belt_prefab=(2002, catalog.building(2002).model_index),
            primitives=primitives,
        ),
    )

    # The ramp before the physical transfer must not erase the far flat carry
    # run. Merges attach on plane 3; model 40 source branches use plane 2.
    assert {(-1, 3, 3), (1, 3, 3)} <= merges
    assert {(-1, 5, 2), (1, 5, 2)} <= branches


def _single_route_scene():
    canvas = routing_domain._Canvas(limit=(-2, -2, 8, 8))
    source = canvas.add(PlacedBuilding(2001, 35, 0, 0, carries_item="iron-ore"))
    sink = canvas.add(PlacedBuilding(2001, 35, 6, 0, carries_item="iron-ore"))
    net = routing_domain._Net(
        routing_domain._Port(source, 0, 0, 0, 0),
        routing_domain._Port(sink, 6, 0, 6, 6),
        "iron-ore",
        net_id=NetId(0, 1, "iron-ore", NetRole.INTERNAL, 0),
    )
    return canvas, net


def _assert_linked(canvas, net):
    current = net.source.belt
    visited = set()
    while current != net.dst.belt:
        assert current not in visited
        visited.add(current)
        current = canvas.buildings[current].output_obj
        assert current is not None


def test_accepted_settlement_hands_off_exact_linked_candidate_once() -> None:
    canvas, net = _single_route_scene()
    accepted = []

    def settle(workspace, owners):
        _assert_linked(workspace, net)
        assert owners[net.source.belt] == owners[net.dst.belt] == frozenset((net.net_id,))
        placement = Placement(tuple(slots.assign_belt_slots(workspace.buildings)))
        report = validate.validate(
            placement,
            only=["geom.belt_single_occupancy"],
            expect_power=False,
        )
        assert not report.errors
        result = RouteSettlementCompleted(
            finalize.PlacementCompleted(
                placement, report, finalize.PlacementCompletionTimings(0, 0)
            ),
        )
        accepted.append((workspace.buildings, result))
        return result

    result = routing_domain._route_all(
        canvas,
        [net],
        2001,
        35,
        canvas.limit,
        budget=WorkBudget(left=20_000),
        settle=settle,
    )
    assert result.status is DetailedRouteStatus.ROUTED
    assert len(accepted) == 1
    assert result.settlement is accepted[0][1]
    assert canvas.buildings is accepted[0][0]
    _assert_linked(canvas, net)


def test_zero_net_selection_reaches_settlement_and_cancellation_stops() -> None:
    canvas = routing_domain._Canvas(limit=(-2, -2, 2, 2))
    cancelled = RouteSettlementCancelled("projection")
    calls = []

    def settle(workspace, owners):
        calls.append((tuple(workspace.buildings), owners))
        return cancelled

    result = routing_domain._route_all(canvas, [], 2001, 35, canvas.limit, settle=settle)
    assert calls == [((), ())]
    assert result.settlement is cancelled
    assert result.status is DetailedRouteStatus.BUDGET
    assert not result.exhaustive


@pytest.mark.parametrize(
    "outcome", (RouteSettlementCancelled("power"), RuntimeError("settlement failed"))
)
def test_settlement_cancellation_and_exception_do_not_try_another_candidate(
    outcome: RouteSettlementCancelled | RuntimeError,
) -> None:
    canvas, net = _single_route_scene()
    calls = []

    def settle(workspace, owners):
        _assert_linked(workspace, net)
        calls.append(owners)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = routing_domain._route_all(
        canvas,
        [net],
        2001,
        35,
        canvas.limit,
        budget=WorkBudget(left=20_000),
        settle=settle,
    )
    assert len(calls) == 1
    assert not result.exhaustive
    if isinstance(outcome, Exception):
        assert isinstance(result.settlement, RouteSettlementCrashed)
        assert result.settlement.error is outcome
        assert result.settlement.traceback is not None
    else:
        assert result.settlement is outcome
        assert result.status is DetailedRouteStatus.BUDGET


def test_partial_finish_emits_surviving_route_without_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canvas, net = _single_route_scene()
    source = canvas.add(PlacedBuilding(2001, 35, 0, 6, carries_item="iron-ore"))
    sink = canvas.add(PlacedBuilding(2001, 35, 6, 6, carries_item="iron-ore"))
    missing = routing_domain._Net(
        routing_domain._Port(source, 0, 6, 0, 0),
        routing_domain._Port(sink, 6, 6, 6, 6),
        "iron-ore",
        net_id=NetId(2, 3, "iron-ore", NetRole.INTERNAL, 1),
    )
    original = routing_domain._geometric_search

    def search(*args, **kwargs):
        if (0, 6, 0) in args[0].routing_ports:
            return routing_domain._PathSearchResult(None, RouteFailureKind.DYNAMIC_ACCESS, (), 0)
        return original(*args, **kwargs)

    monkeypatch.setattr(routing_domain, "_geometric_search", search)
    monkeypatch.setattr(routing_domain, "RRR_MAX", 1)
    monkeypatch.setattr(routing_domain.last_mile, "B_MAX_STRANDED", 0)

    def settle(_workspace, _owners):
        pytest.fail("partial routing must not invoke settlement")

    result = routing_domain._route_all(
        canvas,
        [net, missing],
        2001,
        35,
        canvas.limit,
        budget=WorkBudget(left=20_000),
        settle=settle,
    )
    assert result.routed == (net.net_id,)
    assert result.stranded == (missing.net_id,)
    assert result.settlement is None
    _assert_linked(canvas, net)
    assert canvas.buildings[source].output_obj is None


def test_budget_exit_materializes_selected_partial_without_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canvas, net = _single_route_scene()
    source = canvas.add(PlacedBuilding(2001, 35, 0, 6, carries_item="iron-ore"))
    sink = canvas.add(PlacedBuilding(2001, 35, 6, 6, carries_item="iron-ore"))
    missing = routing_domain._Net(
        routing_domain._Port(source, 0, 6, 0, 0),
        routing_domain._Port(sink, 6, 6, 6, 6),
        "iron-ore",
        net_id=NetId(2, 3, "iron-ore", NetRole.INTERNAL, 1),
    )
    original = routing_domain._geometric_search
    expired = False
    now = monotonic()

    def search(*args, **kwargs):
        nonlocal expired
        result = original(*args, **kwargs)
        if result.path is not None:
            expired = True
        return result

    def settle(_workspace, _owners):
        pytest.fail("an expired partial cannot invoke full settlement")

    monkeypatch.setattr(routing_domain, "_geometric_search", search)
    monkeypatch.setattr(routing_domain.time, "monotonic", lambda: now + 2.0 if expired else now)
    result = routing_domain._route_all(
        canvas,
        [net, missing],
        2001,
        35,
        canvas.limit,
        budget=WorkBudget(left=20_000),
        deadline=now + 1.0,
        settle=settle,
    )
    assert result.status is DetailedRouteStatus.BUDGET
    assert result.routed == (net.net_id,)
    assert result.stranded == (missing.net_id,)
    assert result.failures[0].kind is RouteFailureKind.BUDGET
    assert result.settlement is None
    _assert_linked(canvas, net)
    assert canvas.buildings[source].output_obj is None


def test_shared_reused_source_keeps_distinct_attachment_causes() -> None:
    canvas = routing_domain._Canvas()
    feeder = canvas.add(PlacedBuilding(2001, 35, -1, 0, z=Fraction(1), output_obj=1))
    source = canvas.add(PlacedBuilding(2001, 35, 0, 0, z=Fraction(1), output_obj=2))
    onward = canvas.add(PlacedBuilding(2001, 35, 1, 0, z=Fraction(1)))
    first = canvas.add(PlacedBuilding(2001, 35, 0, -1))
    owner_a = NetId(0, 1, "iron-ore", NetRole.INTERNAL, 0)
    owner_b = NetId(0, 2, "iron-ore", NetRole.INTERNAL, 1)
    ownership = RouteOwnership(len(canvas.buildings))
    ownership.attribute(len(canvas.buildings), (feeder, source, onward), owner_a)
    assert routing_domain._tap_source(
        canvas,
        source,
        first,
        2001,
        35,
        excused={(-1, 0, 1), (0, 0, 1), (1, 0, 1), (0, -1, 0)},
        ownership=ownership,
        net_id=owner_a,
    )
    original_attachments = tuple(range(4, len(canvas.buildings)))
    second = canvas.add(PlacedBuilding(2001, 35, 0, 1))
    assert routing_domain._tap_source(
        canvas,
        source,
        second,
        2001,
        35,
        ownership=ownership,
        net_id=owner_b,
    )
    owners = ownership.snapshot()
    expected = frozenset((owner_a, owner_b))
    assert all(owners[index] == expected for index in original_attachments)
    assert owners[first] == owners[second] == expected
    wired = slots.assign_belt_slots(canvas.buildings)
    assert not routing_domain.splitter_ports.placement_issues(wired)
    attachments = [
        index
        for index, building in enumerate(wired)
        if building.input_obj == canvas.buildings[source].output_obj
    ]
    assert len(attachments) == 3
    assert len({(wired[index].x, wired[index].y) for index in attachments}) == 1


def test_interior_detour_is_spent_before_endpoints_without_becoming_a_cell_ban() -> None:
    start, alternate, goal, interior = (1, 0, 0), (1, 1, 0), (6, 0, 0), (3, 0, 0)
    tap, other_tap = (0, 0, 0), (0, 1, 0)
    starts, goals = [start, alternate], {goal}
    offers = ({start: tap, alternate: other_tap}, {}, {})
    detour = frozenset({interior})
    proposal = routing_domain._RouteProposal((tap, None), None, detours={detour: None})
    construction = ((7, ((3, 2, 0), (4, 2, 0))),)
    changed = ((7, ((3, 3, 0), (4, 3, 0))),)

    assert proposal.restrict(starts, goals, offers, construction=construction) == (
        starts,
        goals,
        detour,
        True,
    )
    assert proposal.restrict(starts, goals, offers, construction=construction) == (
        [alternate],
        goals,
        frozenset(),
        True,
    )
    assert proposal.restrict(starts, goals, offers, construction=construction) == (
        starts,
        goals,
        frozenset(),
        False,
    )
    # A new physical construction gets its own positive attempt; its ordinary
    # source and original interior remain eligible when that attempt is spent.
    assert proposal.restrict(starts, goals, offers, construction=changed)[2] == detour
    proposal.restrict(starts, goals, offers, construction=changed)
    assert proposal.restrict(starts, goals, offers, construction=changed) == (
        starts,
        goals,
        frozenset(),
        False,
    )
    assert proposal.restrict(starts, goals, offers, construction=construction) == (
        starts,
        goals,
        frozenset(),
        False,
    )
    # Restaking can expose the previously interior support as source access.
    # Even the new context's first positive branch must not suppress it.
    restored_offers = ({interior: tap}, {}, {})
    assert proposal.restrict([interior], goals, restored_offers, construction=changed) == (
        [interior],
        goals,
        frozenset(),
        False,
    )


@pytest.mark.parametrize("complete_ownership", [True, False])
def test_settlement_interior_hint_reroutes_linked_path_with_unchanged_endpoints(
    complete_ownership: bool,
) -> None:
    canvas, net = _single_route_scene()
    implicated = frozenset({net.net_id})
    candidates = []
    stopped = RouteSettlementCancelled("interior-alternative-observed")
    interior = None

    def settle(workspace, owners):
        nonlocal interior
        _assert_linked(workspace, net)
        walk = []
        index = workspace.buildings[net.source.belt].output_obj
        while index != net.dst.belt:
            building = workspace.buildings[index]
            walk.append((building.x, building.y, int(building.z)))
            assert net.net_id in owners[index]
            index = building.output_obj
        candidates.append(tuple(walk))
        if interior is None:
            interior = walk[len(walk) // 2]
        elif interior not in walk:
            return stopped
        return RouteSettlementRefused(
            "projected interior support",
            implicated if complete_ownership else None,
            interior_detours=(RouteInteriorDetour(implicated, frozenset({interior})),),
        )

    result = routing_domain._route_all(
        canvas, [net], 2001, 35, canvas.limit, budget=WorkBudget(left=20_000), settle=settle
    )
    assert result.settlement is stopped
    assert interior not in candidates[-1]
    assert not result.exhaustive


def test_failed_interior_proposal_keeps_ordinary_path_in_existing_repair_slots() -> None:
    canvas = routing_domain._Canvas(
        limit=(0, 0, 6, 0),
        belt_rules=replace(routing_domain._DEFAULT_BELT_RULES, max_z=Fraction(0)),
    )
    source = canvas.add(PlacedBuilding(2001, 35, 0, 0, carries_item="iron-ore"))
    sink = canvas.add(PlacedBuilding(2001, 35, 6, 0, carries_item="iron-ore"))
    net = routing_domain._Net(
        routing_domain._Port(source, 0, 0, 0, 0),
        routing_domain._Port(sink, 6, 0, 6, 6),
        "iron-ore",
        net_id=NetId(0, 1, "iron-ore", NetRole.INTERNAL, 0),
    )
    implicated = frozenset({net.net_id})
    candidates = []
    stopped = RouteSettlementCancelled("ordinary-path-restored")

    def settle(workspace, owners):
        _assert_linked(workspace, net)
        candidates.append(tuple(workspace.buildings))
        if len(candidates) > 1:
            return stopped
        return RouteSettlementRefused(
            "contextual support in the only corridor",
            implicated,
            interior_detours=(RouteInteriorDetour(implicated, frozenset({(3, 0, 0)})),),
        )

    result = routing_domain._route_all(
        canvas, [net], 2001, 35, canvas.limit, budget=WorkBudget(left=20_000), settle=settle
    )
    assert result.settlement is stopped
    assert candidates[0] == candidates[-1]
    assert not result.exhaustive


def test_cluster_keeps_ordinary_paths_after_a_second_routes_detour_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing_domain, "_COMMIT_REPAIR_PASSES", 2)
    monkeypatch.setattr(routing_domain, "RRR_MAX", 1)
    canvas = routing_domain._Canvas(
        limit=(0, 0, 6, 2),
        belt_rules=replace(routing_domain._DEFAULT_BELT_RULES, max_z=Fraction(0)),
    )
    canvas.guard.update((x, 1, 0) for x in range(7))
    nets = []
    for ordinal, y in enumerate((0, 2)):
        source = canvas.add(PlacedBuilding(2001, 35, 0, y, carries_item="iron-ore"))
        sink = canvas.add(PlacedBuilding(2001, 35, 6, y, carries_item="iron-ore"))
        nets.append(
            routing_domain._Net(
                routing_domain._Port(source, 0, y, 0, 0),
                routing_domain._Port(sink, 6, y, 6, 6),
                "iron-ore",
                net_id=NetId(ordinal * 2, ordinal * 2 + 1, "iron-ore", NetRole.INTERNAL, ordinal),
            )
        )
    implicated = frozenset(net.net_id for net in nets)
    candidates = []
    stopped = RouteSettlementCancelled("both-ordinary-paths-restored")

    def settle(workspace, owners):
        for net in nets:
            _assert_linked(workspace, net)
        candidates.append(tuple(workspace.buildings))
        if len(candidates) > 1:
            return stopped
        return RouteSettlementRefused(
            "positive supports in two independent single corridors",
            implicated,
            interior_detours=tuple(
                RouteInteriorDetour(frozenset({net.net_id}), frozenset({(3, net.source.y, 0)}))
                for net in nets
            ),
        )

    result = routing_domain._route_all(
        canvas, nets, 2001, 35, canvas.limit, budget=WorkBudget(left=20_000), settle=settle
    )
    assert result.settlement is stopped
    assert candidates[0] == candidates[-1]
    assert not result.exhaustive


def test_positive_proposal_uses_both_source_and_guard_without_banning_shared_cells() -> None:
    original, alternate, other = (1, 0, 0), (2, 0, 0), (3, 0, 0)
    tap, second_tap = (0, 0, 0), (4, 0, 0)
    goal = (6, 0, 0)
    starts = [original, alternate, other]
    offers = (
        {original: tap, alternate: tap, other: second_tap},
        {},
        {original: tap, other: second_tap},
    )
    proposal = routing_domain._RouteProposal((tap, tap), None)
    selected, goals, constraints, changed = proposal.restrict(
        starts, {goal}, offers, construction=()
    )
    assert changed and selected == [alternate] and goals == {goal} and not constraints
    assert starts == [original, alternate, other]
    # Restaking an upstream changes current offers. The previous omitted tap
    # is available again; no stale endpoint/cell exclusion exists.
    restored = ({original: second_tap}, {}, {original: tap})
    assert proposal.restrict([original], {goal}, restored, construction=()) == (
        [original],
        {goal},
        frozenset(),
        True,
    )


def test_consumed_proposal_restores_new_offer_and_restaked_companion_choices() -> None:
    start_a, start_b, start_c = (1, 0, 0), (2, 0, 0), (3, 0, 0)
    tap_a, tap_b, tap_c = (0, 0, 0), (0, 1, 0), (0, 2, 0)
    goal = (6, 0, 0)
    proposal = routing_domain._RouteProposal((tap_a, tap_a), None)
    offers_ab = ({start_a: tap_a, start_b: tap_b}, {}, {start_a: tap_a, start_b: tap_b})
    offers_ac = ({start_a: tap_a, start_c: tap_c}, {}, {start_a: tap_a, start_c: tap_c})
    companion = ((0, ((0, 0, 0), (1, 0, 0))),)
    restaked = ((0, ((0, 0, 0), (0, 1, 0))),)

    assert proposal.restrict(
        [start_a, start_b],
        {goal},
        offers_ab,
        construction=companion,
    ) == ([start_b], {goal}, frozenset(), True)
    # B was consumed at position zero. A newly exposed C must not inherit
    # position one's exhaustion, even before considering companion changes.
    assert proposal.restrict(
        [start_a, start_c],
        {goal},
        offers_ac,
        construction=companion,
    ) == ([start_c], {goal}, frozenset(), True)
    assert proposal.restrict(
        [start_a, start_b],
        {goal},
        offers_ab,
        construction=restaked,
    ) == ([start_b], {goal}, frozenset(), True)
    # Restoring an identical old context restores its progress, not a fresh
    # retry of B. None of these positive selections mutate endpoint offers.
    assert proposal.restrict(
        [start_a, start_b],
        {goal},
        offers_ab,
        construction=companion,
    ) == ([start_a, start_b], {goal}, frozenset(), False)
    assert offers_ab[0] == {start_a: tap_a, start_b: tap_b}


def test_unchanged_proposal_context_advances_sources_then_destinations() -> None:
    starts = [(1, 0, 0), (2, 0, 0), (3, 0, 0)]
    taps = [(0, 0, 0), (0, 1, 0), (0, 2, 0)]
    direct_goal, alternate_goal, sink = (6, 0, 0), (6, 1, 0), (7, 1, 0)
    goals = {direct_goal, alternate_goal}
    offers = (dict(zip(starts, taps, strict=True)), {alternate_goal: sink}, {})
    proposal = routing_domain._RouteProposal((taps[0], None), None)

    assert proposal.restrict(starts, goals, offers, construction=()) == (
        [starts[1]],
        goals,
        frozenset(),
        True,
    )
    assert proposal.restrict(starts, goals, offers, construction=()) == (
        [starts[2]],
        goals,
        frozenset(),
        True,
    )
    assert proposal.restrict(starts, goals, offers, construction=()) == (
        starts,
        {alternate_goal},
        frozenset(),
        True,
    )
    assert proposal.restrict(starts, goals, offers, construction=()) == (
        starts,
        goals,
        frozenset(),
        False,
    )


def test_proposal_progress_tracks_actual_stakes_guards_and_canonical_witnesses() -> None:
    canvas = routing_domain._Canvas(limit=(-3, -3, 10, 6))
    grid = routing_domain._make_grid(canvas, canvas.limit, (-5, -5, 12, 8), {})
    paths = routing_domain.StakedPaths(routing_domain._STEPS)
    owner = {}
    primitives = RoutePrimitives(canvas.belt_rules)
    start_a, start_b, goal = (1, 0, 0), (2, 0, 0), (6, 0, 0)
    tap_a, tap_b = (0, 0, 0), (0, 1, 0)
    starts, goals = [start_a, start_b], {goal}
    offers = ({start_a: tap_a, start_b: tap_b}, {}, {start_a: tap_a, start_b: tap_b})
    # `_restrict_proposal` is a method of the run object now, not a closure of
    # `_route_all`, so the fifteen names it used to capture are fields filled
    # here instead of cells fabricated from `co_freevars`.
    run = routing_domain._RouteAllRun(budget=WorkBudget(left=0), deadline=None)
    run.proposals = {1: routing_domain._RouteProposal((tap_a, tap_a), None)}
    run.corridor_reservations = routing_domain._CorridorReservations(canvas, grid, owner)
    run.paths = paths
    run.source_hint = {}
    run.sink_hint = {}
    run.path_tap = {}
    run.primitives = primitives
    run.grid = grid
    run.canvas = canvas
    run.owner = owner
    run.guard_claims = {}
    run.path_guards = {}
    run.planned_taps = {}
    run.owned_source_starts = {}
    run.rejected_path_cells = {}
    restrict = run._restrict_proposal

    def next_pass(constraints=frozenset()):
        run.proposal_used = False
        return restrict(1, starts, goals, offers, constraints=constraints)

    assert next_pass() == ([start_b], goals, frozenset())
    assert next_pass() == (starts, goals, frozenset())
    # A companion can change its selected walk without changing this net's
    # endpoint maps. Construction identity must not collapse those two cases.
    upstream = ((0, 3, 0), (1, 3, 0))
    paths.stake(0, upstream, linked_head=False)
    for cell in upstream:
        canvas.blocked[cell] = routing_domain._TENTATIVE
        grid.block(cell)
        owner[cell] = 0
    assert next_pass() == ([start_b], goals, frozenset())
    assert next_pass() == (starts, goals, frozenset())
    paths.unstake(0)
    for cell in upstream:
        del canvas.blocked[cell]
        grid.restore(cell)
        del owner[cell]
    assert next_pass() == (starts, goals, frozenset())

    witness = junction.splitter_route_candidates(
        0,
        0,
        2,
        yaw=0.0,
        altitude_rules=canvas.belt_rules,
        carries_item="iron-ore",
    )[0]
    edge = (witness.entry.dock, witness.exit.dock)
    primitives.witnesses[edge] = witness
    assert next_pass() == ([start_b], goals, frozenset())
    assert next_pass() == (starts, goals, frozenset())
    del primitives.witnesses[edge]
    assert next_pass() == (starts, goals, frozenset())

    guard = (3, 3, 0)
    canvas.guard.add(guard)
    grid.block(guard)
    run.guard_claims[guard] = {0}
    assert next_pass() == ([start_b], goals, frozenset())
    # Equal geometry with another causal owner is a distinct construction.
    run.guard_claims[guard] = {2}
    assert next_pass() == ([start_b], goals, frozenset())
    run.guard_claims[guard] = {0}
    assert next_pass() == (starts, goals, frozenset())
    # A CBS branch's concrete cell constraints also belong to construction,
    # not to another branch's consumed positive choices.
    constrained = frozenset({(4, 4, 0)})
    assert next_pass(constrained) == ([start_b], goals, constrained)
    assert next_pass(constrained) == (starts, goals, constrained)
    assert next_pass() == (starts, goals, frozenset())

    detour = frozenset({(3, 0, 0)})
    run.proposals[1].detours.setdefault(detour, None)
    assert next_pass(constrained) == (starts, goals, constrained | detour)
    # A repeated identical refusal cannot refresh the consumed branch, and
    # removing its temporary mask must leave CBS's actual constraint intact.
    run.proposals[1].detours.setdefault(detour, None)
    assert next_pass(constrained) == (starts, goals, constrained)


def test_failed_prefix_does_not_leave_reused_index_ownership() -> None:
    canvas, net = _single_route_scene()
    other = replace(net, net_id=NetId(2, 3, "iron-ore", NetRole.INTERNAL, 1))
    ownership = RouteOwnership(len(canvas.buildings))
    # First path emits a prefix then collides with the immutable destination;
    # the second path reuses that tail index and must not inherit its owner.
    details = {}
    failed = routing_domain._commit_paths(
        canvas,
        [net, other],
        {0: ((5, 0, 0), (6, 0, 0)), 1: tuple((x, 0, 0) for x in range(1, 6))},
        2001,
        35,
        ownership=ownership,
        failure_details=details,
    )
    assert failed == (0,)
    assert ownership.snapshot()[2] == frozenset((other.net_id,))
    _assert_linked(canvas, other)


def test_contextual_refusal_moves_existing_tap_after_upstream_restaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canvas = routing_domain._Canvas(limit=(-3, -3, 10, 6))
    source = canvas.add(PlacedBuilding(2001, 35, -1, 0, carries_item="iron-ore"))
    trunk_sink = canvas.add(PlacedBuilding(2001, 35, 8, 0, carries_item="iron-ore"))
    branch_sink = canvas.add(PlacedBuilding(2001, 35, 3, 4, carries_item="iron-ore"))
    port = routing_domain._Port(source, -1, 0, -1, -1)
    nets = [
        routing_domain._Net(
            port,
            routing_domain._Port(trunk_sink, 8, 0, 8, 8),
            "iron-ore",
            net_id=NetId(0, 1, "iron-ore", NetRole.INTERNAL, 0),
        ),
        routing_domain._Net(
            port,
            routing_domain._Port(branch_sink, 3, 4, 3, 3),
            "iron-ore",
            net_id=NetId(0, 2, "iron-ore", NetRole.INTERNAL, 1),
        ),
    ]
    original = routing_domain._geometric_search
    initial = iter(
        (
            tuple((x, 0, 0) for x in range(8)),
            ((3, 1, 0), (3, 2, 0), (3, 3, 0)),
        )
    )

    def search(*args, **kwargs):
        path = next(initial, None)
        if path is not None:
            assert path[0] in args[1] and path[-1] in args[2]
            return routing_domain._PathSearchResult(path, None, (), 0)
        return original(*args, **kwargs)

    monkeypatch.setattr(routing_domain, "_geometric_search", search)
    candidates = []
    stopped = RouteSettlementCancelled("alternative-observed")

    def settle(workspace, owners):
        geometry = tuple(
            (building.x, building.y, building.z)
            for building in workspace.buildings
            if building.item_id == catalog.SPLITTER_ID
        )
        candidates.append(geometry)
        if len(candidates) > 1 and geometry != candidates[0]:
            return stopped
        return RouteSettlementRefused("contextual-frame", frozenset((nets[1].net_id,)))

    result = routing_domain._route_all(
        canvas,
        nets,
        2001,
        35,
        canvas.limit,
        budget=WorkBudget(left=20_000),
        settle=settle,
    )
    assert result.settlement is stopped
    assert candidates[0] != candidates[-1]
    assert not result.exhaustive
    assert result.last_mile is not None and result.last_mile.relation_strips == ()


def test_earlier_partial_incumbent_cannot_inherit_later_refused_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canvas, net = _single_route_scene()
    source = canvas.add(PlacedBuilding(2001, 35, 0, 6, carries_item="iron-ore"))
    sink = canvas.add(PlacedBuilding(2001, 35, 6, 6, carries_item="iron-ore"))
    other = routing_domain._Net(
        routing_domain._Port(source, 0, 6, 0, 0),
        routing_domain._Port(sink, 6, 6, 6, 6),
        "iron-ore",
        net_id=NetId(2, 3, "iron-ore", NetRole.INTERNAL, 1),
    )
    original = routing_domain._geometric_search
    missed = False

    def search(*args, **kwargs):
        nonlocal missed
        if not missed and (0, 6, 0) in args[0].routing_ports:
            missed = True
            return routing_domain._PathSearchResult(None, RouteFailureKind.DYNAMIC_ACCESS, (), 0)
        return original(*args, **kwargs)

    monkeypatch.setattr(routing_domain, "_geometric_search", search)
    monkeypatch.setattr(routing_domain, "_REPAIR_PASSES", 0)
    monkeypatch.setattr(routing_domain.last_mile, "B_MAX_STRANDED", 0)
    monkeypatch.setattr(routing_domain, "RRR_MAX", 2)
    budget = WorkBudget(left=20_000)
    refused = []

    def settle(workspace, owners):
        _assert_linked(workspace, other)
        workspace.add(PlacedBuilding(2001, 35, 8, 8, carries_item="refused-marker"))
        budget.left = 0
        refusal = RouteSettlementRefused("later-candidate", frozenset((other.net_id,)))
        refused.append(refusal)
        return refusal

    result = routing_domain._route_all(
        canvas,
        [net, other],
        2001,
        35,
        canvas.limit,
        budget=budget,
        settle=settle,
    )
    assert len(refused) == 1
    assert result.settlement is None
    assert result.routed == (net.net_id,)
    assert result.stranded == (other.net_id,)
    assert all(building.carries_item != "refused-marker" for building in canvas.buildings)
    _assert_linked(canvas, net)


def test_unused_source_offers_do_not_exhaust_physical_admission_deadline(monkeypatch):
    """Pay deterministic time for real projection, then require both physical links."""
    clock = [monotonic()]
    deadline = clock[0] + 30.0
    canvas = routing_domain._Canvas(limit=(-3, -3, 85, 9))
    source = canvas.add(PlacedBuilding(2001, 35, -1, 0, carries_item="iron-ore"))
    trunk = canvas.add(PlacedBuilding(2001, 35, 80, 0, carries_item="iron-ore"))
    branch = canvas.add(PlacedBuilding(2001, 35, 40, 7, carries_item="iron-ore"))
    port = routing_domain._Port(source, -1, 0, -1, -1)
    nets = [
        routing_domain._Net(
            port,
            routing_domain._Port(sink, x, y, x, x),
            "iron-ore",
            net_id=NetId(0, ordinal + 1, "iron-ore", NetRole.INTERNAL, ordinal),
        )
        for ordinal, (sink, x, y) in enumerate(((trunk, 80, 0), (branch, 40, 7)))
    ]
    canvas.junction_projection = routing_domain._CompositionProjection(
        tuple(canvas.buildings), canvas.limit, BandPolicy("portable")
    )
    original = routing_domain._CompositionProjection.allows_buildings

    def charged(self, candidates, *, committed=(), deadline=None):
        clock[0] += 1.0
        return original(self, candidates, committed=committed, deadline=deadline)

    monkeypatch.setattr(routing_domain._CompositionProjection, "allows_buildings", charged)
    monkeypatch.setattr(routing_domain.time, "monotonic", lambda: clock[0])
    result = routing_domain._route_all(canvas, nets, 2001, 35, canvas.limit, deadline=deadline)
    assert result.status is DetailedRouteStatus.ROUTED
    assert set(result.routed) == {net.net_id for net in nets}
    reached = set()
    pending = [source]
    while pending:
        index = pending.pop()
        if index in reached:
            continue
        reached.add(index)
        output = canvas.buildings[index].output_obj
        if output is not None:
            pending.append(output)
        pending.extend(canvas.buildings.by_input_obj(index))
    assert {trunk, branch} <= reached


def test_large_pass_preserves_search_opportunity_for_later_easy_net(monkeypatch):
    monkeypatch.setattr(routing_domain, "_SINGLE_ROUND_NETS", 2)
    canvas = routing_domain._Canvas(
        limit=(-2, -2, 102, 6),
        belt_rules=replace(routing_domain._DEFAULT_BELT_RULES, max_z=Fraction(0)),
    )
    nets = []
    for ordinal, (length, y) in enumerate(((100, 0), (4, 4))):
        source = canvas.add(PlacedBuilding(2001, 35, 0, y, carries_item="iron-ore"))
        sink = canvas.add(PlacedBuilding(2001, 35, length, y, carries_item="iron-ore"))
        nets.append(
            routing_domain._Net(
                routing_domain._Port(source, 0, y, 0, 0),
                routing_domain._Port(sink, length, y, length, length),
                "iron-ore",
                net_id=NetId(ordinal * 2, ordinal * 2 + 1, "iron-ore", NetRole.INTERNAL, ordinal),
            )
        )
    result = routing_domain._route_all(
        canvas, nets, 2001, 35, canvas.limit, budget=WorkBudget(left=80)
    )
    assert nets[1].net_id in result.routed
    _assert_linked(canvas, nets[1])


def test_refused_provider_reports_every_withdrawn_dependent_with_quota_left(monkeypatch):
    canvas = routing_domain._Canvas(limit=(-3, -3, 12, 8))
    source = canvas.add(PlacedBuilding(2001, 35, -1, 0, carries_item="iron-ore"))
    trunk = canvas.add(PlacedBuilding(2001, 35, 10, 0, carries_item="iron-ore"))
    branch = canvas.add(PlacedBuilding(2001, 35, 5, 5, carries_item="iron-ore"))
    port = routing_domain._Port(source, -1, 0, -1, -1)
    nets = [
        routing_domain._Net(
            port,
            routing_domain._Port(sink, x, y, x, x),
            "iron-ore",
            net_id=NetId(0, ordinal + 1, "iron-ore", NetRole.INTERNAL, ordinal),
        )
        for ordinal, (sink, x, y) in enumerate(((trunk, 10, 0), (branch, 5, 5)))
    ]
    monkeypatch.setattr(routing_domain, "_COMMIT_REPAIR_PASSES", 0)
    monkeypatch.setattr(routing_domain, "RRR_MAX", 1)
    monkeypatch.setattr(routing_domain.last_mile, "B_MAX_STRANDED", 0)
    budget = WorkBudget(left=20_000)

    def refuse_provider(workspace, owners):
        return RouteSettlementRefused("provider-context", frozenset((nets[0].net_id,)))

    result = routing_domain._route_all(
        canvas, nets, 2001, 35, canvas.limit, budget=budget, settle=refuse_provider
    )
    assert budget.left is not None and budget.left > 0
    assert result.routed == ()
    assert set(result.stranded) == {net.net_id for net in nets}


def test_route_dependencies_take_precedence_over_distance_priority() -> None:
    # The failed repair's preference was 98 before 97 before 92. Source and
    # destination attachments both require the opposite construction order.
    ordered = routing_domain._dependency_order(
        (98, 73, 93, 97, 92, 74),
        {92: {97}, 97: {98, 93}, 74: {73}, 12: {92}},
    )
    assert ordered == (92, 97, 98, 93, 74, 73)


def test_cyclic_route_dependencies_refuse_the_entire_reconstruction() -> None:
    assert routing_domain._dependency_order((3, 1, 2), {1: {2}, 2: {1}}) is None


def test_the_merge_frontier_request_is_built_at_the_call_site() -> None:
    """`deadline` is rebound mid-run, so the request cannot be hoisted.

    routing_domain rebinds the routing deadline in two `finally:` clauses. A
    request built once and reused would carry the clock from whichever branch
    built it.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout" / "routing_domain.py"
    tree = ast.parse(path.read_text())
    # `_DEFAULT_MERGE_REQUEST` is the shared all-defaults singleton the
    # signature reads; it holds no caller collection and no clock, so it is not
    # a construction at a call site.
    singletons = {
        id(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
    }
    constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_MergeFrontierRequest"
        and id(node) not in singletons
    ]
    assert len(constructions) == 2, [node.lineno for node in constructions]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_merge_frontier"
    ]
    assert len(calls) == 2
    for call in calls:
        inline = [
            argument
            for argument in [*call.args, *(keyword.value for keyword in call.keywords)]
            if isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.func.id == "_MergeFrontierRequest"
        ]
        assert inline, f"_merge_frontier at line {call.lineno} must build its request inline"


def test_merge_frontier_takes_five_parameters() -> None:
    import inspect

    from flab2bp.layout import routing_domain

    parameters = inspect.signature(routing_domain._merge_frontier).parameters
    assert list(parameters) == ["canvas", "paths", "siblings", "junctionable", "request"]


def test_the_merge_frontier_request_is_frozen_and_never_stored_into() -> None:
    """The request is read-only: `frozen=True`, and no site assigns a field.

    `merged_cells` and `path_ranges` are collections the callee reads through;
    freezing the reference is what is wanted, and no parameter is rebound in
    `_merge_frontier`'s body, so a frozen object is a faithful stand-in for the
    keyword block it replaces.
    """
    import ast
    from dataclasses import FrozenInstanceError, fields
    from pathlib import Path

    request = routing_domain._MergeFrontierRequest()
    assert [field.name for field in fields(request)] == [
        "provenance",
        "belt_prefab",
        "tentative_ok",
        "owned_guard",
        "primitives",
        "source_choices",
        "witness",
        "admit_tap",
        "deadline",
        "path_ranges",
        "merged_cells",
        "protected_sinks",
        "source_feeds",
        "trace",
    ]
    with pytest.raises(FrozenInstanceError):
        request.deadline = 1.0  # type: ignore[misc]

    source = (
        Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout" / "routing_domain.py"
    )
    tree = ast.parse(source.read_text())
    stores = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "request"
    ]
    assert stores == []


def test_the_merge_frontier_body_names_no_bare_former_parameter() -> None:
    """Every one of the 14 lifted names is reached through `request.` now."""
    import ast
    from pathlib import Path

    lifted = {
        "provenance",
        "belt_prefab",
        "tentative_ok",
        "owned_guard",
        "primitives",
        "source_choices",
        "witness",
        "admit_tap",
        "deadline",
        "path_ranges",
        "merged_cells",
        "protected_sinks",
        "source_feeds",
        "trace",
    }
    source = (
        Path(__file__).resolve().parents[2] / "src" / "flab2bp" / "layout" / "routing_domain.py"
    )
    tree = ast.parse(source.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_merge_frontier"
    )
    bare = sorted(
        {node.id for node in ast.walk(function) if isinstance(node, ast.Name) and node.id in lifted}
    )
    assert bare == []


def test_each_merge_frontier_request_carries_the_deadline_of_its_own_call() -> None:
    """A deadline rebound BETWEEN two source calls must reach the second one.

    `_RouteAllRun.deadline` is a mutable field restored in a `finally:` clause,
    so the request is the one thing in the migration that cannot be hoisted or
    cached: it must be built where the old keyword argument was evaluated. Two
    nets give two source-side `_merge_frontier` calls; rebinding the field from
    inside the first is the literal proof that the second reads the live value
    rather than a frozen copy.
    """
    import inspect
    from collections.abc import Callable, Mapping, Sequence
    from typing import cast

    from flab2bp.layout.base import PlacedBuilding as _PlacedBuilding

    bounds = (-2, -2, 202, 161)
    canvas = routing_domain._Canvas(
        limit=bounds, belt_rules=replace(routing_domain._DEFAULT_BELT_RULES, max_z=Fraction(0))
    )
    tower = canvas.power_building
    for x, y in ((0, 0), (200, 159)):
        canvas.add(
            _PlacedBuilding(
                tower.item_id, tower.model_index, x, y, width=tower.width, height=tower.height
            ),
            solid=True,
        )

    def port(x: int, y: int) -> routing_domain._Port:
        index = canvas.add(_PlacedBuilding(2003, 37, x, y, carries_item="gear"))
        return routing_domain._Port(index, x, y, x, x)

    ports = [(port(50, 80), port(150, 80)), (port(50, 40), port(150, 40))]
    canvas.guard.update((100, y, 0) for y in range(160))
    canvas.junction_projection = routing_domain._CompositionProjection(
        canvas.buildings, bounds, BandPolicy("200"), belt_rules=canvas.belt_rules
    )
    nets = [
        routing_domain._Net(
            source, destination, "gear", net_id=NetId(ordinal, 1, "gear", NetRole.INTERNAL, 0)
        )
        for ordinal, (source, destination) in enumerate(ports)
    ]
    canvas.guard.remove((100, 80, 0))
    canvas.guard.remove((100, 40, 0))

    original = routing_domain._merge_frontier
    sentinel = monotonic() + 3600.0
    #: (deadline the run held when the call was made, deadline the request carried)
    seen: list[tuple[float | None, float | None]] = []

    def capturing(
        merge_canvas: routing_domain._Canvas,
        merge_paths: Mapping[int, Sequence[tuple[int, int, int]]],
        siblings: tuple[int, ...],
        junctionable: Callable[[int, int, int], bool] | None = None,
        request: routing_domain._MergeFrontierRequest = routing_domain._DEFAULT_MERGE_REQUEST,
    ) -> set[tuple[int, int, int]]:
        if junctionable is not None:
            run = cast(
                routing_domain._RouteAllRun,
                inspect.getclosurevars(junctionable).nonlocals["self"],
            )
            seen.append((run.deadline, request.deadline))
            # Rebind between this call and the next one, exactly as the two
            # `finally:` restores do mid-run.
            run.deadline = sentinel
        return original(merge_canvas, merge_paths, siblings, junctionable, request)

    routing_domain._merge_frontier = capturing  # type: ignore[assignment]
    try:
        routing_domain._route_all(canvas, nets, 2003, 37, bounds, budget=WorkBudget(left=100_000))
    finally:
        routing_domain._merge_frontier = original

    assert len(seen) >= 2, seen
    # Every source request carried the run's live deadline, not a cached one.
    assert all(live == carried for live, carried in seen), seen
    # And the rebind had teeth: the value genuinely changed between calls.
    assert seen[0][1] is None
    assert seen[1][1] == sentinel
