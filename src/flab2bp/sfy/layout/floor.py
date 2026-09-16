"""The floor every Satisfactory build stands on: a full slab of the designer.

One function, shared because both strategies need exactly the same thing and a
second copy would be a second place for the stand height to drift.  It is a
full floor rather than slabs under the machines: a Blueprint Designer's cells
are the build's own ground, a machine standing on nothing is not a build a
player can paste, and the count is the mark's own -- so what this returns
depends on the designer and on nothing the strategy decided.

Which is also why :mod:`flab2bp.sfy.layout.measure` does not count it.  The
floor is identical in every build in a mark, so it says nothing about which of
two builds is smaller.
"""

from __future__ import annotations

from collections.abc import Iterator

from flab2bp.sfy.layout.model import FoundationObj, Pose
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer

__all__ = ["foundations"]


def foundations(
    designer: Designer, registry: Registry, ids: Iterator[int]
) -> tuple[FoundationObj, ...]:
    """A full floor of the shipped foundation, covering ``designer``.

    Each slab stands at half its own box's thickness, so that its top is where a
    strategy stands its machines: the pose of a foundation is its centre, and a
    machine sitting at ``z = 0`` would otherwise be buried half a slab deep.

    ``ids`` is the build's object counter, handed in rather than started here so
    that the floor's ids are part of the one run of numbering every object in
    the placement shares.
    """
    side = designer.foundation_cm
    box = registry.buildables[FOUNDATION_CLASS].clearance[0]
    stand = (box.max[2] - box.min[2]) / 2.0
    half = designer.half_cm
    count = int(round(2.0 * half / side))
    return tuple(
        FoundationObj(
            id=next(ids),
            class_name=FOUNDATION_CLASS,
            pose=Pose(
                -half + side / 2.0 + i * side,
                -half + side / 2.0 + j * side,
                stand,
                0.0,
            ),
        )
        for i in range(count)
        for j in range(count)
    )
