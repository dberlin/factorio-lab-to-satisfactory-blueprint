"""FBlueprintHeader, FSaveObjectVersionData and FBlueprintRecord.

Layouts come from FGFactoryBlueprintTypes.h and FGSaveSession.h and were
verified on the fixture corpus. The engine-version block is present only when
SaveVersion >= SerializeDataPackageVersionAndCustomVersions; fixtures with
SaveVersion 46 and 52 go straight from the recipe list to the chunk stream.

The .sbpcfg record does not store the blueprint name (the game fills it from
the file name). Whatever follows the icon library is carried as an opaque
``tail`` and written back verbatim, because what is in it depends on the config
version, and on the file: the corpus's five version-3 records end there with
nothing, its seven version-4 records carry 45 to 50 bytes, and 36 of its 37
version-6 records carry the five bytes (int32 6, uint8 0) of the serialized
FPlayerInfoHandle for LastEditedBy in the
NewPlayerInfoHandleSerializationFormat family -- logistics-23 carries two. This
project writes the five-byte version-6 shape (:meth:`BlueprintRecord.new`).
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
from flab2bp.sfy.versions import (
    BLUEPRINT_CONFIG_VERSION,
    BLUEPRINT_HEADER_VERSION,
    SaveCustomVersion,
)

__all__ = [
    "BlueprintHeader",
    "BlueprintRecord",
    "EngineVersion",
    "ItemAmount",
    "SaveObjectVersionData",
    "has_version_block",
    "read_header",
    "read_record",
    "write_header",
    "write_record",
]


@dataclass(frozen=True, slots=True)
class EngineVersion:
    major: int
    minor: int
    patch: int
    changelist: int
    branch: str


@dataclass(frozen=True, slots=True)
class SaveObjectVersionData:
    data_version: int
    ue4: int
    ue5: int
    licensee: int
    engine: EngineVersion
    custom_versions: tuple[tuple[bytes, int], ...]


@dataclass(frozen=True, slots=True)
class ItemAmount:
    item: ObjectRef
    amount: int


@dataclass(frozen=True, slots=True)
class BlueprintHeader:
    header_version: int
    save_version: int
    build_version: int
    dimensions: tuple[int, int, int]
    cost: tuple[ItemAmount, ...]
    recipes: tuple[ObjectRef, ...]
    version_data: SaveObjectVersionData | None


def has_version_block(save_version: int) -> bool:
    return save_version >= SaveCustomVersion.SerializeDataPackageVersionAndCustomVersions


def _read_version_data(r: Reader) -> SaveObjectVersionData:
    data_version = r.u32()
    ue4, ue5, licensee = r.i32(), r.i32(), r.i32()
    engine = EngineVersion(r.u16(), r.u16(), r.u16(), r.u32(), r.fstring())
    count = r.i32()
    customs = tuple((r.guid(), r.i32()) for _ in range(count))
    return SaveObjectVersionData(data_version, ue4, ue5, licensee, engine, customs)


def _write_version_data(w: Writer, v: SaveObjectVersionData) -> None:
    w.u32(v.data_version)
    w.i32(v.ue4)
    w.i32(v.ue5)
    w.i32(v.licensee)
    w.u16(v.engine.major)
    w.u16(v.engine.minor)
    w.u16(v.engine.patch)
    w.u32(v.engine.changelist)
    w.fstring(v.engine.branch)
    w.i32(len(v.custom_versions))
    for guid, version in v.custom_versions:
        w.guid(guid)
        w.i32(version)


def read_header(r: Reader) -> BlueprintHeader:
    header_version = r.i32()
    if header_version != BLUEPRINT_HEADER_VERSION:
        raise ArchiveError(f"unsupported blueprint header version {header_version}")
    save_version, build_version = r.i32(), r.i32()
    dims = (r.i32(), r.i32(), r.i32())
    cost = tuple(ItemAmount(r.object_ref(), r.i32()) for _ in range(r.i32()))
    recipes = tuple(r.object_ref() for _ in range(r.i32()))
    version_data = _read_version_data(r) if has_version_block(save_version) else None
    return BlueprintHeader(
        header_version, save_version, build_version, dims, cost, recipes, version_data
    )


def write_header(w: Writer, h: BlueprintHeader) -> None:
    w.i32(h.header_version)
    w.i32(h.save_version)
    w.i32(h.build_version)
    for d in h.dimensions:
        w.i32(d)
    w.i32(len(h.cost))
    for c in h.cost:
        w.object_ref(c.item)
        w.i32(c.amount)
    w.i32(len(h.recipes))
    for ref in h.recipes:
        w.object_ref(ref)
    if has_version_block(h.save_version):
        if h.version_data is None:
            raise ArchiveError("save version requires a version block")
        _write_version_data(w, h.version_data)


@dataclass(frozen=True, slots=True)
class BlueprintRecord:
    config_version: int
    description: str
    icon_id: int
    color: tuple[float, float, float, float]
    icon_library_path: str
    icon_library_name: str
    tail: bytes

    @classmethod
    def new(
        cls,
        description: str,
        icon_id: int = 282,
        color: tuple[float, float, float, float] = (0.397, 0.076, 0.076, 1.0),
    ) -> BlueprintRecord:
        return cls(
            BLUEPRINT_CONFIG_VERSION,
            description,
            icon_id,
            color,
            "/Game/FactoryGame/-Shared/Blueprint/IconLibrary",
            "IconLibrary",
            b"\x06\x00\x00\x00\x00",
        )


def read_record(data: bytes) -> BlueprintRecord:
    r = Reader(data)
    config_version = r.i32()
    description = r.fstring()
    icon_id = r.i32()
    color = (r.f32(), r.f32(), r.f32(), r.f32())
    path, name = r.fstring(), r.fstring()
    tail = r.bytes(r.remaining())
    return BlueprintRecord(config_version, description, icon_id, color, path, name, tail)


def write_record(rec: BlueprintRecord) -> bytes:
    w = Writer()
    w.i32(rec.config_version)
    w.fstring(rec.description)
    w.i32(rec.icon_id)
    for c in rec.color:
        w.f32(c)
    w.fstring(rec.icon_library_path)
    w.fstring(rec.icon_library_name)
    w.raw(rec.tail)
    return w.getvalue()
