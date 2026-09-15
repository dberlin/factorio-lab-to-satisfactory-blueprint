"""``ManifoldRows``: a whole Satisfactory build in one Blueprint Designer.

The shape is one a Satisfactory player would recognise.  Every machine group is
a *row* -- :func:`~flab2bp.sfy.layout.manifold.build_row`'s line of machines, fed
by splitter chains and drained by a merger chain -- and the rows are stacked
along ``Y`` in production order, each one turned about so that its input end
faces the corridor the row before it output into.  Down each side runs a
*corridor* of belt columns carrying every trunk: what the build takes in at the
``-Y`` wall, what one row hands to the next, and what leaves at the ``+Y`` wall.

What this module does, and what the three beside it do
------------------------------------------------------
This one assembles.  It reads every limit once into a
:class:`~flab2bp.sfy.layout.corridors.Measures`, hands that same value to the
three stages, and turns what they hand back into an
:class:`~flab2bp.sfy.layout.model.SfyPlacement` with a floor under it and a pole
line beside it.  The stages are :mod:`~flab2bp.sfy.layout.rows` (how many rows
each group is laid as and where they stand), :mod:`~flab2bp.sfy.layout.nets`
(which item runs up which corridor, where its mergers and splitters stand, and in
which column) and :mod:`~flab2bp.sfy.layout.laying` (the shape of every belt).

It also owns the vocabulary of refusal.  :data:`REFUSALS` is the whole of it and
:func:`_refuse` is the one place a refusal is built; a stage is handed that
function rather than the table, so nothing below this module can invent a cause.
The choices that are this project's own are made in the stage that makes them and
say so there.

Legality is the validator's
---------------------------
Nothing here decides what the game accepts.  Every placement this module returns
is meant to come back clean from ``validate(placement, spec, registry)``, and
where it cannot lay one it refuses with
:class:`~flab2bp.layout.base.NoValidLayout` and a cause out of :data:`REFUSALS`.
"""

from __future__ import annotations

import itertools
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from functools import cache, partial
from typing import Any

from flab2bp.lab.data import load_vendored
from flab2bp.lab.url import Game
from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import BudgetExhausted, WorkBudget, WorkLimits
from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.corridors import (
    CorridorError,
    Measures,
    attachment_box_cm,
    attachment_pitch_cm,
    attachment_reach_cm,
    belt_pitch_cm,
    turn_radius_cm,
)
from flab2bp.sfy.layout.laying import CorridorLayer
from flab2bp.sfy.layout.manifold import SPLITTER_CLASS, RowError, shortest_belt_cm
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    Link,
    MachineObj,
    Pose,
    SfyPlacement,
)
from flab2bp.sfy.layout.nets import NetPlanner
from flab2bp.sfy.layout.power import PowerError, PowerPlan, PowerRow
from flab2bp.sfy.layout.power import place as place_power
from flab2bp.sfy.layout.rows import RowPlan, RowPlanner
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, SfyBuildSpec

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
    "a curved leg is longer than a belt may be",
    "layout exceeded the budget",
    # Two more this shape of build can hit that the brief does not name.
    "a row makes something the spec never sends out",
    "a row is fed from the corridor on the other side of the build",
    # The row/corridor walk's own backstop, kept separate from the width bound it
    # used to borrow: a build that ran out of PASSES has not been shown not to
    # fit, and a reader told "rows exceed the designer width" would go measuring
    # a designer when what happened is that this module gave up.
    "row splitting did not converge",
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
    "curve": "a curved leg is longer than a belt may be",
    "limits": GAME_LIMITS,
}

#: What a :class:`~flab2bp.sfy.layout.power.PowerError`'s cause is called where a
#: caller can see it.
_POWER_CAUSES = {
    "wire": "wire exceeds the maximum length",
    "room": "no room for a power pole",
    "port": "a machine has no power connection",
    "data": GAME_DATA,
    "limits": GAME_LIMITS,
}

COLUMN_TRIES = 100_000
"""Column tries the search may make before it gives up.

Ours, and a backstop rather than a bound: a corridor of a dozen columns and a
hundred trunks is two orders below it, so reaching it means the search is not
converging rather than that the build is large.
"""


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

    def build(self) -> SfyPlacement:
        """The three stages, wired up, and the placement they add up to.

        The order is the only one there is: a fluid is refused before any
        geometry, the limits are read once so that no two stages can be laid out
        against different numbers, the rows and the corridors they need settle
        together (:meth:`~flab2bp.sfy.layout.rows.RowPlanner._plan_rows` asks the
        net planner what it took after every pass), and only then is anything
        drawn.  The object numbering runs through all of it, which is why the
        counter the row planner ends on is the one the corridors carry on from:
        the walk starts it again for every pass it throws away.
        """
        self._refuse_fluids()
        measures = _measure(self.registry, self.designer)
        refuse = partial(_refuse, self.spec)
        planner = RowPlanner(
            spec=self.spec,
            designer=self.designer,
            registry=self.registry,
            lab_map=self.lab_map,
            budget=self.budget,
            measures=measures,
            refuse=refuse,
            ids=self.ids,
        )
        nets = NetPlanner(
            spec=self.spec,
            registry=self.registry,
            budget=self.budget,
            measures=measures,
            refuse=refuse,
        )
        row_plan, corridor_plan = planner._plan_rows(
            lambda plan: nets._plan_columns(plan.rows, plan.x_edge)
        )
        self.ids = planner.ids
        layer = CorridorLayer(
            spec=self.spec,
            registry=self.registry,
            lab_map=self.lab_map,
            budget=self.budget,
            measures=measures,
            ids=self.ids,
            row_plan=row_plan,
            corridor_plan=corridor_plan,
        )
        corridors = layer._lay_corridors()
        return self._placement(measures, row_plan, corridors, tuple(layer.turns))

    # --- the floor and the placement ----------------------------------------

    def _floor(self, measures: Measures) -> tuple[FoundationObj, ...]:
        """A full floor of the shipped foundation, covering the designer.

        Each slab stands at half its own box's thickness, so that its top is
        where the row builder stands its machines.
        """
        side = self.designer.foundation_cm
        box = self.registry.buildables[FOUNDATION_CLASS].clearance[0]
        stand = (box.max[2] - box.min[2]) / 2.0
        count = int(round(2.0 * measures.half / side))
        return tuple(
            FoundationObj(
                id=next(self.ids),
                class_name=FOUNDATION_CLASS,
                pose=Pose(
                    -measures.half + side / 2.0 + i * side,
                    -measures.half + side / 2.0 + j * side,
                    stand,
                    0.0,
                ),
            )
            for i in range(count)
            for j in range(count)
        )

    def _placement(
        self,
        measures: Measures,
        row_plan: RowPlan,
        corridors: tuple[list[AttachmentObj], list[BeltRun], list[Link]],
        turns: tuple[str, ...],
    ) -> SfyPlacement:
        attachments, belts, links = corridors
        machines: list[MachineObj] = []
        for row in row_plan.rows:
            machines.extend(row.geometry.machines)
            attachments.extend(row.geometry.attachments)
            belts.extend(row.geometry.belts)
            links.extend(row.geometry.links)
        power = self._power(row_plan)
        return SfyPlacement(
            designer=self.designer,
            machines=tuple(machines),
            attachments=tuple(attachments),
            belts=tuple(belts),
            poles=power.poles,
            wires=power.wires,
            foundations=self._floor(measures),
            links=tuple(links),
            description=self._description(row_plan, belts, power, turns),
            short_desc=f"{self.spec.label or 'manifold rows'}: {len(machines)} machines",
        )

    def _power(self, row_plan: RowPlan) -> PowerPlan:
        """A pole line along every row, and a wire from every machine onto it.

        The rows are handed over as they finally stand, so the poles are placed
        against the boxes the validator will judge; the corridor is not, because
        a belt carries no clearance box of its own and a pole's own box is soft.
        Power objects take the last ids in the build.
        """
        return place_power(
            [PowerRow(row.geometry.machines, row.geometry.attachments) for row in row_plan.rows],
            self.registry,
            ids=self.ids,
            designer=self.designer,
        )

    def _description(
        self,
        row_plan: RowPlan,
        belts: Sequence[BeltRun],
        power: PowerPlan,
        turns: Sequence[str],
    ) -> str:
        """The manifest's precursor: what arrives where, what leaves where, what runs.

        It carries the two things about this build a reader cannot measure off
        the blueprint without laying it out again: which rows were laid facing
        each other because the spec's own rates paired them machine for machine,
        and how the corners were made.
        """
        lines = [
            f"{self.spec.label or 'manifold rows'} in a {self.designer.mark} designer, "
            f"{row_plan.band_cm:.0f} cm of band"
        ]
        for run in sorted(belts, key=lambda belt: (belt.item_id, belt.start[0])):
            if run.boundary_start:
                lines.append(f"entry: {run.item_id} at x={run.start[0]:.0f}")
        for run in sorted(belts, key=lambda belt: (belt.item_id, belt.end[0])):
            if run.boundary_end:
                lines.append(f"exit: {run.item_id} at x={run.end[0]:.0f}")
        for row in row_plan.rows:
            for group in row.groups:
                lines.append(
                    f"row {row.index}: {group.count} x {group.machine_class} running "
                    f"{group.recipe_id} at {float(group.clock) * 100:g}%"
                )
            if row.paired_item:
                lines.append(
                    f"row {row.index}: paired on {row.paired_item} -- "
                    f"{row.groups[0].count} straight belts, machine to machine, "
                    "no chain pair and no trunk between them"
                )
        tally = Counter(turns)
        lines.append(
            "turns: "
            + (
                ", ".join(f"{count} {kind}" for kind, count in sorted(tally.items()))
                if tally
                else "none"
            )
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


# --- the numbers every stage is laid out against ---------------------------


def _measure(registry: Registry, designer: Designer) -> Measures:
    """Every number the rest of this build is laid out against, read once.

    Apart from the designer's own half width these are all the corridor's, and
    all of them come out of ``registry.json``; nothing below this line reads a
    limit again.
    """
    return Measures(
        grid=_grid(registry),
        radius=turn_radius_cm(registry),
        pitch=belt_pitch_cm(registry),
        node_pitch=attachment_pitch_cm(registry),
        lead=_lead_cm(registry),
        lead_in=_lead_in_cm(registry),
        half=designer.half_cm,
        reach=attachment_reach_cm(registry),
        box=attachment_box_cm(registry),
    )


def _grid(registry: Registry) -> float:
    grid = registry.limits.hologram_grid_cm
    if grid is None:
        raise RowError("the registry states no hologram grid, so nothing has a place to stand")
    return grid


def _lead_cm(registry: Registry) -> float:
    """The shortest run between a corridor attachment's port and a turn or another.

    The port stands 100 cm out of the attachment and the shortest belt the game
    allows is :func:`~flab2bp.sfy.layout.manifold.shortest_belt_cm`, so this is
    the sum of the two, and both numbers are the registry's.  Not rounded to the
    grid: a belt is a spline between two ports, not a hologram on a cell.
    """
    splitter = registry.buildables[SPLITTER_CLASS]
    reach = max(abs(port.translation[0]) for port in splitter.ports if port.kind == "belt")
    return reach + shortest_belt_cm(registry.limits)


def _lead_in_cm(registry: Registry) -> float:
    """How far a belt runs flat out of a port before it starts to climb.

    The shortest belt the game allows, which is the row builder's own rule and
    for its reason: ``ports.position`` refuses a belt that leaves a port more
    than ``PORT_ANGLE_RAD`` off the port's facing, and every belt port in this
    build faces along the ground.  The shortest piece the game would let stand on
    its own is the smallest honest answer.
    """
    return shortest_belt_cm(registry.limits)


@cache
def _dataset() -> Any:
    """FactorioLab's own Satisfactory dataset, loaded once."""
    return load_vendored(Game.SFY)
