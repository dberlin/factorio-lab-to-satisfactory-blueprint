"""Every net, negotiated across rip-up-and-reroute rounds.

This is the DSP router's loop (``m3-router-reference.md`` §1.7) with the
Satisfactory pieces in it.  Nets are offered the floor in order of what they
carry; every round rips up ALL of them and routes them again against a higher
congestion pressure; each net's whole tree is searched, then built into belts,
attachment turns and lifts and committed together with the columns its lifts
stand in; the best round -- fewest stranded, then least belt -- is the one the
occupancy is left holding.  Searching the whole tree before building any of it
is R-M3-7's own doing: what a piece of belt CARRIES is not known until the tree
is, so a belt laid branch by branch would be a belt laid on a guess at its tier.

Failures are RETURNED, not raised: the packer
(Task 8) is the caller that decides whether to move a machine, and a routing
refusal swallowed here would be a strategy quietly shipping half a build.

**Four ways a net loses its round, and only one of them proves anything.**

1. The search found no path.  Only a sealed pocket proves the geometry refused;
   a budget or a deadline proves only that a bound ended it, which is why
   :class:`~flab2bp.sfy.layout.router.Routed` carries the cause and why the
   stranded list carries the ``Routed`` beside the net.
2. The realiser refused the path (:class:`~...realise.RealiseError`).  Task 6
   measured why this is ORDINARY rather than exceptional: with the shipped
   registry a corner never fits immediately after a climb -- an incline leaves
   0 to 100 cm of straight against an attachment turn's 301 cm -- and a
   same-level corner wants about four straight nodes on each side.  So a path
   that turns soon after climbing, or in tight quarters, is refused by
   construction, and the loop's answer is to charge :data:`BLAME_WEIGHT` at the
   nodes the error names and ask again with those nodes ``closed``, at most
   :data:`REALISE_RETRIES` times.
3. The lift column was not free.  **The ruling this module was written to**
   (2026-09-15): a per-level movement table carries one via per move, so the
   kernel admits a lift on the strength of its two END nodes alone and no
   commit-side rule can stop it proposing the same lift through the same
   machine next round.  So when :func:`~...realise.realise` hands back a
   :class:`~...realise.Realised` whose ``lift_columns`` meet the occupancy --
   every node of the column at every level strictly between the ends, plus the
   footprint of the lift's own clearance box through
   :func:`~...validate.lift_box` -- it is treated exactly as a
   ``RealiseError("lift", column nodes)``: :data:`BLAME_WEIGHT` is charged at
   the lift's two end nodes, the nodes the kernel DOES test, and the net is
   re-queried in the same round with those ends ``closed``, at most
   :data:`LIFT_RETRIES` times.  That is a COUNT and not a convergence: Task 5
   measured that closing one lift's bottom in an open corridor simply moves the
   lift one node sideways, so a loop that retried until it settled would retry
   for ever.

4. The path stood in its own trunk's shadow.  A tap side has to be OPENED for
   the query that may start there, and an opened node is passable for the whole
   path, so a branch can leave one tap and then run along the line of tap sides
   a grid step from its own trunk.  :func:`_in_the_shadow` catches that on the
   path that comes back and the sides it walked through are shut for the next
   query -- a correction rather than congestion, so nothing is charged for it.

**What the loop shuts before it asks anything** (:func:`_shut_shafts`).  A port
terminal's node and its reach lie inside the machine's own hard box, so a lift
with either end on one of them has its column inside that box and can never be
built.  The kernel cannot know that -- a lift row names no intermediate node --
so every query shuts the shaft over every port of the net it is routing, and the
net spends its round arguing about belts rather than about the same illegal lift
at thirty different heights.

**A tap is a splitter standing on a committed run** (R-M3-7, and the DSP
junction shape of §1.15).  The first sink of a net is routed from its sources;
every later sink starts from the SIDE node of a legal tap on the tree already
committed, or from a source no belt has left yet, whichever the kernel finds
cheaper -- both are in one start set, so nothing here chooses.  A source left
over once every sink is fed runs the other way and merges in.  The attachment is
then stood on the tap node with its through ports one grid step back and forward
along the run, the run is laid as two pieces either side of it, and the tap
branch leaves by the side port that faces it.  Nothing in
:mod:`~flab2bp.sfy.layout.realise` had to change for that: a
:class:`~...realise.Terminal` of kind ``"tap"`` is a port on an attachment and
is laid to exactly like a port on a machine.

**The designer wall may hand the same item over twice** (R-M3-5, and the
controller's ruling of 2026-09-15).  A machine port is one place one belt
starts, and using it spends it; the boundary is not.  What is outside the
designer can hand the item over at two places as easily as at one, so a sink
with no tap to start from -- which is every later sink of a wall-fed net whose
first belt in is too short to cut open, and the packer stands a wall-fed port as
close to the wall as its apron allows -- takes an entry of its own.  A tap is
still asked FIRST, because it is one belt fewer; the wall is the fallback, and
:attr:`RoutedTree.boundaries` records every crossing so a build's description
can say how many belts to bring to it and where.  A source that cannot merge
into the exit tree gets its own way out for the same reason.  Each crossing is
its own belt carrying only the flow of its connected component. Boundary rates
are balanced after the machine branches are known; any imported share still
owed to a machine-rooted component is merged in before the tree is built.

**Tiers come after the tree, from what each PIECE carries** (R-M3-7).  A trunk
above a tap carries its own sink's draw plus everything the tap feeds, so the
belt classes cannot be chosen until the whole tree is known; ``belt_class_for``
is asked once per piece with that piece's own rate, and a rate past the fastest
belt the spec funds refuses ``run exceeds the belt ceiling`` through
:class:`~flab2bp.sfy.layout.grid_nets.NetError`.

**Nothing here searches cells** (global constraint 7).  Every path comes back
from :func:`~flab2bp.sfy.layout.router.route_net`, and the Python above it works
on nets, paths, taps and the router's own results.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction

from flab2bp.layout.budget import WorkBudget, expired
from flab2bp.layout.geometric_motion import MotionWitness
from flab2bp.sfy.geometry import box_bounds, port_forward, world_port
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import (
    LATTICE_TOUCH_CM,
    TAP_CLEAR_NODES,
    GridNet,
    NetError,
    _node_on_ray,
    facing_for,
    snapped,
    tap_nodes,
)
from flab2bp.sfy.layout.lattice import (
    GROUND_LEVEL,
    BoxBounds,
    Lattice,
    Node,
    Occupancy,
    _belt_boxes,
    belt_levels,
)
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS
from flab2bp.sfy.layout.model import AttachmentObj, Pose, Vector, belt_ends
from flab2bp.sfy.layout.motion import (
    CutProof,
    FixedPathSummary,
    MotionPreparationExhausted,
    MotionProfile,
    PathProof,
    lift_heights,
    motion_profile,
)
from flab2bp.sfy.layout.realise import (
    Realised,
    RealiseError,
    Terminal,
    realise,
)
from flab2bp.sfy.layout.router import (
    BudgetCause,
    Routed,
    RouteFailureKind,
    TransitionTable,
    route_net,
)
from flab2bp.sfy.layout.transitions import FLAT_STEPS, incline_run_nodes, sfy_transitions
from flab2bp.sfy.layout.validate import (
    BELT_CLEARANCE_HALF_WIDTH_CM,
    TOUCH_CM,
    lift_box,
)
from flab2bp.sfy.registry import Port, Registry

__all__ = [
    "BLAME_WEIGHT",
    "LIFT_RETRIES",
    "PRESSURE_BASE",
    "PRESSURE_GROWTH",
    "REALISE_RETRIES",
    "RRR_MAX",
    "RoutedTree",
    "RoutingOutcome",
    "route_all",
]

RRR_MAX = 8
"""How many rip-up-and-reroute rounds one call may run.

The DSP router's own count (``routing_domain.RRR_MAX``), kept because the two
loops negotiate the same way: this project's, not the game's.
"""

BLAME_WEIGHT = 40.0
"""What one refusal charges the congestion history at a node it names.

Ours.  It has to dominate the price of going round: a flat step costs ``1``
before congestion and a node's congestion price is ``pressure * history``, so at
the first round's pressure one charge is worth tens of steps of detour -- which
is the point, because a corner the realiser refused will be refused again and a
belt that walled another net in has to be worth moving.
"""

LIFT_RETRIES = 3
"""How many TRIES one net gets against an occupied lift column, in one round.

The ruling's count, and a count rather than a convergence: Task 5 measured that
closing one lift's bottom node in an open corridor moves the lift one node
sideways and proposes it again, so there is no fixed point to iterate to.  Three
is the whole allowance -- the first query and two more -- after which the net is
stranded for the round and the next round's pressure and history do the arguing.
"""

REALISE_RETRIES = 3
"""How many TRIES one net gets against a path the build refused, in one round.

Covers ``corner``, ``leg``, ``lift`` and ``stub`` together -- and the shadow
correction, which is the one kind that charges nothing -- for the same reason
:data:`LIFT_RETRIES` is a count: the blamed nodes are closed and the next query
usually returns the same shape one node over.
"""

PRESSURE_BASE = 0.5
PRESSURE_GROWTH = 1.6
"""The congestion schedule, round by round: ``0.5 * 1.6 ** round``.

The DSP router's own (``routing_domain:9943``), and the reason it is a geometric
ramp rather than a constant is negotiation: the first rounds let a net take the
cheap floor and find out who else wanted it, and the last rounds make a
contested node so expensive that only the net with nowhere else to go pays for
it.
"""


@dataclass(frozen=True, slots=True)
class RoutedTree:
    """One net's committed tree: a path per sink, and where it was tapped.

    ``paths`` is one path per sink and one more per source that had to merge in;
    ``taps`` is ``(the node an attachment stands on, the index in ``paths`` of
    the path it stands on)``, one per branch that starts or ends at one.

    ``boundaries`` is where this tree really crosses the designer wall:
    ``(the index in ``paths``, the node it crosses at)``, one entry per branch
    END that stands on a wall line.  There is usually one, and there is more
    than one where a later sink had no tap to start from and took an entry of
    its own (R-M3-5) -- which is a thing a build's description has to be able to
    say, because a reader who pastes the blueprint has to know how many belts to
    bring to it and where.
    """

    net: GridNet
    paths: tuple[tuple[Node, ...], ...]
    taps: tuple[tuple[Node, int], ...]
    boundaries: tuple[tuple[int, Node], ...]
    #: Piece witnesses follow each branch's actual attachment cuts.
    proofs: tuple[tuple[PathProof, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    """What one call to :func:`route_all` established, and what it cost.

    ``stranded`` carries the last search's evidence beside each net that has no
    tree.  Where the SEARCH refused, that ``Routed`` has no path and says which
    kind of refusal it was; where the REALISER refused, the search did return a
    path and it is kept as it stands, because "a path was found and the build
    would not have it" is different evidence from "there is no path" and the
    packer reads them differently.

    ``blame`` is what this call learned about the floor, node by node.  Most of
    it is also in ``occupancy.history``, which is what the kernel prices: a wall
    a sealed pocket censused, a corner the realiser refused, a lift whose column
    was not free.  The exception is deliberate and :meth:`_Run.note` states it --
    the belt standing in a stranded net's own doorway is named here for the
    packer without being priced for the router.
    """

    trees: tuple[RoutedTree, ...]
    stranded: tuple[tuple[GridNet, Routed], ...]
    realised: tuple[Realised, ...]
    rounds: int
    work: int
    blame: Mapping[Node, float]


# --- the pieces one round works with ---------------------------------------


@dataclass(frozen=True, slots=True)
class _Endpoint:
    """One place a net's flow really starts or ends, and what it moves there.

    A machine port is one endpoint and one belt leaves it.  A designer wall is
    ALSO one endpoint, with one terminal per node of the wall line -- R-M3-5
    lets the stream cross at any ``X`` -- but it is one a net may use more than
    once: the boundary is where the build meets what is outside it, and what is
    outside it can hand the same item over at two places as easily as at one.
    :attr:`wall` is which kind this is, and it is the one thing that decides
    whether using an endpoint spends it.
    """

    terminals: tuple[Terminal, ...]
    rate: Fraction

    @property
    def wall(self) -> bool:
        """Whether this is the designer boundary rather than one machine's port."""
        return bool(self.terminals) and self.terminals[0].kind == "wall"

    @property
    def nodes(self) -> tuple[Node, ...]:
        return tuple(terminal.node for terminal in self.terminals)

    @property
    def opened(self) -> tuple[Node, ...]:
        """The nodes inside a machine's own box this endpoint may use -- R-M3-2 (d)."""
        return tuple(node for terminal in self.terminals for node in terminal.reach)

    def at(self, node: Node) -> Terminal:
        return next(terminal for terminal in self.terminals if terminal.node == node)


@dataclass(frozen=True, slots=True)
class _Tap:
    """A place a committed run may be cut open, and the node beside it.

    ``node`` is where the attachment stands and ``side`` the node its side port
    reaches, which is the node a tap branch really starts or ends on.  ``side``
    is shadowed by the run itself -- two belts a grid step apart lap -- so it is
    offered to one query at a time through ``opened`` and never made passable in
    the shared occupancy.
    """

    branch: int
    index: int
    node: Node
    side: Node
    cut: CutProof | None = None
    piece: int = 0
    stood: _Stood | None = None
    terminal: Terminal | None = None


@dataclass(slots=True)
class _Branch:
    """One path of one net's tree, and what it is joined to at each end."""

    path: tuple[Node, ...]
    #: Where it starts, unless it starts at a tap, when the attachment's side
    #: port is the source and is not known until the attachment is stood.
    source: Terminal | None
    #: Where it ends, unless it merges into another branch.
    sink: Terminal | None
    #: The one rate this branch's own terminal fixes, and WHICH end it fixes is
    #: what ``merging`` says: a branch that feeds a sink is pinned at its far
    #: end, by that sink's draw; a branch that carries a spare source into the
    #: tree is pinned at its NEAR end, by that source's whole output.  Which end
    #: is pinned is which end :func:`_flows` counts from.
    pinned: Fraction
    tap: _Tap | None
    merging: bool
    motion: MotionWitness | None = None
    proofs: tuple[PathProof, ...] = ()
    summaries: tuple[FixedPathSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class _Stood:
    """One conveyor attachment standing on a tap, and the three ports in use."""

    obj: AttachmentObj
    through_in: Port
    through_out: Port
    side: Port


@dataclass(frozen=True, slots=True)
class _Try:
    """One attempt at one net: the tree, or why there is none and who to charge."""

    tree: RoutedTree | None = None
    realised: tuple[Realised, ...] = ()
    columns: tuple[tuple[Node, Node], ...] = ()
    routed: Routed | None = None
    #: ``""`` where there is a tree; else ``"route"``, ``"realise"``, ``"lift"``
    #: or ``"shadow"`` -- a path that stood in its own trunk's shadow, which is
    #: corrected rather than priced and so is charged nothing.
    failure: str = ""
    #: What to charge the congestion history: the nodes the refusal NAMED.
    nodes: tuple[Node, ...] = ()
    #: What to close for the next query, which is not always the same thing --
    #: see :func:`_shut_column`.
    shut: tuple[Node, ...] = ()
    #: A realised tap could instead be discharged by an independent wall crossing.
    wall_fallback: bool = False


@dataclass(frozen=True, slots=True)
class _Physical:
    """One hard-object box or conservative belt-segment hull, not a node shadow.

    Only a belt's directly linked END segment gets a lift-contact exemption;
    later segments of that same belt can still collide with the shaft.
    """

    object_id: int
    bounds: BoxBounds
    contacts: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _Played:
    """One whole round, and the key the best of them is chosen by."""

    trees: tuple[RoutedTree, ...]
    stranded: tuple[tuple[GridNet, Routed], ...]
    realised: tuple[Realised, ...]
    columns: tuple[tuple[int, Node, Node], ...]
    physical: tuple[tuple[int, tuple[_Physical, ...]], ...]
    length_cm: float

    @property
    def key(self) -> tuple[int, float]:
        return (len(self.stranded), self.length_cm)


@dataclass(slots=True)
class _Run:
    """The mutable state one :func:`route_all` call rebinds while it runs.

    ``budget`` is the caller's ledger, held BY REFERENCE: the searches spend
    against it and a copy would lose the charge.
    """

    occupancy: Occupancy
    lattice: Lattice
    measures: Measures
    registry: Registry
    budget: WorkBudget
    deadline: float | None
    ids: Iterator[int]
    belt_class_for: Callable[[Fraction, str], str]
    lift_class: str
    transitions: TransitionTable
    #: How many straight nodes a tap keeps on either side of itself.
    clear: int
    #: Occupancy ids for the paths this call commits, apart from the net ids so
    #: that nothing reading ``occupancy.owner`` can mistake one for the other.
    stakes: Iterator[int]
    pressure: float = PRESSURE_BASE
    work: int = 0
    blame: dict[Node, float] = field(default_factory=dict)
    #: Net id to the occupancy ids its paths and lift columns are committed under.
    staked: dict[int, list[int]] = field(default_factory=dict)
    #: The other way round: which net one committed occupancy id belongs to.
    owners: dict[int, int] = field(default_factory=dict)
    #: Every node a committed PATH stands on, and whose it is.
    held: dict[Node, int] = field(default_factory=dict)
    #: Actual realised objects, cached once per admitted net, released with its stakes.
    physical: dict[int, tuple[_Physical, ...]] = field(default_factory=dict)
    profile: MotionProfile | None = None
    proofs: dict[int, tuple[tuple[PathProof, ...], ...]] = field(default_factory=dict)

    # -- the search ---------------------------------------------------------

    def query(
        self,
        starts: Collection[Terminal],
        goals: Collection[Terminal],
        opened: Collection[Node],
        closed: Collection[Node],
    ) -> Routed:
        """One net's search, charged to the ledger and to the history.

        The wall a sealed pocket names is charged here rather than through
        ``route_net``'s own ``blame`` dict, so that one charge reaches both the
        history the kernel prices and the blame the packer reads: two tallies
        that could drift apart would be two different stories about the same
        refusal.
        """
        if self.profile is None:
            if expired(self.deadline):
                return _out_of_time()
            if self.budget.left is not None:
                if self.budget.left <= 0:
                    return Routed(None, RouteFailureKind.BUDGET, (), 0, BudgetCause.ALLOWANCE)
                self.budget.left -= 1
            self.work += 1
            self.profile = motion_profile(
                self.lattice, self.measures, self.registry, self.lift_class, self.transitions
            )
        routed = route_net(
            self.occupancy,
            starts=tuple(terminal.node for terminal in starts),
            goals=tuple(terminal.node for terminal in goals),
            opened=tuple(opened),
            closed=tuple(closed),
            pressure=self.pressure,
            budget=self.budget,
            deadline=self.deadline,
            transitions=self.transitions,
            profile=self.profile,
            sources=starts,
            sinks=goals,
        )
        self.work += routed.work
        if routed.wall:
            self.charge(routed.wall)
        return routed

    def charge(self, nodes: Collection[Node]) -> None:
        """Price ``nodes`` for the next query, and note that this call did.

        Both halves together: the congestion history the kernel reads, and the
        blame the packer reads.  Everything charged here was PROVED by a search
        or by the builder -- a wall a sealed pocket censused, a corner the
        realiser refused, a lift whose column was not free -- so pricing it is
        telling the next query something true about the floor.
        """
        for node in nodes:
            if not self.lattice.holds(node):
                continue
            self.occupancy.history[self.lattice.index(node)] += BLAME_WEIGHT
        self.note(nodes)

    def note(self, nodes: Collection[Node]) -> None:
        """Record blame for the PACKER without pricing it for the router.

        The asymmetry is deliberate and it is this module's one departure from
        "blame and history are the same number".  A net walled into its own port
        is evidence about where the MACHINES stand, and moving one is the
        packer's remedy (Task 8); pricing the same nodes for the router would
        make the node the walled-in net itself has to use expensive, and the one
        net that cannot do without it is the one that would then avoid it.
        """
        for node in nodes:
            if self.lattice.holds(node):
                self.blame[node] = self.blame.get(node, 0.0) + BLAME_WEIGHT

    # -- what is standing ---------------------------------------------------

    def stake(self, net_id: int, path: Sequence[Node]) -> None:
        """Commit ``path`` for ``net_id``, in the occupancy and in ``held``.

        The two are written together, always here, because an occupancy and a
        node-to-net map that disagreed would be a router that quietly routes
        through a committed belt.
        """
        stake = next(self.stakes)
        self.occupancy.commit(stake, path)
        self.staked.setdefault(net_id, []).append(stake)
        self.owners[stake] = net_id
        for node in path:
            self.held[node] = net_id

    def release(self, net_id: int) -> None:
        self.proofs.pop(net_id, None)
        self.physical.pop(net_id, None)
        for stake in self.staked.pop(net_id, []):
            self.occupancy.rip_up(stake)
            self.owners.pop(stake, None)
        self.held = {node: owner for node, owner in self.held.items() if owner != net_id}

    def foreign(self, node: Node, net_id: int) -> bool:
        """Whether another net's belt is standing ON ``node`` or a step from it.

        ``owner`` cannot answer this: a node two runs both deny records the
        FIRST of them, so a node this net's own trunk shadows reads back as this
        net's however many others shadow it too.  What can answer it is the one
        thing this loop keeps itself -- which net each committed PATH node
        belongs to -- because a node a grid step from a run is exactly a node in
        that run's shadow (R-M3-2).

        Stricter than R-M3-2 by one node, and ours: the rule keeps the node
        BEYOND a run's last node free for a belt crossing it, and this refuses
        that node too rather than stand a splitter's side belt on the one node
        the rule was holding open for somebody else.
        """
        if self.held.get(node, net_id) != net_id:
            return True
        return any(
            self.held.get((node[0] + dx, node[1] + dy, node[2]), net_id) != net_id
            for dx, dy in FLAT_STEPS
        )

    def blocker(self, node: Node) -> int | None:
        """Which net is holding or SHADOWING ``node``, if any.

        The shadow counts: two belts a grid step apart lap, so a run beside a
        node denies it as surely as a run on it, and a net asking who is in its
        way wants the same answer either way.
        """
        if not self.lattice.holds(node):
            return None
        stake = self.occupancy.owner.get(self.lattice.index(node))
        return None if stake is None else self.owners.get(stake)

    def release_all(self) -> None:
        for net_id in tuple(self.staked):
            self.release(net_id)

    def available(self, net_id: int, node: Node) -> bool:
        """Whether ``node`` is free for ``net_id`` -- a lift's column asks this.

        A node the WORLD denies is never available; a node another net is
        holding or shadowing is not either; a node this net already staked is,
        because a lift standing in its own net's belt is the belt that arrives
        at it.
        """
        if not self.lattice.holds(node):
            return False
        index = self.lattice.index(node)
        if self.occupancy.base[index] == 0:
            return False
        owner = self.occupancy.owner.get(index)
        return owner is None or owner in self.staked.get(net_id, ())

    def tap_sites(
        self, branches: Sequence[_Branch], net: GridNet, *, merging: bool = False
    ) -> dict[Node, _Tap]:
        """Every node a belt of this net may leave the tree from -- R-M3-7.

        Keyed by the actual attachment side port. ``tap_access`` moves the
        search endpoint one normal step outside the trunk's shadow; the tap
        itself remains where the attachment stands and no belt ever stands.

        A side node is offered only when it is THIS net's to open.  The world
        may deny it; another belt may be standing on it; and -- the case the
        first cut of this module missed -- another net's belt may be SHADOWING
        it, which denies it just as surely, because two belts a grid step apart
        lap by 58 cm.  The trunk's own shadow is the one that does not count:
        that is what a tap is.
        """
        paths = [branch.path for branch in branches]
        breaks = {branch.tap.node for branch in branches if branch.tap is not None}
        for terminal in (*net.sources, *net.sinks):
            breaks.update(terminal.reach)
            breaks.add(terminal.node)
        where: dict[Node, tuple[int, int]] = {}
        for index, path in enumerate(paths):
            for position, node in enumerate(path):
                where.setdefault(node, (index, position))
        out: dict[Node, _Tap] = {}
        for node in tap_nodes(paths, self.lattice, clear=self.clear, breaks=breaks):
            index, position = where[node]
            path = paths[index]
            ahead = path[position + 1]
            across = (node[1] - ahead[1], ahead[0] - node[0])
            for sign in (1, -1):
                side = (node[0] + sign * across[0], node[1] + sign * across[1], node[2])
                tap = _Tap(branch=index, index=position, node=node, side=side)
                stood = _stand_at(self, tap, ahead, merging=merging, object_id=-1)
                side_terminal = _tap_port(self, stood.obj, stood.side)
                side = side_terminal.node
                if not self.lattice.holds(side) or side in self.held:
                    continue
                if self.occupancy.base[self.lattice.index(side)] == 0:
                    continue
                if self.foreign(side, net.id):
                    continue
                tap = replace(tap, side=side, stood=stood)
                if branches[index].proofs:
                    if self.profile is None:
                        raise ValueError("a certified branch lost its geometry profile")
                    parent = branches[index]
                    if not parent.summaries:
                        parent.summaries = tuple(
                            FixedPathSummary(
                                proof, self.profile, budget=self.budget, deadline=self.deadline
                            )
                            for proof in parent.proofs
                        )
                    before = _tap_port(self, stood.obj, stood.through_in)
                    after = _tap_port(self, stood.obj, stood.through_out)
                    cut = None
                    piece_index = 0
                    for candidate_piece, summary in enumerate(parent.summaries):
                        try:
                            _ = summary.proof.path.index(node)
                            before_index = summary.proof.path.index(before.node)
                            after_index = summary.proof.path.index(after.node)
                        except ValueError:
                            continue
                        cut = summary.cut(before_index, after_index, before, after)
                        piece_index = candidate_piece
                        break
                    if cut is None:
                        continue
                    tap = replace(tap, cut=cut, piece=piece_index, stood=stood)
                out[side] = tap
        return out

    def tap_access(
        self,
        branches: Sequence[_Branch],
        net: GridNet,
        closed: Collection[Node],
        *,
        merging: bool = False,
    ) -> dict[Node, _Tap]:
        """Offer the free node one normal step beyond each attachment side.

        The side port is in its trunk's one-node shadow. Opening every possible
        side for a search also opens a parallel corridor and permits ramp vias
        through other candidates. Instead the query begins or ends outside that
        shadow; the chosen path retains the single normal step to the true port.
        This applies equally to splitter departures and merger approaches.
        """
        out: dict[Node, _Tap] = {}
        for side, tap in self.tap_sites(branches, net, merging=merging).items():
            if tap.stood is None:
                raise ValueError("a tap candidate lost its registry connector geometry")
            port = _side(self, tap.stood, side)
            access = (
                side[0] + round(port.facing[0]),
                side[1] + round(port.facing[1]),
                side[2],
            )
            if side in closed or access in closed or not self.lattice.holds(access):
                continue
            if not self.occupancy.flags[self.lattice.index(access)]:
                continue
            terminal = replace(port, node=access)
            out[access] = replace(tap, terminal=terminal)
        return out


# --- the loop --------------------------------------------------------------


def route_all(
    nets: Sequence[GridNet],
    occupancy: Occupancy,
    *,
    measures: Measures,
    registry: Registry,
    budget: WorkBudget,
    deadline: float | None,
    ids: Iterator[int],
    belt_class_for: Callable[[Fraction, str], str],
    lift_class: str,
) -> RoutingOutcome:
    """Route every net, negotiating congestion across rip-up rounds.

    The nets are offered the floor by what they carry, heaviest first, because a
    belt that carries the build's main product is the one whose detour costs
    most.  Every round rips ALL of them up and routes them again -- nothing is
    kept from the round before but the congestion history -- and the best round
    is the one the occupancy is left holding, so that Task 9's poles stand on
    what the returned paths really left free.

    The clock is read between rounds and between nets.  A net the clock never
    reached is stranded with a ``BUDGET``/``DEADLINE`` :class:`Routed`, which
    says "more seconds would have changed this" and nothing about the build.
    """
    lattice = occupancy.lattice
    run = _Run(
        occupancy=occupancy,
        lattice=lattice,
        measures=measures,
        registry=registry,
        budget=budget,
        deadline=deadline,
        ids=ids,
        belt_class_for=belt_class_for,
        lift_class=lift_class,
        transitions=sfy_transitions(
            lattice.n + 1,
            lift_heights(lattice, registry),
            incline_run_nodes(registry.limits, lattice.grid_cm),
        ),
        clear=TAP_CLEAR_NODES,
        stakes=_stake_ids(nets),
    )
    order = tuple(sorted(nets, key=lambda net: (-net.rate, net.id)))
    best: _Played | None = None
    rounds = 0
    for index in range(RRR_MAX):
        if expired(deadline):
            break
        rounds = index + 1
        run.pressure = PRESSURE_BASE * PRESSURE_GROWTH**index
        played = _play(run, order)
        if best is None or played.key < best.key:
            best = played
        if not played.stranded:
            break
    if best is None:
        run.release_all()
        return RoutingOutcome(
            trees=(),
            stranded=tuple((net, _out_of_time()) for net in order),
            realised=(),
            rounds=0,
            work=run.work,
            blame=dict(run.blame),
        )
    _restore(run, best)
    return RoutingOutcome(
        trees=best.trees,
        stranded=best.stranded,
        realised=best.realised,
        rounds=rounds,
        work=run.work,
        blame=dict(run.blame),
    )


def _stake_ids(nets: Sequence[GridNet]) -> Iterator[int]:
    """Occupancy ids for the paths this call commits, apart from the net ids.

    A net's tree is several committed paths and a lift column is another, so the
    occupancy is staked per PATH rather than per net; the ids start past every
    net's so that nothing reading ``occupancy.owner`` can mistake one for the
    other.
    """
    start = max((net.id for net in nets), default=0) + 1
    return iter(range(start, start + (1 << 30)))


def _play(run: _Run, order: Sequence[GridNet]) -> _Played:
    """One round: every net ripped up, then every net routed against the pressure."""
    run.release_all()
    trees: list[RoutedTree] = []
    stranded: list[tuple[GridNet, Routed]] = []
    realised: list[Realised] = []
    columns: list[tuple[int, Node, Node]] = []
    for net in order:
        if expired(run.deadline):
            stranded.append((net, _out_of_time()))
            continue
        attempt = _route_one(run, net)
        if attempt.tree is None:
            if (
                attempt.routed is None
                or attempt.routed.kind is not RouteFailureKind.MOTION_EXHAUSTED
            ):
                run.note(_in_the_doorway(run, net))
            stranded.append((net, attempt.routed if attempt.routed else _out_of_time()))
            continue
        trees.append(attempt.tree)
        realised.extend(attempt.realised)
        columns.extend((net.id, low, high) for low, high in attempt.columns)
    return _Played(
        trees=tuple(trees),
        stranded=tuple(stranded),
        realised=tuple(realised),
        columns=tuple(columns),
        physical=tuple(run.physical.items()),
        length_cm=_belt_length_cm(realised),
    )


def _in_the_doorway(run: _Run, net: GridNet) -> tuple[Node, ...]:
    """Whoever is standing right outside a stranded net's own ports.

    A sealed pocket is the only refusal that censuses a wall, and a net whose
    port is walled in rarely gets one: the movement table offers a LIFT out of
    every node, so the search leaves the pocket, wanders, and comes back as a
    lift the column check refuses rather than as an exhaustion that names
    anybody.  This is the blame that case still deserves, and it is bounded by
    the net's own terminals rather than by the size of what the search reached:
    the nodes one grid step out from a terminal and its reach, that another net
    is holding or shadowing.  A machine standing there is nobody's to move and
    is not named.

    Ours, and the reason it is here rather than in the router: the router can
    only name what its own search proved, and this is what the LOOP knows.  It
    is NOTED rather than charged -- see :meth:`_Run.note`.
    """
    named: list[Node] = []
    seen: set[Node] = set()
    for terminal in (*net.sources, *net.sinks):
        for node in (terminal.node, *terminal.reach):
            for dx, dy in FLAT_STEPS:
                beside = (node[0] + dx, node[1] + dy, node[2])
                if beside in seen:
                    continue
                seen.add(beside)
                blocker = run.blocker(beside)
                if blocker is not None and blocker != net.id:
                    named.append(beside)
    return tuple(named)


def _shut_shafts(run: _Run, net: GridNet) -> tuple[Node, ...]:
    """Every node over a machine port that a lift could land on, shut in advance.

    A port terminal's node and its reach lie INSIDE the machine's hard box
    (R-M3-2 (d)), so a lift with either end on one of them has its column inside
    that box and can never be built: the column check refuses it, every time, at
    every height.  The kernel cannot know that -- a lift row names no
    intermediate node, which is the whole reason the column is checked here --
    so left to itself it offers the move, the builder refuses it, and the net
    spends its round's whole allowance proving the same thing one level higher.

    Saying it once, per query, costs this net only the right to fly over its own
    port's column, and the nodes it gives up there are inside the machine for
    most of their height anyway.  Wall terminals stand in open floor and keep
    their shafts.
    """
    lattice = run.lattice
    out: list[Node] = []
    for terminal in (*net.sources, *net.sinks):
        if terminal.kind != "port":
            continue
        for node in (terminal.node, *terminal.reach):
            out.extend((node[0], node[1], level) for level in range(node[2] + 1, lattice.n + 1))
    return tuple(out)


def _wall_end(endpoint: _Endpoint | None, node: Node) -> Terminal:
    """The wall terminal a branch that took its own exit really ends on.

    A guard rather than a lookup: a branch only reaches here having been routed
    to the wall line, so the endpoint is there; an endpoint that is not is a
    branch routed to a boundary this net does not have, which is a fault in this
    module and not a build that cannot be laid.
    """
    if endpoint is None:
        raise NetError("data", "a branch was routed to a designer wall this net does not use")
    return endpoint.at(node)


def _fell_short(routed: Routed | None) -> bool:
    """Whether a query is worth asking again another way.

    No query at all, or one the GEOMETRY refused.  A bound is different: a
    budget or a deadline stop proves nothing about where a belt could go, and
    asking the same net a second question on a ledger that has just run out
    spends what is left of it to be told so again.
    """
    return routed is None or (routed.path is None and routed.kind is not RouteFailureKind.BUDGET)


def _in_the_shadow(walked: Sequence[Node], sides: Collection[Node]) -> tuple[Node, ...]:
    """Refuse any candidate side opened by an overlapping machine-port reach.

    Tap access no longer opens side nodes. A query still opens its machine
    reaches, however, and those must not turn another attachment's side into
    a transit node. The selected side is joined to the path only AFTER this
    check. A correction, not congestion: nothing is charged for it.
    """
    return tuple(node for node in walked if node in sides)


def _route_one(run: _Run, net: GridNet) -> _Try:
    """One net, retried within the round over what the build itself refused.

    A search refusal ends the net's round at once -- the pressure is what argues
    with another net, and arguing again at the same pressure would give the same
    answer.  A realise refusal and an occupied lift column are different: they
    are the BUILD refusing a path the search was right to offer, so the nodes
    that refused it are charged and closed and the same net asks again, a
    bounded number of times (:data:`REALISE_RETRIES`, :data:`LIFT_RETRIES`).

    A tapped tree rejected by the realiser gets one boundary alternative within
    that same retry allowance: optional taps with a corresponding wall entry
    or exit are omitted. Geometry and history still apply; only the topology
    changes, so the rejected tap's nodes need not be closed for that retry.

    The first query already has :func:`_shut_shafts` closed against it, so none
    of those tries is spent on a lift out of the net's own port -- a move the
    movement table offers and the column check can only ever refuse.
    """
    own = {
        node for terminal in (*net.sources, *net.sinks) for node in (terminal.node, *terminal.reach)
    }
    closed: set[Node] = set(_shut_shafts(run, net))
    lift_tries = 0
    realise_tries = 0
    allow_taps = True
    while True:
        before_work = run.budget.left
        before_charge = run.work
        try:
            attempt = _attempt(run, net, closed, allow_taps=allow_taps)
        except MotionPreparationExhausted as failure:
            spent = (
                0
                if before_work is None or run.budget.left is None
                else before_work - run.budget.left
            )
            attempt = _Try(
                failure="route",
                routed=Routed(
                    None,
                    RouteFailureKind.BUDGET,
                    (),
                    spent,
                    BudgetCause.DEADLINE if failure.deadline else BudgetCause.ALLOWANCE,
                ),
            )
        finally:
            if before_work is not None and run.budget.left is not None:
                run.work += max(0, before_work - run.budget.left - (run.work - before_charge))
        if attempt.tree is not None:
            return attempt
        run.release(net.id)
        run.charge(attempt.nodes)
        if attempt.failure == "route":
            return attempt
        if attempt.failure == "lift":
            lift_tries += 1
            if lift_tries >= LIFT_RETRIES:
                return attempt
        else:
            realise_tries += 1
            if realise_tries >= REALISE_RETRIES:
                return attempt
        if attempt.failure == "realise" and attempt.wall_fallback and allow_taps:
            allow_taps = False
            continue
        # A net's own port is never closed against it.  A corner at the first
        # node of a run, and a lift whose bottom stands on the port it leaves,
        # both name a node the net cannot do without; closing it would not
        # correct the query, it would refuse to route the net at all -- and the
        # refusal would come back as ``dynamic-access``, which says the port was
        # unreachable when what really happened is that this loop shut it.
        #
        # And where that leaves nothing new shut, the next query is the query
        # just asked: the same flags, the same answer.  Stop rather than spend
        # the allowance proving it.
        before = len(closed)
        closed.update(node for node in attempt.shut if node not in own)
        if len(closed) == before:
            return attempt


def _attempt(run: _Run, net: GridNet, closed: Collection[Node], *, allow_taps: bool = True) -> _Try:
    """One whole tree for one net: search every branch, then build them all.

    The search comes first and the building second, and not the other way round,
    because what a piece of belt CARRIES is not known until the tree is: a trunk
    above a tap carries its own sink's draw plus everything the tap feeds, and a
    belt laid before that is a belt laid on a guess at its tier (R-M3-7).
    """
    sources = _endpoints(net.sources, net.per_source)
    sinks = _endpoints(net.sinks, net.per_sink)
    branches: list[_Branch] = []
    unused = [index for index, source in enumerate(sources) if not source.wall]
    from_wall = next((index for index, source in enumerate(sources) if source.wall), None)
    to_wall = next((sink for sink in sinks if sink.wall), None)
    routed: Routed | None = None
    for sink in sinks:
        taps = run.tap_access(branches, net, closed) if allow_taps or from_wall is None else {}
        starts: dict[Node, int | _Tap] = {}
        opened: set[Node] = set(sink.opened)
        for index in unused:
            opened.update(sources[index].opened)
            for node in sources[index].nodes:
                starts[node] = index
        for side, tap in taps.items():
            starts[side] = tap
        sides = tuple(tap.side for tap in taps.values())
        routed = (
            run.query(_query_starts(starts, sources), sink.terminals, opened, closed)
            if starts
            else None
        )
        path = routed.path if routed is not None and routed.path is not None else ()
        stray = _in_the_shadow(path[1:], sides)
        if (_fell_short(routed) or stray) and from_wall is not None:
            # Another entry of its own.  A tap is preferred because it is one
            # belt fewer, so the wall is asked second and only where the tree
            # had no legal route from a tap.
            starts = dict.fromkeys(sources[from_wall].nodes, from_wall)
            opened = set(sink.opened)
            routed = run.query(_query_starts(starts, sources), sink.terminals, opened, closed)
            path = routed.path if routed.path is not None else ()
            stray = _in_the_shadow(path[1:], sides)
        if routed is None:
            return _Try(failure="route", routed=_nowhere_to_start())
        if routed.path is None:
            return _Try(failure="route", routed=routed)
        if stray:
            return _Try(failure="shadow", shut=stray, routed=routed)
        head = starts[path[0]]
        selected_tap = head if isinstance(head, _Tap) else None
        source = _tap_terminal(head) if isinstance(head, _Tap) else sources[head].at(path[0])
        _commit_branch(
            run,
            net,
            branches,
            routed,
            source,
            sink.at(path[-1]),
            sink.rate,
            tap=selected_tap,
            merging=False,
        )
        if not isinstance(head, _Tap) and not sources[head].wall:
            unused.remove(head)
    for index in tuple(unused):
        taps = (
            run.tap_access(branches, net, closed, merging=True)
            if allow_taps or to_wall is None
            else {}
        )
        opened = set(sources[index].opened)
        routed = (
            run.query(
                sources[index].terminals,
                tuple(_tap_terminal(tap) for tap in taps.values()),
                opened,
                closed,
            )
            if taps
            else None
        )
        path = routed.path if routed is not None and routed.path is not None else ()
        sides = tuple(tap.side for tap in taps.values())
        stray = _in_the_shadow(path[:-1], sides)
        merged = routed is not None and routed.path is not None
        if (_fell_short(routed) or stray) and to_wall is not None:
            # An exit of its own, for the same reason and in the same order: a
            # merger into the tree is one belt fewer than a second way out.
            opened = set(sources[index].opened) | set(to_wall.opened)
            routed = run.query(sources[index].terminals, to_wall.terminals, opened, closed)
            merged = False
            path = routed.path if routed.path is not None else ()
            stray = _in_the_shadow(path[:-1], sides)
        if routed is None:
            return _Try(failure="route", routed=_nowhere_to_go())
        if routed.path is None:
            return _Try(failure="route", routed=routed)
        if stray:
            return _Try(failure="shadow", shut=stray, routed=routed)
        merge_tap = taps[path[-1]] if merged else None
        sink_terminal = (
            _tap_terminal(merge_tap) if merge_tap is not None else _wall_end(to_wall, path[-1])
        )
        _commit_branch(
            run,
            net,
            branches,
            routed,
            sources[index].at(path[0]),
            sink_terminal,
            sources[index].rate,
            tap=merge_tap,
            merging=merged,
        )
        unused.remove(index)
    balances = _boundary_flows(branches, sources, sinks)
    if balances is None:
        return _Try(failure="route", routed=_nowhere_to_go())
    roots, imports = balances
    for root, rate in imports.items():
        root_source = branches[root].source
        if rate == 0 or root_source is None or root_source.kind == "wall":
            continue
        if from_wall is None:
            return _Try(failure="route", routed=_nowhere_to_start())
        # Geometry using a machine source does not discharge the wall's supply
        # obligation. Merge the missing share into THIS component, not a nearby
        # component whose own sources already cover its sinks.
        taps = {
            side: tap
            for side, tap in run.tap_access(branches, net, closed, merging=True).items()
            if roots[tap.branch] == root
        }
        if not taps:
            return _Try(failure="route", routed=_nowhere_to_go())
        wall = sources[from_wall]
        routed = run.query(
            wall.terminals, tuple(_tap_terminal(tap) for tap in taps.values()), wall.opened, closed
        )
        if routed.path is None:
            return _Try(failure="route", routed=routed)
        path = routed.path
        stray = _in_the_shadow(path[:-1], tuple(tap.side for tap in taps.values()))
        if stray:
            return _Try(failure="shadow", shut=stray, routed=routed)
        tap = taps[path[-1]]
        _commit_branch(
            run,
            net,
            branches,
            routed,
            wall.at(path[0]),
            _tap_terminal(tap),
            rate,
            tap=tap,
            merging=True,
        )
        roots.append(root)
    return _build(run, net, branches, routed)


def _tap_terminal(tap: _Tap) -> Terminal:
    if tap.terminal is None:
        raise ValueError("a query tap lost its actual connector terminal")
    return tap.terminal


def _query_starts(
    starts: Mapping[Node, int | _Tap], sources: Sequence[_Endpoint]
) -> tuple[Terminal, ...]:
    return tuple(
        _tap_terminal(origin) if isinstance(origin, _Tap) else sources[origin].at(node)
        for node, origin in starts.items()
    )


def _commit_branch(
    run: _Run,
    net: GridNet,
    branches: list[_Branch],
    routed: Routed,
    source: Terminal,
    sink: Terminal,
    rate: Fraction,
    *,
    tap: _Tap | None,
    merging: bool,
) -> None:
    """Publish the selected attachment, both parent proofs and child together."""
    if routed.path is None or routed.motion is None:
        raise ValueError("a production branch cannot commit without its motion witness")
    revised: tuple[PathProof, ...] | None = None
    if tap is not None:
        if tap.stood is None or tap.cut is None:
            raise ValueError("a production tap cannot commit without both parent cut proofs")
        object_id = next(run.ids)
        stood = replace(tap.stood, obj=replace(tap.stood.obj, id=object_id))

        def assigned(terminal: Terminal) -> Terminal:
            if terminal.port is None:
                raise ValueError("an attachment proof lost its connector")
            return replace(terminal, port=(object_id, terminal.port[1]))

        cut = replace(tap.cut, sink=assigned(tap.cut.sink), source=assigned(tap.cut.source))
        left, right = cut.pieces()
        parent = branches[tap.branch]
        revised = (*parent.proofs[: tap.piece], left, right, *parent.proofs[tap.piece + 1 :])
        if merging:
            sink = assigned(sink)
        else:
            source = assigned(source)
        tap = replace(tap, stood=stood, terminal=sink if merging else source, cut=None)
    proof = PathProof(routed.path, source, sink, routed.motion)
    branch = _Branch(
        routed.path,
        None if tap is not None and not merging else source,
        None if tap is not None and merging else sink,
        rate,
        tap,
        merging,
        routed.motion,
        (proof,),
    )
    if tap is not None and revised is not None:
        branches[tap.branch].proofs = revised
        branches[tap.branch].summaries = ()
    branches.append(branch)
    run.stake(net.id, routed.path)
    if tap is not None:
        endpoint = routed.path[-1] if merging else routed.path[0]
        run.stake(net.id, (endpoint, tap.side))


def _boundary_flows(
    branches: Sequence[_Branch], sources: Sequence[_Endpoint], sinks: Sequence[_Endpoint]
) -> tuple[list[int], dict[int, Fraction]] | None:
    """Balance wall crossings against the fixed obligations in each component.

    A wall is one FLOW with many possible crossings. Pinning every output
    crossing to that entire flow duplicates it; treating its input as merely a
    geometric fallback loses it when a local producer can reach the sink.
    Machine rates remain authoritative. A disconnected component with excess
    production but no output, or demand the external input cannot cover, is
    unroutable in this topology rather than permission to change those rates.
    """
    made = {end.terminals[0].node: end.rate for end in sources if not end.wall}
    drawn = {end.terminals[0].node: end.rate for end in sinks if not end.wall}
    roots: list[int] = []
    supply: dict[int, Fraction] = {}
    demand: dict[int, Fraction] = {}
    exits: dict[int, int] = {}
    for index, branch in enumerate(branches):
        root = roots[branch.tap.branch] if branch.tap is not None else index
        roots.append(root)
        supply.setdefault(root, Fraction(0))
        demand.setdefault(root, Fraction(0))
        if branch.source is not None and branch.source.kind != "wall":
            supply[root] += made[branch.source.node]
        if branch.sink is not None:
            if branch.sink.kind == "wall":
                exits[root] = index
            else:
                demand[root] += drawn[branch.sink.node]
    imports = {root: max(demand[root] - rate, Fraction(0)) for root, rate in supply.items()}
    if any(supply[root] > demand[root] and root not in exits for root in supply):
        return None
    remaining = next((end.rate for end in sources if end.wall), Fraction(0)) - sum(
        imports.values(), Fraction(0)
    )
    if remaining < 0:
        return None
    if remaining:
        if not exits:
            return None
        # Imported flow beyond internal consumption belongs to an output.
        # Prefer a component already entered from the wall to avoid a merger.
        root = next(iter(exits))
        for candidate in exits:
            source = branches[candidate].source
            if source is not None and source.kind == "wall":
                root = candidate
                break
        imports[root] += remaining
    for root, index in exits.items():
        rate = supply[root] + imports[root] - demand[root]
        if rate <= 0:
            return None
        branches[index].pinned = rate
    for root, rate in imports.items():
        source = branches[root].source
        if source is not None and source.kind == "wall" and rate <= 0:
            return None
    exported = sum((branches[index].pinned for index in exits.values()), Fraction(0))
    if exported != next((end.rate for end in sinks if end.wall), Fraction(0)):
        return None
    return roots, imports


def _build(run: _Run, net: GridNet, branches: Sequence[_Branch], routed: Routed | None) -> _Try:
    """The tree as belts, attachments and lifts, or which nodes refused it."""
    children: list[list[int]] = [[] for _ in branches]
    for index, branch in enumerate(branches):
        if branch.tap is not None:
            children[branch.tap.branch].append(index)
    for kids in children:
        kids.sort(key=lambda child: _tap_of(branches[child]).index)
    flows = _flows(branches, children)
    try:
        realised = _lay(run, net, branches, children, flows)
    except RealiseError as failure:
        wall_source = any(terminal.kind == "wall" for terminal in net.sources)
        wall_sink = any(terminal.kind == "wall" for terminal in net.sinks)
        return _Try(
            failure="realise",
            nodes=failure.nodes,
            shut=_shut_column(run.lattice, failure.nodes)
            if failure.cause == "lift"
            else tuple(failure.nodes),
            routed=routed,
            wall_fallback=any(
                branch.tap is not None and (wall_sink if branch.merging else wall_source)
                for branch in branches
            ),
        )
    columns = tuple((low, high) for laid in realised for low, high in laid.lift_columns)
    physical = _physical(realised, run.registry)
    blocked = _blocked_column(run, net, realised, physical)
    if blocked:
        return _Try(
            failure="lift",
            nodes=blocked,
            shut=_shut_column(run.lattice, blocked),
            routed=routed,
        )
    for low, high in columns:
        run.stake(net.id, _column(low, high))
    run.physical[net.id] = physical
    run.proofs[net.id] = tuple(branch.proofs for branch in branches)
    return _Try(
        tree=RoutedTree(
            net=net,
            paths=tuple(branch.path for branch in branches),
            taps=tuple(
                (branch.tap.node, branch.tap.branch)
                for branch in branches
                if branch.tap is not None
            ),
            boundaries=_boundaries(branches),
            proofs=tuple(branch.proofs for branch in branches),
        ),
        realised=realised,
        columns=columns,
        routed=routed,
    )


# --- what each piece of belt carries ---------------------------------------


def _flows(
    branches: Sequence[_Branch], children: Sequence[Sequence[int]]
) -> list[tuple[Fraction, ...]]:
    """What every PIECE of every branch carries, in path order -- R-M3-7.

    Items travel a branch in path order, so a junction on it CHANGES what the
    branch carries from there on: a splitter sends ``carried_in`` of its child
    away, and a merger brings ``carried_out`` of its child in.  Those deltas are
    the same however the branch is read; what differs is which end of the branch
    the arithmetic can start from, and that is the end its own terminal pins.

    A branch that feeds a sink is pinned at its FAR end -- the sink draws what it
    draws -- so it is read backwards, adding a splitter's child back on and
    taking a merger's child back off.  A branch that carries a spare source into
    the tree is pinned at its NEAR end -- the source makes what it makes -- so it
    is read FORWARDS.  Reading a merging branch backwards from its source's rate
    is what the first cut of this module did, and it is wrong the moment a
    second spare source merges into it: both of its pieces come out short by the
    child's rate, which under-tiers the belt and can go negative.

    A child is always routed after its parent, so the branches are already in an
    order where a child's answer is known before its parent needs it.
    """
    pieces: list[tuple[Fraction, ...]] = [()] * len(branches)
    for index in reversed(range(len(branches))):
        deltas = [
            pieces[child][-1] if branches[child].merging else -pieces[child][0]
            for child in children[index]
        ]
        flow = branches[index].pinned
        if branches[index].merging:
            walked = [flow]
            for delta in deltas:
                flow += delta
                walked.append(flow)
            pieces[index] = tuple(walked)
            continue
        walked = [flow]
        for delta in reversed(deltas):
            flow -= delta
            walked.append(flow)
        pieces[index] = tuple(reversed(walked))
    return pieces


# --- standing the attachments and laying the belts -------------------------


def _lay(
    run: _Run,
    net: GridNet,
    branches: Sequence[_Branch],
    children: Sequence[Sequence[int]],
    flows: Sequence[Sequence[Fraction]],
) -> tuple[Realised, ...]:
    """Every branch as belts, cut at the attachments its children stand on."""
    stood: dict[int, _Stood] = {}
    for kids in children:
        for child in kids:
            stood[child] = _stand(run, branches, child)
    out: list[Realised] = []
    for holder in stood.values():
        out.append(
            Realised(belts=(), attachments=(holder.obj,), lifts=(), links=(), lift_columns=())
        )
    for index, branch in enumerate(branches):
        cuts = [_tap_of(branches[child]).index for child in children[index]]
        pieces = (
            [proof.path for proof in branch.proofs] if branch.proofs else _pieces(branch.path, cuts)
        )
        joins = [stood[child] for child in children[index]]
        for position, piece in enumerate(pieces):
            proof = branch.proofs[position] if branch.proofs else None
            source = (
                proof.source
                if proof is not None
                else (
                    _through(run, joins[position - 1], piece[0], leaving=True)
                    if position
                    else _start(run, branch, stood.get(index))
                )
            )
            sink = (
                proof.sink
                if proof is not None
                else (
                    _through(run, joins[position], piece[-1], leaving=False)
                    if position < len(joins)
                    else _end(run, branch, stood.get(index))
                )
            )
            rate = flows[index][position]
            out.append(
                realise(
                    piece,
                    source=source,
                    sink=sink,
                    lattice=run.lattice,
                    measures=run.measures,
                    registry=run.registry,
                    belt_class=run.belt_class_for(rate, net.item_id),
                    lift_class=run.lift_class,
                    item_id=net.item_id,
                    rate=rate,
                    ids=run.ids,
                    motion=branch.proofs[position].motion if branch.proofs else branch.motion,
                )
            )
    return tuple(out)


def _start(run: _Run, branch: _Branch, stood: _Stood | None) -> Terminal:
    """Where a branch's first piece begins: its own source, or the tap it leaves."""
    if branch.source is not None:
        return branch.source
    if stood is None or branch.tap is None:
        raise RealiseError("stub", (), "a branch with no source and no tap has no beginning")
    return replace(_side(run, stood, branch.tap.side), node=branch.path[0])


def _end(run: _Run, branch: _Branch, stood: _Stood | None) -> Terminal:
    """Where a branch's last piece ends: its own sink, or the merger it feeds."""
    if branch.sink is not None:
        return branch.sink
    if stood is None or branch.tap is None:
        raise RealiseError("stub", (), "a branch with no sink and no tap has no end")
    return replace(_side(run, stood, branch.tap.side), node=branch.path[-1])


def _stand(run: _Run, branches: Sequence[_Branch], child: int) -> _Stood:
    """The splitter or merger one branch joins the tree by, turned to the run.

    Yaw is the flow's own heading at the tap node, which puts the attachment's
    through ports one grid step back and forward along the run and its two side
    ports on the two nodes across it; which of those two the branch leaves by is
    read off where the branch really goes, not named here.
    """
    branch = branches[child]
    tap = _tap_of(branch)
    if tap.stood is not None:
        if tap.stood.obj.id < 0:
            stood = replace(tap.stood, obj=replace(tap.stood.obj, id=next(run.ids)))
            branch.tap = replace(tap, stood=stood)
            return stood
        return tap.stood
    ahead = branches[tap.branch].path[tap.index + 1]
    return _stand_at(run, tap, ahead, merging=branch.merging, object_id=next(run.ids))


def _stand_at(run: _Run, tap: _Tap, ahead: Node, *, merging: bool, object_id: int) -> _Stood:
    """Read attachment ports without emitting an object for a candidate tap."""
    flow = (ahead[0] - tap.node[0], ahead[1] - tap.node[1])
    class_name = MERGER_CLASS if merging else SPLITTER_CLASS
    buildable = run.registry.buildables.get(class_name)
    if buildable is None:
        raise NetError("data", f"the registry does not describe {class_name}")
    here = run.lattice.world(tap.node)
    pose = Pose(here[0], here[1], here[2], math.degrees(math.atan2(flow[1], flow[0])))
    obj = AttachmentObj(id=object_id, class_name=class_name, pose=pose)
    ports = [port for port in buildable.ports if port.kind == "belt"]
    left = (-flow[1], flow[0])
    on_left = (tap.side[0] - tap.node[0], tap.side[1] - tap.node[1]) == left
    sign = 1.0 if on_left else -1.0
    # A splitter takes one belt in and sends three out, a merger the other way
    # round, so which direction the SIDE port has is the class's own answer and
    # not a third thing to decide here.
    side_direction = "input" if merging else "output"
    return _Stood(
        obj=obj,
        through_in=_pick(ports, class_name, "input", lambda t: t[0] < -LATTICE_TOUCH_CM, _straight),
        through_out=_pick(
            ports, class_name, "output", lambda t: t[0] > LATTICE_TOUCH_CM, _straight
        ),
        side=_pick(
            ports,
            class_name,
            side_direction,
            lambda t: t[1] * sign > LATTICE_TOUCH_CM,
            lambda t: abs(t[0]) <= LATTICE_TOUCH_CM,
        ),
    )


def _straight(translation: Vector) -> bool:
    return abs(translation[1]) <= LATTICE_TOUCH_CM


def _pick(
    ports: Sequence[Port],
    class_name: str,
    direction: str,
    along: Callable[[Vector], bool],
    across: Callable[[Vector], bool],
) -> Port:
    """One of an attachment's four belt ports, by where it stands and which way
    it runs.

    By POSITION rather than by name -- the game's own splitter and merger put
    their through ports on ``+-X`` and their side ports on ``+-Y``, and a port
    name is not a source for anything (global constraint 1) -- and by
    ``direction``, which is read out of the same registry entry.  Both, because
    position alone would wire a belt into the wrong end of an attachment the
    moment a class laid its ports out differently, and ``ports.direction`` is
    the check that would then fail.
    """
    for port in ports:
        if port.direction == direction and along(port.translation) and across(port.translation):
            return port
    raise NetError(
        "data",
        f"{class_name} has no belt {direction} port where an attachment turn needs one",
    )


def _through(run: _Run, stood: _Stood, node: Node, *, leaving: bool) -> Terminal:
    """The terminal of an attachment's through port, held to the node it stands on."""
    port = stood.through_out if leaving else stood.through_in
    return _port_terminal(run, stood.obj, port, node)


def _side(run: _Run, stood: _Stood, node: Node) -> Terminal:
    return _port_terminal(run, stood.obj, stood.side, node)


def _tap_port(run: _Run, obj: AttachmentObj, port: Port) -> Terminal:
    """Actual connector and first outward lattice node, including off-grid ports."""
    transform = obj.pose.transform()
    world = world_port(transform, port)
    facing = facing_for(port_forward(transform, port), run.lattice, port.name)
    node = _node_on_ray(world, facing, run.lattice, port.name)
    return _port_terminal(run, obj, port, node)


def _port_terminal(run: _Run, obj: AttachmentObj, port: Port, node: Node) -> Terminal:
    """One attachment port as a terminal, checked against the lattice it claims.

    An attachment's ports stand one grid step out with the shipped registry, so
    a port's own node is the node beside the one the attachment stands on.  That
    is READ rather than assumed: a registry whose attachments reached further
    would cut the run somewhere else, and a terminal that claimed the wrong node
    would hand the realiser a path that does not end where it says it does.
    """
    transform = obj.pose.transform()
    world = snapped(world_port(transform, port), node, run.lattice)
    from flab2bp.sfy.layout.realise import _stub

    facing = facing_for(port_forward(transform, port), run.lattice, port.name)
    _ = _stub(world, run.lattice.world(node), facing, end=0, at=node)
    return Terminal(
        node=node,
        world=world,
        facing=facing,
        port=(obj.id, port.name),
        kind="tap",
    )


def _pieces(path: Sequence[Node], cuts: Sequence[int]) -> list[tuple[Node, ...]]:
    """One branch's path, cut at the nodes its attachments stand on.

    The attachment occupies the node itself and its two through ports stand on
    the nodes either side, so the piece before a cut ends one node short of it
    and the piece after starts one node past it.

    Cuts come from ``tap_nodes`` with ``clear >= TAP_CLEAR_NODES`` (three).
    A ramp's flat half may belong to a straight run, but that clearance keeps
    both through-port nodes off its source, via and landing in either path
    direction. Slicing therefore leaves every ramp whole inside one piece;
    it does not need another ramp decoder here.

    A piece with no nodes in it is refused rather than built: two attachments
    would be standing on adjacent nodes with no belt between them, which
    :func:`~flab2bp.sfy.layout.grid_nets.tap_nodes`'s ``clear`` is there to
    prevent.  It cannot happen with a ``clear`` of two or more, and it is
    checked because if it ever does the loop should strand the net rather than
    fall over on an empty tuple.
    """
    out: list[tuple[Node, ...]] = []
    start = 0
    for cut in cuts:
        out.append(tuple(path[start:cut]))
        start = cut + 1
    out.append(tuple(path[start:]))
    for index, piece in enumerate(out):
        if not piece:
            raise RealiseError(
                "leg",
                (path[cuts[min(index, len(cuts) - 1)]],),
                "two attachments would stand on this run with no belt between them",
            )
    return out


# --- the lift columns ------------------------------------------------------


def _physical(realised: Sequence[Realised], registry: Registry) -> tuple[_Physical, ...]:
    """Cache physical bounds once; curved belt hulls remain conservative AABBs."""
    links: dict[tuple[int, str], int] = {}
    for laid in realised:
        for link in laid.links:
            links[link.a] = link.b[0]
            links[link.b] = link.a[0]
    boxes: list[_Physical] = []
    for laid in realised:
        for belt in laid.belts:
            entry, exit_end = belt_ends(registry, belt.class_name)
            last = len(belt.points) - 2
            for index, bounds in enumerate(_belt_boxes(belt)):
                contacts: list[int] = []
                for at_end, port in ((index == 0, entry), (index == last, exit_end)):
                    other = links.get((belt.id, port))
                    if at_end and other is not None:
                        contacts.append(other)
                boxes.append(_Physical(belt.id, bounds, tuple(contacts)))
        for obj in laid.attachments:
            transform = obj.pose.transform()
            for box in registry.buildables[obj.class_name].clearance:
                if not box.soft:
                    boxes.append(_Physical(obj.id, box_bounds(box, transform)))
        for lift in laid.lifts:
            column_box = lift_box(lift, registry)
            centre, reach = column_box.centre, column_box.reach
            column_bounds: BoxBounds = (
                (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
                (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
            )
            boxes.append(_Physical(lift.id, column_bounds))
    return tuple(boxes)


def _overlap(left: BoxBounds, right: BoxBounds) -> bool:
    return all(
        min(left[1][axis], right[1][axis]) - max(left[0][axis], right[0][axis]) > TOUCH_CM
        for axis in range(3)
    )


def _blocked_column(
    run: _Run,
    net: GridNet,
    realised: Sequence[Realised],
    physical: tuple[_Physical, ...],
) -> tuple[Node, ...]:
    """Refuse occupied centre shafts or physical overlap, never overlap of shadows.

    Flags already expand world geometry by a belt's clearance. Expanding a lift
    to virtual belt centres and consulting those flags charges that clearance
    twice. Compare the retained physical boxes instead. Node shadows are only
    a fail-closed fallback for foreign stakes with no registered geometry.
    """
    own_bounds = {part.object_id: part.bounds for part in physical}
    own_stakes = run.staked.get(net.id, ())
    half = run.lattice.designer.half_cm
    for laid in realised:
        for lift, (low, high) in zip(laid.lifts, laid.lift_columns, strict=True):
            bounds = own_bounds[lift.id]
            if any(
                not run.available(net.id, node)
                for node in _column(low, high)
                if node not in (low, high)
            ):
                return (low, high)
            if (
                any(
                    bounds[0][axis] < -half - TOUCH_CM or bounds[1][axis] > half + TOUCH_CM
                    for axis in (0, 1)
                )
                or bounds[0][2] < run.lattice.grid_cm - TOUCH_CM
                or bounds[1][2] > run.lattice.n * run.lattice.grid_cm + TOUCH_CM
            ):
                return (low, high)
            if any(_overlap(bounds, other) for other in run.occupancy.static_bounds):
                return (low, high)
            if any(
                _overlap(bounds, part.bounds)
                for owner, parts in run.physical.items()
                if owner != net.id
                for part in parts
            ):
                return (low, high)
            if any(
                _overlap(bounds, part.bounds)
                for part in physical
                if part.object_id != lift.id and lift.id not in part.contacts
            ):
                return (low, high)
            for node in _box_nodes(run.lattice, *bounds):
                index = run.lattice.index(node)
                for stake in run.occupancy._claims.get(index, ()):
                    if stake not in own_stakes and run.owners.get(stake) not in run.physical:
                        return (low, high)
    return ()


def _shut_column(lattice: Lattice, ends: Sequence[Node]) -> tuple[Node, ...]:
    """What a refused lift closes: its ends, and every landing above its bottom.

    Closing the two ends alone is not enough, and the reason is the movement
    table: a lift is one row per legal height, so a search whose lift was
    refused simply picks the next height up and proposes the SAME shaft one
    level taller, walking the ladder to the ceiling while the retries run out.
    Every landing above the bottom goes with the ends, so that one refusal
    answers the whole family of lifts out of that node rather than one of them.

    Bounded by the lattice's own height and by nothing else, and closed for ONE
    query: the shared occupancy never sees it.
    """
    if len(ends) != 2:
        return tuple(ends)
    low, high = sorted(ends, key=lambda node: node[2])
    if (low[0], low[1]) != (high[0], high[1]):
        return tuple(ends)
    return (*ends, *((low[0], low[1], level) for level in range(low[2] + 1, lattice.n + 1)))


def _boundaries(branches: Sequence[_Branch]) -> tuple[tuple[int, Node], ...]:
    """Every branch end that stands on the designer wall, in branch order.

    One entry per crossing rather than per branch: a branch that came in at the
    wall and went out at it again would be two, which is what a description
    counting belts at the boundary wants.
    """
    out: list[tuple[int, Node]] = []
    for index, branch in enumerate(branches):
        if branch.source is not None and branch.source.kind == "wall":
            out.append((index, branch.path[0]))
        if branch.sink is not None and branch.sink.kind == "wall":
            out.append((index, branch.path[-1]))
    return tuple(out)


def _column(low: Node, high: Node) -> tuple[Node, ...]:
    """Every node of a lift's shaft, both ends included."""
    return tuple((low[0], low[1], level) for level in range(low[2], high[2] + 1))


def _box_nodes(lattice: Lattice, low: Vector, high: Vector) -> list[Node]:
    """Every node whose belt box meets the world box ``[low, high]``.

    Used only to fail closed on unregistered foreign claims. World flags are
    already expanded by a belt's clearance and MUST NOT be read through this
    second expansion; registered geometry is compared as physical bounds.
    """
    grid, half = lattice.grid_cm, lattice.designer.half_cm
    across = BELT_CLEARANCE_HALF_WIDTH_CM
    xs = _meeting(low[0], high[0], across, -half, grid, lattice.n)
    ys = _meeting(low[1], high[1], across, -half, grid, lattice.n)
    ks = belt_levels(low[2], high[2], grid)
    ks = range(max(ks.start, GROUND_LEVEL), min(ks.stop, lattice.n + 1))
    return [(i, j, k) for i in xs for j in ys for k in ks]


def _meeting(lo: float, hi: float, half: float, origin: float, grid: float, n: int) -> range:
    first = math.floor((lo - half + TOUCH_CM - origin) / grid) + 1
    last = math.ceil((hi + half - TOUCH_CM - origin) / grid) - 1
    return range(max(first, 0), min(last + 1, n + 1))


# --- small arithmetic over paths -------------------------------------------


def _endpoints(terminals: Sequence[Terminal], rates: Sequence[Fraction]) -> tuple[_Endpoint, ...]:
    """A net's terminals grouped into the places its flow really starts or ends.

    Every machine port is its own endpoint and needs its own belt; the wall line
    is one endpoint however many nodes it offers, because R-M3-5's "at any ``X``
    the router chooses" is one stream and not thirty.
    """
    out = [
        _Endpoint((terminal,), rate)
        for terminal, rate in zip(terminals, rates, strict=True)
        if terminal.kind != "wall"
    ]
    wall = [
        (terminal, rate)
        for terminal, rate in zip(terminals, rates, strict=True)
        if terminal.kind == "wall"
    ]
    if wall:
        out.append(_Endpoint(tuple(terminal for terminal, _ in wall), wall[0][1]))
    return tuple(out)


def _tap_of(branch: _Branch) -> _Tap:
    if branch.tap is None:
        raise NetError("data", "a branch of the tree lost the tap it was routed from")
    return branch.tap


def _belt_length_cm(realised: Sequence[Realised]) -> float:
    """How much belt a round laid, which is what its rounds are ranked by."""
    return sum(
        math.dist(a[0], b[0])
        for laid in realised
        for belt in laid.belts
        for a, b in zip(belt.points, belt.points[1:], strict=False)
    )


def _out_of_time() -> Routed:
    return Routed(None, RouteFailureKind.BUDGET, (), 0, BudgetCause.DEADLINE)


def _nowhere_to_start() -> Routed:
    return Routed(None, RouteFailureKind.DYNAMIC_ACCESS, (), 0)


def _nowhere_to_go() -> Routed:
    return Routed(None, RouteFailureKind.DYNAMIC_ACCESS, (), 0)


def _restore(run: _Run, best: _Played) -> None:
    """Leave the occupancy holding the BEST round rather than the last one tried.

    Task 9 stands power poles on what routing left free, so an occupancy that
    described a round whose paths were not returned would put a pole through a
    belt.
    """
    run.release_all()
    for tree in best.trees:
        for path in tree.paths:
            run.stake(tree.net.id, path)
        run.proofs[tree.net.id] = tree.proofs
        for proofs in tree.proofs:
            for proof in proofs:
                for terminal in (proof.source, proof.sink):
                    if terminal.kind == "tap":
                        # Access-node paths retain their connector displacement
                        # as a stub, so restore its ownership along with proof.
                        side = _node_on_ray(
                            terminal.world, terminal.facing, run.lattice, "restored tap"
                        )
                        run.stake(tree.net.id, (terminal.node, side))
    for net_id, low, high in best.columns:
        run.stake(net_id, _column(low, high))
    run.physical.update(best.physical)
