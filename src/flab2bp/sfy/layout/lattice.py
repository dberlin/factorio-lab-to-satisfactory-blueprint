"""The node lattice a grid-routed belt is searched on, and what stands in it.

A grid-routed build puts every machine on the build gun's own 1 m hologram grid
and finds every belt with a geometric interval router.  This module is the world
that router searches: :class:`Lattice` says where a node is, and
:class:`Occupancy` says whether a belt centreline may pass one.

**Where a node is** (R-M3-1).  Node ``(i, j, k)`` is world
``(-half + 100 i, -half + 100 j, 100 k)``, for ``0 <= i, j, k <= n``, where
``half`` is :attr:`~flab2bp.sfy.spec.Designer.half_cm`, ``n`` is the designer's
side in grid steps and the ``100`` is ``limits.hologram_grid_cm`` READ -- the
build gun's own smallest move, never a number written here.  A belt centreline
runs along lattice lines between nodes, so a belt at level ``k`` has centreline
``z = 100 k``.  The slab is level 1, machines stand on it, and every belt port of
a grid-snapped machine sits at level 2 -- which is why
:data:`GROUND_LEVEL` is 2 and levels 0 and 1 are impassable everywhere (R-M3-3).

**What blocks a node** (R-M3-2).  A node at level ``k`` is impassable when the
158 x 158 x 30 cm box a belt carries there -- twice
:data:`~flab2bp.sfy.layout.validate.BELT_CLEARANCE_HALF_WIDTH_CM` square and
twice :data:`~flab2bp.sfy.layout.validate.BELT_CLEARANCE_HALF_HEIGHT_CM` tall,
orientation-free -- meets any hard clearance box, the designer wall, or a lift's
column box.  Every one of those shapes is the game's: the boxes are
``registry.json``'s ``Buildable.clearance``, placed by
:func:`flab2bp.sfy.geometry.box_bounds`; the lift's is
:func:`~flab2bp.sfy.layout.validate.lift_box`; the belt's own half extents are
``belt.clearance``'s, read out of ``AFGBuildableConveyorBelt::CreateClearanceData``.
A committed run blocks its own nodes and the two nodes across it at its level:
two belts 100 cm apart lap by 58 cm, and a belt ending or turning on the node
beside a run reaches 50 cm along its own last segment into the run's 79.
Adjacent levels never interact.

**Seven places this lattice is stricter than the game, and they are ours.**
This list is the whole of it; ``docs/sfy-layout-model.md`` states the same seven
in prose, with what each one costs.

1. Belt pitch is 200 cm.  The game's own closest legal pitch is 158, which is
   not a multiple of the grid step, so the lattice offers 100 (a lap of 58 cm,
   refused) or 200 (a clearance of 42 cm).
2. The node beyond a run's last node, along the run, stays free for a
   perpendicular belt -- and only that node.  A run's clearance chain stops at
   its last node, so a belt crossing 100 cm past it comes no closer than 21 cm.
3. R7's one grid step between two machines' hard boxes stays: ``buildable.clearance``
   is ``partial`` (``AFGHologram::TestClearanceOverlap`` was never read), so the
   gap two holograms really need is unknown and this project keeps its own.
4. A belt's own path from its port to its machine's box edge lies INSIDE that
   machine's hard box.  Those nodes are blocked here like any other node in the
   box; opening them for one port's net alone is not this module's business --
   they travel on Task 6's ``Terminal.reach`` and reach Task 5's ``route_net``
   as ``opened``, so one mechanism opens per-net nodes.  What this module does
   promise is the other half: :meth:`Occupancy.commit` never takes OWNERSHIP of
   a node the world already denies, so a repair search can never rip a net up
   in the hope of freeing a node a machine is standing in.
5. The outermost lines and the topmost level are closed: a centreline there
   hangs its own clearance outside the designer, which ``geom.bounds`` refuses
   -- and ``geom.bounds`` is this project's rule, since the designer CLIPS what
   overhangs rather than turning it away.  :attr:`Lattice.open_lines` and
   :attr:`Lattice.open_levels` are where that is stated.
6. A rotated clearance box is blocked by its world-axis bounding box, which is
   exact for the quarter turns a grid-snapped build places and over-covers any
   other yaw -- see :func:`_mark_box`.
7. A belt already in the placement is blocked by one axis-aligned box per spline
   segment rather than by the game's chain of short boxes along the curve, so
   the corner a turn never reaches is denied too -- see :func:`_belt_boxes`.

**The loops that flatten a placement onto this lattice are the only per-node
Python this milestone allows.**  Everything downstream of them is a geometric
interval router: nothing searches cells, and nothing walks the lattice asking a
predicate per node.  They are written accordingly -- a box's node range is
computed once and the resulting slab of indices is marked with slice
assignments, never one predicate call per node per box.

**The invariant.**  :meth:`Occupancy.free` is the predicate, and
:attr:`Occupancy.flags` -- the flat array the kernel actually searches -- must
agree with it node for node.  A ``free()`` that knows something the array does
not is the DSP router's worst failure mode: the search returns paths the
committer then throws away, every round, having learned nothing.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import chain

from flab2bp.layout.geometric_world import GridIndex
from flab2bp.sfy.geometry import box_bounds
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeamObj,
    BeltRun,
    FoundationObj,
    LiftObj,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    Vector,
)
from flab2bp.sfy.layout.validate import (
    BELT_CLEARANCE_HALF_HEIGHT_CM,
    BELT_CLEARANCE_HALF_WIDTH_CM,
    TOUCH_CM,
    beam_box,
    lift_box,
    passthrough_box,
    pipe_chain,
)
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import Designer

__all__ = [
    "BoxBounds",
    "GROUND_LEVEL",
    "Lattice",
    "Node",
    "Occupancy",
    "belt_levels",
    "occupancy_for",
]

Node = tuple[int, int, int]
BoxBounds = tuple[Vector, Vector]

GROUND_LEVEL = 2
"""The lowest level a belt centreline may stand on -- R-M3-3.

Derived, not chosen: the slab's top is one grid step up and a grid-snapped
machine's belt port sits one step above that, so level 0 is inside the
foundation, level 1 is the band between the slab and the ports, and level 2 is
where a machine's own ports are.  Nothing may route below the ports.
"""


# --- where a node is -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lattice:
    """The 1 m node grid over one Blueprint Designer -- R-M3-1.

    ``n`` and ``codec`` are DERIVED and never passed: the designer's own
    dimensions and the hologram grid step already fix both, and a second way to
    supply either is a second way to disagree with the array the router
    searches.

    ``open_lines`` and ``open_levels`` are the lattice's own geometry rather
    than an occupancy: a belt centreline on the outermost line hangs its 79 cm
    of clearance outside the designer, and one on the topmost level hangs 15 cm
    through the ceiling, both of which ``geom.bounds`` refuses.  They are stated
    once here so that :meth:`Occupancy.free` and the flattening cannot disagree
    about them.

    ``object_lines`` is the shared standing domain for turn and tap attachments:
    the open belt lines whose registry-measured attachment box fits wholly in
    the designer. A box may touch the wall (``geom.bounds`` uses ``TOUCH_CM``).
    The belt and attachment extents are independent containment requirements,
    not margins to add: insetting ``open_lines`` by the full attachment extent
    would unnecessarily discard another line per side with the shipped registry.
    """

    designer: Designer
    #: ``limits.hologram_grid_cm``: the build gun's own smallest move.
    grid_cm: float
    #: Attachment clearance half-extent, read by :meth:`over` from the registry.
    #: Zero preserves direct construction for node-only geometry; callers that
    #: stand attachments use :meth:`over` to include their measured extent.
    object_half_cm: float = 0.0
    #: Nodes per axis, minus one: the designer's side in grid steps.
    n: int = field(init=False)
    #: The flat index codec, shared with the DSP router so that one statement of
    #: the x-major layout serves both games.
    codec: GridIndex = field(init=False)
    #: The lines ``i`` (and ``j``) a belt may stand on at all.
    open_lines: range = field(init=False)
    #: The open belt lines on which the attachment box stays inside the designer.
    object_lines: range = field(init=False)
    #: The levels ``k`` a belt may stand on at all.
    open_levels: range = field(init=False)

    def __post_init__(self) -> None:
        dims = self.designer.dims
        if dims[0] != dims[1] or dims[0] != dims[2]:
            raise ValueError(
                f"the {self.designer.mark} designer is {dims} foundations, which is not a "
                "cube; one node count per axis cannot describe it"
            )
        steps = dims[0] * self.designer.foundation_cm / self.grid_cm
        n = round(steps)
        if abs(steps - n) > TOUCH_CM:
            raise ValueError(
                f"the {self.designer.mark} designer is "
                f"{dims[0] * self.designer.foundation_cm:.1f} cm across, which is not a "
                f"whole number of {self.grid_cm:.1f} cm grid steps"
            )
        half = self.designer.half_cm
        across = _lines_within(-half, half, BELT_CLEARANCE_HALF_WIDTH_CM, -half, self.grid_cm)
        up = _lines_within(
            0.0, self.designer.height_cm, BELT_CLEARANCE_HALF_HEIGHT_CM, 0.0, self.grid_cm
        )
        standing = _lines_within(-half, half, self.object_half_cm, -half, self.grid_cm)
        lines = range(max(across.start, 0), min(across.stop, n + 1))
        object.__setattr__(self, "n", n)
        object.__setattr__(self, "codec", GridIndex(0, 0, n + 1, n + 1))
        object.__setattr__(self, "open_lines", lines)
        object.__setattr__(
            self,
            "object_lines",
            range(max(standing.start, lines.start), min(standing.stop, lines.stop)),
        )
        object.__setattr__(
            self, "open_levels", range(max(up.start, GROUND_LEVEL), min(up.stop, n + 1))
        )

    @classmethod
    def over(cls, designer: Designer, registry: Registry) -> Lattice:
        """The lattice over ``designer``, stepped by the game's own hologram grid.

        A registry that carries no grid step is refused rather than given one:
        an invented step would put every node somewhere the build gun does not.
        """
        grid_cm = registry.limits.hologram_grid_cm
        if grid_cm is None:
            raise ValueError(
                "the registry states no hologram_grid_cm, so there is no grid to lay "
                "nodes on and none may be invented here"
            )
        # Imported where it is used: the corridor's measures are a reading of the
        # same registry, and nothing about where a NODE is needs them.
        from flab2bp.sfy.layout.corridors import attachment_box_cm

        return cls(designer, grid_cm, attachment_box_cm(registry))

    def world(self, node: Node) -> Vector:
        """Where ``node`` is, in centimetres -- R-M3-1."""
        half = self.designer.half_cm
        return (
            -half + self.grid_cm * node[0],
            -half + self.grid_cm * node[1],
            self.grid_cm * node[2],
        )

    def node(self, point: Vector) -> Node | None:
        """Which node ``point`` is, or ``None`` if it is not one.

        ``None`` covers both ways of not being a node: a point between two
        lattice lines by more than :data:`~flab2bp.sfy.layout.validate.TOUCH_CM`,
        and a point outside the designer altogether.
        """
        half = self.designer.half_cm
        i = self._line(point[0], -half)
        j = self._line(point[1], -half)
        k = self.level(point[2])
        if i is None or j is None or k is None:
            return None
        return (i, j, k)

    def level(self, z: float) -> int | None:
        """Which level ``z`` is, or ``None`` if it is between two."""
        return self._line(z, 0.0)

    def _line(self, value: float, origin: float) -> int | None:
        """Which lattice line along one axis ``value`` is, or ``None``."""
        steps = (value - origin) / self.grid_cm
        line = round(steps)
        if abs(steps - line) * self.grid_cm > TOUCH_CM or not 0 <= line <= self.n:
            return None
        return line

    def holds(self, node: Node) -> bool:
        """Whether ``node`` is a node of this lattice at all."""
        return all(0 <= node[axis] <= self.n for axis in range(3))

    def index(self, node: Node) -> int:
        """``node``'s flat index, bounds-checked."""
        if not self.holds(node):
            raise IndexError(f"{node} is outside a lattice of {self.n + 1} nodes per axis")
        return self.codec.encode(node)

    def size(self) -> int:
        return (self.n + 1) ** 3


def _lines_within(lo: float, hi: float, half: float, origin: float, grid: float) -> range:
    """The lines along one axis whose box lies wholly inside ``[lo, hi]``."""
    first = math.ceil((lo + half - TOUCH_CM - origin) / grid)
    last = math.floor((hi - half + TOUCH_CM - origin) / grid)
    return range(first, last + 1)


def _lines_meeting(lo: float, hi: float, half: float, origin: float, grid: float) -> range:
    """The lines along one axis whose belt box MEETS ``[lo, hi]``.

    "Meets" is the validator's own reading: the two boxes must really lap, by
    more than :data:`~flab2bp.sfy.layout.validate.TOUCH_CM`, so that two things
    sharing a face are not called an overlap.
    """
    first = math.floor((lo - half + TOUCH_CM - origin) / grid) + 1
    last = math.ceil((hi + half - TOUCH_CM - origin) / grid) - 1
    return range(first, last + 1)


def belt_levels(z0: float, z1: float, grid: float) -> range:
    """The levels a belt may not stand on because ``[z0, z1]`` is in the way.

    A belt's clearance reaches
    :data:`~flab2bp.sfy.layout.validate.BELT_CLEARANCE_HALF_HEIGHT_CM` above and
    below its centreline, so level ``k`` is denied when ``100 k +- 15`` laps
    ``[z0, z1]``.  The range is not clamped to any designer: a caller with a
    lattice clamps it.
    """
    return _lines_meeting(z0, z1, BELT_CLEARANCE_HALF_HEIGHT_CM, 0.0, grid)


# --- what stands in it -----------------------------------------------------


@dataclass
class Occupancy:
    """Which nodes of a :class:`Lattice` a belt centreline may pass.

    ``flags`` is the flat array the router's kernel searches -- ``1`` where a
    belt may pass, ``0`` where it may not -- and :meth:`free` is the predicate
    it must agree with node for node.  ``base`` is ``flags`` as the world alone
    left it, before any path was committed; rip-up restores from it rather than
    writing ``1``, because a ripped node is not necessarily a free one.

    ``owner`` answers "whose belt is in my way" for a repair search: node index
    to net id, for every node a committed path holds or SHADOWS.  A node can be
    shadowed by two nets at once -- two legal runs 200 cm apart both deny the
    line between them -- so the claims behind ``owner`` are kept as a list and
    the node is only restored when the last of them lets go.
    """

    lattice: Lattice
    flags: bytearray
    base: bytes
    owner: dict[int, int]
    #: Congestion history per node, as the DSP kernel reads it: absent is 0.0.
    history: array[float]
    #: Physical world boxes BEFORE expansion by a hypothetical belt centreline.
    static_bounds: list[BoxBounds]
    _paths: dict[int, tuple[Node, ...]] = field(default_factory=dict, repr=False)
    _claims: dict[int, list[int]] = field(default_factory=dict, repr=False)

    def free(self, node: Node) -> bool:
        """THE predicate: may a belt centreline pass ``node``?

        The structural half is the lattice's own -- R-M3-3's floor and the
        designer wall, both of which :attr:`Lattice.open_lines` and
        :attr:`Lattice.open_levels` state once -- and the rest is the flat
        array.  The flattening writes the structural half into ``flags`` as
        well, so the two answer alike and
        ``test_flags_agree_with_free_on_every_node`` can say so.
        """
        lattice = self.lattice
        if node[0] not in lattice.open_lines or node[1] not in lattice.open_lines:
            return False
        if node[2] not in lattice.open_levels:
            return False
        # Those three ranges all lie inside ``0..n``, so the node is on the
        # lattice by the time the codec sees it and needs no second check.
        return self.flags[lattice.codec.encode(node)] == 1

    def block_box(self, low: Vector, high: Vector) -> None:
        """Deny every node whose belt box meets the world box ``[low, high]``.

        Part of building the world, not of routing it: ``base`` is refreshed
        here, so a box may not be added once a path is committed.
        """
        if self._paths:
            raise RuntimeError(
                "a box cannot be added to an occupancy that already holds committed paths: "
                "rip-up restores from `base`, and this would move it under them"
            )
        _mark_box(self, low, high)
        self.base = bytes(self.flags)

    def commit(self, net: int, path: Sequence[Node], *, claim_blocked: bool = False) -> None:
        """Hold ``path`` and its shadow for ``net`` -- R-M3-2's committed run.

        A node the world already denies is passed over rather than claimed: it
        is not this net's to give back, and a repair search that read ``owner``
        there would rip this net up to free a node a machine is standing in
        (R-M3-2 (d)).

        Physical shafts also claim blocked interface reservations: those nodes
        can be opened by a later query, but not through a foreign lift. The
        original base flag still controls what rip-up restores.
        """
        if net in self._paths:
            raise ValueError(f"net {net} is already committed; rip it up before committing again")
        for index in self._shadow(path):
            if self.base[index] == 0 and not claim_blocked:
                continue
            self.flags[index] = 0
            claims = self._claims.setdefault(index, [])
            if net not in claims:
                claims.append(net)
            self.owner.setdefault(index, net)
        self._paths[net] = tuple(path)

    def rip_up(self, net: int) -> None:
        """Give back what ``net`` holds, to the state the WORLD left it in.

        A node another net still shadows stays denied and changes hands; a node
        nothing is left holding goes back to ``base``, which is not the same as
        going back to passable.  Ripping up a net that holds nothing is a
        no-op, because a rip-up-and-reroute loop asks about nets it never routed.
        """
        path = self._paths.pop(net, None)
        if path is None:
            return
        for index in self._shadow(path):
            claims = self._claims.get(index)
            if claims is None or net not in claims:
                continue
            claims.remove(net)
            if claims:
                self.owner[index] = claims[0]
            else:
                del self._claims[index]
                del self.owner[index]
                self.flags[index] = self.base[index]

    def snapshot(self) -> bytes:
        """A copy of ``flags`` for one net's query to open its own nodes in."""
        return bytes(self.flags)

    def _shadow(self, path: Sequence[Node]) -> set[int]:
        """Every node index ``path`` holds or shadows -- R-M3-2.

        Each node of the path, and for every segment the two nodes across it at
        each of its ends: two belts one line apart lap by 58 cm, and a belt
        ending or turning beside a run reaches 50 cm into the run's 79.  The
        node BEYOND an end, along the run, is deliberately not here -- a run's
        clearance stops at its last node.  A node where the path turns is the
        end of two segments and is shadowed across both of them.
        """
        lattice = self.lattice
        out = {lattice.index(node) for node in path if lattice.holds(node)}
        for head, tail in zip(path, path[1:], strict=False):
            dx, dy = tail[0] - head[0], tail[1] - head[1]
            if dx and dy:
                raise ValueError(
                    f"the segment {head} -> {tail} is diagonal; a belt on this lattice turns "
                    "on a node, so a diagonal run has no across"
                )
            if not dx and not dy:
                continue  # a climb in place has no across
            across = (0, 1) if dx else (1, 0)
            for node in (head, tail):
                for sign in (1, -1):
                    beside = (
                        node[0] + sign * across[0],
                        node[1] + sign * across[1],
                        node[2],
                    )
                    if lattice.holds(beside):
                        out.add(lattice.index(beside))
        return out


def occupancy_for(
    lattice: Lattice,
    machines: Sequence[MachineObj],
    attachments: Sequence[AttachmentObj],
    lifts: Sequence[LiftObj],
    belts: Sequence[BeltRun],
    registry: Registry,
    *,
    foundations: Sequence[FoundationObj] = (),
    pipes: Sequence[PipeRun] = (),
    pipe_attachments: Sequence[PipeAttachmentObj] = (),
    beams: Sequence[BeamObj] = (),
    passthroughs: Sequence[PassthroughObj] = (),
) -> Occupancy:
    """Flatten a placement onto ``lattice``: what a belt may pass, and where not.

    The world in the order it is laid down: the designer itself (R-M3-1 and
    R-M3-3), then every hard clearance box a machine, attachment or floor carries,
    then every lift's column box, then every belt already in the placement.
    Machine and attachment soft clearances may be shared. Foundations instead
    reserve their slab volume even when the registry calls it soft: the composer
    supplies these as floors without passthroughs, not as empty routing space.
    """
    size = lattice.size()
    occupancy = Occupancy(
        lattice=lattice,
        flags=_open_designer(lattice),
        base=b"",
        owner={},
        history=array("d", bytes(8 * size)),
        static_bounds=[],
    )
    for obj in chain[MachineObj | AttachmentObj | PipeAttachmentObj | FoundationObj](
        machines, attachments, pipe_attachments, foundations
    ):
        buildable = registry.buildables.get(obj.class_name)
        if buildable is None:
            continue
        transform = obj.pose.transform()
        for box in buildable.clearance:
            if box.soft and not isinstance(obj, FoundationObj):
                continue
            low, high = box_bounds(box, transform)
            _mark_box(occupancy, low, high)
    for lift in lifts:
        if abs(lift.height_cm) <= 0.0:
            continue  # a lift of no height has no axis, and so no box
        column = lift_box(lift, registry)
        centre, reach = column.centre, column.reach
        _mark_box(
            occupancy,
            (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
            (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
        )
    for run in belts:
        for low, high in _belt_boxes(run):
            _mark_box(occupancy, low, high)
    dynamic_boxes = chain(
        (box for run in pipes for box in pipe_chain(run, registry)),
        (beam_box(beam, registry) for beam in beams),
        (passthrough_box(hole, registry) for hole in passthroughs),
    )
    for dynamic_box in dynamic_boxes:
        centre, reach = dynamic_box.centre, dynamic_box.reach
        _mark_box(
            occupancy,
            (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
            (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
        )
    occupancy.base = bytes(occupancy.flags)
    return occupancy


def _open_designer(lattice: Lattice) -> bytearray:
    """``flags`` with nothing in the designer: the walls and the floor alone.

    Built the other way round from everything else -- all denied, then the one
    block of nodes a belt may stand on opened -- so the structural half of
    :meth:`Occupancy.free` and this cannot drift apart.  One slice assignment
    per column, not one write per node.
    """
    flags = bytearray(lattice.size())
    levels = lattice.open_levels
    passable = b"\x01" * len(levels)
    for i in lattice.open_lines:
        for j in lattice.open_lines:
            start = lattice.codec.encode((i, j, levels.start))
            flags[start : start + len(levels)] = passable
    return flags


def _mark_box(occupancy: Occupancy, low: Vector, high: Vector) -> None:
    """Deny every node whose belt box meets the world box ``[low, high]``.

    The box's node range is worked out once per axis and the resulting slab is
    written a column at a time; nothing here asks a question per node.  The box
    is axis-aligned, which is exact for the quarter-turn yaws a grid-snapped
    build uses and conservative -- stricter, never laxer -- for any other.
    """
    occupancy.static_bounds.append((low, high))
    lattice = occupancy.lattice
    grid, half = lattice.grid_cm, lattice.designer.half_cm
    across = BELT_CLEARANCE_HALF_WIDTH_CM
    xs = _clamp(_lines_meeting(low[0], high[0], across, -half, grid), lattice.n)
    ys = _clamp(_lines_meeting(low[1], high[1], across, -half, grid), lattice.n)
    ks = _clamp(belt_levels(low[2], high[2], grid), lattice.n)
    if not xs or not ys or not ks:
        return
    denied = bytes(len(ks))
    for i in xs:
        for j in ys:
            start = lattice.codec.encode((i, j, ks.start))
            occupancy.flags[start : start + len(ks)] = denied


def _clamp(lines: range, n: int) -> range:
    return range(max(lines.start, 0), min(lines.stop, n + 1))


def _belt_boxes(run: BeltRun) -> Iterator[tuple[Vector, Vector]]:
    """One world box per spline segment of ``run``, as wide as its clearance.

    ``belt.clearance`` lays a 158 x 30 cm box about each segment's own axis, and
    what a node needs is a shape that CONTAINS the segment: a cubic Hermite lies
    inside the convex hull of its four Bezier control points, so the hull's
    bounding box holds the curve exactly rather than by sampling.  On a straight
    run -- which is every run this lattice routes -- the hull is the chord and
    the box is the game's own.  On a turn it is wider than the game's chain,
    which is stricter and ours.
    """
    reach = (
        BELT_CLEARANCE_HALF_WIDTH_CM,
        BELT_CLEARANCE_HALF_WIDTH_CM,
        BELT_CLEARANCE_HALF_HEIGHT_CM,
    )
    for (head, _, leave), (tail, arrive, _) in zip(run.points, run.points[1:], strict=False):
        hull = (
            head,
            (head[0] + leave[0] / 3.0, head[1] + leave[1] / 3.0, head[2] + leave[2] / 3.0),
            (tail[0] - arrive[0] / 3.0, tail[1] - arrive[1] / 3.0, tail[2] - arrive[2] / 3.0),
            tail,
        )
        low = tuple(min(p[a] for p in hull) - reach[a] for a in range(3))
        high = tuple(max(p[a] for p in hull) + reach[a] for a in range(3))
        yield (low[0], low[1], low[2]), (high[0], high[1], high[2])
