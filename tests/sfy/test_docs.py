from __future__ import annotations

import json
from pathlib import Path

import pytest

from flab2bp.sfy import docs
from flab2bp.sfy.registry import Registry, RegistryError, load_registry

DOCS = docs.docs_path()
needs_game = pytest.mark.skipif(not DOCS.exists(), reason="Satisfactory install not present")

CLEARANCE = (
    "((ClearanceBox=(Min=(X=-400.000000,Y=-500.000000,Z=0.000000),"
    "Max=(X=400.000000,Y=500.000000,Z=600.000000),IsValid=True)),"
    "(Type=CT_Soft,ClearanceBox=(Min=(X=-20.000000,Y=-20.000000,Z=0.000000),"
    "Max=(X=20.000000,Y=20.000000,Z=250.000000),IsValid=True),"
    "RelativeTransform=(Translation=(X=-165.000000,Y=-460.000000,Z=0.000000))))"
)


def test_satisfactory_dir_follows_the_environment(monkeypatch):
    monkeypatch.setenv("FLAB2BP_SATISFACTORY_DIR", "/opt/sf")
    assert docs.satisfactory_dir() == Path("/opt/sf")
    assert docs.docs_path() == Path("/opt/sf/CommunityResources/Docs/en-US.json")


def test_parse_clearance_reads_hard_and_soft_boxes():
    boxes = docs.parse_clearance(CLEARANCE)
    assert len(boxes) == 2
    assert boxes[0].min == (-400.0, -500.0, 0.0)
    assert boxes[0].max == (400.0, 500.0, 600.0)
    assert not boxes[0].soft
    assert boxes[0].translation == (0.0, 0.0, 0.0)
    assert boxes[1].soft and boxes[1].translation == (-165.0, -460.0, 0.0)


def test_parse_clearance_of_an_empty_export_is_empty():
    assert docs.parse_clearance("") == ()


def test_parse_clearance_rejects_a_box_without_bounds():
    with pytest.raises(docs.DocsParseError):
        docs.parse_clearance("((Type=CT_Soft,ClearanceBox=(IsValid=True)))")


def test_parse_vector():
    assert docs.parse_vector("(X=6,Y=-5.5,Z=0.000000)") == (6.0, -5.5, 0.0)
    with pytest.raises(docs.DocsParseError):
        docs.parse_vector("(X=6,Y=6)")


def test_parse_item_amounts():
    text = (
        "((ItemClass=\"/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/Parts/"
        "IronPlate/Desc_IronPlate.Desc_IronPlate_C'\",Amount=1),"
        "(ItemClass=\"/Script/Engine.BlueprintGeneratedClass'/Game/FactoryGame/Resource/Parts/"
        "Wire/Desc_Wire.Desc_Wire_C'\",Amount=3))"
    )
    assert docs.parse_item_amounts(text) == (("Desc_IronPlate_C", 1), ("Desc_Wire_C", 3))


def test_parse_item_amounts_rejects_an_unreadable_entry():
    with pytest.raises(docs.DocsParseError):
        docs.parse_item_amounts('((ItemClass="/Game/Nope.Nope",Amount=1))')


def test_parse_class_list():
    text = (
        '("/Game/FactoryGame/Buildable/Factory/ConstructorMk1/Build_ConstructorMk1'
        '.Build_ConstructorMk1_C",'
        '"/Game/FactoryGame/Equipment/BuildGun/BP_BuildGun.BP_BuildGun_C")'
    )
    assert docs.parse_class_list(text) == ("Build_ConstructorMk1_C", "BP_BuildGun_C")


@needs_game
def test_extract_known_values():
    data = docs.extract(docs.load_docs(DOCS))
    ctor = data["buildables"]["Build_ConstructorMk1_C"]
    assert ctor["power_mw"] == 4.0 and ctor["manufacturing_speed"] == 1.0
    assert ctor["clearance"][0]["max"] == [400.0, 500.0, 600.0]
    assert ctor["native_class"] == "FGBuildableManufacturer"
    assert ctor["max_potential"] == 1.0
    assert ctor["potential_shard_slots"] == 0 and ctor["production_boost_slots"] == 1
    assert data["buildables"]["Build_ConveyorBeltMk1_C"]["belt_speed_per_min"] == 120.0
    assert data["buildables"]["Build_ConveyorBeltMk6_C"]["belt_speed_per_min"] == 2400.0
    assert data["buildables"]["Build_BlueprintDesigner_C"]["designer_dims"] == [4, 4, 4]
    assert data["buildables"]["Build_BlueprintDesigner_Mk3_C"]["designer_dims"] == [6, 6, 6]
    assert data["buildables"]["Build_ConveyorLiftMk1_C"]["mesh_height_cm"] == 200.0
    assert data["buildables"]["Build_Foundation_8x2_01_C"]["width_cm"] == 800.0
    plate = data["recipes"]["Recipe_IronPlate_C"]
    assert plate["producers"] == ["Build_ConstructorMk1_C"] and plate["duration_s"] == 6.0
    assert plate["ingredients"] == [["Desc_IronIngot_C", 3]]
    assert plate["products"] == [["Desc_IronPlate_C", 2]]
    assert data["descriptors"]["Desc_ConstructorMk1_C"] == "Build_ConstructorMk1_C"
    assert data["build_recipes"]["Build_ConstructorMk1_C"] == "Recipe_ConstructorMk1_C"


def test_committed_docs_json_loads_into_registry_dataclasses():
    here = Path(docs.__file__).parent / "data" / "docs.json"
    reg = Registry.from_docs_only(json.loads(here.read_text()))
    assert reg.buildables["Build_ConstructorMk1_C"].clearance[0].max == (400.0, 500.0, 600.0)
    assert reg.buildables["Build_ConstructorMk1_C"].ports == ()
    assert reg.recipes["Recipe_IronPlate_C"].producers == ("Build_ConstructorMk1_C",)
    assert reg.descriptors["Desc_ConstructorMk1_C"] == "Build_ConstructorMk1_C"
    assert reg.build_recipes["Build_ConstructorMk1_C"] == "Recipe_ConstructorMk1_C"
    assert reg.provenance["docs_sha256"]
    assert reg.limits.belt_max_spline_cm == 5600.1
    assert reg.limits.pipe_min_bend_radius_cm == 75.0
    assert reg.limits.belt_bend_radius_cm is None


def test_registry_rejects_a_payload_with_a_missing_section():
    with pytest.raises(RegistryError, match="recipes"):
        Registry.from_docs_only({"provenance": {}, "buildables": {}})


def test_load_registry_without_a_file_raises(tmp_path):
    # registry.json itself arrives in Task 10; the reader already exists.
    with pytest.raises(RegistryError, match="no registry"):
        load_registry(tmp_path / "registry.json")
