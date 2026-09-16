"""The neutral judge for a Satisfactory placement.

The shape is :mod:`flab2bp.layout.validate`'s -- :class:`Severity`,
:class:`Finding`, :class:`Report`, a ``CHECKS`` registry filled by a decorator --
because the two validators answer the same question about two games and a
reader should not have to learn a second vocabulary.  What is new here is the
third argument to :func:`check`: every check names the rule it enforces.

Where a bound comes from
------------------------
Legality is what the build gun's hologram does or allows.  So each check carries
a ``rule``:

* the id of a rule in ``data/hologram_rules.json``, read out of the shipped
  game's machine code, **or**
* :data:`PROJECT`, which says the bound is this project's own and obliges the
  docstring to say why we are stricter than the game.

Three consequences, and they are rules a reviewer holds this module to:

1. **Only a rule whose ``effect`` is ``refuse`` turns a placement away.**  A
   ``compute`` rule (``belt.clearance``) hands over a shape to reproduce and
   refuses nothing; a ``snap`` or ``clamp`` rule moves a value rather than
   rejecting it.  A check that refuses on one of those would be dressing this
   project's own judgement up as the game's, so it names :data:`PROJECT`
   instead -- ``belt.capsule`` is exactly that case.
2. **A ``partial`` rule is a bound this module may not assume it knows.**  It
   runs what the rule's read part states, and it lists itself in
   ``Report.skipped`` with the reason for the part that was never read, so its
   silence is never mistaken for coverage.  ``geom.hard_clearance`` is the one.
3. **No number here comes from a blueprint.**  Every bound is
   ``registry.json``'s (with its own source and governing rule) or a rule's;
   a community ``.sbp`` can hold clipped geometry from an older game and is not
   evidence of anything.  The constants this module states itself are all
   tolerances and sampling resolutions, each one documented as ours.

Two tiers of check, as in the DSP judge: most need only a
:class:`~flab2bp.sfy.layout.model.SfyPlacement`, three need the
:class:`~flab2bp.sfy.spec.SfyBuildSpec` it was meant to realise and are reported
in ``Report.skipped`` without one rather than passing in silence.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from functools import cache, cached_property
from itertools import chain
from pathlib import Path

from flab2bp.sfy.archive import Reader
from flab2bp.sfy.geometry import placed_box, port_forward, quat_rotate, world_port
from flab2bp.sfy.header import BlueprintHeader, read_header
from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.emit import EmitError, decode, emit
from flab2bp.sfy.layout.model import (
    BeamObj,
    BeltRun,
    LiftObj,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    Placed,
    SfyPlacement,
    Vector,
    WireObj,
    belt_ends,
    lift_geometry,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import hermite, spline_length, tangent_at_distance
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import ClearanceBox, Port, Registry
from flab2bp.sfy.rules import HologramRule, load_rules
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup
from flab2bp.sfy.templates import TemplateError, TemplateLibrary

__all__ = [
    "CHECKS",
    "NEEDS_SPEC",
    "PROJECT",
    "RULE_FOR",
    "Context",
    "Finding",
    "Report",
    "Severity",
    "WorldBox",
    "lift_box",
    "beam_box",
    "passthrough_box",
    "pipe_chain",
    "lift_half_width",
    "validate",
    "required_input_head_m",
]

PROJECT = "project"
"""The ``rule`` of a check that enforces this project's own bound, not the game's."""

# --- what this module states itself ----------------------------------------
#
# Every constant below is a TOLERANCE or a SAMPLING RESOLUTION, never a bound:
# the bounds live in ``registry.json``'s ``limits`` and in the rules.

TOUCH_CM = 1e-6
"""How deep two boxes must lap before this module calls it an intersection.

Ours.  Two buildables that share a face -- a machine standing on a foundation,
two foundations side by side -- overlap by exactly zero, and floating point
turns that into a few times 1e-16 either way.  A placement is refused for
volume, not for arithmetic noise.
"""

BELT_CONNECTION_CM = 5.0
"""How far into a neighbour a belt's clearance may reach at its own connection.

Ours.  ``buildable.clearance`` is ``partial``: the tolerance the game applies
lives in ``AFGHologram::TestClearanceOverlap``, which was not read, and two
belts joined end to end do lap by a sliver where their tangents differ.
"""

PORT_CM = 1.0
"""How far a belt's end may sit from the port it is wired to.  Ours: M1b's
stated assumption is that a belt begins AT its port, and this is the slack that
assumption is allowed."""

LIFT_YAW_TOLERANCE_DEG = 1e-6
"""How far a lift's top yaw may sit off the step the build gun turns it in.

Ours, and noise rather than slack: a yaw is written as a quaternion of four
doubles and read back through ``atan2``, so a quarter turn does not always come
back as exactly 90.  A lift meant to be turned is turned by a whole step or by
nothing."""

PORT_ANGLE_RAD = 0.01
"""How far a belt may leave off its port's facing.  Ours, for the same reason:
``flab2bp.sfy.geometry.port_forward`` says in as many words that "a belt leaves
along its port's facing" is this project's authoring rule and not a rule read
out of the game."""

CAPSULE_SEGMENT_CM = 50.0
"""How long one box of a belt's clearance chain is, at most.

Ours, and a resolution rather than a bound.  ``belt.clearance`` states the box
-- ``Min = (-L/2, -79, -15)``, ``Max = (L/2, 79, 15)`` about each segment's own
axis -- but leaves ``GetNextDistanceExceedingTolerance`` unread, so the game's
own segment LENGTHS cannot be reproduced.  Laying our own shorter boxes makes
the chain hug the spline more closely than the game's, never less.
"""

CURVATURE_SAMPLES = 2048
"""Parameter steps per segment the curvature check inverts arc length with.

Ours, and a resolution: :data:`flab2bp.sfy.layout.splines.DISTANCE_SAMPLES` is
finer still, and this is the step that keeps a whole-designer validation inside
a second.  At 2048 the sample lands within a fiftieth of a centimetre of the
distance asked for, four orders below the 50 cm spacing the rule samples at.
"""

DEG_TO_RAD = 0.017453292
"""The degrees-to-radians float ``ValidateIncline`` multiplies ``mMaxIncline`` by
(``belt.incline``, ``0xaa57de``).  Not ``math.pi / 180``: the game's own
constant is a ``float`` and the branch is a strict compare, so at exactly the
limit the two disagree."""

HALF_PI_F32 = 1.5707963705062866
"""The ``pi/2`` ``ValidateIncline`` subtracts ``acos`` from (``0xaa57ce``,
``0xaa57d2``).  Unreal's ``PI`` is a ``float``, so the game's right angle is
4.4e-8 rad off ``math.pi / 2`` -- which is the whole elevation of a level chord
and is why this is written out rather than computed."""

ZERO_NORMAL = 1e-8
"""The SQUARED length below which ``FVector::GetSafeNormal`` hands back
``ZeroVector`` (``belt.incline`` ``0xaa576a``, ``belt.curvature``
``0xaa53a4``/``0xaa53ab``).  It is squared, not a length, and the zero vector it
returns is USED by both callers rather than ending the comparison."""

BELT_CLEARANCE_HALF_WIDTH_CM = 79.0
BELT_CLEARANCE_HALF_HEIGHT_CM = 15.0
"""``belt.clearance``'s box about a segment's axis: ``Min = (-L/2, -79, -15)``,
``Max = (L/2, 79, 15)``, from ``AFGBuildableConveyorBelt::CreateClearanceData``
(``0x4d7d4a``, ``0x4d7d58``).  Read out of the game, not measured off a mesh --
the Mk1 belt's own mesh is 89.1 cm across and substituting it would refuse
placements the game accepts."""

CURVATURE_SAMPLES_PER_CM = 0.02
CURVATURE_RADIUS_SCALE = 1.5
CURVATURE_RADIUS_OFFSET_CM = 15.0
"""``ValidateCurvature``'s own three constants (``belt.curvature``: ``0xaa52dd``
``mulss 0.02``, ``0xaa54e2`` ``mulss 1.5``, ``0xaa54ee`` ``subss 15.0``)."""

_HARD_CLEARANCE_UNREAD = (
    "buildable.clearance is partial: the box-against-box decision is in "
    "AFGHologram::TestClearanceOverlap (0xad6790, 11457 bytes), which the "
    "extraction did not follow, so the game's own tolerance and its "
    "soft-versus-hard distinction are unknown and this check's silence about "
    "them proves nothing."
)
#: What ``belt.capsule`` skips, with ``{lift}`` left for the paragraph about the
#: lift boxes in the placement being judged. :func:`_capsule_unread` fills it:
#: :func:`lift_half_width` has two branches and a constant cannot say which one
#: a given registry took, so the sentence is built from what actually happened.
_CAPSULE_UNREAD = (
    "belt.clearance leaves two things unread, so this check's silence about "
    "them proves nothing: the two flag bytes of FFGClearanceData, where a "
    "CT_Soft marking would live, so every box in the chain is treated as hard; "
    "and the tolerance arithmetic inside "
    "UFGSplineMeshGenerationLibrary::GetNextDistanceExceedingTolerance, so the "
    "game's own segment lengths cannot be reproduced and the chain is cut at "
    "this project's own 50 cm instead. The exclusion of the box a wired port "
    "sits inside rests on the same unread AFGHologram::TestClearanceOverlap "
    "that keeps buildable.clearance partial. {lift}"
)
_LIFT_BOX_UNREAD = (
    "A conveyor lift's box is here too, and lift.clearance is partial for a "
    "third reason -- but no longer for its width: the half-extent "
    "AFGBuildableConveyorLift::FitClearance builds the box from is the module "
    "global at 0x19B8118, the static "
    "AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D, which sfy-native quotes by "
    "its PDB symbol into registry.json's lift_clearance_half_extent_cm -- an "
    "INITIALISER, what the image holds before the game runs. THIS PLACEMENT "
    "USED {widths}. What is still unread is where along its axis the game puts "
    "that box: FitClearance scales the centre by a third vector reached through "
    "a pointer nothing names, so the box here is centred between the lift's two "
    "ends, which is this project's reading of the span the rule does state."
)
_NO_LIFT_BOX = (
    "A conveyor lift's box would be judged here too, and how wide one is rests "
    "partly on a reading of ours -- but this placement carries no lift, so "
    "nothing here turns on it."
)
_LIFT_WIDTH_FROM_LIMIT = (
    "registry.json's lift_clearance_half_extent_cm, which is the game's own "
    "half-extent out of AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D and not a "
    "reading of ours"
)
_LIFT_WIDTH_FROM_PORTS = (
    "this project's own M2 reading -- half the connector clearance "
    "registry.json puts on the lift's two ports, which is a number the game "
    "keeps about a lift's CONNECTIONS and not about its box -- because this "
    "registry carries no lift_clearance_half_extent_cm"
)
_NO_BOUNDARY = (
    "no belt in this placement flags an end as a boundary end, so this is a fragment "
    "rather than a build whose external inputs and outputs are missing, and there is "
    "nothing here to hold to a wall"
)
_NO_WIRES = (
    "the placement carries no wires, so there is nothing here to judge: it is a "
    "fragment rather than a build, and nothing in the file says whether power was "
    "left out or forgotten"
)

_FIXTURE_DIR = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "sfy"


class Severity(StrEnum):
    """What a finding means for the build.

    Two values, not the DSP judge's three.  ``ERROR`` is a placement this
    project will not author; ``INFO`` is a check saying what it did not cover.
    There is no ``WARNING``, because nothing here emits one: a severity no
    finding carries is a promise the report does not keep, and a reader who
    filtered on it would be filtering on nothing.
    """

    ERROR = "error"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    message: str
    objects: tuple[int, ...] = ()
    detail: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Report:
    findings: tuple[Finding, ...]
    #: Check ids that were evaluated over EVERYTHING they claim to cover.  One
    #: listed here and carrying no finding is a real pass.
    checks_run: tuple[str, ...] = ()
    #: Check ids that could not be evaluated, in whole or in part.  A check that
    #: names a ``partial`` rule lives here permanently, and says why in an
    #: ``INFO`` finding: the part of the comparison nobody has read is coverage
    #: it does not have, and an unvalidated build must never read as a clean one.
    skipped: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(f.severity is Severity.ERROR for f in self.findings)

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    def by_check(self, check: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.check == check)


# --- oriented boxes --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorldBox:
    """One clearance box where it actually stands, as an oriented box.

    ``axes`` are the box's own three unit axes in world space and ``half`` its
    half extent along each of them, so a corner is
    ``centre + sum(+-half[k] * axes[k])``.

    ``reach`` is the half extent of the box's world-axis-aligned bounding box,
    computed once at construction.  It is what :func:`_apart` rejects a pair on
    before anything runs a separating-axis test, and on a designer full of belts
    that broad phase is the difference between a report and a coffee break.
    """

    owner: int
    label: str
    centre: Vector
    axes: tuple[Vector, Vector, Vector]
    half: Vector
    soft: bool = False
    exclude_for_snapping: bool = False
    reach: Vector = field(default=(0.0, 0.0, 0.0), init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reach",
            tuple(sum(self.half[k] * abs(self.axes[k][i]) for k in range(3)) for i in range(3)),
        )

    def holds(self, point: Vector) -> bool:
        """Whether ``point`` is inside the box, a face counting as inside."""
        offset = (
            point[0] - self.centre[0],
            point[1] - self.centre[1],
            point[2] - self.centre[2],
        )
        return all(abs(_dot(offset, self.axes[k])) <= self.half[k] + TOUCH_CM for k in range(3))

    def corners(self) -> tuple[Vector, ...]:
        out: list[Vector] = []
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    signs = (sx, sy, sz)
                    out.append(
                        tuple(  # type: ignore[arg-type]
                            self.centre[i]
                            + sum(signs[k] * self.half[k] * self.axes[k][i] for k in range(3))
                            for i in range(3)
                        )
                    )
        return tuple(out)


def _place_box(box: ClearanceBox, transform: Transform, owner: int, label: str) -> WorldBox:
    """``box`` on an actor standing at ``transform``.

    The composition itself -- the box's own ``RelativeTransform`` and then the
    actor's -- is :func:`flab2bp.sfy.geometry.placed_box`, which the pole placer
    and the row builder measure with too; what is added here is the flags and the
    label a finding names the box by.
    """
    centre, axes, half = placed_box(box, transform)
    return WorldBox(
        owner=owner,
        label=label,
        centre=centre,
        axes=axes,
        half=half,
        soft=box.soft,
        exclude_for_snapping=box.exclude_for_snapping,
    )


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit(v: Vector) -> Vector:
    scale = math.sqrt(_dot(v, v))
    if scale == 0.0:
        raise ValueError("a zero vector has no direction")
    return (v[0] / scale, v[1] / scale, v[2] / scale)


def _apart(a: WorldBox, b: WorldBox) -> bool:
    """Whether the two boxes' world bounding boxes miss each other.

    The broad phase.  A world axis that separates the two AABBs separates the
    boxes inside them, so a ``True`` here is a real answer and not a guess --
    and it costs three subtractions where :func:`_penetration` costs fifteen
    projections over eight products each.
    """
    return any(
        abs(b.centre[i] - a.centre[i]) > a.reach[i] + b.reach[i] + TOUCH_CM for i in range(3)
    )


def _penetration(a: WorldBox, b: WorldBox) -> float:
    """How deep two oriented boxes lap, or ``0.0`` if a plane separates them.

    The separating-axis test: two boxes are disjoint exactly when one of the
    fifteen candidate axes -- each box's three, and the nine cross products --
    separates their projections.  The smallest overlap over all fifteen is the
    penetration depth, which is what lets a caller forgive a lap of a few
    centimetres without forgiving a machine driven through a machine.
    """
    axes: list[Vector] = [*a.axes, *b.axes]
    for i in a.axes:
        for j in b.axes:
            candidate = _cross(i, j)
            if _dot(candidate, candidate) > 1e-12:
                axes.append(_unit(candidate))
    delta = (b.centre[0] - a.centre[0], b.centre[1] - a.centre[1], b.centre[2] - a.centre[2])
    depth = math.inf
    for axis in axes:
        reach = sum(a.half[k] * abs(_dot(axis, a.axes[k])) for k in range(3))
        reach += sum(b.half[k] * abs(_dot(axis, b.axes[k])) for k in range(3))
        overlap = reach - abs(_dot(delta, axis))
        if overlap <= 0.0:
            return 0.0
        depth = min(depth, overlap)
    return depth


# --- the belt's own clearance chain ----------------------------------------


def _sample_spline(points: Sequence[tuple[Vector, Vector, Vector]], count: int) -> list[Vector]:
    """``count + 1`` points evenly spaced by ARC LENGTH along a spline."""
    total = spline_length(points)
    targets = [i * total / count for i in range(count + 1)]
    out: list[Vector] = [points[0][0]]
    index = 1
    walked = 0.0
    previous = points[0][0]
    steps = 256
    for s in range(len(points) - 1):
        p0, _, leave = points[s]
        p1, arrive, _ = points[s + 1]
        for i in range(1, steps + 1):
            current = hermite(p0, leave, p1, arrive, i / steps)
            span = math.dist(previous, current)
            while index <= count and walked + span >= targets[index]:
                share = 0.0 if span == 0.0 else (targets[index] - walked) / span
                out.append(
                    (
                        previous[0] + share * (current[0] - previous[0]),
                        previous[1] + share * (current[1] - previous[1]),
                        previous[2] + share * (current[2] - previous[2]),
                    )
                )
                index += 1
            walked += span
            previous = current
    while index <= count:
        out.append(points[-1][0])
        index += 1
    return out


def _belt_chain(run: BeltRun) -> tuple[WorldBox, ...]:
    """The chain of boxes ``belt.clearance`` lays along one belt.

    One box per segment, ``158 cm`` wide and ``30 cm`` tall about the segment's
    own axis and as long as the segment, placed by the segment's transform --
    ``AFGBuildableConveyorBelt::CreateClearanceData``, read whole.  The number
    of segments is the part that rule leaves unread, so the chain is cut at
    :data:`CAPSULE_SEGMENT_CM`, which is ours.
    """
    total = spline_length(run.points)
    count = max(1, math.ceil(total / CAPSULE_SEGMENT_CM))
    samples = _sample_spline(run.points, count)
    chain: list[WorldBox] = []
    for i in range(count):
        head, tail = samples[i], samples[i + 1]
        span = math.dist(head, tail)
        if span <= 0.0:
            continue
        forward = _unit((tail[0] - head[0], tail[1] - head[1], tail[2] - head[2]))
        side = _cross((0.0, 0.0, 1.0), forward)
        if _dot(side, side) <= 1e-12:  # a vertical run has no natural side
            side = _cross((0.0, 1.0, 0.0), forward)
        side = _unit(side)
        up = _unit(_cross(forward, side))
        chain.append(
            WorldBox(
                owner=run.id,
                label=f"segment {i}",
                centre=tuple(  # type: ignore[arg-type]
                    (head[k] + tail[k]) / 2.0 for k in range(3)
                ),
                axes=(forward, side, up),
                half=(
                    span / 2.0,
                    BELT_CLEARANCE_HALF_WIDTH_CM,
                    BELT_CLEARANCE_HALF_HEIGHT_CM,
                ),
            )
        )
    return tuple(chain)


def beam_box(beam: BeamObj, registry: Registry) -> WorldBox:
    """Project envelope of the native +X beam, using mLength and mSize, not scale."""
    size = registry.buildables[beam.class_name].beam_size_cm
    if size is None:
        raise ValueError(f"{beam.class_name} has no sourced beam cross-section")
    box = ClearanceBox(
        (0.0, -size / 2, -size / 2),
        (beam.length_cm, size / 2, size / 2),
        True,
        (0.0, 0.0, 0.0),
    )
    return _place_box(box, beam.pose.transform(), beam.id, f"beam {beam.id}")


def passthrough_box(hole: PassthroughObj, registry: Registry) -> WorldBox:
    """Dynamic middle envelope; cap coverage is disclosed by geom.dynamic."""
    buildable = registry.buildables[hole.class_name]
    bounds = buildable.mesh_bounds_cm
    if bounds is None:
        raise ValueError(f"{hole.class_name} has no sourced passthrough mesh bounds")
    low, high = bounds
    # Pipe middle mesh runs along +X and ConstructMeshes turns it along Up;
    # the lift middle mesh is already a horizontal ring in XY.
    axes = (0, 1) if "Lift" in hole.class_name else (1, 2)
    half = tuple(max(abs(low[k]), abs(high[k])) for k in axes)
    box = ClearanceBox(
        (-half[0], -half[1], -hole.thickness_cm / 2),
        (half[0], half[1], hole.thickness_cm / 2),
        True,
        (0.0, 0.0, 0.0),
    )
    return _place_box(box, hole.pose.transform(), hole.id, f"passthrough {hole.id}")


def pipe_chain(run: PipeRun, registry: Registry) -> tuple[WorldBox, ...]:
    """Sample the pipe's mesh envelope, not the conveyor clearance.

    This project's conservative oriented boxes use the cooked mMesh Y/Z
    bounds. They are not claimed to reproduce the unextracted native pipe
    clearance routine; pipe.capsule records that distinction.
    """
    bounds = registry.buildables[run.class_name].mesh_bounds_cm
    if bounds is None:
        raise ValueError(f"{run.class_name} has no sourced pipe mesh bounds")
    low, high = bounds
    half_y = max(abs(low[1]), abs(high[1]))
    half_z = max(abs(low[2]), abs(high[2]))
    count = max(1, math.ceil(spline_length(run.points) / CAPSULE_SEGMENT_CM))
    points = _sample_spline(run.points, count)
    boxes: list[WorldBox] = []
    for index, (start, end) in enumerate(zip(points, points[1:], strict=False)):
        length = math.dist(start, end)
        if length == 0:
            continue
        axis = _unit((end[0] - start[0], end[1] - start[1], end[2] - start[2]))
        side = _cross((0.0, 0.0, 1.0), axis)
        if _dot(side, side) <= ZERO_NORMAL:
            side = _cross((0.0, 1.0, 0.0), axis)
        side = _unit(side)
        boxes.append(
            WorldBox(
                run.id,
                f"pipe {run.id} mesh segment {index}",
                ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, (start[2] + end[2]) / 2),
                (axis, side, _unit(_cross(axis, side))),
                (length / 2, half_y, half_z),
            )
        )
    return tuple(boxes)


def lift_box(lift: LiftObj, registry: Registry) -> WorldBox:
    """The ONE box ``lift.clearance`` lays along a conveyor lift.

    ``AFGConveyorLiftHologram::UpdateClearance`` hands the lift's height and its
    mesh height to ``AFGBuildableConveyorLift::FitClearance``, which returns a
    single box spanning the lift rather than a chain along a spline -- that much
    the rule states, and it is the shape used here: the box runs from one end of
    the lift to the other, about the axis the two ends are strung along.

    **How wide it is is read too**, and out of the same rule.  ``FitClearance``
    takes the half-extent from the module global at ``0x19B8118``, which is the
    static ``AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D``; that is in
    ``.data``, which ``sfy-native``'s operand annotation will not quote as a
    constant, so the tool reads it the other way instead -- by its PDB symbol,
    through ``sfy-native data`` -- and the rule carries the bytes, the
    ``(100.0, 100.0)`` they hold and the ``(95.0, 95.0)`` left after the ``-5``
    shrink.  :func:`lift_half_width` takes that from
    :attr:`Limits.lift_clearance_half_extent_cm`, so both of this box's
    dimensions are the game's -- unless the registry carries no such limit, when
    the M2 connector reading stands in and :func:`_capsule_unread` says so.

    What is still unread is where along the axis the box *sits*: ``FitClearance``
    scales the centre by a third vector reached through a pointer nothing names,
    which is why ``lift.clearance`` is still ``partial``.  The box here is
    centred between the lift's two ends, which is this module's reading of the
    span the rule does state, and the ``belt.capsule`` skip note says so.
    """
    geometry = lift_geometry(registry, lift.class_name)
    bottom, _ = lift.bottom_end(geometry)
    top, _ = lift.top_end(geometry)
    along = _unit(tuple(top[i] - bottom[i] for i in range(3)))  # type: ignore[arg-type]
    side = _cross((0.0, 0.0, 1.0), along)
    if _dot(side, side) <= 1e-12:  # a vertical lift, which is every lift
        side = _cross((0.0, 1.0, 0.0), along)
    side = _unit(side)
    across = lift_half_width(lift, registry)
    return WorldBox(
        owner=lift.id,
        label=f"{lift.class_name} {lift.id} lift box",
        centre=tuple((bottom[k] + top[k]) / 2.0 for k in range(3)),  # type: ignore[arg-type]
        axes=(along, side, _unit(_cross(along, side))),
        half=(math.dist(bottom, top) / 2.0, across, across),
    )


def lift_half_width(lift: LiftObj, registry: Registry) -> float:
    """Half the width :func:`lift_box` gives a lift: the game's, where it is read.

    :attr:`Limits.lift_clearance_half_extent_cm` is what
    ``AFGBuildableConveyorLift::FitClearance`` builds the box from -- the two
    doubles of ``AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D``, read out of
    the shipped image by its PDB symbol, less the ``-5`` the function adds to
    each -- so where the registry carries it, this is the game's own half-extent
    and not a reading of ours.

    The fallback behind it is the M2 reading, for a registry built before that
    limit was filled: the connector clearance the lift's own two ports carry
    (``UFGFactoryConnectionComponent``'s ``mClearance``, 200 cm) taken as the
    box's full width. That is this project's reading of a number the game keeps
    about a lift's *connections* rather than about its box, and the
    ``belt.capsule`` skip note says which of the two a given run took --
    :func:`_lift_width_reading` is the branch, and :func:`_capsule_unread` is
    what puts it in words. A lift class with neither is refused rather than
    given a number: an invented width would be this module inventing geometry,
    which is the one thing it may not do.
    """
    return _lift_width_reading(lift, registry)[0]


def _lift_width_reading(lift: LiftObj, registry: Registry) -> tuple[float, str]:
    """:func:`lift_half_width`'s answer, and where it was read, in words.

    The two travel together because a reader of a report has to be able to tell
    the game's own half-extent from this project's stand-in for it, and only the
    branch that ran knows which one this was.
    """
    half_extent = registry.limits.lift_clearance_half_extent_cm
    if half_extent is not None:
        return half_extent, _LIFT_WIDTH_FROM_LIMIT
    buildable = registry.buildables.get(lift.class_name)
    clearances = [
        p.clearance for p in (buildable.ports if buildable else ()) if p.clearance is not None
    ]
    if not clearances:
        raise ValueError(
            f"the registry gives {lift.class_name} no connector clearance on either port and "
            "no lift_clearance_half_extent_cm, so there is no width to give its clearance box"
        )
    return max(clearances) / 2.0, _LIFT_WIDTH_FROM_PORTS


def _capsule_unread(ctx: Context) -> str:
    """:data:`_CAPSULE_UNREAD` with the lift paragraph this placement earned.

    ``lift_half_width`` has two branches, so a constant cannot state which width
    the check used without being wrong half the time. The distinct readings the
    placement's lifts actually got are named here instead, each with its number
    and its source; a placement with no lift says that rather than claim one.
    """
    readings = sorted({_lift_width_reading(lift, ctx.registry) for lift in ctx.placement.lifts})
    if not readings:
        return _CAPSULE_UNREAD.format(lift=_NO_LIFT_BOX)
    widths = "; ".join(f"{half:g} cm each way, from {source}" for half, source in readings)
    return _CAPSULE_UNREAD.format(lift=_LIFT_BOX_UNREAD.format(widths=widths))


# --- the context every check is handed -------------------------------------


@dataclass
class Context:
    """A placement, what it was meant to realise, and the game data to judge it by."""

    placement: SfyPlacement
    spec: SfyBuildSpec | None
    registry: Registry
    rules: Mapping[str, HologramRule]
    #: The templates ``roundtrip`` writes with, or ``None`` to take the repo's
    #: own blueprint corpus.
    library: TemplateLibrary | None = None
    skipped: dict[str, str] = field(default_factory=dict)

    def skip(self, cid: str, reason: str) -> Finding:
        """Record that ``cid`` did not cover everything it claims to, and say why."""
        self.skipped[cid] = reason
        return Finding(cid, Severity.INFO, reason)

    def finding(self, cid: str, message: str, *objects: int, **detail: object) -> Finding:
        return Finding(cid, Severity.ERROR, message, tuple(objects), detail)

    @cached_property
    def placed(self) -> dict[int, Placed]:
        return {obj.id: obj for obj in self.placement.objects}

    @cached_property
    def boxes(self) -> tuple[WorldBox, ...]:
        """Every registry clearance box in the build, where it stands.

        Belts are not here: a conveyor carries no ``mClearanceData`` of its own
        (``registry.json`` gives ``Build_ConveyorBeltMk1_C`` none), because the
        hologram builds one per spline segment at placement time.  That is
        :func:`_belt_chain`'s job.  Nor are lifts, for the same reason and with
        the same answer: a lift's class carries no box either, and
        ``AFGConveyorLiftHologram::UpdateClearance`` builds one at placement
        time out of the height -- :func:`lift_box`.
        """
        out: list[WorldBox] = []
        for obj in self.placement.objects:
            if isinstance(obj, BeltRun | LiftObj | PipeRun | WireObj):
                continue
            buildable = self.registry.buildables.get(obj.class_name)
            if buildable is None:
                continue
            if isinstance(obj, BeamObj):
                out.append(beam_box(obj, self.registry))
                continue
            if isinstance(obj, PassthroughObj):
                out.append(passthrough_box(obj, self.registry))
                continue
            transform = obj.pose.transform()
            for i, box in enumerate(buildable.clearance):
                label = f"{obj.class_name} {obj.id} box {i}"
                out.append(_place_box(box, transform, obj.id, label))
        return tuple(out)

    @cached_property
    def belt_chains(self) -> dict[int, tuple[WorldBox, ...]]:
        return {run.id: _belt_chain(run) for run in self.placement.belts}

    @cached_property
    def pipe_chains(self) -> dict[int, tuple[WorldBox, ...]]:
        return {run.id: pipe_chain(run, self.registry) for run in self.placement.pipes}

    @cached_property
    def conveyor_boxes(self) -> dict[int, tuple[WorldBox, ...]]:
        """Every conveyor's own clearance, by object id: belt chains and lift boxes.

        The two are built differently -- one box per spline segment for a belt
        (``belt.clearance``), one box spanning the whole lift for a lift
        (``lift.clearance``) -- and judged together, because what they may not
        lap is the same list.
        """
        return {
            **self.belt_chains,
            **{
                lift.id: (lift_box(lift, self.registry),)
                for lift in self.placement.lifts
                if abs(lift.height_cm) > 0.0
            },
        }

    @cached_property
    def wired_port_boxes(self) -> dict[int, frozenset[int]]:
        """Conveyor id -> indices into :attr:`boxes` of the boxes holding its own ports.

        The narrow form of "a conveyor is not tested against what it is wired
        to".
        A Constructor's ``Output0`` sits at ``(0, 300, 100)`` inside a hard box
        that runs to ``y = 500``, so the belt starting there starts 200 cm
        inside the machine and the game must be excluding something; what it
        excludes is unread (``TestClearanceOverlap``).  Excluding the ONE box
        the port is inside leaves every other box of the same buildable --
        an Assembler's second, upper box, for instance -- under test.
        """
        out: dict[int, set[int]] = {}
        for link in self.placement.links:
            for side, other in ((link.a, link.b), (link.b, link.a)):
                run = self.placed.get(other[0])
                if not isinstance(run, BeltRun | LiftObj | PipeRun):
                    continue
                where = self.world_port(*side)
                if where is None:
                    continue
                for i, box in enumerate(self.boxes):
                    if box.owner == side[0] and box.holds(where):
                        out.setdefault(run.id, set()).add(i)
        return {run_id: frozenset(indices) for run_id, indices in out.items()}

    @cached_property
    def connection_points(self) -> dict[frozenset[int], tuple[Vector, ...]]:
        """Where two linked objects meet, in world space, by the pair they join."""
        out: dict[frozenset[int], list[Vector]] = {}
        for link in self.placement.links:
            key = frozenset((link.a[0], link.b[0]))
            for side in (link.a, link.b):
                where = self.world_port(*side)
                if where is not None:
                    out.setdefault(key, []).append(where)
        return {pair: tuple(points) for pair, points in out.items()}

    @cached_property
    def partners(self) -> dict[int, frozenset[int]]:
        """Which objects each object is wired to by a :class:`Link`."""
        joined: dict[int, set[int]] = {}
        for link in self.placement.links:
            joined.setdefault(link.a[0], set()).add(link.b[0])
            joined.setdefault(link.b[0], set()).add(link.a[0])
        return {oid: frozenset(ids) for oid, ids in joined.items()}

    @cached_property
    def hydraulic_graph(self) -> dict[tuple[int, str], set[tuple[int, str]]]:
        return _fluid_graph(self, forward=True)

    @cached_property
    def pump_inlets(self) -> frozenset[tuple[int, str]]:
        return frozenset(
            (obj.id, port.name)
            for obj in self.placement.pipe_attachments
            if (buildable := self.registry.buildables[obj.class_name]).native_class
            == "FGBuildablePipelinePump"
            for port in buildable.ports
            if port.kind == "pipe" and port.direction == "input"
        )

    def port(self, oid: int, name: str) -> Port | None:
        obj = self.placed.get(oid)
        if obj is None or isinstance(obj, WireObj):
            return None
        buildable = self.registry.buildables.get(obj.class_name)
        if buildable is None:
            return None
        return next((p for p in buildable.ports if p.name == name), None)

    def world_port(self, oid: int, name: str) -> Vector | None:
        """Where a port sits in the world -- and, for a conveyor, which end it is.

        A conveyor's two connections both sit at the actor's origin
        (``registry.json`` gives both ``ConveyorAny`` ports translation zero),
        so ``world_port`` cannot tell them apart; the run's own ends can, and
        ``flow`` says which is which.  A LIFT's ends are the same question with
        a different answer: the game moves them at runtime, to the actor and to
        ``mTopTransform`` (``lift.connectors``), which is what
        :meth:`LiftObj.bottom_end <flab2bp.sfy.layout.model.LiftObj.bottom_end>`
        works out.
        """
        obj = self.placed.get(oid)
        if obj is None:
            return None
        if isinstance(obj, PipeRun):
            if self.port(oid, name) is None:
                return None
            start, _ = pipe_ends(self.registry, obj.class_name)
            snapped = obj.snapped_passthroughs[0 if name == start else 1]
            hole = self.placed.get(snapped) if snapped is not None else None
            if isinstance(hole, PassthroughObj):
                return hole.pose.location
            return obj.start if name == start else obj.end
        if isinstance(obj, BeltRun):
            entry, _ = belt_ends(self.registry, obj.class_name)
            return obj.start if name == entry else obj.end
        if isinstance(obj, LiftObj):
            return self.lift_end(obj, name)[0]
        if isinstance(obj, WireObj):
            return None
        port = self.port(oid, name)
        if port is None:
            return None
        return world_port(obj.pose.transform(), port)

    def lift_end(self, lift: LiftObj, name: str) -> tuple[Vector, Vector]:
        """``(where this end of the lift is, which way it faces)``.

        ``flow`` names the two: the entry is ``mConnection0``, which
        ``SetupConnections`` puts at the actor, and the exit ``mConnection1`` at
        ``mTopTransform``.  Which is physically higher depends on the sign of
        the height and not on either name.
        """
        geometry = lift_geometry(self.registry, lift.class_name)
        entry, _ = belt_ends(self.registry, lift.class_name)
        return lift.bottom_end(geometry) if name == entry else lift.top_end(geometry)

    def port_facing(self, oid: int, name: str) -> Vector | None:
        """Which way a port faces in the world, for an object that has a pose.

        The registry's own answer through
        :func:`~flab2bp.sfy.geometry.port_forward`, except for a lift, whose two
        connections are moved at runtime and whose top carries a yaw of its own.
        """
        obj = self.placed.get(oid)
        if isinstance(obj, PipeRun):
            start, end = pipe_ends(self.registry, obj.class_name)
            if name not in (start, end):
                return None
            tangent = obj.points[0][2] if name == start else obj.points[-1][1]
            normal = (0.0, 0.0, 0.0) if _dot(tangent, tangent) < ZERO_NORMAL else _unit(tangent)
            return (-normal[0], -normal[1], -normal[2]) if name == start else normal
        if isinstance(obj, LiftObj):
            return self.lift_end(obj, name)[1]
        if obj is None or isinstance(obj, BeltRun | WireObj):
            return None
        port = self.port(oid, name)
        if port is None:
            return None
        return port_forward(obj.pose.transform(), port)

    def group_for(self, machine: MachineObj) -> SfyMachineGroup | None:
        if self.spec is None:
            return None
        return next(
            (
                g
                for g in self.spec.groups
                if g.machine_class == machine.class_name and g.recipe_class == machine.recipe_class
            ),
            None,
        )


Check = Callable[[Context], Iterable[Finding]]
CHECKS: dict[str, Check] = {}
#: Check ids that cannot be evaluated without an :class:`SfyBuildSpec`.
NEEDS_SPEC: set[str] = set()
#: Check id -> the ``hologram_rules.json`` rule it enforces, or :data:`PROJECT`.
RULE_FOR: dict[str, str] = {}


def check[F: Check](cid: str, *, needs_spec: bool = False, rule: str = PROJECT) -> Callable[[F], F]:
    def register(fn: F) -> F:
        CHECKS[cid] = fn
        RULE_FOR[cid] = rule
        if needs_spec:
            NEEDS_SPEC.add(cid)
        return fn

    return register


# --- geometry --------------------------------------------------------------


@check("geom.bounds")
def _bounds(ctx: Context) -> Iterable[Finding]:
    """Nothing may stand outside the Blueprint Designer's own volume.

    The box is ``[-half, half]^2 x [0, height]`` of the designer the placement
    declares, and both numbers are the game's: the cell count is the designer
    buildable's ``designer_dims`` and the centimetres per cell the shipped
    foundation's own footprint (:func:`flab2bp.sfy.spec.designer`).

    The rule is this project's own.  ``AFGBuildableBlueprintDesigner`` clips
    what sticks out rather than refusing it, and no rule in
    ``hologram_rules.json`` was read that turns an over-hanging hologram away --
    so this is us refusing to author a build the designer would quietly cut in
    half, not the hologram refusing anything.
    """
    designer = ctx.placement.designer
    half, height = designer.half_cm, designer.height_cm

    def outside(point: Vector) -> bool:
        return (
            abs(point[0]) > half + TOUCH_CM
            or abs(point[1]) > half + TOUCH_CM
            or point[2] < -TOUCH_CM
            or point[2] > height + TOUCH_CM
        )

    chains = {**ctx.conveyor_boxes, **ctx.pipe_chains}
    for obj in ctx.placement.objects:
        if isinstance(obj, WireObj):
            continue
        points: list[Vector] = []
        if isinstance(obj, BeltRun | PipeRun):
            points += [location for location, _, _ in obj.points]
        elif isinstance(obj, LiftObj):
            ends = belt_ends(ctx.registry, obj.class_name)
            points += [ctx.lift_end(obj, name)[0] for name in ends]
        else:
            points.append(obj.pose.location)
        for box in chains.get(obj.id, ()):
            points += list(box.corners())
        for box in ctx.boxes:
            if box.owner == obj.id:
                points += list(box.corners())
        stray = [p for p in points if outside(p)]
        if stray:
            yield ctx.finding(
                "geom.bounds",
                f"{obj.class_name} {obj.id} reaches {_round(stray[0])}, outside the "
                f"{designer.mark} designer's {2 * half:.0f} x {2 * half:.0f} x "
                f"{height:.0f} cm volume",
                obj.id,
                point=_round(stray[0]),
                half_cm=half,
                height_cm=height,
            )


@check("geom.hard_clearance", rule="buildable.clearance")
def _hard_clearance(ctx: Context) -> Iterable[Finding]:
    """No two hard clearance boxes may share space -- ``buildable.clearance``.

    ``AFGHologram::CheckClearance`` sweeps every hologram's boxes against every
    overlapping actor's and turns each overlap into a construct disqualifier, so
    the effect is ``refuse``.  The boxes are ``registry.json``'s, with their
    full ``RelativeTransform``, and the test is the separating-axis one the
    oriented boxes ask for.

    Soft boxes are left alone: ``Type=CT_Soft`` is the game's own marking for a
    box that may be shared, and a foundation's box is soft, which is why a
    machine may stand on one.  A box flagged ``ExcludeForSnapping`` IS tested --
    the flag excludes it from snapping, not from clearance -- and the pair is
    reported like any other.

    **What this check does not know**, and why it is always in ``skipped``:
    ``buildable.clearance`` is ``partial``.  The box-against-box decision lives
    in ``AFGHologram::TestClearanceOverlap`` (``0xad6790``, 11457 bytes), which
    the extraction did not follow, so the game's own tolerance and the exact
    meaning it gives soft-versus-hard are unread.  This check therefore enforces
    only what the read part states and forgives a lap under
    :data:`TOUCH_CM`, which is ours.
    """
    yield ctx.skip("geom.hard_clearance", _HARD_CLEARANCE_UNREAD)
    hard = [box for box in ctx.boxes if not box.soft]
    for i, first in enumerate(hard):
        for second in hard[i + 1 :]:
            if first.owner == second.owner:
                continue
            if _apart(first, second):
                continue
            depth = _penetration(first, second)
            if depth > TOUCH_CM:
                yield ctx.finding(
                    "geom.hard_clearance",
                    f"{first.label} and {second.label} lap by {depth:.1f} cm; the "
                    "build gun adds a clearance disqualifier for an overlap",
                    first.owner,
                    second.owner,
                    depth_cm=round(depth, 3),
                    exclude_for_snapping=first.exclude_for_snapping or second.exclude_for_snapping,
                )


@check("belt.capsule")
def _capsule(ctx: Context) -> Iterable[Finding]:
    """A conveyor's clearance may not lap another conveyor's, or a hard box.

    The SHAPE is the game's: ``belt.clearance`` is ``extracted``, and
    ``AFGBuildableConveyorBelt::CreateClearanceData`` lays one box per spline
    segment, ``Min = (-L/2, -79, -15)`` and ``Max = (L/2, 79, 15)`` about the
    segment's own axis.  The REFUSAL is this project's own, which is why this
    check names no rule: ``belt.clearance``'s effect is ``compute`` -- it works
    out boxes and turns nothing away -- and the rule that does refuse,
    ``buildable.clearance``, is ``partial``.

    **What this check does not know**, and why it is always in ``skipped``: two
    parts of ``belt.clearance`` were never read, and neither is guessed at here.
    The two flag bytes of ``FFGClearanceData`` -- where a ``CT_Soft`` marking
    would live -- are unread, so every box in the chain is treated as hard.  And
    the tolerance arithmetic inside ``GetNextDistanceExceedingTolerance`` is
    unread, so the game's own segment LENGTHS cannot be reproduced and the chain
    is cut at :data:`CAPSULE_SEGMENT_CM`, which is ours.

    A belt is not tested against **the one box a port it is wired to sits
    inside**.  That exclusion is forced by the game's own numbers: a
    Constructor's ``Output0`` sits at ``(0, 300, 100)`` inside a hard box that
    runs to ``y = 500``, so a belt that starts at its own port starts 200 cm
    inside the machine feeding it, and the game must be excluding something in
    the unread ``TestClearanceOverlap``.  Every OTHER box of the same buildable
    stays under test -- an Assembler's upper box is not forgiven because its
    lower one holds the port.

    Where two belts meet, the lap is forgiven up to :data:`BELT_CONNECTION_CM`,
    and only for segment pairs within one box length of the shared connection
    point: two runs joined end to end lap by a sliver where their tangents
    differ, and that is the only place they are entitled to.

    A LIFT is judged here too, with one box rather than a chain -- see
    :func:`lift_box`, and :func:`_capsule_unread`, which names the width each
    lift here actually got and where it was read.  Two conveyors WIRED to
    each other are
    not tested against each other at all when one of them is a lift's neighbour:
    a lift's box spans the lift itself, so whatever meets it meets it inside its
    own box, and the centimetre-level forgiveness two belts get end to end
    cannot express that.  The exclusion rests on the same unread
    ``TestClearanceOverlap`` as the wired-port one above, and it is this
    project's, not the game's.
    """
    yield ctx.skip("belt.capsule", _capsule_unread(ctx))
    chains = ctx.conveyor_boxes
    hard = [(i, box) for i, box in enumerate(ctx.boxes) if not box.soft]
    runs = [obj for obj in ctx.placement.objects if obj.id in chains]
    for i, run in enumerate(runs):
        for other in runs[i + 1 :]:
            lifting = isinstance(run, LiftObj) or isinstance(other, LiftObj)
            if lifting and other.id in ctx.partners.get(run.id, frozenset()):
                continue  # see the docstring: a lift and a conveyor wired to it
            near = ctx.connection_points.get(frozenset((run.id, other.id)), ())
            clash = _worst(chains[run.id], chains[other.id], near)
            if clash is not None:
                depth, first, second = clash
                yield ctx.finding(
                    "belt.capsule",
                    f"conveyor {run.id}'s clearance ({first.label}) laps conveyor "
                    f"{other.id}'s ({second.label}) by {depth:.1f} cm",
                    run.id,
                    other.id,
                    depth_cm=round(depth, 3),
                )
        excluded = ctx.wired_port_boxes.get(run.id, frozenset())
        for index, box in hard:
            if index in excluded:
                continue
            clash = _worst(chains[run.id], (box,), ())
            if clash is not None:
                depth, first, _ = clash
                yield ctx.finding(
                    "belt.capsule",
                    f"conveyor {run.id}'s clearance ({first.label}) laps {box.label} by "
                    f"{depth:.1f} cm",
                    run.id,
                    box.owner,
                    depth_cm=round(depth, 3),
                )


def _worst(
    left: Sequence[WorldBox], right: Sequence[WorldBox], near: Sequence[Vector] = ()
) -> tuple[float, WorldBox, WorldBox] | None:
    """The deepest lap between two sets of boxes, or ``None`` if there is none.

    A lap under :data:`TOUCH_CM` is arithmetic noise.  A lap under
    :data:`BELT_CONNECTION_CM` is forgiven as well, but only where BOTH boxes
    are within one box length of a point in ``near`` -- the place the two runs
    are wired together.  Ten metres down the belt, the same five centimetres is
    a belt through a belt.
    """
    found: tuple[float, WorldBox, WorldBox] | None = None
    if _spans_miss(left, right):
        return None
    for a in left:
        for b in right:
            if _apart(a, b):
                continue
            depth = _penetration(a, b)
            if depth <= TOUCH_CM:
                continue
            if depth <= BELT_CONNECTION_CM and _at_connection(a, b, near):
                continue
            if found is None or depth > found[0]:
                found = (depth, a, b)
    return found


def _spans_miss(left: Sequence[WorldBox], right: Sequence[WorldBox]) -> bool:
    """Whether the two SETS of boxes are apart, over one bounding box each.

    The same world-axis reject as :func:`_apart`, one level up.  Two belts at
    opposite ends of a designer are settled in six comparisons instead of a
    hundred boxes against a hundred boxes, and a designer full of belts is
    almost all such pairs.
    """
    if not left or not right:
        return True
    for i in range(3):
        low = max(
            min(box.centre[i] - box.reach[i] for box in left),
            min(box.centre[i] - box.reach[i] for box in right),
        )
        high = min(
            max(box.centre[i] + box.reach[i] for box in left),
            max(box.centre[i] + box.reach[i] for box in right),
        )
        if low > high + TOUCH_CM:
            return True
    return False


def _at_connection(a: WorldBox, b: WorldBox, near: Sequence[Vector]) -> bool:
    """Whether both boxes sit within one box length of a shared connection point."""
    return any(
        math.dist(a.centre, point) <= 2.0 * a.half[0]
        and math.dist(b.centre, point) <= 2.0 * b.half[0]
        for point in near
    )


@check("slab.under_every_foot")
def _slab(ctx: Context) -> Iterable[Finding]:
    """Every machine stands wholly on foundation.

    This project's own rule, and a strict one: the game lets a machine stand on
    the world's own ground, and nothing in ``hologram_rules.json`` refuses a
    machine for the floor under it -- ``AFGBuildableHologram::CheckValidFloor``
    reads ``mMaxPlacementFloorAngle`` and ``mNeedsValidFloor`` and was not
    extracted.  Inside a Blueprint Designer there IS no ground, so a machine
    with no slab under it pastes into thin air, and we refuse to author one.

    The footprint is the machine's hard clearance boxes projected onto ``XY``,
    and a foundation counts when its box's TOP is at the machine's own ``z``.
    """
    tops = [box for obj in ctx.placement.foundations for box in ctx.boxes if box.owner == obj.id]
    for machine in ctx.placement.machines:
        floor = machine.pose.z
        cover = [
            _extent(box)
            for box in tops
            if abs(max(c[2] for c in box.corners()) - floor) <= TOUCH_CM
        ]
        for box in ctx.boxes:
            if box.owner != machine.id or box.soft:
                continue
            gap = _uncovered(_extent(box), cover)
            if gap is not None:
                yield ctx.finding(
                    "slab.under_every_foot",
                    f"{machine.class_name} {machine.id} has no foundation under "
                    f"{_round((gap[0], gap[1], floor))}..{_round((gap[2], gap[3], floor))}",
                    machine.id,
                    gap=[round(v, 3) for v in gap],
                )


def _extent(box: WorldBox) -> tuple[float, float, float, float]:
    """``box``'s footprint as an axis-aligned ``(x0, y0, x1, y1)`` rectangle."""
    corners = box.corners()
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _uncovered(
    want: tuple[float, float, float, float], have: Sequence[tuple[float, float, float, float]]
) -> tuple[float, float, float, float] | None:
    """A sub-rectangle of ``want`` no rectangle in ``have`` covers, if there is one.

    Coordinate compression: every edge of every rectangle cuts the plane into
    cells, each of which is wholly inside or wholly outside each rectangle, so
    testing one point per cell is exact.
    """
    x0, y0, x1, y1 = want
    xs = sorted({x0, x1, *(v for r in have for v in (r[0], r[2]) if x0 < v < x1)})
    ys = sorted({y0, y1, *(v for r in have for v in (r[1], r[3]) if y0 < v < y1)})
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2.0, (ys[j] + ys[j + 1]) / 2.0
            if not any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in have):
                return (xs[i], ys[j], xs[i + 1], ys[j + 1])
    return None


# --- the belt's four bounds ------------------------------------------------


@check("belt.max_length", rule="belt.max_length")
def _max_length(ctx: Context) -> Iterable[Finding]:
    """A belt longer than ``mMaxSplineLength`` is refused -- ``belt.max_length``.

    ``ValidateConveyorBelt`` compares ``GetSplineLength()`` against
    ``mMaxSplineLength`` and adds ``UFGCDConveyorTooLong`` when it is strictly
    greater (``0xaa50a9 comiss``, ``0xaa50b0 jbe``), so exactly the limit is
    legal.  The measure is ARC LENGTH along the spline, which
    :func:`~flab2bp.sfy.layout.splines.spline_length` is, and the limit is
    ``registry.json``'s ``belt_max_spline_cm``.
    """
    limit = ctx.registry.limits.belt_max_spline_cm
    for run in ctx.placement.belts:
        length = spline_length(run.points)
        if length > limit:
            yield ctx.finding(
                "belt.max_length",
                f"belt {run.id} is {length:.1f} cm of spline, past the "
                f"{limit:.1f} cm the hologram allows",
                run.id,
                length_cm=round(length, 3),
                limit_cm=limit,
            )


@check("belt.min_length", rule="belt.min_length")
def _min_length(ctx: Context) -> Iterable[Finding]:
    """A belt must be longer than half a mesh segment -- ``belt.min_length``.

    ``ValidateMinLength`` sums the CHORDS between consecutive ``mSplineData``
    points and returns true as soon as the total is strictly greater than
    ``mMeshLength * 0.5001`` (``0xaa58b2 mulss``, ``0xaa59ed comiss``).  The
    floor is ``registry.json``'s ``belt_min_length_cm``, which is that product
    for the mark the registry describes; the comparison is strict, so a belt
    exactly at the floor is too short.

    The polyline is deliberately NOT the arc length ``belt.max_length`` uses:
    the two rules measure different things and they differ on a curve.
    """
    floor = ctx.registry.limits.belt_min_length_cm
    if floor is None:
        return
    for run in ctx.placement.belts:
        polyline = sum(
            math.dist(run.points[i][0], run.points[i + 1][0]) for i in range(len(run.points) - 1)
        )
        if polyline <= floor:
            yield ctx.finding(
                "belt.min_length",
                f"belt {run.id}'s points span {polyline:.2f} cm, at or under the "
                f"{floor:.2f} cm floor",
                run.id,
                polyline_cm=round(polyline, 3),
                floor_cm=floor,
            )


@check("belt.incline", rule="belt.incline")
def _incline(ctx: Context) -> Iterable[Finding]:
    """No chord may rise past ``mMaxIncline`` -- ``belt.incline``.

    ``ValidateIncline`` walks consecutive ``mSplineData`` locations, normalises
    the difference in 3-D and takes ``|PI/2 - acos(clamp(u.Z, -1, 1))|``, then
    refuses when that exceeds ``mMaxIncline * 0.017453292`` (``0xaa57eb comiss``,
    ``0xaa57ee ja``).  It is reproduced here in that form rather than as an
    ``atan2`` because the branch is strict and the game's degrees-to-radians
    constant is a ``float``: at exactly 35 degrees the two spellings disagree,
    and exactly 35 is legal.

    A descent is bounded the same way -- the absolute value makes it symmetric.
    """
    limit_deg = ctx.registry.limits.belt_max_incline_deg
    if limit_deg is None:
        return
    limit = limit_deg * DEG_TO_RAD
    for run in ctx.placement.belts:
        for i in range(len(run.points) - 1):
            head, tail = run.points[i][0], run.points[i + 1][0]
            delta = (tail[0] - head[0], tail[1] - head[1], tail[2] - head[2])
            squared = _dot(delta, delta)
            # GetSafeNormal tests the SQUARED length against 1e-8 (0xaa576a) and
            # hands back ZeroVector below it, whose Z is 0 -- so a zero chord
            # comes out level rather than being passed over.
            up = 0.0 if squared < ZERO_NORMAL else delta[2] / math.sqrt(squared)
            elevation = abs(HALF_PI_F32 - math.acos(min(1.0, max(-1.0, up))))
            if elevation > limit:
                yield ctx.finding(
                    "belt.incline",
                    f"belt {run.id} climbs {math.degrees(elevation):.2f} degrees between "
                    f"points {i} and {i + 1}, past the {limit_deg:.1f} the hologram allows",
                    run.id,
                    degrees=round(math.degrees(elevation), 4),
                    limit_deg=limit_deg,
                )


@check("belt.curvature", rule="belt.curvature")
def _curvature(ctx: Context) -> Iterable[Finding]:
    """No turn tighter than ``mBendRadius * 1.5 - 15`` -- ``belt.curvature``.

    ``AFGConveyorBeltHologram::ValidateCurvature`` (``0xaa5280``), instruction
    for instruction:

    * ``n = RoundToInt(GetSplineLength() * 0.02)`` -- one sample per 50 cm.  UE's
      ``RoundToInt`` on SSE is ``0xaa52ea addss xmm2,xmm2``, ``0xaa52ee addss
      0.5``, ``0xaa52f6 cvtss2si``, ``0xaa52fa sar esi,1``: double it, add a
      half, convert to nearest, shift back.  ``0xaa5307 jle`` returns "legal" at
      once when ``n <= 0``, so a belt under 25 cm is never curvature-checked.
    * ``step = GetSplineLength() / n`` (``0xaa5300 divss``).
    * For ``i`` in ``[0, n)``: ``A`` and ``B`` are
      ``GetTangentAtDistanceAlongSpline`` at ``i * step`` and ``(i + 1) * step``
      (``0xaa535b``, ``0xaa540f``), each through ``GetSafeNormal2D`` -- the
      ``1e-8`` / reciprocal-square-root dance at ``0xaa53d6..0xaa53e7``, which
      zeroes ``Z``.  So a climb does not COUNT as curvature; ``belt.incline``
      judges the slope.  A tangent with no horizontal part at all is a different
      matter: ``GetSafeNormal2D`` hands back ``FVector::ZeroVector``
      (``0xaa53ab``), the dot is ``0``, ``acos(0)`` is ``pi/2`` and the radius
      is ``step / (pi/2)`` -- so a vertical sample is REFUSED, not skipped.
    * ``theta = acos(clamp(A . B, -1, 1))`` (``0xaa54a3`` dot, ``0xaa54b8`` and
      ``0xaa54c5`` clamp, ``0xaa54cd call acos``).
    * The radius is ``step / theta`` -- ``0xaa54da movaps xmm1,xmm14`` (``xmm14``
      holds ``step``), ``0xaa54de divsd xmm1,xmm0`` (``xmm0`` is ``acos``'s
      answer), ``0xaa54ea cvtsd2ss`` back to a ``float`` -- and ``0xaa54f6
      comiss xmm3,xmm2`` / ``0xaa54f9 jb`` returns false when it is below
      ``mBendRadius * 1.5 - 15.0`` (``0xaa54d2`` loads ``mBendRadius``,
      ``0xaa54e2 mulss 1.5``, ``0xaa54ee subss 15.0``).  A straight pair gives
      ``theta == 0`` and the hardware division yields ``+inf``, which passes;
      that is reproduced here rather than papered over.

    ``mBendRadius`` comes from ``registry.json``'s ``belt_bend_radius_cm`` and
    the floor is not typed anywhere in this module.

    **The SCOPE is this project's own.**  ``ValidateConveyorBelt`` calls this
    only while the build gun is in the curve build mode (``0xaa515e`` reads
    ``mBuildModeCurve``, ``0xaa5187 je`` skips the call), so the game does not
    curvature-check a straight-mode belt at all.  Per ruling R8 we apply it to
    every belt we author: a blueprint is pasted, not built in a mode, and a turn
    the curve mode would refuse is one we decline to write.
    """
    bend = ctx.registry.limits.belt_bend_radius_cm
    if bend is None:
        return
    floor = bend * CURVATURE_RADIUS_SCALE - CURVATURE_RADIUS_OFFSET_CM
    for run in ctx.placement.belts:
        length = spline_length(run.points)
        count = _round_to_int(length * CURVATURE_SAMPLES_PER_CM)
        if count <= 0:
            continue
        step = length / count
        tightest = math.inf
        where = 0
        for i in range(count):
            first = _flat(tangent_at_distance(run.points, i * step, samples=CURVATURE_SAMPLES))
            second = _flat(
                tangent_at_distance(run.points, (i + 1) * step, samples=CURVATURE_SAMPLES)
            )
            theta = math.acos(min(1.0, max(-1.0, _dot(first, second))))
            radius = math.inf if theta == 0.0 else step / theta
            if radius < tightest:
                tightest, where = radius, i
        if tightest < floor:
            yield ctx.finding(
                "belt.curvature",
                f"belt {run.id} turns on {tightest:.1f} cm at sample {where}, inside the "
                f"{floor:.1f} cm floor ({bend:.1f} * 1.5 - 15)",
                run.id,
                radius_cm=round(tightest, 3),
                floor_cm=floor,
            )


@check("pipe.max_length", rule="pipe.max_length")
def _pipe_max_length(ctx: Context) -> Iterable[Finding]:
    """Enforce pipe.max_length's strict arc-length comparison."""
    limit = ctx.registry.limits.pipe_max_spline_cm
    for run in ctx.placement.pipes:
        length = spline_length(run.points)
        if length > limit:
            yield ctx.finding(
                "pipe.max_length",
                f"pipe {run.id} is {length:.2f} cm, past the {limit:.2f} cm native limit",
                run.id,
                length_cm=length,
                limit_cm=limit,
            )


@check("pipe.min_length", rule="pipe.min_length")
def _pipe_min_length(ctx: Context) -> Iterable[Finding]:
    """pipe.min_length requires chord sum strictly greater than mMeshLength / 2."""
    for run in ctx.placement.pipes:
        mesh = ctx.registry.buildables[run.class_name].mesh_length_cm
        if mesh is None:
            yield ctx.skip("pipe.min_length", f"pipe {run.id}'s mesh length is unknown")
            continue
        length = sum(
            math.dist(a[0], b[0]) for a, b in zip(run.points, run.points[1:], strict=False)
        )
        if length <= mesh / 2:
            yield ctx.finding(
                "pipe.min_length",
                f"pipe {run.id}'s chord length {length:.2f} cm is not above {mesh / 2:.2f} cm",
                run.id,
                length_cm=length,
                floor_cm=mesh / 2,
            )


@check("pipe.curvature", rule="pipe.curvature")
def _pipe_curvature(ctx: Context) -> Iterable[Finding]:
    """pipe.curvature samples full 3D tangents at mMinBendRadius / 2 spacing.

    RVA 0xae9380 uses RoundToInt(length * 2 / radius), and refuses a
    sampled radius below mMinBendRadius * 1.05. There is no pipe incline rule.
    """
    bend = ctx.registry.limits.pipe_min_bend_radius_cm
    for run in ctx.placement.pipes:
        length = spline_length(run.points)
        count = _round_to_int(length * 2 / bend)
        if count <= 0:
            continue
        step = length / count
        for index in range(count):
            a = tangent_at_distance(run.points, index * step, samples=CURVATURE_SAMPLES)
            b = tangent_at_distance(run.points, (index + 1) * step, samples=CURVATURE_SAMPLES)
            a = (0.0, 0.0, 0.0) if _dot(a, a) < ZERO_NORMAL else _unit(a)
            b = (0.0, 0.0, 0.0) if _dot(b, b) < ZERO_NORMAL else _unit(b)
            angle = math.acos(min(1.0, max(-1.0, _dot(a, b))))
            radius = math.inf if angle == 0 else step / angle
            if radius < bend * 1.05:
                yield ctx.finding(
                    "pipe.curvature",
                    f"pipe {run.id}'s 3D bend radius {radius:.2f} cm is below {bend * 1.05:.2f} cm",
                    run.id,
                    radius_cm=radius,
                    distance_cm=(index + 0.5) * step,
                )
                break


def _fluid_graph(
    ctx: Context, *, forward: bool = False
) -> dict[tuple[int, str], set[tuple[int, str]]]:
    """Connection-component graph; machines never join their input/output fluids."""
    graph: dict[tuple[int, str], set[tuple[int, str]]] = {}

    def join(a: tuple[int, str], b: tuple[int, str], reverse: bool = True) -> None:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set())
        if reverse:
            graph[b].add(a)

    for link in ctx.placement.links:
        a, b = ctx.port(*link.a), ctx.port(*link.b)
        if a and b and a.kind == b.kind == "pipe":
            join(link.a, link.b)
    for run in ctx.placement.pipes:
        entry, exit_ = pipe_ends(ctx.registry, run.class_name)
        join((run.id, entry), (run.id, exit_))
    for obj in ctx.placement.pipe_attachments:
        ports = [p for p in ctx.registry.buildables[obj.class_name].ports if p.kind == "pipe"]
        for a in ports:
            for b in ports:
                if a == b:
                    continue
                if forward and (a.direction == "output" or b.direction == "input"):
                    continue
                join((obj.id, a.name), (obj.id, b.name), reverse=not forward)
    return graph


def _component(
    start: tuple[int, str],
    graph: Mapping[tuple[int, str], set[tuple[int, str]]],
    *,
    stop: frozenset[tuple[int, str]] = frozenset(),
) -> set[tuple[int, str]]:
    visited: set[tuple[int, str]] = set()
    pending = [start]
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        if node not in stop:
            pending.extend(graph.get(node, ()))
    return visited


@check("pipe.fluid_requirements", rule="pipe.fluid_requirements")
def _pipe_fluids(ctx: Context) -> Iterable[Finding]:
    """pipe.fluid_requirements forbids distinct descriptors in one connected network.

    Authored item labels stand for committed descriptors. This is not a claim
    to simulate GetFluidDescriptor or to copy runtime network IDs.
    """
    graph = _fluid_graph(ctx)
    visited: set[tuple[int, str]] = set()
    for node in graph:
        if node in visited:
            continue
        component = _component(node, graph)
        visited.update(component)
        runs = {oid: run for oid, _ in component if isinstance(run := ctx.placed.get(oid), PipeRun)}
        items = {run.item_id for run in runs.values() if run.item_id}
        if len(items) > 1:
            yield ctx.finding(
                "pipe.fluid_requirements",
                f"connected pipe network mixes distinct fluids {sorted(items)}",
                *sorted(runs),
                items=sorted(items),
            )


@check("pipe.capsule")
def _pipe_capsule(ctx: Context) -> Iterable[Finding]:
    """Project mesh-envelope collision check, not unextracted pipe clearance."""
    if not ctx.placement.pipes:
        return
    yield ctx.skip(
        "pipe.capsule",
        "Pipe collision uses sampled cooked-mesh envelopes; the "
        "native pipe clearance sweep and overlap tolerance are not extracted.",
    )
    chains = {**ctx.conveyor_boxes, **ctx.pipe_chains}
    pipes = {run.id for run in ctx.placement.pipes}
    keys = list(chains)
    for index, oid in enumerate(keys):
        for other in keys[index + 1 :]:
            if oid not in pipes and other not in pipes:
                continue
            near = ctx.connection_points.get(frozenset((oid, other)), ())
            clash = _worst(chains[oid], chains[other], near)
            if clash is not None:
                yield ctx.finding(
                    "pipe.capsule",
                    f"transport {oid} and {other} intersect by {clash[0]:.2f} cm",
                    oid,
                    other,
                    depth_cm=clash[0],
                )
    for run in ctx.placement.pipes:
        excluded = ctx.wired_port_boxes.get(run.id, ())
        for index, box in enumerate(ctx.boxes):
            if box.soft or index in excluded or _owns_hole_crossing(ctx, run.id, box.owner):
                continue
            clash = _worst(ctx.pipe_chains[run.id], (box,))
            if clash is not None:
                yield ctx.finding(
                    "pipe.capsule",
                    f"pipe {run.id} intersects {box.label} by {clash[0]:.2f} cm",
                    run.id,
                    box.owner,
                    depth_cm=clash[0],
                )


def _fluid_capacity(ctx: Context) -> Iterable[Finding]:
    """Exact m3/s capacities and every machine's feed AND byproduct drainage."""
    spec = ctx.spec
    if spec is None:
        return
    labmap = load_lab_map()
    tiers = {
        labmap.machines[t.item_id]: t.cubic_metres_per_second
        for t in spec.pipe_tiers
        if t.item_id in labmap.machines
    }
    funded_families = {
        (ctx.registry.buildables[class_name].native_class, capacity)
        for class_name, capacity in tiers.items()
    }
    for run in ctx.placement.pipes:
        buildable = ctx.registry.buildables[run.class_name]
        native = buildable.pipe_flow_limit_m3s
        capacity = tiers.get(run.class_name)
        # Indicator visibility does not change a pipeline's sourced native
        # transport class or rated capacity. Do not mistake a cosmetic actor
        # variant for an unfunded tier (nor fund a faster tier by name).
        if (
            capacity is None
            and native is not None
            and (buildable.native_class, Fraction(str(native))) in funded_families
        ):
            capacity = Fraction(str(native))
        if capacity is None:
            yield ctx.finding(
                "flow.capacity", f"pipe {run.id}'s mark is not funded by the spec", run.id
            )
        else:
            if native is not None:
                capacity = min(capacity, Fraction(str(native)))
            if run.cubic_metres_per_second > capacity or run.cubic_metres_per_second < 0:
                yield ctx.finding(
                    "flow.capacity",
                    f"pipe {run.id} carries "
                    f"{run.cubic_metres_per_second} m3/s, capacity {capacity}",
                    run.id,
                    capacity=str(capacity),
                )
        if run.item_id not in spec.fluid_items:
            yield ctx.finding(
                "flow.capacity",
                f"pipe {run.id} carries non-fluid or unknown item {run.item_id!r}",
                run.id,
                item=run.item_id,
            )
    if ctx.placement.stack_lanes:
        yield from _fluid_network_capacity(ctx)
    neighbours: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for link in ctx.placement.links:
        neighbours.setdefault(link.a, []).append(link.b)
        neighbours.setdefault(link.b, []).append(link.a)
    for machine in ctx.placement.machines:
        group = ctx.group_for(machine)
        if group is None:
            continue
        ports = ctx.registry.buildables[machine.class_name].ports
        for direction, rates, verb in (
            ("input", group.inputs_per_machine, "feed"),
            ("output", group.outputs_per_machine, "drain"),
        ):
            attached: dict[str, Fraction] = {}
            for port in ports:
                if port.kind != "pipe" or port.direction != direction:
                    continue
                for oid, _ in neighbours.get((machine.id, port.name), ()):
                    pipe = ctx.placed.get(oid)
                    if isinstance(pipe, PipeRun):
                        attached[pipe.item_id] = (
                            attached.get(pipe.item_id, Fraction()) + pipe.cubic_metres_per_second
                        )
            for item, rate in rates.items():
                if item not in spec.fluid_items:
                    continue
                needed = rate * machine.clock / group.clock
                found = attached.get(item, Fraction())
                if found < needed:
                    yield ctx.finding(
                        "flow.capacity",
                        f"machine {machine.id} requires {needed} "
                        f"m3/s {item!r} {verb}; connected pipes provide {found}",
                        machine.id,
                        item=item,
                        needed=str(needed),
                        supplied=str(found),
                    )


def _round_to_int(value: float) -> int:
    """``FMath::RoundToInt`` as ``ValidateCurvature`` performs it on SSE.

    ``addss xmm2,xmm2; addss 0.5; cvtss2si; sar 1`` -- double, add a half,
    convert to the nearest integer, then shift back.  ``cvtss2si`` rounds half
    to EVEN under the default ``MXCSR``, and the doubling is precisely what
    stops that showing: the tie lands on ``2x + 0.5`` instead of on ``x``, and
    the shift recovers ``floor(x + 0.5)`` -- half UP -- for every ``x``.
    Python's ``round`` is the same half-to-even rule and ``>> 1`` the same
    arithmetic shift, so this is the instruction sequence rather than a
    paraphrase of its result.
    """
    return int(round(2.0 * value + 0.5)) >> 1


def _flat(tangent: Vector) -> Vector:
    """``FVector::GetSafeNormal2D``: flatten ``Z`` away and normalise.

    The guard is the SQUARED horizontal length against ``1e-8``
    (:data:`ZERO_NORMAL`, ``0xaa53a4`` ``comisd xmm1, xmm11``), and what it hands back is
    ``FVector::ZeroVector`` (``0xaa53ab``) rather than nothing at all.  That
    matters: the zero vector goes on into the dot product like any other, the
    dot is ``0``, ``acos(0)`` is ``pi/2``, and the radius comes out
    ``step / (pi/2)`` -- about 32 cm at the game's own 50 cm sampling, far
    inside the floor.  **A belt that climbs vertically is refused by the
    curvature rule**, not passed over, and that is the game's arithmetic and not
    an interpretation of it.
    """
    squared = tangent[0] * tangent[0] + tangent[1] * tangent[1]
    if squared < ZERO_NORMAL:
        return (0.0, 0.0, 0.0)
    scale = math.sqrt(squared)
    return (tangent[0] / scale, tangent[1] / scale, 0.0)


# --- ports -----------------------------------------------------------------


@check("ports.connected_once")
def _connected_once(ctx: Context) -> Iterable[Finding]:
    """Every belt end is wired, and no connection carries two belts.

    This project's own rule.  The game refuses a SECOND belt on an occupied
    connection -- ``ValidateConveyorBelt`` adds ``UFGCDInvalidPlacement`` for a
    snapped connection that already has something on it -- but that branch is
    recorded as prose in ``belt.max_length``'s comparison rather than extracted
    as a rule of its own, so the bound is claimed as ours rather than as the
    game's.  Nothing in the game refuses a belt for having a loose END: a player
    builds one every time.  We refuse it because a blueprint we author with a
    dangling belt is a build that silently does not run.

    A link onto a connection whose direction is ``snap_only`` or ``unknown`` is
    refused too.  ``belt.snap_directions`` states it outright: ``FCD_SNAP_ONLY``
    hands out no direction at all, which is what a conveyor pole is, so a link
    there is one this project cannot say the meaning of.

    Belts, lifts and pipes all obey this occupancy contract. Only an explicitly
    flagged boundary end must be wired ZERO times. ``flow.boundary`` proves
    that each such end belongs to a structural stack lane (or the historical
    horizontal walls for legacy conveyor fragments), rather than forgiving an
    accidentally disconnected internal run.
    """
    counts: Counter[tuple[int, str]] = Counter()
    for link in ctx.placement.links:
        counts[link.a] += 1
        counts[link.b] += 1
        for side in (link.a, link.b):
            port = ctx.port(*side)
            if port is None:
                yield ctx.finding(
                    "ports.connected_once",
                    f"object {side[0]} has no port named {side[1]!r} for a link to use",
                    side[0],
                )
            elif port.direction in ("snap_only", "unknown"):
                yield ctx.finding(
                    "ports.connected_once",
                    f"object {side[0]}'s {side[1]} is {port.direction}, which hands out "
                    "no direction (belt.snap_directions), so nothing may be wired to it",
                    side[0],
                    direction=port.direction,
                )
    for side, count in counts.items():
        if count > 1:
            yield ctx.finding(
                "ports.connected_once",
                f"object {side[0]}'s {side[1]} carries {count} links; one connection "
                "component holds one belt",
                side[0],
                links=count,
            )
    for run in chain[BeltRun | LiftObj | PipeRun](
        ctx.placement.belts, ctx.placement.lifts, ctx.placement.pipes
    ):
        ends = (pipe_ends if isinstance(run, PipeRun) else belt_ends)(ctx.registry, run.class_name)
        for end, open_by_design in zip(ends, (run.boundary_start, run.boundary_end), strict=True):
            wanted = 0 if open_by_design else 1
            found = counts[(run.id, end)]
            if found == wanted:
                continue
            why = (
                "it is flagged as a boundary end, which stops on the designer wall and "
                "takes no link"
                if open_by_design
                else "one end of a belt is one connection"
            )
            yield ctx.finding(
                "ports.connected_once",
                f"transport {run.id}'s {end} end is wired {found} times, not {wanted}: {why}",
                run.id,
                end=end,
                links=found,
                boundary=open_by_design,
            )


@check("ports.direction")
def _direction(ctx: Context) -> Iterable[Finding]:
    """A link runs from an output to an input.  This project's own rule.

    ``AFGConveyorBeltHologram::SetupSnappedConnectionDirections`` does not let a
    belt choose: ``mConnectionComponents[0]->mDirection`` is set to the snapped
    connection's ``GetCompatibleSnapDirection()`` and
    ``mConnectionComponents[1]->mDirection`` to that connection's own direction
    (``0xa8b9e4``/``0xa8b9e9``, ``0xa8b9f6``/``0xa8b9fd``).  So the belt's near
    end is whatever the port it met is compatible with, and its far end carries
    the port's direction outward, to meet the compatible thing at the other end.

    ``belt.snap_directions`` is the EVIDENCE for that and not the rule this
    check enforces, which is why the check is ours.  Its ``effect`` is ``snap``:
    the hologram does not turn a mismatched pair away, so a refusal built on it
    would be this project's judgement wearing the game's name.  What the
    instructions do show is that the assignment above **cannot produce** an
    output wired to an output or an input to an input -- a belt's two directions
    are taken from the connection it met, never chosen -- so such a link is not
    a placement the build gun refuses but a placement it never makes, and we
    decline to author one.  The check enforces no number.

    A belt's own two ends are ``any``; which is which is ``flow``'s answer in
    ``registry.json``, so the upstream side of a link must be the belt's exit
    end and the downstream side its entry.  A LIFT's two ends are ``any`` for
    the same reason and answered the same way, and the sign of its height does
    not come into it: ``reversed_swaps_flow`` is false, so items enter by
    ``mConnection0`` -- the end at the actor -- whichever way the lift runs.
    """
    for link in ctx.placement.links:
        upstream, downstream = ctx.port(*link.a), ctx.port(*link.b)
        if upstream is None or downstream is None:
            continue  # ports.connected_once reports a port that is not there
        if upstream.kind != downstream.kind or upstream.kind not in ("belt", "pipe"):
            yield ctx.finding(
                "ports.direction",
                f"cannot join {upstream.kind} to {downstream.kind}: incompatible transport media",
                link.a[0],
                link.b[0],
            )
            continue
        if upstream.kind == "pipe":
            # Pipe links are unordered on disk. Only fixed same-direction ends conflict;
            # an ANY junction is bidirectional, not a fictitious conveyor splitter.
            if upstream.direction == downstream.direction and upstream.direction in (
                "input",
                "output",
            ):
                yield ctx.finding(
                    "ports.direction",
                    f"two pipe {upstream.direction} ports cannot exchange fluid",
                    link.a[0],
                    link.b[0],
                )
            continue
        if upstream.direction not in ("output", "any"):
            yield ctx.finding(
                "ports.direction",
                f"object {link.a[0]}'s {link.a[1]} is an {upstream.direction}, so nothing "
                f"flows out of it into {link.b[0]}'s {link.b[1]}",
                link.a[0],
                link.b[0],
            )
        if downstream.direction not in ("input", "any"):
            yield ctx.finding(
                "ports.direction",
                f"object {link.b[0]}'s {link.b[1]} is an {downstream.direction}, so nothing "
                f"flows into it from {link.a[0]}'s {link.a[1]}",
                link.a[0],
                link.b[0],
            )
        for side, wanted in ((link.a, 1), (link.b, 0)):
            run = ctx.placed.get(side[0])
            if not isinstance(run, BeltRun | LiftObj):
                continue
            ends = belt_ends(ctx.registry, run.class_name)
            if side[1] != ends[wanted]:
                kind = "lift" if isinstance(run, LiftObj) else "belt"
                yield ctx.finding(
                    "ports.direction",
                    f"{kind} {run.id} meets this link by its {side[1]} end, but flow calls "
                    f"{ends[wanted]} the end items {'leave' if wanted else 'enter'} by",
                    run.id,
                )


@check("ports.position")
def _position(ctx: Context) -> Iterable[Finding]:
    """A belt starts and ends ON the ports it is wired to, facing the right way.

    This project's own rule, and the one M1b stated rather than read: nothing in
    the game refuses a spline for where it begins or which way it leaves.
    ``ValidateConveyorBelt``'s four checks are length, minimum length, incline
    and curvature, and ``flab2bp.sfy.geometry.port_forward`` says so in as many
    words -- the hologram's router is handed a connection normal, but which
    vector it hands the spline builder is inlined code the extraction did not
    unpick.  We author to the assumption, so we check it: a belt drawn a few
    centimetres off its port is a belt the game will snap somewhere else, and
    the paste will not be the build we costed.

    The slack is :data:`PORT_CM` and :data:`PORT_ANGLE_RAD`, both ours.  The
    facing is only checked where the upstream side is a buildable's port: two
    belts joined to each other share a point, and their tangents are
    :func:`~flab2bp.sfy.layout.splines.concat`'s business.

    A LIFT's two ends are held to the same centimetre, and where they are is
    the game's answer rather than the registry's: ``SetupConnections`` moves
    them to the actor and to ``mTopTransform`` (``lift.connectors``), which is
    what :meth:`Context.lift_end` works out.  A belt leaving a lift's TOP is
    held to that end's own facing -- the top yaw turns the connector with it --
    on exactly the assumption the paragraph above states, and nothing here
    checks which way a lift's end faces the port it stands ON: what the game
    does with a lift's own yaw when it snaps to a connection was not read, so
    there is no rule to hold it to and none is invented.
    """
    for link in ctx.placement.links:
        for side, other in ((link.a, link.b), (link.b, link.a)):
            run = ctx.placed.get(side[0])
            if not isinstance(run, BeltRun | LiftObj | PipeRun):
                continue
            here = ctx.world_port(*side)
            there = ctx.world_port(*other)
            if here is None or there is None:
                continue
            gap = math.dist(here, there)
            if gap > PORT_CM:
                kind = (
                    "pipe"
                    if isinstance(run, PipeRun)
                    else "lift"
                    if isinstance(run, LiftObj)
                    else "belt"
                )
                yield ctx.finding(
                    "ports.position",
                    f"{kind} {run.id}'s {side[1]} end is {gap:.2f} cm from object "
                    f"{other[0]}'s {other[1]}, past the {PORT_CM:.0f} cm this project allows",
                    run.id,
                    other[0],
                    gap_cm=round(gap, 3),
                )
        first, second = ctx.port(*link.a), ctx.port(*link.b)
        if first is not None and second is not None and first.kind == second.kind == "pipe":
            a, b = ctx.port_facing(*link.a), ctx.port_facing(*link.b)
            if a is not None and b is not None:
                angle = math.acos(min(1.0, max(-1.0, -_dot(a, b))))
                if angle > PORT_ANGLE_RAD:
                    yield ctx.finding(
                        "ports.position",
                        f"pipe connection normals do not oppose ({angle:.3f} rad)",
                        link.a[0],
                        link.b[0],
                        angle_rad=round(angle, 5),
                    )
        upstream = ctx.placed.get(link.a[0])
        run = ctx.placed.get(link.b[0])
        if isinstance(upstream, BeltRun) or not isinstance(run, BeltRun):
            continue
        facing = ctx.port_facing(*link.a)
        if facing is None:
            continue
        leave = run.points[0][2]
        if _dot(leave, leave) < 1e-12:
            continue
        angle = math.acos(min(1.0, max(-1.0, _dot(_unit(leave), facing))))
        if angle > PORT_ANGLE_RAD:
            yield ctx.finding(
                "ports.position",
                f"belt {run.id} leaves object {link.a[0]}'s {link.a[1]} {angle:.3f} rad "
                f"off its facing, past the {PORT_ANGLE_RAD} this project allows",
                run.id,
                link.a[0],
                angle_rad=round(angle, 5),
            )


# --- conveyor lifts --------------------------------------------------------


@check("lift.height")
def _lift_height(ctx: Context) -> Iterable[Finding]:
    """A lift stands between ``lift_min_cm`` and ``lift_max_cm`` tall.

    This project's own rule, over the game's own numbers.  ``lift.height_range``
    is a **clamp**: ``UpdateTopTransform`` takes ``minss`` of the wanted height
    and ``mMaximumHeight`` and ``maxss`` of that and the floor
    (``0xaa4979``/``0xaa497d``, and the sign-flipped pair at
    ``0xaa4987``..``0xaa499f``), so the hologram cannot be given an illegal
    height and there is no disqualifier to name.  We refuse instead, because a
    lift we author outside the window is one the game would silently build at a
    different height -- ending nowhere near the port it was drawn to.

    Native UpdateTopTransform derives the snapped minimum from half the hole
    thickness plus one or two steps (0xaa481f..0xaa4867), not the registry's
    initial vertical minimum. We conservatively require the larger branch;
    actor-only data does not identify the inherited sign field selecting it.
    ConfigureActor swaps slots in reverse construction (0xa6950a/0xa695ec),
    so either serialized end can carry the snap. The post-clamp remainder is
    added to the maximum too. ``ports.passthrough`` checks actual ownership.

    Without a hole, the ordinary ``lift_min_cm`` remains conservative for
    direct snaps to vertical connectors: their 2.5/3.5-step minimum override
    at 0xaa4871..0xaa48c2 is not reconstructed from actor-only placement data.
    The limit applies to the magnitude of either upward or downward travel.
    """
    limits = ctx.registry.limits
    low, high = limits.lift_min_cm, limits.lift_max_cm
    if low is None or high is None:
        return
    ordinary_limits = (low, high)
    for lift in ctx.placement.lifts:
        holes = [
            hole
            for oid in lift.snapped_passthroughs
            if oid is not None and isinstance(hole := ctx.placed.get(oid), PassthroughObj)
        ]
        low, high = ordinary_limits
        if holes:
            step = limits.lift_step_cm
            if step is None:
                yield ctx.finding("lift.height", "snapped lift height step is unknown", lift.id)
                continue
            low = max(hole.thickness_cm / 2 + 2 * step for hole in holes)
            high += min(int(hole.thickness_cm * 0.5 + step) % 100 for hole in holes)
        rise = abs(lift.height_cm)
        if low - TOUCH_CM <= rise <= high + TOUCH_CM:
            continue
        yield ctx.finding(
            "lift.height",
            f"lift {lift.id} is {rise:.0f} cm, outside the {low:.0f}..{high:.0f} cm the "
            "hologram clamps a lift into, so the game would build it at a different height",
            lift.id,
            height_cm=round(lift.height_cm, 3),
            min_cm=low,
            max_cm=high,
        )


@check("lift.step")
def _lift_step(ctx: Context) -> Iterable[Finding]:
    """A lift's height is a whole number of ``lift_step_cm``.

    This project's own rule, and the game's own arithmetic.  ``lift.step`` is a
    **snap**, not a refusal: ``UpdateTopTransform`` computes
    ``floor(raw / mStepHeight + 0.5) * mStepHeight`` -- ``0xaa4769`` ``divss``,
    ``0xaa476d`` ``addss`` the 0.5 at ``0xf6dee8``, ``0xaa4776`` ``roundps ...,
    1`` and ``0xaa477c`` ``mulss``, on the step loaded at ``0xaa46e8`` -- and
    the rounded height is what the clamp and the mesh then use.  So a height off
    the step is one the game MOVES, by up to half a step, and the lift ends
    somewhere other than the port it was drawn to.  We refuse to author one.

    A hologram snapped at slot zero carries the native half-thickness remainder:
    ``int(thickness * 0.5 + step) % 100`` (0xaa481f..0xaa4843).
    ConfigureActor swaps actor slots in reverse mode (0xa6950a/0xa695ec), so
    either actual snapped end can supply this remainder. With both ends snapped,
    TrySnapToActor instead writes the exact hole-origin difference directly to
    mTopTransform (0xa94dca..0xa94f91); that path never calls UpdateTopTransform.
    Such a bridge is constrained by its two actual hole centres, not the step.
    """
    step = ctx.registry.limits.lift_step_cm
    if not step:
        return
    for lift in ctx.placement.lifts:
        rise = abs(lift.height_cm)
        holes = [ctx.placed.get(oid) for oid in lift.snapped_passthroughs if oid is not None]
        if len(holes) == 2 and all(isinstance(hole, PassthroughObj) for hole in holes):
            # The separate passthrough check enforces exact centres and reciprocal
            # ownership; do not impose free-end snapping on a two-hole bridge.
            continue
        remainders = {
            int(hole.thickness_cm * 0.5 + step) % 100
            for hole in holes
            if isinstance(hole, PassthroughObj)
        } or {0}
        off = min(
            min((rise - remainder) % step, step - (rise - remainder) % step)
            for remainder in remainders
        )
        if off <= TOUCH_CM:
            continue
        yield ctx.finding(
            "lift.step",
            f"lift {lift.id} is {rise:.1f} cm, which is {off:.1f} cm off a multiple of the "
            f"{step:.0f} cm the hologram snaps a height to",
            lift.id,
            height_cm=round(lift.height_cm, 3),
            step_cm=step,
        )


@check("lift.top_yaw")
def _lift_top_yaw(ctx: Context) -> Iterable[Finding]:
    """A lift's top faces one of the four directions the build gun can give it.

    This project's own rule, over the game's own step.  ``lift.top_yaw`` is a
    ``compute`` rule -- it works out a number the game then writes and turns no
    placement away -- so the refusal is ours, and what it rests on is that the
    step is not a preference: ``AFGConveyorLiftHologram::GetRotationStep``
    returns **90** once the first placement point is down and 0 only while it is
    still live (``0xa7c19a`` ``xor eax,eax`` against ``0xa7c1a2`` ``mov
    eax,5Ah``), ``AFGHologram::ApplyScrollRotationTo`` rounds the yaw onto that
    lattice (``0xaafe85``..``0xaafe97``), and ``UpdateTopTransform`` stores the
    quaternion of the rotator it is handed into ``mTopTransform``
    (``0xaa49d1``, stored at ``0xaa49ee``/``0xaa4a01``).  A top yaw off the
    lattice is therefore not a yaw the build gun can produce at all: pasting one
    is authoring a lift no player could build, and the blueprint would not be
    the build that was costed.

    The step is the registry's own ``top_yaw_step_deg``, and ``top_yaw_free``
    beside it says whether the top may be turned at all -- a class that says it
    may not is held to a yaw of zero rather than to a lattice.
    """
    for lift in ctx.placement.lifts:
        geometry = lift_geometry(ctx.registry, lift.class_name)
        step = geometry.top_yaw_step_deg
        if not geometry.top_yaw_free:
            if abs(lift.top_yaw_deg) > LIFT_YAW_TOLERANCE_DEG:
                yield ctx.finding(
                    "lift.top_yaw",
                    f"lift {lift.id}'s top is turned {lift.top_yaw_deg:.1f} degrees and the "
                    "registry says this class's top yaw is not free",
                    lift.id,
                    top_yaw_deg=round(lift.top_yaw_deg, 3),
                )
            continue
        if not step:
            continue
        off = min(lift.top_yaw_deg % step, step - lift.top_yaw_deg % step)
        if off <= LIFT_YAW_TOLERANCE_DEG:
            continue
        yield ctx.finding(
            "lift.top_yaw",
            f"lift {lift.id}'s top is turned {lift.top_yaw_deg:.1f} degrees, which is "
            f"{off:.1f} off the {step:.0f} degree step the build gun turns a lift's top in",
            lift.id,
            top_yaw_deg=round(lift.top_yaw_deg, 3),
            step_deg=step,
        )


@check("lift.placement", rule="lift.placement")
def _lift_placement(ctx: Context) -> Iterable[Finding]:
    """A lift may not end on a connection that already has something on it.

    ``lift.placement``, ``extracted`` and ``refuse``:
    ``AFGConveyorLiftHologram::CheckValidPlacement`` tests each of the two
    ``mSnappedConnectionComponents`` -- ``cmp byte ptr [rax+268h], 0; je`` at
    ``0xa681fa``/``0xa68201`` and ``0xa68253``/``0xa6825a``, which is
    ``UFGFactoryConnectionComponent::mHasConnectedComponent`` -- and adds
    ``UFGCDInvalidPlacement`` when one is set, so the build gun turns the lift
    away.  This is that comparison: for each end of each lift, the connection it
    is wired to must carry that one link and nothing else.

    The guard the rule records is about the build gun's two clicks rather than
    about a finished build: the test runs only once ``mActivePointIdx > 0``, so
    the FIRST click may land on an occupied port and the second is what refuses.
    A placement has both ends down, so both are judged here.
    """
    counts: Counter[tuple[int, str]] = Counter()
    for link in ctx.placement.links:
        counts[link.a] += 1
        counts[link.b] += 1
    for lift in ctx.placement.lifts:
        for side, other in _lift_links(ctx, lift.id):
            taken = counts[other] - 1
            if taken <= 0:
                continue
            yield ctx.finding(
                "lift.placement",
                f"lift {lift.id}'s {side[1]} ends on object {other[0]}'s {other[1]}, which "
                f"already carries {taken} other connection: the build gun adds "
                "UFGCDInvalidPlacement for a snapped connection that is taken",
                lift.id,
                other[0],
                links=counts[other],
            )


def _lift_links(ctx: Context, lift_id: int) -> Iterable[tuple[tuple[int, str], tuple[int, str]]]:
    """``(this lift's side, the connection it snapped to)`` for each of its links."""
    for link in ctx.placement.links:
        for side, other in ((link.a, link.b), (link.b, link.a)):
            if side[0] == lift_id:
                yield (side, other)


# --- rates -----------------------------------------------------------------


@check("flow.capacity", needs_spec=True)
def _capacity(ctx: Context) -> Iterable[Finding]:
    """No belt carries more than its mark does, and no machine is starved.

    This project's own rule -- a belt that is over its rate does not refuse to
    build, it backs up -- and the speed is the FactorioLab dataset's, reached
    through the spec's ``belt_tiers``.  Deliberately NOT ``registry.json``'s
    ``belt_speed_per_min``: that field is the game's ``mSpeed``, which is twice
    the item rate, and reading it as items per minute would let every belt carry
    double.

    The second half: every item a machine's group consumes must arrive on a belt
    wired into one of its inputs, at THAT MACHINE's rate or better.  Which is not
    the group's per-machine rate for every machine in it: a group is
    ``count - 1`` machines at ``clock`` and one at ``last_clock``, because the
    odd machine at the end of a row absorbs the fractional remainder, and the
    last one eats ``last_clock / clock`` of what the others do.  The placement
    says which is which -- :attr:`MachineObj.clock` is that machine's own
    potential and ``spec.machines`` holds the two multisets equal -- so the
    expectation is ``inputs_per_machine * machine.clock / group.clock``, in exact
    ``Fraction`` arithmetic.  Comparing every machine against the full-clock rate
    reports a row FactorioLab costed correctly as starved.

    An item the spec funds from ``external_inputs`` that no belt in the
    placement carries is not this check's business, and it now SAYS so instead
    of passing over in silence: a placement like that is a FRAGMENT -- one row, a
    pair of machines -- where the boundary belt is simply not in the picture, and
    calling its machines starved would be a statement about the fragment.  A
    whole build has the belt, and ``flow.boundary`` is what refuses one whose
    ``-Y`` wall does not carry exactly the spec's external inputs, so nothing
    goes unjudged: what is skipped here is refused there.
    """
    spec = ctx.spec
    if spec is None:
        return
    speeds = _tier_speeds(spec, load_lab_map())
    yield from _fluid_capacity(ctx)
    for run in ctx.placement.belts:
        speed = speeds.get(run.class_name)
        if speed is None:
            yield ctx.finding(
                "flow.capacity",
                f"belt {run.id} is a {run.class_name}, which is not one of the marks the "
                f"spec funds ({', '.join(sorted(speeds)) or 'none'})",
                run.id,
            )
        elif run.items_per_second > speed:
            yield ctx.finding(
                "flow.capacity",
                f"belt {run.id} carries {float(run.items_per_second):.4g} items/s of "
                f"{run.item_id!r}, past the {float(speed):.4g} a {run.class_name} moves",
                run.id,
                carried=str(run.items_per_second),
                capacity=str(speed),
            )
    outside: list[str] = []
    carried_items = {run.item_id for run in ctx.placement.belts}
    incoming: dict[int, list[int]] = {}
    for link in ctx.placement.links:
        incoming.setdefault(link.b[0], []).append(link.a[0])
    for machine in ctx.placement.machines:
        group = ctx.group_for(machine)
        if group is None:
            continue
        for item, rate in group.inputs_per_machine.items():
            if item in spec.fluid_items:
                continue
            wanted = rate * machine.clock / group.clock
            supplied = _supplied(ctx, machine, item, incoming)
            if supplied == 0 and item in spec.external_inputs and item not in carried_items:
                outside.append(f"{machine.class_name} {machine.id} on {item!r}")
                continue
            if supplied < wanted:
                yield ctx.finding(
                    "flow.capacity",
                    f"{machine.class_name} {machine.id} needs {float(wanted):.4g} items/s of "
                    f"{item!r} and the belts on its inputs bring {float(supplied):.4g}",
                    machine.id,
                    item=item,
                    needed=str(wanted),
                    supplied=str(supplied),
                )
    if outside:
        yield ctx.skip(
            "flow.capacity",
            f"what reaches {', '.join(outside)} was not measured: the spec belts that item "
            "in from outside the blueprint and no belt in this placement carries it, which "
            "makes this a fragment rather than a build -- a whole build's -Y wall is held to "
            "the spec's external inputs by flow.boundary",
        )


def _tier_speeds(spec: SfyBuildSpec, labmap: LabMap) -> dict[str, Fraction]:
    """``Build_ConveyorBelt*_C`` -> items per second, from the lab dataset."""
    speeds: dict[str, Fraction] = {}
    for tier in spec.belt_tiers:
        class_name = labmap.machines.get(tier.item_id)
        if class_name is not None:
            speeds[class_name] = tier.items_per_second
    return speeds


def _supplied(
    ctx: Context, machine: MachineObj, item: str, incoming: Mapping[int, list[int]]
) -> Fraction:
    """Rate on the nearest feeder belts, through any snapped one-to-one lifts."""
    total = Fraction(0)
    pending = list(incoming.get(machine.id, ()))
    visited: set[int] = set()
    while pending:
        object_id = pending.pop()
        if object_id in visited:
            continue
        visited.add(object_id)
        source = ctx.placed.get(object_id)
        if isinstance(source, BeltRun):
            if source.item_id == item:
                total += source.items_per_second
        elif isinstance(source, LiftObj):
            pending.extend(incoming.get(object_id, ()))
    return total


@check("flow.balance", needs_spec=True)
def _balance(ctx: Context) -> Iterable[Finding]:
    """Every item the spec consumes is funded by a row or by the boundary.

    This project's own rule: it is arithmetic on the spec, not a bound the game
    holds anything to.  Per item, what the rows produce plus what is belted in
    must cover what the rows consume plus what leaves.  Exact ``Fraction``
    arithmetic throughout -- rounding is precisely what this exists to catch.
    """
    spec = ctx.spec
    if spec is None:
        return
    produced: Counter[str] = Counter()
    consumed: Counter[str] = Counter()
    for group in spec.groups:
        for item, rate in group.row_outputs.items():
            produced[item] += rate  # type: ignore[assignment]
        for item, rate in group.row_inputs.items():
            consumed[item] += rate  # type: ignore[assignment]
    for item in sorted({*produced, *consumed, *spec.external_inputs, *spec.outputs}):
        supply = Fraction(produced[item]) + spec.external_inputs.get(item, Fraction(0))
        demand = (
            Fraction(consumed[item])
            + spec.outputs.get(item, Fraction(0))
            + spec.surplus_outputs.get(item, Fraction(0))
        )
        if supply < demand:
            yield ctx.finding(
                "flow.balance",
                f"{item!r}: {float(supply):.6g} items/s produced and belted in against "
                f"{float(demand):.6g} consumed and sent out",
                item=item,
                supply=str(supply),
                demand=str(demand),
            )


def _fluid_network_capacity(ctx: Context) -> Iterable[Finding]:
    """Prove aggregate fluid demand/export across rated pipe and pump bottlenecks."""
    spec = ctx.spec
    if spec is None:
        return
    graph = _fluid_graph(ctx, forward=True)
    for item in sorted(spec.fluid_items):
        sources: list[tuple[tuple[int, str], Fraction]] = []
        sinks: list[tuple[tuple[int, str], Fraction]] = []
        for machine in ctx.placement.machines:
            group = ctx.group_for(machine)
            if group is None:
                continue
            ports = ctx.registry.buildables[machine.class_name].ports
            for incoming, rates, target in (
                (True, group.inputs_per_machine, sinks),
                (False, group.outputs_per_machine, sources),
            ):
                if item not in rates:
                    continue
                # Recipe port assignment is already verified by adjacent labelled runs.
                for port in ports:
                    if port.kind != "pipe" or port.direction != ("input" if incoming else "output"):
                        continue
                    node = (machine.id, port.name)
                    if any(
                        isinstance(pipe := ctx.placed.get(ref[0]), PipeRun) and pipe.item_id == item
                        for ref in graph.get(node, ())
                    ):
                        target.append((node, rates[item] * machine.clock / group.clock))
        for lane in ctx.placement.stack_lanes:
            if lane.kind == "pipe" and lane.item_id == item:
                if lane.input_per_second:
                    sources.append((lane.bottom, lane.input_per_second))
                if lane.output_per_second:
                    sinks.append((lane.top, lane.output_per_second))
        demand = sum((rate for _, rate in sinks), Fraction())
        if demand == 0:
            continue
        residual: dict[tuple[int, str], dict[tuple[int, str], Fraction]] = {}

        def add(
            a: tuple[int, str],
            b: tuple[int, str],
            capacity: Fraction,
            residual: dict[tuple[int, str], dict[tuple[int, str], Fraction]] = residual,
        ) -> None:
            row = residual.setdefault(a, {})
            row[b] = row.get(b, Fraction()) + capacity
            residual.setdefault(b, {}).setdefault(a, Fraction())

        for a, neighbours in graph.items():
            obj = ctx.placed[a[0]]
            if isinstance(obj, PipeRun) and obj.item_id != item:
                continue
            for b in neighbours:
                other = ctx.placed[b[0]]
                if isinstance(other, PipeRun) and other.item_id != item:
                    continue
                capacity = demand
                if a[0] == b[0]:
                    buildable = ctx.registry.buildables[obj.class_name]
                    if isinstance(obj, PipeRun):
                        capacity = obj.cubic_metres_per_second
                    if buildable.pipe_flow_limit_m3s is not None:
                        capacity = min(capacity, Fraction(str(buildable.pipe_flow_limit_m3s)))
                add(a, b, max(Fraction(), capacity))
        source, sink = (-1, "supply"), (-1, "demand")
        for node, rate in sources:
            add(source, node, rate)
        for node, rate in sinks:
            add(node, sink, rate)
        delivered = Fraction()
        while delivered < demand:
            previous: dict[tuple[int, str], tuple[int, str]] = {}
            pending = [source]
            for node in pending:
                for neighbour, capacity in residual.get(node, {}).items():
                    if capacity <= 0 or neighbour == source or neighbour in previous:
                        continue
                    previous[neighbour] = node
                    pending.append(neighbour)
                if sink in previous:
                    break
            if sink not in previous:
                break
            amount, node = demand - delivered, sink
            while node != source:
                parent = previous[node]
                amount = min(amount, residual[parent][node])
                node = parent
            node = sink
            while node != source:
                parent = previous[node]
                residual[parent][node] -= amount
                residual[node][parent] += amount
                node = parent
            delivered += amount
        if delivered < demand:
            yield ctx.finding(
                "flow.capacity",
                f"{item!r} connected fluid network can deliver/drain "
                f"only {delivered} of {demand} m3/s across its rated bottlenecks",
                item=item,
                needed=str(demand),
                supplied=str(delivered),
            )


def _transport_graph(ctx: Context, kind: str) -> dict[tuple[int, str], set[tuple[int, str]]]:
    if kind == "pipe":
        return _fluid_graph(ctx, forward=True)
    graph: dict[tuple[int, str], set[tuple[int, str]]] = {}
    for link in ctx.placement.links:
        a, b = ctx.port(*link.a), ctx.port(*link.b)
        if a and b and a.kind == b.kind == "belt":
            graph.setdefault(link.a, set()).add(link.b)
    for run in chain[BeltRun | LiftObj](ctx.placement.belts, ctx.placement.lifts):
        entry, exit_ = belt_ends(ctx.registry, run.class_name)
        graph.setdefault((run.id, entry), set()).add((run.id, exit_))
    for attachment in ctx.placement.attachments:
        ports = ctx.registry.buildables[attachment.class_name].ports
        for inlet in ports:
            if inlet.kind == "belt" and inlet.direction == "input":
                graph.setdefault((attachment.id, inlet.name), set()).update(
                    (attachment.id, p.name)
                    for p in ports
                    if p.kind == "belt" and p.direction == "output"
                )
    return graph


def _lane_capacity(
    ctx: Context,
    graph: Mapping[tuple[int, str], set[tuple[int, str]]],
    start: tuple[int, str],
    end: tuple[int, str],
    item: str,
    claimed: Fraction,
) -> Fraction:
    """Widest single continuation, constrained by every physical transport actor."""
    best = {start: claimed}
    pending = [start]
    while pending:
        node = pending.pop()
        obj = ctx.placed.get(node[0])
        available = best[node]
        if isinstance(obj, PipeRun):
            available = (
                min(available, obj.cubic_metres_per_second) if obj.item_id == item else Fraction()
            )
        elif isinstance(obj, BeltRun):
            available = min(available, obj.items_per_second) if obj.item_id == item else Fraction()
        if obj is not None:
            buildable = ctx.registry.buildables[obj.class_name]
            if isinstance(obj, BeltRun | LiftObj):
                # Docs mSpeed is twice items/minute, not an items/minute capacity.
                if buildable.belt_speed_per_min is None:
                    available = Fraction()
                else:
                    available = min(available, Fraction(str(buildable.belt_speed_per_min)) / 120)
            elif buildable.pipe_flow_limit_m3s is not None:
                available = min(available, Fraction(str(buildable.pipe_flow_limit_m3s)))
        for other in graph.get(node, ()):
            if available > best.get(other, Fraction()):
                best[other] = available
                pending.append(other)
    return best.get(end, Fraction())


def _stack_boundary(ctx: Context) -> Iterable[Finding]:
    """Project structural repeat contract, including user-approved manual seams."""
    placement = ctx.placement
    gap, pitch = placement.stack_connection_gap_cm, placement.stack_height_cm
    if not math.isfinite(gap) or gap < 0 or not math.isfinite(pitch) or pitch <= gap:
        yield ctx.finding(
            "flow.boundary", "stack pitch and common manual connection gap are invalid"
        )
        return
    if not placement.stack_lanes:
        yield ctx.finding("flow.boundary", "structural placement declares no material stack lanes")
        return
    if not placement.beams or not placement.foundations:
        yield ctx.finding(
            "flow.boundary", "structural stack lanes need actual beams and foundation slabs"
        )
    for box in ctx.boxes:
        if max(corner[2] for corner in box.corners()) > pitch + PORT_CM:
            yield ctx.finding(
                "flow.boundary",
                f"{box.label} extends above the structural repeat pitch",
                box.owner,
                pitch_cm=pitch,
            )
    declared: Counter[tuple[int, str]] = Counter()
    totals_in: dict[str, Fraction] = {}
    totals_out: dict[str, Fraction] = {}
    graphs = {kind: _transport_graph(ctx, kind) for kind in ("belt", "pipe")}
    spec = ctx.spec
    for lane in placement.stack_lanes:
        declared.update((lane.bottom, lane.top))
        bottom, top = ctx.world_port(*lane.bottom), ctx.world_port(*lane.top)
        ports = (ctx.port(*lane.bottom), ctx.port(*lane.top))
        if bottom is None or top is None or any(p is None or p.kind != lane.kind for p in ports):
            yield ctx.finding(
                "flow.boundary",
                f"{lane.item_id!r} lane has missing or wrong-medium ends",
                lane.bottom[0],
                lane.top[0],
            )
            continue
        if (
            math.dist(bottom[:2], top[:2]) > PORT_CM
            or abs(bottom[2] + pitch - top[2] - gap) > PORT_CM
        ):
            yield ctx.finding(
                "flow.boundary",
                f"{lane.item_id!r} lane cannot repeat vertically: "
                "next bottom must equal current top plus the common gap at identical XY",
                lane.bottom[0],
                lane.top[0],
                bottom=bottom,
                top=top,
                gap_cm=gap,
                pitch_cm=pitch,
            )
        if top[2] <= bottom[2]:
            yield ctx.finding(
                "flow.boundary",
                f"{lane.item_id!r} lane top is not above its bottom",
                lane.bottom[0],
                lane.top[0],
            )
        for node, expected in ((lane.bottom, (0.0, 0.0, -1.0)), (lane.top, (0.0, 0.0, 1.0))):
            normal = ctx.port_facing(*node)
            if normal is None or _dot(normal, expected) < math.cos(PORT_ANGLE_RAD):
                yield ctx.finding(
                    "flow.boundary",
                    f"{lane.item_id!r} boundary end must face vertically outward",
                    node[0],
                    port=node[1],
                )
            obj = ctx.placed[node[0]]
            if not isinstance(obj, LiftObj | PipeRun):
                yield ctx.finding(
                    "flow.boundary",
                    "vertical stack boundary is not a lift or pipe endpoint",
                    node[0],
                )
                continue
            ends = (pipe_ends if isinstance(obj, PipeRun) else belt_ends)(
                ctx.registry, obj.class_name
            )
            hole_id = obj.snapped_passthroughs[ends.index(node[1])] if node[1] in ends else None
            if hole_id is None or not _owns_hole_crossing(ctx, obj.id, hole_id):
                yield ctx.finding(
                    "flow.boundary",
                    f"{lane.item_id!r} boundary lacks its actual "
                    "reciprocal, slab-aligned passthrough",
                    node[0],
                )
        if lane.kind == "belt" and gap:
            limits = ctx.registry.limits
            if (
                limits.lift_min_cm is None
                or limits.lift_max_cm is None
                or not limits.lift_min_cm <= gap <= limits.lift_max_cm
                or limits.lift_step_cm is None
                or abs(gap / limits.lift_step_cm - round(gap / limits.lift_step_cm)) > TOUCH_CM
            ):
                yield ctx.finding(
                    "flow.boundary",
                    "manual belt seam gap cannot be joined by a legal lift",
                    lane.bottom[0],
                    gap_cm=gap,
                )
        if lane.kind == "pipe" and gap:
            obj = ctx.placed[lane.bottom[0]]
            mesh = ctx.registry.buildables[obj.class_name].mesh_length_cm
            if mesh is None or not mesh / 2 < gap <= ctx.registry.limits.pipe_max_spline_cm:
                yield ctx.finding(
                    "flow.boundary",
                    "manual pipe seam gap is outside native length bounds",
                    lane.bottom[0],
                    gap_cm=gap,
                )
        if lane.input_per_second < 0 or lane.output_per_second < 0 or lane.capacity_per_second <= 0:
            yield ctx.finding("flow.boundary", f"{lane.item_id!r} lane has invalid rates")
        if max(lane.input_per_second, lane.output_per_second) > lane.capacity_per_second:
            yield ctx.finding(
                "flow.boundary", f"{lane.item_id!r} local demand/export exceeds lane capacity"
            )
        graph = graphs[lane.kind]
        reachable = _component(lane.bottom, graph)
        if lane.top not in reachable:
            yield ctx.finding(
                "flow.boundary",
                f"{lane.item_id!r} lane lacks a complete bottom-to-top path",
                lane.bottom[0],
                lane.top[0],
            )
        capacity = _lane_capacity(
            ctx, graph, lane.bottom, lane.top, lane.item_id, lane.capacity_per_second
        )
        if capacity < lane.capacity_per_second:
            yield ctx.finding(
                "flow.boundary",
                f"{lane.item_id!r} continuation carries only {capacity}, "
                f"below its declared {lane.capacity_per_second} material units/s capacity",
                lane.bottom[0],
                lane.top[0],
                capacity=str(capacity),
            )
        for incoming, local_rate in (
            (True, lane.input_per_second),
            (False, lane.output_per_second),
        ):
            if local_rate <= 0 or spec is None:
                continue
            served = False
            for machine in placement.machines:
                group = ctx.group_for(machine)
                if group is None:
                    continue
                rates = group.inputs_per_machine if incoming else group.outputs_per_machine
                if lane.item_id not in rates:
                    continue
                for port in ctx.registry.buildables[machine.class_name].ports:
                    if port.kind != lane.kind or port.direction != (
                        "input" if incoming else "output"
                    ):
                        continue
                    node = (machine.id, port.name)
                    if node in reachable if incoming else lane.top in _component(node, graph):
                        served = True
            if not served:
                yield ctx.finding(
                    "flow.boundary",
                    f"{lane.item_id!r} lane has no internal "
                    f"{'feed branch' if incoming else 'output collector'} to a recipe machine",
                )
        totals_in[lane.item_id] = totals_in.get(lane.item_id, Fraction()) + lane.input_per_second
        totals_out[lane.item_id] = totals_out.get(lane.item_id, Fraction()) + lane.output_per_second
    flagged: set[tuple[int, str]] = set()
    for transport in chain[BeltRun | LiftObj | PipeRun](
        placement.belts, placement.lifts, placement.pipes
    ):
        ends = (pipe_ends if isinstance(transport, PipeRun) else belt_ends)(
            ctx.registry, transport.class_name
        )
        for name, boundary in zip(
            ends, (transport.boundary_start, transport.boundary_end), strict=True
        ):
            if boundary:
                flagged.add((transport.id, name))
    if set(declared) != flagged or any(count != 1 for count in declared.values()):
        yield ctx.finding(
            "flow.boundary",
            "intentional open transport ends must equal the unique declared stack lane ends",
            undeclared=sorted(flagged - set(declared)),
            not_open=sorted(set(declared) - flagged),
        )
    if spec is None:
        yield ctx.skip("flow.boundary", "structural lane rates cannot be compared without a spec")
        return
    expected_out = dict(spec.outputs)
    for item, rate in spec.surplus_outputs.items():
        expected_out[item] = expected_out.get(item, Fraction()) + rate
    for got, wanted, direction in (
        (totals_in, spec.external_inputs, "input"),
        (totals_out, expected_out, "output"),
    ):
        got = {item: rate for item, rate in got.items() if rate}
        if got != wanted:
            yield ctx.finding(
                "flow.boundary",
                f"stack lane local {direction} rates differ from the spec",
                placed={k: str(v) for k, v in got.items()},
                expected={k: str(v) for k, v in wanted.items()},
            )


def _hole_errors(ctx: Context, hole: PassthroughObj) -> list[str]:
    errors: list[str] = []
    if not math.isfinite(hole.thickness_cm) or hole.thickness_cm <= 0:
        errors.append("thickness is not finite and positive")
    up = quat_rotate(hole.pose.transform().rotation, (0.0, 0.0, 1.0))
    owners = [
        box
        for box in ctx.boxes
        if box.owner in {f.id for f in ctx.placement.foundations} and box.holds(hole.pose.location)
    ]
    if not any(
        abs(_dot(up, box.axes[2])) >= math.cos(PORT_ANGLE_RAD)
        and abs(hole.thickness_cm - 2 * box.half[2]) <= PORT_CM
        and abs(
            _dot(
                (
                    hole.pose.x - box.centre[0],
                    hole.pose.y - box.centre[1],
                    hole.pose.z - box.centre[2],
                ),
                up,
            )
        )
        <= PORT_CM
        for box in owners
    ):
        errors.append("hole plane/thickness does not match a containing foundation slab")
    refs = (hole.top_connection, hole.bottom_connection)
    if all(ref is None for ref in refs):
        errors.append("hole owns no transport endpoint")
    if refs[0] is not None and refs[0] == refs[1]:
        errors.append("top and bottom both claim the same endpoint")
    for ref, sign in zip(refs, (-1.0, 1.0), strict=True):
        if ref is None:
            continue
        obj = ctx.placed.get(ref[0])
        if not isinstance(obj, PipeRun | LiftObj):
            errors.append(f"{ref!r} is not a pipe or lift end")
            continue
        if isinstance(obj, PipeRun) != ("Pipe" in hole.class_name):
            errors.append(f"{ref!r} has the wrong transport medium")
        ends = (pipe_ends if isinstance(obj, PipeRun) else belt_ends)(ctx.registry, obj.class_name)
        if ref[1] not in ends:
            errors.append(f"{ref!r} is not an actual transport endpoint")
            continue
        index = ends.index(ref[1])
        if obj.snapped_passthroughs[index] != hole.id:
            errors.append(f"{ref!r} does not reciprocally name this hole")
        raw = (
            (obj.start if index == 0 else obj.end)
            if isinstance(obj, PipeRun)
            else ctx.lift_end(obj, ref[1])[0]
        )
        if math.dist(raw, hole.pose.location) > PORT_CM:
            errors.append(f"{ref!r} spline/lift endpoint is displaced from the hole actor origin")
        normal = ctx.port_facing(*ref)
        if normal is None or _dot(normal, (sign * up[0], sign * up[1], sign * up[2])) < math.cos(
            PORT_ANGLE_RAD
        ):
            errors.append(f"{ref!r} does not face into its geometric side of the hole")
    return errors


def _owns_hole_crossing(ctx: Context, transport: int, owner: int) -> bool:
    """Only an actual, reciprocal, geometrically valid hole can excuse crossing."""
    for hole in ctx.placement.passthroughs:
        refs = (hole.top_connection, hole.bottom_connection)
        if not any(ref is not None and ref[0] == transport for ref in refs) or _hole_errors(
            ctx, hole
        ):
            continue
        if owner == hole.id or any(
            box.owner == owner and box.holds(hole.pose.location) for box in ctx.boxes
        ):
            return True
    return False


@check("ports.passthrough")
def _passthroughs(ctx: Context) -> Iterable[Finding]:
    """Project reciprocal ownership, medium, centre, normal and slab-thickness contract."""
    claims: Counter[tuple[int, str]] = Counter()
    for hole in ctx.placement.passthroughs:
        for ref in (hole.top_connection, hole.bottom_connection):
            if ref is not None:
                claims[ref] += 1
        for problem in _hole_errors(ctx, hole):
            yield ctx.finding("ports.passthrough", f"hole {hole.id}: {problem}", hole.id)
    for obj in chain[LiftObj | PipeRun](ctx.placement.lifts, ctx.placement.pipes):
        ends = (pipe_ends if isinstance(obj, PipeRun) else belt_ends)(ctx.registry, obj.class_name)
        for name, hole_id in zip(ends, obj.snapped_passthroughs, strict=True):
            if hole_id is None:
                continue
            named_hole = ctx.placed.get(hole_id)
            node = (obj.id, name)
            if not isinstance(named_hole, PassthroughObj) or node not in (
                named_hole.top_connection,
                named_hole.bottom_connection,
            ):
                yield ctx.finding(
                    "ports.passthrough",
                    f"transport {obj.id}'s {name} names a hole "
                    "that does not own that exact endpoint",
                    obj.id,
                    hole_id,
                )
            if claims[node] != 1:
                yield ctx.finding(
                    "ports.passthrough",
                    f"endpoint {node!r} must belong to exactly one hole side",
                    obj.id,
                )


@check("geom.dynamic")
def _dynamic_geometry(ctx: Context) -> Iterable[Finding]:
    """Project dynamic dimensions; native beam mMaxLength is a construction bound."""
    for beam in ctx.placement.beams:
        maximum = ctx.registry.buildables[beam.class_name].beam_max_length_cm
        if (
            maximum is None
            or not math.isfinite(beam.length_cm)
            or not 0 < beam.length_cm <= maximum
        ):
            yield ctx.finding(
                "geom.dynamic",
                f"beam {beam.id} length is outside its sourced construction bounds",
                beam.id,
                length_cm=beam.length_cm,
                max_cm=maximum,
            )
    if ctx.placement.passthroughs:
        yield ctx.skip(
            "geom.dynamic",
            "Passthrough middle-mesh width and dynamic slab thickness "
            "are bounded; native constructed cap mesh extents are not yet extracted.",
        )


def required_input_head_m(
    placement: SfyPlacement,
    bottom: tuple[int, str],
    registry: Registry,
) -> float:
    """Required external priming head for a completed routed fluid input.

    This is metres above ``bottom``, derived from reachable connection centres
    before the next pump inlet. Interior spline humps do not add head; splitting
    a run at a crest does, because that creates a pipe endpoint there. This is
    an external supply obligation, not measured world pressure. Missing source
    geometry returns infinity, which cannot certify a lane.
    """
    return _required_head(Context(placement, None, registry, {}), bottom)


def _required_head(ctx: Context, node: tuple[int, str]) -> float:
    start = ctx.world_port(*node)
    if start is None:
        return math.inf
    region = _component(node, ctx.hydraulic_graph, stop=ctx.pump_inlets)
    peak = start[2]
    for ref in region:
        # Junctions connect all native mouths internally; an unused mouth is
        # not a pipe endpoint and must not invent a climb above the route.
        if isinstance(ctx.placed.get(ref[0]), PipeAttachmentObj) and not any(
            neighbour[0] != ref[0] for neighbour in ctx.hydraulic_graph.get(ref, ())
        ):
            continue
        where = ctx.world_port(*ref)
        if where is not None:
            peak = max(peak, where[2])
    return max(0.0, (peak - start[2]) / 100)


@check("pipe.head")
def _pipe_head(ctx: Context) -> Iterable[Finding]:
    """Project hydraulic design envelope; not a simulation of runtime pressure.

    Pumps reset head, never add their ratings together. Each output-connected
    region ends at the next pump's inlet. External head is a stated player
    supply obligation, not assumed infinite pressure or a measured world fact.
    """
    if not ctx.placement.pipes:
        return
    graph = ctx.hydraulic_graph
    pumps = {
        obj.id: obj
        for obj in ctx.placement.pipe_attachments
        if ctx.registry.buildables[obj.class_name].native_class == "FGBuildablePipelinePump"
    }
    for oid, pump in pumps.items():
        buildable = ctx.registry.buildables[pump.class_name]
        outputs = [p for p in buildable.ports if p.kind == "pipe" and p.direction == "output"]
        for port in outputs:
            required = _required_head(ctx, (oid, port.name))
            head = buildable.pump_design_head_m
            if head is None or required > head + PORT_CM / 100:
                yield ctx.finding(
                    "pipe.head",
                    f"pump {oid}'s forward-connected climb needs "
                    f"{required:.2f} m of head; sourced design head is {head}",
                    oid,
                    required_head_m=required,
                    design_head_m=head,
                )
            capacity = buildable.pipe_flow_limit_m3s
            immediate = [
                pipe
                for ref in graph.get((oid, port.name), ())
                if isinstance(pipe := ctx.placed.get(ref[0]), PipeRun)
            ]
            carried = max((pipe.cubic_metres_per_second for pipe in immediate), default=Fraction())
            if capacity is None or carried > Fraction(str(capacity)):
                yield ctx.finding(
                    "flow.capacity",
                    f"pump {oid} cannot carry its connected "
                    f"{carried} m3/s at sourced limit {capacity}",
                    oid,
                )
    for machine in ctx.placement.machines:
        for port in ctx.registry.buildables[machine.class_name].ports:
            if port.kind != "pipe" or port.direction != "output":
                continue
            node = (machine.id, port.name)
            if node not in graph:
                continue
            required = _required_head(ctx, node)
            if required > PORT_CM / 100:
                yield ctx.skip(
                    "pipe.head",
                    f"machine {machine.id}'s output-to-first-pump/consumer "
                    f"route needs {required:.2f} m initial head. This class's inherent "
                    "output head has not been sourced, so priming this internal "
                    "byproduct route is not certified.",
                )
    for lane in ctx.placement.stack_lanes:
        if lane.kind != "pipe" or lane.input_per_second <= 0:
            continue
        required = _required_head(ctx, lane.bottom)
        declared = lane.required_input_head_m
        if not math.isfinite(declared) or declared < 0 or declared + PORT_CM / 100 < required:
            yield ctx.finding(
                "pipe.head",
                f"{lane.item_id!r} external input requires at least "
                f"{required:.2f} m priming head, not the declared {declared:.2f} m",
                lane.bottom[0],
                required_head_m=required,
                declared_head_m=declared,
            )
        yield Finding(
            "pipe.head",
            Severity.INFO,
            f"External supply obligation: {lane.item_id!r} "
            f"must arrive with at least {declared:.2f} m head above its boundary port; "
            "the blueprint cannot measure or guarantee world supply.",
            (lane.bottom[0],),
            {"required_input_head_m": declared},
        )


@check("flow.boundary")
def _boundary(ctx: Context) -> Iterable[Finding]:
    """A belt end left open on purpose stands on the designer wall it claims.

    This project's own rule, and the counterpart of the exemption
    ``ports.connected_once`` grants: a build takes its external inputs in at the
    ``-Y`` wall and sends its outputs out at the ``+Y`` wall, and the belt that
    carries one stops there with nothing wired to it, because what it meets is
    outside the blueprint.  Nothing in the game refuses a belt for stopping in
    mid-air -- a player builds one every time -- so the bound is ours: an open
    end that is NOT on the wall it claims is a belt that silently goes nowhere,
    and a build whose boundary belts are not the spec's external inputs and
    outputs is not the build that was costed.

    The wall is the designer's own ``half_cm`` and the slack is :data:`PORT_CM`,
    the same centimetre ``ports.position`` allows a belt at its port.

    A placement with no flagged end at all is a FRAGMENT -- one row, a pair of
    machines -- rather than a build with its boundary missing, and this check
    stands aside on it and says so, the way ``power.wires`` does on a placement
    with no wires.  Without a spec the item halves cannot be compared either,
    and the check says that too rather than passing in silence.
    """
    if ctx.placement.stack_lanes or ctx.placement.stack_height_cm:
        yield from _stack_boundary(ctx)
        return
    if ctx.placement.pipes:
        yield ctx.finding(
            "flow.boundary",
            "fluid production needs explicit structural stack lanes; horizontal "
            "legacy fragment boundaries do not establish fluid entry or drainage",
        )
    flagged = [run for run in ctx.placement.belts if run.boundary_start or run.boundary_end]
    if not flagged:
        yield ctx.skip("flow.boundary", _NO_BOUNDARY)
        return
    half = ctx.placement.designer.half_cm
    entries: Counter[str] = Counter()
    exits: Counter[str] = Counter()
    for run in flagged:
        for where, wall, carried in (
            (run.start, -half, run.boundary_start),
            (run.end, half, run.boundary_end),
        ):
            if not carried:
                continue
            (entries if wall < 0 else exits)[run.item_id] += 1
            if abs(where[1] - wall) > PORT_CM:
                yield ctx.finding(
                    "flow.boundary",
                    f"belt {run.id} is a boundary belt for {run.item_id!r} and its open end "
                    f"is at y = {where[1]:.1f}, {abs(where[1] - wall):.1f} cm off the "
                    f"y = {wall:.0f} wall",
                    run.id,
                    y_cm=round(where[1], 3),
                    wall_cm=wall,
                )
    spec = ctx.spec
    if spec is None:
        yield ctx.skip(
            "flow.boundary",
            "the items on the boundary belts are the spec's external inputs and outputs, "
            "and no spec was given, so only where the open ends stand was judged",
        )
        return
    for got, wanted, what in (
        (entries, set(spec.external_inputs), "enters at the -Y wall"),
        (exits, set(spec.outputs) | set(spec.surplus_outputs), "leaves at the +Y wall"),
    ):
        if set(got) != wanted:
            yield ctx.finding(
                "flow.boundary",
                f"the build {what} on {sorted(got)} and the spec says {sorted(wanted)}",
                placed=sorted(got),
                spec=sorted(wanted),
            )
        repeated = {item: count for item, count in got.items() if count > 1}
        if repeated:
            yield ctx.finding(
                "flow.boundary",
                f"each item needs one boundary belt, but the build {what} "
                f"through multiple belts for {sorted(repeated)}",
                crossings=repeated,
            )


@check("spec.machines", needs_spec=True)
def _machines(ctx: Context) -> Iterable[Finding]:
    """The build placed is the build the spec asked for.

    This project's own rule, and the one that makes the rest mean something: a
    placement that validates beautifully and is missing a row is not the build
    that was costed.  A group of ``count`` machines is ``count - 1`` at
    ``clock`` and one at ``last_clock`` -- the odd machine at the end of a row
    absorbs the fractional remainder -- so the clocks are compared as a multiset
    rather than in order.
    """
    spec = ctx.spec
    if spec is None:
        return
    wanted: Counter[tuple[str, str, Fraction]] = Counter()
    for group in spec.groups:
        wanted[(group.machine_class, group.recipe_class, group.clock)] += group.count - 1
        wanted[(group.machine_class, group.recipe_class, group.last_clock)] += 1
    wanted += Counter()  # drop the zero counts a one-machine group leaves
    built: Counter[tuple[str, str, Fraction]] = Counter(
        (m.class_name, m.recipe_class, m.clock) for m in ctx.placement.machines
    )
    for key in sorted({*wanted, *built}, key=str):
        if wanted[key] != built[key]:
            machine_class, recipe, clock = key
            yield ctx.finding(
                "spec.machines",
                f"the spec asks for {wanted[key]} x {machine_class} running {recipe} at "
                f"{float(clock) * 100:g}% and the placement has {built[key]}",
                wanted=wanted[key],
                built=built[key],
                machine=machine_class,
                recipe=recipe,
            )


# --- power and the round trip ----------------------------------------------


@check("power.wires")
def _wires(ctx: Context) -> Iterable[Finding]:
    """Every wire is short enough, every connection is inside its limit, and every
    machine is on a pole.

    The NUMBERS are the game's and the refusal is this project's own, in all
    three parts.  ``registry.json`` carries ``wire_max_cm`` per power-line class --
    ``AFGBuildableWire::mMaxLength`` out of Docs.json, which
    ``limits_sources["wire_max_cm"]`` says is where it came from -- and
    ``max_connections`` per port out of the cooked assets
    (``mMaxNumConnectionLinks``).  **No extracted hologram rule governs either.**
    ``hologram_rules.json`` holds nothing that turns a wire away for its length
    or a connection away for its link count, so what happens above those numbers
    in the game is unread and the refusal here is ours rather than the
    hologram's.  That a machine must reach a pole is ours outright as well: the
    game is happy to build an unpowered machine and we decline to author one.

    A wire whose end names no connection the registry gives that object, or whose
    class carries no ``wire_max_cm``, is REPORTED rather than passed over: a wire
    whose length cannot be measured is a wire nobody has checked, and a check
    that says nothing about it is a check claiming coverage it has not got.
    """
    if not ctx.placement.wires:
        for obj in ctx.placement.pipe_attachments:
            buildable = ctx.registry.buildables.get(obj.class_name)
            if buildable and any(p.kind == "power" for p in buildable.ports):
                yield ctx.finding(
                    "power.wires", f"pump {obj.id} has no powered pole connection", obj.id
                )
        yield ctx.skip("power.wires", _NO_WIRES)
        return
    counts: Counter[tuple[int, str]] = Counter()
    joined: dict[int, set[int]] = {}
    for wire in ctx.placement.wires:
        counts[wire.link.a] += 1
        counts[wire.link.b] += 1
        joined.setdefault(wire.link.a[0], set()).add(wire.link.b[0])
        joined.setdefault(wire.link.b[0], set()).add(wire.link.a[0])
        head, tail = ctx.world_port(*wire.link.a), ctx.world_port(*wire.link.b)
        for side, where in ((wire.link.a, head), (wire.link.b, tail)):
            if where is None:
                yield ctx.finding(
                    "power.wires",
                    f"wire {wire.id} names object {side[0]}'s {side[1]}, which is not a "
                    "connection the registry gives it, so where this wire ends is unknown",
                    wire.id,
                    side[0],
                    end=side[1],
                )
        limit = ctx.registry.limits.wire_max_cm.get(wire.class_name)
        if limit is None:
            yield ctx.finding(
                "power.wires",
                f"wire {wire.id} is a {wire.class_name}, which the registry gives no "
                f"wire_max_cm, so nothing here knows how far one reaches",
                wire.id,
                class_name=wire.class_name,
            )
        if head is None or tail is None or limit is None:
            # Each of the three is reported above; without all three there is no
            # length to compare, and passing over it silently is what this check
            # used to do.
            continue
        length = math.dist(head, tail)
        if length > limit:
            yield ctx.finding(
                "power.wires",
                f"wire {wire.id} spans {length:.1f} cm, past the {limit:.0f} cm a "
                f"{wire.class_name} reaches",
                wire.id,
                length_cm=round(length, 3),
                limit_cm=limit,
            )
    for side, count in counts.items():
        port = ctx.port(*side)
        if port is None or port.max_connections is None:
            continue
        if count > port.max_connections:
            yield ctx.finding(
                "power.wires",
                f"object {side[0]}'s {side[1]} carries {count} wires, past the "
                f"{port.max_connections} it accepts",
                side[0],
                wires=count,
                max_connections=port.max_connections,
            )
    poles = {pole.id for pole in ctx.placement.poles}
    for machine in chain[MachineObj | PipeAttachmentObj](
        ctx.placement.machines, ctx.placement.pipe_attachments
    ):
        buildable = ctx.registry.buildables.get(machine.class_name)
        if buildable is None or not any(p.kind == "power" for p in buildable.ports):
            continue
        if not _reaches(machine.id, poles, joined):
            yield ctx.finding(
                "power.wires",
                f"{machine.class_name} {machine.id} is on no wire that reaches a pole",
                machine.id,
            )


def _reaches(start: int, wanted: set[int], joined: Mapping[int, set[int]]) -> bool:
    """Whether a walk of the wire graph from ``start`` arrives at one of ``wanted``."""
    seen = {start}
    queue = [start]
    while queue:
        here = queue.pop()
        if here in wanted:
            return True
        for there in joined.get(here, ()):
            if there not in seen:
                seen.add(there)
                queue.append(there)
    return False


@check("roundtrip")
def _roundtrip(ctx: Context) -> Iterable[Finding]:
    """``decode(emit(placement)) == placement``.

    This project's own rule and the only one about the FILE rather than the
    build: a placement that does not survive being written and read back is one
    the rest of the report is judging in place of the thing that will actually
    be pasted.

    ``emit`` needs a template library and a build version.  The caller supplies
    the library through ``validate(..., library=...)``; where it does not, both
    come from the repo's own corpus the way Task 5's tests take them -- the
    newest fixture header, and one actor of each class to clone.  That is the
    corpus used as a source of FORMAT, which is what ``TemplateLibrary`` is for;
    no bound and no tolerance anywhere in this module is read from a blueprint.
    A class the library has no template for is a gap in the library rather than
    a fault in the placement, so it goes to ``skipped`` with the reason.
    """
    fallback, header = _corpus()
    library = ctx.library if ctx.library is not None else fallback
    if library is None or header is None:
        yield ctx.skip(
            "roundtrip",
            f"no library was given and there is no blueprint corpus at {_FIXTURE_DIR}, "
            "so this placement cannot be written at all",
        )
        return
    try:
        written = emit(
            ctx.placement,
            ctx.registry,
            library,
            load_lab_map(),
            build_version=header.build_version,
            version_data=header.version_data,
        )
    except TemplateError as exc:
        yield ctx.skip("roundtrip", f"the corpus has no template to write this placement: {exc}")
        return
    except EmitError as exc:
        yield ctx.finding("roundtrip", f"this placement cannot be written: {exc}")
        return
    try:
        again = decode(written, ctx.registry)
    except EmitError as exc:
        yield ctx.finding("roundtrip", f"the blueprint this placement wrote cannot be read: {exc}")
        return
    if again != ctx.placement:
        yield ctx.finding(
            "roundtrip",
            "the placement that comes back out of the blueprint is not the one that "
            f"went in: {_first_difference(ctx.placement, again)}",
        )


@cache
def _corpus() -> tuple[TemplateLibrary | None, BlueprintHeader | None]:
    """The fixture library and newest header, built once: scanning costs seconds."""
    if not _FIXTURE_DIR.is_dir():
        return (None, None)
    paths = sorted(_FIXTURE_DIR.glob("*.sbp"))
    if not paths:
        return (None, None)
    headers = [read_header(Reader(path.read_bytes())) for path in paths]
    newest = max(headers, key=lambda h: (h.save_version, h.build_version))
    return (TemplateLibrary.from_fixtures(paths), newest)


def _first_difference(want: SfyPlacement, got: SfyPlacement) -> str:
    for name in (
        "designer",
        "machines",
        "attachments",
        "belts",
        "lifts",
        "poles",
        "wires",
        "foundations",
        "pipes",
        "pipe_attachments",
        "beams",
        "passthroughs",
    ):
        mine, theirs = getattr(want, name), getattr(got, name)
        if mine != theirs:
            return f"{name} differ ({len(mine)} in, {len(theirs)} out)"
    if want.links != got.links:
        return f"links differ ({want.links} in, {got.links} out)"
    return "the two placements differ in a field this summary does not name"


# --- running them ----------------------------------------------------------


def _round(point: Vector) -> tuple[float, float, float]:
    return (round(point[0], 2), round(point[1], 2), round(point[2], 2))


def validate(
    placement: SfyPlacement,
    spec: SfyBuildSpec | None,
    registry: Registry,
    *,
    rules: Mapping[str, HologramRule] | None = None,
    only: Iterable[str] | None = None,
    library: TemplateLibrary | None = None,
) -> Report:
    """Judge ``placement``, optionally against the ``spec`` it should realise.

    Without a ``spec`` the three checks in :data:`NEEDS_SPEC` cannot run and are
    listed in ``Report.skipped`` rather than passing in silence.  A check that
    names a ``partial`` rule, or that finds nothing of its kind to judge, puts
    itself there too and says why in an ``INFO`` finding -- so a report with an
    empty ``findings`` and a populated ``skipped`` is not a clean build, it is a
    build nobody finished looking at.

    ``rules`` is the rule table to judge by, ``data/hologram_rules.json``'s by
    default; it is what :func:`_may_refuse` reads to hold every check to the
    effect of the rule it names.  ``library`` is the templates ``roundtrip``
    writes with, the repo's own corpus by default.
    """
    wanted = set(only) if only is not None else None
    known = rules if rules is not None else load_rules()
    ctx = Context(placement, spec, registry, known, library)
    findings: list[Finding] = []
    ran: list[str] = []
    skipped: list[str] = []
    for cid, fn in CHECKS.items():
        if wanted is not None and cid not in wanted:
            continue
        if cid in NEEDS_SPEC and spec is None:
            skipped.append(cid)
            findings.append(
                Finding(
                    cid,
                    Severity.INFO,
                    "this check judges a placement against the spec it realises, and no "
                    "spec was given",
                )
            )
            continue
        produced = list(fn(ctx))
        if any(f.severity is Severity.ERROR for f in produced):
            _may_refuse(cid, known)
        findings.extend(produced)
        (skipped if cid in ctx.skipped else ran).append(cid)
    return Report(tuple(findings), tuple(ran), tuple(skipped))


def _may_refuse(cid: str, rules: Mapping[str, HologramRule]) -> None:
    """Refuse to REPORT a refusal a check has no authority for.

    The discipline in this module's docstring, enforced by this module rather
    than by a test: a check that turns a placement away either names a rule the
    instructions show turning one away -- ``effect`` ``refuse`` -- or declares
    the bound this project's own.  A ``compute``, ``clamp`` or ``snap`` rule
    describes something the game works out or moves, so a refusal citing one
    would be our judgement wearing the game's name.  Raising here rather than
    dropping the finding is deliberate: a validator quietly weakening itself is
    worse than one that stops.
    """
    rule = RULE_FOR[cid]
    if rule == PROJECT:
        return
    effect = rules[rule].effect
    if effect != "refuse":
        raise ValueError(
            f"check {cid!r} reported an error citing {rule!r}, whose effect is "
            f"{effect!r}: nothing in that rule's instructions turns a placement away. "
            f"Either name the rule that does, or register the check with rule={PROJECT!r} "
            "and say in its docstring why this project is stricter than the game."
        )
