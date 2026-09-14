"""Bytes after an object's property list, by object kind and class.

The game serializes class-specific state after the tagged properties, and the
shape follows the object's kind first: an actor writes ``int32 0`` (the four
bytes are ``UObject``'s ``HasGuid`` bool and padding), a component writes
``int32 0, int32 0`` (the second is ``UCSModifiedProperties``' empty array).
Two classes break out of that rule: conveyor belts and lifts, which are actors
writing ``int32 0, int32 item count`` followed by that many items, and
``Build_PowerLine_C``, whose trailer is ``int32 0``, two ``FObjectReferenceDisc``
for the wire's two connection components and further wire data -- kept verbatim
in this milestone and decoded in M4.

Anything that does not match the shape its kind and class predict is kept
verbatim as a :class:`PlainTrailer`, so an unknown class still writes back byte
for byte. Only an empty item list is decoded for conveyors; a belt carrying
items stays plain until the item layout is needed.

``kind`` is the object table's kind field: 1 actor, 0 component (the constants
live in :mod:`flab2bp.sfy.objects`, which imports this module).
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, Reader, Writer

__all__ = [
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
    """A power line's wire data, kept verbatim."""

    raw: bytes


Trailer = PlainTrailer | BuildableTrailer | ComponentTrailer | ConveyorTrailer | PowerLineTrailer


def _is_conveyor(class_name: str) -> bool:
    return class_name.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))


def read_trailer(r: Reader, class_name: str, kind: int) -> Trailer:
    """Read the rest of an object body as its class-specific trailer."""
    raw = r.bytes(r.remaining())
    if class_name == "Build_PowerLine_C":
        return PowerLineTrailer(raw)
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
        case PlainTrailer(raw) | PowerLineTrailer(raw):
            w.raw(raw)
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


def trailer_for_new(class_name: str, kind: int) -> Trailer:
    """The empty trailer a freshly authored object of this class and kind needs."""
    if class_name == "Build_PowerLine_C":
        raise KeyError("power line trailers are copied from a fixture template")
    if _is_conveyor(class_name):
        return ConveyorTrailer(())
    if kind == _COMPONENT:
        return ComponentTrailer()
    if kind == _ACTOR:
        return BuildableTrailer()
    raise KeyError(f"{class_name}: object kind {kind}")
