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
together here: every object at save version 58 and 60 has the control byte and
a modern tag, every object at save version 46 and 52 has neither. The control
byte is therefore what selects the tag format, and it is kept on
:class:`ObjectProperties` so the body writes back identically.

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
:data:`BINARY_STRUCTS` and for a modern tag flagged
:data:`TAG_NATIVE_SERIALIZE`; everything else is a nested property list, which
must consume exactly ``size`` bytes. A struct whose bytes do not parse, or a
binary struct whose ``size`` matches no known layout, degrades to
:class:`BinaryStruct`, which still writes back byte for byte.

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
from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer

__all__ = [
    "BINARY_STRUCTS",
    "TAG_BOOL_TRUE",
    "TAG_HAS_ARRAY_INDEX",
    "TAG_HAS_PROPERTY_GUID",
    "TAG_NATIVE_SERIALIZE",
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
    "LinearColor",
    "Map",
    "Name",
    "Object",
    "ObjectProperties",
    "Opaque",
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


@dataclass(frozen=True, slots=True)
class Property:
    tag: Tag
    value: Value


PropertyList = tuple[Property, ...]


class ObjectProperties(tuple[Property, ...]):
    """An object body's property list plus the byte UE5 writes in front of it.

    ``control`` is the ``uint8 SerializationControl`` of
    ``UObject::Serialize``, present from ``FileVersionUE5`` 1011 and always 0
    in this corpus, or ``None`` for the older bodies that have no such byte. It
    also selects the tag format (see the module docstring).

    The byte belongs to the object body, not to a property list, so nested
    struct lists never have one. It rides on the tuple rather than in it, so
    the list still compares equal to a plain tuple of properties and still
    iterates as one.
    """

    control: int | None

    def __new__(
        cls, items: tuple[Property, ...] = (), control: int | None = None
    ) -> ObjectProperties:
        self = super().__new__(cls, items)
        self.control = control
        return self


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


def _read_type_name(r: Reader) -> TypeName:
    name = r.fstring()
    count = r.i32()
    if count < 0:
        raise ArchiveError(f"type name {name!r} declares {count} parameters")
    return TypeName(name, tuple(_read_type_name(r) for _ in range(count)))


def _write_type_name(w: Writer, node: TypeName) -> None:
    w.fstring(node.name)
    w.i32(len(node.params))
    for p in node.params:
        _write_type_name(w, p)


def _read_modern_tag(r: Reader, name: str) -> _TagRead:
    node = _read_type_name(r)
    size = r.i32()
    flags = r.u8()
    index = r.i32() if flags & TAG_HAS_ARRAY_INDEX else 0
    guid = r.guid() if flags & TAG_HAS_PROPERTY_GUID else None
    bool_value = (1 if flags & TAG_BOOL_TRUE else 0) if node.name == "BoolProperty" else None
    # The tree's parameters are what the classic tag spelled out as named fields.
    params = node.params
    first = params[0].name if params else None
    struct_name = first if node.name == "StructProperty" else None
    enum_name = first if node.name in ("ByteProperty", "EnumProperty") else None
    inner_type = first if node.name in ("ArrayProperty", "SetProperty") else None
    if inner_type == "StructProperty" and params[0].params:
        struct_name = params[0].params[0].name
    key_type = value_type = None
    if node.name == "MapProperty" and len(params) >= 2:
        key_type, value_type = params[0].name, params[1].name
    tag = Tag(
        name,
        node.name,
        index,
        struct_name=struct_name,
        enum_name=enum_name,
        inner_type=inner_type,
        key_type=key_type,
        value_type=value_type,
        guid=guid,
        type_name=node,
        flags=flags,
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
    assert tag.type_name is not None
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


def read_struct_value(r: Reader, struct_name: str | None, size: int) -> Value:
    """Read a struct value of exactly ``size`` bytes, degrading to a binary blob."""
    return _read_struct_value(r, struct_name, size, modern=False, native=False)


def _read_struct_value(
    r: Reader, struct_name: str | None, size: int, modern: bool, native: bool
) -> Value:
    name = struct_name or ""
    start = r.pos
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


def read_property_list(r: Reader) -> PropertyList:
    """Read one object body's properties, through the ``None`` terminator.

    A leading zero byte is UE5's ``SerializationControl`` (see
    :class:`ObjectProperties`) and selects the modern tag format. A tag name is
    an FString of at least five bytes, so the low byte of a real first tag is
    never zero and the two cannot be confused.
    """
    control: int | None = None
    if r.remaining() and r.data[r.pos] == 0:
        control = r.u8()
    return ObjectProperties(_read_fields(r, control is not None), control)


def write_property_list(w: Writer, props: PropertyList) -> None:
    """Write an object body's properties, followed by the ``None`` terminator."""
    control = getattr(props, "control", None)
    if control is not None:
        w.u8(control)
    _write_fields(w, props)


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
