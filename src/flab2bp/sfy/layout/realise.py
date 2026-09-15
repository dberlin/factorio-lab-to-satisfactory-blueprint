"""One lattice path as conveyors, attachment turns and conveyor lifts.

Task 5's router hands back a PATH and nothing else: a tuple of lattice nodes,
4-connected in ``XY``, climbing by the 2:1 incline the movement table offers and
hopping levels by a lift.  This module is what turns one of those into the
objects a blueprint holds -- :class:`~flab2bp.sfy.layout.model.BeltRun`,
:class:`~flab2bp.sfy.layout.model.AttachmentObj`,
:class:`~flab2bp.sfy.layout.model.LiftObj` and the
:class:`~flab2bp.sfy.layout.model.Link` between them.  It invents no geometry of
its own: :class:`~flab2bp.sfy.layout.corridors.Route` walks the cursor through
straights, inclines and quarter turns,
:func:`~flab2bp.sfy.layout.corridors.choose_turn` decides every right angle and
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

**What a corner costs, and where the space comes from.**  Global constraint 8:
at every turn the realiser takes whichever of arc and attachment costs the least
space where it stands, which is exactly what ``choose_turn`` answers.  The space
it is offered is the FREE length of the straight either side -- the leg's own
length, less what the corner before it already spent, less what the leg must
keep for itself.  A flat leg keeps nothing.  An incline keeps
:func:`~flab2bp.sfy.layout.corridors.descent_run_cm` of run, because the rise is
fixed by the two levels and shortening the run past that angle is a belt
``belt.incline`` refuses: so a turn may eat into a climb, but only down to the
game's own steepest chord.  Where neither turn fits, the corner is refused and
Task 7's rip-up loop is told which nodes pinched it; this module never
re-routes.

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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Literal

from flab2bp.sfy.layout.corridors import (
    CorridorError,
    Measures,
    Route,
    Turn,
    choose_turn,
    descent_run_cm,
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
from flab2bp.sfy.layout.validate import PORT_ANGLE_RAD, TOUCH_CM
from flab2bp.sfy.registry import Registry

__all__ = ["RealiseError", "Realised", "Terminal", "realise"]

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
    """One span's nodes as straight pieces, with the incline's via read as a via.

    A step whose ``dz`` is zero and whose horizontal displacement is one grid
    step is a flat move -- UNLESS the step after it climbs one level in the same
    direction, in which case the two together are one incline leg of two grid
    steps of run per level of rise and the middle node is the via.  Anything
    else is a move this lattice does not offer and is refused rather than
    guessed at.
    """
    moves: list[_Move] = []
    index, last = span
    while index < last:
        here, there = path[index], path[index + 1]
        dx, dy, dz = there[0] - here[0], there[1] - here[1], there[2] - here[2]
        if dz == 0 and abs(dx) + abs(dy) == 1:
            if index + 1 < last:
                beyond = path[index + 2]
                ex, ey, ez = beyond[0] - there[0], beyond[1] - there[1], beyond[2] - there[2]
                if abs(ez) == 1 and (ex, ey) == (dx, dy):
                    moves.append(
                        _Move(
                            direction=(float(dx), float(dy), 0.0),
                            run=2.0 * grid,
                            rise=float(ez) * grid,
                            end=index + 2,
                        )
                    )
                    index += 2
                    continue
            moves.append(
                _Move(direction=(float(dx), float(dy), 0.0), run=grid, rise=0.0, end=index + 1)
            )
            index += 1
            continue
        raise RealiseError(
            "leg",
            (here, there),
            f"{here} -> {there} is not a move this lattice offers: a belt steps one grid "
            "step at a level, climbs a level over a via, or takes a lift straight up",
        )
    return moves


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


def _free(move: _Move, registry: Registry) -> float:
    """How much of a leg a corner may take, which is all of it unless it climbs.

    A climb's rise is fixed by the two levels it joins, so the run a turn eats
    out of it makes it steeper; ``descent_run_cm`` is the shortest run
    ``belt.incline`` allows for that rise, and what is left over is the only
    part a corner may have.
    """
    if abs(move.rise) <= TOUCH_CM:
        return move.run
    return max(0.0, move.run - descent_run_cm(move.rise, registry))


def _turns(
    moves: Sequence[_Move],
    path: Sequence[Node],
    measures: Measures,
    registry: Registry,
) -> tuple[list[Turn | None], list[float], list[float]]:
    """Which turn each corner takes, and what it takes out of the legs either side.

    The sweep runs once, from the upstream end: a corner is offered what is left
    of the leg behind it -- the leg's free length less what the corner before it
    spent -- and the whole free length of the leg ahead.  Nothing needs a second
    pass, because the next corner is then offered what this one left it, so the
    two spends on a leg can never add to more than the leg has.

    Two numbers come back per corner because :class:`Turn` states two: ``cost``
    is what the turn spends of the straight and is what the choice is made on;
    ``reach`` is where the turn takes hold and is what the cursor has to stop
    short by.  An arc's are equal, an attachment's are not -- it stands on the
    corner with its ports one reach out, but it also denies the shortest legal
    belt beyond them.
    """
    corners: list[Turn | None] = [None] * (len(moves) - 1)
    reach_start = [0.0] * len(moves)
    reach_end = [0.0] * len(moves)
    spent = [0.0] * len(moves)
    free = [_free(move, registry) for move in moves]
    for index in range(len(moves) - 1):
        here, there = moves[index], moves[index + 1]
        if _dot(here.direction, there.direction) >= _PARALLEL:
            continue  # a change of slope, not of direction: no turn stands here
        if _cross(here.direction, there.direction) < 0.5:
            raise RealiseError(
                "corner",
                _blame(path, here.end),
                "the path doubles back on itself, and a belt has no way to turn through "
                "half a circle on one node",
            )
        along = free[index] - spent[index]
        across = free[index + 1]
        turn = choose_turn(measures, along, across)
        if turn.cost > along + _EPS or turn.cost > across + _EPS:
            raise RealiseError(
                "corner",
                _blame(path, here.end),
                f"a corner with {along:.0f} cm of run into it and {across:.0f} cm out has "
                f"room for neither an arc nor an attachment, the cheaper of which costs "
                f"{turn.cost:.0f} cm of each",
            )
        corners[index] = turn
        spent[index] += turn.cost
        spent[index + 1] += turn.cost
        reach_end[index] = turn.reach
        reach_start[index + 1] = turn.reach
    return (corners, reach_start, reach_end)


def _route(
    moves: Sequence[_Move],
    start: Vector,
    path: Sequence[Node],
    measures: Measures,
    registry: Registry,
) -> Route:
    """One run of the path as a :class:`Route`: the straights and the turns.

    The cursor stops one reach short of every corner because that is where the
    turn takes hold -- an arc leaves the straight a radius early and an
    attachment's input port stands a reach back from the corner it sits on --
    and :meth:`Route.turn` puts it down again one reach the other side.
    """
    corners, reach_start, reach_end = _turns(moves, path, measures, registry)
    route = Route(point=start, heading=moves[0].direction)
    for index, move in enumerate(moves):
        route.go(move.run - reach_start[index] - reach_end[index], move.rise)
        turn = corners[index] if index < len(corners) else None
        if turn is not None:
            route.turn(_is_left(move.direction, moves[index + 1].direction), turn)
    return route


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

    lifts = _lifts(runs, edges, source, sink, lattice, registry, lift_class, ids)
    entry, exit_end = belt_ends(registry, lift_class)
    floor = shortest_belt_cm(registry.limits)
    belts: list[BeltRun] = []
    attachments: list[AttachmentObj] = []
    links: list[Link] = []
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
        start = (
            source.world
            if index == 0
            else lifts[index - 1].top_end(lift_geometry(registry, lift_class))[0]
        )
        route = _route(moves, start, nodes, measures, registry)
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
