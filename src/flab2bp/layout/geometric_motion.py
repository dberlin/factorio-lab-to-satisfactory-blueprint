"""Query-local finite motion constraints for the geometric interval wavefront.

The policy describes a product graph, not another routing backend. Movement
indices name exact primitive shapes in original (untransposed) coordinates;
prices and per-level availability remain properties of ``GeometricWorld``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Inclusive grid-local bounds: x-low, x-high, y-low, y-high, z-low, z-high.
MotionBox = tuple[int, int, int, int, int, int]
MotionMove = tuple[int, int, int, bool]


@dataclass(frozen=True, slots=True)
class MotionGuard:
    """Union of boxes in which a transition's SOURCE cell must lie."""

    boxes: tuple[MotionBox, ...]


@dataclass(frozen=True, slots=True)
class MotionEdge:
    """One legal primitive and its finite-state/provenance effect.

    ``guard == -1`` permits any source cell. ``action == -1`` denotes no
    geometric event; nonnegative actions are opaque caller-owned identities.
    """

    source: int
    move: int
    target: int
    guard: int = -1
    action: int = -1


@dataclass(frozen=True, slots=True)
class MotionEndpoint:
    """One permitted motion state at an original flat-indexed endpoint."""

    cell: int
    state: int
    #: Selected terminal geometry, used only by accepting rows.
    action: int = -1


@dataclass(frozen=True, slots=True)
class MotionPolicy:
    """Immutable, finite product-graph policy; absence preserves DSP semantics.

    Initial/accepting rows refine, never enlarge, query starts/goals. A mature
    unguarded action-free self-loop may use whole-run horizontal closure;
    all other transitions translate complete intervals between state envelopes.
    Extra graph edges have no implicit motion semantics and are not supported
    by constrained queries.
    """

    state_count: int
    moves: tuple[MotionMove, ...]
    edges: tuple[MotionEdge, ...]
    initial: tuple[MotionEndpoint, ...]
    accepting: tuple[MotionEndpoint, ...]
    guards: tuple[MotionGuard, ...] = ()


@dataclass(frozen=True, slots=True)
class MotionStep:
    """Certified primitive beginning at ``path_index`` (before a ramp via)."""

    path_index: int
    move: int
    source: int
    target: int
    action: int = -1


@dataclass(frozen=True, slots=True)
class MotionWitness:
    """Exact finite-state trace of the selected physical path."""

    initial_state: int
    final_state: int
    steps: tuple[MotionStep, ...]
    #: Action at the final path node, e.g. a turn into an off-grid sink stub.
    final_action: int = -1


def validate_motion(policy: MotionPolicy, size: int, checkpoint: Callable[[], None]) -> None:
    """Validate finite indices and exact shapes, charging preparation as we go."""
    if not isinstance(policy, MotionPolicy):
        raise ValueError("motion must be a MotionPolicy")

    def integer(value: int, low: int, high: int, name: str) -> None:
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"invalid motion {name}: {value!r}")

    integer(policy.state_count, 1, 2**31 - 1, "state count")
    for name in ("moves", "edges", "initial", "accepting", "guards"):
        if not isinstance(getattr(policy, name), tuple):
            raise ValueError(f"motion {name} must be an immutable tuple")
    seen: set[MotionMove] = set()
    for move in policy.moves:
        checkpoint()
        if not isinstance(move, tuple) or len(move) != 4:
            raise ValueError("motion move must be (dx, dy, dz, via)")
        dx, dy, dz, via = move
        for delta in (dx, dy, dz):
            integer(delta, -(2**30), 2**30, "move displacement")
        if type(via) is not bool:
            raise ValueError("motion via must be boolean")
        if via and (dx % 2 or dy % 2):
            raise ValueError("motion ramp via requires even XY displacements")
        if move in seen:
            raise ValueError("motion movement shapes must be unique")
        seen.add(move)
    for guard in policy.guards:
        checkpoint()
        if not isinstance(guard, MotionGuard) or not isinstance(guard.boxes, tuple):
            raise ValueError("motion guard must contain immutable boxes")
        for box in guard.boxes:
            checkpoint()
            if not isinstance(box, tuple) or len(box) != 6:
                raise ValueError("motion guard box must contain six inclusive bounds")
            for bound in box:
                integer(bound, -(2**31), 2**31 - 1, "guard bound")
            if any(box[axis] > box[axis + 1] for axis in (0, 2, 4)):
                raise ValueError("motion guard box bounds are reversed")
    for edge in policy.edges:
        checkpoint()
        if not isinstance(edge, MotionEdge):
            raise ValueError("motion edge must be a MotionEdge")
        integer(edge.source, 0, policy.state_count - 1, "source state")
        integer(edge.target, 0, policy.state_count - 1, "target state")
        integer(edge.move, 0, len(policy.moves) - 1, "move index")
        integer(edge.guard, -1, len(policy.guards) - 1, "guard index")
        integer(edge.action, -1, 2**31 - 1, "action")
    for endpoints in (policy.initial, policy.accepting):
        for endpoint in endpoints:
            checkpoint()
            if not isinstance(endpoint, MotionEndpoint):
                raise ValueError("motion endpoint must be a MotionEndpoint")
            integer(endpoint.cell, 0, size - 1, "endpoint cell")
            integer(endpoint.state, 0, policy.state_count - 1, "endpoint state")
            integer(endpoint.action, -1, 2**31 - 1, "endpoint action")
            if endpoints is policy.initial and endpoint.action != -1:
                raise ValueError("motion initial endpoints cannot carry a terminal action")
