"""Bytes after an object's property list, by object kind and class.

The game serializes class-specific state after the tagged properties, and the
shape follows the object's kind first: an actor writes ``int32 0`` (the four
bytes are ``UObject``'s ``HasGuid`` bool and padding), a component writes
``int32 0, int32 0`` (the second is ``UCSModifiedProperties``' empty array).
Two classes break out of that rule: conveyor belts and lifts, which are actors
writing ``int32 0, int32 item count`` followed by that many items, and
``Build_PowerLine_C``, which writes the actor's ``int32 0`` and then the two
``FObjectReferenceDisc`` naming the circuit connections the wire joins.

The wire's layout is the game's, read out of ``AFGBuildableWire::Serialize``;
``docs/sfy-struct-layouts.md`` records the disassembly and the evidence
addresses. Because that layout is known in full, a power line is the one class
whose trailer is decoded strictly: bytes that are not the two references raise
:class:`~flab2bp.sfy.archive.ArchiveError` rather than surviving as opaque
bytes, so nothing is silently dropped or invented.

Anything else that does not match the shape its kind and class predict is kept
verbatim as a :class:`PlainTrailer`, so an unknown class still writes back byte
for byte. Only an empty item list is decoded for conveyors; a belt carrying
items stays plain until the item layout is needed.

``kind`` is the object table's kind field: 1 actor, 0 component (the constants
live in :mod:`flab2bp.sfy.objects`, which imports this module).
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer

__all__ = [
    "POWER_LINE",
    "BuildableTrailer",
    "ComponentTrailer",
    "ConveyorItem",
    "ConveyorTrailer",
    "PlainTrailer",
    "PowerLineTrailer",
    "Trailer",
    "read_trailer",
    "trailer_for_new",
    "write_trailer",
]

_ACTOR = 1
_COMPONENT = 0


@dataclass(frozen=True, slots=True)
class PlainTrailer:
    """Bytes whose layout is not decoded here; written back verbatim."""

    raw: bytes


@dataclass(frozen=True, slots=True)
class BuildableTrailer:
    """An actor's four zero bytes."""


@dataclass(frozen=True, slots=True)
class ComponentTrailer:
    """A component's eight zero bytes."""


@dataclass(frozen=True, slots=True)
class ConveyorItem:
    """One item riding a belt or lift; its layout is not decoded here."""

    raw: bytes


@dataclass(frozen=True, slots=True)
class ConveyorTrailer:
    """``int32 0``, the item count, then that many items."""

    items: tuple[ConveyorItem, ...]


@dataclass(frozen=True, slots=True)
class PowerLineTrailer:
    """The two circuit connections a wire joins, as ``AFGBuildableWire`` saves them.

    ``mConnections[2]`` is replicated rather than ``SaveGame``, so the class's
    own ``Serialize`` writes it here instead of it appearing in the tagged
    property list -- where the wire's two ``SaveGame`` members,
    ``mWireInstances`` and ``mCachedLength``, do appear. Both references are
    written even when a wire dangles, as two empty strings each.
    """

    connections: tuple[ObjectRef, ObjectRef]


Trailer = PlainTrailer | BuildableTrailer | ComponentTrailer | ConveyorTrailer | PowerLineTrailer

POWER_LINE = "Build_PowerLine_C"


def _is_conveyor(class_name: str) -> bool:
    return class_name.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))


def _read_power_line(r: Reader) -> PowerLineTrailer:
    """``int32 0`` from the base actor, then ``mConnections[0]`` and ``[1]``."""
    lead = r.i32()
    if lead != 0:
        raise ArchiveError(f"a power line's lead int32 is {lead}, and the engine writes 0")
    connections = (r.object_ref(), r.object_ref())
    if r.remaining():
        raise ArchiveError(
            f"{r.remaining()} bytes left after a power line's two connection references; "
            "a blueprint saved at save custom version 33 to 39 carries two legacy FVector "
            "mConnectionLocations there, which this decoder does not read"
        )
    return PowerLineTrailer(connections)


def read_trailer(r: Reader, class_name: str, kind: int) -> Trailer:
    """Read the rest of an object body as its class-specific trailer."""
    if class_name == POWER_LINE:
        return _read_power_line(r)
    raw = r.bytes(r.remaining())
    if _is_conveyor(class_name):
        if raw == b"\x00" * 8:
            return ConveyorTrailer(())
        return PlainTrailer(raw)
    if kind == _COMPONENT and raw == b"\x00" * 8:
        return ComponentTrailer()
    if kind == _ACTOR and raw == b"\x00" * 4:
        return BuildableTrailer()
    return PlainTrailer(raw)


def write_trailer(w: Writer, t: Trailer) -> None:
    match t:
        case PlainTrailer(raw):
            w.raw(raw)
        case PowerLineTrailer(connections):
            if len(connections) != 2:
                raise ArchiveError(f"a power line joins two connections, not {len(connections)}")
            w.i32(0)
            for ref in connections:
                w.object_ref(ref)
        case BuildableTrailer():
            w.i32(0)
        case ComponentTrailer():
            w.i32(0)
            w.i32(0)
        case ConveyorTrailer(items):
            w.i32(0)
            w.i32(len(items))
            for item in items:
                w.raw(item.raw)
        case _:
            raise ArchiveError(f"unknown trailer {t!r}")


def trailer_for_new(
    class_name: str,
    kind: int,
    connections: tuple[ObjectRef, ObjectRef] | None = None,
) -> Trailer:
    """The trailer a freshly authored object of this class and kind needs.

    Every class but the power line has an empty one; a wire carries the two
    circuit connections it joins, which only the caller placing it knows.
    """
    if class_name == POWER_LINE:
        if connections is None:
            raise KeyError(f"{POWER_LINE} needs the two circuit connections the wire joins")
        return PowerLineTrailer(connections)
    if connections is not None:
        raise ArchiveError(f"{class_name} is not a wire and joins no circuit connections")
    if _is_conveyor(class_name):
        return ConveyorTrailer(())
    if kind == _COMPONENT:
        return ComponentTrailer()
    if kind == _ACTOR:
        return BuildableTrailer()
    raise KeyError(f"{class_name}: object kind {kind}")
