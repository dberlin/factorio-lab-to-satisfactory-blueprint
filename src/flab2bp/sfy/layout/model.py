"""Where every object in a Satisfactory build stands, in centimetres.

A :class:`SfyPlacement` is the whole build as geometry: machines, conveyor
attachments, belt runs, conveyor lifts, poles, wires and foundations, each with
an id, the game class it is built from, and where it is. It is the hand-off between the layout
stages and :mod:`flab2bp.sfy.layout.emit`, which writes it into a blueprint, and
it is what the validator is given to judge.

The model states positions and nothing else. It does not know what is legal --
that is the hologram's business, read out of the game into
``data/hologram_rules.json`` and enforced by the validator -- and it does not
compute a shape: a belt's spline comes from
:mod:`flab2bp.sfy.layout.splines`, which builds the shapes the game's own router
builds.

**What the file can carry.** An actor's transform is written as ten 32-bit
floats (``objects.write_toc``), so :class:`Pose` states its position at that
width and no finer: a placement that claims a nanometre the file cannot hold is
claiming something the game will never read. What a belt carries and how fast
has no property in a blueprint at all -- a belt in a file holds the items that
happen to be sitting on it, not a contract -- so it is carried here because the
rest of the pipeline needs it, left out of what equality compares
(``compare=False``), and handed back at its default by
:func:`~flab2bp.sfy.layout.emit.decode` rather than guessed at. The same goes
for a machine's ``clock``, for a different reason: the file DOES carry the
potential, as one 32-bit float, and a clock is an exact
:class:`~fractions.Fraction`, so a clock the float cannot hold comes back as the
number beside it. Equality compares that number -- :attr:`MachineObj.stored_clock`
-- rather than nothing at all, so a machine emitted at the wrong potential still
fails the round trip. That is what makes ``decode(emit(placement)) == placement``
a statement about the file rather than a statement about this dataclass.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from flab2bp.sfy.docs import quaternion_to_rotator
from flab2bp.sfy.geometry import quat_rotate, snap_zeros
from flab2bp.sfy.layout.splines import SplinePoint, Vector, yaw_quaternion
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import LiftGeometry, Registry
from flab2bp.sfy.spec import Designer

__all__ = [
    "UNIT_SCALE",
    "AttachmentObj",
    "BeamObj",
    "BeltRun",
    "FoundationObj",
    "LiftObj",
    "Link",
    "MachineObj",
    "PassthroughObj",
    "PipeAttachmentObj",
    "PipeRun",
    "Placed",
    "PoleObj",
    "Pose",
    "SfyPlacement",
    "StackLane",
    "SplinePoint",
    "Vector",
    "WireObj",
    "belt_ends",
    "lift_geometry",
    "pipe_ends",
    "stored_float",
]

UNIT_SCALE: Vector = (1.0, 1.0, 1.0)
"""Every buildable this project places is placed at its own size."""


def stored_float(value: float) -> float:
    """``value`` at the width the object table stores a position in.

    ``objects.write_toc`` writes an actor's rotation, translation and scale with
    ``Writer.f32``, so a coordinate that is not a 32-bit float is not a
    coordinate the game will ever see. Rounding here rather than at write time
    means a placement says exactly where the build will stand, and that what
    comes back out of :func:`~flab2bp.sfy.layout.emit.decode` is the same number
    that went in.

    ``array("f")`` is the round trip -- no dependency and no packing format to
    get wrong -- which is how :mod:`flab2bp.sfy.templates` takes the game's own
    single-precision arithmetic.
    """
    return array("f", (value,))[0]


@dataclass(frozen=True, slots=True)
class Pose:
    """An Unreal position and full ``FRotator`` orientation, in centimetres/degrees.

    The original positional ``x, y, z, yaw_deg`` API is unchanged. Equality uses
    the quaternion actually stored by the object table, rather than comparing
    Euler angles (which are non-unique, especially at pitch +/-90 degrees).
    """

    x: float
    y: float
    z: float
    yaw_deg: float = field(compare=False)
    pitch_deg: float = field(default=0.0, compare=False)
    roll_deg: float = field(default=0.0, compare=False)
    _rotation: tuple[float, float, float, float] = field(init=False, compare=False, repr=False)
    _stored_rotation: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for name in ("x", "y", "z", "yaw_deg", "pitch_deg", "roll_deg"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ValueError(f"pose {name} must be finite")
            object.__setattr__(self, name, stored_float(value))
        pitch, yaw, roll = (
            math.radians(angle) / 2.0 for angle in (self.pitch_deg, self.yaw_deg, self.roll_deg)
        )
        sp, cp = math.sin(pitch), math.cos(pitch)
        sy, cy = math.sin(yaw), math.cos(yaw)
        sr, cr = math.sin(roll), math.cos(roll)
        rotation = (
            cr * sp * sy - sr * cp * cy,
            -cr * sp * cy - sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy,
        )
        self._set_rotation(rotation)

    def _set_rotation(self, rotation: tuple[float, float, float, float]) -> None:
        # q and -q describe the same physical rotation.
        sign = next((math.copysign(1.0, v) for v in reversed(rotation) if v), 1.0)
        canonical = tuple(sign * value for value in rotation)
        object.__setattr__(self, "_rotation", canonical)
        object.__setattr__(self, "_stored_rotation", tuple(map(stored_float, canonical)))

    @classmethod
    def from_transform(cls, transform: Transform) -> Pose:
        """Preserve the exact serialized quaternion, including gimbal-lock poses."""
        rotation = transform.rotation
        if not all(math.isfinite(value) for value in rotation) or not math.isclose(
            sum(value * value for value in rotation), 1.0, abs_tol=1e-5
        ):
            raise ValueError(f"actor rotation is not a unit quaternion: {rotation}")
        pitch, yaw, roll = quaternion_to_rotator(*rotation)
        angles = tuple(
            float(round(value)) if abs(value - round(value)) < 1e-4 else value
            for value in (yaw, pitch, roll)
        )
        pose = cls(*transform.translation, *angles)
        pose._set_rotation(rotation)
        return pose

    @property
    def location(self) -> Vector:
        return (self.x, self.y, self.z)

    def transform(self) -> Transform:
        """The actor transform the object table writes for this pose."""
        return Transform(self._rotation, self.location, UNIT_SCALE)


@dataclass(frozen=True, slots=True)
class MachineObj:
    """One production building running one recipe.

    ``clock`` is the requested potential as a fraction of 100 % and
    ``somersloops`` how many sit in the production-boost slots. Both are written
    into the blueprint now, into the ``SaveGame`` floats
    :func:`~flab2bp.sfy.layout.emit._set_potential` names.

    ``somersloops`` is a count, and the production boost that encodes it is
    exact at the width the file holds, so it is part of equality. ``clock``
    itself is not: it is an exact :class:`~fractions.Fraction` and the file
    holds one ``float``, so a clock that is not a 32-bit number -- ``moc=133``'s
    133/100 -- comes back as the stored number beside it. See the module
    docstring.

    What IS compared is :attr:`stored_clock`, the clock at exactly that width.
    The exactness the file cannot promise is dropped once, here, rather than
    dropped altogether: a machine written at the wrong potential, or one that
    inherited a fixture's overclock, is a different number at 32 bits too, so
    ``decode(emit(placement)) == placement`` catches it.
    """

    id: int
    class_name: str
    pose: Pose
    recipe_class: str
    clock: Fraction = field(default=Fraction(1), compare=False)
    somersloops: int = 0
    #: :attr:`clock` at the width the file holds it, and the part of the clock
    #: equality asks about. Derived in ``__post_init__``, never passed in.
    stored_clock: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stored_clock", stored_float(float(self.clock)))


@dataclass(frozen=True, slots=True)
class AttachmentObj:
    """A conveyor splitter or merger."""

    id: int
    class_name: str
    pose: Pose


@dataclass(frozen=True, slots=True)
class PoleObj:
    """A power pole."""

    id: int
    class_name: str
    pose: Pose


@dataclass(frozen=True, slots=True)
class FoundationObj:
    """One foundation slab."""

    id: int
    class_name: str
    pose: Pose


@dataclass(frozen=True, slots=True)
class BeltRun:
    """One conveyor belt: the spline it runs along, in world coordinates.

    ``points`` are ``(location, arrive tangent, leave tangent)`` triples as
    :mod:`flab2bp.sfy.layout.splines` builds them. The belt actor stands at the
    first point, so :meth:`local_points` is what goes into ``mSplineData``.

    ``item_id`` is the FactorioLab item id the run carries (``labmap.items``
    maps it to the game's ``Desc_*_C``) and ``items_per_second`` its rate. A
    blueprint has no property for either -- a belt in a file carries the items
    that happen to be sitting on it, not a contract -- so they are outside
    equality, as the module docstring explains.

    ``boundary_start`` and ``boundary_end`` mark an end that sits on the
    designer wall and is deliberately left unwired: the build's external inputs
    arrive at the ``-Y`` wall and its outputs leave at the ``+Y`` wall, and what
    they are joined to is outside the blueprint. They are the one thing in this
    model that says an unwired end is intended, so ``ports.connected_once``
    exempts a flagged end and ``flow.boundary`` holds it to the wall it claims.
    Like the two rate fields they are outside equality: no property in the file
    carries them, so :func:`~flab2bp.sfy.layout.emit.decode` hands a belt back
    unflagged and the round trip is still a statement about the file.
    """

    id: int
    class_name: str
    points: tuple[SplinePoint, ...]
    item_id: str = field(default="", compare=False)
    items_per_second: Fraction = field(default=Fraction(0), compare=False)
    boundary_start: bool = field(default=False, compare=False)
    boundary_end: bool = field(default=False, compare=False)

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError(f"belt {self.id} needs at least two points to be a run")
        origin = Pose(*self.points[0][0], 0.0).location
        object.__setattr__(
            self,
            "points",
            tuple(
                (_as_stored(location, origin), arrive, leave)
                for location, arrive, leave in self.points
            ),
        )

    @property
    def start(self) -> Vector:
        """Where the run begins: the belt's ``flow.entry`` end (see :func:`belt_ends`)."""
        return self.points[0][0]

    @property
    def end(self) -> Vector:
        """Where the run ends: the belt's ``flow.exit`` end."""
        return self.points[-1][0]

    @property
    def pose(self) -> Pose:
        """Where the belt actor stands: on its first point, unrotated.

        A conveyor's two connection components sit at the actor's origin with no
        offset and no rotation of their own (``registry.json`` gives both
        ``ConveyorAny`` ports ``translation (0,0,0)``), so a belt's ends are its
        spline's and turning the actor would say nothing.
        """
        return Pose(*self.start, 0.0)

    def local_points(self) -> tuple[SplinePoint, ...]:
        """The spline in the belt actor's own frame, for ``templates.set_spline``.

        ``mSplineData`` is stored relative to the actor, which stands at the
        run's first point, so that point is the local origin. The tangents are
        directions and do not move.
        """
        origin = self.pose.location
        return tuple(
            (
                (
                    location[0] - origin[0],
                    location[1] - origin[1],
                    location[2] - origin[2],
                ),
                arrive,
                leave,
            )
            for location, arrive, leave in self.points
        )


@dataclass(frozen=True, slots=True)
class PipeRun:
    """A bidirectional pipeline spline. Rates are cubic metres per second."""

    id: int
    class_name: str
    points: tuple[SplinePoint, ...]
    item_id: str = field(default="", compare=False)
    cubic_metres_per_second: Fraction = field(default=Fraction(), compare=False)
    boundary_start: bool = field(default=False, compare=False)
    boundary_end: bool = field(default=False, compare=False)
    snapped_passthroughs: tuple[int | None, int | None] = (None, None)

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError(f"pipe {self.id} needs at least two points to be a run")
        if len(self.snapped_passthroughs) != 2:
            raise ValueError("a pipe has exactly two passthrough slots")
        origin = Pose(*self.points[0][0], 0.0).location
        object.__setattr__(
            self,
            "points",
            tuple(
                (_as_stored(location, origin), arrive, leave)
                for location, arrive, leave in self.points
            ),
        )

    @property
    def start(self) -> Vector:
        return self.points[0][0]

    @property
    def end(self) -> Vector:
        return self.points[-1][0]

    @property
    def pose(self) -> Pose:
        return Pose(*self.start, 0.0)

    def local_points(self) -> tuple[SplinePoint, ...]:
        origin = self.pose.location
        return tuple(
            ((at[0] - origin[0], at[1] - origin[1], at[2] - origin[2]), arrive, leave)
            for at, arrive, leave in self.points
        )


@dataclass(frozen=True, slots=True)
class PipeAttachmentObj:
    """A native pipeline junction or pump; power is represented by WireObj."""

    id: int
    class_name: str
    pose: Pose


@dataclass(frozen=True, slots=True)
class BeamObj:
    """A beam extending ``length_cm`` along its actor's local +X axis."""

    id: int
    class_name: str
    pose: Pose
    length_cm: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.length_cm) or self.length_cm <= 0:
            raise ValueError("beam length must be finite and positive")
        object.__setattr__(self, "length_cm", stored_float(self.length_cm))


@dataclass(frozen=True, slots=True)
class PassthroughObj:
    """Foundation hole with transport COMPONENT references, not fictional ports."""

    id: int
    class_name: str
    pose: Pose
    thickness_cm: float
    top_connection: tuple[int, str] | None = None
    bottom_connection: tuple[int, str] | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.thickness_cm) or self.thickness_cm <= 0:
            raise ValueError("passthrough thickness must be finite and positive")
        object.__setattr__(self, "thickness_cm", stored_float(self.thickness_cm))


@dataclass(frozen=True, slots=True)
class LiftObj:
    """One conveyor lift: a vertical run between two connections.

    A lift is not a belt with a spline in it.  It is an actor with a height:
    ``pose`` is the BOTTOM, because ``AFGBuildableConveyorLift::SetupConnections``
    puts ``mConnection0`` at the actor transform exactly and facing the actor's
    own forward (the ``lift.connectors`` rule, and
    :class:`~flab2bp.sfy.registry.LiftGeometry` carries the two numbers), and
    the top end is ``mTopTransform`` beside it.

    ``height_cm`` is SIGNED, the way ``mTopTransform``'s translation is: the top
    sits that far along the geometry's ``top_offset_axis`` from the actor, above
    it for a positive height and below it for a negative one.  Which way items
    travel does not depend on that sign.  ``reversed_swaps_flow`` is false --
    items always enter by ``mConnection0``, and
    ``AFGBuildableConveyorLift::GetConveyorLiftFlowDirection`` reads nothing but
    the sign of that Z to decide which way the MESH runs -- so a lift that
    carries items upward is one whose actor is at the bottom, and a lift that
    carries them down is one whose actor is at the top with a negative height.
    :func:`belt_ends` names the two ports either way: the entry is the bottom's.

    ``top_yaw_deg`` is the yaw ``mTopTransform`` carries, **in the actor's own
    frame** (``Hologram/FGConveyorLiftHologram.h:102-104``: "Transform of the
    top part of the lift, in actor local space"), so the top end faces the
    pose's yaw plus this one.  ``lift.top_yaw`` says it is a whole number of
    ``top_yaw_step_deg`` steps -- four directions, 90 degrees apart -- and the
    validator holds it to that.

    The height is NOT held at :func:`stored_float` width.  A pose is ten 32-bit
    floats because that is what the object table writes; ``mTopTransform`` is a
    property beside it, written as doubles, so rounding the height here would
    claim a coarseness the file does not have.
    """

    id: int
    class_name: str
    pose: Pose
    height_cm: float
    top_yaw_deg: float = 0.0
    snapped_passthroughs: tuple[int | None, int | None] = (None, None)
    boundary_start: bool = field(default=False, compare=False)
    boundary_end: bool = field(default=False, compare=False)

    def __post_init__(self) -> None:
        if len(self.snapped_passthroughs) != 2:
            raise ValueError("a lift has exactly two passthrough slots")

    def bottom_end(self, geometry: LiftGeometry) -> tuple[Vector, Vector]:
        """``(where the bottom connection sits, which way it faces)``, in world space.

        ``mConnection0``'s relative transform is the identity moved
        ``CONNECTION_RELATIVE_FORWARD`` (which is zero) along its own forward,
        so the connection is at the actor and faces the actor's forward:
        ``geometry.bottom_offset`` and ``geometry.bottom_facing``, both turned
        by the pose's yaw.
        """
        facing = (
            self._snapped_facing(0)
            if self.snapped_passthroughs[0] is not None
            else self._facing(geometry, 0.0)
        )
        return (self._at(geometry.bottom_offset), facing)

    def top_end(self, geometry: LiftGeometry) -> tuple[Vector, Vector]:
        """``(where the top connection sits, which way it faces)``, in world space.

        ``mConnection1``'s relative transform is ``mTopTransform``: its
        translation is :meth:`LiftGeometry.top_offset` of this lift's height and
        its rotation is :attr:`top_yaw_deg`, so the end sits that far along the
        offset axis and faces that much further round than the bottom.
        """
        return (
            self._at(geometry.top_offset(self.height_cm)),
            self._snapped_facing(1)
            if self.snapped_passthroughs[1] is not None
            else self._facing(geometry, self.top_yaw_deg),
        )

    def _at(self, offset: Vector) -> Vector:
        turned = quat_rotate(self.pose.transform().rotation, offset)
        origin = self.pose.location
        return (turned[0] + origin[0], turned[1] + origin[1], turned[2] + origin[2])

    def _facing(self, geometry: LiftGeometry, extra_yaw_deg: float) -> Vector:
        """The connector normal of an end turned ``extra_yaw_deg`` past the actor.

        ``UFGFactoryConnectionComponent::GetConnectorNormal`` is the component's
        own forward (``FGFactoryConnectionComponent.h:142``), and
        ``geometry.bottom_facing`` is what that forward is under an unrotated
        relative transform.  The top's relative transform adds its own yaw to
        the actor's, which is the only difference between the two ends.
        """
        local = quat_rotate(yaw_quaternion(extra_yaw_deg), geometry.bottom_facing)
        return snap_zeros(quat_rotate(self.pose.transform().rotation, local))

    def _snapped_facing(self, index: int) -> Vector:
        # SetupConnections 0x505bdf/0x505bf9: base Z >= top Z picks Up
        # for component0; component1 negates it (0x505f4f..0x505f93).
        sign = 1.0 if self.height_cm > 0.0 else -1.0
        local = (0.0, 0.0, sign if index else -sign)
        return snap_zeros(quat_rotate(self.pose.transform().rotation, local))


@dataclass(frozen=True, slots=True, eq=False)
class Link:
    """One connection: ``a``'s port wired to ``b``'s port.

    Each side is ``(object id, port name)``, and a port name is the name of the
    connection component on the actor -- ``Output0`` on a Constructor,
    ``Input1`` on a splitter, ``ConveyorAny0`` on a belt -- which is what the
    registry calls its ports and what :meth:`TemplateLibrary.instantiate
    <flab2bp.sfy.templates.TemplateLibrary.instantiate>` names them.

    ``a`` is the upstream side: the output, or the belt end items leave by.
    Nothing in the file records a direction -- ``mConnectedComponent`` is
    written on both sides -- so :func:`~flab2bp.sfy.layout.emit.decode` puts the
    pair back in that order by reading the two ports' directions out of the
    registry.
    """

    a: tuple[int, str]
    b: tuple[int, str]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Link):
            return NotImplemented
        return (self.a == other.a and self.b == other.b) or (
            self.a == other.b and self.b == other.a
        )

    def __hash__(self) -> int:
        return hash((min(self.a, self.b), max(self.a, self.b)))


@dataclass(frozen=True, slots=True)
class WireObj:
    """One power line joining two power connections."""

    id: int
    class_name: str
    link: Link


Placed = (
    MachineObj
    | AttachmentObj
    | BeltRun
    | LiftObj
    | PoleObj
    | WireObj
    | FoundationObj
    | PipeRun
    | PipeAttachmentObj
    | BeamObj
    | PassthroughObj
)
"""Anything a placement holds: everything with an id and a class name."""


@dataclass(frozen=True, slots=True)
class StackLane:
    """Paired boundary endpoints, local rates and repeat-limiting transport capacity."""

    item_id: str
    kind: Literal["belt", "pipe"]
    bottom: tuple[int, str]
    top: tuple[int, str]
    input_per_second: Fraction
    output_per_second: Fraction
    capacity_per_second: Fraction
    required_input_head_m: float = 0.0


@dataclass(frozen=True, slots=True)
class SfyPlacement:
    """A whole build, placed.

    ``links`` is every belt-to-port and belt-to-belt connection in the build;
    a wire's own link lives on the :class:`WireObj`.

    ``description`` and ``short_desc`` are the text the ``.sbpcfg`` beside the
    blueprint carries, so they are not part of what the ``.sbp`` round trip
    compares. Machine clocks are also serialized in the blueprint as potential
    floats; :class:`MachineObj` compares their stored-width representation.

    ``links`` is held in a canonical order -- sorted, and built once in
    :meth:`__post_init__` -- rather than in the order the author wrote them.
    A blueprint records a connection on both actors and nowhere records a
    SEQUENCE, so the order :func:`~flab2bp.sfy.layout.emit.decode` hands back is
    the order the connection components stand in the file, which no author
    chooses. Comparing the tuple as written would make
    ``decode(emit(placement)) == placement`` false for every build that has more
    than one belt in it, over a difference the file does not hold.
    """

    designer: Designer
    machines: tuple[MachineObj, ...] = ()
    attachments: tuple[AttachmentObj, ...] = ()
    belts: tuple[BeltRun, ...] = ()
    lifts: tuple[LiftObj, ...] = ()
    poles: tuple[PoleObj, ...] = ()
    wires: tuple[WireObj, ...] = ()
    foundations: tuple[FoundationObj, ...] = ()
    links: tuple[Link, ...] = ()
    description: str = field(default="", compare=False)
    short_desc: str = field(default="", compare=False)
    pipes: tuple[PipeRun, ...] = ()
    pipe_attachments: tuple[PipeAttachmentObj, ...] = ()
    beams: tuple[BeamObj, ...] = ()
    passthroughs: tuple[PassthroughObj, ...] = ()
    stack_height_cm: float = field(default=0.0, compare=False)
    stack_connection_gap_cm: float = field(default=0.0, compare=False)
    stack_lanes: tuple[StackLane, ...] = field(default=(), compare=False)
    #: Lazily built by :meth:`by_id`, and outside equality, repr and ``__init__``
    #: because it is a cache of the tuples above rather than part of the build.
    _index: dict[int, Placed] | None = field(default=None, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "links", tuple(sorted(self.links, key=_link_order)))

    @property
    def objects(self) -> tuple[Placed, ...]:
        """Every placed object, in the order :func:`~flab2bp.sfy.layout.emit.emit`
        writes them."""
        return (
            *self.foundations,
            *self.machines,
            *self.attachments,
            *self.belts,
            *self.lifts,
            *self.poles,
            *self.pipes,
            *self.pipe_attachments,
            *self.beams,
            *self.passthroughs,
            *self.wires,
        )

    def by_id(self, id: int) -> Placed:
        """The object with this id, whatever kind it is.

        Ids are the build's own numbering and are unique across every kind: they
        are what a :class:`Link` names and what the actor's name in the file
        carries.

        The index behind it is built once and kept: a validator asks this
        question per link and per wire, and a scan per question turns a report
        over a designer-sized build quadratic in the number of objects.
        """
        index = self._index
        if index is None:
            index = {placed.id: placed for placed in self.objects}
            object.__setattr__(self, "_index", index)
        try:
            return index[id]
        except KeyError:
            raise KeyError(f"this placement has no object with id {id}") from None


def _link_order(link: Link) -> tuple[int, str, int, str]:
    """The key :class:`SfyPlacement` sorts ``links`` by.

    Component pairs are physically undirected references. Retain the authored
    side order for routing, but order/equality must not depend on which pipe end
    the decoder encounters first.
    """
    first, second = min(link.a, link.b), max(link.a, link.b)
    return (first[0], first[1], second[0], second[1])


def lift_geometry(registry: Registry, class_name: str) -> LiftGeometry:
    """Where this lift class's two ends sit, out of ``registry.json``.

    Every field of the :class:`~flab2bp.sfy.registry.LiftGeometry` was read out
    of the game -- the ``lift.connectors`` and ``lift.top_yaw`` rules -- and
    ``load_registry`` refuses a lift class that carries none, so a class with no
    geometry here is not a lift and raises rather than being given a default.
    """
    buildable = registry.buildables.get(class_name)
    geometry = None if buildable is None else buildable.lift
    if geometry is None:
        raise KeyError(f"the registry gives {class_name} no lift geometry, so it is not a lift")
    return geometry


def belt_ends(registry: Registry, class_name: str) -> tuple[str, str]:
    """``(entry, exit)``: which connection of a conveyor items enter and leave by.

    Both names come from ``registry.json``'s ``flow`` for the class, which was
    read out of the game -- ``AFGBuildableConveyorBase::Factory_Tick`` grabs
    through ``mConnection0``, and the cooked class default object says which
    named component that is. A class with no ``flow`` is not a conveyor and
    raises.

    A conveyor LIFT is a conveyor too, and this answers for one: a lift does not
    override ``Factory_Tick``, so items enter it by ``mConnection0`` -- the end
    at the actor, whichever way the lift runs -- and leave by ``mConnection1``
    at ``mTopTransform`` (the ``lift.connectors`` rule). The sentence below
    about the first spline point is a belt's; a lift has no spline, and
    :meth:`LiftObj.bottom_end` is where its entry sits.

    That the entry sits at the run's **first** spline point is this project's
    stated assumption from M1b and not something read out of the game
    (``query.spline_points`` says so at ``query.py:47-54``). Every placement
    this package builds keeps it, and the validator checks the geometry that
    follows from it.
    """
    buildable = registry.buildables.get(class_name)
    flow = None if buildable is None else buildable.flow
    if flow is None:
        raise KeyError(f"the registry gives {class_name} no conveyor flow, so it has no belt ends")
    return (flow.entry, flow.exit)


def pipe_ends(registry: Registry, class_name: str) -> tuple[str, str]:
    """Spline start/end components, without assigning conveyor flow to a pipe.

    AFGBuildablePipeBase::Splice documents end 1 of its first segment and end 0
    of its second segment at the cut. Pipeline's named component pair implements
    those native indices; both ends can carry fluid in either direction.
    ``assets.json.conveyor_connections`` records the actual CDO member refs for
    all four pipeline variants as PipelineConnection0/PipelineConnection1.
    PipeBase::SetupConnections (0x54b040) places them at the first/last spline
    points with -LeaveTangent/+ArriveTangent, respectively.
    """
    buildable = registry.buildables[class_name]
    names = {port.name for port in buildable.ports if port.kind == "pipe"}
    pair = ("PipelineConnection0", "PipelineConnection1")
    if buildable.native_class != "FGBuildablePipeline" or not set(pair) <= names:
        raise KeyError(f"{class_name} has no known native pipeline endpoint pair")
    return pair


def _as_stored(location: Vector, origin: Vector) -> Vector:
    """One spline point as the file will give it back.

    A belt's spline is stored in the actor's own frame: the actor's transform at
    32-bit width in the object table, the points beside it as doubles. So a
    world point comes back out of a blueprint as ``origin + (point - origin)``
    with the origin rounded to a ``float``, and storing exactly that is what
    makes the round trip an equality rather than a near miss.
    """
    return (
        origin[0] + (location[0] - origin[0]),
        origin[1] + (location[1] - origin[1]),
        origin[2] + (location[2] - origin[2]),
    )
