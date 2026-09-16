"""Small exhaustive product graphs, independent of native interval dominance."""

from __future__ import annotations

import heapq
import math
from array import array
from dataclasses import replace
from random import Random

import pytest

from flab2bp.layout.geometric_motion import (
    MotionEdge,
    MotionEndpoint,
    MotionGuard,
    MotionPolicy,
)
from flab2bp.layout.geometric_router import GeometricQuery, GeometricResult, route
from flab2bp.layout.geometric_world import GeometricWorld


def _allowed(policy: MotionPolicy, edge: MotionEdge, cell: tuple[int, int, int]) -> bool:
    if edge.guard < 0:
        return True
    x, y, z = cell
    return any(
        xl <= x <= xh and yl <= y <= yh and zl <= z <= zh
        for xl, xh, yl, yh, zl, zh in policy.guards[edge.guard].boxes
    )


def _price(query: GeometricQuery, index: int) -> float:
    history = query.world.history
    return query.pressure * (
        (history[index] if history is not None else 0.0)
        + (query.present[index] if query.present is not None else 0.0)
    )


def _oracle(query: GeometricQuery) -> float | None:
    """Exhaustively relax cell/state pairs; this is deliberately test-only."""
    policy = query.motion
    assert policy is not None
    world = query.world
    accepting = {(row.cell, row.state) for row in policy.accepting if row.cell in query.goals}
    distances: dict[tuple[int, int], float] = {}
    queue: list[tuple[float, int, int]] = []
    for row in policy.initial:
        if row.cell not in query.starts:
            continue
        cost = _price(query, row.cell) if query.charge_occupied_cells else 0.0
        key = row.cell, row.state
        if cost < distances.get(key, math.inf):
            distances[key] = cost
            heapq.heappush(queue, (cost, *key))
    while queue:
        cost, index, state = heapq.heappop(queue)
        if cost != distances[index, state]:
            continue
        if (index, state) in accepting:
            return cost
        cell = world.cell(index)
        x, y, z = cell
        for edge in policy.edges:
            if edge.source != state or not _allowed(policy, edge, cell):
                continue
            shape = policy.moves[edge.move]
            dx, dy, dz, via = shape
            target = x + dx, y + dy, z + dz
            if not (
                0 <= target[0] < world.nx
                and 0 <= target[1] < world.ny
                and 0 <= target[2] < world.nz
            ):
                continue
            target_index = world.codec.encode(target)
            if not world.flags[target_index]:
                continue
            extra_price = _price(query, target_index)
            if via:
                middle = world.codec.encode((x + dx // 2, y + dy // 2, z))
                if not world.flags[middle]:
                    continue
                if query.charge_occupied_cells:
                    extra_price += _price(query, middle)
            for mx, my, mz, mv, base in world.transitions[z]:
                if (mx, my, mz, mv) != shape:
                    continue
                candidate = cost + base + extra_price
                key = target_index, edge.target
                if candidate < distances.get(key, math.inf):
                    distances[key] = candidate
                    heapq.heappush(queue, (candidate, *key))
    return None


def _assert_witness(query: GeometricQuery, result: GeometricResult) -> None:
    policy, path, witness = query.motion, result.path, result.motion
    assert policy is not None and path is not None and witness is not None
    assert MotionEndpoint(path[0], witness.initial_state) in policy.initial
    assert MotionEndpoint(path[-1], witness.final_state, witness.final_action) in policy.accepting
    state, position = witness.initial_state, 0
    cost = _price(query, path[0]) if query.charge_occupied_cells else 0.0
    for step in witness.steps:
        assert step.path_index == position
        assert step.source == state
        source = query.world.cell(path[position])
        assert any(
            edge.source == step.source
            and edge.target == step.target
            and edge.move == step.move
            and edge.action == step.action
            and _allowed(policy, edge, source)
            for edge in policy.edges
        )
        dx, dy, dz, via = policy.moves[step.move]
        x, y, z = source
        if via:
            position += 1
            assert query.world.cell(path[position]) == (x + dx // 2, y + dy // 2, z)
            assert query.world.flags[path[position]]
            if query.charge_occupied_cells:
                cost += _price(query, path[position])
        position += 1
        assert query.world.cell(path[position]) == (x + dx, y + dy, z + dz)
        assert query.world.flags[path[position]]
        cost += _price(query, path[position])
        cost += min(
            base
            for mx, my, mz, mv, base in query.world.transitions[z]
            if (mx, my, mz, mv) == (dx, dy, dz, via)
        )
        state = step.target
    assert position == len(path) - 1
    assert state == witness.final_state
    assert result.cost == pytest.approx(cost)


def _check(query: GeometricQuery) -> GeometricResult:
    expected = _oracle(query)
    result = route(query)
    assert result.kind not in {"budget", "cancelled"}
    if expected is None:
        assert result.path is None
        assert result.motion is None
        # A constrained exhausted component is not a physical wall certificate.
        if result.kind == "motion-exhausted":
            assert result.reachable == ()
            assert result.co_reachable is None
    else:
        assert result.kind == "routed"
        assert result.cost == pytest.approx(expected)
        _assert_witness(query, result)
    return result


def test_more_expensive_arrival_keeps_its_usable_motion_state() -> None:
    world = GeometricWorld(
        5,
        3,
        1,
        0,
        0,
        bytearray([1] * 15),
        None,
        (((1, 0, 0, False, 1.0), (0, 1, 0, False, 1.0), (0, -1, 0, False, 1.0)),),
    )
    start, goal = world.codec.encode((0, 1, 0)), world.codec.encode((4, 1, 0))
    policy = MotionPolicy(
        4,
        ((1, 0, 0, False), (0, 1, 0, False), (0, -1, 0, False)),
        (
            MotionEdge(0, 0, 1),
            MotionEdge(1, 0, 1),
            MotionEdge(0, 1, 2),
            MotionEdge(2, 0, 2),
            MotionEdge(2, 2, 3, guard=0, action=17),
            MotionEdge(3, 0, 3),
        ),
        (MotionEndpoint(start, 0),),
        (MotionEndpoint(goal, 3),),
        (MotionGuard(((1, 3, 2, 2, 0, 0),)),),
    )
    result = _check(GeometricQuery(world, (start,), (goal,), 0.0, 100_000, motion=policy))
    assert result.cost == 6.0
    assert result.motion is not None
    assert [step.action for step in result.motion.steps if step.action >= 0] == [17]


def test_goal_coordinate_does_not_discharge_unfinished_motion() -> None:
    world = GeometricWorld(
        3,
        1,
        1,
        0,
        0,
        bytearray([1] * 3),
        None,
        (((1, 0, 0, False, 1.0),),),
    )
    policy = MotionPolicy(
        3,
        ((1, 0, 0, False),),
        (MotionEdge(0, 0, 1), MotionEdge(1, 0, 2)),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(1, 2),),
    )
    query = GeometricQuery(world, (0,), (1,), 0.0, 100_000, motion=policy)
    result = _check(query)
    assert result.kind == "motion-exhausted"
    assert route(replace(query, motion=None)).path == (0, 1)
    zero = replace(policy, initial=(MotionEndpoint(1, 0),))
    assert _check(replace(query, starts=(1,), motion=zero)).path is None
    admitted = replace(zero, accepting=(MotionEndpoint(1, 0),))
    result = _check(replace(query, starts=(1,), motion=admitted))
    assert result.path == (1,)
    assert result.cost == 0.0


@pytest.mark.parametrize("along_y", [False, True])
def test_ramp_and_lift_witness_keeps_source_plane_and_transposed_guards(along_y: bool) -> None:
    dx, dy = (0, 2) if along_y else (2, 0)
    world = GeometricWorld(
        1 if along_y else 3,
        3 if along_y else 1,
        3,
        0,
        0,
        bytearray(9),
        array("d", [0.0] * 9),
        (((dx, dy, 1, True, 3.01),), ((0, 0, 1, False, 2.0),), ()),
    )
    via = world.codec.encode((dx // 2, dy // 2, 0))
    landing = world.codec.encode((dx, dy, 1))
    goal = world.codec.encode((dx, dy, 2))
    world.flags[via] = world.flags[landing] = world.flags[goal] = 1
    assert world.history is not None
    world.history[0], world.history[via] = 4.0, 2.0
    world.history[landing], world.history[goal] = 0.7, 1.5
    policy = MotionPolicy(
        3,
        ((dx, dy, 1, True), (0, 0, 1, False)),
        (MotionEdge(0, 0, 1, action=7), MotionEdge(1, 1, 2, guard=0, action=11)),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(goal, 2),),
        (MotionGuard(((dx, dx, dy, dy, 1, 1),)),),
    )
    query = GeometricQuery(
        world,
        (0, 1),
        (goal,),
        1.0,
        100_000,
        charge_occupied_cells=True,
        motion=policy,
    )
    result = _check(query)
    assert result.path == (0, via, landing, goal)
    assert result.cost == pytest.approx(13.21)
    world.flags[via] = 0
    assert _check(query).path is None


@pytest.mark.parametrize("seed", range(12))
def test_directed_product_graph_matches_exhaustive_oracle(seed: int) -> None:
    random = Random(seed)
    nx, ny, nz = (5, 3, 2) if seed % 2 else (3, 5, 2)
    shapes = (
        (1, 0, 0, False),
        (-1, 0, 0, False),
        (0, 1, 0, False),
        (0, -1, 0, False),
        (2, 0, 1, True),
        (0, 2, 1, True),
        (-2, 0, -1, True),
        (0, -2, -1, True),
        (0, 0, 1, False),
        (0, 0, -1, False),
    )
    flags = bytearray(random.random() > 0.17 for _ in range(nx * ny * nz))
    world = GeometricWorld(
        nx,
        ny,
        nz,
        0,
        0,
        flags,
        array("d", (random.randrange(4) / 4 for _ in flags)),
        tuple(
            tuple(
                (*shape, 1.0 + move / 8)
                for move, shape in enumerate(shapes)
                if 0 <= z + shape[2] < nz
            )
            for z in range(nz)
        ),
    )
    start, other, goal = 0, nz, len(flags) - 1
    flags[start] = flags[other] = flags[goal] = 1
    edges: list[MotionEdge] = []
    for state in range(4):
        for move in range(len(shapes)):
            if random.random() < 0.8:
                edges.append(
                    MotionEdge(state, move, random.randrange(4), action=move if move >= 4 else -1)
                )
            if random.random() < 0.25:
                edges.append(
                    MotionEdge(state, move, random.randrange(4), guard=0, action=20 + move)
                )
    policy = MotionPolicy(
        4,
        shapes,
        tuple(edges),
        (MotionEndpoint(start, 0), MotionEndpoint(other, 1)),
        (MotionEndpoint(goal, 2), MotionEndpoint(goal, 3)),
        (MotionGuard(((0, nx // 2, 0, ny - 1, 0, nz - 1),)),),
    )
    _check(
        GeometricQuery(
            world,
            (start, other),
            (goal,),
            1.0,
            100_000,
            present=array("d", (random.randrange(3) / 2 for _ in flags)),
            charge_occupied_cells=bool(seed % 2),
            motion=policy,
        )
    )


def test_transient_translation_preserves_the_best_interior_origin() -> None:
    history = array("d", [0.0] * 36)
    for x in range(7):
        history[3 * x + 1] = 3.0
    world = GeometricWorld(
        12,
        3,
        1,
        0,
        0,
        bytearray([1] * 36),
        history,
        (((1, 0, 0, False, 1.0), (0, 1, 0, False, 1.0)),),
    )
    goal = world.codec.encode((9, 2, 0))
    policy = MotionPolicy(
        5,
        ((1, 0, 0, False), (0, 1, 0, False)),
        (
            MotionEdge(0, 0, 0),
            MotionEdge(0, 1, 1, guard=0),
            MotionEdge(1, 0, 2),
            MotionEdge(2, 0, 3),
            MotionEdge(3, 0, 3),
            MotionEdge(3, 1, 4),
        ),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(goal, 4),),
        (MotionGuard(((4, 10, 0, 0, 0, 0),)),),
    )
    result = _check(GeometricQuery(world, (0,), (goal,), 1.0, 100_000, motion=policy))
    assert result.cost == 11.0
    assert result.path is not None
    assert world.codec.encode((7, 1, 0)) in result.path


def test_transient_progress_becomes_whole_run_without_losing_trace() -> None:
    world = GeometricWorld(
        300,
        1,
        1,
        0,
        0,
        bytearray([1] * 300),
        None,
        (((1, 0, 0, False, 1.0),),),
    )
    policy = MotionPolicy(
        4,
        ((1, 0, 0, False),),
        (MotionEdge(0, 0, 1), MotionEdge(1, 0, 2), MotionEdge(2, 0, 3), MotionEdge(3, 0, 3)),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(299, 3),),
    )
    result = _check(GeometricQuery(world, (0,), (299,), 0.0, 100_000, motion=policy))
    assert result.cost == 299.0
    # A per-cell queue implementation violates the interval-work contract.
    assert result.metrics["interval_pops"] < 20


def test_interrupted_motion_query_returns_no_partial_proof() -> None:
    world = GeometricWorld(
        300,
        1,
        1,
        0,
        0,
        bytearray([1] * 300),
        None,
        (((1, 0, 0, False, 1.0),),),
    )
    policy = MotionPolicy(
        1,
        ((1, 0, 0, False),),
        (MotionEdge(0, 0, 0),),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(299, 0),),
    )
    query = GeometricQuery(world, (0,), (299,), 0.0, 1, motion=policy)
    result = route(query)
    assert result.kind == "budget"
    assert result.metrics["charged_work"] <= 1
    assert result.path is None
    assert result.motion is None
    cancelled = route(replace(query, max_work=100_000, cancelled=lambda: True))
    assert cancelled.kind == "cancelled"
    assert cancelled.path is None
    assert cancelled.motion is None


def test_terminal_action_is_retained_even_for_a_zero_edge_route() -> None:
    world = GeometricWorld(
        1,
        1,
        1,
        0,
        0,
        bytearray([1]),
        None,
        ((),),
    )
    policy = MotionPolicy(
        1,
        (),
        (),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(0, 0, action=23),),
    )
    result = _check(GeometricQuery(world, (0,), (0,), 0.0, 1000, motion=policy))
    assert result.path == (0,)
    assert result.motion is not None
    assert result.motion.final_action == 23


@pytest.mark.parametrize(
    "edge",
    [
        MotionEdge(1, 0, 0),
        MotionEdge(0, 1, 0),
        MotionEdge(0, 0, 0, guard=0),
    ],
)
def test_invalid_policy_indices_are_rejected_before_native_access(edge: MotionEdge) -> None:
    world = GeometricWorld(
        2,
        1,
        1,
        0,
        0,
        bytearray([1, 1]),
        None,
        (((1, 0, 0, False, 1.0),),),
    )
    policy = MotionPolicy(
        1,
        ((1, 0, 0, False),),
        (edge,),
        (MotionEndpoint(0, 0),),
        (MotionEndpoint(1, 0),),
    )
    with pytest.raises(ValueError):
        route(GeometricQuery(world, (0,), (1,), 0.0, 1000, motion=policy))
