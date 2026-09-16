"""Complete directed interval routing on an already admitted query graph.

An affine label covers an integer X interval in one (Y, level) row. Horizontal
min-plus closure expands whole free, constant-price runs; other moves translate
intervals and intersect landing and ramp-via profiles. Lower envelopes retain
cheapest arrivals and immutable predecessors retain their physical witnesses.
No cell-priority search and no A* fallback are used.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, TypedDict

from ._geometric_kernel import search_intervals
from .geometric_motion import MotionPolicy, MotionWitness
from .geometric_world import GeometricWorld

#: The only routing backend; reported in placement stats as ``route_backend``.
BACKEND: Final = "geometric"


class GeometricMetrics(TypedDict):
    """Native query accounting; geometric work is not A* expansion count.

    ``charged_work`` is scanned occupancy/history cells plus processed active
    intervals, including the bounded reverse probe (at most min(max_work/16,
    1024) work) for unconstrained queries. Constrained queries charge policy
    validation, conversion and indexing to the same allowance and search only
    forward. Offers, profile intersections, and selected-path certification are
    measured separately. All native inner loops also enforce the deadline.
    ``certified_edges`` counts directed edge checks during certification, not
    the selected path's length. ``retained_native_bytes`` is an accounted
    storage lower bound: it excludes Python inputs, unused heap capacity, and
    temporary allocation peaks, and is not peak resident memory.
    """

    charged_work: int
    prepared_cells: int
    interval_pops: int
    labels: int
    offers: int
    intersections: int
    profile_scans: int
    certified_edges: int
    retained_native_bytes: int
    copied_history_cells: int
    preparation_s: float
    search_s: float
    certification_s: float


@dataclass(frozen=True, slots=True)
class GeometricQuery:
    """Caller-admitted graph and limits with borrowed congestion buffers.

    Cell prices are ``pressure * (present + history)``; present is already
    scaled by the caller. By default only landings are charged. Relaxed
    congestion routing also charges each start and occupied ramp-via cell.
    Cancellation returns no witness; callback exceptions propagate.
    """

    world: GeometricWorld
    starts: tuple[int, ...]
    goals: tuple[int, ...]
    pressure: float
    max_work: int
    deadline: float | None = None
    extra_edges: Mapping[int, tuple[tuple[int, float], ...]] = field(default_factory=dict)
    present: array[float] | None = None
    charge_occupied_cells: bool = False
    cancelled: Callable[[], bool] | None = None
    motion: MotionPolicy | None = None


@dataclass(frozen=True, slots=True)
class GeometricResult:
    """Certified path, budget/cancellation refusal, or reachable rows.

    Reachability intervals are ``(y, z, lo_x, hi_x)`` in grid-local coordinates.
    ``reachable`` is the complete source component on forward exhaustion;
    ``co_reachable`` is the complete goal-reaching component on reverse
    exhaustion (``None`` means no reverse proof). Neither component is supplied
    for an interrupted or motion-exhausted query. Motion exhaustion proves only
    that no accepting product-state path exists; it is not a sealed component.
    Path certification proves the admitted directed graph, not a factory's
    later physical transaction, source splitter, or final placement validator.
    """

    kind: Literal["routed", "budget", "exhausted", "cancelled", "motion-exhausted"]
    path: tuple[int, ...] | None
    cost: float | None
    reachable: tuple[tuple[int, int, int, int], ...]
    co_reachable: tuple[tuple[int, int, int, int], ...] | None
    metrics: GeometricMetrics
    motion: MotionWitness | None = None


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """A kernel result as its two callers read it: a path, a charge, a reason.

    Both routers reached into :class:`GeometricResult` the same way -- pull
    ``metrics["charged_work"]``, compare ``kind`` against the same string
    literals -- so the kernel's status encoding was spelled out at every call
    site. The four flags are mutually exclusive and exactly one is set when
    ``path`` is ``None``.

    This says WHICH bound the kernel hit, not which the CALLER should report:
    only the caller knows whether its own clock or allowance was the tighter
    one, so it still derives its
    :class:`~flab2bp.layout.route_feedback.BudgetCause` from these flags plus
    the limits it passed in.
    """

    path: tuple[int, ...] | None
    #: ``GeometricMetrics.charged_work``, the kernel's own exact charge.
    work: int
    exhausted_budget: bool
    cancelled: bool
    exhausted: bool
    motion_exhausted: bool = False
    motion: MotionWitness | None = None


def summarize(result: GeometricResult) -> SearchOutcome:
    """Read a kernel result once, for every caller that asks the same things."""
    return SearchOutcome(
        path=result.path,
        work=result.metrics["charged_work"],
        exhausted_budget=result.kind == "budget",
        cancelled=result.kind == "cancelled",
        exhausted=result.kind == "exhausted",
        motion_exhausted=result.kind == "motion-exhausted",
        motion=result.motion,
    )


def route(query: GeometricQuery) -> GeometricResult:
    """Search the query without changing the caller's occupancy or budget."""
    world = query.world
    status, path, cost, reachable, metrics, motion = search_intervals(
        world.flags,
        world.history,
        query.pressure,
        world.nx,
        world.ny,
        world.nz,
        world.transitions,
        query.starts,
        query.goals,
        query.extra_edges,
        query.max_work,
        query.deadline,
        query.present,
        query.charge_occupied_cells,
        query.cancelled,
        query.motion,
    )
    kind: Literal["routed", "budget", "exhausted", "cancelled", "motion-exhausted"]
    if status == 0:
        kind = "routed"
    elif status == 1:
        kind = "budget"
    elif status == 3:
        kind = "cancelled"
    elif status == 5:
        kind = "motion-exhausted"
    else:
        kind = "exhausted"
    co_reachable = reachable if status == 4 else None
    if status == 4:
        reachable = ()
    return GeometricResult(kind, path, cost, reachable, co_reachable, metrics, motion)
