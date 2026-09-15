"""The corridors either side of a manifold: columns, turns, bridges and paths.

A manifold build is rows packed along ``Y`` with a corridor down each side of
them.  Every trunk belt in the build -- what a row eats, what it makes, what
arrives at the designer wall and what leaves by it -- runs up a corridor in a
*column*: a lane of one belt width at a fixed ``X``, entered and left by a
*transverse* piece that runs across the corridor at one ``Y``.

This module holds two separable things:

* **which column a belt takes**, which is arithmetic on intervals and nothing
  else -- :func:`assign_columns` never sees a belt, a row or a designer, which
  is what lets the crossing rule be tested on numbers;
* **what a corridor path looks like** once the column is known: :class:`Route`
  walks a cursor through straights, inclines and quarter turns, and
  :func:`lay_path` cuts the result into conveyors the file can hold.

**Every distance is the game's.**  The column pitch is ``belt.clearance``'s own
box plus one hologram grid step; the turn radius is ``belt_bend_radius_cm``; how
steep a climb may be is ``belt_max_incline_deg``; how short a belt may be is
``belt_min_length_cm``; how long one may be is ``belt_max_spline_cm``.  Two
numbers are this project's own and say so where they are computed: the height a
bridge stands at (:func:`bridge_z_cm`, which is the row builder's crossing gap),
and how far past a crossed column a bridge may start to come down
(:data:`BRIDGE_CLEARANCE_BOXES`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.layout.manifold import crossing_gap_cm, grid_ceil
from flab2bp.sfy.layout.model import BeltRun, Link, SplinePoint, Vector, belt_ends
from flab2bp.sfy.layout.splines import concat, incline, quarter_turn, spline_length, straight
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import Registry

__all__ = [
    "BRIDGE_CLEARANCE_BOXES",
    "Assignment",
    "ColumnRequest",
    "CorridorError",
    "Interval",
    "LaidPath",
    "Route",
    "assign_columns",
    "attachment_pitch_cm",
    "belt_pitch_cm",
    "bridge_z_cm",
    "descent_run_cm",
    "lay_path",
    "turn_radius_cm",
]

BRIDGE_CLEARANCE_BOXES = 2.0
"""How many belt clearance half-widths past a crossed column a bridge may begin
to come down.

Ours.  ``belt.clearance`` gives a belt 79 cm to each side of its centreline, so
the crossed column's lane and the bridge's own lane are clear of each other once
their centrelines are two half-widths apart -- and only then may the bridge give
up any of the crossing gap it climbed for.
"""

_EPS = 1e-9


class CorridorError(ValueError):
    """A corridor this module will not lay, with the cause the strategy names.

    ``cause`` is a discriminator, not prose, and there are seven:

    * ``"width"`` -- no column left in the corridor to put a belt in;
    * ``"bridge"`` -- the only columns left need a crossing that will not fit;
    * ``"depth"`` -- two things the corridor has to join are closer together
      along ``Y`` than the turns between them are long;
    * ``"backwards"`` -- something the trunk has to meet stands outside the span
      it runs through, so serving it would mean running back down the corridor;
    * ``"limits"`` -- ``registry.json`` states no bound this module needs, which
      is a hole in the extraction rather than a fact about the build;
    * ``"path"`` -- a path with no length at all, which is two ports in the same
      place and a fault in the caller rather than in the designer;
    * ``"curve"`` -- a curved leg longer than a belt may be, which this module
      will not cut because cutting it would mean guessing at tangents.  No
      corridor this module BUILDS can raise it today: every curve it lays is a
      quarter turn of ``turn_radius_cm``, about 628 cm of arc, against a
      ``belt_max_spline_cm`` of 5600.1 -- an order of magnitude of headroom.
      It is reachable only from a route handed to :func:`lay_path` directly,
      which is how it is tested, and it is kept because the bound is the game's
      and the radius is not: a wider turn, or a tier with a shorter spline,
      would make it a real refusal rather than a guard.

    :mod:`flab2bp.sfy.layout.strategy` turns each into the refusal the caller
    sees, so the refusal strings live in one place.
    """

    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(detail)
        self.cause = cause
        self.detail = detail


# --- the numbers a corridor stands on --------------------------------------


def _grid(registry: Registry) -> float:
    grid = registry.limits.hologram_grid_cm
    if grid is None:
        raise CorridorError("limits", "the registry states no hologram grid")
    return grid


def belt_pitch_cm(registry: Registry) -> float:
    """How far apart two corridor columns stand, centre to centre.

    Two belt boxes side by side and one hologram grid step between them:
    ``belt.clearance`` puts a belt's box 79 cm out on each side, so two lanes
    closer than 158 cm lap, and the grid step is the game's own smallest move
    rather than a gap chosen here.
    """
    grid = _grid(registry)
    return grid_ceil(2.0 * BELT_CLEARANCE_HALF_WIDTH_CM + grid, grid)


def turn_radius_cm(registry: Registry) -> float:
    """The radius every corridor-to-row turn is built on.

    ``belt.curvature`` refuses a turn tighter than ``mBendRadius * 1.5 - 15``
    measured as arc over angle, and a quarter circle of radius ``R`` measures
    exactly ``R``.  Twice ``belt_bend_radius_cm``, on the grid, clears that floor
    with room for the sampling to wander and lands every turn on the grid the
    attachments stand on.
    """
    bend = registry.limits.belt_bend_radius_cm
    if bend is None:
        raise CorridorError("limits", "the registry states no belt bend radius")
    return grid_ceil(2.0 * bend, _grid(registry))


def attachment_pitch_cm(registry: Registry) -> float:
    """How far apart two splitters (or two mergers) stand along a column.

    The brief asks for one grid pitch, and the game's own geometry will not have
    it: a splitter's two through ports are 100 cm out on either side, so two of
    them one grid step apart would want a belt of minus one metre between them,
    and ``belt.min_length`` refuses anything at or under ``belt_min_length_cm``.
    This is the smallest grid multiple that leaves a legal belt between two
    attachments, and both numbers in it are read.
    """
    floor = registry.limits.belt_min_length_cm
    if floor is None:
        raise CorridorError("limits", "the registry states no minimum belt length")
    return grid_ceil(2.0 * _through_port_offset(registry) + floor, _grid(registry))


def _through_port_offset(registry: Registry) -> float:
    """How far out a conveyor attachment's through ports sit, from the registry."""
    splitter = registry.buildables["Build_ConveyorAttachmentSplitter_C"]
    return max(abs(port.translation[0]) for port in splitter.ports if port.kind == "belt")


def bridge_z_cm(belt_z: float, registry: Registry) -> float:
    """How high a transverse stands to cross an occupied column.

    One crossing gap above the corridor's own height, which is the row builder's
    :func:`~flab2bp.sfy.layout.manifold.crossing_gap_cm`: twice a belt clearance
    box's height, so two belts crossing have a whole box of air between their
    centrelines.  Ours, and derived rather than chosen.
    """
    return belt_z + crossing_gap_cm(registry)


def descent_run_cm(rise: float, registry: Registry) -> float:
    """The shortest run, on the grid, a belt may climb or fall ``rise`` over.

    ``belt.incline`` refuses a chord steeper than ``belt_max_incline_deg``, so
    the run is the rise over that angle's tangent, rounded up to the grid.
    """
    if abs(rise) <= _EPS:
        return 0.0
    limit = registry.limits.belt_max_incline_deg
    if limit is None:
        raise CorridorError("limits", "the registry states no maximum belt incline")
    return grid_ceil(abs(rise) / math.tan(math.radians(limit)), _grid(registry))


@dataclass(frozen=True, slots=True)
class Measures:
    """Every number a manifold build is laid out against, read once.

    Apart from :attr:`half` -- the designer's own half width -- these are all the
    corridor's, and all of them come out of ``registry.json``.  The value lives
    here rather than beside the build because the row planner, the net planner
    and the corridor layer are each handed the same one:
    :func:`flab2bp.sfy.layout.strategy._measure` reads it at the top of a run and
    nothing below that line reads a limit again, so no two stages can be laid out
    against different numbers.
    """

    #: The hologram grid every object stands on.
    grid: float
    #: The radius every corridor-to-row turn is built on.
    radius: float
    #: How far apart two corridor columns stand, centre to centre.
    pitch: float
    #: How far apart two splitters (or two mergers) stand along a column.
    node_pitch: float
    #: The shortest run between a corridor attachment's port and a turn or another.
    lead: float
    #: How far a belt runs flat out of a port before it starts to climb.
    lead_in: float
    #: Half the designer's own side, which is where its walls are.
    half: float


# --- which column a belt takes ---------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    """One belt's occupancy of one column: the ``Y`` it runs through.

    ``y_a`` is where it enters the corridor and ``y_b`` where it leaves; which
    is the greater depends on which way the belt runs, and every question this
    class answers is about the span between them.
    """

    column: int
    y_a: float
    y_b: float
    belt: str

    @property
    def low(self) -> float:
        return min(self.y_a, self.y_b)

    @property
    def high(self) -> float:
        return max(self.y_a, self.y_b)

    def strictly_contains(self, y: float, margin: float = 0.0) -> bool:
        """Whether a transverse at ``y`` would have to get past this belt."""
        return self.low - margin < y < self.high + margin

    def overlaps(self, other: Interval, margin: float = 0.0) -> bool:
        """Whether two belts would be in the same lane at the same ``Y``."""
        return self.low - margin < other.high and other.low < self.high + margin


@dataclass(frozen=True, slots=True)
class ColumnRequest:
    """One corridor belt asking for a column.

    ``y_a`` and ``y_b`` are the two ends, each of which reaches across the
    corridor to a row or to a wall.  ``taps`` are the ``Y`` of every FURTHER
    transverse the belt has: a spine with a merger or a splitter on it reaches
    into a row there too, and that reach crosses whatever stands inside it
    exactly as its two ends do.

    A belt never has to get past ITSELF, which is the only exclusion the crossing
    test needs: a tap leaves the very column the belt is in.
    """

    belt: str
    y_a: float
    y_b: float
    taps: tuple[float, ...] = ()

    @property
    def low(self) -> float:
        return min(self.y_a, self.y_b)

    @property
    def high(self) -> float:
        return max(self.y_a, self.y_b)


@dataclass(frozen=True, slots=True)
class Assignment:
    """The column one request got, and what each of its transverses has to bridge.

    ``bridged_taps`` runs parallel to the request's own ``taps``.
    """

    request: ColumnRequest
    column: int
    bridged_a: tuple[int, ...] = ()
    bridged_b: tuple[int, ...] = ()
    bridged_taps: tuple[tuple[int, ...], ...] = ()

    @property
    def belt(self) -> str:
        return self.request.belt

    @property
    def interval(self) -> Interval:
        return Interval(self.column, self.request.y_a, self.request.y_b, self.request.belt)


Bridgeable = Callable[[ColumnRequest, int], bool]


def assign_columns(
    requests: Sequence[ColumnRequest],
    *,
    columns: int,
    placed: Sequence[Interval] = (),
    margin: float = 0.0,
    bridgeable: Bridgeable | None = None,
    budget: WorkBudget | None = None,
) -> tuple[Assignment, ...]:
    """Give every request a column, innermost first, in order of ``y_b``.

    The rule, from the inside out.  A belt may take column ``c`` when no belt
    already in ``c`` runs through the same ``Y``, and every occupied column
    between ``c`` and the row -- one its transverses have to cross -- can be
    bridged.  Columns are tried innermost first, so the answer is the innermost
    column with room; a crossing that ``bridgeable`` turns down refuses the
    whole corridor rather than being quietly driven through.

    Moving further out never undoes a crossing: the columns a transverse crosses
    are the occupied ones inside the column it lands in, so the set only grows.
    That is why "the innermost column where no bridge is needed, else the
    innermost that can be bridged" is one loop and not two.

    ``margin`` widens every interval it is compared against, and is the caller's
    way of saying how wide the belts really are: a transverse level with another
    trunk's END still has to get past its clearance box.

    ``placed`` is what already stands in the corridor, so a second call can add
    branches to the spines a first call assigned.  ``budget`` is charged one
    ``assignments`` unit per column tried, which is what bounds the search.
    """
    decide = bridgeable if bridgeable is not None else _always
    standing = list(placed)
    out: list[Assignment] = []
    for request in sorted(requests, key=lambda r: (r.high, r.low, r.belt)):
        out.append(_place(request, standing, columns, margin, decide, budget))
        standing.append(out[-1].interval)
    return tuple(out)


def _always(request: ColumnRequest, column: int) -> bool:
    return True


def _place(
    request: ColumnRequest,
    standing: Sequence[Interval],
    columns: int,
    margin: float,
    bridgeable: Bridgeable,
    budget: WorkBudget | None,
) -> Assignment:
    refused: list[int] = []
    for column in range(columns):
        if budget is not None:
            budget.charge("assignments")
        if any(
            other.column == column and other.overlaps(_as_interval(request, column), margin)
            for other in standing
        ):
            continue
        crossed_a = _crossed(request, standing, column, request.y_a, margin)
        crossed_b = _crossed(request, standing, column, request.y_b, margin)
        taps = tuple(_crossed(request, standing, column, y, margin) for y in request.taps)
        rejected = [
            c
            for c in {*crossed_a, *crossed_b, *(c for tap in taps for c in tap)}
            if not bridgeable(request, c)
        ]
        if rejected:
            refused.extend(rejected)
            continue
        return Assignment(request, column, crossed_a, crossed_b, taps)
    if refused:
        raise CorridorError(
            "bridge",
            f"{request.belt} would have to bridge column(s) {sorted(set(refused))} and the "
            "crossing does not fit",
        )
    raise CorridorError(
        "width",
        f"{request.belt} spans y {request.low:.0f} to {request.high:.0f} and every one of "
        f"the {columns} columns in its corridor is taken",
    )


def _as_interval(request: ColumnRequest, column: int) -> Interval:
    return Interval(column, request.y_a, request.y_b, request.belt)


def _crossed(
    request: ColumnRequest,
    standing: Sequence[Interval],
    column: int,
    y: float,
    margin: float,
) -> tuple[int, ...]:
    """Which occupied columns a transverse at ``y`` has to get past.

    Every column inside the one the belt lands in whose belt runs through that
    ``Y``, and the belt's own column is not one of them: a tap leaves the column
    it is part of.
    """
    return tuple(
        sorted(
            {
                other.column
                for other in standing
                if other.column < column
                and other.belt != request.belt
                and other.strictly_contains(y, margin)
            }
        )
    )


# --- what a path looks like ------------------------------------------------


@dataclass
class Route:
    """A belt path under construction: a cursor and the legs it has walked.

    Every leg is a spline piece from :mod:`flab2bp.sfy.layout.splines` -- the
    shapes the game's own router builds -- and consecutive legs share their end
    points, so :func:`concat` joins them into one spline without inventing a
    segment.
    """

    point: Vector
    heading: Vector
    legs: list[tuple[SplinePoint, ...]] = field(default_factory=list)

    def go(self, distance: float, rise: float = 0.0) -> None:
        """Run ``distance`` along the current heading, climbing ``rise`` over it.

        A run of nothing is nothing: a turn that lands exactly on the port it was
        aimed at needs no straight after it, and a zero-length spline piece has
        no direction to be built from.
        """
        if distance <= _EPS:
            if abs(rise) > _EPS:
                raise CorridorError(
                    "bridge", f"a belt cannot climb {rise:.0f} cm over no run at all"
                )
            return
        leg = (
            incline(self.point, self.heading, distance, rise)
            if abs(rise) > _EPS
            else straight(self.point, self.heading, distance)
        )
        self.legs.append(leg)
        self.point = leg[-1][0]

    def turn(self, left: bool, radius: float) -> None:
        """A horizontal quarter circle onto the heading ``left`` points at."""
        leg = quarter_turn(self.point, self.heading, left, radius)
        self.legs.append(leg)
        self.point = leg[-1][0]
        self.heading = leg[-1][2]


def _is_straight(leg: tuple[SplinePoint, ...]) -> bool:
    """Whether a two-point leg is the straight one between its two points.

    Both tangents at the seam -- the first point's leave and the second's arrive
    -- point along the chord, which is what
    :func:`~flab2bp.sfy.layout.splines.straight` builds and what
    :func:`~flab2bp.sfy.layout.splines.quarter_turn` deliberately does not.
    """
    head, tail = leg[0][0], leg[1][0]
    chord = (tail[0] - head[0], tail[1] - head[1], tail[2] - head[2])
    span = math.sqrt(sum(value * value for value in chord))
    if span <= _EPS:
        return False
    unit = tuple(value / span for value in chord)
    for tangent in (leg[0][2], leg[1][1]):
        reach = math.sqrt(sum(value * value for value in tangent))
        if reach <= _EPS:
            return False
        if sum(tangent[i] * unit[i] for i in range(3)) / reach < 1.0 - 1e-9:
            return False
    return True


def _split(leg: tuple[SplinePoint, ...], limit: float) -> list[tuple[SplinePoint, ...]]:
    """``leg`` in pieces no longer than ``limit``, or as it was if it is short enough.

    Only a straight leg is ever long enough to want this -- a quarter turn of the
    corridor's radius is a few metres of arc against a limit of tens -- and a
    straight leg is two points with the chord between them, so the pieces are the
    chord's own fractions and every one of them is the shape
    :func:`~flab2bp.sfy.layout.splines.straight` builds.  A CURVED leg past the
    limit is refused rather than cut: cutting it would mean guessing tangents,
    and nothing in this package makes one.

    Straight is asked of the TANGENTS, not of the number of points: a quarter turn
    is two points as well, and cutting one along its chord would hand back a
    straight belt where a turn was asked for.
    """
    length = spline_length(leg)
    if length <= limit:
        return [leg]
    if len(leg) != 2 or not _is_straight(leg):
        raise CorridorError(
            "curve",
            f"a curved leg of {length:.0f} cm is past the {limit:.0f} cm a belt may be, and "
            "this module will not guess at the tangents to cut one",
        )
    head, tail = leg[0][0], leg[1][0]
    pieces = math.ceil(length / limit)
    if length / pieces >= limit:
        # A length that divides exactly lands ON the bound, where floating point
        # can put the measured arc either side of it and ``belt.max_length``'s
        # comparison is strict.  One more piece costs a belt and settles it.
        pieces += 1
    step: Vector = (
        (tail[0] - head[0]) / pieces,
        (tail[1] - head[1]) / pieces,
        (tail[2] - head[2]) / pieces,
    )
    out: list[tuple[SplinePoint, ...]] = []
    for piece in range(pieces):
        start: Vector = (
            head[0] + piece * step[0],
            head[1] + piece * step[1],
            head[2] + piece * step[2],
        )
        out.append(
            incline(start, (step[0], step[1], 0.0), math.hypot(step[0], step[1]), step[2])
            if abs(step[2]) > _EPS
            else straight(start, step, math.dist((0.0, 0.0, 0.0), step))
        )
    return out


@dataclass(frozen=True, slots=True)
class LaidPath:
    """One corridor path as conveyors: the belts and the links that wire them."""

    belts: tuple[BeltRun, ...]
    links: tuple[Link, ...]


def lay_path(
    route: Route,
    *,
    registry: Registry,
    class_name: str,
    item_id: str,
    rate: Fraction,
    ids: Iterator[int],
    upstream: tuple[int, str] | None,
    downstream: tuple[int, str] | None,
) -> LaidPath:
    """Cut ``route`` into belts, wire them end to end, and wire the two ends.

    A path longer than ``belt_max_spline_cm`` is more than one conveyor, because
    that is the bound ``belt.max_length`` holds every belt to; the cut falls at a
    leg boundary, where two pieces already share a point, so the two belts meet
    exactly and ``ports.position`` has nothing to report.  A single leg longer
    than the limit is cut inside itself first, by :func:`_split`.

    ``upstream`` and ``downstream`` are the ports the path starts and ends on;
    ``None`` means the designer wall, and the belt that end belongs to is flagged
    as a boundary end so that ``ports.connected_once`` expects no link there and
    ``flow.boundary`` holds it to the wall.
    """
    # ``belt_max_spline_cm`` is one of the four limits the public headers state
    # outright, so ``Limits`` gives it a default and it is never ``None`` -- which
    # is why there is no guard here and why ``mypy`` would call one unreachable.
    limit = registry.limits.belt_max_spline_cm
    chunks: list[list[tuple[SplinePoint, ...]]] = []
    walked = 0.0
    for long in route.legs:
        for leg in _split(long, limit):
            length = spline_length(leg)
            if chunks and walked + length > limit:
                chunks.append([leg])
                walked = length
            else:
                if not chunks:
                    chunks.append([])
                chunks[-1].append(leg)
                walked += length
    if not chunks or not chunks[0]:
        raise CorridorError("path", f"the path for {item_id!r} has no length at all")
    entry, exit_end = belt_ends(registry, class_name)
    belts: list[BeltRun] = []
    links: list[Link] = []
    for index, chunk in enumerate(chunks):
        belt = BeltRun(
            id=next(ids),
            class_name=class_name,
            points=concat(*chunk),
            item_id=item_id,
            items_per_second=rate,
            boundary_start=upstream is None and index == 0,
            boundary_end=downstream is None and index == len(chunks) - 1,
        )
        if belts:
            links.append(Link(a=(belts[-1].id, exit_end), b=(belt.id, entry)))
        belts.append(belt)
    if upstream is not None:
        links.append(Link(a=upstream, b=(belts[0].id, entry)))
    if downstream is not None:
        links.append(Link(a=(belts[-1].id, exit_end), b=downstream))
    return LaidPath(tuple(belts), tuple(links))
