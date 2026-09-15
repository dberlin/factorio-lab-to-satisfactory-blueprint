"""Every trunk that runs up a corridor: its ends, its nodes, and its column.

A *net* is one item's flow up one of the two corridors: the rows that make it and
the wall it arrives at are its sources, the rows that eat it and the wall it
leaves by are its sinks, and between them stands one belt up one column with a
merger for every source it joins on the way and a splitter for every sink it
drops off at.  This module works all of that out before a single belt is drawn --
which item runs up which side, where each node stands, and which column each
trunk is given -- so that :mod:`~flab2bp.sfy.layout.laying` has nothing left to
decide but the shape of the belt.

What is FactorioLab's and what is ours
--------------------------------------
Which rows make and eat what is the spec's, which is FactorioLab's own solved
flow; nothing here re-solves a rate.  The one rate this module decides is
:func:`_share`, and only where an item crosses the boundary on both sides of the
build at once, which no fixture flow does.

Nothing here decides what the game accepts either.  A trunk that cannot be stood
raises :class:`~flab2bp.sfy.layout.corridors.CorridorError`, and a build whose
rows cannot be fed refuses through the one refusal builder
:mod:`flab2bp.sfy.layout.strategy` hands it.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.geometry import port_forward
from flab2bp.sfy.layout.corridors import (
    COLUMN_LANE_CM,
    Assignment,
    ColumnRequest,
    CorridorError,
    Measures,
    assign_columns,
    choose_turn,
)
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS, ChainEnd
from flab2bp.sfy.layout.model import Pose, Vector
from flab2bp.sfy.layout.rows import _Row
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.spec import SfyBuildSpec

__all__ = ["CorridorPlan", "NetPlanner"]

_EPS = 1e-6
"""How close to a bound counts as inside it, in centimetres.

The same tolerance :mod:`~flab2bp.sfy.layout.rows` and
:mod:`~flab2bp.sfy.layout.laying` compare with.
"""


@dataclass(frozen=True, slots=True)
class _Terminal:
    """One end of a trunk: a row's chain end, or the designer wall.

    ``side`` is which corridor it belongs to, ``-1`` for the ``-X`` one.  A wall
    terminal has no point: where it stands across ``X`` depends on the column it
    is given, which is not decided until every interval is in.
    """

    item: str
    rate: Fraction
    side: int
    y: float
    wall: bool
    point: Vector = (0.0, 0.0, 0.0)
    link: tuple[int, str] | None = None


@dataclass(frozen=True, slots=True)
class _Node:
    """A merger taking one more source in, or a splitter sending one sink off."""

    y: float
    merging: bool
    branch: _Terminal


@dataclass
class _Net:
    """One item's flow up one corridor: its sources, its sinks, and its nodes."""

    item: str
    side: int
    sources: list[_Terminal]
    sinks: list[_Terminal]
    nodes: list[_Node] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.item}{'+' if self.side > 0 else '-'}"


@dataclass(frozen=True, slots=True)
class CorridorPlan:
    """Every trunk in the two corridors, and the column each one was given."""

    nets: tuple[_Net, ...]
    #: Each net's assignment, by :attr:`_Net.label`.
    spine: dict[str, Assignment]
    #: How far out of the rows the innermost column of either corridor stands.
    offset: float
    #: How far out of the rows the corridor actually reaches, in centimetres:
    #: the offset plus the outermost column any trunk took.  It is measured here
    #: rather than by the caller because only this module knows which columns were
    #: handed out, and :meth:`~flab2bp.sfy.layout.rows.RowPlanner._plan_rows`
    #: holds it against the designer to decide whether to split again.
    reach_cm: float


@dataclass
class NetPlanner:
    """Which item runs up which corridor, where its nodes stand, in which column."""

    spec: SfyBuildSpec
    registry: Registry
    budget: WorkBudget
    measures: Measures
    #: The one place a refusal is built, handed down by
    #: :mod:`flab2bp.sfy.layout.strategy` so that no module below it can invent a
    #: cause the strategy has not named.
    refuse: Callable[[str, str], NoValidLayout]

    def _plan_columns(self, rows: Sequence[_Row], x_edge: float) -> CorridorPlan:
        """Every trunk's column, worked out before a single belt is drawn.

        The columns are assigned against a corridor as wide as it could possibly
        need to be -- one column per trunk -- rather than against the designer,
        because how far the corridor reaches is one of the things
        :meth:`~flab2bp.sfy.layout.rows.RowPlanner._plan_rows` is still deciding.
        What the corridor really took is :func:`_outermost_column`, and holding
        that against the designer is ``_plan_rows``'s last step.
        """
        nets = self._nets(rows)
        offset = self._column_offset(rows, x_edge, nets)
        # The two corridors are two sets of columns and share nothing: a belt on
        # one side of the rows cannot be in the way of a belt on the other, so
        # each side is assigned on its own.
        requests: dict[int, list[ColumnRequest]] = {-1: [], 1: []}
        for net in nets:
            self._plan_nodes(net)
            requests[net.side].append(
                ColumnRequest(
                    belt=net.label,
                    y_a=_spine_start(net),
                    y_b=_spine_end(net),
                    taps=tuple(node.y for node in net.nodes),
                )
            )
        spine = {
            assignment.belt: assignment
            for side in (-1, 1)
            for assignment in assign_columns(
                requests[side],
                columns=max(1, len(requests[side])),
                margin=BELT_CLEARANCE_HALF_WIDTH_CM,
                budget=self.budget,
            )
        }
        return CorridorPlan(
            nets=tuple(nets),
            spine=spine,
            offset=offset,
            reach_cm=offset + _outermost_column(spine) * self.measures.pitch,
        )

    def _column_offset(self, rows: Sequence[_Row], x_edge: float, nets: Sequence[_Net]) -> float:
        """How far out of the rows the innermost corridor column stands.

        Far enough that a belt turning into a row has the turn's own cost and a
        legal straight to land on, and never closer than a belt's own half width
        plus a centimetre, so that the column's lane is clear of the rows' band
        rather than sharing a face with it -- which ``belt.capsule`` calls a lap.

        **A corridor with one trunk in it gets the close floor and a corridor
        with two gets a column pitch.**  A crossing is what needs the room: a
        transverse that has to ride over an occupied column climbs a crossing gap
        before it and comes down after it, and both slopes have to happen between
        the row and the column.  Where a side carries ONE trunk nothing is ever
        crossed, so that room is a metre and a half of designer floor per side
        that nothing can stand in -- and a Mk1 is 3200 cm across where a
        three-machine Constructor row is 2600, which is exactly the margin a
        build lives or dies by.
        """
        inset = min(
            (
                max(0.0, x_edge - abs(end.pose.x))
                for row in rows
                for end in (*row.geometry.chain_in, row.geometry.chain_out)
            ),
            default=0.0,
        )
        crowded = Counter(net.side for net in nets)
        lane = (
            self.measures.pitch if any(count > 1 for count in crowded.values()) else COLUMN_LANE_CM
        )
        # What the turn into a row needs OUT OF THE TRANSVERSE is where it takes
        # hold plus the shortest belt from there into the chain end -- an arc's
        # whole radius, an attachment's one port offset -- and what the row's own
        # band already holds beyond its chain ends pays for part of it.
        turn = choose_turn(self.measures, math.inf, math.inf)
        return max(lane, float(math.ceil(max(0.0, turn.reach + self.measures.lead_in - inset))))

    def _nets(self, rows: Sequence[_Row]) -> list[_Net]:
        """Every item's sources and sinks, split by the corridor they face."""
        sources: dict[tuple[str, int], list[_Terminal]] = {}
        sinks: dict[tuple[str, int], list[_Terminal]] = {}
        for row in rows:
            out = self._row_terminal(row.geometry.chain_out, MERGER_CLASS)
            sources.setdefault((out.item, out.side), []).append(out)
            for chain in row.geometry.chain_in:
                sink = self._row_terminal(chain, SPLITTER_CLASS)
                sinks.setdefault((sink.item, sink.side), []).append(sink)
        nets: list[_Net] = []
        for item in sorted({key[0] for key in (*sources, *sinks)}):
            nets.extend(self._nets_for(item, sources, sinks))
        return nets

    def _nets_for(
        self,
        item: str,
        sources: dict[tuple[str, int], list[_Terminal]],
        sinks: dict[tuple[str, int], list[_Terminal]],
    ) -> list[_Net]:
        """One item's nets, one per corridor it is wanted in.

        A corridor with demand and nothing on its own side to meet it is fed from
        the ``-Y`` wall, which is what the spec's ``external_inputs`` are; one
        with supply and nothing to eat it sends the rest out at the ``+Y`` wall,
        which is what ``outputs`` and ``surplus_outputs`` are.  Where the spec
        belts an item in that a row also makes, the wall belt is one more source
        and the mergers put the two together.
        """
        arriving = self.spec.external_inputs.get(item, Fraction(0))
        leaving = self.spec.outputs.get(item, Fraction(0)) + self.spec.surplus_outputs.get(
            item, Fraction(0)
        )
        sides = sorted({side for name, side in (*sources, *sinks) if name == item})
        mine = {side: list(sources.get((item, side), [])) for side in sides}
        theirs = {side: list(sinks.get((item, side), [])) for side in sides}
        hungry = [side for side in sides if theirs[side] and not mine[side]]
        if hungry and arriving <= 0:
            if not any(mine.values()):
                # Not a refusal: SfyBuildSpec's own validator turns a spec with an
                # unsupplied input away at construction, so a spec that reaches
                # here with one is this package disagreeing with itself.
                raise ValueError(
                    f"no row makes {item!r} and the spec belts none of it in, which "
                    "SfyBuildSpec._no_dangling_demand refuses at construction; a spec that "
                    "reaches the layout stage with it is a bug in this package"
                )
            raise self.refuse(
                "a row is fed from the corridor on the other side of the build",
                f"{item!r} is wanted in the {'+X' if hungry[0] > 0 else '-X'} corridor, is made "
                "only on the other side of the rows, and the spec belts none of it in",
            )
        if arriving > 0:
            for side, rate in _share(arriving, hungry or sides[:1], theirs).items():
                mine[side].append(self._wall_terminal(item, rate, side, entry=True))
        spare = [side for side in sides if mine[side] and not theirs[side]]
        if spare and leaving <= 0:
            raise self.refuse(
                "a row makes something the spec never sends out",
                f"{item!r} is made by a row, eaten by nothing and is neither an output nor a "
                "declared surplus",
            )
        if leaving > 0:
            for side, rate in _share(leaving, spare or sides[:1], mine).items():
                theirs[side].append(self._wall_terminal(item, rate, side, entry=False))
        return [
            _Net(item=item, side=side, sources=mine[side], sinks=theirs[side])
            for side in sides
            if mine[side] and theirs[side]
        ]

    def _wall_terminal(self, item: str, rate: Fraction, side: int, *, entry: bool) -> _Terminal:
        return _Terminal(
            item=item,
            rate=rate,
            side=side,
            y=-self.measures.half if entry else self.measures.half,
            wall=True,
        )

    def _row_terminal(self, end: ChainEnd, class_name: str) -> _Terminal:
        """One chain end as a trunk terminal, with the corridor it faces.

        Which corridor is the port's own facing: a merger's ``Output1`` looks out
        of the ``+X`` end of an unflipped row and out of the ``-X`` end of a
        flipped one, and the belt wired to it has nowhere else to go.
        """
        port = _port(self.registry.buildables[class_name], end.port)
        facing = port_forward(Pose(0.0, 0.0, 0.0, end.pose.yaw_deg).transform(), port)
        return _Terminal(
            item=end.item_id,
            rate=end.items_per_second,
            side=1 if facing[0] > 0 else -1,
            y=end.pose.y,
            wall=False,
            point=end.pose.location,
            link=(end.object_id, end.port),
        )

    def _plan_nodes(self, net: _Net) -> None:
        """Lay the nodes out along the column, in the order the spine meets them.

        The spine runs one way, from the lowest source to the highest sink, and
        that is not a choice: production order puts every producing row below the
        row that eats what it makes, the wall the build takes in at is below all
        of them, and the wall it sends out at is above.  So the first source and
        the last sink are the spine's own two ends, and every other terminal is a
        row that has to be joined on the way past.

        Each of those gets a node standing at ITS OWN ``Y``, so that the belt
        between the node and the row is one straight transverse across the
        corridor -- no turn, and so no second column to find.  What that costs is
        a bound: two nodes have to stand at least an attachment pitch apart and
        clear of the spine's own turns, which two rows always are and which is
        checked here rather than discovered as a belt of minus a metre.

        **The nodes are sorted by ``Y``, not by kind.**  A source can stand
        between two sinks -- a row that makes some of an item the row below it
        also makes, feeding a row above them both -- and then the spine merges it
        in on the way past, after it has already split some off.  Which is what
        the sort states: the spine meets what it meets in the order it gets there,
        and :func:`_carried` reads the rate the same way.

        What the spine CANNOT do is meet something outside its own span.  A source
        above the last sink would have to run back down the corridor against
        everything else in it, and one monotone column cannot carry two
        directions; that is a named refusal rather than a merger standing past the
        end of the belt it merges into.
        """
        net.sources.sort(key=lambda terminal: terminal.y)
        net.sinks.sort(key=lambda terminal: terminal.y)
        net.nodes = sorted(
            [_Node(y=source.y, merging=True, branch=source) for source in net.sources[1:]]
            + [_Node(y=sink.y, merging=False, branch=sink) for sink in net.sinks[:-1]],
            key=lambda node: (node.y, node.merging),
        )
        if not net.nodes:
            return
        span = (_spine_start(net), _spine_end(net))
        stray = [node for node in net.nodes if not span[0] - _EPS <= node.y <= span[1] + _EPS]
        if stray:
            raise CorridorError(
                "backwards",
                f"the {net.item!r} trunk runs from y = {span[0]:.0f} to {span[1]:.0f} and a "
                f"{'source' if stray[0].merging else 'sink'} of it stands at "
                f"{stray[0].y:.0f}, outside that span",
            )
        clear = (
            _spine_start(net)
            + (0.0 if net.sources[0].wall else self.measures.radius)
            + self.measures.lead
        )
        for node in net.nodes:
            if node.y + _EPS < clear:
                raise CorridorError(
                    "depth",
                    f"the {net.item!r} trunk's {'merger' if node.merging else 'splitter'} stands "
                    f"at y = {node.y:.0f} and the corridor is not clear until {clear:.0f}",
                )
            clear = node.y + self.measures.node_pitch
        last = (
            _spine_end(net)
            - (0.0 if net.sinks[-1].wall else self.measures.radius)
            - self.measures.lead
        )
        if net.nodes[-1].y > last + _EPS:
            raise CorridorError(
                "depth",
                f"the {net.item!r} trunk's last node stands at y = {net.nodes[-1].y:.0f}, "
                f"{net.nodes[-1].y - last:.0f} cm past where the corridor has to turn out",
            )


# --- helpers ---------------------------------------------------------------


def _outermost_column(spine: dict[str, Assignment]) -> int:
    """How far out the corridors actually reach, as a column index.

    The MAXIMUM index, not a count: columns are handed out from the wall
    outwards and a trunk may skip one, so this says how far the corridor
    reaches rather than how many trunks are in it.
    """
    return max((assignment.column for assignment in spine.values()), default=0)


def _spine_start(net: _Net) -> float:
    return min(terminal.y for terminal in net.sources)


def _spine_end(net: _Net) -> float:
    return max(terminal.y for terminal in net.sinks)


def _share(
    total: Fraction, sides: Sequence[int], demand: dict[int, list[_Terminal]]
) -> dict[int, Fraction]:
    """``total`` split between corridors in proportion to what each one wants.

    **Ours**, and the only rate this module decides: FactorioLab states what
    crosses the boundary and says nothing about which side of a build it crosses
    on, because it knows nothing about sides.  One corridor takes the lot, which
    is every build the fixtures produce.  Where an item is wanted on both sides
    the split is by the rate each side asks for, in exact ``Fraction`` arithmetic,
    so that the two wall belts add up to the rate the spec states and neither is
    sized for the whole build.
    """
    if len(sides) == 1:
        return {sides[0]: total}
    wants = {side: sum((t.rate for t in demand[side]), Fraction(0)) for side in sides}
    whole = sum(wants.values(), Fraction(0))
    if whole <= 0:
        return {sides[0]: total}
    return {side: total * wants[side] / whole for side in sides}


def _carried(net: _Net, after: int) -> Fraction:
    """What the spine carries once it has passed ``after`` nodes."""
    merged = sum(
        (node.branch.rate for node in net.nodes[:after] if node.merging), net.sources[0].rate
    )
    split = sum((node.branch.rate for node in net.nodes[:after] if not node.merging), Fraction(0))
    return merged - split


def _port(buildable: Buildable, name: str) -> Port:
    return next(port for port in buildable.ports if port.name == name)
