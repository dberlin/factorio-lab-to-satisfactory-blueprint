"""Drawing the corridors: the attachments that stand in them and the belts between.

By the time anything here runs every decision has been taken --
:mod:`~flab2bp.sfy.layout.rows` has stood the rows and
:mod:`~flab2bp.sfy.layout.nets` has said which item runs up which column, where
each merger and splitter stands, and which column each trunk was given.  What is
left is geometry: the shape of the belt out of a port, across the corridor, up
the column, over anything already in the way, and into the next port.

Four shapes and the same three pieces in each -- a transverse across the corridor,
a run up the column, a transverse back in -- are :meth:`CorridorLayer._route`; the
straight belt from a node to the row it serves is :meth:`CorridorLayer._lay_tap`.
Every turn is a quarter circle of the corridor's own radius and every climb is
:func:`~flab2bp.sfy.layout.corridors.descent_run_cm`, so no distance here is
chosen: it is the registry's through
:class:`~flab2bp.sfy.layout.corridors.Measures` and
:func:`~flab2bp.sfy.layout.corridors.lay_path`.

Nothing here decides what the game accepts.  A belt that cannot be shaped raises
:class:`~flab2bp.sfy.layout.corridors.CorridorError` with the cause
:mod:`flab2bp.sfy.layout.strategy` turns into the refusal a caller sees.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fractions import Fraction

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.labmap import LabMap, machine_class
from flab2bp.sfy.layout.corridors import (
    BRIDGE_CLEARANCE_BOXES,
    Assignment,
    CorridorError,
    Measures,
    Route,
    bridge_z_cm,
    descent_run_cm,
    lay_path,
)
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS, RowError
from flab2bp.sfy.layout.model import AttachmentObj, BeltRun, Link, Pose, Vector
from flab2bp.sfy.layout.nets import CorridorPlan, _carried, _Net, _Node, _Terminal
from flab2bp.sfy.layout.rows import RowPlan
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM
from flab2bp.sfy.registry import Buildable, Port, Registry
from flab2bp.sfy.spec import SfyBuildSpec
from flab2bp.spec import BeltTier

__all__ = ["CorridorLayer"]

_EPS = 1e-6
"""How close to a bound counts as inside it, in centimetres.

The same tolerance :mod:`~flab2bp.sfy.layout.rows` and
:mod:`~flab2bp.sfy.layout.nets` compare with.
"""

_UP: Vector = (0.0, 1.0, 0.0)
"""The way every corridor column runs: from the ``-Y`` wall towards the ``+Y``."""

_Anchor = tuple[Vector, Vector, tuple[int, str] | None]
"""Where a path starts or ends: the point, the way it is going, the port to wire."""


@dataclass
class CorridorLayer:
    """One run's corridors, drawn from the row plan and the corridor plan."""

    spec: SfyBuildSpec
    registry: Registry
    lab_map: LabMap
    budget: WorkBudget
    measures: Measures
    #: The build's object numbering, carried on from the rows.
    ids: Iterator[int]
    row_plan: RowPlan
    corridor_plan: CorridorPlan

    def _lay_corridors(self) -> tuple[list[AttachmentObj], list[BeltRun], list[Link]]:
        attachments: list[AttachmentObj] = []
        belts: list[BeltRun] = []
        links: list[Link] = []
        for net in self.corridor_plan.nets:
            self.budget.check()  # the clock bounds the drawing as well as the search
            x = self._column_x(net.side, self.corridor_plan.spine[net.label].column)
            stood = [self._stand(node, x) for node in net.nodes]
            attachments.extend(obj for obj, _ in stood)
            self._lay_spine(net, x, stood, belts, links)
            for index, (obj, ports) in enumerate(stood):
                self._lay_tap(net, index, obj, ports, belts, links)
        return attachments, belts, links

    def _column_x(self, side: int, column: int) -> float:
        return side * (
            self.row_plan.x_edge + self.corridor_plan.offset + column * self.measures.pitch
        )

    def _stand(self, node: _Node, x: float) -> tuple[AttachmentObj, tuple[Port, Port, Port]]:
        """One corridor attachment, turned so that it flows up the column.

        Yaw 90 puts the through ports on ``+-Y`` -- the way a corridor runs -- and
        the two side ports on ``+-X``; which of those faces out of the corridor is
        read off its facing rather than named.
        """
        class_name = MERGER_CLASS if node.merging else SPLITTER_CLASS
        buildable = self.registry.buildables[class_name]
        pose = Pose(x, node.y, self.row_plan.belt_z, 90.0)
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
        assignment = self.corridor_plan.spine[net.label]
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
        crossed = self.corridor_plan.spine[net.label].bridged_taps[index]
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
            over = max(over, bridge_z_cm(self.row_plan.belt_z, self.registry))
        up = descent_run_cm(over - start[2], self.registry)
        down = descent_run_cm(over - end[2], self.registry)
        flat = self.measures.lead_in if up else 0.0
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
            return ((x, -self.measures.half, self.row_plan.belt_z), _UP, None)
        return (terminal.point, (float(side), 0.0, 0.0), terminal.link)

    def _arrive(self, terminal: _Terminal, x: float, side: int) -> _Anchor:
        """Where a path ends, and which way it is going when it gets there."""
        if terminal.wall:
            return ((x, self.measures.half, self.row_plan.belt_z), _UP, None)
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
        bridge = bridge_z_cm(self.row_plan.belt_z, self.registry)
        transverse_b = abs(arriving[0]) > 0.5
        stop = end[1] - (self.measures.radius if transverse_b else 0.0)
        if abs(heading[0]) > 0.5:
            crossed = assignment.bridged_a
            self._run_out(route, net, x, bridge if crossed else point[2], crossed)
            route.turn(left, self.measures.radius)
            if crossed:
                # A bridge comes down inside its own column, right after the turn.
                self._climb(route, net, self.row_plan.belt_z, stop, first=True)
        self._climb(
            route,
            net,
            max(end[2], bridge) if transverse_b and assignment.bridged_b else end[2],
            stop,
            first=False,
        )
        if transverse_b:
            route.turn(left, self.measures.radius)
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

        The rise starts a flat :attr:`~flab2bp.sfy.layout.corridors.Measures.lead_in`
        out of the port, not at it: the port faces along the ground and
        ``ports.position`` holds this project to a belt that leaves a port along
        the port's own facing, which is the same rule the row builder's feeders
        keep.
        """
        span = abs(x - route.point[0]) - self.measures.radius
        rise = climb - route.point[2]
        lift = descent_run_cm(rise, self.registry)
        flat = self.measures.lead_in if lift else 0.0
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


# --- helpers ---------------------------------------------------------------


def _flat(vector: Vector) -> Vector:
    """A facing as a heading along one axis: which way a belt runs at a port."""
    return (1.0 if vector[0] > 0 else -1.0, 0.0, 0.0)


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
