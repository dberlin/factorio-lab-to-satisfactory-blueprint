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
2. **An incline** ``(2 dx, 2 dy, +-1)`` with a via, price ``3``.  ``belt.incline``
   caps a belt at ``limits.belt_max_incline_deg`` = 35 degrees, and
   ``grid_ceil(100 / tan 35, 100)`` is 200 cm of run per 100 cm of rise -- two
   grid steps per level, which is exactly the DSP ramp's shape.  The via is the
   node the belt passes half a level up; it is not a lattice node itself (its
   altitude is between two), so it is charged on the level it departs from, as
   DSP charges its ramp.
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

**What a lift row does NOT say.**  The kernel tests the landing node of every
move, and an incline's via as well, but a lift row names no intermediate node --
so a lift of ``h`` is admitted on the strength of its two ends alone, even if
something stands in the column between them.  That is the realiser's to check:
a committed lift must hold its whole column, and the occupancy's shadow must
grow to match, or the search will keep proposing lifts through machines.  It is
recorded here rather than fixed here because the fix is a commit-side rule, not
a movement.

**The toll** (R-M3-3).  A move that lands on level ``k`` pays
``0.01 * (k - GROUND_LEVEL)`` on top of its family price, so a belt prefers the
port level and climbs only to cross something.  The toll is small enough that it
never reorders two paths of different length: a whole grid step costs ``1``.

Costs must dominate XY Manhattan displacement or the kernel's geometric lower
bound is unsound.  They do, by construction: ``1 >= 1`` for a flat step,
``3 >= 2`` for an incline and ``2 + h >= 0`` for a lift.
"""

from __future__ import annotations

from flab2bp.layout.geometric_world import GeometricTransition
from flab2bp.sfy.layout.lattice import GROUND_LEVEL

__all__ = ["FLAT_STEPS", "GROUND_LEVEL", "level_toll", "lift_cost", "sfy_transitions"]

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


def lift_cost(height_levels: int) -> float:
    """What a conveyor lift of ``height_levels`` levels costs.

    Priced by its height so that a router reaches for the shortest lift that
    clears what is in the way, and by a fixed base so that a lift is never
    free next to the inclines it competes with.
    """
    return _LIFT_BASE_COST + float(height_levels)


def sfy_transitions(
    levels: int, lift_heights: range
) -> tuple[tuple[GeometricTransition, ...], ...]:
    """The movement table for a lattice of ``levels`` levels, grouped by source.

    ``levels`` is the kernel's ``nz`` and the table must have exactly that many
    rows.  ``lift_heights`` is the legal lift window in LEVELS, as the caller
    read it from ``Registry.limits`` -- an empty range is a build with no lifts
    and is perfectly legal here.

    Levels below :data:`~flab2bp.sfy.layout.lattice.GROUND_LEVEL` get an empty
    row: a belt may not stand there (R-M3-3), so there is no move OUT of one,
    and for the same reason no move lands on one either.  The occupancy denies
    those nodes as well -- this is the movement graph agreeing with the world
    rather than relying on it.
    """
    if levels <= GROUND_LEVEL:
        raise ValueError(
            f"a lattice of {levels} levels has nothing at or above the port level "
            f"{GROUND_LEVEL}, so no belt can move on it"
        )
    if lift_heights.step <= 0 or (lift_heights and lift_heights.start <= 0):
        raise ValueError(f"{lift_heights!r} is not a range of positive lift heights")
    by_level: list[tuple[GeometricTransition, ...]] = []
    for level in range(levels):
        if level < GROUND_LEVEL:
            by_level.append(())
            continue
        moves: list[GeometricTransition] = []
        for dx, dy in FLAT_STEPS:
            moves.append((dx, dy, 0, False, _STEP_COST + level_toll(level)))
            for rise in (1, -1):
                if _reachable(level + rise, levels):
                    moves.append(
                        (2 * dx, 2 * dy, rise, True, _INCLINE_COST + level_toll(level + rise))
                    )
        for height in lift_heights:
            for rise in (height, -height):
                if _reachable(level + rise, levels):
                    moves.append((0, 0, rise, False, lift_cost(height) + level_toll(level + rise)))
        by_level.append(tuple(moves))
    return tuple(by_level)


def _reachable(level: int, levels: int) -> bool:
    """Whether a move may LAND on ``level``: on the lattice, and not below the ports."""
    return GROUND_LEVEL <= level < levels
