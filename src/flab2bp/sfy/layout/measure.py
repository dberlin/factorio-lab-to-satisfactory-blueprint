"""How big a Satisfactory build is, and which of two builds wins the race.

Spec section 9: strategies are raced and "the smallest valid stack wins (fewest
blueprints, then total occupied volume, then belt length)".  That sentence is
:func:`race_key`; :func:`measure` is the three numbers it reads, plus the two a
report wants beside them.

What "occupied volume" is, and what it deliberately is not
----------------------------------------------------------
The axis-aligned box around what the build IS: its machines, its conveyor
attachments, its lifts and every point of every belt.  Three things are left out
on purpose.

The **floor** is the whole designer in every build, so counting it would make
every build in a mark the same size and hand every race to the tie-break.  The
**poles and wires** are the same argument one step down: a pole line follows the
rows it powers, so it adds nothing a reader could act on, and a wire is not a
thing standing on the ground at all.

And it is a **bounding box, not a sum of boxes**.  Two builds of the same
machines differ in how far apart they stand them, which is exactly what a race
is meant to reward; a sum of clearance boxes would be equal for both.

Hard boxes, and the objects that have none
------------------------------------------
A machine is measured at its HARD clearance boxes -- ``CT_Soft`` marks ground
that may be shared, which is how a machine stands on a foundation at all, so a
soft box is not space the build occupies.  Every conveyor attachment and every
conveyor lift carries soft boxes only (``Build_ConveyorAttachmentSplitter_C``
and ``Build_ConveyorLiftMk1_C`` declare no hard box in ``registry.json``), so
each is measured at its own origin, and a lift also reaches to its top end --
its height is the one thing about it a footprint cannot see.

Belt length is arc length, :func:`~flab2bp.sfy.layout.splines.spline_length`:
the same measure ``belt.max_length`` holds a run to, rather than the polyline
through its points, because a curve is longer than its chords and a strategy
that turns more should be charged for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.geometry import Vector, box_bounds
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    MachineObj,
    Pose,
    SfyPlacement,
    lift_geometry,
)
from flab2bp.sfy.layout.splines import spline_length
from flab2bp.sfy.registry import Registry

__all__ = ["Measure", "measure", "race_key"]

#: How many blueprints a placement is.  One, for the whole of M3: a
#: :class:`~flab2bp.sfy.layout.model.SfyPlacement` is one Blueprint Designer's
#: worth of build, and stacking several is M4.  The field exists now because it
#: is the FIRST key the race is decided on, and adding a key later would change
#: which build wins every cell of the corpus.
BLUEPRINTS_PER_PLACEMENT = 1


@dataclass(frozen=True, slots=True)
class Measure:
    """How big one build is, in the terms the race is decided in."""

    #: Blueprints the build occupies: :data:`BLUEPRINTS_PER_PLACEMENT` in M3.
    blueprints: int
    #: The axis-aligned box around the build, in cubic centimetres.
    volume_cm3: float
    #: Arc length of every belt run, summed.
    belt_cm: float
    #: Conveyor lifts in the build.  Reported, not raced on.
    lifts: int
    #: Conveyor attachments -- splitters and mergers -- in the build.  The same.
    attachments: int


def measure(placement: SfyPlacement, registry: Registry) -> Measure:
    """How big ``placement`` is: its bounding volume, its belt, what it took."""
    corners = _corners(placement, registry)
    volume = 1.0
    for axis in range(3):
        volume *= max(corner[axis] for corner in corners) - min(corner[axis] for corner in corners)
    return Measure(
        blueprints=BLUEPRINTS_PER_PLACEMENT,
        volume_cm3=volume,
        belt_cm=sum(spline_length(run.points) for run in placement.belts),
        lifts=len(placement.lifts),
        attachments=len(placement.attachments),
    )


def race_key(measure: Measure, strategy_index: int) -> tuple[int, float, float, int]:
    """Spec section 9's order, smallest first, with the tie settled by name.

    ``strategy_index`` is the strategy's place in
    :data:`~flab2bp.sfy.strategy_names.SFY_PRODUCTION_STRATEGIES`, so two builds
    that measure the same go to the strategy named first -- the same winner on
    every run, rather than whichever process finished first.
    """
    return (measure.blueprints, measure.volume_cm3, measure.belt_cm, strategy_index)


def _corners(placement: SfyPlacement, registry: Registry) -> list[Vector]:
    """Every point the build's bounding box has to contain.

    A placement with nothing in it would have no box at all, which is not a
    measurement this function may invent: the caller asked how big a build is
    and there is no build.  The strategies never return one -- a spec with no
    groups refuses long before geometry -- so this raises rather than returning
    a zero somebody could race against.
    """
    corners: list[Vector] = []
    standing: list[AttachmentObj | MachineObj] = [*placement.machines, *placement.attachments]
    for obj in standing:
        corners += _extent(registry, obj.class_name, obj.pose)
    for lift in placement.lifts:
        corners += _extent(registry, lift.class_name, lift.pose)
        corners.append(lift.top_end(lift_geometry(registry, lift.class_name))[0])
    for run in placement.belts:
        corners += [point[0] for point in run.points]
    if not corners:
        raise ValueError("this placement holds no machine, attachment, lift or belt to measure")
    return corners


def _extent(registry: Registry, class_name: str, pose: Pose) -> list[Vector]:
    """One object's hard clearance boxes where it has any, its origin where it does not."""
    boxes = [box for box in registry.buildables[class_name].clearance if not box.soft]
    if not boxes:
        return [pose.location]
    corners: list[Vector] = []
    for box in boxes:
        low, high = box_bounds(box, pose.transform())
        corners += [low, high]
    return corners
