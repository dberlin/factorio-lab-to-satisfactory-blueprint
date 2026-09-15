"""One Satisfactory net, routed by the geometric interval kernel.

:func:`route_net` is the whole of this module's public surface: it turns one
net's endpoints and one :class:`~flab2bp.sfy.layout.lattice.Occupancy` into one
query for :func:`flab2bp.layout.geometric_router.route`, and reads the answer
back the way ``routing_domain._geometric_search`` reads it -- the function this
one is modelled on, step for step.  **The router is geometric.** It works on
intervals, runs and paths; the only per-node Python here is the handful of
``opened`` nodes a net unlocks for itself and the bounded blame census a
complete exhaustion is allowed (global constraint 7).

**Nothing here is imported until it is used.**  ``flab2bp.layout``'s router and
its Cython kernel are imported INSIDE :func:`route_net` (global constraint 9):
importing this module must not drag a native extension into a process that only
wanted to talk about belts.

**What a net opens for itself.**  A belt's own path from its port to the edge of
its machine's hard box lies inside that box, and its goals may sit on a designer
wall line.  Those nodes are denied to everyone by the world, and they are opened
for ONE query by ``opened``: a private copy of
:attr:`~flab2bp.sfy.layout.lattice.Occupancy.flags` with those indices set
passable.  The shared occupancy is never written.

**What a net closes against itself.**  ``closed`` is the mirror of ``opened``,
and it is how a rip-up loop refuses a move the movement table cannot judge.  A
conveyor lift is the one such move: a per-level transition row carries a single
via, so there is nowhere in it to name the column a lift stands in, and the
kernel admits a lift on its two end nodes alone.  Realisation is what catches a
lift through a machine; it charges congestion history at those two ends -- the
nodes the kernel DOES test -- and re-queries this net with them ``closed``, a
bounded number of times, before the net is stranded.  Nothing here has to know
that: ``closed`` is just nodes this query may not stand on.

**What the ledger is charged.**  ``budget.left`` is READ before the kernel and
SET to that reading minus the kernel's own charge after it, never decremented,
so two searches sharing a ledger cannot lose a charge between them.  A ``left``
of ``None`` is unbounded and the ledger is then inert.  Routing's clock is the
explicit ``deadline`` argument, not the ledger's own field.

**What the returned path looks like.**  It is the kernel's own certified node
sequence, and it names an incline's VIA as a node of its own, on the level the
belt departs from: a climb reads as one flat grid step followed by one step that
is both along and up.  A lift, having no intermediate, reads as a single
``(0, 0, +-h)`` step.  Loops are left in -- splicing them out needs the ramp
context (``routing_domain._cut_loops``) that belongs to whoever turns a path
into belts.

**What a refusal may claim.**  Only complete exhaustion proves anything: it
alone fills :attr:`Routed.wall`, with the nodes on the boundary of the
searched component that another net is holding.  A budget or deadline stop
names nobody -- it did not finish looking.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from flab2bp.layout.budget import expired
from flab2bp.sfy.layout.lattice import Node, Occupancy
from flab2bp.sfy.layout.transitions import FLAT_STEPS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flab2bp.layout.budget import WorkBudget
    from flab2bp.layout.geometric_router import GeometricResult
    from flab2bp.layout.geometric_world import GeometricTransition

#: The movement table as the kernel takes it: a row per source level.
TransitionTable = tuple[tuple["GeometricTransition", ...], ...]

__all__ = [
    "MAX_SEARCH_WORK",
    "BudgetCause",
    "RouteFailureKind",
    "Routed",
    "TransitionTable",
    "route_net",
]

#: The most charged work one net's search may spend, whatever the ledger says.
#: The DSP router's own per-query cap: a single net that spends more than this
#: is not about to succeed, and the pass has other nets to route.
MAX_SEARCH_WORK = 200_000

#: Largest reached component a failed search will census for a wall.  A bigger
#: pocket is not a pocket; nothing it touches is the reason this net failed.
_BLAME_MAX_POCKET = 32_768

#: Largest wall a failed search will charge anybody for.  A search that dies in
#: a pocket walled by three cells has named three suspects, one of which really
#: did cut this net off; one walled by three thousand has named no one.
_BLAME_MAX_WALL = 64


class RouteFailureKind(Enum):
    """Why one net did not get a path."""

    #: The search finished and there is no path: the start's component is
    #: sealed.  The only kind that carries wall evidence.
    SEALED_POCKET = "sealed-pocket"
    #: The query was never admissible: no endpoint on the lattice, or no start
    #: a belt may stand on.  Charged nothing.
    DYNAMIC_ACCESS = "dynamic-access"
    #: A bound ended it.  Proves nothing about the net.
    BUDGET = "budget"


class BudgetCause(Enum):
    """Which bound produced a :attr:`RouteFailureKind.BUDGET`."""

    #: The work allowance -- this query's cap or the shared ledger's remainder.
    ALLOWANCE = "allowance"
    #: The wall clock.
    DEADLINE = "deadline"
    #: Not a budget refusal at all.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Routed:
    """What one net's search returned: a path, a charge, and a reason if not.

    ``work`` is the kernel's own charge in ITS unit -- scanned occupancy and
    history cells plus processed active intervals -- and is not a count of
    expanded nodes; this router expands no nodes.
    """

    path: tuple[Node, ...] | None
    kind: RouteFailureKind | None
    #: Only complete exhaustion fills this.
    wall: tuple[Node, ...]
    work: int
    cause: BudgetCause = BudgetCause.UNKNOWN

    def __post_init__(self) -> None:
        if self.cause is not BudgetCause.UNKNOWN and self.kind is not RouteFailureKind.BUDGET:
            raise ValueError("only a BUDGET search result carries a budget cause")
        if self.wall and self.kind is not RouteFailureKind.SEALED_POCKET:
            raise ValueError("only a sealed pocket has wall evidence")


def route_net(
    occupancy: Occupancy,
    *,
    starts: Collection[Node],
    goals: Collection[Node],
    opened: Collection[Node] = (),
    closed: Collection[Node] = (),
    pressure: float,
    budget: WorkBudget,
    deadline: float | None,
    transitions: TransitionTable,
    blame: dict[Node, float] | None = None,
) -> Routed:
    """The cheapest belt path from ``starts`` to ``goals``, or why there is none.

    ``opened`` is this net's own nodes -- its port reach inside its machine's
    hard box, its taps, its goals on a wall line -- made passable for this query
    alone.  ``closed`` is its mirror: nodes made IMPASSABLE for this query alone,
    which is how the rip-up loop refuses a move the movement table cannot judge.
    A lift is the case that needs it -- the kernel admits one on its two end
    nodes alone, having nowhere in a per-level row to name the column between
    them -- so a lift that realisation finds runs through a machine comes back
    as history charged at those two ends plus a re-query with them ``closed``.
    A node in both sets is closed: ``closed`` is a correction and corrections
    win.  ``pressure`` scales the per-node congestion price the kernel reads
    out of ``occupancy.history``.  ``transitions`` is
    :func:`~flab2bp.sfy.layout.transitions.sfy_transitions` for this lattice;
    it is passed rather than built so that one caller's registry-derived lift
    window serves a whole pass.  ``blame``, when given, collects a point against
    every node named in a wall.

    The returned path is the kernel's own certified sequence of nodes, loops and
    all: cutting them needs the ramp context the realiser owns, not this.
    """
    lattice = occupancy.lattice
    levels = lattice.n + 1
    if len(transitions) != levels:
        raise ValueError(
            f"the movement table has {len(transitions)} rows for a lattice of {levels} levels; "
            "the kernel needs exactly one row per level"
        )

    goal_nodes = tuple(node for node in goals if lattice.holds(node))
    if not goal_nodes:
        return Routed(None, RouteFailureKind.DYNAMIC_ACCESS, (), 0)

    flags = bytearray(occupancy.snapshot())
    for node in opened:
        if lattice.holds(node):
            flags[lattice.index(node)] = 1
    for node in closed:
        if lattice.holds(node):
            flags[lattice.index(node)] = 0
    start_nodes = tuple(
        node for node in starts if lattice.holds(node) and flags[lattice.index(node)]
    )
    if not start_nodes:
        return Routed(None, RouteFailureKind.DYNAMIC_ACCESS, (), 0)
    if budget.left is not None and budget.left <= 0:
        return Routed(None, RouteFailureKind.BUDGET, (), 0, BudgetCause.ALLOWANCE)
    if expired(deadline):
        return Routed(None, RouteFailureKind.BUDGET, (), 0, BudgetCause.DEADLINE)

    from flab2bp.layout import geometric_router
    from flab2bp.layout.geometric_world import GeometricWorld

    # The kernel returns its exact charge on every normal exit.
    start_left = budget.left if budget.left is not None else 1 << 62
    world = GeometricWorld(
        nx=levels,
        ny=levels,
        nz=levels,
        gx0=0,
        gy0=0,
        flags=flags,
        history=occupancy.history,
        transitions=transitions,
    )
    max_work = min(MAX_SEARCH_WORK, start_left)
    result = geometric_router.route(
        geometric_router.GeometricQuery(
            world=world,
            starts=tuple(lattice.index(node) for node in start_nodes),
            goals=tuple(lattice.index(node) for node in goal_nodes),
            pressure=pressure,
            max_work=max_work,
            deadline=deadline,
        )
    )
    outcome = geometric_router.summarize(result)
    work = outcome.work
    if budget.left is not None:
        budget.left = start_left - work

    if outcome.exhausted_budget or outcome.cancelled:
        # The native search raises one limit for both bounds, so read them back
        # here: it charges exactly `max_work` and stops only when the allowance
        # is what ended it, and anything short of that with an expired clock was
        # the clock.
        cause = (
            BudgetCause.ALLOWANCE if work >= max_work or deadline is None else BudgetCause.DEADLINE
        )
        return Routed(None, RouteFailureKind.BUDGET, (), work, cause)
    if outcome.path is not None:
        return Routed(tuple(world.cell(index) for index in outcome.path), None, (), work)
    wall = _wall(occupancy, result, flags, transitions, blame)
    return Routed(None, RouteFailureKind.SEALED_POCKET, wall, work)


def _wall(
    occupancy: Occupancy,
    result: GeometricResult,
    flags: bytearray,
    transitions: TransitionTable,
    blame: dict[Node, float] | None,
) -> tuple[Node, ...]:
    """Who is standing on the boundary of the component the search reached.

    The census walks the INTERVALS the kernel returned and expands each to its
    nodes only inside the generator -- the one place this milestone allows
    per-node Python above the kernel, and bounded twice: a component larger than
    :data:`_BLAME_MAX_POCKET` nodes is not censused at all, and a wall wider
    than :data:`_BLAME_MAX_WALL` nodes has named nobody and is dropped.

    Only nodes another net OWNS are named.  The net being routed is not
    committed while it is being routed, so every owner here is somebody else,
    and a node the WORLD denies -- a machine, a wall -- has no owner and is
    nobody's to give back.
    """
    reverse = result.co_reachable is not None
    reached = result.co_reachable if reverse else result.reachable
    if reached is None:
        return ()  # the kernel proved nothing, so there is nobody to name
    if sum(hi - lo + 1 for _y, _z, lo, hi in reached) > _BLAME_MAX_POCKET:
        return ()
    owner = occupancy.owner
    lattice = occupancy.lattice
    wall = {
        node
        for node in _boundary(occupancy, reached, reverse, flags, transitions)
        if lattice.codec.encode(node) in owner
    }
    if len(wall) > _BLAME_MAX_WALL:
        return ()
    named = tuple(sorted(wall))
    if blame is not None:
        for node in named:
            blame[node] = blame.get(node, 0.0) + 1.0
    return named


def _boundary(
    occupancy: Occupancy,
    reached: tuple[tuple[int, int, int, int], ...],
    reverse: bool,
    flags: bytearray,
    transitions: TransitionTable,
) -> Iterator[Node]:
    """Every impassable node touching the component the kernel settled.

    Forward exhaustion (``reachable``) keeps the cardinal ownership frontier the
    DSP router uses: a node the wavefront would have stepped onto and could not.
    Reverse exhaustion (``co_reachable``) instead exposes the PREDECESSORS of
    the goal-reaching component, so it reads the movement table backwards --
    including each incline's via, which stays on its original source level.

    ``reached`` is the kernel's own ``(y, z, lo_x, hi_x)`` intervals; each is
    expanded to its nodes here inside the generator and nowhere else.
    """
    lattice = occupancy.lattice
    settled = (
        (x * lattice.codec.rows + y) * lattice.codec.levels + z
        for y, z, lo, hi in reached
        for x in range(lo, hi + 1)
    )
    if not reverse:
        for index in settled:
            x, y, level = lattice.codec.decode(index)
            for dx, dy in FLAT_STEPS:
                node = (x + dx, y + dy, level)
                if lattice.holds(node) and not flags[lattice.codec.encode(node)]:
                    yield node
        return
    levels = lattice.n + 1
    incoming: list[list[tuple[int, int, int, bool]]] = [[] for _ in range(levels)]
    for source_level, row in enumerate(transitions):
        for dx, dy, dz, via, _cost in row:
            target = source_level + dz
            if 0 <= target < levels:
                incoming[target].append((dx, dy, source_level, via))
    for index in settled:
        x, y, level = lattice.codec.decode(index)
        for dx, dy, source_level, via in incoming[level]:
            source = (x - dx, y - dy, source_level)
            if lattice.holds(source) and not flags[lattice.codec.encode(source)]:
                yield source
            ramp_via = (x - dx // 2, y - dy // 2, source_level)
            if via and lattice.holds(ramp_via) and not flags[lattice.codec.encode(ramp_via)]:
                yield ramp_via
