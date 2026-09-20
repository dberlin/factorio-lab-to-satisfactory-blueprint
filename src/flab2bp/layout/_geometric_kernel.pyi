"""Native directed interval-wavefront boundary.

Status 2 carries complete source-reachable intervals; status 4 carries complete
goal-reaching intervals in the same payload slot. Status 5 is constrained
motion exhaustion, not a physical component. Other statuses carry no component.
"""

from array import array
from collections.abc import Callable, Mapping, Sequence

from .geometric_motion import MotionBox, MotionPolicy, MotionWitness
from .geometric_router import GeometricMetrics
from .geometric_world import GeometricTransition

def free_motion_boxes(
    domain: MotionBox, forbidden: Sequence[MotionBox]
) -> tuple[MotionBox, ...]: ...
def search_intervals(
    flags: bytearray,
    history: array[float] | list[float] | None,
    pressure: float,
    nx: int,
    ny: int,
    nz: int,
    transitions: Sequence[Sequence[GeometricTransition]],
    starts: Sequence[int],
    goals: Sequence[int],
    extra_edges: Mapping[int, Sequence[tuple[int, float]]],
    max_work: int,
    deadline: float | None,
    present: array[float] | None = None,
    charge_occupied_cells: bool = False,
    cancelled: Callable[[], bool] | None = None,
    motion: MotionPolicy | None = None,
) -> tuple[
    int,
    tuple[int, ...] | None,
    float | None,
    tuple[tuple[int, int, int, int], ...],
    GeometricMetrics,
    MotionWitness | None,
]: ...
