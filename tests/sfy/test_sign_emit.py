"""Connection signs preserve authored content and real native construction costs."""

from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import read_sbp, write_sbp
from flab2bp.sfy.layout.emit import _level_refs, decode
from flab2bp.sfy.layout.model import Pose, SfyPlacement, SignObj
from flab2bp.sfy.properties import Array, LinearColor, SoftObject, Str, Struct
from flab2bp.sfy.query import find, object_index
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.spec import designer
from tests.sfy.test_emit import _emit

SIGN = "Build_StandaloneWidgetSign_SmallWide_C"


def test_paired_connection_signs_preserve_content_style_and_native_cost() -> None:
    registry = load_registry()
    placement = SfyPlacement(
        designer("mk1", registry),
        signs=(
            SignObj(0, SIGN, Pose(200, -500, 550, 90), "INPUT", "B", "BPW_Sign4x1_5"),
            SignObj(1, SIGN, Pose(200, -500, 500, 90), "180 Iron Ingot", "B"),
            SignObj(2, SIGN, Pose(-200, 500, 550, -90), "Output", "C", "BPW_Sign4x1_5"),
            SignObj(3, SIGN, Pose(-200, 500, 500, -90), "120 Iron Plate", "C"),
        ),
    )
    blueprint = read_sbp(write_sbp(_emit(placement)))
    decoded = decode(blueprint, registry)
    assert decoded == placement
    assert decode(read_sbp(write_sbp(_emit(decoded))), registry) == placement
    assert {row.item.name: row.amount for row in blueprint.header.cost} == {
        "Desc_IronPlate_C": 12,
        "Desc_QuartzCrystal_C": 12,
    }
    assert tuple(ref.name for ref in blueprint.header.recipes) == (
        "Recipe_StandaloneWidgetSign_SmallWide_C",
    )
    index = object_index(blueprint)
    for (_, data), sign in zip(blueprint.objects, placement.signs, strict=True):
        text = find(data.properties, "mPrefabTextElementSaveData")
        assert isinstance(text, Array)
        content = {}
        for element in text.items:
            assert isinstance(element, Struct)
            name = find(element.fields, "ElementName")
            value = find(element.fields, "Text")
            assert isinstance(name, Str) and isinstance(value, Str)
            content[name.v] = value.v
        assert content == {"Name": sign.text, "Label": sign.label}
        prefab = find(data.properties, "mSoftActivePrefabLayout")
        assert isinstance(prefab, SoftObject)
        reader = Reader(prefab.raw)
        assert reader.fstring() == (
            "/Game/FactoryGame/Interface/UI/InGame/Signs/SignLayouts/" + sign.layout
        )
        assert reader.fstring() == sign.layout + "_C"
        assert reader.fstring() == ""
        reader.expect_end()
        assert find(data.properties, "mForegroundColor") == LinearColor(
            0.7835379838943481, 0.2917709946632385, 0.057805001735687256, 1.0
        )
        assert find(data.properties, "mBackgroundColor") == LinearColor(0, 0, 0, 1)
        for icon_property in ("mPrefabIconElementSaveData", "mGlobalPrefabIconElementSaveData"):
            icons = find(data.properties, icon_property)
            assert isinstance(icons, Array)
            assert icons.items == ()
        for prop in data.properties:
            assert all(ref.path in index for ref in _level_refs(prop.value))
