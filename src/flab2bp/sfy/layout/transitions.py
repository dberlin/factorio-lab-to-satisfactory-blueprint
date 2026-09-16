"""How a Satisfactory belt moves between lattice nodes, as the kernel reads it.

The interval kernel
(:func:`flab2bp.layout._geometric_kernel.search_intervals`) takes its whole
movement graph as one table: a row per source level, each row a tuple of
``(dx, dy, dz, via, cost)``.  ``via`` marks a move that also occupies the
intermediate node ``(dx // 2, dy // 2)`` ON THE SOURCE LEVEL -- the DSP ramp's
"via" -- and ``cost`` is the forward base price before congestion.  This module
builds that table for Satisfactory, and it is the ONLY place the three ways a
belt may move are written down.

**The three families**, and where each one's shape comes from:

1. **A flat step** ``(dx, dy, 0)`` over one grid step, price ``1``.  A belt
   running along a lattice line.
2. **An incline** ``(run dx, run dy, +-1)`` with a via, price ``3``, where
   ``run`` is :func:`incline_run_nodes` READ out of ``Registry.limits`` --
   ``belt.incline`` caps a belt at ``limits.belt_max_incline_deg``, so the run
   is one level's rise over that angle's tangent, rounded up to the grid, by the
   same arithmetic as :func:`~flab2bp.sfy.layout.manifold._descent_run`.  On the
   shipped registry that is ``grid_ceil(100 / tan 35, 100)`` = 200 cm, two grid
   steps, which is exactly the DSP ramp's shape -- but it is a reading, not a
   constant, and :func:`sfy_transitions` refuses a run it cannot model rather
   than assuming this one.  The via is the node the belt passes half a level up;
   it is not a lattice node itself (its altitude is between two), so it is
   charged on the level it departs from, as DSP charges its ramp.
3. **A lift** ``(0, 0, +-h)``, price :func:`lift_cost`.  A conveyor lift is a
   pure ``(x, y)``-preserving edge whose ends may face any quarter turn, so for
   a lattice router it is a vertical edge with the turn baked in at no cost.
   Its legal heights are the caller's to compute from ``Registry.limits``
   (``lift_min_cm``, ``lift_max_cm``, ``lift_step_cm``, clamped by the
   designer) -- R-M3-3 -- and are handed in as ``lift_heights``.  Nothing here
   invents one.

**Lifts are table rows, not** ``GeometricQuery.extra_edges``.  The kernel's C++
handles a move's ``dz`` generically (``z + move.dz`` with a bounds check) rather
than assuming a unit climb, so a four-level hop decodes, prices and certifies
exactly like any other row; ``test_the_kernel_accepts_a_multi_level_vertical_move``
is the spike that proved it and is what would say so if it ever stopped being
true.  Extra edges would have cost a per-node Python dict over every passable
node, which global constraint 7 forbids and this avoids entirely.

**A ramp's via is the MIDPOINT, and it is the only intermediate node the kernel
tests.**  Measured, not assumed: with a single ``(4, 0, 1, True)`` move, blocking
the source-level node at ``+2`` makes the search exhaust, while blocking ``+1``
or ``+3`` changes nothing.  So a run of two nodes is the only run this table can
state honestly -- a longer ramp would pass over nodes the kernel never looks at,
and the search would return belts through machines.  :func:`sfy_transitions`
therefore refuses any run but two, with the reading that produced it in the
message.  Should the game's incline limit ever shallow out, the fix is a
per-node admission (``GeometricQuery.extra_edges``, or a wider via concept in
the kernel), not a wider ramp row here.

**What a lift row does NOT say, and where the column IS enforced.**  The kernel
tests the landing node of every move, and a ramp's midpoint via as well, but a
lift row names no intermediate node at all.  A per-level movement table cannot
say otherwise: a row is ``(dx, dy, dz, via, cost)`` and has exactly one via, so
there is nowhere to put the four-to-forty-eight nodes a lift's column occupies.
A lift of ``h`` is therefore admitted on the strength of its two ends alone,
even with a machine standing between them, and no commit-side rule alone can
stop the search from proposing it again next round.

The column is enforced at realisation, with learning that cannot thrash: a lift
through an occupied column is a realise-time refusal, which charges congestion
history at the lift's two END nodes -- the nodes the kernel DOES check -- and
re-queries the same net in the same round with those end nodes in
:func:`~flab2bp.sfy.layout.router.route_net`'s ``closed`` set, bounded, before
the net is stranded.  ``closed`` is the mirror of ``opened``: nodes made
impassable in one query's private flags.  Pruning per node with ``extra_edges``
is recorded as a lever, not built -- it would cost a Python dict entry per
passable node, which global constraint 7 forbids.

**The toll** (R-M3-3).  A move that lands on level ``k`` pays
``0.01 * (k - GROUND_LEVEL)`` on top of its family price, so a belt prefers the
port level and climbs only to cross something.  The toll is small enough that it
never reorders two paths of different length: a whole grid step costs ``1``.

Costs must dominate XY Manhattan displacement or the kernel's geometric lower
bound is unsound.  They do, by construction: ``1 >= 1`` for a flat step,
``3 >= 2`` for an incline and ``2 + h >= 0`` for a lift.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from flab2bp.layout.geometric_world import GeometricTransition
from flab2bp.sfy.layout.lattice import GROUND_LEVEL
from flab2bp.sfy.layout.manifold import grid_ceil

if TYPE_CHECKING:
    from flab2bp.sfy.registry import Limits

__all__ = [
    "FLAT_STEPS",
    "GROUND_LEVEL",
    "incline_run_nodes",
    "level_toll",
    "lift_cost",
    "sfy_transitions",
]

#: The only ramp run this table can state: the kernel gives a move ONE via and
#: puts it at the midpoint, so a longer ramp would cross nodes nothing tests.
_MODELLED_INCLINE_RUN = 2

#: The four ways a belt leaves a node without changing level.  Stated once: the
#: movement table is built from it and a failed search's cardinal frontier is
#: read with it, and a frontier that disagreed with the moves would blame the
#: wrong nodes.
FLAT_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))

#: What one grid step of straight run costs.
_STEP_COST = 1.0

#: What one incline costs: two grid steps of run and a level of rise, priced
#: above the two flat steps it displaces so a belt does not climb for fun.
_INCLINE_COST = 3.0

#: A lift's fixed price, before its height.  Two steps' worth: a lift is two
#: buildings' worth of connection wherever it stands, however short it is.
_LIFT_BASE_COST = 2.0

#: What a level above the ports costs, per level -- R-M3-3.
_LEVEL_TOLL = 0.01


def level_toll(level: int) -> float:
    """What landing on ``level`` costs on top of a move's own price -- R-M3-3.

    Zero at and below :data:`~flab2bp.sfy.layout.lattice.GROUND_LEVEL`: levels
    below it are impassable anyway, and a negative toll would pay a belt to
    route into the foundation.
    """
    return _LEVEL_TOLL * max(level - GROUND_LEVEL, 0)


def incline_run_nodes(limits: Limits, grid_cm: float) -> int:
    """How many grid steps of run a belt needs to climb one lattice level.

    ``belt.incline`` refuses a chord steeper than ``limits.belt_max_incline_deg``,
    so one level's rise needs that rise over the angle's tangent, rounded up to
    the grid -- the arithmetic
    :func:`~flab2bp.sfy.layout.manifold._descent_run` already uses for a
    feeder's fall, applied to one grid step of rise.  A registry that states no
    incline limit is refused rather than given one: a made-up angle is a belt
    the game would call too steep.
    """
    if limits.belt_max_incline_deg is None:
        raise ValueError(
            "the registry states no maximum belt incline, so no ramp can be sized "
            "and none may be invented here"
        )
    run_cm = grid_ceil(grid_cm / math.tan(math.radians(limits.belt_max_incline_deg)), grid_cm)
    return round(run_cm / grid_cm)


def lift_cost(height_levels: int) -> float:
    """What a conveyor lift of ``height_levels`` levels costs.

    Priced by its height so that a router reaches for the shortest lift that
    clears what is in the way, and by a fixed base so that a lift is never
    free next to the inclines it competes with.
    """
    return _LIFT_BASE_COST + float(height_levels)


def sfy_transitions(
    levels: int, lift_heights: range, incline_run: int
) -> tuple[tuple[GeometricTransition, ...], ...]:
    """The movement table for a lattice of ``levels`` levels, grouped by source.

    ``levels`` is the kernel's ``nz`` and the table must have exactly that many
    rows.  ``lift_heights`` is the legal lift window in LEVELS and ``incline_run``
    the grid steps a ramp needs per level, both as the caller read them from
    ``Registry.limits`` (:func:`incline_run_nodes`, and the ``lift_*`` window
    clamped by the designer -- R-M3-3).  An empty ``lift_heights`` is a build
    with no lifts and is perfectly legal here.  Nothing in this function is a
    physical number; it is arithmetic over what it was handed.

    A run other than :data:`_MODELLED_INCLINE_RUN` is REFUSED rather than
    approximated: the kernel gives a move one via and puts it at the midpoint,
    so a longer ramp would pass over nodes nothing tests.

    A level nothing may stand on gets an empty row and is never landed on --
    the levels below :data:`~flab2bp.sfy.layout.lattice.GROUND_LEVEL`, which are
    inside the foundation, and the lattice's topmost node, through which a
    belt's own clearance would hang (R-M3-3; see :func:`_reachable`).  The
    occupancy denies those nodes as well: this is the movement graph agreeing
    with the world rather than relying on it.
    """
    if levels <= GROUND_LEVEL + 1:
        raise ValueError(
            f"a lattice of {levels} levels has nothing a belt may stand on above the port "
            f"level {GROUND_LEVEL}, so no belt can move on it"
        )
    if lift_heights.step <= 0 or (lift_heights and lift_heights.start <= 0):
        raise ValueError(f"{lift_heights!r} is not a range of positive lift heights")
    if incline_run != _MODELLED_INCLINE_RUN:
        raise ValueError(
            f"the registry's incline limit needs {incline_run} grid steps of run per level, "
            f"but the kernel tests one via per move and puts it at the midpoint, so only a "
            f"run of {_MODELLED_INCLINE_RUN} can be stated here without admitting belts "
            f"through nodes nothing looks at"
        )
    by_level: list[tuple[GeometricTransition, ...]] = []
    for level in range(levels):
        if not _reachable(level, levels):
            by_level.append(())
            continue
        moves: list[GeometricTransition] = []
        for dx, dy in FLAT_STEPS:
            moves.append((dx, dy, 0, False, _STEP_COST + level_toll(level)))
            for rise in (1, -1):
                if _reachable(level + rise, levels):
                    moves.append(
                        (
                            incline_run * dx,
                            incline_run * dy,
                            rise,
                            True,
                            _INCLINE_COST + level_toll(level + rise),
                        )
                    )
        for height in lift_heights:
            for rise in (height, -height):
                if _reachable(level + rise, levels):
                    moves.append((0, 0, rise, False, lift_cost(height) + level_toll(level + rise)))
        by_level.append(tuple(moves))
    return tuple(by_level)


def _reachable(level: int, levels: int) -> bool:
    """Whether a move may LAND on ``level``.

    Between the port level and one short of the lattice's top node.  Both ends
    are the world's, not a preference: levels below the ports are inside the
    foundation (R-M3-3), and the topmost node is the designer's ceiling, through
    which a belt's own clearance would hang -- which is why
    :attr:`~flab2bp.sfy.layout.lattice.Lattice.open_levels` always stops one
    short of it.  A move that lands where nothing may stand is a move the kernel
    can only ever reject, and R-M3-3's ``min(48, n - 2)`` lift window and this
    ceiling have to agree or the two disagree about the tallest legal lift.
    """
    return GROUND_LEVEL <= level < levels - 1
