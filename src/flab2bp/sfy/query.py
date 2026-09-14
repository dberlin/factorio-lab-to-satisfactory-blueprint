"""Read-only helpers over decoded blueprints.

Nothing here mutates a blueprint: every function walks the decoded tree and
returns plain tuples, so callers can ask the questions the layout code needs
(what is this actor's inventory, where does this belt run, what is that
connection wired to) without re-implementing the property walk each time.
"""

from __future__ import annotations

from flab2bp.sfy.archive import ObjectRef
from flab2bp.sfy.codec import Blueprint
from flab2bp.sfy.objects import COMPONENT, ObjectData, ObjectHeader
from flab2bp.sfy.properties import Array, Object, PropertyList, Struct, Value, Vector

__all__ = ["children", "connected", "find", "find_all", "object_index", "spline_points"]


def find(props: PropertyList, name: str) -> Value | None:
    """The first value tagged ``name``, or ``None`` when there is none."""
    for p in props:
        if p.tag.name == name:
            return p.value
    return None


def find_all(props: PropertyList, name: str) -> tuple[Value, ...]:
    """Every value tagged ``name``, in file order."""
    return tuple(p.value for p in props if p.tag.name == name)


def object_index(bp: Blueprint) -> dict[str, tuple[ObjectHeader, ObjectData]]:
    """Every object in the blueprint, keyed by its full instance path."""
    return {h.path: (h, d) for h, d in bp.objects}


def children(bp: Blueprint, actor_path: str) -> tuple[tuple[ObjectHeader, ObjectData], ...]:
    """The components whose TOC entry names ``actor_path`` as their parent."""
    return tuple((h, d) for h, d in bp.objects if h.kind == COMPONENT and h.parent == actor_path)


def spline_points(d: ObjectData) -> tuple[tuple[Vector, Vector, Vector], ...]:
    """``mSplineData`` as (location, arrive tangent, leave tangent) triples.

    The locations are in the spline actor's own frame, not the world's: put them
    through the actor's transform before comparing them with anything else.

    Which of a conveyor's two connections sits at which end of this list is not
    established here, and no caller may assume it. What *is* read out of the
    game is the item-flow order of the two connections --
    ``Buildables/FGBuildableConveyorBase.h:380`` and
    ``AFGBuildableConveyorBase::Factory_Tick``, which grabs through
    ``mConnection0`` -- and ``registry.json`` carries it as each conveyor mark's
    ``flow``. Tying either end to a spline index is a separate reading of the
    game, not something to take from what a blueprint happens to contain.

    Empty for an object that has no spline, which is every object but a
    conveyor belt, a conveyor lift and a pipe, and empty as well for a spline
    whose points are not the three-vector ``SplinePointData`` shape.
    """
    value = find(d.properties, "mSplineData")
    if not isinstance(value, Array):
        return ()
    out = []
    for item in value.items:
        if not isinstance(item, Struct):
            return ()
        fields = {p.tag.name: p.value for p in item.fields}
        location = fields.get("Location")
        arrive = fields.get("ArriveTangent")
        leave = fields.get("LeaveTangent")
        if not (
            isinstance(location, Vector)
            and isinstance(arrive, Vector)
            and isinstance(leave, Vector)
        ):
            return ()
        out.append((location, arrive, leave))
    return tuple(out)


def connected(d: ObjectData) -> ObjectRef | None:
    """A connection component's ``mConnectedComponent``, or ``None`` when unwired."""
    value = find(d.properties, "mConnectedComponent")
    return value.ref if isinstance(value, Object) and not value.ref.is_null else None
