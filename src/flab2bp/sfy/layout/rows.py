"""How many rows each group is laid as, and where every one of them stands.

One group is one *row* -- :func:`~flab2bp.sfy.layout.manifold.build_row`'s line
of machines, fed by splitter chains and drained by a merger chain -- unless it is
longer than the floor between the two corridors, in which case it is laid as
several rows of the same recipe.  How much floor there IS between the corridors
depends on how wide the corridors turn out to be, and that is not known until the
trunks are assigned, so :meth:`RowPlanner._plan_rows` walks the two to a fixed
point: lay the rows, ask the corridor planner what it took, split again if what
is left over no longer holds them.

Where the numbers come from
---------------------------
Every distance a row is built on is the row builder's, which reads the registry's
clearance boxes and ports, and every distance the band is measured with is
:class:`~flab2bp.sfy.layout.corridors.Measures`.  The choices that are this
project's own say so where they are made: how much floor is left at each wall for
a belt to turn into a row (:func:`_wall_margin`), how much between two rows for a
trunk to turn out of one and into the next (:func:`_row_gap`), that the rows are
centred rather than pushed to one side, and the frame a row is measured in before
it is moved (:func:`_measuring_designer`).  The grid helpers everything here
rounds with live here too, because the band is the thing that is measured on the
grid.

Nothing here decides what the game accepts.  Where the rows cannot be stood this
refuses through the one refusal builder :mod:`flab2bp.sfy.layout.strategy` hands
it, so every cause is one of that module's named ones.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.corridors import COLUMN_LANE_CM, Measures, Turn, choose_turn
from flab2bp.sfy.layout.manifold import (
    ChainEnd,
    RowError,
    RowGeometry,
    build_pair,
    build_row,
    hard_footprint_cm,
    machine_pitch_cm,
)
from flab2bp.sfy.layout.model import Pose
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import Buildable, Registry
from flab2bp.sfy.spec import Designer, DirectPair, SfyBuildSpec, SfyMachineGroup, direct_pairs

if TYPE_CHECKING:  # the corridor planner is handed to the walk, never imported by it
    from flab2bp.sfy.layout.nets import CorridorPlan

__all__ = ["RowPlan", "RowPlanner"]

SPLIT_ROUNDS = 6
"""How many times the row plan and the corridor width may chase each other.

Ours, and a backstop rather than a bound: splitting a group only ever adds rows,
which only ever narrows them, so a pass that does not settle the width makes the
next one's job easier and the walk is monotone.  Two passes settle every build in
the corpus; six is room for a build whose corridors grow twice on the way.

Running out of passes refuses with ``row splitting did not converge`` rather than
``rows exceed the designer width``, because it is a statement about this module
and not about the designer: nothing here has shown the build does not fit.
"""

_EPS = 1e-6
"""How close to a bound counts as inside it, in centimetres.

The same tolerance :mod:`~flab2bp.sfy.layout.nets` and
:mod:`~flab2bp.sfy.layout.laying` compare with.
"""


@dataclass(frozen=True, slots=True)
class _Unit:
    """One thing to lay as a row: a group, or a pair laid facing itself.

    A pair is :class:`~flab2bp.sfy.spec.DirectPair`'s two groups, and it is one
    unit because they stand as one row: producers along one line, consumers along
    another, one straight belt from each to its partner and nothing between them.
    """

    groups: tuple[SfyMachineGroup, ...]
    pair: DirectPair | None = None

    @property
    def count(self) -> int:
        """How many machines stand along the widest line of this unit."""
        return max(group.count for group in self.groups)


@dataclass(frozen=True, slots=True)
class _Row:
    """One unit's row, where it finally stands."""

    index: int
    groups: tuple[SfyMachineGroup, ...]
    geometry: RowGeometry
    flip: bool
    #: The item a paired row hands machine to machine, if it is one.
    paired_item: str = ""


@dataclass(frozen=True, slots=True)
class RowPlan:
    """The rows as they finally stand, and what the corridors are laid against.

    The three numbers beside the rows are the ones every later stage asks for and
    none is a property of a single row: :attr:`belt_z` is how high the corridors
    run, which is the highest any row's chain ends stand, :attr:`x_edge` is how
    far out of the middle of the floor the rows reach, which is where the
    innermost column is measured from, and :attr:`band_cm` is the depth the whole
    build takes -- the rows, the gaps their trunks turn in, and the margin at
    each wall -- which is the number the designer's own depth is held against.
    """

    rows: tuple[_Row, ...]
    belt_z: float
    x_edge: float
    band_cm: float


@dataclass
class RowPlanner:
    """The rows: how many each group is laid as, and where each one stands."""

    spec: SfyBuildSpec
    designer: Designer
    registry: Registry
    lab_map: LabMap
    budget: WorkBudget
    measures: Measures
    #: The one place a refusal is built, handed down by
    #: :mod:`flab2bp.sfy.layout.strategy` so that no module below it can invent a
    #: cause the strategy has not named.
    refuse: Callable[[str, str], NoValidLayout]
    #: The build's object numbering.  Every pass of the walk starts it again,
    #: because the rows it built last time are thrown away; the counter the walk
    #: ends on is the one the rest of the build carries on from.
    ids: Iterator[int] = field(default_factory=lambda: itertools.count(1))

    def _plan_rows(
        self, plan_columns: Callable[[RowPlan], CorridorPlan]
    ) -> tuple[RowPlan, CorridorPlan]:
        """Lay the rows out, splitting any that are longer than the wall.

        A group wider than the floor between the two corridors is laid as several
        rows of the same recipe (:func:`_split_group`), which the corridor already
        knows how to feed: the split rows are several sinks of one item, and one
        source with several sinks is a splitter chain.

        How wide the floor between the corridors IS depends on how many columns
        the corridors need, and that is known only once the rows are placed and
        the trunks assigned -- so this walks to a fixed point rather than guessing
        at it.  The first pass assumes the narrowest corridor there can be (one
        column, at the closest a column may stand), lays the rows, measures what
        the corridors really took, and splits again if the rows no longer fit.
        Splits only ever grow, so the walk ends: either the rows fit inside what
        the corridors left, or a single machine does not and the refusal names its
        class.

        ``plan_columns`` is how the walk measures a corridor -- the net planner's
        own pass over the rows it has just laid.  It is handed in rather than
        reached for, because the corridor is laid out against the rows and this
        module knows nothing about trunks.  The plan it returns for the pass that
        settles is the one the build goes on to draw, which is why both come back.

        A PAIR is one unit and is never split: the whole point of it is that
        machine *i* faces machine *i*, and splitting the two halves into
        different numbers of rows takes that away.  Where a pair does not fit the
        floor, the pairing is given up and its two groups go back to being two
        rows the manifold feeds, which always fits if anything does.
        """
        units = _units(_production_order(self.spec.groups), direct_pairs(self.spec))
        allowance = COLUMN_LANE_CM + max(BELT_CLEARANCE_HALF_WIDTH_CM, self.measures.box)
        splits = [1] * len(units)
        for _ in range(SPLIT_ROUNDS):
            self.budget.check()
            splits = self._splits(units, splits, allowance)
            if any(
                unit.pair is not None and rows > 1 for unit, rows in zip(units, splits, strict=True)
            ):
                units = _unpaired(units)
                splits = [1] * len(units)
                continue
            self.ids = itertools.count(1)
            rows, x_edge, band = self._lay_rows(units, splits)
            plan = RowPlan(
                rows=tuple(rows),
                belt_z=max(row.geometry.belt_z_cm for row in rows),
                x_edge=x_edge,
                band_cm=band,
            )
            corridors = plan_columns(plan)
            # A column is not just a belt lane: mergers, splitters and the
            # attachments that turn a belt all stand IN one, and their box is
            # wider than a belt's.  ``geom.bounds`` measures every box, soft ones
            # included, so what the designer has to hold is the widest of them.
            wanted = corridors.reach_cm + max(BELT_CLEARANCE_HALF_WIDTH_CM, self.measures.box)
            if plan.x_edge + wanted <= self.measures.half + _EPS:
                return plan, corridors
            allowance = _grid_ceil(wanted, self.measures.grid)
        raise self.refuse(
            "row splitting did not converge",
            f"the rows and the corridors they need did not settle inside the "
            f"{self.designer.mark} designer in {SPLIT_ROUNDS} passes",
        )

    def _splits(self, units: Sequence[_Unit], splits: Sequence[int], allowance: float) -> list[int]:
        """How many rows each unit is laid as, given what the corridors take.

        ``per_row`` is what the floor between the corridors holds: a row of ``n``
        machines is ``(n - 1)`` pitches plus one machine's own hard footprint, and
        both numbers are the registry's through the row builder.  A count never
        falls -- a later pass has less floor to play with, not more -- so the walk
        in :meth:`_plan_rows` cannot oscillate.

        A PAIR is measured at the wider of its two machines, on both counts: its
        two lines share one pitch so that machine *i* faces machine *i*, and that
        pitch is the wider machine's.
        """
        usable = 2.0 * (self.measures.half - allowance)
        out: list[int] = []
        for unit, before in zip(units, splits, strict=True):
            machines = [_machine(self.registry, group.machine_class) for group in unit.groups]
            pitch = max(machine_pitch_cm(machine, self.registry.limits) for machine in machines)
            wide = max(box[2] - box[0] for box in map(hard_footprint_cm, machines))
            per_row = int(math.floor((usable - wide) / pitch + _EPS)) + 1
            if per_row < 1:
                raise self.refuse(
                    "rows exceed the designer width",
                    f"one {unit.groups[0].machine_class} is {wide:.0f} cm wide and the "
                    f"{self.designer.mark} designer leaves {usable:.0f} cm of floor between "
                    "its two corridors, so not even one of them fits in a row",
                )
            out.append(max(before, -(-unit.count // per_row)))
        return out

    def _lay_rows(
        self, units: Sequence[_Unit], splits: Sequence[int]
    ) -> tuple[list[_Row], float, float]:
        """Build every row and stand it where it belongs on the floor.

        ``splits[i]`` is how many rows unit ``i`` is laid as.  Every row of one
        group stands next to its siblings and shares their ``flip``, which is what
        puts all their chain inputs in one corridor and all their merger outputs
        in the other: one splitter chain can then feed the lot and one merger
        chain can drain it.  **The flip alternates per UNIT, not per row** --
        which for a build that splits nothing is the same thing, and for one that
        splits is the only assignment that works, because two rows facing opposite
        corridors cannot be fed from one trunk.

        Returns the rows, how far out of the middle the widest of them reaches --
        which is what the corridor columns are measured from -- and the whole
        band the build takes along ``Y``.
        """
        measuring = _measuring_designer(self.designer)
        shares = [
            (index, share)
            for index, (unit, rows) in enumerate(zip(units, splits, strict=True))
            for share in _split_unit(unit, rows)
        ]
        built: list[RowGeometry] = []
        for parent, share in shares:
            # The clock bounds the whole of lay_out, not only the column search:
            # a row is the largest piece of work this module does in one step.
            self.budget.check()
            built.append(
                build_pair(
                    share.pair,
                    self.registry,
                    designer=measuring,
                    belt_tiers=self.spec.belt_tiers,
                    flip=bool(parent % 2),
                    next_id=self.ids,
                    lab_map=self.lab_map,
                )
                if share.pair is not None
                else build_row(
                    share.groups[0],
                    self.registry,
                    designer=measuring,
                    belt_tiers=self.spec.belt_tiers,
                    flip=bool(parent % 2),
                    next_id=self.ids,
                    lab_map=self.lab_map,
                )
            )
        width = max(row.width_cm for row in built)
        # Which turn a trunk will be made of is the drawing's choice, and it is
        # made with the room that turn really has.  What a row plan can say is
        # which turn the drawing would take if nothing were in its way, which is
        # the cheapest one; reserving for that is what lets a build fit at all,
        # and a turn that then cannot be drawn refuses with its centimetres.
        turn = choose_turn(self.measures, math.inf, math.inf)
        gaps = [
            _row_gap(
                before,
                after,
                turn,
                self.measures.grid,
                siblings=shares[index][0] == shares[index + 1][0],
            )
            for index, (before, after) in enumerate(itertools.pairwise(built))
        ]
        low = _wall_margin(built[0], turn, self.measures.grid, entry=True)
        high = _wall_margin(built[-1], turn, self.measures.grid, entry=False)
        deep = low + sum(row.depth_cm for row in built) + sum(gaps) + high
        if deep > 2.0 * self.measures.half + _EPS:
            raise self.refuse(
                "rows exceed the designer depth",
                f"{len(built)} rows want {deep:.0f} cm of band and the {self.designer.mark} "
                f"designer is {2 * self.measures.half:.0f} cm deep",
            )
        # What room is left over is shared between the two walls, on the grid, so
        # that the build stands in the middle of the designer rather than against
        # the near wall.
        slack = _grid_floor((2.0 * self.measures.half - deep) / 2.0, self.measures.grid)
        x_edge = _grid_ceil(width / 2.0, self.measures.grid)
        rows: list[_Row] = []
        y = -self.measures.half + slack + low
        for index, geometry in enumerate(built):
            if index:
                y += gaps[index - 1]
            centre = _grid_round((geometry.x_min_cm + geometry.x_max_cm) / 2.0, self.measures.grid)
            parent, share = shares[index]
            rows.append(
                _Row(
                    index=index,
                    groups=share.groups,
                    geometry=_translate(geometry, -centre, y - geometry.y_min_cm),
                    flip=bool(parent % 2),
                    paired_item=share.pair.item if share.pair is not None else "",
                )
            )
            y += geometry.depth_cm
        return rows, x_edge, deep


# --- helpers ---------------------------------------------------------------


def _units(order: Sequence[SfyMachineGroup], pairs: Sequence[DirectPair]) -> list[_Unit]:
    """The production order with every matched pair collapsed into one unit.

    A pair takes the PRODUCER's place in the order and its consumer drops out of
    it, which keeps the order topological: everything the producer eats still
    comes before it, and everything that eats what the consumer makes still comes
    after, because the consumer sat there already.
    """
    consumers = {id(pair.consumer) for pair in pairs}
    by_producer = {id(pair.producer): pair for pair in pairs}
    units: list[_Unit] = []
    for group in order:
        if id(group) in consumers:
            continue
        pair = by_producer.get(id(group))
        if pair is None:
            units.append(_Unit(groups=(group,)))
        else:
            units.append(_Unit(groups=(pair.producer, pair.consumer), pair=pair))
    return units


def _unpaired(units: Sequence[_Unit]) -> list[_Unit]:
    """The same units with every pairing given up, producer first.

    Which is always a build if anything is: two rows and a trunk up a corridor is
    what the manifold does, and it wants no machine to face any other.
    """
    return [_Unit(groups=(group,)) for unit in units for group in unit.groups]


def _split_unit(unit: _Unit, rows: int) -> list[_Unit]:
    """One unit as ``rows`` rows' worth of it.

    A pair is never split -- :meth:`RowPlanner._plan_rows` gives the pairing up
    rather than ask for that -- so only a single group ever divides here, and it
    divides by :func:`_split_group`.
    """
    if unit.pair is not None:
        if rows != 1:
            raise ValueError("a paired unit is one row or it is not a pair")
        return [unit]
    return [_Unit(groups=(share,)) for share in _split_group(unit.groups[0], rows)]


def _machine(registry: Registry, class_name: str) -> Buildable:
    machine = registry.buildables.get(class_name)
    if machine is None:
        raise RowError(f"the registry has no buildable called {class_name!r}")
    return machine


def _measuring_designer(designer: Designer) -> Designer:
    """The frame a row is built in before it is moved to where it stands.

    :func:`~flab2bp.sfy.layout.manifold.build_row` builds a row about its first
    machine and refuses one that leaves the designer IN THAT FRAME, which is not
    where the row will stand: a row this module centres may fit a designer the row
    builder would have turned away.  The loosest frame that still cannot admit a
    row which could never fit anywhere is the designer doubled across the floor --
    a row's band has to fit inside the designer's depth wherever it stands, so
    neither side of its own frame can be longer than the whole designer.  The
    height is untouched, because a row does not move up.
    """
    return Designer(
        mark=designer.mark,
        dims=(2 * designer.dims[0], 2 * designer.dims[1], designer.dims[2]),
        foundation_cm=designer.foundation_cm,
    )


def _split_group(group: SfyMachineGroup, rows: int) -> list[SfyMachineGroup]:
    """One group as ``rows`` rows' worth of it, machines as even as they divide.

    Nothing is re-solved here: FactorioLab costed ``count - 1`` machines at
    ``clock`` and one at ``last_clock`` -- the odd machine at the end of a row
    absorbs the fractional remainder -- and the split hands the underclocked one
    to the LAST row and leaves every other row at the group's own clock
    throughout.  Summed back up, the multiset of clocks is the one the spec
    states, which is what ``spec.machines`` compares; and each row's own
    ``row_inputs`` and ``row_outputs`` are that row's share, which is what its
    chain ends carry and what its belt tiers are chosen against.

    The shares differ by at most one machine, largest first, so a group of seven
    in rows of at most four is four and three rather than four, one and two.

    ``last_clock`` is not the only per-share figure the last machine owns: the
    shards it takes and the megawatts it draws are stated for ITS clock too, so
    a share whose last machine runs at the group's clock must carry the group's
    per-machine figures in all three places.  Left at the group's underclocked
    values, every non-last share would under-order shards and under-report
    power, and the shares' :attr:`~flab2bp.sfy.spec.SfyMachineGroup.row_power_mw`
    would no longer add up to the group's.
    """
    if rows < 1 or rows > group.count:
        raise ValueError(f"{group.recipe_id}: {group.count} machines cannot be laid as {rows} rows")
    base, extra = divmod(group.count, rows)
    counts = [base + (1 if index < extra else 0) for index in range(rows)]
    return [
        group.model_copy(
            update=(
                {"count": count}
                if index == rows - 1
                else {
                    "count": count,
                    "last_clock": group.clock,
                    "last_power_shards": group.power_shards_per_machine,
                    "last_power_mw": group.power_mw_per_machine,
                }
            )
        )
        for index, count in enumerate(counts)
    ]


def _production_order(groups: Sequence[SfyMachineGroup]) -> list[SfyMachineGroup]:
    """Groups in topological order of the item graph, ties by machine count.

    A group that makes what another eats comes first, so every trunk between two
    rows runs the same way up its corridor.  A cycle -- which no FactorioLab flow
    this project has seen produces -- is broken by taking the largest group left,
    so the order is always total and always the same for the same spec.
    """
    makers: dict[str, set[int]] = {}
    for index, group in enumerate(groups):
        for item in group.outputs_per_machine:
            makers.setdefault(item, set()).add(index)
    waiting = {
        index: {
            maker
            for item in group.inputs_per_machine
            for maker in makers.get(item, set())
            if maker != index
        }
        for index, group in enumerate(groups)
    }
    order: list[SfyMachineGroup] = []
    left = set(waiting)
    while left:
        ready = [index for index in sorted(left) if not waiting[index] & left]
        pick = max(ready or sorted(left), key=lambda index: (groups[index].count, -index))
        order.append(groups[pick])
        left.discard(pick)
    return order


def _row_gap(
    before: RowGeometry,
    after: RowGeometry,
    turn: Turn,
    grid: float,
    *,
    siblings: bool = False,
) -> float:
    """How much floor is left between two rows' bands.

    One grid step is the brief's gap and the least the build gun would leave.
    Where a trunk turns out of one row and into the next it wants more: each turn
    spends its own :attr:`~flab2bp.sfy.layout.corridors.Turn.cost` of ``Y``, and
    what the two bands already hold beyond their chain ends counts towards it.
    So the gap is whatever the two turns still want, and never less than a grid
    step.  Ours, and derived: what a turn costs is the corridor's and the
    overhangs are the rows' own.

    ``siblings`` -- two rows of ONE group, which :func:`_split_group` made -- get
    the grid step and nothing more, because no trunk turns between them: they are
    two sinks of the same item and two sources of the same one, and the spine
    reaches into each of them across the corridor without turning.  That matters
    to what fits: splitting pays for width in depth, and three metres of it per
    split is the difference between a build and a refusal.
    """
    if siblings:
        return grid
    over = (before.y_max_cm - before.chain_out.pose.y) + (
        min(end.pose.y for end in after.chain_in) - after.y_min_cm
    )
    return max(grid, float(math.ceil(2.0 * turn.cost - over)))


def _wall_margin(row: RowGeometry, turn: Turn, grid: float, *, entry: bool) -> float:
    """How much floor the wall needs for the belt that crosses it to reach the row.

    Ours, and the same arithmetic as :func:`_row_gap` with the wall standing in
    for the other row: a belt arriving at the wall has to turn into the row's
    chain end, and that turn costs a piece of ``Y`` which the row's own band may
    already cover.

    **Only where a turn actually happens.**  A chain end that already faces the
    wall the belt comes in at is met by a straight belt, and a straight belt
    wants nothing but its own shortest length; charging it for a turn was a
    metre of designer floor at each wall that nothing used.  What a turn costs
    when there IS one is the turn's own, not a constant here.

    An ARC asks one grid step more, and it is geometry rather than taste: a
    belt's clearance box is 79 cm to each side of its centreline and square to
    it, so a box on a turning piece that began ON the wall would have a corner
    outside the designer -- 4.8 cm outside, measured -- and ``geom.bounds`` would
    refuse the build.  A straight step at the wall puts the first box square to
    it.  An attachment turn has no such piece: every belt at it is straight.
    """
    inside = (
        min(end.pose.y for end in row.chain_in) - row.y_min_cm
        if entry
        else row.y_max_cm - row.chain_out.pose.y
    )
    wanted = turn.cost + (0.0 if turn.is_attachment else grid)
    return max(0.0, float(math.ceil(wanted - inside)))


def _translate(row: RowGeometry, dx: float, dy: float) -> RowGeometry:
    """The same row, moved on the floor.

    A row is built about its first machine and the build stands it somewhere
    else.  Everything with a position moves and nothing else changes: the ids are
    the build's own numbering and a link names ids, so the links are untouched.
    """

    def pose(where: Pose) -> Pose:
        return Pose(where.x + dx, where.y + dy, where.z, where.yaw_deg)

    def end(chain: ChainEnd) -> ChainEnd:
        return replace(chain, pose=pose(chain.pose))

    x0, y0, x1, y1 = row.machine_footprint_cm
    return replace(
        row,
        machines=tuple(replace(machine, pose=pose(machine.pose)) for machine in row.machines),
        attachments=tuple(replace(obj, pose=pose(obj.pose)) for obj in row.attachments),
        belts=tuple(
            replace(
                belt,
                points=tuple(
                    ((p[0] + dx, p[1] + dy, p[2]), arrive, leave)
                    for p, arrive, leave in belt.points
                ),
            )
            for belt in row.belts
        ),
        chain_in=tuple(end(chain) for chain in row.chain_in),
        chain_out=end(row.chain_out),
        machine_footprint_cm=(x0 + dx, y0 + dy, x1 + dx, y1 + dy),
        x_min_cm=row.x_min_cm + dx,
        y_min_cm=row.y_min_cm + dy,
        x_max_cm=row.x_max_cm + dx,
        y_max_cm=row.y_max_cm + dy,
    )


def _grid_ceil(value: float, grid: float) -> float:
    return math.ceil(value / grid - 1e-9) * grid


def _grid_floor(value: float, grid: float) -> float:
    return math.floor(value / grid + 1e-9) * grid


def _grid_round(value: float, grid: float) -> float:
    return round(value / grid) * grid
