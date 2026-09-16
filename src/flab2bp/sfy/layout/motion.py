"""Registry-derived finite turn/cut policy and fixed-path legality summaries.

The only search here is over the finite geometry automaton. Physical routing
remains the shared affine-interval kernel; fixed-path summaries never explore
neighbouring cells. Spline chunking, emitted chord minima and collision checks
remain the realiser's final gates.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, final

from flab2bp.layout.budget import BudgetExhausted, WorkBudget, expired
from flab2bp.layout.geometric_motion import (
    MotionEdge,
    MotionEndpoint,
    MotionGuard,
    MotionMove,
    MotionPolicy,
    MotionStep,
    MotionWitness,
)
from flab2bp.sfy.layout.corridors import Measures, Turn
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice, Node
from flab2bp.sfy.layout.manifold import MERGER_CLASS, SPLITTER_CLASS, shortest_belt_cm
from flab2bp.sfy.layout.model import lift_geometry
from flab2bp.sfy.layout.transitions import FLAT_STEPS, incline_run_nodes, sfy_transitions
from flab2bp.sfy.layout.turns import eligible, fits_turn, free_run, options
from flab2bp.sfy.layout.validate import BELT_CLEARANCE_HALF_WIDTH_CM, PORT_ANGLE_RAD, TOUCH_CM

if TYPE_CHECKING:
    from flab2bp.sfy.layout.realise import Terminal
    from flab2bp.sfy.layout.router import TransitionTable
    from flab2bp.sfy.registry import Registry

_EPS = 1e-9
# State modes: a running slope leg, empty wall/port start, pending lift output.
_RUN, _WALL, _PORT, _LIFT = range(4)


@final
class MotionPreparationExhausted(BudgetExhausted):
    """The original query bound ended policy preparation."""

    def __init__(self, *, deadline: bool) -> None:
        super().__init__("motion policy preparation exhausted its query bound")
        self.deadline = deadline


def _charge(budget: WorkBudget, deadline: float | None, amount: int = 1) -> None:
    if expired(deadline):
        raise MotionPreparationExhausted(deadline=True)
    if budget.left is not None:
        if budget.left < amount:
            budget.left = 0
            raise MotionPreparationExhausted(deadline=False)
        budget.left -= amount


def lift_heights(lattice: Lattice, registry: Registry) -> range:
    """The registry lift window intersected with the lattice's height range."""
    limits = registry.limits
    low_cm, high_cm, step_cm = limits.lift_min_cm, limits.lift_max_cm, limits.lift_step_cm
    if low_cm is None or high_cm is None or step_cm is None:
        return range(0)
    grid = lattice.grid_cm
    low = round(low_cm / grid)
    high = min(round(high_cm / grid), lattice.n - GROUND_LEVEL)
    step = max(round(step_cm / grid), 1)
    return range(low, high + 1, step) if high >= low else range(0)


def motion_moves(lattice: Lattice, registry: Registry, lift_class: str) -> tuple[MotionMove, ...]:
    """Canonical original-coordinate primitive identities (independent of cost)."""
    del lift_class  # Geometry affects legal edges, not the movement table's shapes.
    return _motion_moves(
        lattice.n + 1,
        lift_heights(lattice, registry),
        incline_run_nodes(registry.limits, lattice.grid_cm),
    )


@lru_cache(maxsize=16)
def _motion_moves(levels: int, heights: range, incline: int) -> tuple[MotionMove, ...]:
    table = sfy_transitions(levels, heights, incline)
    return tuple(sorted({move[:4] for row in table for move in row}))


@dataclass(frozen=True, slots=True)
class MotionProfile:
    """Immutable geometric numbers; occupancy and endpoints are query-local."""

    lattice: Lattice = field(compare=False, hash=False)
    grid: float
    turns: tuple[Turn, ...]
    moves: tuple[MotionMove, ...]
    available: tuple[int, ...]
    ramp_free: tuple[float, ...]
    flat_cap: float
    ramp_cap: int
    lift_yaws: tuple[tuple[bool, ...], ...]
    stub_credits: tuple[float, ...]
    belt_min: float
    lift_lead: float

    def free(self, state: _State) -> float:
        return state.progress if state.slope == 0 else self.ramp_free[round(state.progress)]

    def closed(self, state: _State) -> bool:
        return state.previous < 0 or self.turns[state.previous].cost <= self.free(state) + _EPS

    def previous_reach(self, state: _State) -> float:
        return 0.0 if state.previous < 0 else self.turns[state.previous].reach

    def mature(self, state: _State) -> _State:
        """Coalesce debt-free states only after every future turn fits."""
        if state.mode != _RUN:
            return state
        if not self.closed(state) or self.free(state) + _EPS < self.flat_cap:
            return state
        progress = self.flat_cap if state.slope == 0 else float(self.ramp_cap)
        return _State(state.heading, state.slope, progress)


def motion_profile(
    lattice: Lattice,
    measures: Measures,
    registry: Registry,
    lift_class: str,
    transitions: TransitionTable | None = None,
) -> MotionProfile:
    """Read existing geometry helpers, without compiling a product automaton."""
    turns = options(measures)
    threshold = max(turn.cost for turn in turns) + max(turn.reach for turn in turns)
    free = tuple(
        free_run(2 * count * lattice.grid_cm, count * lattice.grid_cm, registry)
        for count in range(lattice.n + 1)
    )
    ramp_cap = next(
        (count for count, room in enumerate(free) if room >= threshold - _EPS), lattice.n
    )
    geometry = lift_geometry(registry, lift_class)
    if any(abs(value) > TOUCH_CM for value in geometry.bottom_offset) or any(
        abs(a - b) > TOUCH_CM
        for a, b in zip(geometry.top_offset_axis, (0.0, 0.0, 1.0), strict=True)
    ):
        raise ValueError("the vertical movement table cannot represent offset lift connectors")
    yaws: list[tuple[bool, ...]] = []
    for ax, ay in FLAT_STEPS:
        row: list[bool] = []
        for bx, by in FLAT_STEPS:
            delta = math.degrees(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
            step = geometry.top_yaw_step_deg
            snapped = 0.0 if step <= 0 else round(delta / step) * step
            row.append(
                abs(delta - snapped) <= TOUCH_CM
                and (geometry.top_yaw_free or abs(snapped) <= TOUCH_CM)
            )
        yaws.append(tuple(row))
    moves = motion_moves(lattice, registry, lift_class)
    shapes = (
        set(moves) if transitions is None else {move[:4] for row in transitions for move in row}
    )
    if not shapes.issubset(moves):
        raise ValueError("the supplied physical table has no Satisfactory primitive identity")
    available = tuple(index for index, move in enumerate(moves) if move in shapes)
    stub_credits: set[float] = set()
    for class_name in (SPLITTER_CLASS, MERGER_CLASS):
        for port in registry.buildables[class_name].ports:
            if port.kind != "belt":
                continue
            offset = max(abs(port.translation[0]), abs(port.translation[1]))
            stub = math.ceil((offset - TOUCH_CM) / lattice.grid_cm) * lattice.grid_cm - offset
            if stub > TOUCH_CM:
                stub_credits.add(stub)
    lift_half = registry.limits.lift_clearance_half_extent_cm
    if lift_half is None:
        raise ValueError("the registry states no lift shaft clearance")
    belt_min = shortest_belt_cm(registry.limits)
    return MotionProfile(
        lattice,
        lattice.grid_cm,
        turns,
        moves,
        available,
        free,
        threshold,
        ramp_cap,
        tuple(yaws),
        tuple(sorted(stub_credits)),
        belt_min,
        max(belt_min, lift_half + BELT_CLEARANCE_HALF_WIDTH_CM),
    )


@dataclass(frozen=True, slots=True)
class _State:
    heading: int
    slope: int
    progress: float
    previous: int = -1
    mode: int = _RUN
    # Origin of the current flat leg: no cut, terminal, or lift connector.
    cut: int = _RUN


@dataclass(frozen=True, slots=True)
class _End:
    node: Node
    heading: int
    stub: float
    port: bool


def _end(terminal: Terminal, lattice: Lattice, *, source: bool) -> _End:
    from flab2bp.sfy.layout.realise import RealiseError, _stub

    sign = 1 if source else -1
    facing = tuple(sign * value for value in terminal.facing)
    heading = max(
        range(len(FLAT_STEPS)),
        key=lambda index: sum(a * b for a, b in zip(FLAT_STEPS[index], facing, strict=False)),
    )
    dx, dy = FLAT_STEPS[heading]
    if dx * facing[0] + dy * facing[1] < math.cos(PORT_ANGLE_RAD):
        raise RealiseError("stub", (terminal.node,), "a motion endpoint needs a cardinal facing")
    node_world = lattice.world(terminal.node)
    stub = _stub(
        terminal.world if source else node_world,
        node_world if source else terminal.world,
        (facing[0], facing[1], facing[2]),
        end=0 if source else None,
        at=terminal.node,
    )
    if stub is not None and abs(stub.rise) > TOUCH_CM:
        raise RealiseError(
            "stub", (terminal.node,), "a connector stub must lie on its flat facing ray"
        )
    return _End(
        terminal.node, heading, 0.0 if stub is None else stub.run, terminal.port is not None
    )


def _initial(profile: MotionProfile, end: _End) -> _State:
    if end.stub > TOUCH_CM:
        return profile.mature(_State(end.heading, 0, min(end.stub, profile.flat_cap), cut=_PORT))
    return _State(end.heading, 0, 0.0, mode=_PORT if end.port else _WALL)


def _advance(
    profile: MotionProfile, state: _State, move: MotionMove
) -> tuple[tuple[_State, int, int], ...]:
    dx, dy, dz, via = move
    if not dx and not dy:
        # A lift cuts the belt. Turn debt alone cannot certify the intervening
        # straight: a debt-free 100 cm leg is still shorter than a real belt,
        # and a ramp ending beside the shaft needs its swept clearance too.
        lead = profile.belt_min if state.cut == _PORT else profile.lift_lead
        if state.mode == _WALL or (
            state.mode == _RUN
            and (
                state.slope
                or not profile.closed(state)
                or (state.previous < 0 and state.progress + _EPS < lead)
            )
        ):
            return ()
        return ((_State(state.heading, 0, 0.0, mode=_LIFT), -1, -1),)
    heading = FLAT_STEPS.index(
        (int(math.copysign(1, dx)) if dx else 0, int(math.copysign(1, dy)) if dy else 0)
    )
    slope = (1 if dz > 0 else -1) if via else 0
    distance = float(abs(dx) + abs(dy)) * profile.lattice.grid_cm
    progress = 1.0 if slope else min(distance, profile.flat_cap)
    fresh = profile.mature(_State(heading, slope, progress))
    if state.mode in (_PORT, _WALL):
        if heading != state.heading or (state.mode == _PORT and slope):
            return ()
        target = profile.mature(_State(heading, slope, progress, cut=_RUN if slope else _PORT))
        return ((target, -1, -1),)
    if state.mode == _LIFT:
        return (
            ((profile.mature(_State(heading, slope, progress, cut=_LIFT)), -1, -1),)
            if not slope and profile.lift_yaws[state.heading][heading]
            else ()
        )
    if heading == state.heading and slope == state.slope:
        cap = float(profile.ramp_cap) if slope else profile.flat_cap
        advanced = profile.mature(
            _State(
                heading, slope, min(cap, state.progress + progress), state.previous, cut=state.cut
            )
        )
        return ((advanced, -1, -1),)
    if not profile.closed(state):
        return ()
    if heading == state.heading:
        # Do not begin an incline inside the lift's expanded shaft footprint.
        # Ordinary slope changes retain their existing continuous-belt rules.
        if state.cut == _LIFT and state.progress + _EPS < profile.lift_lead:
            return ()
        return ((fresh, -1, -1),)  # Closing a slope leg discharges its debt, even without a turn.
    ax, ay = FLAT_STEPS[state.heading]
    bx, by = FLAT_STEPS[heading]
    if ax * bx + ay * by:
        return ()  # No half-circle primitive.
    out: list[tuple[_State, int, int]] = []
    for action in sorted(range(len(profile.turns)), key=lambda index: profile.turns[index].cost):
        turn = profile.turns[action]
        if turn.is_attachment and (state.slope or slope):
            continue
        if not fits_turn(turn, profile.free(state), profile.previous_reach(state)):
            continue
        target = profile.mature(_State(heading, slope, progress, action))
        out.append((target, 0 if turn.is_attachment else -1, action))
    return tuple(out)


def _accept(profile: MotionProfile, state: _State, end: _End) -> int | None:
    """The exact terminal action, or no accepting endpoint for this state."""
    if state.mode in (_PORT, _WALL):
        valid = state.heading == end.heading and (
            end.stub > TOUCH_CM or (state.mode == _PORT and end.port)
        )
        return -1 if valid else None
    if state.mode == _LIFT:
        valid = (end.stub + _EPS >= profile.belt_min or (end.stub <= TOUCH_CM and end.port)) and (
            profile.lift_yaws[state.heading][end.heading]
        )
        return -1 if valid else None
    if state.cut and state.slope == 0 and state.progress + end.stub + _EPS < profile.belt_min:
        return None
    if end.stub <= TOUCH_CM:
        valid = (
            state.heading == end.heading
            and profile.closed(state)
            and (not end.port or state.slope == 0)
        )
        return -1 if valid else None
    if state.heading == end.heading:
        enlarged = (
            state
            if state.slope
            else _State(state.heading, 0, state.progress + end.stub, state.previous, cut=state.cut)
        )
        return -1 if profile.closed(enlarged) else None
    if not profile.closed(state):
        return None
    ax, ay = FLAT_STEPS[state.heading]
    bx, by = FLAT_STEPS[end.heading]
    if ax * bx + ay * by:
        return None
    for action in sorted(range(len(profile.turns)), key=lambda index: profile.turns[index].cost):
        turn = profile.turns[action]
        if not eligible(turn, float(state.slope), 0.0, end.node, profile.lattice):
            continue
        if (
            fits_turn(turn, profile.free(state), profile.previous_reach(state))
            and turn.cost <= end.stub + _EPS
        ):
            return action
    return None


@dataclass(frozen=True, slots=True)
class _Topology:
    states: tuple[_State, ...]
    edges: tuple[MotionEdge, ...]
    by_move: tuple[tuple[MotionEdge, ...], ...]


# Profiles contain only immutable lattice/registry-derived values. The bounded
# cache survives queries, never retains occupancy or query endpoints.
_TOPOLOGIES: dict[tuple[MotionProfile, tuple[float, ...]], _Topology] = {}


def _topology(
    profile: MotionProfile, stubs: tuple[float, ...], budget: WorkBudget, deadline: float | None
) -> _Topology:
    stubs = tuple(sorted(set(stubs) | set(profile.stub_credits)))
    key = (profile, stubs)
    cached = _TOPOLOGIES.get(key)
    if cached is not None:
        _charge(budget, deadline)
        return cached
    states: list[_State] = []
    indices: dict[_State, int] = {}
    edges: list[MotionEdge] = []
    by_move: list[list[MotionEdge]] = [[] for _ in profile.moves]

    def number(state: _State) -> int:
        if state not in indices:
            indices[state] = len(states)
            states.append(state)
        return indices[state]

    for heading in range(len(FLAT_STEPS)):
        for mode in (_PORT, _WALL):
            _ = number(_State(heading, 0, 0.0, mode=mode))
        for stub in stubs:
            _ = number(profile.mature(_State(heading, 0, min(stub, profile.flat_cap), cut=_PORT)))
    cursor = 0
    while cursor < len(states):
        _charge(budget, deadline)
        for move_id in profile.available:
            move = profile.moves[move_id]
            for target, guard, action in _advance(profile, states[cursor], move):
                edge = MotionEdge(cursor, move_id, number(target), guard, action)
                edges.append(edge)
                by_move[move_id].append(edge)
        cursor += 1
    topology = _Topology(tuple(states), tuple(edges), tuple(tuple(row) for row in by_move))
    if len(_TOPOLOGIES) >= 16:
        del _TOPOLOGIES[next(iter(_TOPOLOGIES))]
    _TOPOLOGIES[key] = topology
    return topology


def _guard(profile: MotionProfile) -> MotionGuard:
    lines = profile.lattice.object_lines
    return (
        MotionGuard(())
        if not lines
        else MotionGuard(
            (
                (
                    lines.start,
                    lines.stop - 1,
                    lines.start,
                    lines.stop - 1,
                    0,
                    profile.lattice.n,
                ),
            )
        )
    )


def compile_policy(
    profile: MotionProfile,
    sources: Collection[Terminal],
    sinks: Collection[Terminal],
    *,
    budget: WorkBudget,
    deadline: float | None,
) -> MotionPolicy:
    """Compile actual connector directions/stubs inside the original query bound."""
    _charge(budget, deadline)
    starts = tuple(_end(terminal, profile.lattice, source=True) for terminal in sources)
    ends = tuple(_end(terminal, profile.lattice, source=False) for terminal in sinks)
    stubs = tuple(sorted({end.stub for end in starts if end.stub > TOUCH_CM}))
    topology = _topology(profile, stubs, budget, deadline)
    index = {state: position for position, state in enumerate(topology.states)}
    initial = tuple(
        MotionEndpoint(profile.lattice.index(end.node), index[_initial(profile, end)])
        for end in starts
    )
    accepting: list[MotionEndpoint] = []
    for end in ends:
        _charge(budget, deadline)
        cell = profile.lattice.index(end.node)
        for position, state in enumerate(topology.states):
            action = _accept(profile, state, end)
            if action is not None:
                accepting.append(MotionEndpoint(cell, position, action))
    return MotionPolicy(
        len(topology.states),
        profile.moves,
        topology.edges,
        initial,
        tuple(accepting),
        (_guard(profile),),
    )


@dataclass(frozen=True, slots=True)
class PathProof:
    """One certified fixed piece, with the actual terminals used to prove it."""

    path: tuple[Node, ...]
    source: Terminal
    sink: Terminal
    motion: MotionWitness


@dataclass(frozen=True, slots=True)
class _Primitive:
    index: int
    move: int
    end: int


def _primitives(path: Sequence[Node], moves: tuple[MotionMove, ...]) -> tuple[_Primitive, ...]:
    lookup = {move: index for index, move in enumerate(moves)}
    out: list[_Primitive] = []
    index = 0
    while index + 1 < len(path):
        here, there = path[index], path[index + 1]
        dx, dy, dz = tuple(b - a for a, b in zip(here, there, strict=True))
        last = index + 1
        via = False
        if (dx or dy) and last + 1 < len(path):
            beyond = path[last + 1]
            after = tuple(b - a for a, b in zip(there, beyond, strict=True))
            if dz == 0 and after[:2] == (dx, dy) and abs(after[2]) == 1:
                dx, dy, dz = 2 * dx, 2 * dy, after[2]
                last += 1
                via = True
        shape = (dx, dy, dz, via)
        if shape not in lookup:
            raise ValueError("fixed motion path contains an unsupported or partial primitive")
        out.append(_Primitive(index, lookup[shape], last))
        index = last
    return tuple(out)


@final
class FixedPathSummary:
    """Reusable prefix/suffix automaton relations for cuts of ONE fixed path.

    A candidate intersects the two cached relations at its actual through ports;
    only the selected cut materialises the two traces. No objects are emitted
    and no physical graph is searched during admission.
    """

    def __init__(
        self,
        proof: PathProof,
        profile: MotionProfile,
        *,
        budget: WorkBudget,
        deadline: float | None,
    ) -> None:
        self.proof = proof
        self.profile = profile
        self.budget = budget
        self.deadline = deadline
        start = _end(proof.source, profile.lattice, source=True)
        sink = _end(proof.sink, profile.lattice, source=False)
        # The registry's through-port stub residues are seeded in the shared
        # topology; the original source can contribute one additional residue.
        self.topology = _topology(
            profile, (start.stub,) if start.stub > TOUCH_CM else (), budget, deadline
        )
        self.primitives = _primitives(proof.path, profile.moves)
        self.boundaries = {primitive.index: i for i, primitive in enumerate(self.primitives)}
        self.boundaries[len(proof.path) - 1] = len(self.primitives)
        states = self.topology.states
        self.initial = states.index(_initial(profile, start))
        self.prefix: list[dict[int, tuple[int, MotionEdge] | None]] = [{self.initial: None}]
        for primitive in self.primitives:
            _charge(budget, deadline)
            row: dict[int, tuple[int, MotionEdge] | None] = {}
            previous = self.prefix[-1]
            for edge in self.topology.by_move[primitive.move]:
                if edge.source in previous and self._matches(edge, primitive):
                    _ = row.setdefault(edge.target, (edge.source, edge))
            self.prefix.append(row)
        self.suffix: list[dict[int, MotionEdge | None]] = [{} for _ in self.prefix]
        self.final_actions = {
            index: action
            for index, state in enumerate(states)
            if (action := _accept(profile, state, sink)) is not None
        }
        self.suffix[-1] = dict.fromkeys(self.final_actions)
        for position in reversed(range(len(self.primitives))):
            _charge(budget, deadline)
            primitive = self.primitives[position]
            after = self.suffix[position + 1]
            for edge in self.topology.by_move[primitive.move]:
                if edge.target in after and self._matches(edge, primitive):
                    _ = self.suffix[position].setdefault(edge.source, edge)

    def _matches(self, edge: MotionEdge, primitive: _Primitive) -> bool:
        node = self.proof.path[primitive.index]
        return edge.move == primitive.move and (
            edge.guard < 0
            or (
                node[0] in self.profile.lattice.object_lines
                and node[1] in self.profile.lattice.object_lines
            )
        )

    def cut(self, before: int, after: int, sink: Terminal, source: Terminal) -> CutProof | None:
        """Admit the two remaining parent pieces, without constructing traces."""
        _charge(self.budget, self.deadline)
        left, right = self.boundaries.get(before), self.boundaries.get(after)
        if left is None or right is None or left >= right:
            return None
        end = _end(sink, self.profile.lattice, source=False)
        start = _initial(self.profile, _end(source, self.profile.lattice, source=True))
        try:
            first = self.topology.states.index(start)
        except ValueError:
            return None
        chosen = next(
            (
                (state, action)
                for state in self.prefix[left]
                if (action := _accept(self.profile, self.topology.states[state], end)) is not None
            ),
            None,
        )
        if chosen is None or first not in self.suffix[right]:
            return None
        last, final_action = chosen
        return CutProof(self, left, right, last, first, sink, source, final_action)


@dataclass(frozen=True, slots=True)
class CutProof:
    summary: FixedPathSummary
    left: int
    right: int
    final: int
    initial: int
    sink: Terminal
    source: Terminal
    final_action: int = -1

    def pieces(self) -> tuple[PathProof, PathProof]:
        summary = self.summary
        _charge(summary.budget, summary.deadline)
        backward: list[MotionStep] = []
        state = self.final
        for position in reversed(range(self.left)):
            _charge(summary.budget, summary.deadline)
            parent = summary.prefix[position + 1][state]
            if parent is None:
                raise ValueError("fixed prefix lost its predecessor")
            previous, edge = parent
            primitive = summary.primitives[position]
            backward.append(
                MotionStep(primitive.index, edge.move, edge.source, edge.target, edge.action)
            )
            state = previous
        before = (
            summary.primitives[self.left].index
            if self.left < len(summary.primitives)
            else len(summary.proof.path) - 1
        )
        after = (
            summary.primitives[self.right].index
            if self.right < len(summary.primitives)
            else len(summary.proof.path) - 1
        )
        forward: list[MotionStep] = []
        state = self.initial
        for position in range(self.right, len(summary.primitives)):
            _charge(summary.budget, summary.deadline)
            suffix_edge = summary.suffix[position][state]
            if suffix_edge is None:
                raise ValueError("fixed suffix lost its successor")
            primitive = summary.primitives[position]
            forward.append(
                MotionStep(
                    primitive.index - after,
                    suffix_edge.move,
                    suffix_edge.source,
                    suffix_edge.target,
                    suffix_edge.action,
                )
            )
            state = suffix_edge.target
        return (
            PathProof(
                summary.proof.path[: before + 1],
                summary.proof.source,
                self.sink,
                MotionWitness(
                    summary.initial, self.final, tuple(reversed(backward)), self.final_action
                ),
            ),
            PathProof(
                summary.proof.path[after:],
                self.source,
                summary.proof.sink,
                MotionWitness(self.initial, state, tuple(forward), summary.final_actions[state]),
            ),
        )
