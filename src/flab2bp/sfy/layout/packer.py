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

**One grid step between two hard boxes, and it is OURS** (R7).  ``buildable.clearance``
is ``partial`` -- ``AFGHologram::TestClearanceOverlap`` was never read -- so the
gap two holograms really need is unknown and this project keeps its own: every
footprint enters the no-overlap inflated by one grid step on every side, which
is the same step :func:`~flab2bp.sfy.layout.manifold.machine_pitch_cm` puts
between two machines of a manifold row.  The margin against the designer wall is
the same statement: an inflated footprint, and every apron, lies inside
:attr:`~flab2bp.sfy.layout.lattice.Lattice.open_lines`.

**The apron is DERIVED** -- see :func:`port_apron_nodes`.  A lift can never land
on a port node (its column would run through the machine it serves), so every
approach to a port is horizontal and the last corner before it is an attachment
turn, which needs that turn's ``box + lead_in`` of straight run beside it.  An
apron is that run in whole nodes: ``apron_nodes`` nodes along the port's facing,
one node wide, tied to the yaw.  It is kept clear of every OTHER machine's
inflated footprint and of nothing else -- an apron laps its own machine's box,
because the port sits inside it, and two machines' aprons may lap each other,
because belts share space by level and only footprints are exclusive.

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
priced as objective terms rather than as constraints, so that a second
arrangement is drawn towards a floor the router has already been over.

**The net ids are the router's.**  :func:`~flab2bp.sfy.layout.grid_nets.nets_for`
numbers nets from 1, the direct pairs first (one per machine pair, in
:func:`~flab2bp.sfy.spec.direct_pairs` order) and then one per remaining item in
sorted order -- consuming an id even for an item that turns out to have nowhere
to come from or go.  :func:`_plan_nets` mirrors that numbering exactly, because
``nets_for`` needs placed machines and the packer has none yet, and the two
would be useless to each other if a weight fed back under net 3 landed on a
different net.  What keeps the mirror honest is the machine ORDER: this module
emits machines group by group in ``spec.groups`` order, which is the order
``nets_for``'s own ``_machines_by_group`` hands them out in.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import count
from typing import Final

from ortools.sat.python import cp_model

from flab2bp.sfy.layout.corridors import Measures, attachment_turn
from flab2bp.sfy.layout.grid_nets import terminal_for
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

MORE_ITEMS_THAN_PORTS: Final = "more input items than the machine has belt ports"
"""The same refusal :func:`~flab2bp.sfy.layout.grid_nets.nets_for` raises for a
recipe that moves more items than its machine has belt ports of that direction.
It is reached here first, because the packer assigns the same ports."""

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

Presolve is charged to the same clock, and the hot-node tables make it the
expensive part: one machine with a blame history over half the designer spends
0.8 units before the search starts.  A per-machine budget alone would hand that
model less than its presolve and get ``UNKNOWN`` back from a problem with one
rectangle in it.
"""

_SEED_MODULUS: Final = 2**31 - 1
"""``random_seed`` is an int32 field, so a caller's seed is taken modulo it."""


def port_apron_nodes(measures: Measures) -> int:
    """How much straight run one belt port keeps clear of other machines, in nodes.

    DERIVED, never written.  Task 7 measured that a lift can never land on a
    port node -- its column would run through the machine it serves -- so every
    approach to a port is horizontal and the last corner before it is an
    attachment turn standing on the corner.  What such a turn costs beside its
    own box is :func:`~flab2bp.sfy.layout.corridors.attachment_turn`'s ``cost``,
    which is ``measures.box + measures.lead_in``, and the apron is that length
    in whole grid steps.  Both figures come out of ``registry.json`` through
    :class:`~flab2bp.sfy.layout.corridors.Measures`.
    """
    return math.ceil(attachment_turn(measures).cost / measures.grid)


@dataclass(frozen=True, slots=True)
class Feedback:
    """What the router learned about one arrangement, priced for the next one.

    ``failed_nets`` is net id to weight: a net with no tree is worth
    ``1 + weight`` of an ordinary net's distance, so the next arrangement pulls
    its terminals together first.  ``hot_nodes`` is the router's blame projected
    onto the ground plane -- a node that turned a search away costs whatever
    machine stands on it.

    Both are EVIDENCE and neither is a constraint: the DSP packer proved that no
    cheap surrogate predicts routability, so a floor the router failed on is
    made expensive rather than illegal.
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

    ``box`` is the hard footprint turned by the yaw, inflated by one grid step
    on every side (R7) and rounded OUTWARD to whole centimetres, as offsets from
    the machine's own node.  ``inside`` is the same footprint UNINFLATED, in
    node offsets, which is the window the hot-node summed-area table reads.
    """

    box: tuple[int, int, int, int]
    inside: tuple[int, int, int, int]
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
    step = _whole_grid(grid)
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
                raise ValueError(
                    f"{class_name}'s {port.name} stands at level {terminal.node[2]} with the "
                    f"machine on the slab, and R-M3-3 puts a grid-snapped machine's belt "
                    f"ports at level {GROUND_LEVEL}"
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
                box=(
                    math.floor(turned[0]) - step,
                    math.floor(turned[1]) - step,
                    math.ceil(turned[2]) + step,
                    math.ceil(turned[3]) + step,
                ),
                inside=(
                    math.ceil(turned[0] / grid),
                    math.ceil(turned[1] / grid),
                    math.floor(turned[2] / grid),
                    math.floor(turned[3] / grid),
                ),
                aprons=tuple(aprons),
            )
        )
    return tuple(shapes)


def _whole_grid(grid_cm: float) -> int:
    """The grid step in whole centimetres, or a refusal to pretend it is one."""
    step = round(grid_cm)
    if abs(grid_cm - step) > 0.0:
        raise ValueError(
            f"the hologram grid is {grid_cm} cm, which is not a whole number of centimetres; "
            "this packer states every box in whole centimetres and cannot round the grid"
        )
    return step


# --- which machine is which, and what it has to be near --------------------


@dataclass(frozen=True, slots=True)
class _Stand:
    """One machine the spec runs: which group it belongs to, and where in it."""

    group: SfyMachineGroup
    index: int


@dataclass(frozen=True, slots=True)
class _Port:
    """One machine's one belt port, as the objective names it."""

    machine: int
    name: str


@dataclass(frozen=True, slots=True)
class _NetPlan:
    """One net of :func:`~flab2bp.sfy.layout.grid_nets.nets_for`, before placement.

    ``wall_in`` and ``wall_out`` are R-M3-5's designer wall: a whole line of
    terminals the router picks one of, so the objective prices only the distance
    ACROSS to that line rather than to any node on it.
    """

    id: int
    sources: tuple[_Port, ...]
    sinks: tuple[_Port, ...]
    wall_in: bool
    wall_out: bool
    direct: bool


def _stands(spec: SfyBuildSpec) -> tuple[_Stand, ...]:
    """Every machine the spec runs, group by group in the spec's own order.

    That order is what makes :func:`_plan_nets`' mirror of ``nets_for`` exact:
    ``_machines_by_group`` pools a placement by ``(recipe_class, machine_class)``
    and hands each group the next ``count`` of them in placement order, so a
    placement laid out group by group lines up index for index.
    """
    return tuple(
        _Stand(group=group, index=index) for group in spec.groups for index in range(group.count)
    )


def _port_name(
    registry: Registry, class_name: str, items: Sequence[str], item_id: str, direction: str
) -> str:
    """Which belt port of ``class_name`` carries ``item_id`` -- ``_terminal``'s rule.

    Mirrored rather than shared because
    :func:`~flab2bp.sfy.layout.grid_nets.nets_for` needs placed machines and the
    packer has none yet.  The rule itself is stated there: the recipe's items in
    that direction, in id order, take the machine's belt ports of that direction
    in name order.
    """
    buildable = _buildable(registry, class_name)
    ports = sorted(
        (port for port in buildable.ports if port.kind == "belt" and port.direction == direction),
        key=lambda port: port.name,
    )
    if len(items) > len(ports):
        raise PackError(
            MORE_ITEMS_THAN_PORTS,
            f"{class_name} has {len(ports)} belt {direction} ports and its recipe moves "
            f"{len(items)} items",
        )
    return ports[items.index(item_id)].name


def _plan_nets(spec: SfyBuildSpec, registry: Registry) -> tuple[_NetPlan, ...]:
    """Every net :func:`~flab2bp.sfy.layout.grid_nets.nets_for` will number, in its order.

    The direct pairs first, one net per machine pair; then one per remaining
    item in sorted order.  An id is consumed for EVERY loose item, including one
    that turns out to have nowhere to come from or go, because ``nets_for``
    consumes it before it finds out -- and a mirror that skipped the id would
    number every later net one lower than the router does.
    """
    by_group: dict[int, tuple[int, ...]] = {}
    first = 0
    for group in spec.groups:
        by_group[id(group)] = tuple(range(first, first + group.count))
        first += group.count
    ids = count(1)
    plans: list[_NetPlan] = []
    paired: set[int] = set()
    for pair in direct_pairs(spec):
        paired |= {id(pair.producer), id(pair.consumer)}
        out = _port_name(
            registry,
            pair.producer.machine_class,
            tuple(sorted(pair.producer.outputs_per_machine)),
            pair.item,
            "output",
        )
        into = _port_name(
            registry,
            pair.consumer.machine_class,
            tuple(sorted(pair.consumer.inputs_per_machine)),
            pair.item,
            "input",
        )
        for maker, eater in zip(
            by_group[id(pair.producer)], by_group[id(pair.consumer)], strict=True
        ):
            plans.append(
                _NetPlan(
                    id=next(ids),
                    sources=(_Port(maker, out),),
                    sinks=(_Port(eater, into),),
                    wall_in=False,
                    wall_out=False,
                    direct=True,
                )
            )
    for item_id in _loose_items(spec, paired):
        plan = _tree_plan(spec, registry, by_group, paired, item_id, next(ids))
        if plan is not None:
            plans.append(plan)
    return tuple(plans)


def _loose_items(spec: SfyBuildSpec, paired: frozenset[int] | set[int]) -> tuple[str, ...]:
    """Every item a net may still have to move, sorted -- ``_loose_items``' own rule."""
    items: set[str] = set(spec.external_inputs) | set(spec.outputs) | set(spec.surplus_outputs)
    for group in spec.groups:
        if id(group) in paired:
            continue
        items |= set(group.inputs_per_machine) | set(group.outputs_per_machine)
    return tuple(sorted(items))


def _tree_plan(
    spec: SfyBuildSpec,
    registry: Registry,
    by_group: Mapping[int, tuple[int, ...]],
    paired: frozenset[int] | set[int],
    item_id: str,
    net_id: int,
) -> _NetPlan | None:
    """One item's whole tree, or ``None`` where it has nowhere to come from or go."""
    sources: list[_Port] = []
    sinks: list[_Port] = []
    for group in spec.groups:
        if id(group) in paired:
            continue
        if item_id in group.outputs_per_machine:
            name = _port_name(
                registry,
                group.machine_class,
                tuple(sorted(group.outputs_per_machine)),
                item_id,
                "output",
            )
            sources.extend(_Port(machine, name) for machine in by_group[id(group)])
        if item_id in group.inputs_per_machine:
            name = _port_name(
                registry,
                group.machine_class,
                tuple(sorted(group.inputs_per_machine)),
                item_id,
                "input",
            )
            sinks.extend(_Port(machine, name) for machine in by_group[id(group)])
    wall_in = bool(spec.external_inputs.get(item_id))
    wall_out = bool(spec.outputs.get(item_id)) or bool(spec.surplus_outputs.get(item_id))
    if not (sources or wall_in) or not (sinks or wall_out):
        return None
    return _NetPlan(
        id=net_id,
        sources=tuple(sources),
        sinks=tuple(sinks),
        wall_in=wall_in,
        wall_out=wall_out,
        direct=False,
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

    model = cp_model.CpModel()
    xs: list[cp_model.IntVar] = []
    ys: list[cp_model.IntVar] = []
    yaws: list[list[cp_model.IntVar]] = []
    x_boxes: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    y_boxes: list[tuple[cp_model.IntVar, cp_model.IntVar]] = []
    x_intervals: list[cp_model.IntervalVar] = []
    y_intervals: list[cp_model.IntervalVar] = []
    for machine, stand in enumerate(stands):
        turns = shapes[stand.group.machine_class]
        x = model.new_int_var(0, lattice.n, f"x{machine}")
        y = model.new_int_var(0, lattice.n, f"y{machine}")
        literals = [model.new_bool_var(f"yaw{machine}_{int(yaw)}") for yaw in _YAWS]
        model.add_exactly_one(literals)
        edges = []
        spans = []
        for axis, (node, name) in enumerate(((x, "fx"), (y, "fy"))):
            lo = model.new_int_var(low, high, f"{name}0_{machine}")
            hi = model.new_int_var(low, high, f"{name}1_{machine}")
            # The size is its own variable rather than ``hi - lo``: an interval's
            # size has to be affine in ONE variable, and a turned box is as wide
            # as the yaw says.
            side = model.new_int_var(
                min(shape.box[axis + 2] - shape.box[axis] for shape in turns),
                max(shape.box[axis + 2] - shape.box[axis] for shape in turns),
                f"{name}w_{machine}",
            )
            for literal, shape in zip(literals, turns, strict=True):
                model.add(lo == grid * node + shape.box[axis]).only_enforce_if(literal)
                model.add(hi == grid * node + shape.box[axis + 2]).only_enforce_if(literal)
                model.add(side == shape.box[axis + 2] - shape.box[axis]).only_enforce_if(literal)
            edges.append((lo, hi))
            spans.append(side)
        x_boxes.append(edges[0])
        y_boxes.append(edges[1])
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

    nets = _plan_nets(spec, registry)
    apron_x, apron_y = _add_aprons(
        model,
        stands,
        shapes,
        xs,
        ys,
        yaws,
        x_boxes,
        y_boxes,
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
        walled=any(net.wall_in or net.wall_out for net in nets),
    )
    _add_objective(model, nets, registry, lattice, stands, shapes, xs, ys, yaws, feedback)
    return _Build(model=model, xs=xs, ys=ys, yaws=yaws)


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
    x_boxes: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    y_boxes: Sequence[tuple[cp_model.IntVar, cp_model.IntVar]],
    *,
    grid: int,
    low: int,
    high: int,
    apron: int,
) -> tuple[
    list[tuple[cp_model.IntVar, cp_model.IntVar]],
    list[tuple[cp_model.IntVar, cp_model.IntVar]],
]:
    """Keep every belt port's straight run clear of every OTHER machine's box.

    Not one :meth:`add_no_overlap_2d` over aprons and boxes together: that would
    also hold an apron off its own machine, which the port sits inside, and off
    every other apron, which belts are entitled to share.  What is asked for is
    exactly the pairs -- this apron against that box -- so each pair is one
    disjunction of the four ways two rectangles can miss each other.  The box on
    the other side is already inflated by a grid step, so a box that ENDS on an
    apron line is a machine a grid step away from it, which is what R7 allows.

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
                    run = (apron - 1) * port.step[axis]
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
                _keep_apart(model, edges[0], edges[1], x_boxes[other], y_boxes[other])
    return reach_x, reach_y


def _keep_apart(
    model: cp_model.CpModel,
    apron_x: tuple[cp_model.IntVar, cp_model.IntVar],
    apron_y: tuple[cp_model.IntVar, cp_model.IntVar],
    box_x: tuple[cp_model.IntVar, cp_model.IntVar],
    box_y: tuple[cp_model.IntVar, cp_model.IntVar],
) -> None:
    """One apron and one inflated footprint miss each other on at least one side."""
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
    nets: Sequence[_NetPlan],
    registry: Registry,
    lattice: Lattice,
    stands: Sequence[_Stand],
    shapes: Mapping[str, tuple[_Shape, ...]],
    xs: Sequence[cp_model.IntVar],
    ys: Sequence[cp_model.IntVar],
    yaws: Sequence[Sequence[cp_model.IntVar]],
    feedback: Feedback | None,
) -> None:
    """What an arrangement costs: belt to run, floor the router blamed.

    Three sums, all in the same scaled units and all minimised together:

    * every net's Manhattan distance, in nodes, from the centroid of its
      sources' port nodes to each of its sinks', weighted ``1 + failed``;
    * every direct pair's distance away from ONE shortest legal belt, at twice
      that weight -- the smallest multiple that makes the pair's own total
      strictly fall towards that distance from below and strictly rise past it,
      so the minimum is exactly at a shortest belt rather than anywhere under
      it;
    * every machine's share of the blame history under its footprint, read out
      of a summed-area table and only when there is any.
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
                if net.wall_in:
                    terms.append(weight * _span(model, into[1], lines.start, lattice.n))
                continue
            span = _span(model, centre[0] - into[0], 0, lattice.n) + _span(
                model, centre[1] - into[1], 0, lattice.n
            )
            terms.append(weight * span)
            if net.direct:
                terms.append(2 * weight * _span(model, span - shortest, 0, 2 * lattice.n))
        if centre is not None and net.wall_in:
            terms.append(weight * _span(model, centre[1], lines.start, lattice.n))
        if centre is not None and net.wall_out:
            terms.append(weight * _span(model, centre[1], lines.stop - 1, lattice.n))
    terms.extend(_hot_terms(model, lattice, stands, shapes, xs, ys, yaws, feedback))
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

    def of(self, port: _Port) -> tuple[cp_model.IntVar, cp_model.IntVar]:
        made = self._made.get((port.machine, port.name))
        if made is not None:
            return made
        turns = self._shapes[self._stands[port.machine].group.machine_class]
        node = (
            self._model.new_int_var(0, self._n, f"px{port.machine}_{port.name}"),
            self._model.new_int_var(0, self._n, f"py{port.machine}_{port.name}"),
        )
        centres = (self._xs[port.machine], self._ys[port.machine])
        for literal, shape in zip(self._yaws[port.machine], turns, strict=True):
            apron = next(one for one in shape.aprons if one.name == port.name)
            for axis in range(2):
                self._model.add(node[axis] == centres[axis] + apron.node[axis]).only_enforce_if(
                    literal
                )
        self._made[port.machine, port.name] = node
        return node


def _centroid(
    model: cp_model.CpModel, nodes: _PortNodes, net: _NetPlan, n: int
) -> tuple[cp_model.IntVar, cp_model.IntVar] | None:
    """Where a net comes from: the floor of the mean of its sources' port nodes.

    ``None`` where the net has no machine source at all, which is an item the
    spec belts in: the wall is a whole LINE of terminals the router picks one
    of (R-M3-5), so it has no node to take a mean with and the sinks are priced
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


def _hot_terms(
    model: cp_model.CpModel,
    lattice: Lattice,
    stands: Sequence[_Stand],
    shapes: Mapping[str, tuple[_Shape, ...]],
    xs: Sequence[cp_model.IntVar],
    ys: Sequence[cp_model.IntVar],
    yaws: Sequence[Sequence[cp_model.IntVar]],
    feedback: Feedback | None,
) -> list[cp_model.LinearExpr]:
    """What each machine's own footprint costs in blame the router already paid.

    A summed-area table over the blame projected onto the ground plane turns
    "how much history is under this machine" into one table lookup per candidate
    node, which is the trick that makes an O(1) surrogate out of an oracle
    thousands of times slower (``m3-router-reference.md`` §2.4).  The table is
    per SHAPE rather than per machine -- two machines of a class at one yaw
    cover the same nodes -- and none of it is built when nothing is hot.
    """
    if feedback is None or not feedback.hot_nodes:
        return []
    side = lattice.n + 1
    ground = [[0] * side for _ in range(side)]
    for (i, j, _level), weight in feedback.hot_nodes.items():
        if 0 <= i < side and 0 <= j < side:
            ground[i][j] += round(_WEIGHT_SCALE * weight)
    area = [[0] * (side + 1) for _ in range(side + 1)]
    for i in range(side):
        for j in range(side):
            area[i + 1][j + 1] = ground[i][j] + area[i][j + 1] + area[i + 1][j] - area[i][j]
    tables: dict[tuple[int, int, int, int], list[int]] = {}
    terms: list[cp_model.LinearExpr] = []
    for machine, stand in enumerate(stands):
        under = model.new_int_var(0, area[side][side], f"hot{machine}")
        index = model.new_int_var(0, side * side - 1, f"at{machine}")
        model.add(index == xs[machine] * side + ys[machine])
        for literal, shape in zip(yaws[machine], shapes[stand.group.machine_class], strict=True):
            table = tables.get(shape.inside)
            if table is None:
                table = _hot_table(area, shape.inside, side)
                tables[shape.inside] = table
            at_yaw = model.new_int_var(0, area[side][side], "")
            model.add_element(index, table, at_yaw)
            model.add(under == at_yaw).only_enforce_if(literal)
        terms.append(under)
    return terms


def _hot_table(
    area: Sequence[Sequence[int]], inside: tuple[int, int, int, int], side: int
) -> list[int]:
    """How much blame one shape covers from each node, flattened x-major.

    x-major because that is the lattice's own layout
    (:class:`~flab2bp.layout.geometric_world.GridIndex`), so an index built here
    reads the same way as one built there.
    """
    out: list[int] = []
    for i in range(side):
        for j in range(side):
            x0 = min(max(i + inside[0], 0), side)
            y0 = min(max(j + inside[1], 0), side)
            x1 = min(max(i + inside[2] + 1, 0), side)
            y1 = min(max(j + inside[3] + 1, 0), side)
            if x1 <= x0 or y1 <= y0:
                out.append(0)
                continue
            out.append(area[x1][y1] - area[x0][y1] - area[x1][y0] + area[x0][y0])
    return out


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
    """
    stands = _stands(spec)
    if not stands:
        raise PackError(NO_ARRANGEMENT, "the spec runs no machines, so there is nothing to stand")
    measures = _measure(registry, designer)
    build = _build(spec, registry, lattice, measures, stands, feedback)

    solver = cp_model.CpSolver()
    # Never zero: ortools reads ``num_workers == 0`` as ALL CORES.
    solver.parameters.num_workers = max(workers, 1)
    solver.parameters.random_seed = seed % _SEED_MODULUS
    solver.parameters.max_deterministic_time = max(
        _DETERMINISTIC_TIME_FLOOR, _DETERMINISTIC_TIME_PER_MACHINE * len(stands)
    )
    if deadline is not None:
        left = deadline - time.monotonic()
        if left <= 0.0:
            raise PackError(
                OVER_BUDGET,
                f"the deadline passed {-left:.3f} s before the packer was called, so no "
                "arrangement was searched for at all",
            )
        solver.parameters.max_time_in_seconds = left
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
