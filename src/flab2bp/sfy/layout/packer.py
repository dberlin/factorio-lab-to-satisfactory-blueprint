"""Where a grid-routed build stands its machines, and what it learned from the router.

A grid-routed build packs every machine on the build gun's own 1 m hologram grid
and then finds every belt with a geometric interval router.  This module is the
first half: :func:`pack` hands back one arrangement of
:class:`~flab2bp.sfy.layout.model.MachineObj`, and
:mod:`flab2bp.sfy.layout.rrr` finds out whether it can be wired.  Nothing here
predicts that -- see "no cut predicts routability" below.

**A machine stands ON a node** (R-M3-1).  Its ``(x, y)`` pair is a node pair --
its hologram-snapped centre -- ``z`` is the slab top, and its yaw is one of the
build gun's four quarter turns.  Everything the model reserves is stated about
that node: the hard footprint
(:func:`~flab2bp.sfy.layout.manifold.hard_footprint_cm`, the union of the
buildable's hard clearance boxes on the ground) turned by the yaw, and one
apron per belt port.

**ONE grid step between two hard boxes, and it is OURS** (R7).  ``buildable.clearance``
is ``partial`` -- ``AFGHologram::TestClearanceOverlap`` was never read -- so the
gap two holograms really need is unknown and this project keeps its own, and it
is the same one step :func:`~flab2bp.sfy.layout.manifold.machine_pitch_cm` puts
between two machines of a manifold row (a footprint plus ONE step, a 100 cm
gap).  A rule about the gap BETWEEN two boxes is stated on each of them as half
of it: every footprint enters the no-overlap grown by half a step on every side,
so two boxes that do not lap stand one whole step apart and no more.  Growing
each by a whole step would reserve two, which is a rule nobody stated and 100 cm
of floor per machine pair nobody asked for.  The margin against the designer
wall is the same rectangle: a grown footprint, and every apron, lies inside
:attr:`~flab2bp.sfy.layout.lattice.Lattice.open_lines`.

**The apron is DERIVED** -- see :func:`port_apron_nodes`.  A lift can never land
on a port node (its column would run through the machine it serves), so every
approach to a port is horizontal and the last corner before it is an attachment
turn, which needs the grid turn's ``reach + lead_in`` of straight run beside it.  An
apron reserves that many grid steps along the port's facing, including both
the port node and the final approach node, tied to the yaw.

What an apron keeps out of is a DIFFERENT rectangle from what a machine keeps
out of, and a larger one: an apron is somewhere a belt centreline really has to
stand, and :meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` denies any node
whose 158 cm belt box meets a hard box, so an apron reserved nearer than
:data:`~flab2bp.sfy.layout.validate.BELT_CLEARANCE_HALF_WIDTH_CM` to a machine
would be a run the router cannot use.  Each machine therefore carries a second
rectangle -- its hard box grown by that clearance -- and every OTHER machine's
apron is held off it.  Off it and off nothing else: an apron laps its own
machine's box, because the port sits inside it, and two machines' aprons may lap
each other, because belts share space by level and only footprints are
exclusive.

**Two integer frames, both exact.**  The no-overlap and the wall margin are in
whole centimetres measured from the designer's own ``-X``/``-Y`` wall, so that a
footprint edge that is not a multiple of the grid step (a Smelter's is 250 cm)
costs nothing in rounding; the objective is in NODES, because what it prices is
how far a belt has to run between two ports and a belt runs between nodes.  Node
``i`` is at ``grid * i`` in the centimetre frame, which is one translation away
from :meth:`~flab2bp.sfy.layout.lattice.Lattice.world`.  A footprint edge is
rounded OUTWARD to whole centimetres, which is stricter and never laxer.

**No cut predicts routability.**  The DSP packer built a routing-capacity
cumulative cut, measured it and removed it: no cheap surrogate reached 0.55 AUC
(``m3-router-reference.md`` §2.4).  So this model carries no reachability
constraint of any kind.  What it carries instead is EVIDENCE -- a
:class:`Feedback` of the nets the router could not lay and the nodes it blamed,
so that a second arrangement is drawn away from a floor the router has already
been over.

The two halves of that evidence enter the model differently.  A failed net
raises what pulling its own ports together is worth.  The :data:`HOT_NODES`
hottest ground positions become fixed one-node keep-outs, keeping the feedback
model proportional to machines times selected nodes rather than floor area.
If those keep-outs prove infeasible, packing retries with failed-net weights
alone: routing evidence is not proof that the machines cannot fit.

**The net ids are the router's, because there is only one place they come
from.**  :func:`~flab2bp.sfy.layout.grid_nets.net_plan` numbers every net a spec
has to belt before anything is placed, and both this module and
:func:`~flab2bp.sfy.layout.grid_nets.nets_for` read that one list -- the packer
for the ports it has to pull together, the router for the same ports with poses
on them -- so ``GridNet.id`` is ``NetPlan.id`` by construction rather than by
agreement.  That matters because :class:`Feedback` is keyed by net id: a weight
fed back under net 3 has to land on net 3.

What this module owes the plan is the MACHINE ORDER.  A plan names a port as
``(machine index, port name)`` into the flat machine list ``net_plan``'s
docstring states -- the spec's groups in spec order, each group's machines in
index order -- and :func:`_stands` builds exactly that list, so the machines in
:attr:`Pack.machines` are already at the indices the plan means.

**A wall side is a LINE, not a node** (R-M3-5).  A plan carries machine ports
only; an empty side is the designer wall, the ``-Y`` wall for a source and the
``+Y`` wall for a sink.  The router picks which node of that line it enters at,
so what the objective prices is the distance to the NEAREST point on the line --
the across-wall distance alone -- rather than to any node on it.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from heapq import nsmallest
from itertools import count
from typing import Final

from ortools.sat.python import cp_model

from flab2bp.sfy.layout.corridors import Measures, attachment_turn_tight
from flab2bp.sfy.layout.grid_nets import NetPlan, net_plan, terminal_for
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node
from flab2bp.sfy.layout.manifold import (
    RowError,
    hard_footprint_cm,
    shortest_belt_cm,
    slab_top_cm,
)
from flab2bp.sfy.layout.model import MachineObj, Pose
from flab2bp.sfy.layout.refusals import GAME_DATA, REFUSALS
from flab2bp.sfy.layout.strategy import _measure
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import Buildable, Registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec, SfyMachineGroup, direct_pairs

__all__ = [
    "DECAY",
    "NO_ARRANGEMENT",
    "OVER_BUDGET",
    "Feedback",
    "Pack",
    "PackError",
    "pack",
    "port_apron_nodes",
]

NO_ARRANGEMENT: Final = "the packer found no arrangement"
"""The refusal a spec that cannot be stood inside the designer earns.

The string itself, not a discriminator: unlike
:class:`~flab2bp.sfy.layout.corridors.CorridorError`'s causes, a packer refusal
has only one reading, so it is named out of
:data:`~flab2bp.sfy.layout.refusals.REFUSALS` verbatim and the strategy passes it
through rather than translating it.  :class:`PackError` checks that it is really
in the table.
"""

OVER_BUDGET: Final = "packing exceeded the budget"
"""The refusal a solve that ran out of clock with NO incumbent earns.

A solve that ran out of clock WITH one returns the incumbent instead: an
arrangement the router can try is worth more than a refusal, and the status name
on the :class:`Pack` says it was not proved optimal.
"""

DECAY: Final = 0.85
"""How much of one arrangement's evidence the next one keeps.

The DSP ``FeedbackState``'s own factor, applied by :meth:`Feedback.decayed` (see
``m3-router-reference.md`` §2.4: "evidence decays (0.85) at stage boundaries").
"""

_YAWS: Final = (0.0, 90.0, 180.0, -90.0)
"""The quarter turns the build gun places a grid object at -- ``buildable.rotation_step``."""

_WEIGHT_SCALE: Final = 1000
"""Fixed-point resolution for the objective's weights.

Not a physical number: CP-SAT minimises an integer expression and a feedback
weight is a float, so every weight is carried at a thousandth.  Nothing
geometric is scaled by it -- distances are whole nodes and boxes whole
centimetres.
"""

_DETERMINISTIC_TIME_PER_MACHINE: Final = 0.2
"""``max_deterministic_time`` per machine in the spec.

Scaled to the size of the problem rather than fixed, which is the DSP packer's
recorded failure ported as a warning: a constant calibrated at 15 strips and
handed 53 returned UNKNOWN on every solve (``m3-router-reference.md`` §2.4).
The NUMBER is a backstop rather than a measured constant -- the wall deadline is
what really bounds a solve -- but it is not arbitrary either: on the 3 + 3 chain
this milestone's tests pack, the incumbent reaches its best value by 0.2 units
and twenty-five times that budget does not improve it by one node, because the
bound on a no-overlap model stays far below the incumbent and the solve ends in
``FEASIBLE`` however long it runs.  The DSP packer's own measurement says the
same thing from the other side: longer packs wired FEWER cells.
"""

_DETERMINISTIC_TIME_FLOOR: Final = 1.0
"""The smallest whole budget a solve is given, however few machines it stands.

Presolve is charged to the same clock, so a per-machine budget alone would hand
a two-machine model less than its own presolve and get ``UNKNOWN`` back from a
problem with two rectangles in it.
"""

_SEED_MODULUS: Final = 2**31 - 1
"""``random_seed`` is an int32 field, so a caller's seed is taken modulo it."""


def port_apron_nodes(measures: Measures) -> int:
    """How many grid steps a belt port keeps clear of other machines.

    DERIVED, never written.  Task 7 measured that a lift can never land on a
    port node -- its column would run through the machine it serves -- so every
    approach to a port is horizontal and the last corner before it is an
    attachment turn standing on the corner.  The grid realiser charges
    :func:`~flab2bp.sfy.layout.corridors.attachment_turn_tight`'s ``cost``:
    ``reach + lead_in``, not the manifold's soft attachment box.  The apron is
    that length in whole grid steps, not an inclusive node count: its reserved
    line contains one more node than this value. Both figures come from
    ``registry.json`` through :class:`~flab2bp.sfy.layout.corridors.Measures`.
    """
    return math.ceil(attachment_turn_tight(measures).cost / measures.grid)


@dataclass(frozen=True, slots=True)
class Feedback:
    """What the router learned about one arrangement, priced for the next one.

    ``failed_nets`` is net id to weight: a net with no tree is worth
    ``1 + weight`` of an ordinary net's distance, so the next arrangement pulls
    its terminals together first.  ``hot_nodes`` is the router's blame, summed
    over levels at each ground position.  The heaviest :data:`HOT_NODES`
    positions are kept clear of machines for the next routing attempt.

    Failed-net weights remain active if no arrangement can honour the hot
    positions; :func:`pack` then retries without the keep-outs.
    """

    failed_nets: Mapping[int, float]
    hot_nodes: Mapping[Node, float]

    def decayed(self) -> Feedback:
        """This evidence one arrangement older, faded by :data:`DECAY`.

        The decay lives here rather than in the caller so that the two halves --
        weights and hot nodes -- cannot fade at different rates.  Task 10 calls
        it once per arrangement boundary, before folding in what the round it
        just ran learned.
        """
        return Feedback(
            failed_nets={net: weight * DECAY for net, weight in self.failed_nets.items()},
            hot_nodes={node: weight * DECAY for node, weight in self.hot_nodes.items()},
        )


@dataclass(frozen=True, slots=True)
class Pack:
    """One arrangement of machines, and what the solver made of it.

    ``status`` is CP-SAT's own status name: ``OPTIMAL`` where the model was
    solved to proof and ``FEASIBLE`` where the clock stopped it holding an
    incumbent.  ``objective`` is the model's own objective value, in the scaled
    integer units the model minimises (:data:`_WEIGHT_SCALE` per node of belt),
    so it compares two packs of the SAME spec and nothing else.
    """

    machines: tuple[MachineObj, ...]
    objective: float
    status: str


class PackError(ValueError):
    """A spec this module will not stand, named by the refusal it earns.

    ``cause`` is shaped like
    :class:`~flab2bp.sfy.layout.corridors.CorridorError`'s, but it carries the
    refusal string itself rather than a discriminator the strategy translates,
    because a packer refusal has only one reading.  A cause that is not in
    :data:`~flab2bp.sfy.layout.refusals.REFUSALS` is refused here, at the moment
    it is used, rather than reaching a caller as a cause nobody declared.
    """

    def __init__(self, cause: str, detail: str) -> None:
        if cause not in REFUSALS:
            raise ValueError(
                f"{cause!r} is not one of the refusals in flab2bp.sfy.layout.refusals, "
                "and a strategy cannot name a cause that is not in the table"
            )
        super().__init__(detail)
        self.cause = cause
        self.detail = detail


# --- what one buildable takes, at one yaw ----------------------------------


@dataclass(frozen=True, slots=True)
class _Apron:
    """One belt port's straight run, as node offsets from its machine's node.

    ``node`` is where the port's own lattice node stands relative to the
    machine's, and ``step`` is the way it faces, one node at a time.  Both come
    out of :func:`~flab2bp.sfy.layout.grid_nets.terminal_for` applied to a probe
    machine, so the run the packer reserves is the one the router really uses:
    a port's node is not derivable from its translation alone (a port half a
    grid step out reaches to the NEXT line along its own normal), and that rule
    is stated once, there.
    """

    name: str
    node: tuple[int, int]
    step: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Shape:
    """One buildable at one yaw: the ground it denies, and where its belts leave.

    Two rectangles about the machine's own node, in whole centimetres and
    rounded OUTWARD, which is stricter and never laxer.

    ``box`` is what two machines keep out of each other: the hard footprint
    turned by the yaw and inflated by HALF a grid step on every side, so that
    two boxes which do not lap put their hard boxes one WHOLE step apart -- R7,
    which is ours, and the same one step
    :func:`~flab2bp.sfy.layout.manifold.machine_pitch_cm` leaves between two
    machines of a manifold row.  (Inflating by a whole step on each side would
    reserve two, which is a rule nobody stated.)

    ``keepout`` is what a belt keeps out of, which is a different and larger
    question: a belt carries
    :data:`~flab2bp.sfy.layout.validate.BELT_CLEARANCE_HALF_WIDTH_CM` of its own
    clearance to either side of its centreline, so a node closer than that to a
    hard box is a node
    :meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` already denies.  Reserving
    an apron nearer than that would reserve nodes the router cannot use, so the
    apron is held off this rectangle rather than off ``box``.
    """

    box: tuple[int, int, int, int]
    keepout: tuple[int, int, int, int]
    aprons: tuple[_Apron, ...]


def _turned(
    box: tuple[float, float, float, float], yaw: float
) -> tuple[float, float, float, float]:
    """``box`` rotated about ``+Z`` by ``yaw``, which is exact for a quarter turn.

    A quarter turn maps an axis-aligned rectangle onto an axis-aligned
    rectangle, so nothing is lost here that
    :func:`~flab2bp.sfy.geometry.box_bounds` would keep -- the same reading
    :mod:`flab2bp.sfy.layout.lattice`'s note 6 states for a clearance box.
    """
    x0, y0, x1, y1 = box
    if yaw == 0.0:
        return (x0, y0, x1, y1)
    if yaw == 90.0:
        return (-y1, x0, -y0, x1)
    if yaw == 180.0:
        return (-x1, -y1, -x0, -y0)
    return (y0, -x1, y1, -x0)


def _buildable(registry: Registry, class_name: str) -> Buildable:
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        raise PackError(GAME_DATA, f"the registry does not describe {class_name}")
    return buildable


def _shapes(
    class_name: str, lattice: Lattice, registry: Registry, stand_cm: float
) -> tuple[_Shape, ...]:
    """What ``class_name`` takes and offers at each of the four yaws.

    The port offsets are read off a PROBE: the same buildable stood on a node in
    the middle of this lattice and passed through
    :func:`~flab2bp.sfy.layout.grid_nets.terminal_for`.  A machine's node is the
    only thing a pose changes about a port's node -- the offset between the two
    is the same wherever the machine stands -- so one probe per yaw answers for
    every machine of that class, and it answers with the ROUTER's own rule
    rather than with a second reading of the same port.
    """
    buildable = _buildable(registry, class_name)
    try:
        footprint = hard_footprint_cm(buildable)
    except RowError as exc:
        raise PackError(GAME_DATA, str(exc)) from exc
    grid = lattice.grid_cm
    _whole_grid(grid)
    # Half a step per side, so that two boxes which do not lap put their HARD
    # boxes one whole step apart.  A belt keeps its own clearance instead.
    half_step = grid / 2.0
    belt = BELT_CLEARANCE_HALF_WIDTH_CM
    centre = lattice.n // 2
    here = lattice.world((centre, centre, 0))
    ports = sorted(
        (port for port in buildable.ports if port.kind == "belt"), key=lambda port: port.name
    )
    shapes: list[_Shape] = []
    for yaw in _YAWS:
        turned = _turned(footprint, yaw)
        probe = MachineObj(0, class_name, Pose(here[0], here[1], stand_cm, yaw), "")
        aprons: list[_Apron] = []
        for port in ports:
            terminal = terminal_for(probe, port, lattice, registry)
            if terminal.node[2] != GROUND_LEVEL:
                raise PackError(
                    GAME_DATA,
                    f"{class_name}'s {port.name} stands at level {terminal.node[2]} with the "
                    f"machine on the slab, and R-M3-3 puts a grid-snapped machine's belt "
                    f"ports at level {GROUND_LEVEL}",
                )
            aprons.append(
                _Apron(
                    name=port.name,
                    node=(terminal.node[0] - centre, terminal.node[1] - centre),
                    step=(round(terminal.facing[0]), round(terminal.facing[1])),
                )
            )
        shapes.append(
            _Shape(
                box=_grown(turned, half_step),
                keepout=_grown(turned, belt),
                aprons=tuple(aprons),
            )
        )
    return tuple(shapes)


def _grown(box: tuple[float, float, float, float], by: float) -> tuple[int, int, int, int]:
    """``box`` reached out ``by`` on every side, in whole centimetres.

    Rounded OUTWARD, so the rectangle reserved is never smaller than the one
    asked for: on the shipped registry every edge is already whole, and on one
    that is not, the reservation errs towards the strict side.
    """
    return (
        math.floor(box[0] - by),
        math.floor(box[1] - by),
        math.ceil(box[2] + by),
        math.ceil(box[3] + by),
    )


def _whole_grid(grid_cm: float) -> int:
    """The grid step in whole centimetres, or a refusal to pretend it is one.

    A refusal in the table rather than a bare ``ValueError``: what it says is
    that the game data states a grid this packer cannot lay a box on, which is
    the same answer as a machine the game data does not describe, so it leaves
    by the same door.
    """
    step = round(grid_cm)
    if abs(grid_cm - step) > 0.0:
        raise PackError(
            GAME_DATA,
            f"the hologram grid is {grid_cm} cm, which is not a whole number of centimetres; "
            "this packer states every box in whole centimetres and cannot round the grid",
        )
    return step


# --- which machine is which, and what it has to be near --------------------


@dataclass(frozen=True, slots=True)
class _Stand:
    """One machine the spec runs: which group it belongs to, and where in it."""

    group: SfyMachineGroup
    index: int


def _stands(spec: SfyBuildSpec) -> tuple[_Stand, ...]:
    """Every machine the spec runs, group by group in the spec's own order.

    THE flat machine list :func:`~flab2bp.sfy.layout.grid_nets.net_plan`'s
    docstring names: the spec's groups in spec order and each group's machines
    in index order, so group ``g``'s machine ``i`` is at ``sum(count of every
    earlier group) + i``.  A plan's ports are indices into this list, and
    :attr:`Pack.machines` comes back in the same order, so nothing between the
    packer and the router has to map one onto the other.
    """
    return tuple(
        _Stand(group=group, index=index) for group in spec.groups for index in range(group.count)
    )


# --- the model -------------------------------------------------------------


@dataclass(slots=True)
class _Build:
    """One CP-SAT model of one spec, and the handles a solution is read through."""

    model: cp_model.CpModel
    xs: list[cp_model.IntVar]
    ys: list[cp_model.IntVar]
    yaws: list[list[cp_model.IntVar]]


def _build(
    spec: SfyBuildSpec,
    registry: Registry,
    lattice: Lattice,
    measures: Measures,
    stands: Sequence[_Stand],
    feedback: Feedback | None,
) -> _Build:
    """The whole model: where a machine may stand, and what an arrangement costs."""
    grid = _whole_grid(lattice.grid_cm)
    lines = lattice.open_lines
    low, high = grid * lines.start, grid * (lines.stop - 1)
    apron = port_apron_nodes(measures)
    stand_cm = slab_top_cm(registry)
    shapes = {
        stand.group.machine_class: _shapes(stand.group.machine_class, lattice, registry, stand_cm)
        for stand in stands
    }
    # A variable-width/height rectangle relaxes away their yaw correlation.
    # Keep the invariant area bound explicitly: even the smallest allowed
    # orientation of every machine must fit inside the same open-floor domain.
    # Aprons are not counted; their reservations may legally overlap.
    minimum_areas = {
        class_name: min(
            (shape.box[2] - shape.box[0]) * (shape.box[3] - shape.box[1]) for shape in turns
        )
        for class_name, turns in shapes.items()
    }
    occupied = sum(minimum_areas[stand.group.machine_class] for stand in stands)
    available = (high - low) ** 2
    if occupied > available:
        raise PackError(
            NO_ARRANGEMENT,
            f"minimum occupied rectangle area exceeds the open-floor packing domain: "
            f"{occupied} cm2 > {available} cm2 for {len(stands)} machines "
            f"(hard footprints plus the packer's one-step separation)",
        )
    _check_scaled_area(stands, shapes, high - low)

    model = cp_model.CpModel()
    xs: list[cp_model.IntVar] = []
    ys: list[cp_model.IntVar] = []
    yaws: list[list[cp_model.IntVar]] = []
    x_boxes: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    y_boxes: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    keep_x: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    keep_y: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    x_intervals: list[cp_model.IntervalVar] = []
    y_intervals: list[cp_model.IntervalVar] = []
    for machine, stand in enumerate(stands):
        turns = shapes[stand.group.machine_class]
        x = model.new_int_var(0, lattice.n, f"x{machine}")
        y = model.new_int_var(0, lattice.n, f"y{machine}")
        literals = [model.new_bool_var(f"yaw{machine}_{int(yaw)}") for yaw in _YAWS]
        model.add_exactly_one(literals)
        edges = []
        keeps = []
        spans = []
        for axis, (node, name) in enumerate(((x, "fx"), (y, "fy"))):
            # The box carries the wall margin, so its own domain states it: an
            # inflated footprint lies inside ``open_lines``.  The keepout is a
            # belt's clearance about the same footprint, which reaches further,
            # and is a RELATIVE reservation -- what it must miss is an apron, not
            # a wall -- so its domain is only as wide as its offsets make it.
            lo, hi = _edges(model, literals, node, turns, axis, "box", grid, low, high, machine)
            keeps.append(
                _edges(
                    model,
                    literals,
                    node,
                    turns,
                    axis,
                    "keepout",
                    grid,
                    min(shape.keepout[axis] for shape in turns),
                    grid * lattice.n + max(shape.keepout[axis + 2] for shape in turns),
                    machine,
                )
            )
            # The size is its own variable rather than ``hi - lo``: an interval's
            # size has to be affine in ONE variable, and a turned box is as wide
            # as the yaw says.
            side = model.new_int_var(
                min(shape.box[axis + 2] - shape.box[axis] for shape in turns),
                max(shape.box[axis + 2] - shape.box[axis] for shape in turns),
                f"{name}w_{machine}",
            )
            for literal, shape in zip(literals, turns, strict=True):
                model.add(side == shape.box[axis + 2] - shape.box[axis]).only_enforce_if(literal)
            edges.append((lo, hi))
            spans.append(side)
        x_boxes.append(edges[0])
        y_boxes.append(edges[1])
        keep_x.append(keeps[0])
        keep_y.append(keeps[1])
        x_intervals.append(
            model.new_interval_var(edges[0][0], spans[0], edges[0][1], f"xi{machine}")
        )
        y_intervals.append(
            model.new_interval_var(edges[1][0], spans[1], edges[1][1], f"yi{machine}")
        )
        xs.append(x)
        ys.append(y)
        yaws.append(literals)
    model.add_no_overlap_2d(x_intervals, y_intervals)

    nets = net_plan(spec, registry)
    apron_x, apron_y = _add_aprons(
        model,
        stands,
        shapes,
        xs,
        ys,
        yaws,
        keep_x,
        keep_y,
        grid=grid,
        low=low,
        high=high,
        apron=apron,
    )
    _break_symmetry(model, spec, stands, xs, ys, lattice.n)
    _anchor(
        model,
        [*x_boxes, *apron_x],
        [*y_boxes, *apron_y],
        grid=grid,
        low=low,
        high=high,
        hot=bool(feedback and feedback.hot_nodes),
        walled=any(not net.sources or not net.sinks for net in nets),
    )
    _hot_keepouts(model, lattice, stands, keep_x, keep_y, feedback)
    _add_objective(model, nets, registry, lattice, stands, shapes, xs, ys, yaws, feedback)
    return _Build(model=model, xs=xs, ys=ys, yaws=yaws)


def _check_scaled_area(
    stands: Sequence[_Stand], shapes: Mapping[str, tuple[_Shape, ...]], side: int
) -> None:
    """Refute only exact conservative-scale obstructions to the same square.

    For each axis, every chain of disjoint original intervals fits in ``side``.
    An unbounded integer knapsack over ALL allowed side lengths therefore bounds
    its transformed length by ``capacity``.  Longest-path compaction preserves
    those separating orders in a transformed ``capacity`` square.  Every pair
    still separates on at least one axis, so its total transformed area cannot
    exceed ``capacity ** 2``.  Taking each machine's minimum over complete yaw
    products can only weaken that necessary bound, never invent an obstruction.

    The three floor scales 1/2, 2/3, 3/4 are ours: a small proof search, not game
    dimensions or a claim that failing to find a certificate implies feasibility.
    The 3/4 scale proves the measured modular-frame case that ordinary area misses.
    Each costs O(distinct lengths * side / gcd(lengths)) time and O(side / gcd)
    memory; zero weights are skipped.  Knapsack allows unused capacity and
    unlimited multiplicities, both relaxations of the actual machine inventory.
    """
    dimensions = {
        name: tuple((shape.box[2] - shape.box[0], shape.box[3] - shape.box[1]) for shape in turns)
        for name, turns in shapes.items()
    }
    lengths = sorted({length for turns in dimensions.values() for pair in turns for length in pair})
    unit = math.gcd(*lengths)
    for numerator in (1, 2, 3):
        weights = {length: (length // unit) * numerator // (numerator + 1) for length in lengths}
        best = [0] * (side // unit + 1)
        for length, weight in weights.items():
            if weight == 0:
                continue
            step = length // unit
            for room in range(step, len(best)):
                best[room] = max(best[room], best[room - step] + weight)
        capacity = best[-1]
        if capacity == 0:
            continue
        areas = {
            name: min(weights[width] * weights[height] for width, height in turns)
            for name, turns in dimensions.items()
        }
        occupied = sum(areas[stand.group.machine_class] for stand in stands)
        if occupied > capacity * capacity:
            raise PackError(
                NO_ARRANGEMENT,
                f"minimum conservatively scaled rectangle area exceeds the open-floor "
                f"packing domain: {occupied} > {capacity} ** 2 at scale "
                f"{numerator}/{numerator + 1}, with exact side-chain capacity "
                f"for {side} cm (hard footprints plus the packer's one-step separation)",
            )


def _edges(
    model: cp_model.CpModel,
    literals: Sequence[cp_model.IntVar],
    node: cp_model.IntVar,
    turns: Sequence[_Shape],
    axis: int,
    which: str,
    grid: int,
    low: int,
    high: int,
    machine: int,
) -> tuple[cp_model.IntVar, cp_model.IntVar]:
    """One of a machine's rectangles on one axis, tied to its yaw.

    ``which`` names the field of :class:`_Shape` to read, so the two rectangles
    a machine reserves -- what another machine keeps out of, and what a belt
    keeps out of -- are built by one statement rather than two that could drift.
    """
    lo = model.new_int_var(low, high, f"{which}{axis}_{machine}_lo")
    hi = model.new_int_var(low, high, f"{which}{axis}_{machine}_hi")
    for literal, shape in zip(literals, turns, strict=True):
        box: tuple[int, int, int, int] = getattr(shape, which)
        model.add(lo == grid * node + box[axis]).only_enforce_if(literal)
        model.add(hi == grid * node + box[axis + 2]).only_enforce_if(literal)
    return lo, hi


def _anchor(
    model: cp_model.CpModel,
    x_boxes: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    y_boxes: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    *,
    grid: int,
    low: int,
    high: int,
    hot: bool,
    walled: bool,
) -> None:
    """Slide the whole arrangement against the wall on every axis it is free on.

    Machines move a node at a time, so translating one axis of a whole
    arrangement by one node leaves every no-overlap and every apron relation
    exactly as it was.  Where the OBJECTIVE cannot tell either, that translation
    is a symmetry -- a continuous one, and the most expensive kind for a solver
    to search through -- and requiring the leading box to lie within one grid
    step of the wall picks one arrangement out of each family without losing
    any.

    Which axes are free is read off the objective rather than assumed.  Blame
    history is a map of absolute nodes, so ANY hot node fixes both axes; the
    designer wall is R-M3-5's, which is the ``-Y`` and ``+Y`` walls and nothing
    on ``X``, so a build that belts something in or out is fixed along ``Y``
    alone.
    """
    for boxes, free in ((x_boxes, not hot), (y_boxes, not hot and not walled)):
        if not free:
            continue
        least = model.new_int_var(low, high, "")
        model.add_min_equality(least, [lo for lo, _ in boxes])
        model.add(least <= low + grid - 1)


def _add_aprons(
    model: cp_model.CpModel,
    stands: Sequence[_Stand],
    shapes: Mapping[str, tuple[_Shape, ...]],
    xs: Sequence[cp_model.IntVar],
    ys: Sequence[cp_model.IntVar],
    yaws: Sequence[Sequence[cp_model.IntVar]],
    keep_x: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    keep_y: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    *,
    grid: int,
    low: int,
    high: int,
    apron: int,
) -> tuple[
    list[tuple[cp_model.IntVar, cp_model.IntVar]],
    list[tuple[cp_model.IntVar, cp_model.IntVar]],
]:
    """Keep every belt port's straight run clear of every OTHER machine.

    Not one :meth:`add_no_overlap_2d` over aprons and machines together: that
    would also hold an apron off its own machine, which the port sits inside,
    and off every other apron, which belts are entitled to share.  What is asked
    for is exactly the pairs -- this apron against that machine -- so each pair
    is one disjunction of the four ways two rectangles can miss each other.

    The rectangle on the other side is ``keepout``, the machine's hard box grown
    by a belt's own clearance half width, and NOT the box two machines keep out
    of each other by: what an apron reserves is somewhere a belt centreline can
    really stand, and
    :meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` denies any node whose belt
    box meets a hard box.  Touching is allowed, so an apron line that ENDS on the
    keepout edge is a belt exactly its own clearance from the machine -- which is
    the boundary ``_lines_meeting`` leaves open.

    The apron rectangles come back so that :func:`_anchor` can slide the whole
    arrangement against the wall: an apron reaches further out than its own
    machine's box wherever the port stands proud of it (a Manufacturer's output
    apron runs a metre past its inflated box), so an anchor that only knew about
    boxes could ask for a translation that pushes an apron through the wall.
    """
    reach_x: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    reach_y: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    for machine, stand in enumerate(stands):
        turns = shapes[stand.group.machine_class]
        # One rectangle per PORT, not per port per yaw: the ports come off every
        # turn of one buildable in the same order, so the yaw moves the same
        # rectangle rather than offering a second one -- and a rectangle nothing
        # is standing at is a rectangle the solver would still have to keep out
        # of everyone's way.
        for index in range(len(turns[0].aprons)):
            edges = []
            for axis, node in enumerate((xs[machine], ys[machine])):
                lo = model.new_int_var(low, high, f"a{axis}_{machine}_{index}_lo")
                hi = model.new_int_var(low, high, f"a{axis}_{machine}_{index}_hi")
                for literal, shape in zip(yaws[machine], turns, strict=True):
                    port = shape.aprons[index]
                    run = apron * port.step[axis]
                    model.add(
                        lo == grid * node + grid * (port.node[axis] + min(run, 0))
                    ).only_enforce_if(literal)
                    model.add(
                        hi == grid * node + grid * (port.node[axis] + max(run, 0))
                    ).only_enforce_if(literal)
                edges.append((lo, hi))
            reach_x.append(edges[0])
            reach_y.append(edges[1])
            for other in range(len(stands)):
                if other == machine:
                    continue  # a port sits INSIDE its own machine's box
                _keep_apart(model, edges[0], edges[1], keep_x[other], keep_y[other])
    return reach_x, reach_y


def _keep_apart(
    model: cp_model.CpModel,
    apron_x: tuple[cp_model.LinearExprT, cp_model.LinearExprT],
    apron_y: tuple[cp_model.LinearExprT, cp_model.LinearExprT],
    box_x: tuple[cp_model.IntVar, cp_model.IntVar],
    box_y: tuple[cp_model.IntVar, cp_model.IntVar],
) -> None:
    """Two rectangles miss each other on at least one side.

    The first may be degenerate and may be a pair of constants: an apron is a
    node LINE and a blamed node is a single node, and both are held off a
    machine by the same four ways two rectangles can miss.
    """
    sides = [model.new_bool_var("") for _ in range(4)]
    model.add(box_x[1] <= apron_x[0]).only_enforce_if(sides[0])
    model.add(apron_x[1] <= box_x[0]).only_enforce_if(sides[1])
    model.add(box_y[1] <= apron_y[0]).only_enforce_if(sides[2])
    model.add(apron_y[1] <= box_y[0]).only_enforce_if(sides[3])
    model.add_bool_or(sides)


def _break_symmetry(
    model: cp_model.CpModel,
    spec: SfyBuildSpec,
    stands: Sequence[_Stand],
    xs: Sequence[cp_model.IntVar],
    ys: Sequence[cp_model.IntVar],
    n: int,
) -> None:
    """Order the machines of a group that are interchangeable, and only those.

    Two machines of one group stand in the same nets in the same role, so any
    arrangement has an equal one with them in node order and fixing that order
    costs nothing.  The exception is a direct pair's CONSUMER group: its
    machines are tied one for one to the producers' (machine ``i`` eats what
    machine ``i`` makes), so only the two groups permuted TOGETHER is a
    symmetry, and ordering the producers alone already picks one of each orbit.
    """
    consumers = {id(pair.consumer) for pair in direct_pairs(spec)}
    for machine in range(1, len(stands)):
        here, before = stands[machine], stands[machine - 1]
        if here.index == 0 or id(here.group) in consumers:
            continue
        if here.group is not before.group:
            continue
        model.add(
            xs[machine - 1] * (n + 1) + ys[machine - 1] <= xs[machine] * (n + 1) + ys[machine]
        )


def _add_objective(
    model: cp_model.CpModel,
    nets: Sequence[NetPlan],
    registry: Registry,
    lattice: Lattice,
    stands: Sequence[_Stand],
    shapes: Mapping[str, tuple[_Shape, ...]],
    xs: Sequence[cp_model.IntVar],
    ys: Sequence[cp_model.IntVar],
    yaws: Sequence[Sequence[cp_model.IntVar]],
    feedback: Feedback | None,
) -> None:
    """What an arrangement costs in belt to run.

    Two sums, in the same scaled units and minimised together:

    * every net's Manhattan distance, in nodes, from the centroid of its
      sources' port nodes to each of its sinks', weighted ``1 + failed``;
    * every direct pair's distance away from ONE shortest legal belt, at twice
      that weight -- the smallest multiple that makes the pair's own total
      strictly fall towards that distance from below and strictly rise past it,
      so the minimum is exactly at a shortest belt rather than anywhere under
      it.

    A net with no machine source is fed from the ``-Y`` wall and one with no
    machine sink drains to the ``+Y`` wall (R-M3-5), and a wall is a whole LINE
    of terminals the router picks one of.  So a wall side is priced at the
    NEAREST point of its line -- the across-wall distance alone, with nothing
    along it -- which is the real Manhattan distance to the nearest wall
    terminal rather than to a node chosen for it.  Where a plan has machines on
    both sides the wall is not priced at all: the plan carries machine ports
    only, and an item that is both made here and belted in is one the router
    will feed from the machines it has.
    """
    failed: Mapping[int, float] = {} if feedback is None else feedback.failed_nets
    nodes = _PortNodes(model, stands, shapes, xs, ys, yaws, lattice.n)
    shortest = math.ceil(shortest_belt_cm(registry.limits) / lattice.grid_cm)
    lines = lattice.open_lines
    terms: list[cp_model.LinearExpr] = []
    for net in nets:
        weight = round(_WEIGHT_SCALE * (1.0 + failed.get(net.id, 0.0)))
        centre = _centroid(model, nodes, net, lattice.n)
        for sink in net.sinks:
            into = nodes.of(sink)
            if centre is None:
                terms.append(weight * _span(model, into[1], lines.start, lattice.n))
                continue
            span = _span(model, centre[0] - into[0], 0, lattice.n) + _span(
                model, centre[1] - into[1], 0, lattice.n
            )
            terms.append(weight * span)
            if net.paired:
                terms.append(2 * weight * _span(model, span - shortest, 0, 2 * lattice.n))
        if centre is not None and not net.sinks:
            terms.append(weight * _span(model, centre[1], lines.stop - 1, lattice.n))
    model.minimize(sum(terms))


class _PortNodes:
    """Which node each machine's port stands on, made once per port that is asked for.

    A port's node is its machine's node plus an offset that depends only on the
    yaw, so one pair of variables per port serves every net that port is in.
    """

    def __init__(
        self,
        model: cp_model.CpModel,
        stands: Sequence[_Stand],
        shapes: Mapping[str, tuple[_Shape, ...]],
        xs: Sequence[cp_model.IntVar],
        ys: Sequence[cp_model.IntVar],
        yaws: Sequence[Sequence[cp_model.IntVar]],
        n: int,
    ) -> None:
        self._model = model
        self._stands = stands
        self._shapes = shapes
        self._xs = xs
        self._ys = ys
        self._yaws = yaws
        self._n = n
        self._made: dict[tuple[int, str], tuple[cp_model.IntVar, cp_model.IntVar]] = {}

    def of(self, port: tuple[int, str]) -> tuple[cp_model.IntVar, cp_model.IntVar]:
        """One :class:`~flab2bp.sfy.layout.grid_nets.NetPlan` port's node pair.

        ``port`` is the plan's own ``(machine index, port name)``, and the index
        is into :func:`_stands` -- the flat machine list ``net_plan`` documents.
        """
        made = self._made.get(port)
        if made is not None:
            return made
        machine, name = port
        turns = self._shapes[self._stands[machine].group.machine_class]
        node = (
            self._model.new_int_var(0, self._n, f"px{machine}_{name}"),
            self._model.new_int_var(0, self._n, f"py{machine}_{name}"),
        )
        centres = (self._xs[machine], self._ys[machine])
        for literal, shape in zip(self._yaws[machine], turns, strict=True):
            apron = next(one for one in shape.aprons if one.name == name)
            for axis in range(2):
                self._model.add(node[axis] == centres[axis] + apron.node[axis]).only_enforce_if(
                    literal
                )
        self._made[port] = node
        return node


def _centroid(
    model: cp_model.CpModel, nodes: _PortNodes, net: NetPlan, n: int
) -> tuple[cp_model.IntVar, cp_model.IntVar] | None:
    """Where a net comes from: the floor of the mean of its sources' port nodes.

    ``None`` where the net has no machine source at all, which is the ``-Y``
    wall: the wall is a whole LINE of terminals the router picks one of
    (R-M3-5), so it has no node to take a mean with and the sinks are priced
    against the line instead.

    The floor rather than a scaled exact mean: a mean of three nodes is not a
    node, and multiplying the whole term by the source count instead would make
    a three-source net worth three times a one-source net for no reason but its
    arithmetic.
    """
    if not net.sources:
        return None
    ports = [nodes.of(source) for source in net.sources]
    if len(ports) == 1:
        return ports[0]
    centre = (
        model.new_int_var(0, n, f"cx{net.id}"),
        model.new_int_var(0, n, f"cy{net.id}"),
    )
    for axis in range(2):
        total = sum(port[axis] for port in ports)
        model.add(len(ports) * centre[axis] <= total)
        model.add(total <= len(ports) * centre[axis] + len(ports) - 1)
    return centre


def _span(
    model: cp_model.CpModel, expr: cp_model.LinearExpr | cp_model.IntVar, away: int, bound: int
) -> cp_model.IntVar:
    """``|expr - away|`` as a variable, which is what makes a distance linear."""
    out = model.new_int_var(0, bound, "")
    model.add_abs_equality(out, expr - away)
    return out


HOT_NODES: Final = 8
"""How many of the router's blamed nodes one arrangement is held off.

**Ours**, a model-size bound rather than a game fact.  The router may blame any
number of nodes; only the eight heaviest ground positions become constraints.
"""


def _hot_floor(feedback: Feedback | None) -> tuple[tuple[tuple[int, int], float], ...]:
    """The hottest distinct ground positions, summing blame from all levels."""
    if feedback is None:
        return ()
    floor: dict[tuple[int, int], float] = {}
    for (i, j, _level), weight in feedback.hot_nodes.items():
        floor[i, j] = floor.get((i, j), 0.0) + weight
    return tuple(nsmallest(HOT_NODES, floor.items(), key=lambda pair: (-pair[1], pair[0])))


def _hot_keepouts(
    model: cp_model.CpModel,
    lattice: Lattice,
    stands: Sequence[_Stand],
    keep_x: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    keep_y: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    feedback: Feedback | None,
) -> None:
    """Leave the worst of the floor the router blamed open for it to use.

    The :data:`HOT_NODES` heaviest blamed nodes, each held out of every
    machine's belt keepout.  "Out of the keepout" rather than "out of the
    footprint" is the whole point: what the router wants back is a node a belt
    can really stand on, and
    :meth:`~flab2bp.sfy.layout.lattice.Occupancy.free` denies a node its belt box
    cannot clear a hard box at.  A machine merely standing beside a blamed node
    leaves it as unusable as one standing on it.

    Each fixed node is tested against the machine's belt-inflated rectangle:
    this reserves a usable belt centreline, not a square of guessed game
    clearance.  The four-way disjunction is constant-size per machine and hot
    position, with no ``(n + 1) ** 2`` element table to expand during presolve.

    The weights select the nodes but do not price them.  These constraints may
    be infeasible even when the machines fit; :func:`pack` retries with only
    failed-net weights if that is proved.
    """
    if feedback is None or not feedback.hot_nodes:
        return
    grid = _whole_grid(lattice.grid_cm)
    for node, _weight in _hot_floor(feedback):
        at = (grid * node[0], grid * node[1])
        for machine in range(len(stands)):
            _keep_apart(model, (at[0], at[0]), (at[1], at[1]), keep_x[machine], keep_y[machine])


# --- the pack --------------------------------------------------------------


def pack(
    spec: SfyBuildSpec,
    designer: Designer,
    registry: Registry,
    lattice: Lattice,
    *,
    feedback: Feedback | None,
    deadline: float | None,
    workers: int,
    seed: int,
    measures: Measures | None = None,
) -> Pack:
    """Stand every machine the spec runs on the hologram grid, or refuse.

    ``deadline`` is a caller-started monotonic wall, the way every other stage
    of this project reads one.  A solve that reaches it holding a feasible
    arrangement returns that arrangement -- an unproven pack the router can try
    beats a refusal, and :attr:`Pack.status` says which it is; a solve that
    reaches it holding nothing refuses with :data:`OVER_BUDGET`, and a spec
    proved not to fit refuses with :data:`NO_ARRANGEMENT`.

    ``feedback`` is what the last arrangement's routing learned, already decayed
    by the caller (:meth:`Feedback.decayed`).  ``None`` is the first arrangement,
    which is packed on the geometry alone.

    ``measures`` is the one place a limit is read, HANDED DOWN: a strategy reads
    it once at the top of a run and passes the same one to every stage, which is
    what keeps two stages from being laid out against different numbers (see
    :class:`~flab2bp.sfy.layout.corridors.Measures`).  ``None`` reads it here
    from ``designer`` and ``registry`` instead, which is for a caller that has
    no run around it -- a test, or a packer asked for one arrangement on its
    own.  Passing one is what a strategy does; the packer is not the place the
    limits are read twice.
    """
    stands = _stands(spec)
    if not stands:
        raise PackError(NO_ARRANGEMENT, "the spec runs no machines, so there is nothing to stand")
    read = _measure(registry, designer) if measures is None else measures
    build = _build(spec, registry, lattice, read, stands, feedback)

    solver = cp_model.CpSolver()
    # Never zero: ortools reads ``num_workers == 0`` as ALL CORES.
    solver.parameters.num_workers = max(workers, 1)
    solver.parameters.random_seed = seed % _SEED_MODULUS
    solver.parameters.max_deterministic_time = max(
        _DETERMINISTIC_TIME_FLOOR, _DETERMINISTIC_TIME_PER_MACHINE * len(stands)
    )
    _wall(solver, deadline, stands)
    status = solver.solve(build.model)
    if status == cp_model.INFEASIBLE and feedback is not None and feedback.hot_nodes:
        # Blame is evidence, not a proof the machines cannot fit.  Drop only the
        # keep-outs after INFEASIBLE; keep failed-net weights and the original
        # deadline.  UNKNOWN is a budget result, never permission to ignore blame.
        build = _build(spec, registry, lattice, read, stands, Feedback(feedback.failed_nets, {}))
        _wall(solver, deadline, stands)
        status = solver.solve(build.model)
    name = solver.status_name(status)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Pack(
            machines=_placed(build, solver, lattice, registry, stands),
            objective=solver.objective_value,
            status=name,
        )
    if status == cp_model.INFEASIBLE:
        raise PackError(
            NO_ARRANGEMENT,
            f"no arrangement of the {len(stands)} machines this spec runs stands inside the "
            f"{designer.mark} designer with a grid step between their hard boxes",
        )
    if status == cp_model.MODEL_INVALID:
        raise ValueError(f"the packer built a model CP-SAT will not read: {build.model.validate()}")
    raise PackError(
        OVER_BUDGET,
        f"the packer reached its budget with no arrangement at all ({name}) for the "
        f"{len(stands)} machines this spec runs",
    )


def _wall(solver: cp_model.CpSolver, deadline: float | None, stands: Sequence[_Stand]) -> None:
    """Give ``solver`` what is left of the caller's wall, or refuse to start.

    Read afresh at every solve rather than once, so that the retry a build with
    unsatisfiable blame needs cannot spend the wall twice.
    """
    if deadline is None:
        return
    left = deadline - time.monotonic()
    if left <= 0.0:
        raise PackError(
            OVER_BUDGET,
            f"the deadline passed {-left:.3f} s before the packer could search for an "
            f"arrangement of the {len(stands)} machines this spec runs",
        )
    solver.parameters.max_time_in_seconds = left


def _placed(
    build: _Build,
    solver: cp_model.CpSolver,
    lattice: Lattice,
    registry: Registry,
    stands: Sequence[_Stand],
) -> tuple[MachineObj, ...]:
    """The solution as machines: one per stand, in the order the nets expect them.

    The clock is the group's own, and the LAST machine of a group runs at
    ``last_clock`` -- the spec states two clocks per group and the odd machine
    at the end of a row absorbs the remainder, exactly as a manifold row does.
    """
    stand_cm = slab_top_cm(registry)
    ids = count(1)
    machines: list[MachineObj] = []
    for machine, stand in enumerate(stands):
        node = (solver.value(build.xs[machine]), solver.value(build.ys[machine]), 0)
        here = lattice.world(node)
        yaw = next(
            yaw
            for yaw, literal in zip(_YAWS, build.yaws[machine], strict=True)
            if solver.value(literal)
        )
        group = stand.group
        machines.append(
            MachineObj(
                id=next(ids),
                class_name=group.machine_class,
                pose=Pose(here[0], here[1], stand_cm, yaw),
                recipe_class=group.recipe_class,
                clock=group.clock if stand.index < group.count - 1 else group.last_clock,
                somersloops=group.somersloops,
            )
        )
    return tuple(machines)
