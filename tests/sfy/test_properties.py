from collections import Counter
from dataclasses import replace

import pytest

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
from flab2bp.sfy.chunks import decompress_stream, find_stream_start
from flab2bp.sfy.codec import read_sbp, tag_format
from flab2bp.sfy.header import read_header
from flab2bp.sfy.objects import COMPONENT, ObjectData, ObjectHeader, TagFormat, write_object_data
from flab2bp.sfy.properties import (
    TAG_BOOL_TRUE,
    Array,
    BinaryStruct,
    Bool,
    Enum,
    Float,
    Int,
    InventoryItem,
    Object,
    Opaque,
    PlayerInfoHandle,
    Property,
    Struct,
    Tag,
    TypeName,
    Vector,
    check_tag_format,
    modern_type_name,
    read_property_list,
    read_struct_value,
    write_property_list,
)
from flab2bp.sfy.trailers import ComponentTrailer
from flab2bp.sfy.versions import SaveCustomVersion
from tests.sfy.conftest import fixture_paths


def _round_trip(props, modern=False):
    w = Writer()
    write_property_list(w, props)
    r = Reader(w.getvalue())
    again = read_property_list(r, modern)
    r.expect_end()
    return again, w.getvalue()


def test_scalar_properties_round_trip():
    props = (
        Property(Tag("mLength", "FloatProperty", 0), Float(400.0)),
        Property(Tag("mCount", "IntProperty", 0), Int(3)),
        Property(
            Tag("mDirection", "EnumProperty", 0, enum_name="EFactoryConnectionDirection"),
            Enum("EFactoryConnectionDirection::FCD_INPUT"),
        ),
        Property(
            Tag("mConnectedComponent", "ObjectProperty", 0),
            Object(
                ObjectRef(
                    "Persistent_Level",
                    "Persistent_Level:PersistentLevel.Build_ConveyorBeltMk1_C_1.ConveyorAny0",
                )
            ),
        ),
    )
    again, raw = _round_trip(props)
    assert again == props
    assert raw.endswith(b"\x05\x00\x00\x00None\x00")


def test_struct_array_of_vectors_round_trip():
    inner = Tag(
        "mSplineData", "StructProperty", 0, struct_name="SplinePointData", struct_guid=bytes(16)
    )
    point = Struct(
        "SplinePointData",
        (
            Property(
                Tag("Location", "StructProperty", 0, struct_name="Vector", struct_guid=bytes(16)),
                Vector(0.0, 1.0, 2.0),
            ),
            Property(
                Tag(
                    "ArriveTangent",
                    "StructProperty",
                    0,
                    struct_name="Vector",
                    struct_guid=bytes(16),
                ),
                Vector(3.0, 4.0, 5.0),
            ),
        ),
    )
    props = (
        Property(
            Tag("mSplineData", "ArrayProperty", 0, inner_type="StructProperty"),
            Array("StructProperty", inner, (point, point)),
        ),
    )
    again, _ = _round_trip(props)
    assert again == props


def test_modern_tag_round_trip_keeps_the_type_tree():
    """Save version 58 and up: a type-name tree, a flags byte, and a bool in it.

    The tags are authored the way Task 12 will have to author them -- flat
    fields plus ``as_modern()`` -- so the tree is derived, never hand-written.
    """
    inner = Property(
        Tag("SwatchDesc", "ObjectProperty", 0).as_modern(),
        Object(ObjectRef("", "")),
    )
    props = (
        Property(
            Tag("mIsReversed", "BoolProperty", 0).as_modern(TAG_BOOL_TRUE),
            Bool(True),
        ),
        Property(
            Tag(
                "mCustomizationData",
                "StructProperty",
                0,
                struct_name="FactoryCustomizationData",
            ).as_modern(),
            Struct("FactoryCustomizationData", (inner,)),
        ),
    )
    assert props[1].tag.type_name == TypeName(
        "StructProperty",
        (TypeName("FactoryCustomizationData", (TypeName("/Script/FactoryGame"),)),),
    )
    again, _ = _round_trip(props, modern=True)
    assert again == props


def _raw_object_blobs(data: bytes) -> list[bytes]:
    """The untouched per-object byte blobs of a .sbp, in TOC order."""
    r = Reader(data)
    read_header(r)
    p = Reader(decompress_stream(data, find_stream_start(data, r.pos)))
    p.i32()
    p.bytes(p.i32())
    p.i32()
    return [p.bytes(p.i32()) for _ in range(p.i32())]


def test_every_fixture_object_body_parses_with_a_trailer():
    """Every object's body splits into a property list and a trailer.

    The check is against the fixture's own bytes: re-encoding each decoded
    object must reproduce its original blob exactly, so a property or trailer
    that decoded wrongly but self-consistently cannot pass.
    """
    seen = Counter()
    for path in fixture_paths():
        data = path.read_bytes()
        bp = read_sbp(data)
        fmt = tag_format(bp.header)
        blobs = _raw_object_blobs(data)
        assert len(blobs) == len(bp.objects), path.name
        for (h, d), raw in zip(bp.objects, blobs, strict=True):
            assert write_object_data(d, h, fmt) == raw, (path.name, h.path)
            seen[h.class_name] += 1
    assert sum(seen.values()) > 0


def test_a_modern_tag_type_tree_is_derivable_from_its_flat_fields():
    """Every modern tag in the corpus, rebuilt from the classic fields alone.

    This is what lets a later task author properties for a 1.1-family file: it
    can set ``struct_name`` and friends and call ``as_modern()`` instead of
    spelling out ``FPropertyTypeName`` trees by hand.
    """
    checked = Counter()

    def visit(p, where):
        tag = p.tag
        assert tag.type_name is not None, where
        flat = replace(
            tag, type_name=None, flags=None, type_package=None, enum_storage=None, struct_guid=None
        )
        assert modern_type_name(flat) == tag.type_name, (where, tag.type_name)
        assert flat.as_modern(tag.flags or 0) == tag, (where, tag)
        checked[tag.type] += 1
        if isinstance(p.value, Struct):
            for q in p.value.fields:
                visit(q, f"{where}.{tag.name}")
        elif isinstance(p.value, Array):
            assert p.value.inner_tag is None, where
            for item in p.value.items:
                if isinstance(item, Struct):
                    for q in item.fields:
                        visit(q, f"{where}.{tag.name}[]")

    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if not tag_format(bp.header).modern:
            continue
        for h, d in bp.objects:
            for p in d.properties:
                visit(p, h.path)
    assert checked["StructProperty"] > 0 and checked["ArrayProperty"] > 0


def test_writing_a_tag_in_the_wrong_format_for_the_file_is_refused():
    """A hand-built tag in the other format would corrupt the file silently."""
    classic = Property(Tag("mLength", "FloatProperty", 0), Float(400.0))
    modern = Property(Tag("mLength", "FloatProperty", 0).as_modern(), Float(400.0))

    check_tag_format((classic,), modern=False)
    check_tag_format((modern,), modern=True)

    with pytest.raises(ArchiveError, match="mLength: classic tag in a modern file"):
        check_tag_format((classic,), modern=True)
    with pytest.raises(ArchiveError, match="mLength: modern tag in a classic file"):
        check_tag_format((modern,), modern=False)


def test_write_object_data_refuses_a_property_in_the_files_other_tag_format():
    header = ObjectHeader(
        COMPONENT, "/Script/FactoryGame.FGPowerInfoComponent", "L", "L:P.C", 0, None, None, None, ""
    )
    classic = Property(Tag("mTargetConsumption", "FloatProperty", 0), Float(1.0))
    data = ObjectData(None, None, 0, (classic,), ComponentTrailer())
    with pytest.raises(ArchiveError, match=r"L:P\.C\.mTargetConsumption: classic tag"):
        write_object_data(data, header, TagFormat(modern=True, control_byte=True))

    modern = Property(Tag("mTargetConsumption", "FloatProperty", 0).as_modern(), Float(1.0))
    other = ObjectData(None, None, None, (modern,), ComponentTrailer())
    with pytest.raises(ArchiveError, match=r"L:P\.C\.mTargetConsumption: modern tag"):
        write_object_data(other, header, TagFormat(modern=False, control_byte=False))


def test_a_struct_whose_size_fits_no_known_layout_stays_binary():
    """The rewind behind every fixed-layout struct: keep the bytes, lose nothing."""
    blob = bytes(range(13))
    w = Writer()
    w.fstring("mOddVector")
    w.fstring("StructProperty")
    w.i32(len(blob))
    w.i32(0)
    w.fstring("Vector")
    w.guid(bytes(16))
    w.u8(0)
    w.raw(blob)
    w.fstring("None")
    raw = w.getvalue()

    props = read_property_list(Reader(raw), modern=False)
    assert props[0].value == BinaryStruct("Vector", blob)
    out = Writer()
    write_property_list(out, props)
    assert out.getvalue() == raw


def test_an_array_of_an_unknown_inner_type_stays_opaque():
    """The rewind behind every array: an inner type we cannot decode keeps its bytes."""
    payload = b"\x02\x00\x00\x00" + bytes(range(20))
    w = Writer()
    w.fstring("mWeirdArray")
    w.fstring("ArrayProperty")
    w.i32(len(payload))
    w.i32(0)
    w.fstring("MapProperty")
    w.u8(0)
    w.raw(payload)
    w.fstring("None")
    raw = w.getvalue()

    props = read_property_list(Reader(raw), modern=False)
    assert props[0].value == Opaque(payload)
    out = Writer()
    write_property_list(out, props)
    assert out.getvalue() == raw


def test_no_opaque_values_in_the_classes_the_layout_needs():
    needed = {
        "Build_ConstructorMk1_C",
        "Build_SmelterMk1_C",
        "Build_AssemblerMk1_C",
        "Build_ConveyorBeltMk1_C",
        "Build_ConveyorBeltMk5_C",
        "Build_ConveyorLiftMk1_C",
        "Build_ConveyorAttachmentSplitter_C",
        "Build_ConveyorAttachmentMerger_C",
        "Build_PowerPoleMk1_C",
        "Build_PowerLine_C",
        "Build_Foundation_8x1_01_C",
        "Build_FoundationPassthrough_Lift_C",
        "Build_Pipeline_C",
        "FGFactoryConnectionComponent",
        "FGPowerConnectionComponent",
        "FGPipeConnectionComponent",
        "FGInventoryComponent",
        "FGPowerInfoComponent",
        "FGFactoryLegsComponent",
    }
    opaque = Counter()

    def visit(value, cls):
        if isinstance(value, Opaque):
            opaque[cls] += 1
        elif isinstance(value, Struct):
            for p in value.fields:
                visit(p.value, cls)
        elif isinstance(value, Array):
            for item in value.items:
                visit(item, cls)

    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < 58:
            continue
        for h, d in bp.objects:
            if h.class_name not in needed:
                continue
            for p in d.properties:
                visit(p.value, h.class_name)
    assert not opaque, dict(opaque)


def _binary_structs_in(value):
    """Every :class:`BinaryStruct` reachable from a property value."""
    if isinstance(value, BinaryStruct):
        yield value
    elif isinstance(value, Struct):
        for p in value.fields:
            yield from _binary_structs_in(p.value)
    elif isinstance(value, Array):
        for item in value.items:
            yield from _binary_structs_in(item)


def test_no_binary_struct_survives_in_the_corpus():
    """Every struct the corpus carries is decoded; nothing is left as opaque bytes."""
    left = Counter()
    for path in fixture_paths():
        for h, d in read_sbp(path.read_bytes()).objects:
            for p in d.properties:
                for value in _binary_structs_in(p.value):
                    left[(value.name, h.class_name)] += 1
    assert not left, dict(left)


def test_player_info_handle_both_layouts_round_trip():
    for raw in (b"\x00\x05", b"\x00\x05\x00\x00\x00"):
        v = read_struct_value(Reader(raw), "PlayerInfoHandle", len(raw), modern=True)
        assert isinstance(v, PlayerInfoHandle)
        w = Writer()
        v.write(w)
        assert w.getvalue() == raw


def test_player_info_handle_reads_both_layouts_the_same_way():
    """The two shapes differ only in the index's width, not in what it means."""
    narrow = read_struct_value(Reader(b"\x06\x01"), "PlayerInfoHandle", 2, modern=False)
    wide = read_struct_value(Reader(b"\x06\x01\x00\x00\x00"), "PlayerInfoHandle", 5, modern=True)
    assert narrow == PlayerInfoHandle(6, 1, wide_index=False)
    assert wide == PlayerInfoHandle(6, 1, wide_index=True)
    assert narrow.table_index == wide.table_index


def test_player_info_handle_null_service_provider_reads_as_invalid():
    """``PlayerInfoCache.h`` lines 338-342: a legacy handle on the Null provider."""
    handle = read_struct_value(Reader(b"\x00\x07"), "PlayerInfoHandle", 2, modern=False)
    assert handle.table_index == 7  # the wire byte, so the write is byte-identical
    assert handle.player_info_table_index == -1  # what the game loads it as
    assert not handle.is_valid
    assert read_struct_value(Reader(b"\x06\x07"), "PlayerInfoHandle", 2, modern=False).is_valid


def test_player_info_handle_shape_follows_the_save_custom_version():
    """The 2-byte shape is exactly the pre-57 fixtures, the 5-byte one the rest.

    ``FPlayerInfoHandle::operator<<`` gates the wide index on
    :attr:`SaveCustomVersion.NewPlayerInfoHandleSerializationFormat`; the reader
    is handed a size rather than a version, so this is where the two are tied
    together.
    """
    shapes = Counter()
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        for _, d in bp.objects:
            for p in d.properties:
                for handle in _player_info_handles_in(p.value):
                    shapes[(bp.header.save_version, handle.wide_index)] += 1
    assert shapes
    boundary = SaveCustomVersion.NewPlayerInfoHandleSerializationFormat
    assert all(wide == (version >= boundary) for version, wide in shapes)


def _player_info_handles_in(value):
    if isinstance(value, PlayerInfoHandle):
        yield value
    elif isinstance(value, Struct):
        for p in value.fields:
            yield from _player_info_handles_in(p.value)
    elif isinstance(value, Array):
        for item in value.items:
            yield from _player_info_handles_in(item)


def test_inventory_item_round_trips_an_empty_and_a_filled_item():
    empty = b"\x00" * 12
    value = read_struct_value(Reader(empty), "InventoryItem", len(empty), modern=True)
    assert value == InventoryItem(ObjectRef("", ""))
    assert not value.has_state

    w = Writer()
    w.object_ref(ObjectRef("", "/Game/Desc_Fuel.Desc_Fuel_C"))
    w.i32(0)
    filled = w.getvalue()
    value = read_struct_value(Reader(filled), "InventoryItem", len(filled), modern=True)
    assert value == InventoryItem(ObjectRef("", "/Game/Desc_Fuel.Desc_Fuel_C"))

    for raw in (empty, filled):
        out = Writer()
        read_struct_value(Reader(raw), "InventoryItem", len(raw), modern=True).write(out)
        assert out.getvalue() == raw


def test_inventory_item_keeps_an_item_state_it_cannot_decode():
    """``FFGDynamicStruct``'s flag says a state follows; its bytes are kept whole."""
    w = Writer()
    w.object_ref(ObjectRef("", ""))
    w.i32(1)
    w.raw(bytes(range(9)))
    raw = w.getvalue()
    value = read_struct_value(Reader(raw), "InventoryItem", len(raw), modern=True)
    assert value == InventoryItem(ObjectRef("", ""), has_state=True, state=bytes(range(9)))
    out = Writer()
    value.write(out)
    assert out.getvalue() == raw


def test_an_inventory_item_whose_bytes_do_not_parse_stays_binary():
    """The never-drop-bytes fallback still stands behind the new decoders."""
    raw = b"\xff\xff\xff\xff" + bytes(8)
    value = read_struct_value(Reader(raw), "InventoryItem", len(raw), modern=True)
    assert value == BinaryStruct("InventoryItem", raw)
    out = Writer()
    value.write(out)
    assert out.getvalue() == raw
