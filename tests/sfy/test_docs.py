from __future__ import annotations

import json
import math
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


def test_parse_clearance_defaults_the_transform_members_the_export_omits():
    """Unreal drops any member equal to its default, and ``Scale3D``'s is ones."""
    boxes = docs.parse_clearance(CLEARANCE)
    for box in boxes:
        assert box.rotation == (0.0, 0.0, 0.0)
        assert box.scale == (1.0, 1.0, 1.0)
        assert not box.exclude_for_snapping


def test_parse_clearance_reads_the_whole_relative_transform():
    """A quarter turn about Z, a non-default scale and the snapping flag."""
    (box,) = docs.parse_clearance(
        "((Type=CT_Soft,ExcludeForSnapping=True,"
        "ClearanceBox=(Min=(X=-200.000000,Y=-30.000000,Z=0.000000),"
        "Max=(X=200.000000,Y=30.000000,Z=96.000000),IsValid=True),"
        "RelativeTransform=(Rotation=(X=0.000000,Y=-0.000000,Z=0.707107,W=0.707107),"
        "Translation=(X=1.000000,Y=2.000000,Z=3.000000),"
        "Scale3D=(X=1.000000,Y=1.000000,Z=0.000000))))"
    )
    assert box.exclude_for_snapping
    assert box.translation == (1.0, 2.0, 3.0)
    assert box.scale == (1.0, 1.0, 0.0)
    assert box.rotation == pytest.approx((0.0, 90.0, 0.0), abs=1e-4)


@pytest.mark.parametrize(
    ("quaternion", "rotator"),
    [
        ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
        ((0.0, 0.0, 0.707107, 0.707107), (0.0, 90.0, 0.0)),
        ((0.0, 0.0, 1.0, 0.0), (0.0, 180.0, 0.0)),
        ((0.5, 0.5, 0.5, 0.5), (0.0, 90.0, -90.0)),
        ((-0.707107, 0.0, 0.0, 0.707107), (0.0, 0.0, 90.0)),
    ],
)
def test_quaternion_to_rotator_matches_unreal(quaternion, rotator):
    assert docs.quaternion_to_rotator(*quaternion) == pytest.approx(rotator, abs=1e-3)


def _rotator_to_quaternion(pitch: float, yaw: float, roll: float) -> tuple[float, ...]:
    """``FRotator::Quaternion()``, for checking the other direction."""
    half = math.pi / 360.0
    sp, cp = math.sin(pitch * half), math.cos(pitch * half)
    sy, cy = math.sin(yaw * half), math.cos(yaw * half)
    sr, cr = math.sin(roll * half), math.cos(roll * half)
    return (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


@pytest.mark.parametrize(
    "rotator",
    [
        (0.0, 0.0, 0.0),
        (0.0, 90.0, 0.0),
        (0.0, 180.0, 0.0),
        (30.0, 45.0, 60.0),
        (-70.0, 120.0, -15.0),
        # The two poles, where the branch that folds yaw and roll together runs.
        # A rotator there is not unique, so only the rotation can be compared.
        (90.0, 0.0, 0.0),
        (-90.0, 37.0, 0.0),
    ],
)
def test_a_rotation_survives_the_trip_through_a_rotator(rotator):
    quaternion = _rotator_to_quaternion(*rotator)
    back = _rotator_to_quaternion(*docs.quaternion_to_rotator(*quaternion))
    # q and -q are the same rotation, so the two agree when |q . q'| is 1.
    assert abs(sum(a * b for a, b in zip(quaternion, back, strict=True))) == pytest.approx(
        1.0, abs=1e-6
    )


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


def test_hydraulic_units_survive_docs_extraction_and_registry_loading():
    payload = docs.extract(
        {
            "FGBuildablePipelinePump": [
                {
                    "ClassName": "Build_Pump_C",
                    "mDisplayName": "Pump",
                    "mDesignPressure": "20.000000",
                    "mMaxPressure": "22.000000",
                    "mDefaultFlowLimit": "10.000000",
                    "mUserFlowLimit": "-1.000000",
                    "mPowerConsumption": "4.000000",
                }
            ],
            "FGBuildablePipeline": [
                {
                    "ClassName": "Build_Pipe_C",
                    "mDisplayName": "Pipe",
                    "mFlowLimit": "5.000000",
                }
            ],
        }
    )
    registry = Registry.from_docs_only(payload)
    pump = registry.buildables["Build_Pump_C"]
    assert (pump.pump_design_head_m, pump.pump_max_head_m) == (20.0, 22.0)
    assert pump.pipe_flow_limit_m3s == 10.0
    assert pump.power_mw == 4.0
    pipe = registry.buildables["Build_Pipe_C"]
    assert pipe.pipe_flow_limit_m3s == 5.0
    assert pipe.pump_design_head_m is None
    assert pipe.pump_max_head_m is None


def test_beam_bounds_and_cost_are_centimetres_not_metres():
    registry = Registry.from_docs_only(
        docs.extract(
            {
                "FGBuildableBeam": [
                    {
                        "ClassName": "Build_Beam_C",
                        "mDisplayName": "Beam",
                        "mMaxLength": "4000.000000",
                        "mSize": "100.000000",
                        "mLengthPerCost": "400.000000",
                    }
                ]
            }
        )
    )
    beam = registry.buildables["Build_Beam_C"]
    assert beam.beam_max_length_cm == 4000.0
    assert beam.beam_size_cm == 100.0
    assert beam.length_per_cost_cm == 400.0
    assert beam.length_per_cost_source == "docs"


def test_missing_hydraulic_and_beam_fields_are_unknown_not_zero():
    registry = Registry.from_docs_only(
        docs.extract(
            {
                native: [{"ClassName": name, "mDisplayName": name}]
                for native, name in (
                    ("FGBuildablePipelinePump", "Build_Pump_C"),
                    ("FGBuildableBeam", "Build_Beam_C"),
                    ("FGBuildableManufacturer", "Build_Machine_C"),
                )
            }
        )
    )
    for buildable in registry.buildables.values():
        assert buildable.pump_design_head_m is None
        assert buildable.pump_max_head_m is None
        assert buildable.pipe_flow_limit_m3s is None
        assert buildable.beam_max_length_cm is None
        assert buildable.beam_size_cm is None
        assert buildable.length_per_cost_cm is None
        assert buildable.length_per_cost_source is None


def test_fluid_recipe_quantities_remain_integer_litres():
    water = "((ItemClass=\"BlueprintGeneratedClass'/Game/Water.Desc_Water_C'\",Amount=1000))"
    assert docs.parse_item_amounts(water) == (("Desc_Water_C", 1000),)


def test_physical_properties_on_unrelated_native_classes_are_not_reinterpreted():
    registry = Registry.from_docs_only(
        docs.extract(
            {
                "FGBuildableManufacturer": [
                    {
                        "ClassName": "Build_Machine_C",
                        "mDisplayName": "Machine",
                        "mMaxPressure": "99.0",
                        "mMaxLength": "9999.0",
                        "mSize": "50.0",
                        "mLengthPerCost": "200.0",
                    }
                ]
            }
        )
    )
    machine = registry.buildables["Build_Machine_C"]
    assert machine.pump_max_head_m is None
    assert machine.beam_max_length_cm is None
    assert machine.beam_size_cm is None
    assert machine.length_per_cost_cm is None


def test_invalid_hydraulic_default_names_the_source_property():
    with pytest.raises(docs.DocsParseError, match=r"Build_Pump_C\.mDesignPressure"):
        docs.extract(
            {
                "FGBuildablePipelinePump": [
                    {
                        "ClassName": "Build_Pump_C",
                        "mDisplayName": "Pump",
                        "mDesignPressure": "unknown",
                    }
                ]
            }
        )


def test_fluid_identity_comes_from_resource_form_not_descriptor_name():
    registry = Registry.from_docs_only(
        docs.extract(
            {
                "FGItemDescriptor": [
                    {"ClassName": "Desc_LiquidNamedSolid_C", "mForm": "RF_SOLID"},
                    {"ClassName": "Desc_PipeItem_C", "mForm": "RF_LIQUID"},
                    {"ClassName": "Desc_Unknown_C"},
                ],
                "FGResourceDescriptor": [
                    {"ClassName": "Desc_Gas_C", "mForm": "RF_GAS"},
                ],
            }
        )
    )
    assert registry.fluid_items == frozenset({"Desc_PipeItem_C", "Desc_Gas_C"})
