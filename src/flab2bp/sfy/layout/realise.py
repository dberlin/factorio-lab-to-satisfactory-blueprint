"""One lattice path as conveyors, attachment turns and conveyor lifts.

A path is a tuple of lattice nodes, optionally carrying its exact native motion
witness: 4-connected in ``XY``, climbing by the movement table's 2:1 incline and
hopping levels by a lift. This module turns it into the
objects a blueprint holds -- :class:`~flab2bp.sfy.layout.model.BeltRun`,
:class:`~flab2bp.sfy.layout.model.AttachmentObj`,
:class:`~flab2bp.sfy.layout.model.LiftObj` and the
:class:`~flab2bp.sfy.layout.model.Link` between them.  It invents no geometry of
its own: :class:`~flab2bp.sfy.layout.corridors.Route` walks the cursor through
straights, inclines and quarter turns,
:func:`resolve_turns` resolves or replays every right angle and
:func:`~flab2bp.sfy.layout.corridors.lay_path` cuts the result into conveyors --
the same machinery a manifold build is laid with.

**Reading a path, and the one thing a reader gets wrong.**  An incline's VIA is
a node OF THE PATH on the SOURCE level: the movement table marks the move
``via`` and the kernel reports the intermediate node, so a climb of one level
reads as ``(x, y, k) -> (x+1, y, k) -> (x+2, y, k+1)`` -- one flat step and then
an along-and-up step.  That pair is ONE incline leg, 200 cm of run per 100 cm of
rise with the shipped registry, and the via is never a corner.  A flat step is
one grid step at one level; a lift is ``(x, y, k) -> (x, y, k +- h)`` with no
node in between.  Consecutive pieces that point the same way and climb at the
same rate are one straight, so a corner is a change of DIRECTION and nothing
else.

**What a corner costs, and where the space comes from.** Global constraint 8:
an authored path prefers cheaper turns among globally feasible assignments.
A witnessed path replays its selected turn identities without reselection.
The attachment it weighs is
:func:`~flab2bp.sfy.layout.corridors.attachment_turn_tight`, not the manifold's
:func:`~flab2bp.sfy.layout.corridors.attachment_turn`: a splitter's clearance is
``CT_Soft`` and a grid-routed corner stands in open floor, so what the turn
really denies the straight is the reach to its own port plus the shortest legal
belt (201 cm with the shipped registry) rather than the whole box (301).  That
is a grid step of difference and it is the difference between a three-node leg
turning and not.

The space a corner is offered is the FREE length of the straight either side --
the leg's own length, less the previous corner's geometric reach, less what
the leg must keep for itself. The minimum belt between two attachment corners
is charged once, not once per end. A flat leg keeps nothing. An incline keeps
:func:`~flab2bp.sfy.layout.corridors.descent_run_cm` of run, because the rise is
fixed by the two levels and shortening the run past that angle is a belt
``belt.incline`` refuses: an arc may eat into a climb, but only down to the
game's own steepest chord. An attachment requires actual flat runs on both
sides, because its ports face horizontally. Geometry determines which turns
are eligible before their costs are compared. If none fits, Task 7's rip-up
loop is told which nodes pinched it; this module never re-routes.

**Where this is stricter than the game, and it is ours.**  R-M3-4: a belt is cut
only at a port, an attachment or a lift, and every piece between two cuts is at
least :func:`~flab2bp.sfy.layout.manifold.shortest_belt_cm` long -- 101 cm with
the shipped registry, one centimetre over ``belt.min_length``'s floor.  Two
nodes of lattice is 200 cm and clears it; one node is 100 cm and does not, so a
cut one node from another is a belt this module will not author.  The port that
does not stand on a node -- a Manufacturer's inputs are 875 cm out in its own
frame, 25 cm off the grid -- gets that 25 cm as the first (or last) piece of the
belt beside it rather than as a conveyor of its own, for the same reason.

Every distance here is read: the grid step, the turn radius, the attachment's
box and reach and the shortest belt all arrive in
:class:`~flab2bp.sfy.layout.corridors.Measures`, and the lift window comes
straight off ``Registry.limits``.  The figures in these docstrings are what the
shipped registry gives and are examples, never constants.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

from flab2bp.layout.geometric_motion import MotionWitness
from flab2bp.sfy.layout.corridors import (
    CorridorError,
    Measures,
    Route,
    Turn,
    lay_path,
)
from flab2bp.sfy.layout.lattice import Lattice, Node
from flab2bp.sfy.layout.manifold import shortest_belt_cm
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    LiftObj,
    Link,
    Pose,
    Vector,
    belt_ends,
    lift_geometry,
)
from flab2bp.sfy.layout.turns import eligible, fits_turn, free_run, options
from flab2bp.sfy.layout.validate import PORT_ANGLE_RAD, TOUCH_CM
from flab2bp.sfy.registry import Registry

__all__ = ["RealiseError", "Realised", "Terminal", "realise", "resolve_turns"]

_EPS = 1e-9

#: How far two unit headings may differ and still count as the same straight.
_PARALLEL = 1.0 - 1e-9


class RealiseError(ValueError):
    """A path this module will not build, and the nodes that stopped it.

    ``cause`` is a discriminator rather than prose, and there are four:

    * ``"corner"`` -- a right angle where neither an arc nor an attachment fits
      in the free straight either side of it (global constraint 8);
    * ``"leg"`` -- a step the lattice does not offer, or a piece of belt this
      module would have to author shorter than
      :func:`~flab2bp.sfy.layout.manifold.shortest_belt_cm` (R-M3-4);
    * ``"lift"`` -- a vertical edge that is not a height the lift hologram
      clamps to, or a top yaw the build gun cannot turn to;
    * ``"stub"`` -- a terminal whose port does not stand on the facing ray
      through its own node, so no straight joins the two.

    ``nodes`` is what Task 7's rip-up loop learns from: the nodes to blame, in
    path order.  It is deliberately local -- a corner names the corner and its
    two neighbours on the path, not the whole leg -- because the loop prices
    congestion per node and a wide blame moves belts that were never in the way.
    """

    def __init__(self, cause: str, nodes: Sequence[Node], detail: str) -> None:
        super().__init__(detail)
        self.cause = cause
        self.nodes: tuple[Node, ...] = tuple(nodes)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Terminal:
    """One end of a path: a port on an object, or a node at the designer wall.

    ``world`` is where the belt really has to start or stop, which for a port is
    the connection component's own position and need not be a node: a
    Manufacturer's inputs stand 875 cm out in its own frame, 25 cm off the 100 cm
    grid.  ``node`` is the lattice node the path starts or stops on, which for
    such a port is the first node out along ``facing``.

    ``facing`` is the port's normal, and it points the way the game does:
    outward from the object.  So flow LEAVES a source along ``+facing`` and
    ARRIVES at a sink along ``-facing``, which is the same sentence for a
    machine's output, a machine's input and a wall -- at the wall ``facing``
    points into the designer, so a wall source feeds the build and a wall sink
    drains it.

    ``port`` is ``(object id, port name)``, or ``None`` at the wall, where the
    belt end is flagged ``boundary_start``/``boundary_end`` instead of wired.
    ``kind`` says which of the three a terminal is; a ``"tap"`` is a port on a
    conveyor attachment and is treated exactly as a machine's port.

    ``reach`` is R-M3-2 (d): the nodes from the port to its machine's box edge,
    which lie INSIDE that machine's hard box and are therefore denied by the
    occupancy.  They travel here so that one mechanism opens per-net nodes --
    Task 7 fills the field and hands them to ``route_net`` as ``opened``.
    Nothing in this module reads them: by the time a path exists they have
    already done their work.
    """

    node: Node
    world: Vector
    facing: Vector
    port: tuple[int, str] | None
    kind: Literal["port", "wall", "tap"]
    reach: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class Realised:
    """One path, built: the objects, the links, and the columns the lifts hold.

    ``lift_columns`` is ``(bottom node, top node)`` per lift, lowest level
    first whichever way the items travel.  It is here because the movement table
    names no intermediate node for a lift -- the kernel admits one on the
    strength of its two ends alone -- so the column has to be held on COMMIT or
    the search will keep proposing lifts through machines.  Task 7 commits it.
    """

    belts: tuple[BeltRun, ...]
    attachments: tuple[AttachmentObj, ...]
    lifts: tuple[LiftObj, ...]
    links: tuple[Link, ...]
    lift_columns: tuple[tuple[Node, Node], ...]
    #: One entry per corner laid, in path order:
    #: :data:`~flab2bp.sfy.layout.corridors.ARC` or
    #: :data:`~flab2bp.sfy.layout.corridors.ATTACHMENT`.  A caller that wants
    #: turns BY KIND has to be told, because neither of the two counts beside it
    #: answers: an attachment in ``attachments`` may be a tap rather than a turn,
    #: and the right angles visible in the path are a floor on the corners the
    #: realiser was offered -- a stub out of a port standing off its own node can
    #: put one where no three path nodes show it.
    turns: tuple[str, ...] = ()


# --- reading the path ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Move:
    """One straight piece of a run: where it points, how far, how much it climbs.

    ``end`` is the index into the whole path of the node the piece ends on, or
    ``None`` where it ends on a port that is not a node.  It is carried so that
    a refusal can name the nodes rather than the centimetres.
    """

    direction: Vector
    run: float
    rise: float
    end: int | None


@dataclass(frozen=True, slots=True)
class _LiftEdge:
    """A vertical edge of the path, and which way items travel along it."""

    low: Node
    high: Node
    up: bool


def _lift_edges(path: Sequence[Node]) -> tuple[tuple[tuple[int, int], ...], tuple[_LiftEdge, ...]]:
    """The path's spans of horizontal travel, and the vertical edges between them.

    A span is ``(first index, last index)`` inclusive and there is always one
    more span than there are edges, even where a span is a single node: a lift
    standing on the terminal's own node has an empty run beside it, which is the
    case rule 4 calls "connects with no belt".
    """
    spans: list[tuple[int, int]] = []
    edges: list[_LiftEdge] = []
    start = 0
    for index in range(len(path) - 1):
        here, there = path[index], path[index + 1]
        if here[0] != there[0] or here[1] != there[1] or here[2] == there[2]:
            continue
        spans.append((start, index))
        up = there[2] > here[2]
        edges.append(_LiftEdge(low=here if up else there, high=there if up else here, up=up))
        start = index + 1
    spans.append((start, len(path) - 1))
    return (tuple(spans), tuple(edges))


def _steps(path: Sequence[Node], span: tuple[int, int], grid: float) -> list[_Move]:
    """One span's nodes as straight pieces, with a ramp's via read as a via.

    A ramp is THREE nodes -- two endpoints at node altitude and the midpoint via
    between them -- and only two of the three are places a belt really passes at
    a node's height.  The kernel reports the via on the move's SOURCE level, so
    the two nodes that share a level are the source and the via and the level
    change is the other step.  Which of the two steps comes first is the
    direction the path is read in, and both are read here: the ramp is
    recognised from the LEVEL-CHANGE step and the collinear flat step beside it,
    whether that flat step precedes it (``A(k) -> V(k) -> B(k+-1)``, the shape
    ``route_net`` returns) or follows it (``B(k+-1) -> V(k) -> A(k)``, the same
    ramp read the other way).  Assuming the first shape is what made a path piece
    that began at a ramp refuse.

    A level-change step with NO collinear flat step on either side is half a
    ramp: the path stops on the midpoint, which stands half a level up where no
    belt end and no attachment can be.  That is refused, and the message says so
    rather than calling it a move the lattice does not offer.
    """
    moves: list[_Move] = []
    index, last = span
    while index < last:
        here, there = path[index], path[index + 1]
        dx, dy, dz = there[0] - here[0], there[1] - here[1], there[2] - here[2]
        if abs(dx) + abs(dy) != 1:
            raise RealiseError(
                "leg",
                (here, there),
                f"{here} -> {there} is not a move this lattice offers: a belt steps one grid "
                "step at a level, climbs a level over a via, or takes a lift straight up",
            )
        if dz == 0:
            climb = _ramp_ahead(path, index, last)
            if climb is not None:
                moves.append(_ramp((dx, dy), climb, grid, end=index + 2))
                index += 2
                continue
            moves.append(
                _Move(direction=(float(dx), float(dy), 0.0), run=grid, rise=0.0, end=index + 1)
            )
            index += 1
            continue
        if abs(dz) == 1 and _flat_ahead(path, index, last) == (dx, dy):
            moves.append(_ramp((dx, dy), dz, grid, end=index + 2))
            index += 2
            continue
        raise RealiseError(
            "leg",
            (here, there),
            f"{here} -> {there} is half a ramp: it changes {abs(dz)} level(s) over one grid "
            "step, which no belt may do, and the collinear flat step that would make it a "
            "ramp is on neither side -- so one of these two nodes is a ramp's midpoint and "
            "the path stops there, half a level up, where no belt end may be",
        )
    return moves


def _ramp(step: tuple[int, int], rise: int, grid: float, *, end: int) -> _Move:
    """One ramp leg: two grid steps of run for the level it climbs."""
    return _Move(
        direction=(float(step[0]), float(step[1]), 0.0),
        run=2.0 * grid,
        rise=float(rise) * grid,
        end=end,
    )


def _ramp_ahead(path: Sequence[Node], index: int, last: int) -> int | None:
    """The rise of the ramp this flat step is the FIRST half of, or ``None``.

    ``A(k) -> V(k) -> B(k+-1)``: the step after this one changes level by one and
    points the same way, so the two are one ramp and ``V`` is its midpoint.
    """
    if index + 1 >= last:
        return None
    here, there, beyond = path[index], path[index + 1], path[index + 2]
    step = (there[0] - here[0], there[1] - here[1])
    rise = beyond[2] - there[2]
    if abs(rise) != 1 or (beyond[0] - there[0], beyond[1] - there[1]) != step:
        return None
    return rise


def _flat_ahead(path: Sequence[Node], index: int, last: int) -> tuple[int, int] | None:
    """The step after this one if it is flat, or ``None``.

    ``B(k+-1) -> V(k) -> A(k)``: a level-change step whose FOLLOWER is flat and
    collinear is the first half of the same ramp read the other way round, and
    ``V`` -- the node between them -- is the midpoint again.
    """
    if index + 1 >= last:
        return None
    there, beyond = path[index + 1], path[index + 2]
    if beyond[2] != there[2]:
        return None
    return (beyond[0] - there[0], beyond[1] - there[1])


def _merge(moves: Sequence[_Move]) -> list[_Move]:
    """Pieces that point the same way and climb at the same rate, as one straight.

    Which is what makes a corner a change of DIRECTION: two flat steps in a row
    are one leg, two inclines in a row are one longer climb, and a flat step
    followed by a climb is two legs with no turn between them.
    """
    out: list[_Move] = []
    for move in moves:
        if out and _one_straight(out[-1], move):
            head = out[-1]
            out[-1] = replace(
                head, run=head.run + move.run, rise=head.rise + move.rise, end=move.end
            )
            continue
        out.append(move)
    return out


def _one_straight(head: _Move, tail: _Move) -> bool:
    if _dot(head.direction, tail.direction) < _PARALLEL:
        return False
    return abs(head.rise * tail.run - tail.rise * head.run) <= TOUCH_CM * max(head.run, tail.run)


def _stub(head: Vector, tail: Vector, facing: Vector, *, end: int | None, at: Node) -> _Move | None:
    """The straight from a port to its node, or ``None`` where the port IS the node.

    The direction is taken from the two points rather than from the facing --
    the belt has to join them, whatever the connector's rotation says -- and is
    then held to the facing within ``ports.position``'s own angle, so a terminal
    whose node is not on its facing ray is refused instead of being bent to.
    """
    span = math.hypot(tail[0] - head[0], tail[1] - head[1])
    rise = tail[2] - head[2]
    if span <= TOUCH_CM:
        if abs(rise) > TOUCH_CM:
            raise RealiseError(
                "stub",
                (at,),
                f"the port at {tail} stands {rise:.1f} cm above its own node and no run "
                "joins them, so there is no straight to lay",
            )
        return None
    direction = ((tail[0] - head[0]) / span, (tail[1] - head[1]) / span, 0.0)
    if _dot(direction, facing) < math.cos(PORT_ANGLE_RAD):
        raise RealiseError(
            "stub",
            (at,),
            f"the port at {tail} does not stand on the facing ray through its node {at}, "
            "so the belt to it would leave the port off its own normal",
        )
    return _Move(direction=direction, run=span, rise=rise, end=end)


# --- turning a run into a route --------------------------------------------


def resolve_turns(
    moves: Sequence[_Move],
    path: Sequence[Node],
    measures: Measures,
    registry: Registry,
    lattice: Lattice,
    selected: Mapping[int, int] | None = None,
) -> tuple[list[Turn | None], list[float], list[float]]:
    """Resolve a fixed run exactly, preserving cheaper feasible prefixes.

    Each boundary has at most two options. Backward feasibility retains both
    reaches until the following leg discharges their obligations, then forward
    replay chooses the cheapest option with a feasible suffix. Thus a cheap arc
    cannot discard a costlier attachment whose smaller reach is needed later.
    Selection cost and geometric reach stay distinct on a shared leg.

    ``selected`` keys are original path indices, not merged-leg indices. When
    supplied every corner is specified, including a sink-stub turn recorded by
    the motion witness's explicit final endpoint action.
    """
    free = [free_run(move.run, move.rise, registry) for move in moves]
    offered = options(measures)
    candidates: list[list[Turn | None]] = []
    expected: set[int] = set()
    for index, (here, there) in enumerate(zip(moves, moves[1:], strict=False)):
        if _dot(here.direction, there.direction) >= _PARALLEL:
            candidates.append([None])
            continue
        if _cross(here.direction, there.direction) < 0.5:
            raise RealiseError(
                "corner",
                _blame(path, here.end),
                "the path doubles back on itself, and a belt has no way to turn through "
                "half a circle on one node",
            )
        at = here.end if here.end is not None else len(path) - 1
        expected.add(at)
        choices = list(enumerate(offered))
        if selected is not None:
            if at in selected:
                action = selected[at]
                if type(action) is not int or not 0 <= action < len(offered):
                    raise RealiseError("corner", _blame(path, at), "invalid selected turn action")
                choices = [(action, offered[action])]
            else:
                raise RealiseError("corner", _blame(path, at), "missing selected turn action")
        choices.sort(key=lambda choice: (choice[1].cost, choice[0]))
        legal: list[Turn | None] = [
            turn
            for _, turn in choices
            if eligible(turn, here.rise, there.rise, path[at], lattice)
            and fits_turn(turn, free[index], 0.0)
            and fits_turn(turn, free[index + 1], 0.0)
        ]
        if not legal:
            raise RealiseError(
                "corner",
                _blame(path, at),
                f"a corner with {free[index]:.0f} cm of run into it and "
                f"{free[index + 1]:.0f} cm out has no eligible selected turn that fits",
            )
        candidates.append(legal)
    if selected is not None:
        extra = set(selected) - expected
        if extra:
            at = min(extra)
            raise RealiseError("corner", _blame(path, at), "turn action where no corner exists")

    def compatible(previous: Turn | None, following: Turn | None, run: float) -> bool:
        return following is None or fits_turn(
            following, run, previous.reach if previous is not None else 0.0
        )

    for index in range(len(candidates) - 2, -1, -1):
        candidates[index] = [
            turn
            for turn in candidates[index]
            if any(
                compatible(turn, following, free[index + 1]) for following in candidates[index + 1]
            )
        ]
        if not candidates[index]:
            raise RealiseError(
                "corner",
                _blame(path, moves[index + 1].end),
                "consecutive corners have no legal turn assignment on their shared leg",
            )
    corners: list[Turn | None] = []
    reach_start = [0.0] * len(moves)
    reach_end = [0.0] * len(moves)
    previous: Turn | None = None
    for index, feasible in enumerate(candidates):
        turn = next(option for option in feasible if compatible(previous, option, free[index]))
        corners.append(turn)
        if turn is not None:
            reach_end[index] = turn.reach
            reach_start[index + 1] = turn.reach
        previous = turn
    return (corners, reach_start, reach_end)


def _route(
    moves: Sequence[_Move],
    start: Vector,
    path: Sequence[Node],
    measures: Measures,
    registry: Registry,
    lattice: Lattice,
    selected: Mapping[int, int] | None = None,
) -> tuple[Route, tuple[str, ...]]:
    """One run of the path as a :class:`Route`, and the kind of every turn in it.

    The cursor stops one reach short of every corner because that is where the
    turn takes hold -- an arc leaves the straight a radius early and an
    attachment's input port stands a reach back from the corner it sits on --
    and :meth:`Route.turn` puts it down again one reach the other side.
    """
    corners, reach_start, reach_end = resolve_turns(
        moves, path, measures, registry, lattice, selected
    )
    route = Route(point=start, heading=moves[0].direction)
    for index, move in enumerate(moves):
        route.go(move.run - reach_start[index] - reach_end[index], move.rise)
        turn = corners[index] if index < len(corners) else None
        if turn is not None:
            route.turn(_is_left(move.direction, moves[index + 1].direction), turn)
    return (route, tuple(turn.kind for turn in corners if turn is not None))


# --- the lifts -------------------------------------------------------------


def _lift(
    edge: _LiftEdge,
    *,
    arrive: Vector,
    leave: Vector,
    lattice: Lattice,
    registry: Registry,
    lift_class: str,
    ids: Iterator[int],
) -> LiftObj:
    """One vertical edge as a :class:`LiftObj`, turned to meet both belts.

    The actor is the INPUT end: items enter a lift by ``mConnection0``, which
    sits at the actor transform, so a lift that carries items UP stands at the
    bottom with a positive height and one that carries them DOWN stands at the
    top with a negative one.  Both ends' connector normals are the actor's own
    forward turned by a yaw -- the bottom by the pose's, the top by the pose's
    plus ``top_yaw_deg`` -- so the pose's yaw is chosen to put the entry normal
    along the belt that arrives and the top yaw to put the exit normal along the
    belt that leaves, which is what ``ports.position`` holds a belt off a lift's
    top to.

    The height is the lattice's, and it is then held to the game's own window
    and step rather than trusted: the movement table only offers legal heights,
    but a table and a registry that disagreed would build a lift the hologram
    silently moves.
    """
    geometry = lift_geometry(registry, lift_class)
    limits = registry.limits
    levels = edge.high[2] - edge.low[2]
    height = levels * lattice.grid_cm
    low, high, step = limits.lift_min_cm, limits.lift_max_cm, limits.lift_step_cm
    if low is None or high is None or step is None:
        raise RealiseError(
            "lift",
            (edge.low, edge.high),
            "the registry states no lift height window, so no lift may be sized",
        )
    if not low - TOUCH_CM <= height <= high + TOUCH_CM:
        raise RealiseError(
            "lift",
            (edge.low, edge.high),
            f"a lift of {height:.0f} cm is outside the {low:.0f}..{high:.0f} cm the hologram "
            "clamps a lift into",
        )
    if min(height % step, step - height % step) > TOUCH_CM:
        raise RealiseError(
            "lift",
            (edge.low, edge.high),
            f"a lift of {height:.0f} cm is not a whole number of the {step:.0f} cm the "
            "hologram snaps a height to",
        )
    actor = lattice.world(edge.low if edge.up else edge.high)
    yaw = _yaw_of(arrive) - _yaw_of(geometry.bottom_facing)
    top_yaw = _snap(_yaw_of(leave) - _yaw_of(arrive), geometry.top_yaw_step_deg, edge)
    if not geometry.top_yaw_free and abs(top_yaw) > TOUCH_CM:
        raise RealiseError(
            "lift",
            (edge.low, edge.high),
            f"the belt off this lift leaves {top_yaw:.0f} degrees round from the one that "
            f"arrives and the registry says {lift_class}'s top yaw is not free",
        )
    return LiftObj(
        id=next(ids),
        class_name=lift_class,
        pose=Pose(actor[0], actor[1], actor[2], _wrap(yaw)),
        height_cm=height if edge.up else -height,
        top_yaw_deg=top_yaw,
    )


def _snap(yaw: float, step: float, edge: _LiftEdge) -> float:
    """``yaw`` on the lattice of yaws the build gun can turn a lift's top to.

    ``AFGConveyorLiftHologram::GetRotationStep`` is 90 degrees once the first
    placement point is down, so a top yaw off that lattice is one no player
    could build.  A yaw that is not already on it is a corner this module did
    not mean to make, so it is refused rather than rounded.
    """
    if step <= 0.0:
        return 0.0
    wrapped = _wrap(yaw)
    steps = round(wrapped / step)
    if abs(wrapped - steps * step) > TOUCH_CM:
        raise RealiseError(
            "lift",
            (edge.low, edge.high),
            f"the belts either side of this lift meet at {wrapped:.1f} degrees, which is not "
            f"a whole number of the {step:.0f} degree step a lift's top turns in",
        )
    return _wrap(steps * step)


# --- the whole path --------------------------------------------------------


def _replay_motion(
    path: Sequence[Node],
    motion: MotionWitness,
    lattice: Lattice,
    registry: Registry,
    lift_class: str,
) -> dict[int, int]:
    """Validate original-coordinate primitive provenance, not compiler states."""
    from flab2bp.sfy.layout.motion import motion_moves

    primitives = motion_moves(lattice, registry, lift_class)
    selected: dict[int, int] = {}
    index = 0
    for step in motion.steps:
        if (
            type(step.path_index) is not int
            or step.path_index != index
            or type(step.move) is not int
            or not 0 <= step.move < len(primitives)
            or index >= len(path) - 1
        ):
            raise RealiseError(
                "leg", tuple(path[index : index + 3]), "invalid motion primitive index"
            )
        dx, dy, dz, via = primitives[step.move]
        end = index + (2 if via else 1)
        here = path[index]
        target = (here[0] + dx, here[1] + dy, here[2] + dz)
        if end >= len(path) or path[end] != target:
            raise RealiseError(
                "leg", tuple(path[index : end + 1]), "motion primitive does not match path geometry"
            )
        if via and path[index + 1] != (here[0] + dx // 2, here[1] + dy // 2, here[2]):
            raise RealiseError(
                "leg", tuple(path[index : end + 1]), "motion ramp via must lie on its source level"
            )
        if type(step.action) is not int or step.action < -1:
            raise RealiseError("corner", _blame(path, index), "invalid selected turn action")
        if step.action != -1:
            selected[index] = step.action
        index = end
    if index != len(path) - 1:
        raise RealiseError(
            "leg", tuple(path[index:]), "motion witness does not cover the entire path"
        )
    if type(motion.final_action) is not int or motion.final_action < -1:
        raise RealiseError("corner", (path[-1],), "invalid selected endpoint action")
    if motion.final_action != -1:
        selected[len(path) - 1] = motion.final_action
    return selected


def realise(
    path: Sequence[Node],
    *,
    source: Terminal,
    sink: Terminal,
    lattice: Lattice,
    measures: Measures,
    registry: Registry,
    belt_class: str,
    lift_class: str,
    item_id: str,
    rate: Fraction,
    ids: Iterator[int],
    motion: MotionWitness | None = None,
) -> Realised:
    """``path`` as belts, attachment turns and lifts, wired end to end.

    The path is cut at its lifts and nowhere else; each piece between two cuts
    becomes one :class:`Route`, and ``lay_path`` cuts THAT again at any
    attachment the corners called for and at ``belt.max_length``.  A piece with
    no length at all -- a lift standing on the terminal's own node -- becomes no
    belt and one link, which is rule 4's "connects with no belt".

    ``ids`` numbers every object this makes, so a caller that realises several
    nets hands the same counter to each and the ids stay unique across the
    build.
    """
    nodes = tuple(path)
    if not nodes:
        raise RealiseError("leg", (), "a net with no path has nothing to build")
    if nodes[0] != source.node or nodes[-1] != sink.node:
        raise RealiseError(
            "stub",
            (nodes[0], nodes[-1]),
            f"the path runs {nodes[0]} to {nodes[-1]} and the terminals stand on "
            f"{source.node} and {sink.node}",
        )
    selected = (
        _replay_motion(nodes, motion, lattice, registry, lift_class) if motion is not None else None
    )
    spans, edges = _lift_edges(nodes)
    runs = [_merge(_steps(nodes, span, lattice.grid_cm)) for span in spans]
    first = _stub(source.world, lattice.world(nodes[0]), source.facing, end=0, at=nodes[0])
    if first is not None:
        runs[0] = _merge([first, *runs[0]])
    last = _stub(
        lattice.world(nodes[-1]),
        sink.world,
        (-sink.facing[0], -sink.facing[1], -sink.facing[2]),
        end=None,
        at=nodes[-1],
    )
    if last is not None:
        runs[-1] = _merge([*runs[-1], last])
    for moves, facing, node, first_end in (
        (runs[0], source.facing, nodes[0], True),
        (runs[-1], (-sink.facing[0], -sink.facing[1], 0.0), nodes[-1], False),
    ):
        if moves and _dot(
            moves[0].direction if first_end else moves[-1].direction,
            _horizontal(facing),
        ) < math.cos(PORT_ANGLE_RAD):
            raise RealiseError(
                "stub", (node,), "the terminal's belt does not follow its facing ray"
            )
    if (
        not edges
        and not any(runs)
        and _dot(_horizontal(source.facing), _horizontal((-sink.facing[0], -sink.facing[1], 0.0)))
        < math.cos(PORT_ANGLE_RAD)
    ):
        raise RealiseError(
            "stub", (nodes[0],), "directly linked terminals have incompatible headings"
        )
    if selected is not None:
        corners = {
            here.end
            for moves in runs
            for here, there in zip(moves, moves[1:], strict=False)
            if _dot(here.direction, there.direction) < _PARALLEL
        }
        extra = selected.keys() - corners
        if extra:
            raise RealiseError(
                "corner", _blame(nodes, min(extra)), "turn action where no corner exists"
            )

    lifts = _lifts(runs, edges, source, sink, lattice, registry, lift_class, ids)
    entry, exit_end = belt_ends(registry, lift_class)
    floor = shortest_belt_cm(registry.limits)
    belts: list[BeltRun] = []
    attachments: list[AttachmentObj] = []
    links: list[Link] = []
    turns: list[str] = []
    for index, moves in enumerate(runs):
        upstream = source.port if index == 0 else (lifts[index - 1].id, exit_end)
        downstream = sink.port if index == len(runs) - 1 else (lifts[index].id, entry)
        if not moves:
            if upstream is None or downstream is None:
                raise RealiseError(
                    "stub",
                    (nodes[spans[index][0]],),
                    "a terminal at the designer wall needs a belt to reach it, and this one "
                    "has no run at all",
                )
            links.append(Link(a=upstream, b=downstream))
            continue
        _flat_at_cuts(moves, upstream, downstream, nodes, spans[index])
        start = (
            source.world
            if index == 0
            else lifts[index - 1].top_end(lift_geometry(registry, lift_class))[0]
        )
        span_selected = (
            {
                at: action
                for at, action in selected.items()
                if spans[index][0] <= at <= spans[index][1]
            }
            if selected is not None
            else None
        )
        route, kinds = _route(moves, start, nodes, measures, registry, lattice, span_selected)
        turns.extend(kinds)
        try:
            laid = lay_path(
                route,
                registry=registry,
                class_name=belt_class,
                item_id=item_id,
                rate=rate,
                ids=ids,
                upstream=upstream,
                downstream=downstream,
            )
        except CorridorError as failure:
            raise RealiseError(
                "leg",
                _span_nodes(nodes, spans[index]),
                f"this run cannot be cut into conveyors ({failure.cause}): {failure.detail}",
            ) from failure
        for belt in laid.belts:
            length = _chord_cm(belt)
            if length < floor - TOUCH_CM:
                raise RealiseError(
                    "leg",
                    _span_nodes(nodes, spans[index]),
                    f"this run wants a conveyor of {length:.1f} cm and the shortest belt "
                    f"this project authors is {floor:.0f} cm (R-M3-4)",
                )
        belts.extend(laid.belts)
        attachments.extend(laid.attachments)
        links.extend(laid.links)
    return Realised(
        belts=tuple(belts),
        attachments=tuple(attachments),
        lifts=tuple(lifts),
        links=tuple(links),
        lift_columns=tuple((edge.low, edge.high) for edge in edges),
        turns=tuple(turns),
    )


def _lifts(
    runs: Sequence[Sequence[_Move]],
    edges: Sequence[_LiftEdge],
    source: Terminal,
    sink: Terminal,
    lattice: Lattice,
    registry: Registry,
    lift_class: str,
    ids: Iterator[int],
) -> list[LiftObj]:
    """Every vertical edge as a lift, turned to the belts that meet it.

    The two headings a lift is turned by are the FLOW's, not any one run's: a
    run with no moves hands the question straight on, which is how a lift
    standing on a terminal's own node takes its yaw from the port it sits on and
    how two lifts stacked on one another agree.
    """
    arrive: list[Vector] = []
    heading = _horizontal(source.facing)
    for moves in runs:
        if moves:
            heading = moves[-1].direction
        arrive.append(heading)
    leave: list[Vector] = [(0.0, 0.0, 0.0)] * len(runs)
    heading = _horizontal((-sink.facing[0], -sink.facing[1], 0.0))
    for index in reversed(range(len(runs))):
        if runs[index]:
            heading = runs[index][0].direction
        leave[index] = heading
    return [
        _lift(
            edge,
            arrive=arrive[index],
            leave=leave[index + 1],
            lattice=lattice,
            registry=registry,
            lift_class=lift_class,
            ids=ids,
        )
        for index, edge in enumerate(edges)
    ]


# --- small arithmetic ------------------------------------------------------


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: Vector, b: Vector) -> float:
    return abs(a[0] * b[1] - a[1] * b[0])


def _is_left(here: Vector, there: Vector) -> bool:
    """Whether the path turns towards ``+Z`` cross the heading, which is a left."""
    return here[0] * there[1] - here[1] * there[0] > 0.0


def _horizontal(vector: Vector) -> Vector:
    span = math.hypot(vector[0], vector[1])
    if span <= _EPS:
        raise RealiseError(
            "stub",
            (),
            "a terminal's facing points straight up or down, and a belt on this lattice "
            "leaves a port along the floor",
        )
    return (vector[0] / span, vector[1] / span, 0.0)


def _yaw_of(vector: Vector) -> float:
    return math.degrees(math.atan2(vector[1], vector[0]))


def _wrap(yaw: float) -> float:
    """``yaw`` in ``(-180, 180]``, which is the window a Pose's yaw is read in."""
    wrapped = math.fmod(yaw, 360.0)
    if wrapped > 180.0:
        wrapped -= 360.0
    elif wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def _blame(path: Sequence[Node], end: int | None) -> tuple[Node, ...]:
    """The node a corner stands on and its two neighbours along the path."""
    if end is None:
        return (path[-1],)
    low = max(end - 1, 0)
    return tuple(path[low : end + 2])


def _flat_at_cuts(
    moves: Sequence[_Move],
    upstream: tuple[int, str] | None,
    downstream: tuple[int, str] | None,
    path: Sequence[Node],
    span: tuple[int, int],
) -> None:
    """A belt runs FLAT out of a port and flat into one, or this refuses.

    ``ports.position`` holds a belt leaving a buildable's port to that port's own
    normal within ``PORT_ANGLE_RAD``, and every connector this project meets --
    a machine's, an attachment's, a conveyor lift's top -- faces along the
    ground.  A run whose first leg climbs leaves at the ramp's own angle instead:
    26.57 degrees, 0.46 rad, on the shipped registry, which is forty-six times
    the slack.  So a path that ramps out of a cut is a belt the game would put
    somewhere else, and it is refused here with the nodes rather than drawn and
    failed by the judge.

    The far end is held to the same rule.  Nothing checks the tangent a belt
    ARRIVES on, so this half is ours: a connector faces along the ground at both
    ends of a belt, and :mod:`~flab2bp.sfy.layout.laying` has run flat into a
    port as well as out of one since M2.  A DESIGNER WALL is not a port and is
    exempt at either end -- there is no connector there to be off the normal of.

    This is the rule ``Measures.lead_in`` is named for; no length is demanded
    beyond it, because the flat piece's own length is already R-M3-4's business.

    Internal attachment cuts are checked when their turns are selected in
    ``resolve_turns``; they are not present in this span's upstream/downstream endpoints.
    """
    for cut, move, node in (
        (upstream, moves[0], path[span[0]]),
        (downstream, moves[-1], path[span[1]]),
    ):
        if cut is None or abs(move.rise) <= TOUCH_CM:
            continue
        raise RealiseError(
            "leg",
            (node,),
            f"this belt would meet object {cut[0]}'s {cut[1]} while climbing "
            f"{abs(move.rise):.0f} cm over {move.run:.0f}, and a connector faces along the "
            "ground: a belt runs flat out of a port and flat into one",
        )


def _span_nodes(path: Sequence[Node], span: tuple[int, int]) -> tuple[Node, ...]:
    return tuple(path[span[0] : span[1] + 1])


def _chord_cm(belt: BeltRun) -> float:
    """The length ``belt.min_length`` measures: the polyline, not the arc.

    ``AFGConveyorBeltHologram::ValidateMinLength`` sums the chords between the
    spline's points, which on a curve is shorter than
    :func:`~flab2bp.sfy.layout.splines.spline_length`.  R-M3-4's floor is that
    rule's floor, so it is measured that rule's way.
    """
    return sum(math.dist(a[0], b[0]) for a, b in zip(belt.points, belt.points[1:], strict=False))
