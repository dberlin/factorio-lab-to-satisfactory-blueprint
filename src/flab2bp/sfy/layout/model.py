"""Where every object in a Satisfactory build stands, in centimetres.

A :class:`SfyPlacement` is the whole build as geometry: machines, conveyor
attachments, belt runs, poles, wires and foundations, each with an id, the game
class it is built from, and where it is. It is the hand-off between the layout
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

from array import array
from dataclasses import dataclass, field
from fractions import Fraction

from flab2bp.sfy.layout.splines import SplinePoint, Vector, yaw_quaternion
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import Designer

__all__ = [
    "UNIT_SCALE",
    "AttachmentObj",
    "BeltRun",
    "FoundationObj",
    "Link",
    "MachineObj",
    "Placed",
    "PoleObj",
    "Pose",
    "SfyPlacement",
    "SplinePoint",
    "Vector",
    "WireObj",
    "belt_ends",
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
    """Where one object stands and which way it faces.

    ``yaw_deg`` is a rotation about ``+Z``, which is the only rotation the build
    gun applies to a hologram (``buildable.rotation_step``); for a grid object it
    is one of 0, 90, 180 and -90.

    All four numbers are held at 32-bit width.  ``x``, ``y`` and ``z`` because
    that is what ``objects.write_toc`` writes an actor's translation at; the yaw
    for the same reason one step removed -- it becomes an ``FQuat`` of four
    ``f32``, and the angle that quaternion gives back is the ``f32`` yaw rather
    than the ``f64`` one that built it.  Rounding here is what makes a fractional
    yaw survive :func:`~flab2bp.sfy.layout.emit.decode`.
    """

    x: float
    y: float
    z: float
    yaw_deg: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "z", "yaw_deg"):
            object.__setattr__(self, name, stored_float(getattr(self, name)))

    @property
    def location(self) -> Vector:
        return (self.x, self.y, self.z)

    def transform(self) -> Transform:
        """The actor transform the object table writes for this pose."""
        return Transform(yaw_quaternion(self.yaw_deg), self.location, UNIT_SCALE)


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


@dataclass(frozen=True, slots=True)
class WireObj:
    """One power line joining two power connections."""

    id: int
    class_name: str
    link: Link


Placed = MachineObj | AttachmentObj | BeltRun | PoleObj | WireObj | FoundationObj
"""Anything a placement holds: everything with an id and a class name."""


@dataclass(frozen=True, slots=True)
class SfyPlacement:
    """A whole build, placed.

    ``links`` is every belt-to-port and belt-to-belt connection in the build;
    a wire's own link lives on the :class:`WireObj`.

    ``description`` and ``short_desc`` are the text the ``.sbpcfg`` beside the
    blueprint carries -- a machine's clock is recorded there, because the
    blueprint itself has nowhere to put one -- so they are not part of what the
    ``.sbp`` round trip compares.

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
    poles: tuple[PoleObj, ...] = ()
    wires: tuple[WireObj, ...] = ()
    foundations: tuple[FoundationObj, ...] = ()
    links: tuple[Link, ...] = ()
    description: str = field(default="", compare=False)
    short_desc: str = field(default="", compare=False)
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
            *self.poles,
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

    Object id then port name, upstream side first: a total order over links,
    which is all a canonical form needs.  It is written out rather than left to
    ``sorted``'s default so that the ordering is a stated property of the model
    and not an accident of how a tuple of tuples compares.
    """
    return (link.a[0], link.a[1], link.b[0], link.b[1])


def belt_ends(registry: Registry, class_name: str) -> tuple[str, str]:
    """``(entry, exit)``: which connection of a conveyor items enter and leave by.

    Both names come from ``registry.json``'s ``flow`` for the class, which was
    read out of the game -- ``AFGBuildableConveyorBase::Factory_Tick`` grabs
    through ``mConnection0``, and the cooked class default object says which
    named component that is. A class with no ``flow`` is not a conveyor and
    raises.

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
