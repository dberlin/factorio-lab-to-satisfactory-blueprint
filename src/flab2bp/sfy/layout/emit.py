"""Write a placement into a blueprint, and read the placement back out of one.

:func:`emit` authors nothing from scratch. Every object is a copy of an object
the game itself wrote into a fixture blueprint, stamped out by
:class:`~flab2bp.sfy.templates.TemplateLibrary`; what this module supplies is
what is ours -- where each object stands, what it is called, which recipe it
runs, what shape its spline is and what it is wired to. That is exactly the
arrangement ``scripts/sfy_checkpoint1.py`` makes by hand for one Constructor,
and this is that script generalised: the same numbering from
:data:`FIRST_NAME_ID`, the same ``apply_recipe``, ``set_spline`` and ``connect``
calls, and the same ``assemble`` at the end.

:func:`decode` is the inverse, and it exists so that the emitter can be held to
a round trip: ``decode(emit(placement)) == placement``. What a placement carries
that a blueprint has no property for -- what a belt carries and how fast -- comes
back at its default, and :mod:`~flab2bp.sfy.layout.model` leaves it out of what
equality compares.

A machine's clock and its somersloops are no longer in that company. The game's
public headers declare four ``SaveGame`` UPROPERTYs for them in
``Source/FactoryGame/Public/Buildables/FGBuildableFactory.h``
(``CommunityResources/Headers.zip``): ``mPendingPotential`` at line 609 and
``mCurrentPotential`` at line 670, ``mPendingProductionBoost`` at line 613 and
``mCurrentProductionBoost`` at line 674, each a bare ``float``. :func:`emit`
writes all four on every machine that is not a plain 100 % machine with no
somersloop in it -- the pending one because it is what the player's slider sets
and the current one because it is what ``GetCurrentPotential`` returns before a
production cycle has run.

What the numbers MEAN is the game's own data rather than this module's opinion.
A potential is a multiple of the machine's rated speed: Docs.json gives the
Constructor ``mMaxPotential`` 1.0 and the Power Shard ``mExtraPotential`` 0.5,
so three shard slots reach 2.5 and 250 % is written as ``2.5``. A production
boost is ``base_production_boost + n * production_boost_per_slot *
production_boost_multiplier``, which is
``AFGBuildableFactory::GetCurrentMaxPotentialForType`` adding
``UFGPowerShardDescriptor::GetBoostValue`` once per shard; every one of those
numbers is in the registry, read out of Docs.json. Nothing here is measured off
a blueprint: a corpus machine shows what the TAG looks like and says nothing
about what a value means.

:func:`decode` reads both back. ``somersloops`` is an integer count, and the
boost that encodes it is exact at 32-bit width, so it takes part in equality
again. ``clock`` does not, and its reason has changed rather than gone: a clock
is an exact :class:`~fractions.Fraction` and the file holds a ``float``, so
``moc=133``'s 133/100 comes back as the 32-bit number beside it and not as
itself. It stays outside equality for that, and :attr:`SfyPlacement.description`
-- which goes into the ``.sbpcfg`` -- still carries the exact figure.

Whether the GAME keeps a pasted machine's potential, or resets it to 100 %
because no power shard sits in the slot, is not something the file can answer.
That is spec section 7's open question, and it is what ``scripts/sfy_checkpoint2.py``
asks the player to look at.

A power line is written like everything else, with one difference: what joins it
to the two connections it spans is not a property but its class trailer, which
:func:`~flab2bp.sfy.trailers.trailer_for_new` builds from the pair of
``FObjectReferenceDisc`` that ``AFGBuildableWire::Serialize`` writes. The wire is
stamped out of the fixture template through :meth:`TemplateLibrary.instantiate
<flab2bp.sfy.templates.TemplateLibrary.instantiate>` like every other actor,
which takes those two connections as ``connections=`` because only the caller
placing a wire knows them. :func:`_wire_object` then authors the two properties
that are about a wire rather than about stamping one.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import replace
from fractions import Fraction

from flab2bp.sfy.archive import ObjectRef
from flab2bp.sfy.codec import Blueprint
from flab2bp.sfy.geometry import quat_rotate, world_port
from flab2bp.sfy.header import SaveObjectVersionData
from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    SfyPlacement,
    SplinePoint,
    Vector,
    WireObj,
    stored_float,
)
from flab2bp.sfy.layout.splines import Quaternion, yaw_quaternion
from flab2bp.sfy.objects import ACTOR, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import Array, Float, Object, Property, Struct, Tag, Value
from flab2bp.sfy.properties import Vector as PropertyVector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import DESIGNER_CLASSES, Designer, designer
from flab2bp.sfy.templates import (
    LEVEL,
    TemplateLibrary,
    apply_recipe,
    assemble,
    connect,
    set_spline,
)
from flab2bp.sfy.trailers import PowerLineTrailer

__all__ = ["FIRST_NAME_ID", "EmitError", "decode", "emit"]

FIRST_NAME_ID = 2_000_000_000
"""The game names an actor ``<Class>_<digits>``; a placement's object id is
added to this. Numbering from 2e9 keeps every name a blueprint of ours carries
clear of every name in every fixture template, which is the convention
``scripts/sfy_checkpoint1.py`` set."""

CURRENT_RECIPE = "mCurrentRecipe"

CURRENT_POTENTIAL = "mCurrentPotential"
PENDING_POTENTIAL = "mPendingPotential"
CURRENT_PRODUCTION_BOOST = "mCurrentProductionBoost"
PENDING_PRODUCTION_BOOST = "mPendingProductionBoost"
"""The four ``SaveGame`` floats a machine's clock and its somersloops live in,
named as ``FGBuildableFactory.h`` names them -- see the module docstring for the
line each is declared on."""

_POTENTIAL_NAMES = (
    CURRENT_POTENTIAL,
    PENDING_POTENTIAL,
    CURRENT_PRODUCTION_BOOST,
    PENDING_PRODUCTION_BOOST,
)

_FLOAT_TAGS = {name: Tag(name, "FloatProperty", 0).as_modern() for name in _POTENTIAL_NAMES}
"""The tag each of the four is written with. Every one is declared ``float`` in
the header, so every one is a ``FloatProperty``, and a corpus machine that
carries ``mPendingPotential`` writes exactly this tag -- the fixture is the
format example and nothing more."""

_WHOLE_SOMERSLOOP_TOLERANCE = 1e-4
"""How far a stored production boost may sit from a whole number of somersloops
and still be read back as that number: the boost is one 32-bit float, and the
step per somersloop is 1, 0.5 or 0.25 of one across the shipped classes."""

_ATTACHMENT_NATIVE = frozenset(
    {
        "FGBuildableAttachmentSplitter",
        "FGBuildableAttachmentMerger",
        "FGBuildableSplitterSmart",
        "FGBuildableMergerPriority",
    }
)
_POLE_NATIVE = frozenset({"FGBuildablePowerPole"})
_FOUNDATION_NATIVE = frozenset({"FGBuildableFoundationLightweight"})
_WIRE_NATIVE = frozenset({"FGBuildableWire"})
_BELT_NATIVE = frozenset({"FGBuildableConveyorBelt"})
"""Which kind of placed object a class is, by the native class ``registry.json``
read out of the game's own Docs.json -- not by the spelling of its name."""

_ROTATION_TOLERANCE = 1e-6
"""How far a stored rotation may sit from a whole degree's quaternion and still
be read back as that degree: an actor's rotation is four 32-bit floats, whose
last digit lands around 6e-8."""


class EmitError(ValueError):
    """A placement could not be written into a blueprint, or read back out of one."""


def emit(
    placement: SfyPlacement,
    registry: Registry,
    library: TemplateLibrary,
    labmap: LabMap,
    *,
    build_version: int,
    version_data: SaveObjectVersionData | None,
) -> Blueprint:
    """The blueprint a placement describes.

    Objects go in foundations first, then machines, attachments, belts and
    poles, each in the placement's own order, and :func:`assemble
    <flab2bp.sfy.templates.assemble>` puts every actor before every component,
    which is how the game writes 47 of the 49 corpus fixtures. A machine's
    recipe is applied over the whole instantiated actor with :func:`apply_recipe
    <flab2bp.sfy.templates.apply_recipe>`, because the recipe lives on the
    inventory components as well as on the actor; a belt's spline is set in the
    belt actor's own frame, with the run's first point at the actor's origin.
    A machine's clock and its somersloops go on the actor too, as the four
    ``SaveGame`` floats :func:`_set_potential` writes.

    ``links`` are wired with :func:`connect <flab2bp.sfy.templates.connect>`,
    which writes ``mConnectedComponent`` on both sides. A link names a belt end
    by its component name, and which of the two ends items enter by is the
    registry's ``flow`` -- see :func:`~flab2bp.sfy.layout.model.belt_ends` for
    that and for the M1b assumption that the entry is the run's first point.

    ``build_version`` and ``version_data`` are the newest fixture header's, the
    way the checkpoint script takes them: they tell the game the file comes from
    the family it has installed.

    ``labmap`` is not read. It is in the signature because the emitter is the
    boundary where a FactorioLab id would have to become a game class, and
    nothing that reaches the file needs one: a placement already names
    ``Build_*_C``, ``Recipe_*_C`` and the items a recipe costs come from the
    registry inside ``assemble``.

    Wires go last, after every other actor: a wire names the two connection
    COMPONENTS it joins by path, so the objects that own them have to exist
    before it can be written.

    Raises :class:`EmitError` for a placement the file cannot hold -- a
    duplicate id, a link to a port an object does not have, a wire onto itself
    -- and :class:`~flab2bp.sfy.templates.TemplateError` for a class the fixture
    library has no template of.
    """
    del labmap  # see the docstring: nothing written here needs a lab id
    objects: list[tuple[ObjectHeader, ObjectData]] = []
    spans: dict[int, tuple[int, int]] = {}

    def claim(obj_id: int) -> None:
        if obj_id < 0:
            raise EmitError(
                f"object id {obj_id} is negative, which would name an actor below "
                f"{FIRST_NAME_ID} and into the range the fixture templates use"
            )
        if obj_id in spans:
            raise EmitError(f"two objects in this placement share the id {obj_id}")

    def place(obj_id: int, class_name: str, pose: Pose) -> tuple[int, int]:
        claim(obj_id)
        start = len(objects)
        objects.extend(library.instantiate(class_name, FIRST_NAME_ID + obj_id, pose.transform()))
        spans[obj_id] = (start, len(objects))
        return spans[obj_id]

    for slab in placement.foundations:
        place(slab.id, slab.class_name, slab.pose)

    for machine in placement.machines:
        start, stop = place(machine.id, machine.class_name, machine.pose)
        recipe_path = registry.recipe_paths.get(machine.recipe_class)
        if recipe_path is None:
            raise EmitError(f"the registry has no asset path for the recipe {machine.recipe_class}")
        objects[start:stop] = apply_recipe(objects[start:stop], recipe_path, registry)
        actor_header, actor_data = objects[start]
        objects[start] = (actor_header, _set_potential(actor_data, machine, registry))

    for attachment in placement.attachments:
        place(attachment.id, attachment.class_name, attachment.pose)

    for run in placement.belts:
        start, _ = place(run.id, run.class_name, run.pose)
        header, data = objects[start]
        objects[start] = (header, set_spline(data, _spline_values(run.local_points())))

    for pole in placement.poles:
        place(pole.id, pole.class_name, pole.pose)

    for link in placement.links:
        left = _component(objects, spans, link.a)
        right = _component(objects, spans, link.b)
        if left == right:
            raise EmitError(f"a link joins {link.a} to itself")
        left_header, left_data = objects[left]
        right_header, right_data = objects[right]
        left_data, right_data = connect(left_data, left_header.path, right_data, right_header.path)
        objects[left] = (left_header, left_data)
        objects[right] = (right_header, right_data)

    for wire in placement.wires:
        claim(wire.id)
        ends = [_component(objects, spans, side) for side in (wire.link.a, wire.link.b)]
        if ends[0] == ends[1]:
            raise EmitError(f"wire {wire.id} joins {wire.link.a} to itself")
        refs = (
            ObjectRef(LEVEL, objects[ends[0]][0].path),
            ObjectRef(LEVEL, objects[ends[1]][0].path),
        )
        stand, span = _wire_stand(placement, registry, wire)
        at = len(objects)
        objects.append(_wire_object(library, wire, FIRST_NAME_ID + wire.id, stand, refs, span))
        spans[wire.id] = (at, at + 1)
        for end in ends:
            header, data = objects[end]
            objects[end] = (header, _carry_wire(data, objects[at][0].path))

    return assemble(
        tuple(objects),
        placement.designer.dims,
        registry,
        build_version=build_version,
        version_data=version_data,
    )


def decode(bp: Blueprint, registry: Registry) -> SfyPlacement:
    """The placement a blueprint holds: :func:`emit` read backwards.

    Every actor is placed by what it is: a machine is one carrying
    ``mCurrentRecipe``, a belt is a conveyor with ``mSplineData`` (put through
    the actor's transform, because the points are stored in its own frame), and
    an attachment, pole or foundation is one whose native class in
    ``registry.json`` says so. An actor that is none of those is refused rather
    than dropped -- a placement that silently lost an object would make the
    round trip meaningless.

    Ids come back off the actor's name, which :func:`emit` builds from
    :data:`FIRST_NAME_ID`; links come back off the ``mConnectedComponent``
    pairs, each pair once, upstream side first. Which side is upstream is not in
    the file -- both sides carry the same property -- so it is read from the
    registry: the port whose direction is ``output``, or the belt end its
    ``flow`` calls the exit.

    A machine's clock and its somersloops come back off the four ``SaveGame``
    floats :func:`_set_potential` writes -- the clock at the 32-bit width the
    file holds it at, which is why :class:`MachineObj` compares
    ``stored_clock`` rather than the exact :class:`~fractions.Fraction` the rate
    model works in. What the file really has no property for comes back at its
    default: a belt's item and rate, and the placement's description, which
    lives in the ``.sbpcfg``.
    """
    machines: list[MachineObj] = []
    attachments: list[AttachmentObj] = []
    belts: list[BeltRun] = []
    poles: list[PoleObj] = []
    foundations: list[FoundationObj] = []
    spans: list[tuple[int, str, tuple[ObjectRef, ObjectRef]]] = []
    ids: dict[str, int] = {}

    for header, data in bp.objects:
        if header.kind != ACTOR:
            continue
        obj_id = _object_id(header)
        ids[header.path] = obj_id
        pose = _pose(header)
        recipe = find(data.properties, CURRENT_RECIPE)
        native = _native_class(registry, header.class_name)
        if isinstance(recipe, Object) and not recipe.ref.is_null:
            machines.append(
                MachineObj(
                    obj_id,
                    header.class_name,
                    pose,
                    recipe.ref.name,
                    _clock(data),
                    _somersloops(registry, header, data),
                )
            )
        elif native in _BELT_NATIVE:
            belts.append(BeltRun(obj_id, header.class_name, _world_points(header, data)))
        elif native in _ATTACHMENT_NATIVE:
            attachments.append(AttachmentObj(obj_id, header.class_name, pose))
        elif native in _POLE_NATIVE:
            poles.append(PoleObj(obj_id, header.class_name, pose))
        elif native in _FOUNDATION_NATIVE:
            foundations.append(FoundationObj(obj_id, header.class_name, pose))
        elif native in _WIRE_NATIVE:
            if not isinstance(data.trailer, PowerLineTrailer):
                raise EmitError(
                    f"{header.name} is a power line whose trailer is not the two connection "
                    f"references AFGBuildableWire::Serialize writes: {type(data.trailer).__name__}"
                )
            spans.append((obj_id, header.class_name, data.trailer.connections))
        else:
            raise EmitError(
                f"{header.name} is a {header.class_name} ({native}), which the placement model "
                "has no object for"
            )

    return SfyPlacement(
        designer=_designer_of(bp.header.dimensions, registry),
        machines=tuple(machines),
        attachments=tuple(attachments),
        belts=tuple(belts),
        poles=tuple(poles),
        wires=tuple(
            WireObj(obj_id, class_name, Link(_wire_side(refs[0], ids), _wire_side(refs[1], ids)))
            for obj_id, class_name, refs in spans
        ),
        foundations=tuple(foundations),
        links=_links(bp, registry, ids),
    )


def _wire_side(ref: ObjectRef, ids: dict[str, int]) -> tuple[int, str]:
    """``(object id, port name)`` for one end of a wire.

    A wire's trailer names a connection COMPONENT by its full path, so the actor
    is everything before the last dot and the port is what follows -- the same
    two halves :func:`emit` put together.
    """
    parent, _, port = ref.path.rpartition(".")
    try:
        return (ids[parent], port)
    except KeyError:
        raise EmitError(f"a power line names {ref.path}, which is not in this file") from None


def _set_potential(data: ObjectData, machine: MachineObj, registry: Registry) -> ObjectData:
    """``data`` with the machine's clock and somersloops written onto it.

    Every one of the four names is REMOVED first and then written back only if
    this machine wants it. A template is a copy of some fixture's actor, and a
    fixture machine may have been overclocked by the player who built it; a
    build of ours that inherited that machine's potential would run at a speed
    nobody asked for and would not survive :func:`decode`.

    A clock of 1 and no somersloop is the class default, so nothing is written
    for it -- which is also what the game does, since an unchanged ``SaveGame``
    property is not serialised.

    The value written is the machine's exact clock at the width the file holds:
    the :class:`~fractions.Fraction` belongs to the rate model, and one ``f32``
    is all a blueprint has room for, so an inexact clock is rounded once, here,
    and :attr:`MachineObj.stored_clock <flab2bp.sfy.layout.model.MachineObj>` is
    the number a round trip is held to.
    """
    properties = [p for p in data.properties if p.tag.name not in _POTENTIAL_NAMES]
    if machine.clock != 1:
        properties += _both(CURRENT_POTENTIAL, PENDING_POTENTIAL, float(machine.clock))
    if machine.somersloops:
        properties += _both(
            CURRENT_PRODUCTION_BOOST,
            PENDING_PRODUCTION_BOOST,
            _boost_value(registry, machine.class_name, machine.somersloops),
        )
    return replace(data, properties=tuple(properties))


def _both(current: str, pending: str, value: float) -> list[Property]:
    """The current and the pending property for one setting, at the stored width."""
    stored = Float(stored_float(value))
    return [Property(_FLOAT_TAGS[current], stored), Property(_FLOAT_TAGS[pending], stored)]


def _boost_step(registry: Registry, class_name: str) -> tuple[float, float]:
    """``(base boost, boost per somersloop)`` for a class, out of the registry.

    ``GetCurrentMaxProductionBoost`` starts at the class's own
    ``mBaseProductionBoost`` and adds the somersloop's ``mExtraProductionBoost``
    scaled by the class's ``mProductionShardBoostMultiplier`` once per shard, so
    those three numbers are the whole encoding. All three are Docs.json class
    defaults; a class the registry has none for is refused rather than guessed
    at.
    """
    buildable = registry.buildables.get(class_name)
    base = None if buildable is None else buildable.base_production_boost
    multiplier = None if buildable is None else buildable.production_boost_multiplier
    per_slot = registry.limits.production_boost_per_slot
    if base is None or multiplier is None or per_slot is None:
        raise EmitError(
            f"the registry does not say what a somersloop is worth in a {class_name}, so "
            "a production boost cannot be written or read"
        )
    step = per_slot * multiplier
    if step <= 0.0:
        raise EmitError(
            f"a somersloop is worth {step} in a {class_name}, so no boost encodes a count"
        )
    return (base, step)


def _boost_value(registry: Registry, class_name: str, somersloops: int) -> float:
    """The production boost ``somersloops`` in this class's slots come to."""
    base, step = _boost_step(registry, class_name)
    return base + somersloops * step


def _clock(data: ObjectData) -> Fraction:
    """The potential an actor is stored at, as a fraction of 100 %.

    The current one, falling back to the pending one: a machine that has never
    finished a production cycle carries only the pending value, which is what
    the corpus's own machines show. An actor with neither is at the class
    default, which is 100 %.
    """
    for name in (CURRENT_POTENTIAL, PENDING_POTENTIAL):
        value = find(data.properties, name)
        if isinstance(value, Float):
            return Fraction(value.v)
    return Fraction(1)


def _somersloops(registry: Registry, header: ObjectHeader, data: ObjectData) -> int:
    """How many somersloops the production boost an actor carries encodes."""
    for name in (CURRENT_PRODUCTION_BOOST, PENDING_PRODUCTION_BOOST):
        value = find(data.properties, name)
        if not isinstance(value, Float):
            continue
        base, step = _boost_step(registry, header.class_name)
        count = (value.v - base) / step
        whole = round(count)
        if whole < 0 or abs(count - whole) > _WHOLE_SOMERSLOOP_TOLERANCE:
            raise EmitError(
                f"{header.name} carries a production boost of {value.v}, which is "
                f"{count} somersloops in a {header.class_name} and not a whole number of them"
            )
        return whole
    return 0


def _spline_values(
    points: tuple[SplinePoint, ...],
) -> tuple[tuple[PropertyVector, PropertyVector, PropertyVector], ...]:
    """The spline as the property tree carries it: three ``FVector`` per point."""
    return tuple(
        (
            PropertyVector(location[0], location[1], location[2]),
            PropertyVector(arrive[0], arrive[1], arrive[2]),
            PropertyVector(leave[0], leave[1], leave[2]),
        )
        for location, arrive, leave in points
    )


WIRE_INSTANCES = "mWireInstances"
CACHED_LENGTH = "mCachedLength"
WIRES = "mWires"
_WIRES_TAG = Tag(WIRES, "ArrayProperty", 0, inner_type="ObjectProperty").as_modern()
"""The tag the game writes ``UFGCircuitConnectionComponent::mWires`` with, for a
connection that has none yet -- an unwired Constructor's ``PowerInput`` is one."""


def _wire_stand(placement: SfyPlacement, registry: Registry, wire: WireObj) -> tuple[Pose, float]:
    """Where a wire's actor stands, and how far it spans.

    **The corpus's own convention**, which is the only place it is written down:
    of the 330 fixture wires whose two connection points the registry reproduces
    exactly, 276 stand within a centimetre of the SECOND connection's point and
    two at the first, and 482 of all 508 have a ``mWireInstances`` entry whose
    second ``CachedRelativeLocations`` is zero -- the actor sitting on the second
    endpoint.  So the actor goes on ``link.b``'s connection, unrotated: the yaw a
    fixture wire carries is the angle the player's build gun happened to be at,
    and the game throws the mesh away and rebuilds it from the two connections on
    load either way.

    The span is measured between the two connection points, which is the same
    distance ``power.wires`` holds against ``wire_max_cm``.
    """
    ends = [_connection_point(placement, registry, side) for side in (wire.link.a, wire.link.b)]
    return Pose(ends[1][0], ends[1][1], ends[1][2], 0.0), math.dist(ends[0], ends[1])


def _connection_point(placement: SfyPlacement, registry: Registry, side: tuple[int, str]) -> Vector:
    try:
        obj = placement.by_id(side[0])
    except KeyError as exc:
        raise EmitError(str(exc)) from None
    if isinstance(obj, BeltRun | WireObj):
        raise EmitError(f"a wire names {side}, which is not an object with a connection on it")
    buildable = registry.buildables.get(obj.class_name)
    port = (
        None if buildable is None else next((p for p in buildable.ports if p.name == side[1]), None)
    )
    if port is None:
        raise EmitError(f"the registry gives {obj.class_name} no connection called {side[1]}")
    return world_port(obj.pose.transform(), port)


def _wire_object(
    library: TemplateLibrary,
    wire: WireObj,
    name_id: int,
    pose: Pose,
    refs: tuple[ObjectRef, ObjectRef],
    span: float,
) -> tuple[ObjectHeader, ObjectData]:
    """One power line, stamped out of the fixture template.

    Through :meth:`TemplateLibrary.instantiate
    <flab2bp.sfy.templates.TemplateLibrary.instantiate>` like everything else in
    this module: a power line's trailer IS its two circuit connections, which
    only the placement knows, and ``instantiate`` takes them as ``connections``
    and hands them to :func:`~flab2bp.sfy.trailers.trailer_for_new`.

    What is left here is the two things that are about a WIRE rather than about
    stamping.  ``mWireInstances`` goes out EMPTY, because the game's loader
    throws the meshes away and rebuilds them from the two connections --
    ``AFGBuildableWire::Serialize``'s loading side calls
    ``DestroyWireInstances`` and then ``CreateWireInstancesBetweenConnections``
    -- so a copy of some fixture's meshes, at that save's own world coordinates,
    would be bytes the game discards.  ``mCachedLength`` is the span this wire
    really covers rather than the template's.

    A wire owns no components in the corpus and carries no reference into the
    blueprint it came out of; both are checked rather than assumed, because a
    reference this file has no object for is a file the game cannot load.
    """
    built = library.instantiate(wire.class_name, name_id, pose.transform(), connections=refs)
    if len(built) != 1:
        raise EmitError(
            f"the {wire.class_name} template owns {len(built) - 1} components, and a "
            "power line in the corpus owns none"
        )
    header, data = built[0]
    properties = []
    for p in data.properties:
        value = p.value
        if p.tag.name == WIRE_INSTANCES and isinstance(value, Array):
            value = replace(value, items=())
        elif p.tag.name == CACHED_LENGTH and isinstance(value, Float):
            value = Float(stored_float(span))
        stray = next(_level_refs(value), None)
        if stray is not None:
            raise EmitError(
                f"the {wire.class_name} template's {p.tag.name} names {stray.path} in "
                f"{stray.level}, which this blueprint has not got"
            )
        properties.append(Property(p.tag, value))
    return (header, replace(data, properties=tuple(properties)))


def _level_refs(value: Value) -> Iterator[ObjectRef]:
    """Every reference in a value that names an object in some level.

    The three shapes a reference can hide in are the three
    ``templates._map_value`` walks: a value that IS one, an array of them, and a
    struct's own property list.  A reference with no level is an asset path --
    a recipe, an item descriptor -- and names nothing in any blueprint.
    """
    if isinstance(value, Object):
        if value.ref.level:
            yield value.ref
    elif isinstance(value, Array):
        for item in value.items:
            yield from _level_refs(item)
    elif isinstance(value, Struct):
        for field in value.fields:
            yield from _level_refs(field.value)


def _carry_wire(data: ObjectData, wire_path: str) -> ObjectData:
    """``data`` with ``wire_path`` added to this connection's ``mWires``.

    The game records a wire twice over: in the wire's own class trailer, and in
    the ``mWires`` array of each circuit connection it joins.  All 1016 ends of
    the corpus's 508 wires carry it, and every entry names an actor that is in
    the same file, so a blueprint of ours writes it too.
    """
    for index, p in enumerate(data.properties):
        if p.tag.name != WIRES:
            continue
        if not isinstance(p.value, Array):
            raise EmitError(f"{WIRES} on a connection component is not an array")
        value = replace(p.value, items=(*p.value.items, Object(ObjectRef(LEVEL, wire_path))))
        return replace(
            data,
            properties=(
                *data.properties[:index],
                Property(p.tag, value),
                *data.properties[index + 1 :],
            ),
        )
    return replace(
        data,
        properties=(
            *data.properties,
            Property(_WIRES_TAG, Array(WIRES_INNER, None, (Object(ObjectRef(LEVEL, wire_path)),))),
        ),
    )


WIRES_INNER = "ObjectProperty"
"""What ``mWires`` holds, from the tag the game writes on every fixture's."""


def _component(
    objects: list[tuple[ObjectHeader, ObjectData]],
    spans: dict[int, tuple[int, int]],
    side: tuple[int, str],
) -> int:
    """Where the named port of the object with this id sits in the object list."""
    obj_id, port = side
    try:
        start, stop = spans[obj_id]
    except KeyError:
        raise EmitError(
            f"a link names the object {obj_id}, which this placement has not got"
        ) from None
    for index in range(start, stop):
        header = objects[index][0]
        if header.kind != ACTOR and header.name == port:
            return index
    raise EmitError(f"{objects[start][0].class_name} has no connection component called {port}")


def _object_id(header: ObjectHeader) -> int:
    """The placement id behind an actor's name, which :func:`emit` numbered."""
    suffix = header.name.rsplit("_", 1)[-1]
    try:
        number = int(suffix)
    except ValueError:
        raise EmitError(f"{header.name} does not end in the number emit gives an actor") from None
    if number < FIRST_NAME_ID:
        raise EmitError(
            f"{header.name} is numbered below {FIRST_NAME_ID}, so it was not written by this "
            "project"
        )
    return number - FIRST_NAME_ID


def _pose(header: ObjectHeader) -> Pose:
    transform = header.transform
    if transform is None:
        raise EmitError(f"{header.name} is an actor with no transform")
    x, y, z = transform.translation
    return Pose(x, y, z, _yaw_degrees(header, transform))


def _yaw_degrees(header: ObjectHeader, transform: Transform) -> float:
    """The yaw a stored rotation encodes, in degrees.

    A placement turns a buildable about ``+Z`` alone, so a rotation with a pitch
    or a roll in it is not one this model can state and is refused. The angle
    itself is ``2 * atan2(z, w)``; a rotation is four 32-bit floats in the
    object table, so when those numbers are a whole degree's quaternion at that
    width -- which is every yaw the build gun can produce -- that whole degree is
    the answer rather than the seven-digit angle they encode.

    Any other angle comes back as the simplest number whose own quaternion the
    file holds.  The ``f64`` angle four ``f32`` components encode is not the
    angle that built them -- it misses by about a part in ten million, which at
    thirty degrees is larger than the gap between two ``f32`` yaws -- so reading
    it back off the components alone lands one step beside the number that went
    in.  Trying the roundings in turn and taking the first whose quaternion
    matches what is stored lands back ON it, which is what makes
    ``decode(emit(placement)) == placement`` hold for a fractional yaw.  Every
    candidate is CHECKED against the stored rotation, so this never invents a
    tidier angle than the file actually carries.
    """
    x, y, z, w = transform.rotation
    if abs(x) > _ROTATION_TOLERANCE or abs(y) > _ROTATION_TOLERANCE:
        raise EmitError(
            f"{header.name} is turned about more than its up axis: {transform.rotation}"
        )
    yaw = math.degrees(2.0 * math.atan2(z, w))
    for candidate in (float(round(yaw)), *(round(yaw, places) for places in range(1, 7))):
        if _same_rotation(yaw_quaternion(candidate), transform.rotation):
            return candidate
    return stored_float(yaw)


def _same_rotation(a: Quaternion, b: tuple[float, float, float, float]) -> bool:
    return all(abs(p - q) <= _ROTATION_TOLERANCE for p, q in zip(a, b, strict=True))


def _world_points(header: ObjectHeader, data: ObjectData) -> tuple[SplinePoint, ...]:
    """A conveyor's ``mSplineData`` in world coordinates.

    The locations are stored in the actor's own frame (``query.spline_points``
    says so), so each goes through the actor's rotation and translation. The
    tangents are directions in that same frame and turn with it but do not move.
    """
    transform = header.transform
    if transform is None:
        raise EmitError(f"{header.name} is an actor with no transform")
    points = spline_points(data)
    if len(points) < 2:
        raise EmitError(f"{header.name} is a conveyor with no spline to place it by")
    origin = transform.translation
    out: list[SplinePoint] = []
    for location, arrive, leave in points:
        turned = quat_rotate(transform.rotation, (location.x, location.y, location.z))
        out.append(
            (
                (turned[0] + origin[0], turned[1] + origin[1], turned[2] + origin[2]),
                quat_rotate(transform.rotation, (arrive.x, arrive.y, arrive.z)),
                quat_rotate(transform.rotation, (leave.x, leave.y, leave.z)),
            )
        )
    return tuple(out)


def _native_class(registry: Registry, class_name: str) -> str:
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        raise EmitError(f"the registry has no buildable called {class_name}")
    return buildable.native_class


def _designer_of(dimensions: tuple[int, int, int], registry: Registry) -> Designer:
    """Which Blueprint Designer a blueprint of these dimensions was built in.

    ``assemble`` writes the designer's own ``mDimensions`` into the header, and
    the three marks have three different sizes, so the mark comes back out of
    the file rather than being assumed.
    """
    for mark, class_name in DESIGNER_CLASSES.items():
        dims = registry.buildables[class_name].designer_dims
        if dims is not None and tuple(int(v) for v in dims) == tuple(dimensions):
            return designer(mark, registry)
    raise EmitError(
        f"no Blueprint Designer is {dimensions} foundations, so this is not one of ours"
    )


def _links(bp: Blueprint, registry: Registry, ids: dict[str, int]) -> tuple[Link, ...]:
    """Every wired pair in the file, once each, upstream side first."""
    index = object_index(bp)
    seen: set[frozenset[str]] = set()
    links: list[Link] = []
    for header, data in bp.objects:
        if header.kind == ACTOR:
            continue
        peer = connected(data)
        if peer is None:
            continue
        pair = frozenset({header.path, peer.path})
        if pair in seen:
            continue
        seen.add(pair)
        other = index.get(peer.path)
        if other is None:
            raise EmitError(f"{header.path} is wired to {peer.path}, which is not in the file")
        sides = [_side(header, ids), _side(other[0], ids)]
        classes = [_owner_class(header, index), _owner_class(other[0], index)]
        ranked = sorted(
            zip(sides, classes, strict=True),
            key=lambda row: _upstreamness(registry, row[1], row[0][1]),
        )
        links.append(Link(ranked[0][0], ranked[1][0]))
    return tuple(links)


def _side(header: ObjectHeader, ids: dict[str, int]) -> tuple[int, str]:
    parent = header.parent or ""
    try:
        return (ids[parent], header.name)
    except KeyError:
        raise EmitError(f"{header.path} belongs to no actor in this file") from None


def _owner_class(header: ObjectHeader, index: dict[str, tuple[ObjectHeader, ObjectData]]) -> str:
    owner = index.get(header.parent or "")
    if owner is None:
        raise EmitError(f"{header.path} belongs to no actor in this file")
    return owner[0].class_name


def _upstreamness(registry: Registry, class_name: str, port: str) -> int:
    """0 for the side items leave by, 1 for the side they arrive at, 2 for neither.

    A machine's or an attachment's port says which it is in ``registry.json``'s
    ``direction``, read out of the cooked asset. A conveyor's two connections are
    both ``any`` -- the game decides a belt's direction by what is on each end --
    so the registry's ``flow`` decides instead: items leave a belt by its exit.
    """
    buildable = registry.buildables.get(class_name)
    if buildable is None:
        return 2
    if buildable.flow is not None:
        if port == buildable.flow.exit:
            return 0
        if port == buildable.flow.entry:
            return 1
    for candidate in buildable.ports:
        if candidate.name == port:
            if candidate.direction == "output":
                return 0
            if candidate.direction == "input":
                return 1
            return 2
    return 2
