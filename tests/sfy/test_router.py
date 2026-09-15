"""One Satisfactory net, routed by the DSP interval kernel over the lattice.

Nothing here counts expanded cells: the router is a geometric interval router
(the user's binding ruling), so the claims are about the SHAPE of what comes
back -- a straight run is one interval, a wall is crossed by 2:1 inclines, a
climb the incline family cannot pay for is a lift -- and about what a refusal is
allowed to say.  Every physical number comes from the registry: the grid step,
the lift window and the designer's extent are read, never written here.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Collection, Sequence
from functools import cache

import pytest

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.layout.lattice import (
    GROUND_LEVEL,
    Lattice,
    Node,
    Occupancy,
    occupancy_for,
)
from flab2bp.sfy.layout.model import MachineObj, Pose
from flab2bp.sfy.layout.router import (
    MAX_SEARCH_WORK,
    BudgetCause,
    Routed,
    RouteFailureKind,
    route_net,
)
from flab2bp.sfy.layout.transitions import level_toll, lift_cost, sfy_transitions
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import designer

CONSTRUCTOR = "Build_ConstructorMk1_C"
ROD = "Recipe_IronRod_C"
#: A machine stands on the slab, whose top is one grid step up.
SLAB_TOP_CM = 100.0


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _lattice(mark: str = "mk1") -> Lattice:
    return Lattice.over(designer(mark, _registry()), _registry())


def _empty() -> Occupancy:
    return occupancy_for(_lattice(), (), (), (), (), _registry())


def _lift_heights(lattice: Lattice) -> range:
    """The legal lift heights, in levels, as R-M3-3 states them.

    Read from ``Registry.limits`` and clamped by the designer's own height:
    ``4 <= h <= min(48, n - 2)``.  No number in this function is written down.
    """
    limits = _registry().limits
    grid = lattice.grid_cm
    assert limits.lift_min_cm is not None
    assert limits.lift_max_cm is not None
    assert limits.lift_step_cm is not None
    step = round(limits.lift_step_cm / grid)
    low = round(limits.lift_min_cm / grid)
    high = min(round(limits.lift_max_cm / grid), lattice.n - GROUND_LEVEL)
    return range(low, high + 1, step)


def _table(
    lattice: Lattice, *, lifts: bool = True
) -> tuple[tuple[tuple[int, int, int, bool, float], ...], ...]:
    heights = _lift_heights(lattice) if lifts else range(0)
    return sfy_transitions(lattice.n + 1, heights)


def _route(
    occupancy: Occupancy,
    start: Node,
    goal: Node,
    *,
    lifts: bool = True,
    opened: Collection[Node] = (),
    left: int | None = None,
    deadline: float | None = None,
    blame: dict[Node, float] | None = None,
) -> Routed:
    return route_net(
        occupancy,
        starts=(start,),
        goals=(goal,),
        opened=opened,
        pressure=0.0,
        budget=WorkBudget(left=left),
        deadline=deadline,
        transitions=_table(occupancy.lattice, lifts=lifts),
        blame=blame,
    )


def _steps(path: Sequence[Node]) -> list[tuple[int, int, int]]:
    return [(b[0] - a[0], b[1] - a[1], b[2] - a[2]) for a, b in zip(path, path[1:], strict=False)]


def _wall_of_constructors() -> Occupancy:
    """A line of Constructors across the row ``y = 16``, wall to wall."""
    lattice = _lattice()
    machines = tuple(
        MachineObj(
            i, CONSTRUCTOR, Pose(lattice.world((column, 16, 0))[0], 0.0, SLAB_TOP_CM, 0.0), ROD
        )
        for i, column in enumerate(range(0, lattice.n + 1, 4))
    )
    return occupancy_for(lattice, machines, (), (), (), _registry())


# --- the movement table ----------------------------------------------------


def test_the_table_has_one_row_per_level_and_none_below_the_ports() -> None:
    lattice = _lattice()
    table = _table(lattice)
    assert len(table) == lattice.n + 1
    assert table[0] == () and table[1] == ()
    flats = [move for move in table[GROUND_LEVEL] if move[2] == 0]
    assert len(flats) == 4
    assert all(move[3] is False and move[4] == pytest.approx(1.0) for move in flats)


def test_a_climb_costs_a_toll_that_grows_with_the_level() -> None:
    assert level_toll(GROUND_LEVEL) == 0.0
    assert level_toll(GROUND_LEVEL - 1) == 0.0
    assert level_toll(GROUND_LEVEL + 3) == pytest.approx(0.03)
    assert lift_cost(4) == pytest.approx(6.0)


def test_an_incline_is_a_two_to_one_move_with_a_via_on_the_source_level() -> None:
    table = _table(_lattice())
    # Four directions times two signs, once there is a level below to fall to:
    # the port level has none, because levels below it are impassable (R-M3-3).
    assert len([move for move in table[GROUND_LEVEL] if move[3]]) == 4
    inclines = [move for move in table[GROUND_LEVEL + 1] if move[3]]
    assert len(inclines) == 8
    for dx, dy, dz, via, cost in inclines:
        assert via is True
        assert abs(dz) == 1
        assert abs(dx) + abs(dy) == 2
        assert cost == pytest.approx(3.0 + level_toll(GROUND_LEVEL + 1 + dz))


def test_a_lift_is_a_vertical_edge_of_a_legal_height() -> None:
    lattice = _lattice()
    heights = _lift_heights(lattice)
    lifts = [move for move in _table(lattice)[GROUND_LEVEL] if move[:2] == (0, 0)]
    assert lifts, "the ground level must offer a lift"
    for dx, dy, dz, via, cost in lifts:
        assert (dx, dy, via) == (0, 0, False)
        assert abs(dz) in heights
        assert cost == pytest.approx(lift_cost(abs(dz)) + level_toll(GROUND_LEVEL + dz))


# --- one routed net --------------------------------------------------------


def test_a_straight_run_on_an_empty_lattice_is_one_interval() -> None:
    occupancy = _empty()
    routed = _route(occupancy, (4, 4, 2), (4, 28, 2))
    assert routed.kind is None
    assert routed.path is not None
    assert routed.path == tuple((4, j, 2) for j in range(4, 29))
    assert len(routed.path) == 25
    assert _interval_pops(occupancy, (4, 4, 2), (4, 28, 2)) < 64


def _interval_pops(occupancy: Occupancy, start: Node, goal: Node) -> int:
    """The kernel's own interval count for the query ``route_net`` would make.

    ``Routed`` carries no metrics -- the rip-up loop has no use for them -- so
    the one claim that is about the kernel's interval machinery rather than
    about the path asks the kernel directly, on the same world.
    """
    from flab2bp.layout.geometric_router import GeometricQuery, route
    from flab2bp.layout.geometric_world import GeometricWorld

    lattice = occupancy.lattice
    world = GeometricWorld(
        nx=lattice.n + 1,
        ny=lattice.n + 1,
        nz=lattice.n + 1,
        gx0=0,
        gy0=0,
        flags=bytearray(occupancy.snapshot()),
        history=occupancy.history,
        transitions=_table(lattice),
    )
    result = route(
        GeometricQuery(
            world=world,
            starts=(lattice.index(start),),
            goals=(lattice.index(goal),),
            pressure=0.0,
            max_work=MAX_SEARCH_WORK,
        )
    )
    return result.metrics["interval_pops"]


def test_a_wall_of_machines_is_crossed_by_an_incline_over_the_top() -> None:
    """With no lift in the table, the only way over a wall is the 2:1 incline."""
    occupancy = _wall_of_constructors()
    routed = _route(occupancy, (16, 4, 2), (16, 28, 2), lifts=False)
    assert routed.path is not None, routed.kind
    levels = [node[2] for node in routed.path]
    crest = min(k for k in occupancy.lattice.open_levels if occupancy.free((16, 16, k)))
    assert crest == 8
    assert max(levels) == crest
    assert levels[0] == levels[-1] == GROUND_LEVEL
    steps = _steps(routed.path)
    assert any(step[2] for step in steps), "the path must change level"
    for index, (dx, dy, dz) in enumerate(steps):
        if not dz:
            continue
        # An incline is emitted as two nodes -- the via on the level it leaves,
        # then the landing -- so a climb step is always one grid step along and
        # always preceded by a flat step the same way: 200 cm of run per 100 cm
        # of rise, which is the 35-degree limit.
        assert abs(dz) == 1
        assert abs(dx) + abs(dy) == 1
        assert index > 0, "a climb cannot be the path's first step: it needs its via"
        assert steps[index - 1] == (dx, dy, 0)
    assert any(node[1] == 16 for node in routed.path)


def test_a_lift_edge_is_taken_when_the_column_is_free_and_cheaper() -> None:
    """Four levels of climb in a corridor: eight nodes of run, or no run at all.

    The start sits in a three-row corridor roofed at level 5; the goal is four
    nodes along it at level 6, above the roof.  An incline pays two grid steps
    of run per level, so reaching level 6 costs eight nodes of corridor -- past
    the goal and back, since the corridor is all the room there is.  A lift of
    four levels stands still.  Both routes exist, and the router takes the lift
    only because it is cheaper.
    """
    occupancy = _corridor()
    inclined = _route(occupancy, (10, 10, 2), (14, 10, 6), lifts=False)
    assert inclined.path is not None, inclined.kind
    assert len(inclined.path) == 9  # eight nodes of run, every one of them flat
    assert not any(step[:2] == (0, 0) for step in _steps(inclined.path))

    routed = _route(occupancy, (10, 10, 2), (14, 10, 6))
    assert routed.path is not None, routed.kind
    assert (0, 0, 4) in _steps(routed.path)
    assert len(routed.path) == 6  # the lift's two ends plus four nodes of run


def _corridor() -> Occupancy:
    """A three-row corridor at levels 2..5, open to the sky from level 6 up."""
    occupancy = _empty()
    lattice = occupancy.lattice
    edge = lattice.designer.half_cm + lattice.grid_cm
    roof = (150.0, 550.0)  # denies levels 2..5 and leaves 6 open
    south = lattice.world((0, 8, 0))[1]
    north = lattice.world((0, 12, 0))[1]
    occupancy.block_box((-edge, -edge, roof[0]), (edge, south, roof[1]))
    occupancy.block_box((-edge, north, roof[0]), (edge, edge, roof[1]))
    assert occupancy.free((10, 10, 2)) and occupancy.free((10, 10, 5))
    assert not occupancy.free((10, 8, 2)) and not occupancy.free((10, 12, 5))
    assert occupancy.free((10, 8, 6)) and occupancy.free((10, 12, 6))
    return occupancy


def test_the_kernel_accepts_a_multi_level_vertical_move() -> None:
    """THE SPIKE.  A lift is a table row only if the kernel decodes ``dz = 4``.

    The kernel's C++ adds ``move.dz`` to the source level and bounds-checks the
    result, so nothing in it is unit-sized; this pins that reading down with a
    world whose ONLY move is a four-level hop.  If it ever stops holding, lifts
    become ``GeometricQuery.extra_edges`` and this test is what says so.
    """
    from flab2bp.layout.geometric_router import GeometricQuery, route
    from flab2bp.layout.geometric_world import GeometricWorld, GridIndex

    nx = ny = 3
    nz = 8
    codec = GridIndex(0, 0, ny, nz)
    flags = bytearray(nx * ny * nz)
    start, goal = codec.encode((1, 1, 2)), codec.encode((1, 1, 6))
    flags[start] = flags[goal] = 1
    rows: list[tuple[tuple[int, int, int, bool, float], ...]] = [() for _ in range(nz)]
    rows[2] = ((0, 0, 4, False, lift_cost(4)),)
    world = GeometricWorld(
        nx=nx, ny=ny, nz=nz, gx0=0, gy0=0, flags=flags, history=None, transitions=tuple(rows)
    )
    result = route(
        GeometricQuery(
            world=world, starts=(start,), goals=(goal,), pressure=0.0, max_work=MAX_SEARCH_WORK
        )
    )
    assert result.kind == "routed"
    assert result.path is not None
    assert [world.cell(index) for index in result.path] == [(1, 1, 2), (1, 1, 6)]
    assert result.cost == pytest.approx(lift_cost(4))


# --- what a refusal may say ------------------------------------------------


def _ring(level: int) -> tuple[Node, ...]:
    """A closed square of belt one node out from ``(10, 10)``, at ``level``."""
    return (
        (9, 9, level),
        (10, 9, level),
        (11, 9, level),
        (11, 10, level),
        (11, 11, level),
        (10, 11, level),
        (9, 11, level),
        (9, 10, level),
        (9, 9, level),
    )


def _sealed() -> tuple[Occupancy, Node, int]:
    """A start ringed by net 7's committed belt, opened for its own net."""
    occupancy = _empty()
    occupancy.commit(7, _ring(GROUND_LEVEL))
    return occupancy, (10, 10, 2), 7


def test_a_sealed_start_names_the_wall_only_on_exhaustion() -> None:
    occupancy, start, net = _sealed()
    blame: dict[Node, float] = {}
    routed = _route(occupancy, start, (20, 20, 2), lifts=False, opened=(start,), blame=blame)
    assert routed.path is None
    assert routed.kind is RouteFailureKind.SEALED_POCKET
    assert routed.cause is BudgetCause.UNKNOWN
    assert routed.wall
    for node in routed.wall:
        assert occupancy.owner[occupancy.lattice.index(node)] == net
    assert set(blame) == set(routed.wall)
    assert all(weight == pytest.approx(1.0) for weight in blame.values())


def test_a_sealed_goal_names_the_nets_that_shut_the_way_in() -> None:
    """The kernel's reverse proof, read backwards through the movement table.

    A ring at the port level alone is one-way: a belt can still incline IN over
    it, because the incline's via rides the level above.  Ringing that level too
    shuts the goal in, and the kernel comes back with the goal-reaching
    component instead of the source-reaching one -- so the census must name the
    PREDECESSORS of that component, vias and all, rather than its neighbours.
    """
    occupancy = _empty()
    goal = (10, 10, 2)
    for net, level in ((7, 2), (8, 3)):
        occupancy.commit(net, _ring(level))
    routed = _route(occupancy, (4, 4, 2), goal, lifts=False, opened=(goal,))
    assert routed.path is None
    assert routed.kind is RouteFailureKind.SEALED_POCKET
    owners = {occupancy.owner[occupancy.lattice.index(node)] for node in routed.wall}
    assert owners == {7, 8}
    assert {node[2] for node in routed.wall} == {2, 3}


def test_a_budget_stop_proves_nothing_and_names_nobody() -> None:
    occupancy, start, _net = _sealed()
    routed = _route(occupancy, start, (20, 20, 2), lifts=False, opened=(start,), left=1)
    assert routed.path is None
    assert routed.kind is RouteFailureKind.BUDGET
    assert routed.cause is BudgetCause.ALLOWANCE
    assert routed.wall == ()


def test_an_empty_ledger_and_a_passed_clock_refuse_before_any_work() -> None:
    occupancy = _empty()
    spent = _route(occupancy, (4, 4, 2), (4, 28, 2), left=0)
    assert (spent.kind, spent.cause, spent.work) == (
        RouteFailureKind.BUDGET,
        BudgetCause.ALLOWANCE,
        0,
    )
    late = _route(occupancy, (4, 4, 2), (4, 28, 2), deadline=0.0)
    assert (late.kind, late.cause, late.work) == (
        RouteFailureKind.BUDGET,
        BudgetCause.DEADLINE,
        0,
    )


def test_a_goal_or_a_start_off_the_lattice_is_a_dynamic_access_refusal() -> None:
    occupancy = _empty()
    lattice = occupancy.lattice
    away = (lattice.n + 4, 4, 2)
    assert _route(occupancy, (4, 4, 2), away).kind is RouteFailureKind.DYNAMIC_ACCESS
    assert _route(occupancy, away, (4, 28, 2)).kind is RouteFailureKind.DYNAMIC_ACCESS
    # A start the world denies is no start either -- until the net opens it.
    inside_a_wall = (0, 4, 2)
    assert not occupancy.free(inside_a_wall)
    assert _route(occupancy, inside_a_wall, (4, 28, 2)).kind is RouteFailureKind.DYNAMIC_ACCESS
    opened = _route(occupancy, inside_a_wall, (4, 28, 2), opened=(inside_a_wall,))
    assert opened.path is not None


def test_the_search_spends_the_shared_ledger_it_was_handed() -> None:
    occupancy = _empty()
    budget = WorkBudget(left=MAX_SEARCH_WORK)
    routed = route_net(
        occupancy,
        starts=((4, 4, 2),),
        goals=((4, 28, 2),),
        pressure=0.0,
        budget=budget,
        deadline=None,
        transitions=_table(occupancy.lattice),
    )
    assert routed.path is not None
    assert routed.work > 0
    assert budget.left == MAX_SEARCH_WORK - routed.work


def test_opening_a_node_never_touches_the_shared_occupancy() -> None:
    occupancy, start, _net = _sealed()
    before = occupancy.snapshot()
    _route(occupancy, start, (20, 20, 2), lifts=False, opened=(start,))
    assert occupancy.snapshot() == before


def test_importing_the_router_module_does_not_load_the_kernel() -> None:
    code = (
        "import sys;"
        "import flab2bp.sfy.layout.router;"
        "print(sorted(n for n in sys.modules"
        " if n in ('flab2bp.layout._geometric_kernel', 'flab2bp.layout.geometric_router')))"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert ast.literal_eval(out.stdout.strip()) == []
