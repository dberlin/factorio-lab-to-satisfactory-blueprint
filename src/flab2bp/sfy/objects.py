"""Object table (TOC) entries and the per-object data envelope.

TOC entry: int32 kind (1 actor, 0 component); FString class path; FString
level; FString instance path; int32 object flags (SaveVersion >=
SerializeObjectFlags); actors then int32 needTransform, FQuat (4 f32),
FVector translation (3 f32), FVector scale (3 f32), int32 placedInLevel;
components then FString parent actor path.

Object data: int32 size, then for actors FObjectReferenceDisc parent,
int32 component count, that many references; then the tagged property list
and a class-specific trailer, kept here as an opaque ``body``.

The object-flags field was probed on the fixture corpus: it is present at
SaveVersion 52, 58 and 60 and absent at SaveVersion 46, so the boundary
``SaveVersion >= SerializeObjectFlags`` (49) holds for every fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
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
    parent: ObjectRef | None
    components: tuple[ObjectRef, ...] | None
    body: bytes


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
    if header.kind == ACTOR:
        parent = r.object_ref()
        components = tuple(r.object_ref() for _ in range(r.i32()))
        return ObjectData(parent, components, raw[r.pos :])
    return ObjectData(None, None, raw)


def write_object_data(d: ObjectData, header: ObjectHeader) -> bytes:
    w = Writer()
    if header.kind == ACTOR:
        if d.parent is None or d.components is None:
            raise ArchiveError(f"actor {header.path} has no parent reference or component list")
        w.object_ref(d.parent)
        w.i32(len(d.components))
        for c in d.components:
            w.object_ref(c)
    w.raw(d.body)
    return w.getvalue()
