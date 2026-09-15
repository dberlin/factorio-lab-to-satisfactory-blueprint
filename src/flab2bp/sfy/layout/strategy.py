"""``ManifoldRows``: a whole Satisfactory build in one Blueprint Designer.

The shape is one a Satisfactory player would recognise.  Every machine group is
a *row* -- :func:`~flab2bp.sfy.layout.manifold.build_row`'s line of machines, fed
by splitter chains and drained by a merger chain -- and the rows are stacked
along ``Y`` in production order, each one turned about so that its input end
faces the corridor the row before it output into.  Down each side runs a
*corridor* of belt columns carrying every trunk: what the build takes in at the
``-Y`` wall, what one row hands to the next, and what leaves at the ``+Y`` wall.

Where the numbers come from
---------------------------
Every distance is read.  The rows' geometry is the row builder's, which reads the
registry's clearance boxes and ports; the corridor's is
:mod:`flab2bp.sfy.layout.corridors`, which reads ``belt.clearance``,
``belt_bend_radius_cm``, ``belt_min_length_cm``, ``belt_max_incline_deg`` and
``belt_max_spline_cm``; the floor is the shipped foundation's own footprint and
the designer's own cell count.  The choices that are this project's own say so
where they are made: how much floor is left at each wall for a belt to turn into
a row (:func:`_wall_margin`), how much between two rows for a trunk to turn out
of one and into the next (:func:`_row_gap`), that the rows are centred rather
than pushed to one side, and the frame a row is measured in before it is moved
(:func:`_measuring_designer`).

Legality is the validator's
---------------------------
Nothing here decides what the game accepts.  Every placement this module returns
is meant to come back clean from ``validate(placement, spec, registry)``, and
where it cannot lay one it refuses with
:class:`~flab2bp.layout.base.NoValidLayout` and a cause out of :data:`REFUSALS`.
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from functools import cache
from typing import Any

from flab2bp.lab.data import load_vendored
from flab2bp.lab.url import Game
from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import BudgetExhausted, WorkBudget, WorkLimits
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.labmap import LabMap, load_lab_map, machine_class
from flab2bp.sfy.layout.corridors import (
    BRIDGE_CLEARANCE_BOXES,
    Assignment,
    ColumnRequest,
    CorridorError,
    Route,
    assign_columns,
    attachment_pitch_cm,
    belt_pitch_cm,
    bridge_z_cm,
    descent_run_cm,
    lay_path,
    turn_radius_cm,
)
from flab2bp.sfy.layout.manifold import (
    MERGER_CLASS,
    SPLITTER_CLASS,
    ChainEnd,
    RowError,
    RowGeometry,
    build_row,
    grid_ceil,
)
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    Link,
    MachineObj,
    Pose,
    SfyPlacement,
    Vector,
)
from flab2bp.sfy.layout.power import PowerError, PowerPlan, PowerRow
from flab2bp.sfy.layout.power import place as place_power
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import Buildable, Port, Registry, load_registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, SfyBuildSpec, SfyMachineGroup
from flab2bp.spec import BeltTier

__all__ = ["REFUSALS", "ManifoldRows"]

GAME_DATA = "the game data does not describe a machine this build needs"
"""One cause for every way the extraction leaves a hole a build falls into.

A missing buildable, a machine with no hard clearance box or no belt output port,
a belt tier the lab map has no class for: all of them are the same answer to the
caller -- this build cannot be authored from the game data we have -- and all of
them carry the detail that says which.
"""

GAME_LIMITS = "the game data states no limit this build needs"
"""The same, for a ``limits`` field the registry does not carry: the hologram
grid, the minimum belt length, the maximum incline, the bend radius, the maximum
spline.  Separate from :data:`GAME_DATA` because a missing bound is a different
gap from a missing machine, and a reader chasing one is not chasing the other."""

REFUSALS = (
    # The brief's own list.
    "rows exceed the designer depth",
    "rows exceed the designer width",
    "corridor needs a bridge that does not fit",
    "row too deep",
    "run exceeds the belt ceiling",
    "fluids are M4",
    "corridor assignment exceeded the budget",
    # The row builder's other causes, each kept as itself rather than folded into
    # a designer bound it has nothing to do with.
    "row too tall",
    "more input items than the machine has belt ports",
    "a row drains one of several products",
    "a feeder crosses the chain inside it",
    "this spec names no belt",
    GAME_DATA,
    GAME_LIMITS,
    # The corridor's own, beyond the three the brief names.
    "a trunk would have to run back down the corridor",
    "a corridor path has no length",
    "layout exceeded the budget",
    # Two more this shape of build can hit that the brief does not name.
    "a row makes something the spec never sends out",
    "a row is fed from the corridor on the other side of the build",
    # Task 9's three: a pole line that cannot be reached, cannot be stood, or a
    # class with no power connection to wire at all.
    "wire exceeds the maximum length",
    "no room for a power pole",
    "a machine has no power connection",
)
"""Every reason this strategy refuses with, and the only ones it may use."""

#: The row builder's own causes, as they arrive on a ``RowError``, mapped onto the
#: names a caller of this module sees, by the words each message is built from.
#: A row too WIDE for the doubled frame it is measured in is the build being too
#: big for the designer, which is what the caller is told; every other cause is
#: its own, because a caller told "row too deep" about a machine the registry does
#: not carry would go and look at the wrong thing.  Ordered: the first needle a
#: message holds wins, and :func:`_row_cause` refuses to guess at one it has not
#: got.
_ROW_CAUSES: tuple[tuple[str, str], ...] = (
    ("row too deep", "row too deep"),
    ("row too wide", "rows exceed the designer width"),
    ("row too tall", "row too tall"),
    ("run exceeds the belt ceiling", "run exceeds the belt ceiling"),
    ("more input items than", "more input items than the machine has belt ports"),
    ("drains one output item", "a row drains one of several products"),
    ("crosses the chain", "a feeder crosses the chain inside it"),
    ("this spec names no belt", "this spec names no belt"),
    ("the registry states no", GAME_LIMITS),
    ("the registry has no buildable", GAME_DATA),
    ("has no hard clearance box", GAME_DATA),
    ("has no clearance box", GAME_DATA),
    ("has no belt output port", GAME_DATA),
    ("the lab map has no conveyor class", GAME_DATA),
)

#: What a :class:`~flab2bp.sfy.layout.corridors.CorridorError`'s cause is called
#: where a caller can see it.
_CORRIDOR_CAUSES = {
    "width": "rows exceed the designer width",
    "bridge": "corridor needs a bridge that does not fit",
    "depth": "rows exceed the designer depth",
    "backwards": "a trunk would have to run back down the corridor",
    "path": "a corridor path has no length",
    "limits": GAME_LIMITS,
}

#: What a :class:`~flab2bp.sfy.layout.power.PowerError`'s cause is called where a
#: caller can see it.
_POWER_CAUSES = {
    "wire": "wire exceeds the maximum length",
    "room": "no room for a power pole",
    "port": "a machine has no power connection",
}

COLUMN_TRIES = 100_000
"""Column tries the search may make before it gives up.

Ours, and a backstop rather than a bound: a corridor of a dozen columns and a
hundred trunks is two orders below it, so reaching it means the search is not
converging rather than that the build is large.
"""

_EPS = 1e-6


class ManifoldRows:
    """Rows along ``Y``, corridors down each side, one blueprint."""

    name = "manifold-rows"

    def lay_out(
        self,
        spec: SfyBuildSpec,
        designer: Designer,
        *,
        time_budget_s: float = 15.0,
        absolute_deadline: float | None = None,
        registry: Registry | None = None,
        lab_map: LabMap | None = None,
    ) -> SfyPlacement:
        """Lay ``spec`` out inside ``designer``, or refuse with a named cause."""
        deadline = (
            absolute_deadline if absolute_deadline is not None else time.monotonic() + time_budget_s
        )
        layout = _Layout(
            spec=spec,
            designer=designer,
            registry=load_registry() if registry is None else registry,
            lab_map=load_lab_map() if lab_map is None else lab_map,
            budget=WorkBudget(deadline=deadline, limits=WorkLimits(assignments=COLUMN_TRIES)),
        )
        try:
            return layout.build()
        except BudgetExhausted as exc:
            # The clock and the column allowance are two different bounds and a
            # caller does something different about each: more time, or a build
            # that is not asking for thousands of columns.  ``TransportRefusal``
            # says which ran out.
            spent = getattr(exc, "reason", "")
            raise _refuse(
                spec,
                "corridor assignment exceeded the budget"
                if spent == "POLICY_BOUND"
                else "layout exceeded the budget",
                str(exc),
            ) from exc
        except CorridorError as exc:
            raise _refuse(spec, _CORRIDOR_CAUSES[exc.cause], exc.detail) from exc
        except RowError as exc:
            raise _refuse(spec, _row_cause(exc), str(exc)) from exc
        except PowerError as exc:
            raise _refuse(spec, _POWER_CAUSES[exc.cause], exc.detail) from exc


def _refuse(spec: SfyBuildSpec, reason: str, detail: str = "") -> NoValidLayout:
    """The one place a refusal is built, so every cause is one of the named ones."""
    if reason not in REFUSALS:
        raise ValueError(f"{reason!r} is not one of this strategy's named refusals")
    return NoValidLayout(
        reason,
        spec_label=spec.label or "this build",
        attempt_reasons=(detail,) if detail else (),
    )


def _row_cause(error: RowError) -> str:
    """Which named refusal one of the row builder's own errors is.

    Raises rather than guessing.  A default here would tell a caller "row too
    deep" about a machine class the registry does not carry, and a wrong cause is
    worse than a stack trace: it sends the reader to the wrong file.  Every
    message :mod:`flab2bp.sfy.layout.manifold` raises is in :data:`_ROW_CAUSES`,
    and a new one has to be put there deliberately.
    """
    message = str(error)
    for needle, cause in _ROW_CAUSES:
        if needle in message:
            return cause
    raise ValueError(
        f"this module has no named refusal for the row builder's {message!r}; add one to "
        "_ROW_CAUSES and to REFUSALS rather than letting it wear another cause's name"
    )


# --- what stands where -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Row:
    """One group's row, where it finally stands."""

    index: int
    group: SfyMachineGroup
    geometry: RowGeometry
    flip: bool


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


_Anchor = tuple[Vector, Vector, tuple[int, str] | None]
"""Where a path starts or ends: the point, the way it is going, the port to wire."""


# --- the layout ------------------------------------------------------------


@dataclass
class _Layout:
    """One run of the strategy: every decision, in the order it is made."""

    spec: SfyBuildSpec
    designer: Designer
    registry: Registry
    lab_map: LabMap
    budget: WorkBudget
    ids: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    rows: list[_Row] = field(default_factory=list)
    nets: list[_Net] = field(default_factory=list)
    spine: dict[str, Assignment] = field(default_factory=dict)
    grid: float = 0.0
    radius: float = 0.0
    pitch: float = 0.0
    node_pitch: float = 0.0
    lead: float = 0.0
    lead_in: float = 0.0
    half: float = 0.0
    belt_z: float = 0.0
    x_edge: float = 0.0
    offset: float = 0.0
    columns: int = 0

    def build(self) -> SfyPlacement:
        self._refuse_fluids()
        self._measure()
        self.rows = self._lay_rows()
        self.belt_z = max(row.geometry.belt_z_cm for row in self.rows)
        self._plan_columns()
        return self._placement(self._lay_corridors())

    def _measure(self) -> None:
        """Every number the rest of this build is laid out against, read once.

        Apart from the designer's own half width these are all the corridor's,
        and all of them come out of ``registry.json``; nothing below this line
        reads a limit again.
        """
        self.grid = _grid(self.registry)
        self.radius = turn_radius_cm(self.registry)
        self.pitch = belt_pitch_cm(self.registry)
        self.node_pitch = attachment_pitch_cm(self.registry)
        self.lead = _lead_cm(self.registry, self.grid)
        self.lead_in = _lead_in_cm(self.registry, self.grid)
        self.half = self.designer.half_cm

    # --- rows ---------------------------------------------------------------

    def _lay_rows(self) -> list[_Row]:
        """Build every row and stand it where it belongs on the floor."""
        order = _production_order(self.spec.groups)
        measuring = _measuring_designer(self.designer)
        built: list[RowGeometry] = []
        for index, group in enumerate(order):
            # The clock bounds the whole of lay_out, not only the column search:
            # a row is the largest piece of work this module does in one step.
            self.budget.check()
            built.append(
                build_row(
                    group,
                    self.registry,
                    designer=measuring,
                    belt_tiers=self.spec.belt_tiers,
                    flip=bool(index % 2),
                    next_id=self.ids,
                    lab_map=self.lab_map,
                )
            )
        width = max(row.width_cm for row in built)
        if width > 2.0 * self.half + _EPS:
            raise _refuse(
                self.spec,
                "rows exceed the designer width",
                f"the widest row is {width:.0f} cm and the {self.designer.mark} designer is "
                f"{2 * self.half:.0f} cm across",
            )
        gaps = [
            _row_gap(before, after, self.radius, self.grid)
            for before, after in itertools.pairwise(built)
        ]
        low = _wall_margin(built[0], self.radius, self.grid, entry=True)
        high = _wall_margin(built[-1], self.radius, self.grid, entry=False)
        deep = low + sum(row.depth_cm for row in built) + sum(gaps) + high
        if deep > 2.0 * self.half + _EPS:
            raise _refuse(
                self.spec,
                "rows exceed the designer depth",
                f"{len(built)} rows want {deep:.0f} cm of band and the {self.designer.mark} "
                f"designer is {2 * self.half:.0f} cm deep",
            )
        # What room is left over is shared between the two walls, on the grid, so
        # that the build stands in the middle of the designer rather than against
        # the near wall.
        slack = _grid_floor((2.0 * self.half - deep) / 2.0, self.grid)
        self.x_edge = _grid_ceil(width / 2.0, self.grid)
        rows: list[_Row] = []
        y = -self.half + slack + low
        for index, geometry in enumerate(built):
            if index:
                y += gaps[index - 1]
            centre = _grid_round((geometry.x_min_cm + geometry.x_max_cm) / 2.0, self.grid)
            rows.append(
                _Row(
                    index=index,
                    group=order[index],
                    geometry=_translate(geometry, -centre, y - geometry.y_min_cm),
                    flip=bool(index % 2),
                )
            )
            y += geometry.depth_cm
        return rows

    # --- nets and columns ---------------------------------------------------

    def _plan_columns(self) -> None:
        """Every trunk's column, worked out before a single belt is drawn."""
        self.nets = self._nets()
        self.columns = self._column_count()
        # The two corridors are two sets of columns and share nothing: a belt on
        # one side of the rows cannot be in the way of a belt on the other, so
        # each side is assigned on its own.
        requests: dict[int, list[ColumnRequest]] = {-1: [], 1: []}
        for net in self.nets:
            self._plan_nodes(net)
            requests[net.side].append(
                ColumnRequest(
                    belt=net.label,
                    y_a=_spine_start(net),
                    y_b=_spine_end(net),
                    taps=tuple(node.y for node in net.nodes),
                )
            )
        self.spine = {
            assignment.belt: assignment
            for side in (-1, 1)
            for assignment in assign_columns(
                requests[side],
                columns=self.columns,
                margin=BELT_CLEARANCE_HALF_WIDTH_CM,
                budget=self.budget,
            )
        }

    def _column_count(self) -> int:
        """How many columns fit between the rows and the designer wall.

        The innermost column stands far enough out that a belt turning into a row
        has the turn's own radius and a legal straight to land on, and at least
        one column pitch out, so that its lane clears the rows' own band.
        """
        inset = min(
            (
                max(0.0, self.x_edge - abs(end.pose.x))
                for row in self.rows
                for end in (*row.geometry.chain_in, row.geometry.chain_out)
            ),
            default=0.0,
        )
        floor = self.registry.limits.belt_min_length_cm or 0.0
        self.offset = max(self.pitch, grid_ceil(max(0.0, self.radius + floor - inset), self.grid))
        room = self.half - BELT_CLEARANCE_HALF_WIDTH_CM - self.x_edge - self.offset
        if room < -_EPS:
            raise _refuse(
                self.spec,
                "rows exceed the designer width",
                f"the rows reach x = {self.x_edge:.0f} and the innermost corridor column "
                f"would stand at {self.x_edge + self.offset:.0f}, outside the "
                f"{self.designer.mark} designer",
            )
        return 1 + int(math.floor(room / self.pitch + _EPS))

    def _column_x(self, side: int, column: int) -> float:
        return side * (self.x_edge + self.offset + column * self.pitch)

    def _nets(self) -> list[_Net]:
        """Every item's sources and sinks, split by the corridor they face."""
        sources: dict[tuple[str, int], list[_Terminal]] = {}
        sinks: dict[tuple[str, int], list[_Terminal]] = {}
        for row in self.rows:
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
            raise _refuse(
                self.spec,
                "a row is fed from the corridor on the other side of the build",
                f"{item!r} is wanted in the {'+X' if hungry[0] > 0 else '-X'} corridor, is made "
                "only on the other side of the rows, and the spec belts none of it in",
            )
        if arriving > 0:
            for side, rate in _share(arriving, hungry or sides[:1], theirs).items():
                mine[side].append(self._wall_terminal(item, rate, side, entry=True))
        spare = [side for side in sides if mine[side] and not theirs[side]]
        if spare and leaving <= 0:
            raise _refuse(
                self.spec,
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
            item=item, rate=rate, side=side, y=-self.half if entry else self.half, wall=True
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

    # --- where the mergers and splitters stand ------------------------------

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
        clear = _spine_start(net) + (0.0 if net.sources[0].wall else self.radius) + self.lead
        for node in net.nodes:
            if node.y + _EPS < clear:
                raise CorridorError(
                    "depth",
                    f"the {net.item!r} trunk's {'merger' if node.merging else 'splitter'} stands "
                    f"at y = {node.y:.0f} and the corridor is not clear until {clear:.0f}",
                )
            clear = node.y + self.node_pitch
        last = _spine_end(net) - (0.0 if net.sinks[-1].wall else self.radius) - self.lead
        if net.nodes[-1].y > last + _EPS:
            raise CorridorError(
                "depth",
                f"the {net.item!r} trunk's last node stands at y = {net.nodes[-1].y:.0f}, "
                f"{net.nodes[-1].y - last:.0f} cm past where the corridor has to turn out",
            )

    # --- drawing it ---------------------------------------------------------

    def _lay_corridors(self) -> tuple[list[AttachmentObj], list[BeltRun], list[Link]]:
        attachments: list[AttachmentObj] = []
        belts: list[BeltRun] = []
        links: list[Link] = []
        for net in self.nets:
            self.budget.check()  # the clock bounds the drawing as well as the search
            x = self._column_x(net.side, self.spine[net.label].column)
            stood = [self._stand(node, x) for node in net.nodes]
            attachments.extend(obj for obj, _ in stood)
            self._lay_spine(net, x, stood, belts, links)
            for index, (obj, ports) in enumerate(stood):
                self._lay_tap(net, index, obj, ports, belts, links)
        return attachments, belts, links

    def _stand(self, node: _Node, x: float) -> tuple[AttachmentObj, tuple[Port, Port, Port]]:
        """One corridor attachment, turned so that it flows up the column.

        Yaw 90 puts the through ports on ``+-Y`` -- the way a corridor runs -- and
        the two side ports on ``+-X``; which of those faces out of the corridor is
        read off its facing rather than named.
        """
        class_name = MERGER_CLASS if node.merging else SPLITTER_CLASS
        buildable = self.registry.buildables[class_name]
        pose = Pose(x, node.y, self.belt_z, 90.0)
        return (
            AttachmentObj(id=next(self.ids), class_name=class_name, pose=pose),
            _corridor_ports(buildable, pose, inward=-1.0 if x > 0 else 1.0),
        )

    def _lay_spine(
        self,
        net: _Net,
        x: float,
        stood: Sequence[tuple[AttachmentObj, tuple[Port, Port, Port]]],
        belts: list[BeltRun],
        links: list[Link],
    ) -> None:
        """The column itself: the first source up to the last sink, node by node."""
        assignment = self.spine[net.label]
        start = self._leave(net.sources[0], x, net.side)
        carried = net.sources[0].rate
        for index, (obj, ports) in enumerate(stood):
            through_in, through_out, _ = ports
            transform = obj.pose.transform()
            self._lay(
                net,
                start,
                (world_port(transform, through_in), _UP, (obj.id, through_in.name)),
                carried,
                belts,
                links,
                assignment,
            )
            carried = _carried(net, index + 1)
            start = (world_port(transform, through_out), _UP, (obj.id, through_out.name))
        self._lay(
            net, start, self._arrive(net.sinks[-1], x, net.side), carried, belts, links, assignment
        )

    def _lay_tap(
        self,
        net: _Net,
        index: int,
        obj: AttachmentObj,
        ports: tuple[Port, Port, Port],
        belts: list[BeltRun],
        links: list[Link],
    ) -> None:
        """The straight belt between a node and the row it joins the spine for.

        The node stands at the row's own ``Y``, so this is one transverse across
        the corridor and nothing else: it climbs a crossing gap if it has a column
        to get over, and it climbs or falls to meet the chain end's own height,
        both at the ends of the run where there is room for the slope.
        """
        node = net.nodes[index]
        crossed = self.spine[net.label].bridged_taps[index]
        side_port = ports[2]
        at_side: _Anchor = (
            world_port(obj.pose.transform(), side_port),
            _flat(port_forward(obj.pose.transform(), side_port)),
            (obj.id, side_port.name),
        )
        at_row: _Anchor = (node.branch.point, _flat((-at_side[1][0], 0.0, 0.0)), node.branch.link)
        start, finish = (at_row, at_side) if node.merging else (at_side, at_row)
        route = self._tap_route(net, start[0], finish[0], crossed)
        tier = _tier(node.branch.rate, self.spec.belt_tiers, net.item)
        path = lay_path(
            route,
            registry=self.registry,
            class_name=_belt_class(self.lab_map, tier),
            item_id=net.item,
            rate=node.branch.rate,
            ids=self.ids,
            upstream=start[2],
            downstream=finish[2],
        )
        belts.extend(path.belts)
        links.extend(path.links)

    def _tap_route(self, net: _Net, start: Vector, end: Vector, crossed: tuple[int, ...]) -> Route:
        """One transverse, over what it crosses and down to where it ends."""
        span = abs(end[0] - start[0])
        over = max(start[2], end[2])
        if crossed:
            over = max(over, bridge_z_cm(self.belt_z, self.registry))
        up = descent_run_cm(over - start[2], self.registry)
        down = descent_run_cm(over - end[2], self.registry)
        flat = self.lead_in if up else 0.0
        room = min(self._room(start[0], crossed, net.side), self._room(end[0], crossed, net.side))
        if span + _EPS < flat + up + down or room + _EPS < max(flat + up, down):
            raise CorridorError(
                "bridge",
                f"a {net.item!r} belt across the corridor has {span:.0f} cm to climb "
                f"{over - start[2]:.0f} cm and come down {over - end[2]:.0f} cm in",
            )
        route = Route(point=start, heading=_flat((end[0] - start[0], 0.0, 0.0)))
        route.go(flat)
        route.go(up, over - start[2])
        route.go(span - flat - up - down)
        route.go(down, end[2] - over)
        return route

    def _leave(self, terminal: _Terminal, x: float, side: int) -> _Anchor:
        """Where a path starts, and which way it heads out of there."""
        if terminal.wall:
            return ((x, -self.half, self.belt_z), _UP, None)
        return (terminal.point, (float(side), 0.0, 0.0), terminal.link)

    def _arrive(self, terminal: _Terminal, x: float, side: int) -> _Anchor:
        """Where a path ends, and which way it is going when it gets there."""
        if terminal.wall:
            return ((x, self.half, self.belt_z), _UP, None)
        return (terminal.point, (float(-side), 0.0, 0.0), terminal.link)

    def _lay(
        self,
        net: _Net,
        start: _Anchor,
        finish: _Anchor,
        rate: Fraction,
        belts: list[BeltRun],
        links: list[Link],
        assignment: Assignment,
        x: float | None = None,
    ) -> None:
        """One belt path: out of a port, up a column, and into the next port."""
        column = self._column_x(net.side, assignment.column) if x is None else x
        route = self._route(net, start, finish, column, assignment)
        tier = _tier(rate, self.spec.belt_tiers, net.item)
        path = lay_path(
            route,
            registry=self.registry,
            class_name=_belt_class(self.lab_map, tier),
            item_id=net.item,
            rate=rate,
            ids=self.ids,
            upstream=start[2],
            downstream=finish[2],
        )
        belts.extend(path.belts)
        links.extend(path.links)

    def _route(
        self, net: _Net, start: _Anchor, finish: _Anchor, x: float, assignment: Assignment
    ) -> Route:
        """The straights, climbs and quarter turns one path is made of.

        Four shapes, and the same three pieces in each: a transverse across the
        corridor at the ``Y`` the path leaves a row by, a run up the column, and a
        transverse at the ``Y`` it enters one by.  A path that starts or ends on
        the wall, or at a node standing in the column, simply has no transverse at
        that end.  Every turn is a quarter circle of the corridor's own radius,
        and every turn in the build goes the same way round, because a corridor is
        only ever entered from the rows and left towards them.
        """
        point, heading, _ = start
        end, arriving, _ = finish
        route = Route(point=point, heading=heading)
        left = net.side > 0
        bridge = bridge_z_cm(self.belt_z, self.registry)
        transverse_b = abs(arriving[0]) > 0.5
        stop = end[1] - (self.radius if transverse_b else 0.0)
        if abs(heading[0]) > 0.5:
            crossed = assignment.bridged_a
            self._run_out(route, net, x, bridge if crossed else point[2], crossed)
            route.turn(left, self.radius)
            if crossed:
                # A bridge comes down inside its own column, right after the turn.
                self._climb(route, net, self.belt_z, stop, first=True)
        self._climb(
            route,
            net,
            max(end[2], bridge) if transverse_b and assignment.bridged_b else end[2],
            stop,
            first=False,
        )
        if transverse_b:
            route.turn(left, self.radius)
            self._run_in(route, net, end, assignment.bridged_b)
        return route

    def _climb(self, route: Route, net: _Net, height: float, stop: float, *, first: bool) -> None:
        """Change height inside the column, on the way to ``stop``.

        ``first`` puts the slope at the near end of the run and leaves the rest of
        the column for someone else -- which is what a bridge coming down wants --
        and otherwise it goes at the far end, against the turn, which is what a
        bridge going up wants: the belt is at its height before it turns onto the
        transverse that crosses.
        """
        run = stop - route.point[1]
        rise = height - route.point[2]
        lift = descent_run_cm(rise, self.registry)
        if run < -_EPS or run + _EPS < lift:
            raise CorridorError(
                "bridge" if abs(rise) > _EPS else "depth",
                f"the {net.item!r} trunk has {run:.0f} cm of column to change {rise:.0f} cm of "
                f"height in and needs {lift:.0f}",
            )
        if first:
            route.go(lift, rise)
            return
        route.go(run - lift)
        route.go(lift, rise)

    def _run_out(
        self, route: Route, net: _Net, x: float, climb: float, crossed: tuple[int, ...]
    ) -> None:
        """The transverse a path leaves a row by, rising before what it crosses.

        The rise starts a flat :attr:`lead_in` out of the port, not at it: the
        port faces along the ground and ``ports.position`` holds this project to a
        belt that leaves a port along the port's own facing, which is the same
        rule the row builder's feeders keep.
        """
        span = abs(x - route.point[0]) - self.radius
        rise = climb - route.point[2]
        lift = descent_run_cm(rise, self.registry)
        flat = self.lead_in if lift else 0.0
        room = self._room(route.point[0], crossed, net.side)
        if span + _EPS < flat + lift or room + _EPS < flat + lift:
            raise CorridorError(
                "bridge",
                f"the {net.item!r} trunk needs {flat + lift:.0f} cm to climb {rise:.0f} cm out "
                f"of a row and has {min(span, room):.0f}",
            )
        route.go(flat)
        route.go(lift, rise)
        route.go(span - flat - lift)

    def _run_in(self, route: Route, net: _Net, end: Vector, crossed: tuple[int, ...]) -> None:
        """The transverse a path enters a row by, coming down after the last crossing."""
        drop = end[2] - route.point[2]
        fall = descent_run_cm(drop, self.registry)
        span = abs(end[0] - route.point[0])
        room = self._room(end[0], crossed, net.side)
        if span + _EPS < fall or room + _EPS < fall:
            raise CorridorError(
                "bridge",
                f"the {net.item!r} trunk needs {fall:.0f} cm to come down {-drop:.0f} cm into "
                f"a row and has {min(span, room):.0f}",
            )
        route.go(span - fall)
        route.go(fall, drop)

    def _room(self, x: float, crossed: tuple[int, ...], side: int) -> float:
        """How much of a transverse at ``x`` is clear of the columns it crosses.

        The slope has to be over before the NEAREST crossing and may only start
        again after the nearest one on the other side, so what bounds it is the
        crossed column closest to ``x`` -- which is the innermost crossed one at a
        row's end of a transverse and the outermost at the corridor's end.  Taking
        the closest says both without the caller having to say which end it is.

        A crossed belt's lane is :data:`BRIDGE_CLEARANCE_BOXES` half-widths wide,
        and only past that may a bridge give up the height it climbed for.
        """
        if not crossed:
            return math.inf
        nearest = min(abs(x - self._column_x(side, column)) for column in crossed)
        return nearest - BRIDGE_CLEARANCE_BOXES * BELT_CLEARANCE_HALF_WIDTH_CM

    # --- the floor and the placement ----------------------------------------

    def _floor(self) -> tuple[FoundationObj, ...]:
        """A full floor of the shipped foundation, covering the designer.

        Each slab stands at half its own box's thickness, so that its top is
        where the row builder stands its machines.
        """
        side = self.designer.foundation_cm
        box = self.registry.buildables[FOUNDATION_CLASS].clearance[0]
        stand = (box.max[2] - box.min[2]) / 2.0
        count = int(round(2.0 * self.half / side))
        return tuple(
            FoundationObj(
                id=next(self.ids),
                class_name=FOUNDATION_CLASS,
                pose=Pose(
                    -self.half + side / 2.0 + i * side,
                    -self.half + side / 2.0 + j * side,
                    stand,
                    0.0,
                ),
            )
            for i in range(count)
            for j in range(count)
        )

    def _placement(
        self, corridors: tuple[list[AttachmentObj], list[BeltRun], list[Link]]
    ) -> SfyPlacement:
        attachments, belts, links = corridors
        machines: list[MachineObj] = []
        for row in self.rows:
            machines.extend(row.geometry.machines)
            attachments.extend(row.geometry.attachments)
            belts.extend(row.geometry.belts)
            links.extend(row.geometry.links)
        power = self._power()
        return SfyPlacement(
            designer=self.designer,
            machines=tuple(machines),
            attachments=tuple(attachments),
            belts=tuple(belts),
            poles=power.poles,
            wires=power.wires,
            foundations=self._floor(),
            links=tuple(links),
            description=self._description(belts, power),
            short_desc=f"{self.spec.label or 'manifold rows'}: {len(machines)} machines",
        )

    def _power(self) -> PowerPlan:
        """A pole line along every row, and a wire from every machine onto it.

        The rows are handed over as they finally stand, so the poles are placed
        against the boxes the validator will judge; the corridor is not, because
        a belt carries no clearance box of its own and a pole's own box is soft.
        Power objects take the last ids in the build.
        """
        return place_power(
            [PowerRow(row.geometry.machines, row.geometry.attachments) for row in self.rows],
            self.registry,
            ids=self.ids,
            designer=self.designer,
        )

    def _description(self, belts: Sequence[BeltRun], power: PowerPlan) -> str:
        """The manifest's precursor: what arrives where, what leaves where, what runs."""
        lines = [f"{self.spec.label or 'manifold rows'} in a {self.designer.mark} designer"]
        for run in sorted(belts, key=lambda belt: (belt.item_id, belt.start[0])):
            if run.boundary_start:
                lines.append(f"entry: {run.item_id} at x={run.start[0]:.0f}")
        for run in sorted(belts, key=lambda belt: (belt.item_id, belt.end[0])):
            if run.boundary_end:
                lines.append(f"exit: {run.item_id} at x={run.end[0]:.0f}")
        for row in self.rows:
            lines.append(
                f"row {row.index}: {row.group.count} x {row.group.machine_class} running "
                f"{row.group.recipe_id} at {float(row.group.clock) * 100:g}%"
            )
        lines += [f"power: {line}" for line in power.lines]
        return "\n".join(lines)

    # --- the refusal that comes before any geometry -------------------------

    def _refuse_fluids(self) -> None:
        """A fluid is M4, and FactorioLab is what says an item is one.

        The dataset marks a fluid by carrying no stack size, which is the test
        :mod:`flab2bp.sfy.rates` already refuses a whole flow on; a spec built by
        hand can still carry one, and piping is not this milestone.
        """
        data = _dataset()
        wanted = {
            *self.spec.external_inputs,
            *self.spec.outputs,
            *self.spec.surplus_outputs,
            *(item for group in self.spec.groups for item in group.inputs_per_machine),
            *(item for group in self.spec.groups for item in group.outputs_per_machine),
        }
        wet = sorted(
            item
            for item in wanted
            if (entry := data.get_item(item)) is not None and entry.stack is None
        )
        if wet:
            raise _refuse(self.spec, "fluids are M4", f"this build moves {', '.join(wet)}")


# --- helpers ---------------------------------------------------------------

_UP: Vector = (0.0, 1.0, 0.0)
"""The way every corridor column runs: from the ``-Y`` wall towards the ``+Y``."""


@cache
def _dataset() -> Any:
    """FactorioLab's own Satisfactory dataset, loaded once."""
    return load_vendored(Game.SFY)


def _grid(registry: Registry) -> float:
    grid = registry.limits.hologram_grid_cm
    if grid is None:
        raise RowError("the registry states no hologram grid, so nothing has a place to stand")
    return grid


def _lead_cm(registry: Registry, grid: float) -> float:
    """The shortest run between a corridor attachment's port and a turn or another.

    The port stands 100 cm out of the attachment and ``belt.min_length`` refuses a
    belt at or under ``belt_min_length_cm``, so this is the sum of the two on the
    grid, and both numbers are the registry's.
    """
    floor = registry.limits.belt_min_length_cm
    if floor is None:
        raise RowError("the registry states no minimum belt length, so no run can be sized")
    splitter = registry.buildables[SPLITTER_CLASS]
    reach = max(abs(port.translation[0]) for port in splitter.ports if port.kind == "belt")
    return _grid_ceil(reach + floor, grid)


def _lead_in_cm(registry: Registry, grid: float) -> float:
    """How far a belt runs flat out of a port before it starts to climb.

    One minimum belt length on the grid, which is the row builder's own
    ``LEAD_IN_MULTIPLE`` rule and for its reason: ``ports.position`` refuses a
    belt that leaves a port more than ``PORT_ANGLE_RAD`` off the port's facing,
    and every belt port in this build faces along the ground.  The shortest flat
    piece the game would let stand on its own is the smallest honest answer.
    """
    floor = registry.limits.belt_min_length_cm
    if floor is None:
        raise RowError("the registry states no minimum belt length, so no run can be sized")
    return _grid_ceil(floor, grid)


def _grid_ceil(value: float, grid: float) -> float:
    return math.ceil(value / grid - 1e-9) * grid


def _grid_floor(value: float, grid: float) -> float:
    return math.floor(value / grid + 1e-9) * grid


def _grid_round(value: float, grid: float) -> float:
    return round(value / grid) * grid


def _flat(vector: Vector) -> Vector:
    """A facing as a heading along one axis: which way a belt runs at a port."""
    return (1.0 if vector[0] > 0 else -1.0, 0.0, 0.0)


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


def _row_gap(before: RowGeometry, after: RowGeometry, radius: float, grid: float) -> float:
    """How much floor is left between two rows' bands.

    One grid step is the brief's gap and the least the build gun would leave.
    Where a trunk turns out of one row and into the next it wants more: each turn
    eats its own radius of ``Y``, and what the two bands already hold beyond
    their chain ends counts towards it.  So the gap is whatever the two turns
    still want, and never less than a grid step.  Ours, and derived: the radius
    is the corridor's and the overhangs are the rows' own.
    """
    over = (before.y_max_cm - before.chain_out.pose.y) + (
        min(end.pose.y for end in after.chain_in) - after.y_min_cm
    )
    return max(grid, _grid_ceil(2.0 * radius - over, grid))


def _wall_margin(row: RowGeometry, radius: float, grid: float, *, entry: bool) -> float:
    """How much floor is left between the designer wall and the first (or last) row.

    Ours, and the same arithmetic as :func:`_row_gap` with the wall standing in
    for the other row: a belt arriving at the wall has to turn into the row's
    chain end, and that turn eats a radius of ``Y`` which the row's own band may
    already cover.

    One grid step more, for the belt to run straight out of the wall before it
    turns.  That is ours too, and it is geometry rather than taste: a belt's
    clearance box is 79 cm to each side of its centreline and square to it, so a
    box on a turning piece that began ON the wall would have a corner outside the
    designer -- 4.8 cm outside, measured -- and ``geom.bounds`` would refuse the
    build.  A straight step at the wall puts the first box square to it.
    """
    inside = (
        min(end.pose.y for end in row.chain_in) - row.y_min_cm
        if entry
        else row.y_max_cm - row.chain_out.pose.y
    )
    return max(0.0, _grid_ceil(radius + grid - inside, grid))


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


def _port(buildable: Buildable, name: str) -> Port:
    return next(port for port in buildable.ports if port.name == name)


def _corridor_ports(buildable: Buildable, pose: Pose, inward: float) -> tuple[Port, Port, Port]:
    """``(through in, through out, side)`` of an attachment standing in a column.

    The through pair is the one on the attachment's own ``+-X``, which yaw 90 has
    turned to run up the corridor; the side port is whichever of the other two
    faces INTO the corridor, towards the rows, which is where the belt on it goes.
    Both are read off the registry's own port table -- by position for the through
    pair and by facing for the side one -- and never by name.
    """
    belt_ports = [port for port in buildable.ports if port.kind == "belt"]
    through = [port for port in belt_ports if abs(port.translation[0]) > abs(port.translation[1])]
    entry = next(port for port in through if port.direction == "input")
    exit_ = next(port for port in through if port.direction == "output")
    transform = pose.transform()
    side = next(
        port
        for port in belt_ports
        if port not in (entry, exit_) and port_forward(transform, port)[0] * inward > 0.5
    )
    return (entry, exit_, side)


def _carried(net: _Net, after: int) -> Fraction:
    """What the spine carries once it has passed ``after`` nodes."""
    merged = sum(
        (node.branch.rate for node in net.nodes[:after] if node.merging), net.sources[0].rate
    )
    split = sum((node.branch.rate for node in net.nodes[:after] if not node.merging), Fraction(0))
    return merged - split


def _tier(rate: Fraction, tiers: Sequence[BeltTier], item_id: str) -> BeltTier:
    """The slowest belt the spec funds that carries ``rate``."""
    for tier in sorted(tiers, key=lambda tier: tier.items_per_second):
        if tier.items_per_second >= rate:
            return tier
    raise RowError(
        f"run exceeds the belt ceiling: a corridor trunk carries {rate} items/s of "
        f"{item_id!r}, past every belt this spec allows"
    )


def _belt_class(lab_map: LabMap, tier: BeltTier) -> str:
    try:
        return machine_class(lab_map, tier.item_id)
    except KeyError as exc:
        raise RowError(f"the lab map has no conveyor class for {tier.item_id!r}") from exc
