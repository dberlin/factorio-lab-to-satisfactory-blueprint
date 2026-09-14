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
a round trip: ``decode(emit(placement)) == placement``. Three things a placement
carries have no property in a blueprint -- a machine's clock, its somersloops,
and a belt's item and rate -- so ``decode`` returns them at their defaults and
:mod:`~flab2bp.sfy.layout.model` leaves them out of what equality compares. The
clock in particular is not written anywhere: Task 4's reading of the game found
no save property a requested potential is stored in, so a placement's clocks
survive in :attr:`SfyPlacement.description`, which goes into the ``.sbpcfg``
beside the blueprint, and nowhere else. Until the property is read, a build with
underclocked machines pastes at 100 % and the description is what tells the
player.

Power lines are refused rather than half-authored:
:func:`~flab2bp.sfy.trailers.trailer_for_new` says a power line's trailer has to
be copied from a fixture, because what it holds -- the wire's two connections --
is still opaque bytes. Task 9 decodes it and teaches this module to write one.
"""

from __future__ import annotations

import math

from flab2bp.sfy.codec import Blueprint
from flab2bp.sfy.geometry import quat_rotate
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
)
from flab2bp.sfy.layout.splines import Quaternion, yaw_quaternion
from flab2bp.sfy.objects import ACTOR, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import Object
from flab2bp.sfy.properties import Vector as PropertyVector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import DESIGNER_CLASSES, Designer, designer
from flab2bp.sfy.templates import (
    TemplateLibrary,
    apply_recipe,
    assemble,
    connect,
    set_spline,
)

__all__ = ["FIRST_NAME_ID", "EmitError", "decode", "emit"]

FIRST_NAME_ID = 2_000_000_000
"""The game names an actor ``<Class>_<digits>``; a placement's object id is
added to this. Numbering from 2e9 keeps every name a blueprint of ours carries
clear of every name in every fixture template, which is the convention
``scripts/sfy_checkpoint1.py`` set."""

CURRENT_RECIPE = "mCurrentRecipe"

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

    Raises :class:`EmitError` for a placement the file cannot hold -- a wire, a
    duplicate id, a link to a port an object does not have -- and
    :class:`~flab2bp.sfy.templates.TemplateError` for a class the fixture
    library has no template of.
    """
    del labmap  # see the docstring: nothing written here needs a lab id
    if placement.wires:
        raise EmitError(
            "a power line's class trailer is still opaque, so one cannot be authored: "
            "trailers.trailer_for_new refuses Build_PowerLine_C and says the trailer has to "
            "be copied from a fixture template"
        )

    objects: list[tuple[ObjectHeader, ObjectData]] = []
    spans: dict[int, tuple[int, int]] = {}

    def place(obj_id: int, class_name: str, pose: Pose) -> tuple[int, int]:
        if obj_id < 0:
            raise EmitError(
                f"object id {obj_id} is negative, which would name an actor below "
                f"{FIRST_NAME_ID} and into the range the fixture templates use"
            )
        if obj_id in spans:
            raise EmitError(f"two objects in this placement share the id {obj_id}")
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

    What the file has no property for comes back at its default: a machine's
    clock and somersloops, a belt's item and rate, and the placement's
    description, which lives in the ``.sbpcfg``.
    """
    machines: list[MachineObj] = []
    attachments: list[AttachmentObj] = []
    belts: list[BeltRun] = []
    poles: list[PoleObj] = []
    foundations: list[FoundationObj] = []
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
            machines.append(MachineObj(obj_id, header.class_name, pose, recipe.ref.name))
        elif native in _BELT_NATIVE:
            belts.append(BeltRun(obj_id, header.class_name, _world_points(header, data)))
        elif native in _ATTACHMENT_NATIVE:
            attachments.append(AttachmentObj(obj_id, header.class_name, pose))
        elif native in _POLE_NATIVE:
            poles.append(PoleObj(obj_id, header.class_name, pose))
        elif native in _FOUNDATION_NATIVE:
            foundations.append(FoundationObj(obj_id, header.class_name, pose))
        elif native in _WIRE_NATIVE:
            raise EmitError(
                f"{header.name}: a power line's connections are still opaque trailer bytes, "
                "so it cannot be read back into a placement"
            )
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
        foundations=tuple(foundations),
        links=_links(bp, registry, ids),
    )


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
    """
    x, y, z, w = transform.rotation
    if abs(x) > _ROTATION_TOLERANCE or abs(y) > _ROTATION_TOLERANCE:
        raise EmitError(
            f"{header.name} is turned about more than its up axis: {transform.rotation}"
        )
    yaw = math.degrees(2.0 * math.atan2(z, w))
    whole = float(round(yaw))
    if _same_rotation(yaw_quaternion(whole), transform.rotation):
        return whole
    return yaw


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
