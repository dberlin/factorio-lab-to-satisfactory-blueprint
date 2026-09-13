"""Owned replay records, never live preparation objects or cancellation closures.

Projection caches are derived state. Preserve all their fixed physical inputs
and recreate the normal projection object on restoration, with the replay's
query deadline; never serialize a live callback or weaken production cancellation.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

from flab2bp.dsp import catalog
from flab2bp.layout import last_mile, routing_domain
from flab2bp.layout.band_policy import BandPolicy
from flab2bp.layout.base import PlacedBuilding
from flab2bp.layout.route_feedback import Cell


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    buildings: tuple[PlacedBuilding, ...]
    capacity: tuple[int, int, int, int]
    policy: BandPolicy
    belt_rules: catalog.BeltAltitudeRules
    final_extent: bool
    canvas_prefix_count: int
    reserved_buildings: frozenset[PlacedBuilding]

    @classmethod
    def capture(cls, projection: routing_domain._CompositionProjection) -> ProjectionSnapshot:
        return cls(
            projection.buildings,
            projection.capacity,
            projection.policy,
            projection.belt_rules,
            projection.final_extent,
            projection.canvas_prefix_count,
            projection.reserved_buildings,
        )

    def restore(self) -> routing_domain._CompositionProjection:
        projection = routing_domain._CompositionProjection(
            self.buildings,
            self.capacity,
            self.policy,
            belt_rules=self.belt_rules,
            final_extent=self.final_extent,
        )
        projection.canvas_prefix_count = self.canvas_prefix_count
        projection.reserved_buildings = self.reserved_buildings
        return projection


@dataclass(frozen=True, slots=True)
class CanvasSnapshot:
    state: routing_domain._Canvas
    projection: ProjectionSnapshot | None

    @classmethod
    def capture(cls, canvas: routing_domain._Canvas) -> CanvasSnapshot:
        state = canvas.clone()
        projection = (
            None
            if canvas.junction_projection is None
            else ProjectionSnapshot.capture(canvas.junction_projection)
        )
        # The complete fixed geometry lives in `projection`, not in a live
        # closure-bearing object. This detaches the CLONE, never the caller.
        state.junction_projection = None
        return cls(state, projection)

    def restore(self) -> routing_domain._Canvas:
        canvas = self.state.clone()
        if self.projection is not None:
            canvas.junction_projection = self.projection.restore()
        return canvas


def snapshot_grid(grid: routing_domain._Grid | None) -> routing_domain._Grid | None:
    if grid is None:
        return None
    return replace(
        grid,
        occ=bytearray(grid.occ),
        routing_flags=bytearray(grid.routing_flags),
        hist=None if grid.hist is None else copy.copy(grid.hist),
    )


@dataclass(frozen=True, slots=True)
class RouteQuerySnapshot:
    ordinal: int
    canvas: CanvasSnapshot
    grid: routing_domain._Grid | None
    history: dict[Cell, float]
    starts: tuple[Cell, ...]
    # Iteration order is semantic: the bounded native reverse probe charges
    # preparation in endpoint order. Pickling a set silently changes it.
    goals: tuple[Cell, ...]
    pressure: float
    bounds: tuple[int, int, int, int]
    owned_starts: tuple[Cell, ...]
    released_starts: tuple[Cell, ...]
    forbidden: tuple[Cell, ...]
    blocking_owners: dict[Cell, int] | None
    extra_edges: dict[int, tuple[tuple[int, float], ...]] | None
    budget_left: int | None
    deadline_remaining: float | None
    blame: dict[Cell, float] | None


@dataclass(frozen=True, slots=True)
class RouteCase:
    query: RouteQuerySnapshot
    result: routing_domain._PathSearchResult
    budget_left: int | None
    blame: dict[Cell, float] | None


@dataclass(frozen=True, slots=True)
class ClusterCase:
    capture: last_mile.ClusterCapture
    result: last_mile.ClusterResult
