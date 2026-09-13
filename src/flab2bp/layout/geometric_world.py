"""Per-query view of the caller's admitted routing graph.

Occupancy and history are borrowed, not rebuilt from the physical canvas. The
view must not survive a query: reservations and repair histories can change on
the same grid object without changing its identity. The caller must not mutate
either borrowed buffer while the synchronous query is running.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .route_feedback import Cell
    from .routing_domain import _Grid, _RoutingTransition

# (dx, dy, dz, requires source-plane ramp via, forward base cost)
GeometricTransition = tuple[int, int, int, bool, float]


@dataclass(frozen=True, slots=True)
class GridIndex:
    """Flat index <-> cell for a box of ``rows * levels`` columns from ``(gx0, gy0)``.

    THE ONE PLACE THAT KNOWS THE ROUTING GRID'S LAYOUT.  ``_Grid``, this world
    and ``global_router`` all addressed the same flat array, each with its own
    copy of the arithmetic; three copies of one x-major formula is three places
    a level-major slip can hide, and a level-major index is measurably a
    different router (see :class:`~flab2bp.layout.routing_domain._Grid`).

    ``encode`` does NOT bounds-check.  A caller that must reject an outside
    cell checks first -- see ``_Grid.index``, which raises, and
    ``global_router._live_index``, which returns ``None``.
    """

    gx0: int
    gy0: int
    #: Cells per column in y; ``_Grid.gh``.
    rows: int
    levels: int

    def encode(self, cell: Cell) -> int:
        return ((cell[0] - self.gx0) * self.rows + (cell[1] - self.gy0)) * self.levels + cell[2]

    def decode(self, index: int) -> Cell:
        column, z = divmod(index, self.levels)
        x, y = divmod(column, self.rows)
        return x + self.gx0, y + self.gy0, z


@dataclass(frozen=True, slots=True)
class GeometricWorld:
    """Borrowed flat graph with authoritative directed movements."""

    nx: int
    ny: int
    nz: int
    gx0: int
    gy0: int
    flags: bytearray
    history: array[float] | list[float] | None
    transitions: tuple[tuple[GeometricTransition, ...], ...]
    #: Derived, never passed: the world's extent and origin already fix it, and
    #: a second way to supply it is a second way to disagree with the grid.
    codec: GridIndex = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "codec", GridIndex(self.gx0, self.gy0, self.ny, self.nz))

    @classmethod
    def from_grid(
        cls,
        grid: _Grid,
        flags: bytearray,
        transitions: tuple[tuple[_RoutingTransition, ...], ...],
    ) -> GeometricWorld:
        """Use the caller's final flags after source and forbidden overrides."""
        return cls(
            nx=grid.size // grid.xstep,
            ny=grid.gh,
            nz=grid.levels,
            gx0=grid.gx0,
            gy0=grid.gy0,
            flags=flags,
            history=grid.hist,
            transitions=tuple(
                tuple(
                    (dx, dy, offset - dx * grid.xstep - dy * grid.levels, via != 0, cost)
                    for offset, via, dx, dy, cost in row
                )
                for row in transitions
            ),
        )

    def cell(self, index: int) -> Cell:
        """Decode the same flat index used by the routing grid."""
        return self.codec.decode(index)
