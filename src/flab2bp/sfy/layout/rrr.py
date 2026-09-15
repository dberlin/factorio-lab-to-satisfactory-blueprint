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

**Three ways a net loses its round, and only one of them proves anything.**

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
from dataclasses import dataclass, field
from fractions import Fraction

from flab2bp.layout.budget import WorkBudget, expired
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.layout.corridors import Measures
from flab2bp.sfy.layout.grid_nets import (
    LATTICE_TOUCH_CM,
    TAP_CLEAR_NODES,
    GridNet,
    NetError,
    facing_for,
    snapped,
    tap_nodes,
)
from flab2bp.sfy.layout.lattice import (
    GROUND_LEVEL,
    Lattice,
    Node,
    Occupancy,
    belt_levels,
)
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS
from flab2bp.sfy.layout.model import AttachmentObj, Pose, Vector
from flab2bp.sfy.layout.realise import Realised, RealiseError, Terminal, realise
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
"""How many times one net may be re-queried over an occupied lift column.

A COUNT, not a convergence.  Task 5 measured that closing one lift's bottom node
in an open corridor moves the lift one node sideways and proposes it again, so
there is no fixed point to iterate to; after this many tries the net is stranded
for the round and the next round's pressure and history do the arguing.
"""

REALISE_RETRIES = 3
"""How many times one net may be re-queried over a path the realiser refused.

Covers ``corner``, ``leg``, ``lift`` and ``stub`` together, for the same reason
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
    """

    net: GridNet
    paths: tuple[tuple[Node, ...], ...]
    taps: tuple[tuple[Node, int], ...]


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

    A machine port is one endpoint.  A designer wall is ALSO one endpoint, with
    one terminal per node of the wall line: R-M3-5 lets the stream cross at any
    ``X``, so the whole line is one choice rather than N places to belt to.
    """

    terminals: tuple[Terminal, ...]
    rate: Fraction

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


@dataclass(slots=True)
class _Branch:
    """One path of one net's tree, and what it is joined to at each end."""

    path: tuple[Node, ...]
    #: Where it starts, unless it starts at a tap, when the attachment's side
    #: port is the source and is not known until the attachment is stood.
    source: Terminal | None
    #: Where it ends, unless it merges into another branch.
    sink: Terminal | None
    #: What arrives at its far end: a sink's draw, or a source's whole output.
    delivered: Fraction
    tap: _Tap | None
    merging: bool


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
    #: ``""`` where there is a tree; else ``"route"``, ``"realise"`` or ``"lift"``.
    failure: str = ""
    #: What to charge the congestion history: the nodes the refusal NAMED.
    nodes: tuple[Node, ...] = ()
    #: What to close for the next query, which is not always the same thing --
    #: see :func:`_shut_column`.
    shut: tuple[Node, ...] = ()


@dataclass(frozen=True, slots=True)
class _Played:
    """One whole round, and the key the best of them is chosen by."""

    trees: tuple[RoutedTree, ...]
    stranded: tuple[tuple[GridNet, Routed], ...]
    realised: tuple[Realised, ...]
    columns: tuple[tuple[int, Node, Node], ...]
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

    # -- the search ---------------------------------------------------------

    def query(
        self,
        starts: Collection[Node],
        goals: Collection[Node],
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
        routed = route_net(
            self.occupancy,
            starts=tuple(starts),
            goals=tuple(goals),
            opened=tuple(opened),
            closed=tuple(closed),
            pressure=self.pressure,
            budget=self.budget,
            deadline=self.deadline,
            transitions=self.transitions,
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
        for stake in self.staked.pop(net_id, []):
            self.occupancy.rip_up(stake)
            self.owners.pop(stake, None)
        self.held = {node: owner for node, owner in self.held.items() if owner != net_id}

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

    def tap_sites(self, branches: Sequence[_Branch], net: GridNet) -> dict[Node, _Tap]:
        """Every node a belt of this net may leave the tree from -- R-M3-7.

        Keyed by the node the side port stands on, because that is what a query
        is offered: the tap node itself is where the attachment goes and no belt
        ever stands there.  A side node the world denies, or one another belt is
        already standing on, is not offered at all.
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
                if not self.lattice.holds(side) or side in self.held:
                    continue
                if self.occupancy.base[self.lattice.index(side)] == 0:
                    continue
                out[side] = _Tap(branch=index, index=position, node=node, side=side)
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
            _lift_heights(lattice, registry),
            incline_run_nodes(registry.limits, lattice.grid_cm),
        ),
        clear=max(TAP_CLEAR_NODES, math.ceil(measures.radius / measures.grid)),
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


def _route_one(run: _Run, net: GridNet) -> _Try:
    """One net, retried within the round over what the build itself refused.

    A search refusal ends the net's round at once -- the pressure is what argues
    with another net, and arguing again at the same pressure would give the same
    answer.  A realise refusal and an occupied lift column are different: they
    are the BUILD refusing a path the search was right to offer, so the nodes
    that refused it are charged and closed and the same net asks again, a
    bounded number of times (:data:`REALISE_RETRIES`, :data:`LIFT_RETRIES`).
    """
    own = {
        node for terminal in (*net.sources, *net.sinks) for node in (terminal.node, *terminal.reach)
    }
    closed: set[Node] = set()
    lift_tries = 0
    realise_tries = 0
    while True:
        attempt = _attempt(run, net, closed)
        if attempt.tree is not None:
            return attempt
        run.release(net.id)
        run.charge(attempt.nodes)
        if attempt.failure == "route":
            return attempt
        if attempt.failure == "lift":
            lift_tries += 1
            if lift_tries > LIFT_RETRIES:
                return attempt
        else:
            realise_tries += 1
            if realise_tries > REALISE_RETRIES:
                return attempt
        # A net's own port is never closed against it.  A corner at the first
        # node of a run, and a lift whose bottom stands on the port it leaves,
        # both name a node the net cannot do without; closing it would not
        # correct the query, it would refuse to route the net at all -- and the
        # refusal would come back as ``dynamic-access``, which says the port was
        # unreachable when what really happened is that this loop shut it.
        closed.update(node for node in attempt.shut if node not in own)


def _attempt(run: _Run, net: GridNet, closed: Collection[Node]) -> _Try:
    """One whole tree for one net: search every branch, then build them all.

    The search comes first and the building second, and not the other way round,
    because what a piece of belt CARRIES is not known until the tree is: a trunk
    above a tap carries its own sink's draw plus everything the tap feeds, and a
    belt laid before that is a belt laid on a guess at its tier (R-M3-7).
    """
    sources = _endpoints(net.sources, net.per_source)
    sinks = _endpoints(net.sinks, net.per_sink)
    branches: list[_Branch] = []
    unused = list(range(len(sources)))
    routed: Routed | None = None
    for sink in sinks:
        taps = run.tap_sites(branches, net)
        starts: dict[Node, int | _Tap] = {}
        opened: set[Node] = set(sink.opened)
        for index in unused:
            opened.update(sources[index].opened)
            for node in sources[index].nodes:
                starts[node] = index
        for side, tap in taps.items():
            starts[side] = tap
            opened.add(side)
        if not starts:
            return _Try(failure="route", routed=_nowhere_to_start())
        routed = run.query(starts, sink.nodes, opened, closed)
        if routed.path is None:
            return _Try(failure="route", routed=routed)
        path = _cut_loops(routed.path)
        head = starts[path[0]]
        if isinstance(head, _Tap):
            branches.append(
                _Branch(
                    path=path,
                    source=None,
                    sink=sink.at(path[-1]),
                    delivered=sink.rate,
                    tap=head,
                    merging=False,
                )
            )
        else:
            branches.append(
                _Branch(
                    path=path,
                    source=sources[head].at(path[0]),
                    sink=sink.at(path[-1]),
                    delivered=sink.rate,
                    tap=None,
                    merging=False,
                )
            )
            unused.remove(head)
        run.stake(net.id, path)
    for index in tuple(unused):
        taps = run.tap_sites(branches, net)
        if not taps:
            return _Try(failure="route", routed=_nowhere_to_go())
        opened = set(sources[index].opened) | set(taps)
        routed = run.query(sources[index].nodes, tuple(taps), opened, closed)
        if routed.path is None:
            return _Try(failure="route", routed=routed)
        path = _cut_loops(routed.path)
        branches.append(
            _Branch(
                path=path,
                source=sources[index].at(path[0]),
                sink=None,
                delivered=sources[index].rate,
                tap=taps[path[-1]],
                merging=True,
            )
        )
        unused.remove(index)
        run.stake(net.id, path)
    return _build(run, net, branches, routed)


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
        return _Try(
            failure="realise",
            nodes=failure.nodes,
            shut=_shut_column(run.lattice, failure.nodes)
            if failure.cause == "lift"
            else tuple(failure.nodes),
            routed=routed,
        )
    columns = tuple((low, high) for laid in realised for low, high in laid.lift_columns)
    blocked = _blocked_column(run, net, realised)
    if blocked:
        return _Try(
            failure="lift",
            nodes=blocked,
            shut=_shut_column(run.lattice, blocked),
            routed=routed,
        )
    for low, high in columns:
        run.stake(net.id, _column(low, high))
    return _Try(
        tree=RoutedTree(
            net=net,
            paths=tuple(branch.path for branch in branches),
            taps=tuple(
                (branch.tap.node, branch.tap.branch)
                for branch in branches
                if branch.tap is not None
            ),
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

    Read from each branch's far end backwards: what arrives there is the sink's
    own draw (or, on a branch that merges, the whole of its source's output), and
    every junction above it adds back what a splitter sends away or takes off
    what a merger brings in.  A child is always routed after its parent, so the
    branches are already in an order where a child's answer is known before its
    parent needs it.
    """
    pieces: list[tuple[Fraction, ...]] = [()] * len(branches)
    for index in reversed(range(len(branches))):
        flow = branches[index].delivered
        walked = [flow]
        for child in reversed(children[index]):
            if branches[child].merging:
                flow -= pieces[child][-1]
            else:
                flow += pieces[child][0]
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
        pieces = _pieces(branch.path, cuts)
        joins = [stood[child] for child in children[index]]
        for position, piece in enumerate(pieces):
            source = (
                _through(run, joins[position - 1], piece[0], leaving=True)
                if position
                else _start(run, branch, stood.get(index))
            )
            sink = (
                _through(run, joins[position], piece[-1], leaving=False)
                if position < len(joins)
                else _end(run, branch, stood.get(index))
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
                )
            )
    return tuple(out)


def _start(run: _Run, branch: _Branch, stood: _Stood | None) -> Terminal:
    """Where a branch's first piece begins: its own source, or the tap it leaves."""
    if branch.source is not None:
        return branch.source
    if stood is None or branch.tap is None:
        raise RealiseError("stub", (), "a branch with no source and no tap has no beginning")
    return _side(run, stood, branch.tap.side)


def _end(run: _Run, branch: _Branch, stood: _Stood | None) -> Terminal:
    """Where a branch's last piece ends: its own sink, or the merger it feeds."""
    if branch.sink is not None:
        return branch.sink
    if stood is None or branch.tap is None:
        raise RealiseError("stub", (), "a branch with no sink and no tap has no end")
    return _side(run, stood, branch.tap.side)


def _stand(run: _Run, branches: Sequence[_Branch], child: int) -> _Stood:
    """The splitter or merger one branch joins the tree by, turned to the run.

    Yaw is the flow's own heading at the tap node, which puts the attachment's
    through ports one grid step back and forward along the run and its two side
    ports on the two nodes across it; which of those two the branch leaves by is
    read off where the branch really goes, not named here.
    """
    branch = branches[child]
    tap = _tap_of(branch)
    path = branches[tap.branch].path
    ahead = path[tap.index + 1]
    flow = (ahead[0] - tap.node[0], ahead[1] - tap.node[1])
    class_name = MERGER_CLASS if branch.merging else SPLITTER_CLASS
    buildable = run.registry.buildables.get(class_name)
    if buildable is None:
        raise NetError("data", f"the registry does not describe {class_name}")
    here = run.lattice.world(tap.node)
    pose = Pose(here[0], here[1], here[2], math.degrees(math.atan2(flow[1], flow[0])))
    obj = AttachmentObj(id=next(run.ids), class_name=class_name, pose=pose)
    ports = [port for port in buildable.ports if port.kind == "belt"]
    left = (-flow[1], flow[0])
    on_left = (tap.side[0] - tap.node[0], tap.side[1] - tap.node[1]) == left
    sign = 1.0 if on_left else -1.0
    return _Stood(
        obj=obj,
        through_in=_pick(ports, class_name, lambda t: t[0] < -LATTICE_TOUCH_CM, _straight),
        through_out=_pick(ports, class_name, lambda t: t[0] > LATTICE_TOUCH_CM, _straight),
        side=_pick(
            ports,
            class_name,
            lambda t: t[1] * sign > LATTICE_TOUCH_CM,
            lambda t: abs(t[0]) <= LATTICE_TOUCH_CM,
        ),
    )


def _straight(translation: Vector) -> bool:
    return abs(translation[1]) <= LATTICE_TOUCH_CM


def _pick(
    ports: Sequence[Port],
    class_name: str,
    along: Callable[[Vector], bool],
    across: Callable[[Vector], bool],
) -> Port:
    """One of an attachment's four belt ports, by where it stands on the object.

    By POSITION rather than by name: the game's own splitter and merger put
    their through ports on ``+-X`` and their side ports on ``+-Y``, and a port
    name is not a source for anything (global constraint 1).
    """
    for port in ports:
        if along(port.translation) and across(port.translation):
            return port
    raise NetError("data", f"{class_name} has no belt port where an attachment turn needs one")


def _through(run: _Run, stood: _Stood, node: Node, *, leaving: bool) -> Terminal:
    """The terminal of an attachment's through port, held to the node it stands on."""
    port = stood.through_out if leaving else stood.through_in
    return _port_terminal(run, stood.obj, port, node)


def _side(run: _Run, stood: _Stood, node: Node) -> Terminal:
    return _port_terminal(run, stood.obj, stood.side, node)


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
    if run.lattice.node(world) != node:
        raise NetError(
            "data",
            f"{obj.class_name}'s {port.name} stands at {world}, which is not the lattice "
            f"node {node} a run cut here would end on",
        )
    return Terminal(
        node=node,
        world=world,
        facing=facing_for(port_forward(transform, port), run.lattice, port.name),
        port=(obj.id, port.name),
        kind="tap",
    )


def _pieces(path: Sequence[Node], cuts: Sequence[int]) -> list[tuple[Node, ...]]:
    """One branch's path, cut at the nodes its attachments stand on.

    The attachment occupies the node itself and its two through ports stand on
    the nodes either side, so the piece before a cut ends one node short of it
    and the piece after starts one node past it.

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


def _blocked_column(run: _Run, net: GridNet, realised: Sequence[Realised]) -> tuple[Node, ...]:
    """The ends of the first lift whose column is not this net's to stand in.

    The movement table names no intermediate node for a lift, so this is where
    the column is enforced: every node of it at every level strictly between the
    ends, and every node the lift's own clearance box meets.  The nodes returned
    are the lift's two ENDS and not the column -- they are the nodes the kernel
    tests, and charging a node no query can see teaches the search nothing.
    """
    for laid in realised:
        for lift, (low, high) in zip(laid.lifts, laid.lift_columns, strict=True):
            box = lift_box(lift, run.registry)
            centre, reach = box.centre, box.reach
            nodes = [
                *(node for node in _column(low, high) if node not in (low, high)),
                *_box_nodes(
                    run.lattice,
                    (centre[0] - reach[0], centre[1] - reach[1], centre[2] - reach[2]),
                    (centre[0] + reach[0], centre[1] + reach[1], centre[2] + reach[2]),
                ),
            ]
            if any(not run.available(net.id, node) for node in nodes):
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


def _column(low: Node, high: Node) -> tuple[Node, ...]:
    """Every node of a lift's shaft, both ends included."""
    return tuple((low[0], low[1], level) for level in range(low[2], high[2] + 1))


def _box_nodes(lattice: Lattice, low: Vector, high: Vector) -> list[Node]:
    """Every node whose belt box meets the world box ``[low, high]``.

    The read-only mirror of :func:`~flab2bp.sfy.layout.lattice._mark_box`'s node
    range: the same "meets" reading -- the two boxes must really lap, by more
    than :data:`~flab2bp.sfy.layout.validate.TOUCH_CM` -- so that asking whether
    a lift's box is clear and marking one as occupied cannot disagree.
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


def _cut_loops(path: Sequence[Node]) -> tuple[Node, ...]:
    """``path`` with any node it visits twice, and everything between, spliced out.

    The kernel certifies a path and leaves loops in it -- cutting them needs the
    ramp context whoever turns a path into belts owns -- and this is that cut:
    where a node appears twice the steps between are a closed walk, and the step
    out of the first occurrence is the step out of the second, so removing them
    leaves a path the movement table still admits.
    """
    seen: dict[Node, int] = {}
    out: list[Node] = []
    for node in path:
        at = seen.get(node)
        if at is not None:
            del out[at + 1 :]
            seen = {held: index for held, index in seen.items() if index <= at}
            continue
        seen[node] = len(out)
        out.append(node)
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


def _lift_heights(lattice: Lattice, registry: Registry) -> range:
    """The lift heights this designer allows, in levels -- R-M3-3.

    ``lift_min_cm``, ``lift_max_cm`` and ``lift_step_cm`` are the game's and are
    read; the ceiling is the lattice's own topmost landable level.  A registry
    that states no window builds no lifts, which is a legal table rather than an
    error: the movement graph simply offers none.
    """
    limits = registry.limits
    low_cm, high_cm, step_cm = limits.lift_min_cm, limits.lift_max_cm, limits.lift_step_cm
    if low_cm is None or high_cm is None or step_cm is None:
        return range(0)
    grid = lattice.grid_cm
    low = round(low_cm / grid)
    high = min(round(high_cm / grid), lattice.n - GROUND_LEVEL)
    step = max(round(step_cm / grid), 1)
    if high < low:
        return range(0)
    return range(low, high + 1, step)


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
    for net_id, low, high in best.columns:
        run.stake(net_id, _column(low, high))
