from collections import Counter

from flab2bp.sfy.archive import ObjectRef, Reader, Writer
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.properties import (
    TAG_BOOL_TRUE,
    Array,
    Bool,
    Enum,
    Float,
    Int,
    Object,
    Opaque,
    Property,
    Struct,
    Tag,
    TypeName,
    Vector,
    read_property_list,
    write_property_list,
)
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
    """Save version 58 and up: a type-name tree, a flags byte, and a bool in it."""
    struct_tree = TypeName(
        "StructProperty",
        (TypeName("FactoryCustomizationData", (TypeName("/Script/FactoryGame"),)),),
    )
    inner = Property(
        Tag("SwatchDesc", "ObjectProperty", 0, type_name=TypeName("ObjectProperty"), flags=0),
        Object(ObjectRef("", "")),
    )
    props = (
        Property(
            Tag(
                "mIsReversed",
                "BoolProperty",
                0,
                type_name=TypeName("BoolProperty"),
                flags=TAG_BOOL_TRUE,
            ),
            Bool(True),
        ),
        Property(
            Tag(
                "mCustomizationData",
                "StructProperty",
                0,
                struct_name="FactoryCustomizationData",
                type_name=struct_tree,
                flags=0,
            ),
            Struct("FactoryCustomizationData", (inner,)),
        ),
    )
    again, _ = _round_trip(props, modern=True)
    assert again == props


def test_every_fixture_object_body_parses_with_a_trailer():
    """Every object's body splits into a property list and a trailer.

    ``read_object_data`` does the splitting now; what this checks on all 1934
    real bodies is that the properties it hands back write out and read back in
    unchanged, and that a trailer was classified for every one of them.
    """
    seen = Counter()
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        for h, d in bp.objects:
            w = Writer()
            write_property_list(w, d.properties)
            r = Reader(w.getvalue())
            assert read_property_list(r, d.control is not None) == d.properties, (
                path.name,
                h.path,
            )
            r.expect_end()
            assert d.trailer is not None, (path.name, h.path)
            seen[h.class_name] += 1
    assert sum(seen.values()) > 0


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
