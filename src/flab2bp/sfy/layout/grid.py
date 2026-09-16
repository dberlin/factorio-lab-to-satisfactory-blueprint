"""``GridRouted``: machines packed on the hologram grid, belts found by search.

The other shape a Satisfactory build can have.  :mod:`flab2bp.sfy.layout.strategy`
lays a build out as rows with a corridor down each side, which is what a player
draws by hand; this one stands every machine on the build gun's own 1 m hologram
grid wherever a CP-SAT no-overlap model likes it, and then finds every belt with
a geometric interval router over :class:`~flab2bp.sfy.layout.lattice.Lattice`.
Nothing here lays a row, and nothing here is a template (global constraint 8):
where a belt goes is what the router returns.

What this module does, and what the six beside it do
----------------------------------------------------
This one assembles, and it is a LOOP rather than a pipeline.  It reads every
limit once into a :class:`~flab2bp.sfy.layout.corridors.Measures`, builds the
lattice, and then tries deterministic arrangements while the deadline permits:

1. :func:`~flab2bp.sfy.layout.packer.pack` stands the machines, seeded by the
   arrangement number and priced by what the last round's routing learned;
2. :func:`~flab2bp.sfy.layout.lattice.occupancy_for` flattens them onto the
   lattice;
3. :func:`~flab2bp.sfy.layout.grid_nets.nets_for` says what has to be belted;
4. :func:`~flab2bp.sfy.layout.rrr.route_all` negotiates every net across rip-up
   rounds and hands back the trees it committed and the nets it could not;
5. where nothing is stranded, :func:`~flab2bp.sfy.layout.grid_power.place_on_free_nodes`
   stands the poles on what the router left free and
   :func:`~flab2bp.sfy.layout.floor.foundations` lays the slab underneath.

Where something IS stranded, the stranded net ids and the router's blame are
folded into a :class:`~flab2bp.sfy.layout.packer.Feedback`, decayed by
:data:`~flab2bp.sfy.layout.packer.DECAY` at the boundary, and the machines are
packed again against a floor the router has already been over. The first
three arrangements reserve shares of the remaining time; later arrangements
may use whatever remains under the same absolute deadline.

**The router RETURNS its failures.**  That is
:mod:`~flab2bp.sfy.layout.rrr`'s own decision and it is why this module is a
loop at all: a routing refusal raised out of the router would be a strategy
shipping half a build, and a routing refusal swallowed here would be a strategy
that never asked the packer to move anything.

It does NOT own the vocabulary of refusal.  That is
:mod:`flab2bp.sfy.layout.refusals`, shared with the manifold: this module maps
each stage's own error onto one of its named causes and refuses to guess at one
it has not got, the way :func:`~flab2bp.sfy.layout.strategy._row_cause` does.

Legality is the validator's
---------------------------
Nothing here decides what the game accepts.  Every placement this module returns
is meant to come back clean from ``validate(placement, spec, registry)``, and
where it cannot lay one it refuses with
:class:`~flab2bp.layout.base.NoValidLayout` and a cause out of
:data:`~flab2bp.sfy.layout.refusals.REFUSALS`.

Nothing here searches cells (global constraint 7).  The searching is the
kernel's, behind :func:`~flab2bp.sfy.layout.rrr.route_all`; the Python in this
file works on arrangements, nets and outcomes.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from flab2bp.layout.budget import BudgetExhausted, WorkBudget, expired
from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.corridors import CorridorError, Measures
from flab2bp.sfy.layout.floor import foundations
from flab2bp.sfy.layout.grid_nets import (
    GridNet,
    NetError,
    belt_class_for,
    nets_for,
)
from flab2bp.sfy.layout.grid_power import place_on_free_nodes
from flab2bp.sfy.layout.lattice import Lattice, Node, Occupancy, occupancy_for
from flab2bp.sfy.layout.manifold import RowError
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    Link,
    MachineObj,
    SfyPlacement,
)
from flab2bp.sfy.layout.packer import Feedback, Pack, PackError, pack
from flab2bp.sfy.layout.power import PowerError, PowerPlan
from flab2bp.sfy.layout.realise import Realised, RealiseError
from flab2bp.sfy.layout.refusals import GAME_DATA, GAME_LIMITS, refuse
from flab2bp.sfy.layout.rrr import BLAME_WEIGHT, RoutingOutcome, route_all
from flab2bp.sfy.layout.strategy import _dataset, _measure, _row_cause
from flab2bp.sfy.registry import LIFT_NATIVE_CLASS, Registry, load_registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec

__all__ = ["INITIAL_ARRANGEMENTS", "GridRouted"]

INITIAL_ARRANGEMENTS: Final = 3
"""How many initial arrangements reserve separate shares of the remaining time.

**Ours.** Preserve the measured three-arrangement schedule, but do not refuse
just because three early attempts finished. Further deterministic arrangements
may use the caller's remaining deadline. This is a scheduling horizon, not a
retry cap or an extension of the total wall.
"""

PACK_SHARE: Final = 2.0 / 3.0
"""Packing's share of one arrangement's existing wall-clock slice.

Ours, measured on the pinned plate chain: at the default 15-second budget,
the former 1.67-second pack yielded an unroutable 87000-cost incumbent.
A 3-second pack yielded a 69000-cost incumbent that routed in one round.
Two thirds gives packing 3.33 seconds and routing 1.67 seconds in the first
five-second slice, without extending the total deadline or losing retries.
"""

STRANDED_WEIGHT: Final = 1.0
"""What one stranded net adds to its own weight in the next arrangement.

**Ours.**  :class:`~flab2bp.sfy.layout.packer.Feedback` prices a net's distance
at ``1 + weight``, so one stranding doubles what pulling that net's ports
together is worth and a second stranding trebles it -- which is the ordering the
packer needs and not a claim about how much harder the net really is.
"""

HOT_NODE_SCALE: Final = 1.0 / BLAME_WEIGHT
"""What one of the router's blame charges is worth to the packer, in nodes of belt.

**Ours.**  The router charges :data:`~flab2bp.sfy.layout.rrr.BLAME_WEIGHT` at a
node it proved something about, on a scale whose unit is a congestion price; the
packer's objective is in NODES of belt.  One charge is worth one node here, so a
machine whose footprint covers ten blamed nodes costs the same as ten nodes of
extra belt -- comparable with the distances it is minimised against, where the
raw charge would be forty times any of them and would decide every arrangement
on the blame alone.
"""

WORKERS: Final = 1
"""CP-SAT search workers one pack may use.

One, because a strategy is PURE: same spec, same designer, same placement,
modulo the time budget.  More than one worker makes the incumbent a race between
threads, and a strategy that returned a different arrangement on every run would
make the race in :mod:`flab2bp.sfy.layout.measure` unrepeatable.
"""

#: What a :class:`~flab2bp.sfy.layout.grid_nets.NetError`'s cause is called where
#: a caller can see it.  The four the net planner declares, and no default: a
#: caller told "a port is off the hologram lattice" about a machine the registry
#: does not carry would go and look at the wrong file.
_NET_CAUSES: Final[Mapping[str, str]] = {
    "ceiling": "run exceeds the belt ceiling",
    "lattice": "a port is off the hologram lattice",
    "ports": "more input items than the machine has belt ports",
    "data": GAME_DATA,
}

#: What a :class:`~flab2bp.sfy.layout.power.PowerError`'s cause is called where a
#: caller can see it.  The same five the manifold names, because the refusals
#: are the shared ones and the stage under both is the same code.
_POWER_CAUSES: Final[Mapping[str, str]] = {
    "wire": "wire exceeds the maximum length",
    "room": "no room for a power pole",
    "port": "a machine has no power connection",
    "data": GAME_DATA,
    "limits": GAME_LIMITS,
}

#: What a :class:`~flab2bp.sfy.layout.corridors.CorridorError` means HERE.
#: Only two of that module's causes can reach this strategy -- it reads the
#: corridor's limits through :func:`~flab2bp.sfy.layout.strategy._measure` and
#: its turn arithmetic through the realiser, and never plans a corridor -- so
#: only those two are named.  Anything else is a corridor error from a code path
#: this module does not have and is raised rather than dressed in a cause it has
#: nothing to do with.
_CORRIDOR_CAUSES: Final[Mapping[str, str]] = {
    "limits": GAME_LIMITS,
    "data": GAME_DATA,
}


class GridRouted:
    """Machines on the hologram grid, belts negotiated across rip-up rounds."""

    name = "grid-routed"

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
        """Lay ``spec`` out inside ``designer``, or refuse with a named cause.

        The one place every stage's own error becomes one of
        :data:`~flab2bp.sfy.layout.refusals.REFUSALS`.  A budget that ran out is
        told apart by which stage was holding it, because a caller does
        something different about each: a pack that never found an arrangement
        wants a smaller build, and a routing that ran out of clock wants more
        seconds.
        """
        run = _Run(
            spec=spec,
            designer=designer,
            registry=load_registry() if registry is None else registry,
            lab_map=load_lab_map() if lab_map is None else lab_map,
            deadline=(
                absolute_deadline
                if absolute_deadline is not None
                else time.monotonic() + time_budget_s
            ),
        )
        try:
            return run.build()
        except BudgetExhausted as exc:
            raise refuse(spec, run.over_budget, str(exc)) from exc
        except PackError as exc:
            # The packer's cause IS the refusal string: a pack has one reading,
            # and ``PackError`` already refuses a cause that is not in the table.
            raise refuse(spec, exc.cause, exc.detail) from exc
        except NetError as exc:
            raise refuse(spec, _named(_NET_CAUSES, exc.cause, "net planner"), exc.detail) from exc
        except RealiseError as exc:
            raise refuse(spec, "a belt could not be laid", exc.detail) from exc
        except PowerError as exc:
            raise refuse(spec, _named(_POWER_CAUSES, exc.cause, "power stage"), exc.detail) from exc
        except CorridorError as exc:
            raise refuse(spec, _named(_CORRIDOR_CAUSES, exc.cause, "corridor"), exc.detail) from exc
        except RowError as exc:
            raise refuse(spec, _row_cause(exc), str(exc)) from exc


def _named(causes: Mapping[str, str], cause: str, stage: str) -> str:
    """One stage's own cause as a named refusal, or a refusal to guess at it.

    A default here would tell a caller the wrong thing about the wrong file,
    which is worse than a stack trace.  The discipline is
    :func:`~flab2bp.sfy.layout.strategy._row_cause`'s, and so is the remedy:
    name the new cause here and in
    :data:`~flab2bp.sfy.layout.refusals.REFUSALS` deliberately.
    """
    named = causes.get(cause)
    if named is None:
        raise ValueError(
            f"this module has no named refusal for the {stage}'s {cause!r}; add one here and "
            "to flab2bp.sfy.layout.refusals.REFUSALS rather than letting it wear another "
            "cause's name"
        )
    return named


# --- one run ----------------------------------------------------------------


@dataclass
class _Run:
    """One run of the strategy: the loop, and what each pass through it learned."""

    spec: SfyBuildSpec
    designer: Designer
    registry: Registry
    lab_map: LabMap
    deadline: float
    #: Which refusal a :class:`~flab2bp.layout.budget.BudgetExhausted` earns,
    #: which is a property of the stage holding the ledger when it fires.  The
    #: packer is the first stage of every arrangement, so this starts on its
    #: cause and the router's cause is set the moment the router is entered.
    over_budget: str = "packing exceeded the budget"

    def build(self) -> SfyPlacement:
        """The loop: pack, route, and either stand the poles or learn and go again.

        The order is the only one there is.  A fluid is refused before any
        geometry; the limits are read once so that no two stages can be laid out
        against different numbers; the lattice is built from the designer and
        the registry and never from anything a stage decided; and the power
        stage runs LAST, on the occupancy the router left holding its best
        round, because a pole may only stand where no belt does.
        """
        self._refuse_fluids()
        measures = _measure(self.registry, self.designer)
        lattice = Lattice.over(self.designer, self.registry)
        feedback: Feedback | None = None
        outcome: RoutingOutcome | None = None
        tried = 0
        for arrangement in itertools.count(1):
            if arrangement > 1 and expired(self.deadline):
                break
            ends = self._slice(arrangement)
            packed = self._pack(lattice, measures, feedback, arrangement, ends)
            tried = arrangement
            ids = _numbering(packed.machines)
            occupancy = occupancy_for(lattice, packed.machines, (), (), (), self.registry)
            nets = nets_for(self.spec, packed.machines, lattice, self.registry, self.lab_map)
            outcome = self._route(nets, occupancy, measures, ids, ends)
            if expired(self.deadline):
                break
            if not outcome.stranded:
                placement = self._placement(packed, occupancy, outcome, nets, arrangement, ids)
                if expired(self.deadline):
                    raise refuse(
                        self.spec,
                        "routing exceeded the budget",
                        "the layout deadline expired while finalizing the routed arrangement",
                    )
                return placement
            feedback = _folded(feedback, outcome)
        raise self._unrouted(outcome, tried)

    # --- the wall -----------------------------------------------------------

    def _slice(self, arrangement: int) -> float:
        """The first three reserve shares; later attempts use the remaining wall."""
        slots = max(INITIAL_ARRANGEMENTS - arrangement + 1, 1)
        if slots == 1:
            return self.deadline
        now = time.monotonic()
        return now + max(self.deadline - now, 0.0) / slots

    # --- the stages ---------------------------------------------------------

    def _pack(
        self,
        lattice: Lattice,
        measures: Measures,
        feedback: Feedback | None,
        arrangement: int,
        ends: float,
    ) -> Pack:
        """One arrangement of the machines, seeded by which arrangement it is.

        The seed is the arrangement number, so two runs of the same spec walk
        the same sequence of arrangements: the feedback is what
        makes the second differ from the first, and a seed that came from a
        clock would make it differ from itself.
        """
        self.over_budget = "packing exceeded the budget"
        return pack(
            self.spec,
            self.designer,
            self.registry,
            lattice,
            feedback=feedback,
            deadline=time.monotonic() + PACK_SHARE * max(ends - time.monotonic(), 0.0),
            workers=WORKERS,
            seed=arrangement,
            measures=measures,
        )

    def _route(
        self,
        nets: Sequence[GridNet],
        occupancy: Occupancy,
        measures: Measures,
        ids: Iterator[int],
        ends: float,
    ) -> RoutingOutcome:
        """Every net, negotiated -- and the rest of this arrangement's wall.

        The ledger carries the CLOCK and no work allowance.  A work bound would
        be a second bound nobody set: the caller handed over a wall, every
        search is already capped at
        :data:`~flab2bp.sfy.layout.router.MAX_SEARCH_WORK` of its own, and a
        net the clock stops is stranded with evidence that says so.
        """
        self.over_budget = "routing exceeded the budget"
        return route_all(
            nets,
            occupancy,
            measures=measures,
            registry=self.registry,
            budget=WorkBudget(deadline=ends),
            deadline=ends,
            ids=ids,
            belt_class_for=belt_class_for(self.spec, self.lab_map),
            lift_class=self._lift_class(nets),
        )

    def _lift_class(self, nets: Sequence[GridNet]) -> str:
        """Which conveyor lift this build climbs with, out of the game data.

        ONE class serves the whole build --
        :func:`~flab2bp.sfy.layout.rrr.route_all` takes one -- so it has to be
        the one that carries the fastest piece of belt the router can lay.  No
        piece of a net's tree carries more than the net's own total rate, so the
        heaviest net's tier bounds every piece, and the lift is the one the
        registry gives the same ``belt_speed_per_min`` as that tier's conveyor.
        That is the game's own number on both sides and not a reading of the two
        class NAMES: a registry that renamed either would still pair them.

        A build with no nets at all -- a hand-built fragment -- falls back on the
        belt the spec itself funds, which is the same question asked of the only
        rate there is.
        """
        heaviest = max((net.rate for net in nets), default=self.spec.belt_items_per_second)
        item = next((net.item_id for net in nets if net.rate == heaviest), self.spec.belt_item_id)
        belt = belt_class_for(self.spec, self.lab_map)(heaviest, item)
        speed = self.registry.buildables[belt].belt_speed_per_min
        if speed is None:
            raise NetError("data", f"the registry states no belt speed for {belt}")
        lifts = sorted(
            buildable.class_name
            for buildable in self.registry.buildables.values()
            if buildable.native_class == LIFT_NATIVE_CLASS
            and buildable.lift is not None
            and buildable.belt_speed_per_min == speed
        )
        if not lifts:
            raise NetError(
                "data",
                f"the registry describes no conveyor lift that carries {speed:.0f} items a "
                f"minute, which is what {belt} carries, so this build has no way to climb",
            )
        return lifts[0]

    # --- the placement ------------------------------------------------------

    def _placement(
        self,
        packed: Pack,
        occupancy: Occupancy,
        outcome: RoutingOutcome,
        nets: Sequence[GridNet],
        arrangement: int,
        ids: Iterator[int],
    ) -> SfyPlacement:
        """Everything the arrangement and its routing made, plus power and a floor.

        The poles stand on ``occupancy``, which
        :func:`~flab2bp.sfy.layout.rrr.route_all` left holding its BEST round
        rather than its last, so a pole can never be put through a belt the
        strategy is about to return.  The floor takes the last ids in the build,
        as it does under the manifold.
        """
        attachments: list[AttachmentObj] = []
        belts: list[BeltRun] = []
        lifts: list[LiftObj] = []
        links: list[Link] = []
        for laid in outcome.realised:
            attachments.extend(laid.attachments)
            belts.extend(laid.belts)
            lifts.extend(laid.lifts)
            links.extend(laid.links)
        power = place_on_free_nodes(
            packed.machines, occupancy, self.registry, ids=ids, designer=self.designer
        )
        return SfyPlacement(
            designer=self.designer,
            machines=packed.machines,
            attachments=tuple(attachments),
            belts=tuple(belts),
            lifts=tuple(lifts),
            poles=power.poles,
            wires=power.wires,
            foundations=foundations(self.designer, self.registry, ids),
            links=tuple(links),
            description=self._description(
                packed, outcome, nets, arrangement, power, occupancy.lattice
            ),
            short_desc=f"{self.spec.label or 'grid routed'}: {len(packed.machines)} machines",
        )

    def _description(
        self,
        packed: Pack,
        outcome: RoutingOutcome,
        nets: Sequence[GridNet],
        arrangement: int,
        power: PowerPlan,
        lattice: Lattice,
    ) -> str:
        """The manifest's precursor: what a reader cannot measure off the blueprint.

        Which of the arrangements was the one that routed, and whether the
        packer proved it optimal or merely held it when the clock stopped; how
        many rip-up rounds the negotiation took; what the build had to author to
        get its belts round corners and up levels.  None of that survives into
        the ``.sbp``, and all of it is what somebody asking "why does it look
        like this" wants.
        """
        turns, taps = _attachments_by_role(outcome.realised)
        arcs = sum(laid.turns.count("arc") for laid in outcome.realised)
        lifts = sum(len(laid.lifts) for laid in outcome.realised)
        lines = [
            f"{self.spec.label or 'grid routed'} in a {self.designer.mark} designer, "
            f"grid-routed: every machine on the hologram grid and every belt searched for"
        ]
        for tree in outcome.trees:
            for branch, node in tree.boundaries:
                entering = node == tree.paths[branch][0]
                label, wall = ("entry", "-Y") if entering else ("exit", "+Y")
                x, _, z = lattice.world(node)
                y = -self.designer.half_cm if entering else self.designer.half_cm
                lines.append(
                    f"{label}: {tree.net.item_id} at the {wall} wall, "
                    f"belt crossing ({x:g}, {y:g}, {z:g}) cm"
                )
        lines.append(
            f"arrangement {arrangement}: {len(packed.machines)} machines "
            f"packed {packed.status} at objective {packed.objective:.0f}"
        )
        lines.append(
            f"routing: {outcome.rounds} rip-up rounds over {len(nets)} nets, none stranded, "
            f"{outcome.work} work"
        )
        lines.append(
            f"authored: {lifts} conveyor lifts, {turns + taps} conveyor attachments "
            f"({taps} standing on a tap, {turns} turning a corner)"
        )
        lines.append(f"turns: {arcs} arc, {turns} attachment; {lifts} lift transitions")
        lines += [f"power: {line}" for line in power.lines]
        return "\n".join(lines)

    # --- the refusal after the last attempted arrangement -------------------

    def _unrouted(self, outcome: RoutingOutcome | None, tried: int) -> Exception:
        """The absolute deadline ended continuation, not a proof of impossibility."""
        spent = "the clock ran out before any arrangement routed every net"
        if outcome is not None and outcome.stranded:
            named = ", ".join(f"net {net.id} ({net.item_id})" for net, _ in outcome.stranded)
            spent = (
                f"{len(outcome.stranded)} nets were still unrouted when the clock ran out "
                f"after {tried} arrangements: {named}"
            )
        return refuse(self.spec, "routing exceeded the budget", spent)

    # --- the refusal that comes before any geometry -------------------------

    def _refuse_fluids(self) -> None:
        """A fluid is M5, and FactorioLab is what says an item is one.

        The same sentence :class:`~flab2bp.sfy.layout.strategy.ManifoldRows`
        refuses on, asked of the same dataset through the same reader: the
        dataset marks a fluid by carrying no stack size, and piping is not this
        milestone.  It is asked FIRST, before a limit is read or a lattice is
        built, because a build that moves water is not a build either strategy
        can lay out and a pack nobody can use is a pack nobody should pay for.
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
            raise refuse(self.spec, "fluids are M5", f"this build moves {', '.join(wet)}")


# --- what one arrangement hands the next ------------------------------------


def _folded(feedback: Feedback | None, outcome: RoutingOutcome) -> Feedback:
    """Last arrangement's evidence, faded, plus what this one just learned.

    Two halves, and both of them are EVIDENCE rather than constraints: no cheap
    surrogate predicts routability, so a net the router could not lay is made
    expensive to leave spread out and a floor it blamed is made expensive to
    stand on.  The fade is
    :meth:`~flab2bp.sfy.layout.packer.Feedback.decayed`'s, applied once per
    arrangement boundary, so the two halves cannot age at different rates.
    """
    base = Feedback(failed_nets={}, hot_nodes={}) if feedback is None else feedback.decayed()
    failed = dict(base.failed_nets)
    for net, _ in outcome.stranded:
        failed[net.id] = failed.get(net.id, 0.0) + STRANDED_WEIGHT
    hot: dict[Node, float] = dict(base.hot_nodes)
    for node, weight in outcome.blame.items():
        hot[node] = hot.get(node, 0.0) + weight * HOT_NODE_SCALE
    return Feedback(failed_nets=failed, hot_nodes=hot)


# --- small arithmetic over what was built -----------------------------------


def _numbering(machines: Sequence[MachineObj]) -> Iterator[int]:
    """The build's object counter, started past the ids the packer already spent.

    :func:`~flab2bp.sfy.layout.packer.pack` numbers its machines from one, and
    every other object in the build is numbered by this one counter, so it
    starts where the machines stop: an id is what a :class:`Link` names and what
    the actor's name in the file carries, and two objects sharing one would be
    two objects the file cannot tell apart.
    """
    return itertools.count(len(machines) + 1)


def _attachments_by_role(realised: Sequence[Realised]) -> tuple[int, int]:
    """Count realised attachment turns separately from tree junctions."""
    turns = sum(laid.turns.count("attachment") for laid in realised)
    taps = sum(len(laid.attachments) for laid in realised) - turns
    return (turns, taps)
