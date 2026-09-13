"""Object table (TOC) entries and the per-object data envelope.

TOC entry: int32 kind (1 actor, 0 component); FString class path; FString
level; FString instance path; int32 object flags (SaveVersion >=
SerializeObjectFlags); actors then int32 needTransform, FQuat (4 f32),
FVector translation (3 f32), FVector scale (3 f32), int32 placedInLevel;
components then FString parent actor path.

Object data: int32 size, then for actors FObjectReferenceDisc parent,
int32 component count, that many references; then the object body -- a
``uint8 SerializationControl`` byte on the newer saves, the tagged property
list, and a class-specific trailer.

The control byte is ``UObject::Serialize``'s, written from FileVersionUE5 1011
and always 0 in this corpus. It arrives with the UE 5.4 property tag, so its
presence is what tells :mod:`flab2bp.sfy.properties` which tag format to read.
A tag name is an FString of at least five bytes, so the low byte of a real
first tag is never zero and the two cannot be confused.

The object-flags field was probed on the fixture corpus: it is present at
SaveVersion 52, 58 and 60 and absent at SaveVersion 46, so the boundary
``SaveVersion >= SerializeObjectFlags`` (49) holds for every fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
from flab2bp.sfy.properties import PropertyList, read_property_list, write_property_list
from flab2bp.sfy.trailers import Trailer, read_trailer, write_trailer
from flab2bp.sfy.versions import SaveCustomVersion

__all__ = [
    "ACTOR",
    "COMPONENT",
    "ObjectData",
    "ObjectHeader",
    "Transform",
    "read_object_data",
    "read_toc",
    "write_object_data",
    "write_toc",
]

ACTOR = 1
COMPONENT = 0


@dataclass(frozen=True, slots=True)
class Transform:
    rotation: tuple[float, float, float, float]
    translation: tuple[float, float, float]
    scale: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ObjectHeader:
    kind: int
    class_path: str
    level: str
    path: str
    flags: int | None
    transform: Transform | None
    need_transform: int | None
    placed_in_level: int | None
    parent: str | None

    @property
    def name(self) -> str:
        """Last dotted component, e.g. ``Build_ConstructorMk1_C_1``."""
        return self.path.rsplit(".", 1)[-1]

    @property
    def class_name(self) -> str:
        return self.class_path.rsplit(".", 1)[-1]


@dataclass(frozen=True, slots=True)
class ObjectData:
    """One object's payload: the actor envelope, then the decoded body.

    ``control`` is the leading ``uint8 SerializationControl`` byte on bodies
    that carry one (save version 58 and up here) and ``None`` on the older
    ones, where it is absent from the bytes rather than zero.
    """

    parent: ObjectRef | None
    components: tuple[ObjectRef, ...] | None
    control: int | None
    properties: PropertyList
    trailer: Trailer


def _has_flags(save_version: int) -> bool:
    return save_version >= SaveCustomVersion.SerializeObjectFlags


def read_toc(r: Reader, count: int, save_version: int) -> tuple[ObjectHeader, ...]:
    out = []
    for _ in range(count):
        kind = r.i32()
        if kind not in (ACTOR, COMPONENT):
            raise ArchiveError(f"object kind {kind} at {r.pos}")
        class_path, level, path = r.fstring(), r.fstring(), r.fstring()
        flags = r.i32() if _has_flags(save_version) else None
        if kind == ACTOR:
            need = r.i32()
            rot = (r.f32(), r.f32(), r.f32(), r.f32())
            pos = (r.f32(), r.f32(), r.f32())
            scale = (r.f32(), r.f32(), r.f32())
            placed = r.i32()
            out.append(
                ObjectHeader(
                    kind,
                    class_path,
                    level,
                    path,
                    flags,
                    Transform(rot, pos, scale),
                    need,
                    placed,
                    None,
                )
            )
        else:
            out.append(
                ObjectHeader(kind, class_path, level, path, flags, None, None, None, r.fstring())
            )
    return tuple(out)


def write_toc(w: Writer, headers: tuple[ObjectHeader, ...], save_version: int) -> None:
    for h in headers:
        w.i32(h.kind)
        w.fstring(h.class_path)
        w.fstring(h.level)
        w.fstring(h.path)
        if _has_flags(save_version):
            w.i32(h.flags if h.flags is not None else 0)
        if h.kind == ACTOR:
            if h.transform is None:
                raise ArchiveError(f"actor {h.path} has no transform")
            w.i32(h.need_transform or 0)
            for v in h.transform.rotation:
                w.f32(v)
            for v in h.transform.translation:
                w.f32(v)
            for v in h.transform.scale:
                w.f32(v)
            w.i32(h.placed_in_level or 0)
        else:
            w.fstring(h.parent or "")


def read_object_data(raw: bytes, header: ObjectHeader) -> ObjectData:
    r = Reader(raw)
    parent: ObjectRef | None = None
    components: tuple[ObjectRef, ...] | None = None
    if header.kind == ACTOR:
        parent = r.object_ref()
        components = tuple(r.object_ref() for _ in range(r.i32()))
    control = r.u8() if r.remaining() and r.data[r.pos] == 0 else None
    properties = read_property_list(r, control is not None)
    trailer = read_trailer(r, header.class_name, header.kind)
    return ObjectData(parent, components, control, properties, trailer)


def write_object_data(d: ObjectData, header: ObjectHeader) -> bytes:
    w = Writer()
    if header.kind == ACTOR:
        if d.parent is None or d.components is None:
            raise ArchiveError(f"actor {header.path} has no parent reference or component list")
        w.object_ref(d.parent)
        w.i32(len(d.components))
        for c in d.components:
            w.object_ref(c)
    if d.control is not None:
        w.u8(d.control)
    write_property_list(w, d.properties)
    write_trailer(w, d.trailer)
    return w.getvalue()
