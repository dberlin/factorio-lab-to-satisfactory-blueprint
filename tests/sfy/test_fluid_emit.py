"""Physical contracts for fluid transports, full rotations and structural actors."""

from dataclasses import replace

import pytest

from flab2bp.sfy.archive import ObjectRef
from flab2bp.sfy.codec import read_sbp, write_sbp
from flab2bp.sfy.geometry import quat_rotate
from flab2bp.sfy.layout.emit import EmitError, _level_refs, decode
from flab2bp.sfy.layout.model import (
    BeamObj,
    LiftObj,
    Link,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    PoleObj,
    Pose,
    SfyPlacement,
    WireObj,
    lift_geometry,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import straight
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.properties import Array, Int, Object, Property, Tag
from flab2bp.sfy.query import find, object_index
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.sections.model import endpoint, transform_placement
from flab2bp.sfy.spec import designer
from flab2bp.sfy.templates import apply_recipe
from tests.sfy.test_emit import _emit, _library

PIPE = "Build_PipelineMK2_NoIndicator_C"
PUMP = "Build_PipelinePumpMk2_C"
PIPE_HOLE = "Build_FoundationPassthrough_Pipe_C"
LIFT_HOLE = "Build_FoundationPassthrough_Lift_C"
LIFT = "Build_ConveyorLiftMk1_C"


def _roundtrip(placement: SfyPlacement) -> SfyPlacement:
    return decode(read_sbp(write_sbp(_emit(placement))), load_registry())


def test_full_rotation_and_modern_pump_survive_physical_file_roundtrip() -> None:
    registry = load_registry()
    placement = SfyPlacement(
        designer("mk3", registry),
        machines=(
            MachineObj(
                0, "Build_OilRefinery_C", Pose(0, 0, 100, 23.125, 11.75, -7.5), "Recipe_Plastic_C"
            ),
        ),
        pipe_attachments=(
            PipeAttachmentObj(1, PUMP, Pose(1000, 0, 500, 37.25, 61.5, -12.75)),
            PipeAttachmentObj(2, "Build_PipelineJunction_Cross_C", Pose(1000, 0, 1000, 90, 0, 90)),
        ),
        beams=(BeamObj(3, "Build_Beam_C", Pose(-1000, 0, 100, 17.5, 90, 22.25), 1800),),
        passthroughs=(PassthroughObj(4, PIPE_HOLE, Pose(1000, 0, 1600, 31.25, 45.5, 12.25), 200),),
        poles=(PoleObj(5, "Build_PowerPoleMk1_C", Pose(500, 0, 100, 0)),),
        wires=(WireObj(6, "Build_PowerLine_C", Link((5, "PowerConnection"), (1, "PowerInput"))),),
    )
    decoded = _roundtrip(placement)
    assert decoded == placement
    assert _roundtrip(decoded) == decoded
    turn = decoded.beams[0].pose.transform().rotation
    assert quat_rotate(turn, (1, 0, 0)) == pytest.approx((0, 0, 1), abs=1e-6)
    moved = transform_placement(placement, (300, -200, 100), 90)
    assert _roundtrip(moved) == moved
    assert moved.beams[0].length_cm == 1800


def test_stored_boundary_beam_rotation_does_not_shear_geometry() -> None:
    registry = load_registry()
    beam = BeamObj(0, "Build_Beam_C", Pose(1550, -1550, 50, 90), 3100)
    placement = SfyPlacement(designer("mk1", registry), beams=(beam,))
    decoded = _roundtrip(placement)
    # The native +X beam turns along +Y, with its 100 cm cross-section
    # touching x=1600 and z=0. Its f32 quaternion is not exactly unit length.
    assert validate(decoded, None, registry, only={"geom.bounds"}).errors == ()
    assert _roundtrip(decoded).beams[0].pose.transform().rotation == (
        decoded.beams[0].pose.transform().rotation
    )

    outside = replace(beam, pose=replace(beam.pose, x=1550.01))
    report = validate(
        _roundtrip(replace(placement, beams=(outside,))),
        None,
        registry,
        only={"geom.bounds"},
    )
    assert any(f.check == "geom.bounds" and outside.id in f.objects for f in report.errors)


def test_pipe_snapped_endpoints_are_hole_origins_and_all_references_roundtrip() -> None:
    registry = load_registry()
    start, end = pipe_ends(registry, PIPE)
    run = PipeRun(1, PIPE, straight((0, 0, 0), (0, 0, 1), 500), snapped_passthroughs=(2, 3))
    placement = SfyPlacement(
        designer("mk1", registry),
        pipes=(run,),
        passthroughs=(
            PassthroughObj(2, PIPE_HOLE, Pose(0, 0, 100, 0), 100, top_connection=(1, start)),
            PassthroughObj(3, PIPE_HOLE, Pose(0, 0, 600, 0), 200, bottom_connection=(1, end)),
        ),
    )
    # SetupConnections overrides the connection location, not saved spline geometry.
    assert endpoint(placement, 1, start, registry) == ((0, 0, 100), (0, 0, -1))
    assert endpoint(placement, 1, end, registry) == ((0, 0, 600), (0, 0, 1))
    assert run.start == (0, 0, 0)
    assert run.end == (0, 0, 500)
    assert _roundtrip(placement) == placement
    built = _emit(placement)
    index = object_index(built)
    for _, data in built.objects:
        for prop in data.properties:
            assert all(ref.path in index for ref in _level_refs(prop.value))
    actor, data = next((h, d) for h, d in built.objects if h.class_name == PIPE)
    snapped = find(data.properties, "mSnappedPassthroughs")
    assert isinstance(snapped, Array)
    assert len(snapped.items) == 2
    for slot, prop_name in zip(
        snapped.items, ("mTopSnappedConnection", "mBottomSnappedConnection"), strict=True
    ):
        assert isinstance(slot, Object)
        hole_data = index[slot.ref.path][1]
        connection = find(hole_data.properties, prop_name)
        assert isinstance(connection, Object)
        assert index[connection.ref.path][0].parent == actor.path


@pytest.mark.parametrize("height,normal", [(800, (0, 0, -1)), (-800, (0, 0, 1))])
def test_snapped_lift_flow_indices_do_not_reverse_with_signed_height(
    height: float, normal: tuple[float, float, float]
) -> None:
    registry = load_registry()
    flow = registry.buildables[LIFT].flow
    assert flow is not None
    lift = LiftObj(1, LIFT, Pose(0, 0, 1000, 45), height, 90, (2, 3))
    placement = SfyPlacement(
        designer("mk1", registry),
        lifts=(lift,),
        passthroughs=(
            PassthroughObj(2, LIFT_HOLE, lift.pose, 100, top_connection=(1, flow.entry)),
            PassthroughObj(
                3, LIFT_HOLE, Pose(0, 0, 1000 + height, 0), 100, bottom_connection=(1, flow.exit)
            ),
        ),
    )
    assert lift.bottom_end(lift_geometry(registry, LIFT))[1] == normal
    assert lift.top_end(lift_geometry(registry, LIFT))[1] == tuple(-v for v in normal)
    assert _roundtrip(placement) == placement
    actor_data = next(d for h, d in _emit(placement).objects if h.class_name == LIFT)
    assert find(actor_data.properties, "mIsReversed") is None


def test_missing_reciprocal_hole_attachment_is_rejected() -> None:
    registry = load_registry()
    run = PipeRun(1, PIPE, straight((0, 0, 100), (0, 0, 1), 500), snapped_passthroughs=(2, None))
    placement = SfyPlacement(
        designer("mk1", registry),
        pipes=(run,),
        passthroughs=(PassthroughObj(2, PIPE_HOLE, Pose(0, 0, 100, 0), 100),),
    )
    with pytest.raises(EmitError, match="not reciprocal"):
        _emit(placement)


def test_bidirectional_pipe_links_do_not_acquire_conveyor_flow_on_decode() -> None:
    registry = load_registry()
    start, end = pipe_ends(registry, PIPE)
    placement = SfyPlacement(
        designer("mk1", registry),
        pipes=(
            PipeRun(1, PIPE, straight((0, 0, 100), (1, 0, 0), 400)),
            PipeRun(2, PIPE, straight((400, 0, 100), (1, 0, 0), 400)),
        ),
        links=(Link((2, start), (1, end)),),
    )
    assert _roundtrip(placement) == placement


def test_recipe_switch_rebuilds_global_fluid_inventory_indices() -> None:
    registry = load_registry()
    objects = _library().instantiate("Build_OilRefinery_C", 123, Pose(0, 0, 100, 0).transform())
    for recipe, expected in (
        ("Recipe_Plastic_C", {"PipeInputFactory": 0, "PipeOutputFactory": 1}),
        ("Recipe_Alternate_Plastic_1_C", {"PipeInputFactory": 1, "PipeOutputFactory": -1}),
        ("Recipe_Alternate_HeavyOilResidue_C", {"PipeInputFactory": 0, "PipeOutputFactory": 0}),
    ):
        objects = apply_recipe(objects, registry.recipe_paths[recipe], registry)
        actual = {
            h.name: find(d.properties, "mInventoryAccessIndex")
            for h, d in objects
            if h.name in expected
        }
        assert actual == {name: Int(index) for name, index in expected.items()}


def test_instantiating_a_pipe_does_not_reuse_a_runtime_network() -> None:
    library = _library()
    template = library.templates[PIPE]
    header, data = template.components[0]
    contaminated = replace(
        data,
        properties=(
            *data.properties,
            Property(Tag("mPipeNetworkID", "IntProperty", 0).as_modern(), Int(987654)),
        ),
    )
    templates = dict(library.templates)
    templates[PIPE] = replace(
        template, components=((header, contaminated), *template.components[1:])
    )
    objects = type(library)(templates).instantiate(PIPE, 456, Pose(0, 0, 100, 0).transform())
    network = next(
        find(d.properties, "mPipeNetworkID") for h, d in objects if h.name == header.name
    )
    assert network == Int(-1)
    for _, data in objects:
        for prop in data.properties:
            for ref in _level_refs(prop.value):
                assert ref != ObjectRef(header.level, header.path)


def test_beam_cost_uses_native_ten_centimetre_allowance_not_pipe_rounding() -> None:
    registry = load_registry()
    beam_class = "Build_Beam_C"
    recipe = registry.recipes[registry.build_recipes[beam_class]]
    for length, multiplier in ((400, 1), (410, 1), (411, 2), (1800, 5)):
        placement = SfyPlacement(
            designer("mk1", registry),
            beams=(BeamObj(1, beam_class, Pose(0, 0, 100, 0), length),),
        )
        actual = {entry.item.name: entry.amount for entry in _emit(placement).header.cost}
        assert actual == {item: amount * multiplier for item, amount in recipe.ingredients}


def test_pipe_cost_uses_four_metre_segments() -> None:
    registry = load_registry()
    recipe = registry.recipes[registry.build_recipes[PIPE]]
    placement = SfyPlacement(
        designer("mk1", registry),
        pipes=(PipeRun(1, PIPE, straight((0, 0, 100), (1, 0, 0), 800)),),
    )
    actual = {entry.item.name: entry.amount for entry in _emit(placement).header.cost}
    assert actual == {item: amount * 2 for item, amount in recipe.ingredients}
