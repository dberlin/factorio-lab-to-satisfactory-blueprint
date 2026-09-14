"""Unreal's tagged property list, the first half of every blueprint object body.

An object body is a sequence of tagged properties terminated by the FString
``"None"``, followed by a short class-specific trailer that this module does
not touch: :func:`read_property_list` stops on the terminator and leaves the
trailer unread.

Two tag formats
---------------
The corpus spans an engine change. ``UObject::Serialize`` writes a
``uint8 SerializationControl`` byte in front of the properties once
``FileVersionUE5`` reaches 1011, and at 1012 (UE 5.4) the property tag was
replaced by one carrying a full ``FPropertyTypeName`` tree. Both arrive
together: every object at save version 58 and 60 has the control byte and a
modern tag, every object at save version 46 and 52 has neither, so the package
treats one boundary, :data:`flab2bp.sfy.versions.MODERN_TAG_UE5_VERSION`, as
selecting both. Which one a file uses is decided from its header by
:func:`flab2bp.sfy.codec.tag_format` and threaded down; this module takes it as
the ``modern`` argument of :func:`read_property_list` and never infers it from
the bytes. Writing needs no such argument: each :class:`Tag` remembers its own
format.

Classic tag (save version 46, 52): ``FString name`` -- the FString ``"None"``
ends the list; ``FString type``; ``int32 size``; ``int32 index``; then per type
``StructProperty`` an ``FString struct_name`` and a 16-byte guid,
``ByteProperty``/``EnumProperty`` an ``FString enum_name``,
``ArrayProperty``/``SetProperty`` an ``FString inner_type``, ``MapProperty`` an
``FString key_type`` and an ``FString value_type``, ``BoolProperty`` a
``uint8`` holding its whole value; then ``uint8 has_guid`` and, when 1, a
16-byte property guid.

Modern tag (save version 58, 60): ``FString name``; an ``FPropertyTypeName``
tree, written pre-order as ``FString name`` plus ``int32`` child count per
node; ``int32 size``; ``uint8 flags`` (:data:`TAG_HAS_ARRAY_INDEX`,
:data:`TAG_HAS_PROPERTY_GUID`, :data:`TAG_NATIVE_SERIALIZE`,
:data:`TAG_BOOL_TRUE`); then ``int32 index`` and a 16-byte guid when their
flags say so. What the classic tag spells out -- struct name, enum name, inner
and key and value types -- are the tree's parameters here, so :class:`Tag`
carries both: the tree verbatim for byte identity and the flat fields for
callers. A ``BoolProperty``'s value is the :data:`TAG_BOOL_TRUE` flag.

``size`` is never stored on :class:`Tag`: it is recomputed from the value bytes
on write, so a property edited in memory still writes a correct size.

Value layouts
-------------
``IntProperty`` int32; ``Int8Property`` int8; ``Int64Property`` int64;
``UInt32Property`` uint32; ``FloatProperty`` f32; ``DoubleProperty`` f64;
``StrProperty``/``NameProperty``/``EnumProperty`` FString; ``ByteProperty``
FString when the tag names an enum and uint8 otherwise;
``ObjectProperty``/``InterfaceProperty`` two FStrings (level and path);
``SoftObjectProperty``, ``TextProperty``, ``MapProperty`` and ``SetProperty``
are kept as their raw ``size`` bytes.

``StructProperty`` is binary for the fixed-layout engine structs in
:data:`BINARY_STRUCTS`, member by member for the structs in
:data:`KNOWN_CUSTOM_STRUCTS` whose own ``Serialize`` the game supplies, and
otherwise a nested property list, which must consume exactly ``size`` bytes. A
modern tag flagged :data:`TAG_NATIVE_SERIALIZE` says the game wrote the struct
its own way, so it is binary unless the name is one this module knows. A struct
whose bytes do not parse, or a binary struct whose ``size`` matches no known
layout, degrades to :class:`BinaryStruct`, which still writes back byte for
byte. ``docs/sfy-struct-layouts.md`` is where the two custom layouts' evidence
is written out; ``data/struct_schemas.json`` is the game's own field list for
every struct the corpus carries, which ``tests/sfy/test_struct_schemas.py``
checks the tagged ones against.

``ArrayProperty`` is an ``int32`` count and then, for a ``StructProperty``
inner type under a classic tag, an inner tag (name, type, the total size of all
elements, index, struct name, 16-byte guid, ``uint8 has_guid``) followed by
that many struct values; a modern tag has no inner tag, because the element's
struct name is already a parameter of its type tree. For the scalar inner
types it is simply that many values. An array whose inner type or element
layout is not recognised degrades to :class:`Opaque` over the whole value.

Engine float widths differ by save version: a ``Vector`` is three f64 in the
UE5-era fixtures and three f32 in the oldest ones. The width is decided by the
tag's ``size`` and remembered on the value (``wide``) so the write is identical.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer

__all__ = [
    "BINARY_STRUCTS",
    "DEFAULT_ENUM_STORAGE",
    "DEFAULT_TYPE_PACKAGE",
    "INDEX_NONE",
    "KNOWN_CUSTOM_STRUCTS",
    "MAX_TYPE_NAME_DEPTH",
    "ONLINE_SERVICES_NULL",
    "PLAYER_INFO_HANDLE_SIZES",
    "TAG_BOOL_TRUE",
    "TAG_HAS_ARRAY_INDEX",
    "TAG_HAS_PROPERTY_GUID",
    "TAG_NATIVE_SERIALIZE",
    "TYPE_PACKAGES",
    "Array",
    "BinaryStruct",
    "Bool",
    "Box",
    "Byte",
    "Color",
    "Double",
    "Enum",
    "Float",
    "Guid",
    "Int",
    "Int8",
    "Int64",
    "IntVector",
    "InventoryItem",
    "LinearColor",
    "Map",
    "Name",
    "Object",
    "Opaque",
    "PlayerInfoHandle",
    "Property",
    "PropertyList",
    "Quat",
    "Rotator",
    "Set",
    "SoftObject",
    "Str",
    "Struct",
    "Tag",
    "Text",
    "TypeName",
    "UInt32",
    "Value",
    "Vector",
    "Vector2D",
    "check_tag_format",
    "modern_type_name",
    "read_property_list",
    "read_struct_value",
    "write_property_list",
    "write_struct_value",
]

BINARY_STRUCTS: frozenset[str] = frozenset(
    {
        "Vector",
        "Rotator",
        "Quat",
        "Vector2D",
        "IntVector",
        "LinearColor",
        "Color",
        "Box",
        "Guid",
    }
)

KNOWN_CUSTOM_STRUCTS: frozenset[str] = frozenset({"InventoryItem", "PlayerInfoHandle"})
"""Structs the *game* serialises itself, member by member, rather than as a tagged
list or a fixed engine layout. Each has a value class whose docstring cites the
code it was transcribed from -- a header for :class:`PlayerInfoHandle`, the
disassembled DLL for :class:`InventoryItem`. They are checked before
:data:`TAG_NATIVE_SERIALIZE`, which is the flag a modern tag sets to say exactly
this and which would otherwise send them straight to :class:`BinaryStruct`."""

INDEX_NONE = -1
"""Unreal's ``INDEX_NONE``."""

ONLINE_SERVICES_NULL = 0
"""``UE::Online::EOnlineServices::Null``.

The enum is the engine's, so it is in no header the game ships, but
``PlayerInfoCache.h`` pins the value twice over: ``FPlayerInfoHandle``'s
default-constructed ``ServiceProvider`` is ``0`` and is meant to be the invalid
handle (line 281), and the legacy branch treats ``ServiceProvider == Null`` as
exactly that (line 339). Every handle in the corpus says ``6``, which is
``Steam`` counting from ``Null = 0``."""

TAG_HAS_ARRAY_INDEX = 0x01
TAG_HAS_PROPERTY_GUID = 0x02
TAG_NATIVE_SERIALIZE = 0x08
TAG_BOOL_TRUE = 0x10


class Value:
    """A decoded property value that knows how to write its own bytes."""

    __slots__ = ()

    def write(self, w: Writer) -> None:
        raise NotImplementedError(f"{type(self).__name__} cannot write itself")


@dataclass(frozen=True, slots=True)
class TypeName:
    """One node of a modern tag's ``FPropertyTypeName`` tree."""

    name: str
    params: tuple[TypeName, ...] = ()


@dataclass(frozen=True, slots=True)
class Tag:
    """One property's header, in whichever tag format its file uses.

    The flat fields are the classic tag's, and a modern tag fills the same ones
    from its type tree so callers need not care which format they hold.
    ``type_package`` and ``enum_storage`` are the two things the tree says that
    the classic tag never did -- the package a struct or enum name lives in, and
    the integer type behind an enum -- and they exist so that
    :func:`modern_type_name` can rebuild the tree from the flat fields alone.
    ``type_name is None`` means a classic tag.
    """

    name: str
    type: str
    index: int
    struct_name: str | None = None
    struct_guid: bytes | None = None
    enum_name: str | None = None
    inner_type: str | None = None
    key_type: str | None = None
    value_type: str | None = None
    guid: bytes | None = None
    type_name: TypeName | None = None
    flags: int | None = None
    type_package: str | None = None
    enum_storage: str | None = None

    def as_modern(self, flags: int = 0) -> Tag:
        """This tag in the modern format: its type tree and the fields it implies.

        Authoring for a save-version-58-or-later file is then
        ``Tag("mCustomizationData", "StructProperty", 0,
        struct_name="FactoryCustomizationData").as_modern()``. ``flags`` is the
        caller's because :data:`TAG_NATIVE_SERIALIZE` depends on the value, not
        on the tag; the index and guid bits are added by the writer. A struct
        guid is dropped, because a modern tag has nowhere to keep one.
        """
        node = modern_type_name(self)
        flat = _flatten(node)
        return replace(
            self,
            struct_guid=None,
            type_name=node,
            flags=flags,
            type_package=flat.type_package,
            enum_storage=flat.enum_storage,
        )


@dataclass(frozen=True, slots=True)
class Property:
    tag: Tag
    value: Value


PropertyList = tuple[Property, ...]


@dataclass(frozen=True, slots=True)
class Opaque(Value):
    """Bytes whose layout this module does not decode; written back verbatim."""

    raw: bytes

    def write(self, w: Writer) -> None:
        w.raw(self.raw)


@dataclass(frozen=True, slots=True)
class Int(Value):
    v: int

    def write(self, w: Writer) -> None:
        w.i32(self.v)


@dataclass(frozen=True, slots=True)
class Int8(Value):
    v: int

    def write(self, w: Writer) -> None:
        w.i8(self.v)


@dataclass(frozen=True, slots=True)
class Int64(Value):
    v: int

    def write(self, w: Writer) -> None:
        w.i64(self.v)


@dataclass(frozen=True, slots=True)
class UInt32(Value):
    v: int

    def write(self, w: Writer) -> None:
        w.u32(self.v)


@dataclass(frozen=True, slots=True)
class Float(Value):
    v: float

    def write(self, w: Writer) -> None:
        w.f32(self.v)


@dataclass(frozen=True, slots=True)
class Double(Value):
    v: float

    def write(self, w: Writer) -> None:
        w.f64(self.v)


@dataclass(frozen=True, slots=True)
class Bool(Value):
    """Lives entirely in the tag: the value occupies zero bytes of its own."""

    v: bool

    def write(self, w: Writer) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Str(Value):
    v: str

    def write(self, w: Writer) -> None:
        w.fstring(self.v)


@dataclass(frozen=True, slots=True)
class Name(Value):
    v: str

    def write(self, w: Writer) -> None:
        w.fstring(self.v)


@dataclass(frozen=True, slots=True)
class Enum(Value):
    v: str

    def write(self, w: Writer) -> None:
        w.fstring(self.v)


@dataclass(frozen=True, slots=True)
class Byte(Value):
    """A named enum member (FString) or, when ``enum_name`` is absent, a uint8."""

    enum_name: str | None
    v: int | str

    def write(self, w: Writer) -> None:
        if self.enum_name in (None, "None"):
            if not isinstance(self.v, int):
                raise ArchiveError(f"raw ByteProperty value is {type(self.v).__name__}")
            w.u8(self.v)
        else:
            if not isinstance(self.v, str):
                raise ArchiveError(f"enum ByteProperty value is {type(self.v).__name__}")
            w.fstring(self.v)


@dataclass(frozen=True, slots=True)
class Object(Value):
    ref: ObjectRef

    def write(self, w: Writer) -> None:
        w.object_ref(self.ref)


@dataclass(frozen=True, slots=True)
class SoftObject(Value):
    raw: bytes

    def write(self, w: Writer) -> None:
        w.raw(self.raw)


@dataclass(frozen=True, slots=True)
class Text(Value):
    raw: bytes

    def write(self, w: Writer) -> None:
        w.raw(self.raw)


@dataclass(frozen=True, slots=True)
class Map(Value):
    raw: bytes

    def write(self, w: Writer) -> None:
        w.raw(self.raw)


@dataclass(frozen=True, slots=True)
class Set(Value):
    raw: bytes

    def write(self, w: Writer) -> None:
        w.raw(self.raw)


def _write_floats(w: Writer, wide: bool, values: tuple[float, ...]) -> None:
    put = w.f64 if wide else w.f32
    for v in values:
        put(v)


@dataclass(frozen=True, slots=True)
class Vector(Value):
    x: float
    y: float
    z: float
    wide: bool = True

    def write(self, w: Writer) -> None:
        _write_floats(w, self.wide, (self.x, self.y, self.z))


@dataclass(frozen=True, slots=True)
class Rotator(Value):
    pitch: float
    yaw: float
    roll: float
    wide: bool = True

    def write(self, w: Writer) -> None:
        _write_floats(w, self.wide, (self.pitch, self.yaw, self.roll))


@dataclass(frozen=True, slots=True)
class Quat(Value):
    x: float
    y: float
    z: float
    w: float
    wide: bool = True

    def write(self, w: Writer) -> None:
        _write_floats(w, self.wide, (self.x, self.y, self.z, self.w))


@dataclass(frozen=True, slots=True)
class Vector2D(Value):
    x: float
    y: float
    wide: bool = True

    def write(self, w: Writer) -> None:
        _write_floats(w, self.wide, (self.x, self.y))


@dataclass(frozen=True, slots=True)
class IntVector(Value):
    x: int
    y: int
    z: int

    def write(self, w: Writer) -> None:
        w.i32(self.x)
        w.i32(self.y)
        w.i32(self.z)


@dataclass(frozen=True, slots=True)
class LinearColor(Value):
    r: float
    g: float
    b: float
    a: float

    def write(self, w: Writer) -> None:
        for v in (self.r, self.g, self.b, self.a):
            w.f32(v)


@dataclass(frozen=True, slots=True)
class Color(Value):
    """Stored in the engine's BGRA order."""

    b: int
    g: int
    r: int
    a: int

    def write(self, w: Writer) -> None:
        for v in (self.b, self.g, self.r, self.a):
            w.u8(v)


@dataclass(frozen=True, slots=True)
class Box(Value):
    min: Vector
    max: Vector
    valid: int

    def write(self, w: Writer) -> None:
        self.min.write(w)
        self.max.write(w)
        w.u8(self.valid)


@dataclass(frozen=True, slots=True)
class Guid(Value):
    raw: bytes

    def write(self, w: Writer) -> None:
        w.guid(self.raw)


@dataclass(frozen=True, slots=True)
class PlayerInfoHandle(Value):
    """Who built a buildable: ``FPlayerInfoHandle``, in one of its two shapes.

    Transcribed from ``FPlayerInfoHandle::operator<<``, which is inline in
    ``Source/FactoryGame/Public/Online/PlayerInfoCache.h`` lines 313-346 (the
    headers that ship in ``CommunityResources/Headers.zip``). The struct's two
    members are private and carry no ``UPROPERTY``, so the usmap schema for
    ``PlayerInfoHandle`` has no fields at all and this serializer is the only
    description of the bytes::

        uint8 ServiceProvider;
        int32 PlayerInfoTableIndex;

    and the archive writes them one of two ways, gated on the file's save custom
    version (``ar.CustomVer( FSaveCustomVersion::GUID )``):

    * at :attr:`~flab2bp.sfy.versions.SaveCustomVersion.NewPlayerInfoHandleSerializationFormat`
      (57) or later, header line 326: ``ServiceProvider`` then the full
      ``int32`` index -- five bytes, :attr:`wide_index` true;
    * before it, header lines 333-336: ``ServiceProvider`` then a
      ``uint8 LegacyPlayerInfoTableIndex`` -- two bytes.

    The reader is handed the tag's ``size``, not a version, so the size selects
    the shape; ``tests/sfy/test_properties.py`` ties the two together over the
    corpus, where every pre-57 fixture carries the 2-byte shape and every later
    one the 5-byte shape.

    :attr:`table_index` is the index exactly as the wire carries it, which is
    what makes the write byte-identical. The game does not always load it as
    that: on the legacy path a handle whose provider is ``Null`` (0) is an
    invalid handle, and its index becomes ``INDEX_NONE`` (header lines 338-342).
    :attr:`player_info_table_index` is that loaded value.

    One shape is deliberately not decoded here. Exactly at version 57 both
    branches of ``operator<<`` run -- that is the mis-serialisation
    :attr:`~flab2bp.sfy.versions.SaveCustomVersion.FixNewPlayerInfoHandleSerializationFormat`
    (58) was added to fix -- and the handle is written twice, ten bytes. No
    fixture has one; such a value keeps its bytes as a :class:`BinaryStruct`.
    """

    service_provider: int
    table_index: int
    wide_index: bool = True

    @property
    def player_info_table_index(self) -> int:
        """The index the game loads, with the legacy ``Null``-provider rule applied."""
        if not self.wide_index and self.service_provider == ONLINE_SERVICES_NULL:
            return INDEX_NONE
        return self.table_index

    @property
    def is_valid(self) -> bool:
        """``FPlayerInfoHandle::IsValid``: an index other than ``INDEX_NONE``."""
        return self.player_info_table_index != INDEX_NONE

    def write(self, w: Writer) -> None:
        w.u8(self.service_provider)
        if self.wide_index:
            w.i32(self.table_index)
        else:
            w.u8(self.table_index)


@dataclass(frozen=True, slots=True)
class InventoryItem(Value):
    """One item in an inventory slot: ``FInventoryItem``, with its optional state.

    ``FGInventoryComponent.h`` lines 23-74 declare the struct and give the
    members their names; the wire layout is ``FInventoryItem::Serialize``, which
    lives in the shipped DLL and is read with ``tools/sfy-native``'s ``disasm``
    mode (``FactoryGameEGS-FactoryGame-Win64-Shipping``, RVA ``0x894c50``, 166
    bytes from ``.pdata``, 44 instructions). ``docs/sfy-struct-layouts.md`` has
    the annotated listing; the instructions that decide each field are:

    * ``0x894c76 lea rdx,[0F17818h]`` / ``0x894c90 call [0EE6E38h]`` --
      ``ar.CustomVer(FSaveCustomVersion::GUID)``. The constant at ``0xf17818``
      is the 16 bytes ``2f3e0421 d61fe613 519d3b51 30a23636``, which is
      :data:`~flab2bp.sfy.versions.SAVE_CUSTOM_VERSION_GUID` exactly.
    * ``0x894c96 cmp eax,2`` / ``jl`` -- below save custom version 2 the struct
      writes nothing at all.
    * ``0x894c9e lea rdx,[rdi+8]`` (annotated ``FInventoryItem::ItemClass``) /
      ``0x894ca5 call [rax+148h]`` -- the archive's ``UObject*&`` operator, which
      in a save archive is an ``FObjectReferenceDisc``: a level ``FString`` and a
      path ``FString``. This is :attr:`item_class`.
    * ``0x894cbb cmp eax,2Bh`` / ``jl`` -- 0x2B is 43,
      :attr:`~flab2bp.sfy.versions.SaveCustomVersion.RefactoredInventoryItemState`.
      At 43 or later (``0x894cc0 lea rcx,[rdi+10h]``, annotated
      ``FInventoryItem::ItemState``, then ``0x894cc7 call
      FFGDynamicStruct::Serialize``) the item state is an ``FFGDynamicStruct``;
      below it (``0x894cdc lea rdx,[rdi+20h]``) the legacy
      ``LegacyItemStateActor`` object reference is serialised instead.
    * ``FFGDynamicStruct::Serialize`` (RVA ``0x7e7470``) writes, on its saving
      path at ``0x7e7709``, a four-byte flag -- ``setne`` on
      ``ScriptStruct != nullptr``, then ``FArchive::Serialize`` with
      ``r8d = 4`` at ``0x7e7750`` -- and only then, when the flag is set, the
      struct's own type and bytes. That flag is :attr:`has_state` and those
      bytes are :attr:`state`.

    So the corpus's 12 zero bytes are an empty level, an empty path and a zero
    state flag, and an item that names a descriptor is the same three fields with
    the path filled in. The pre-43 legacy shape is not decoded: no fixture has
    one (the oldest is save version 46), and a value that does not fit this
    layout keeps its bytes as a :class:`BinaryStruct`.

    :attr:`state` is kept as bytes rather than decoded: an ``FFGDynamicStruct``
    body is a ``UScriptStruct`` reference followed by that struct's own
    serialisation, and no fixture carries one to check a decoder against.
    """

    item_class: ObjectRef
    has_state: bool = False
    state: bytes = b""

    def write(self, w: Writer) -> None:
        w.object_ref(self.item_class)
        w.i32(1 if self.has_state else 0)
        w.raw(self.state)


@dataclass(frozen=True, slots=True)
class Struct(Value):
    """A struct serialised as a nested, ``None``-terminated property list."""

    name: str
    fields: PropertyList

    def write(self, w: Writer) -> None:
        _write_fields(w, self.fields)


@dataclass(frozen=True, slots=True)
class BinaryStruct(Value):
    """A struct with custom binary content, kept as the tag's ``size`` bytes."""

    name: str
    raw: bytes

    def write(self, w: Writer) -> None:
        w.raw(self.raw)


@dataclass(frozen=True, slots=True)
class Array(Value):
    inner_type: str
    inner_tag: Tag | None
    items: tuple[Value, ...]

    def write(self, w: Writer) -> None:
        w.i32(len(self.items))
        if self.inner_tag is None:
            for item in self.items:
                item.write(w)
            return
        body = Writer()
        for item in self.items:
            item.write(body)
        _write_inner_tag(w, self.inner_tag, len(body))
        w.raw(body.getvalue())


@dataclass(frozen=True, slots=True)
class _TagRead:
    """A tag plus the two on-disk fields that :class:`Tag` deliberately drops."""

    tag: Tag
    size: int
    bool_value: int | None


MAX_TYPE_NAME_DEPTH = 32
"""Nesting a type tree may reach. The deepest in the corpus is 4; a file that
claims more is malformed, and the cap keeps a bad byte from recursing forever."""

TYPE_PACKAGES: dict[str, str] = {
    "Box": "/Script/CoreUObject",
    "Color": "/Script/CoreUObject",
    "Guid": "/Script/CoreUObject",
    "IntVector": "/Script/CoreUObject",
    "LinearColor": "/Script/CoreUObject",
    "Quat": "/Script/CoreUObject",
    "Rotator": "/Script/CoreUObject",
    "Transform": "/Script/CoreUObject",
    "Vector": "/Script/CoreUObject",
    "Vector2D": "/Script/CoreUObject",
    "SplinePointData": "/Script/Engine",
    "TopLevelAssetPath": "/Script/CoreUObject",
}
"""Packages for the engine type names the fixtures use. Everything else the game
serialises comes from ``/Script/FactoryGame``, which :func:`modern_type_name`
assumes when a tag carries no ``type_package`` of its own."""

DEFAULT_TYPE_PACKAGE = "/Script/FactoryGame"
DEFAULT_ENUM_STORAGE = "ByteProperty"


def _read_type_name(r: Reader, depth: int = 0) -> TypeName:
    if depth > MAX_TYPE_NAME_DEPTH:
        raise ArchiveError(f"type name nested deeper than {MAX_TYPE_NAME_DEPTH} at {r.pos}")
    name = r.fstring()
    count = r.i32()
    if count < 0:
        raise ArchiveError(f"type name {name!r} declares {count} parameters")
    return TypeName(name, tuple(_read_type_name(r, depth + 1) for _ in range(count)))


def _write_type_name(w: Writer, node: TypeName) -> None:
    w.fstring(node.name)
    w.i32(len(node.params))
    for p in node.params:
        _write_type_name(w, p)


@dataclass(frozen=True, slots=True)
class _Flat:
    """A type tree spelled as the classic tag's named fields, and back again."""

    struct_name: str | None = None
    enum_name: str | None = None
    inner_type: str | None = None
    key_type: str | None = None
    value_type: str | None = None
    type_package: str | None = None
    enum_storage: str | None = None


def _package_of(node: TypeName) -> str | None:
    """A struct or enum name node's one child is the package it lives in."""
    return node.params[0].name if node.params else None


def _flatten(node: TypeName) -> _Flat:
    """Read a type tree into flat fields; :func:`modern_type_name` is the inverse."""
    params = node.params
    if node.name == "StructProperty" and params:
        return _Flat(struct_name=params[0].name, type_package=_package_of(params[0]))
    if node.name in ("ByteProperty", "EnumProperty") and params:
        return _Flat(
            enum_name=params[0].name,
            type_package=_package_of(params[0]),
            enum_storage=params[1].name if len(params) > 1 else None,
        )
    if node.name in ("ArrayProperty", "SetProperty") and params:
        return replace(_flatten(params[0]), inner_type=params[0].name)
    if node.name == "MapProperty" and len(params) >= 2:
        return _Flat(key_type=params[0].name, value_type=params[1].name)
    return _Flat()


def _named_node(name: str, package: str | None) -> TypeName:
    return TypeName(name, (TypeName(package or TYPE_PACKAGES.get(name, DEFAULT_TYPE_PACKAGE)),))


def _type_node(typ: str, tag: Tag) -> TypeName:
    if typ == "StructProperty" and tag.struct_name:
        return TypeName(typ, (_named_node(tag.struct_name, tag.type_package),))
    enum_name = tag.enum_name
    if typ in ("ByteProperty", "EnumProperty") and enum_name is not None and enum_name != "None":
        storage = TypeName(tag.enum_storage or DEFAULT_ENUM_STORAGE)
        return TypeName(typ, (_named_node(enum_name, tag.type_package), storage))
    if typ in ("ArrayProperty", "SetProperty") and tag.inner_type:
        return TypeName(typ, (_type_node(tag.inner_type, tag),))
    if typ == "MapProperty" and tag.key_type and tag.value_type:
        return TypeName(typ, (TypeName(tag.key_type), TypeName(tag.value_type)))
    return TypeName(typ)


def modern_type_name(tag: Tag) -> TypeName:
    """Build the modern tag's ``FPropertyTypeName`` tree from a tag's flat fields.

    This is the inverse of the flattening a modern tag goes through when it is
    read, so a tag authored from scratch -- ``Tag("mCustomizationData",
    "StructProperty", 0, struct_name="FactoryCustomizationData")`` -- gets the
    tree the game writes without anyone spelling one out. A struct or enum name
    takes its package from the tag's ``type_package``, else from
    :data:`TYPE_PACKAGES`, else :data:`DEFAULT_TYPE_PACKAGE`.

    The ``flags`` byte is deliberately not derived here: ``TAG_NATIVE_SERIALIZE``
    says how the game serialises a struct, which no flat field records, so an
    author sets it alongside the value they chose.
    """
    return _type_node(tag.type, tag)


def _read_modern_tag(r: Reader, name: str) -> _TagRead:
    node = _read_type_name(r)
    size = r.i32()
    flags = r.u8()
    index = r.i32() if flags & TAG_HAS_ARRAY_INDEX else 0
    guid = r.guid() if flags & TAG_HAS_PROPERTY_GUID else None
    bool_value = (1 if flags & TAG_BOOL_TRUE else 0) if node.name == "BoolProperty" else None
    flat = _flatten(node)
    tag = Tag(
        name,
        node.name,
        index,
        struct_name=flat.struct_name,
        enum_name=flat.enum_name,
        inner_type=flat.inner_type,
        key_type=flat.key_type,
        value_type=flat.value_type,
        guid=guid,
        type_name=node,
        flags=flags,
        type_package=flat.type_package,
        enum_storage=flat.enum_storage,
    )
    return _TagRead(tag, size, bool_value)


def _read_classic_tag(r: Reader, name: str) -> _TagRead:
    typ = r.fstring()
    size, index = r.i32(), r.i32()
    struct_name: str | None = None
    struct_guid: bytes | None = None
    enum_name: str | None = None
    inner_type: str | None = None
    key_type: str | None = None
    value_type: str | None = None
    if typ == "StructProperty":
        struct_name = r.fstring()
        struct_guid = r.guid()
    elif typ in ("ByteProperty", "EnumProperty"):
        enum_name = r.fstring()
    elif typ in ("ArrayProperty", "SetProperty"):
        inner_type = r.fstring()
    elif typ == "MapProperty":
        key_type = r.fstring()
        value_type = r.fstring()
    bool_value = r.u8() if typ == "BoolProperty" else None
    guid = r.guid() if r.u8() else None
    tag = Tag(
        name,
        typ,
        index,
        struct_name=struct_name,
        struct_guid=struct_guid,
        enum_name=enum_name,
        inner_type=inner_type,
        key_type=key_type,
        value_type=value_type,
        guid=guid,
    )
    return _TagRead(tag, size, bool_value)


def read_tag(r: Reader, modern: bool = False) -> _TagRead | None:
    """Read one property tag, or return ``None`` on the list terminator."""
    name = r.fstring()
    if name == "None":
        return None
    return _read_modern_tag(r, name) if modern else _read_classic_tag(r, name)


def write_tag(w: Writer, tag: Tag, size: int, bool_value: int | None = None) -> None:
    """Write one property tag with a freshly computed value ``size``."""
    w.fstring(tag.name)
    if tag.type_name is not None:
        _write_modern_tag(w, tag, size, bool_value)
        return
    w.fstring(tag.type)
    w.i32(size)
    w.i32(tag.index)
    if tag.type == "StructProperty":
        w.fstring(tag.struct_name or "")
        w.guid(tag.struct_guid if tag.struct_guid is not None else bytes(16))
    elif tag.type in ("ByteProperty", "EnumProperty"):
        w.fstring(tag.enum_name if tag.enum_name is not None else "None")
    elif tag.type in ("ArrayProperty", "SetProperty"):
        w.fstring(tag.inner_type or "")
    elif tag.type == "MapProperty":
        w.fstring(tag.key_type or "")
        w.fstring(tag.value_type or "")
    if tag.type == "BoolProperty":
        w.u8(1 if bool_value else 0)
    if tag.guid is None:
        w.u8(0)
    else:
        w.u8(1)
        w.guid(tag.guid)


def _write_modern_tag(w: Writer, tag: Tag, size: int, bool_value: int | None) -> None:
    if tag.type_name is None:
        raise ArchiveError(f"{tag.name}: modern tag without a type name")
    _write_type_name(w, tag.type_name)
    w.i32(size)
    flags = tag.flags or 0
    if tag.type == "BoolProperty":
        flags = flags | TAG_BOOL_TRUE if bool_value else flags & ~TAG_BOOL_TRUE
    if tag.index:
        flags |= TAG_HAS_ARRAY_INDEX
    if tag.guid is not None:
        flags |= TAG_HAS_PROPERTY_GUID
    w.u8(flags)
    if flags & TAG_HAS_ARRAY_INDEX:
        w.i32(tag.index)
    if flags & TAG_HAS_PROPERTY_GUID:
        w.guid(tag.guid if tag.guid is not None else bytes(16))


def _read_inner_tag(r: Reader) -> Tag:
    """Read a classic array's inner ``StructProperty`` tag.

    Its ``size`` is the total size of all elements and is recomputed on write.
    """
    name = r.fstring()
    typ = r.fstring()
    r.i32()
    index = r.i32()
    struct_name = r.fstring()
    struct_guid = r.guid()
    guid = r.guid() if r.u8() else None
    return Tag(name, typ, index, struct_name=struct_name, struct_guid=struct_guid, guid=guid)


def _write_inner_tag(w: Writer, tag: Tag, size: int) -> None:
    w.fstring(tag.name)
    w.fstring(tag.type)
    w.i32(size)
    w.i32(tag.index)
    w.fstring(tag.struct_name or "")
    w.guid(tag.struct_guid if tag.struct_guid is not None else bytes(16))
    if tag.guid is None:
        w.u8(0)
    else:
        w.u8(1)
        w.guid(tag.guid)


def _read_binary_struct(r: Reader, name: str, size: int) -> Value | None:
    """Decode a fixed-layout engine struct, or return ``None`` for an unknown size."""
    if name == "Vector":
        if size == 24:
            return Vector(r.f64(), r.f64(), r.f64())
        if size == 12:
            return Vector(r.f32(), r.f32(), r.f32(), wide=False)
    elif name == "Rotator":
        if size == 24:
            return Rotator(r.f64(), r.f64(), r.f64())
        if size == 12:
            return Rotator(r.f32(), r.f32(), r.f32(), wide=False)
    elif name == "Quat":
        if size == 32:
            return Quat(r.f64(), r.f64(), r.f64(), r.f64())
        if size == 16:
            return Quat(r.f32(), r.f32(), r.f32(), r.f32(), wide=False)
    elif name == "Vector2D":
        if size == 16:
            return Vector2D(r.f64(), r.f64())
        if size == 8:
            return Vector2D(r.f32(), r.f32(), wide=False)
    elif name == "IntVector":
        if size == 12:
            return IntVector(r.i32(), r.i32(), r.i32())
    elif name == "LinearColor":
        if size == 16:
            return LinearColor(r.f32(), r.f32(), r.f32(), r.f32())
    elif name == "Color":
        if size == 4:
            return Color(r.u8(), r.u8(), r.u8(), r.u8())
    elif name == "Box":
        if size == 49:
            return Box(Vector(r.f64(), r.f64(), r.f64()), Vector(r.f64(), r.f64(), r.f64()), r.u8())
        if size == 25:
            lo = Vector(r.f32(), r.f32(), r.f32(), wide=False)
            hi = Vector(r.f32(), r.f32(), r.f32(), wide=False)
            return Box(lo, hi, r.u8())
    elif name == "Guid" and size == 16:
        return Guid(r.guid())
    return None


PLAYER_INFO_HANDLE_SIZES = {2: False, 5: True}
"""The two ``FPlayerInfoHandle`` wire sizes, and whether the index is a full
``int32``. See :class:`PlayerInfoHandle` for the branches these come from."""


def _read_player_info_handle(raw: bytes) -> Value | None:
    wide = PLAYER_INFO_HANDLE_SIZES.get(len(raw))
    if wide is None:
        return None
    r = Reader(raw)
    return PlayerInfoHandle(r.u8(), r.i32() if wide else r.u8(), wide_index=wide)


def _read_inventory_item(raw: bytes) -> Value | None:
    r = Reader(raw)
    ref = r.object_ref()
    flag = r.i32()
    if flag not in (0, 1):
        return None
    state = raw[r.pos :]
    if state and not flag:
        return None
    return InventoryItem(ref, bool(flag), state)


def _read_custom_struct(name: str, raw: bytes) -> Value | None:
    """Decode a struct the game serialises itself, or ``None`` if the bytes do not fit."""
    try:
        if name == "PlayerInfoHandle":
            return _read_player_info_handle(raw)
        if name == "InventoryItem":
            return _read_inventory_item(raw)
    except ArchiveError:
        return None
    return None


def read_struct_value(
    r: Reader, struct_name: str | None, size: int, modern: bool = False, native: bool = False
) -> Value:
    """Read a struct value of exactly ``size`` bytes, degrading to a binary blob.

    ``modern`` is the file's tag format, for the nested property list a tagged
    struct turns out to be; ``native`` is :data:`TAG_NATIVE_SERIALIZE` off the
    tag, which says the game wrote the struct its own way. Neither matters to
    the fixed engine layouts or to :data:`KNOWN_CUSTOM_STRUCTS`, which are
    decided by name and size alone.
    """
    return _read_struct_value(r, struct_name, size, modern, native)


def _read_struct_value(
    r: Reader, struct_name: str | None, size: int, modern: bool, native: bool
) -> Value:
    name = struct_name or ""
    start = r.pos
    if name in KNOWN_CUSTOM_STRUCTS:
        raw = r.bytes(size)
        value = _read_custom_struct(name, raw)
        return value if value is not None else BinaryStruct(name, raw)
    if name in BINARY_STRUCTS:
        try:
            value = _read_binary_struct(r, name, size)
        except ArchiveError:
            value = None
        if value is not None and r.pos - start == size:
            return value
        r.pos = start
        return BinaryStruct(name, r.bytes(size))
    raw = r.bytes(size)
    if native:
        return BinaryStruct(name, raw)
    sub = Reader(raw)
    try:
        fields = _read_fields(sub, modern)
    except ArchiveError:
        return BinaryStruct(name, raw)
    if sub.remaining():
        return BinaryStruct(name, raw)
    return Struct(name, fields)


def write_struct_value(w: Writer, value: Value) -> None:
    """Write a struct value's bytes; the caller owns the tag and its size."""
    value.write(w)


_ARRAY_SCALARS: dict[str, Callable[[Reader], Value]] = {
    "ObjectProperty": lambda r: Object(r.object_ref()),
    "IntProperty": lambda r: Int(r.i32()),
    "EnumProperty": lambda r: Enum(r.fstring()),
    "StrProperty": lambda r: Str(r.fstring()),
    "NameProperty": lambda r: Name(r.fstring()),
    "ByteProperty": lambda r: Byte(None, r.u8()),
    "FloatProperty": lambda r: Float(r.f32()),
}


def _read_struct_array(r: Reader, tag: Tag, end: int, count: int, modern: bool) -> Value | None:
    """Read ``count`` struct elements; a classic array leads with an inner tag."""
    inner_tag = None if modern else _read_inner_tag(r)
    name = (inner_tag.struct_name if inner_tag is not None else tag.struct_name) or ""
    items: list[Value] = []
    if count and name in BINARY_STRUCTS:
        region = end - r.pos
        if region < 0 or region % count:
            return None
        elem = region // count
        items = [_read_struct_value(r, name, elem, modern, native=True) for _ in range(count)]
    elif count:
        items = [Struct(name, _read_fields(r, modern)) for _ in range(count)]
    return Array("StructProperty", inner_tag, tuple(items))


def _read_array_body(r: Reader, tag: Tag, end: int, modern: bool) -> Value | None:
    """Decode an array value, or return ``None`` for a layout we do not know."""
    count = r.i32()
    if count < 0:
        return None
    inner = tag.inner_type or ""
    if inner == "StructProperty":
        return _read_struct_array(r, tag, end, count, modern)
    read_one = _ARRAY_SCALARS.get(inner)
    if read_one is None:
        return None
    return Array(inner, None, tuple(read_one(r) for _ in range(count)))


def _read_array(r: Reader, tag: Tag, size: int, modern: bool) -> Value:
    start = r.pos
    try:
        value = _read_array_body(r, tag, start + size, modern)
    except ArchiveError:
        value = None
    if value is None or r.pos != start + size:
        r.pos = start
        return Opaque(r.bytes(size))
    return value


def _read_value(r: Reader, tag: Tag, size: int, bool_value: int | None, modern: bool) -> Value:
    typ = tag.type
    if typ == "BoolProperty":
        return Bool(bool(bool_value))
    if typ == "IntProperty":
        return Int(r.i32())
    if typ == "Int8Property":
        return Int8(r.i8())
    if typ == "Int64Property":
        return Int64(r.i64())
    if typ == "UInt32Property":
        return UInt32(r.u32())
    if typ == "FloatProperty":
        return Float(r.f32())
    if typ == "DoubleProperty":
        return Double(r.f64())
    if typ == "StrProperty":
        return Str(r.fstring())
    if typ == "NameProperty":
        return Name(r.fstring())
    if typ == "EnumProperty":
        return Enum(r.fstring())
    if typ == "ByteProperty":
        if tag.enum_name in (None, "None"):
            return Byte(tag.enum_name, r.u8())
        return Byte(tag.enum_name, r.fstring())
    if typ in ("ObjectProperty", "InterfaceProperty"):
        return Object(r.object_ref())
    if typ == "SoftObjectProperty":
        return SoftObject(r.bytes(size))
    if typ == "TextProperty":
        return Text(r.bytes(size))
    if typ == "MapProperty":
        return Map(r.bytes(size))
    if typ == "SetProperty":
        return Set(r.bytes(size))
    if typ == "StructProperty":
        native = bool((tag.flags or 0) & TAG_NATIVE_SERIALIZE)
        return _read_struct_value(r, tag.struct_name, size, modern, native)
    if typ == "ArrayProperty":
        return _read_array(r, tag, size, modern)
    return Opaque(r.bytes(size))


def read_property_list(r: Reader, modern: bool) -> PropertyList:
    """Read one property list, through the ``None`` terminator.

    ``modern`` selects the tag format: the caller knows it, because the
    ``SerializationControl`` byte that :mod:`flab2bp.sfy.objects` reads off the
    front of an object body arrives with the same engine version.
    """
    return _read_fields(r, modern)


def write_property_list(w: Writer, props: PropertyList) -> None:
    """Write a property list, followed by the ``None`` terminator.

    The tag format is not a parameter here: each :class:`Tag` carries its own
    (``type_name is None`` means the classic one), so a list reads and writes
    back in the format it arrived in. Whether that is the format the *file*
    wants is :func:`check_tag_format`'s question, which
    :func:`flab2bp.sfy.objects.write_object_data` asks before it gets here.
    """
    _write_fields(w, props)


def check_tag_format(props: PropertyList, modern: bool, prefix: str = "") -> None:
    """Raise :class:`ArchiveError` if any tag is in the other file's tag format.

    Each tag writes itself in the shape it carries, so a tag built by hand for
    the wrong format would be emitted without complaint and quietly corrupt the
    file. The caller knows which format the file uses; this walks the whole
    tree -- nested structs, array elements, a classic array's inner tag -- and
    names the first property that disagrees.
    """
    want = "modern" if modern else "classic"
    for p in props:
        where = f"{prefix}{p.tag.name}"
        if (p.tag.type_name is not None) != modern:
            got = "modern" if p.tag.type_name is not None else "classic"
            raise ArchiveError(f"{where}: {got} tag in a {want} file")
        _check_value_format(p.value, modern, where)


def _check_value_format(value: Value, modern: bool, where: str) -> None:
    if isinstance(value, Struct):
        check_tag_format(value.fields, modern, f"{where}.")
    elif isinstance(value, Array):
        if value.inner_type == "StructProperty" and (value.inner_tag is not None) is modern:
            had = "carries" if modern else "lacks"
            want = "modern" if modern else "classic"
            raise ArchiveError(f"{where}: struct array {had} an inner tag in a {want} file")
        for item in value.items:
            _check_value_format(item, modern, where)


def _read_fields(r: Reader, modern: bool) -> PropertyList:
    """Read properties up to and including the ``None`` terminator."""
    out: list[Property] = []
    while True:
        read = read_tag(r, modern)
        if read is None:
            return tuple(out)
        start = r.pos
        value = _read_value(r, read.tag, read.size, read.bool_value, modern)
        if read.tag.type != "BoolProperty" and r.pos - start != read.size:
            raise ArchiveError(f"{read.tag.name}: consumed {r.pos - start} of {read.size}")
        out.append(Property(read.tag, value))


def _write_fields(w: Writer, props: PropertyList) -> None:
    for p in props:
        if p.tag.type == "BoolProperty":
            if not isinstance(p.value, Bool):
                raise ArchiveError(f"{p.tag.name}: BoolProperty holds {type(p.value).__name__}")
            write_tag(w, p.tag, 0, 1 if p.value.v else 0)
            continue
        body = Writer()
        p.value.write(body)
        write_tag(w, p.tag, len(body))
        w.raw(body.getvalue())
    w.fstring("None")
