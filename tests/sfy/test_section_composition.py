"""Whole-section composition preserves clocks, balance and dependency height."""

import itertools
import time
from fractions import Fraction

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.sections.compose import (
    SectionLayout,
    _arrange,
    _partition_group,
    _production_layers,
    _section_frame,
    _sections_for,
)
from flab2bp.sfy.sections.model import endpoint
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.spec import BeltTier
from tests.sfy.conftest import flow_spec, sfy_registry


def _group(machine: str, recipe: str, source: str, product: str) -> SfyMachineGroup:
    return SfyMachineGroup(
        recipe_id=recipe,
        recipe_class=recipe,
        machine_item_id="machine",
        machine_class=machine,
        count=1,
        clock=Fraction(1),
        last_clock=Fraction(1),
        max_clock=Fraction(5, 2),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine={source: Fraction(1, 2)},
        outputs_per_machine={product: Fraction(1, 2)},
        power_mw_per_machine=4.0,
        last_power_mw=4.0,
    )


def test_partition_preserves_fractional_last_machine_and_upgrade_inventory() -> None:
    group = _group("Build_SmelterMk1_C", "Recipe_IngotIron_C", "iron-ore", "iron-ingot")
    group = group.model_copy(
        update={
            "count": 7,
            "clock": Fraction(5, 2),
            "last_clock": Fraction(7, 9),
            "power_shards_per_machine": 3,
            "last_power_shards": 0,
            "somersloops": 1,
            "last_power_mw": 1.25,
        }
    )
    pieces = _partition_group(group, 3)
    assert [piece.count for piece in pieces] == [3, 3, 1]
    assert [piece.last_clock for piece in pieces] == [
        Fraction(5, 2),
        Fraction(5, 2),
        Fraction(7, 9),
    ]
    assert sum(piece.row_inputs["iron-ore"] for piece in pieces) == group.row_inputs["iron-ore"]
    assert (
        sum(piece.row_outputs["iron-ingot"] for piece in pieces) == group.row_outputs["iron-ingot"]
    )
    assert sum(piece.row_power_shards for piece in pieces) == group.row_power_shards
    assert sum(piece.row_power_mw for piece in pieces) == group.row_power_mw
    assert (
        sum(piece.count * piece.somersloops for piece in pieces) == group.count * group.somersloops
    )


def test_cycle_is_refused_instead_of_inventing_production_order() -> None:
    first = _group("Build_SmelterMk1_C", "Recipe_IngotIron_C", "iron-ore", "iron-ingot")
    second = _group("Build_ConstructorMk1_C", "Recipe_IronRod_C", "iron-ingot", "iron-ore")
    spec = SfyBuildSpec(
        groups=(first, second), belt_item_id="conveyor-belt-mk1", belt_items_per_second=Fraction(1)
    )
    with pytest.raises(NoValidLayout, match="cyclic"):
        SectionLayout().lay_out(
            spec, designer("mk3", sfy_registry()), registry=sfy_registry(), lab_map=load_lab_map()
        )


def test_producer_is_not_above_consumer_with_one_real_boundary_per_item() -> None:
    registry = sfy_registry()
    smelter = _group("Build_SmelterMk1_C", "Recipe_IngotIron_C", "iron-ore", "iron-ingot")
    constructor = _group("Build_ConstructorMk1_C", "Recipe_IronRod_C", "iron-ingot", "iron-rod")
    spec = SfyBuildSpec(
        groups=(constructor, smelter),  # Input order is deliberately not production order.
        external_inputs={"iron-ore": Fraction(1, 2)},
        outputs={"iron-rod": Fraction(1, 2)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        belt_upgrades=(BeltTier(item_id="conveyor-belt-mk2", items_per_second=Fraction(2)),),
    )
    placement = SectionLayout().lay_out(
        spec, designer("mk3", registry), registry=registry, lab_map=load_lab_map()
    )
    heights = {machine.recipe_class: machine.pose.z for machine in placement.machines}
    assert heights[smelter.recipe_class] <= heights[constructor.recipe_class]
    assert {
        lane.item_id: lane.input_per_second
        for lane in placement.stack_lanes
        if lane.input_per_second
    } == spec.external_inputs
    assert {
        lane.item_id: lane.output_per_second
        for lane in placement.stack_lanes
        if lane.output_per_second
    } == spec.outputs
    assert len(placement.stack_lanes) == 2
    report = validate(placement, spec, registry)
    assert not report.errors, [finding.message for finding in report.errors]


def test_reinforced_plate_chain_uses_mixed_floors_without_stage_height_inflation() -> None:
    registry = sfy_registry()
    spec = flow_spec("reinforced-iron-plate-10")
    box = designer("mk3", registry)
    lab_map = load_lab_map()
    deadline = time.monotonic() + 15
    ids = itertools.count(1)
    stages = tuple(
        tuple(
            section
            for group in stage
            for section in _sections_for(group, spec, box, registry, lab_map, ids, deadline)
        )
        for stage in _production_layers(spec)
    )
    sections = _arrange(stages, box, registry, deadline)
    machines = [machine for section in sections for machine in section.placement.machines]
    assert len(machines) == 14
    assert len({machine.pose.z for machine in machines}) <= 3
    assert {machine.id: machine.clock for machine in machines} == {
        machine.id: machine.clock
        for stage in stages
        for section in stage
        for machine in section.placement.machines
    }
    for consumer in sections:
        consumer_z = min(machine.pose.z for machine in consumer.placement.machines)
        for producer in sections:
            if producer.group.row_outputs.keys() & consumer.group.row_inputs.keys():
                assert max(machine.pose.z for machine in producer.placement.machines) <= consumer_z
    frames = [(_section_frame(section, registry)[0], section) for section in sections]
    for (low, high), section in frames:
        assert all(-box.half_cm <= low[axis] <= high[axis] <= box.half_cm for axis in (0, 1))
        assert 0 <= low[2] <= high[2] <= box.height_cm
        for (other_low, other_high), other in frames:
            if other is section or other.placement.machines[0].pose.z != (
                section.placement.machines[0].pose.z
            ):
                continue
            assert any(
                high[axis] + 200 <= other_low[axis] or other_high[axis] + 200 <= low[axis]
                for axis in (0, 1)
            )


@pytest.mark.parametrize(
    ("machine", "recipe", "count", "inputs", "outputs"),
    [
        (
            "Build_AssemblerMk1_C",
            "Recipe_IronPlateReinforced_C",
            6,
            {"iron-plate": Fraction(1), "screw": Fraction(2)},
            {"reinforced-iron-plate": Fraction(1, 4)},
        ),
        (
            "Build_ManufacturerMk1_C",
            "Recipe_Computer_C",
            4,
            {
                "circuit-board": Fraction(1, 3),
                "cable": Fraction(2, 3),
                "plastic": Fraction(1),
                "screw": Fraction(4, 3),
            },
            {"computer": Fraction(1, 24)},
        ),
    ],
)
def test_full_rate_multi_input_factories_keep_every_machine_connected(
    machine: str,
    recipe: str,
    count: int,
    inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
) -> None:
    group = _group(machine, recipe, next(iter(inputs)), next(iter(outputs))).model_copy(
        update={"count": count, "inputs_per_machine": inputs, "outputs_per_machine": outputs}
    )
    spec = SfyBuildSpec(
        groups=(group,),
        external_inputs=group.row_inputs,
        outputs=group.row_outputs,
        belt_item_id="conveyor-belt-mk6",
        belt_items_per_second=Fraction(20),
    )
    registry = sfy_registry()
    placement = SectionLayout().lay_out(
        spec, designer("mk3", registry), registry=registry, lab_map=load_lab_map()
    )
    assert len(placement.machines) == count
    report = validate(placement, spec, registry)
    assert not report.errors, [finding.message for finding in report.errors]


def test_native_boundary_fanout_preserves_three_unequal_section_flows() -> None:
    groups = tuple(
        _group("Build_ConstructorMk1_C", recipe, "iron-ingot", product).model_copy(
            update={
                "inputs_per_machine": {"iron-ingot": consumed},
                "outputs_per_machine": {product: produced},
            }
        )
        for recipe, product, consumed, produced in (
            ("Recipe_IronPlate_C", "iron-plate", Fraction(1, 2), Fraction(1, 3)),
            ("Recipe_IronRod_C", "iron-rod", Fraction(1, 4), Fraction(1, 4)),
            ("Recipe_Alternate_Screw_C", "screw", Fraction(5, 24), Fraction(5, 6)),
        )
    )
    spec = SfyBuildSpec(
        groups=groups,
        external_inputs={"iron-ingot": Fraction(23, 24)},
        outputs={item: rate for group in groups for item, rate in group.row_outputs.items()},
        belt_item_id="conveyor-belt-mk6",
        belt_items_per_second=Fraction(20),
    )
    registry = sfy_registry()
    placement = SectionLayout().lay_out(
        spec, designer("mk3", registry), registry=registry, lab_map=load_lab_map()
    )
    assert len(placement.machines) == len(groups)
    report = validate(placement, spec, registry)
    assert not report.errors, [finding.message for finding in report.errors]


def test_adjacent_independent_sections_keep_their_input_approaches_accessible() -> None:
    iron = _group("Build_SmelterMk1_C", "Recipe_IngotIron_C", "iron-ore", "iron-ingot")
    copper = _group("Build_SmelterMk1_C", "Recipe_IngotCopper_C", "copper-ore", "copper-ingot")
    spec = SfyBuildSpec(
        groups=(iron, copper),
        external_inputs={**iron.row_inputs, **copper.row_inputs},
        outputs={**iron.row_outputs, **copper.row_outputs},
        belt_item_id="conveyor-belt-mk6",
        belt_items_per_second=Fraction(20),
    )
    registry = sfy_registry()
    placement = SectionLayout().lay_out(
        spec, designer("mk3", registry), registry=registry, lab_map=load_lab_map()
    )
    assert {
        lane.item_id: lane.input_per_second
        for lane in placement.stack_lanes
        if lane.input_per_second
    } == spec.external_inputs
    assert {
        lane.item_id: lane.output_per_second
        for lane in placement.stack_lanes
        if lane.output_per_second
    } == spec.outputs
    assert len(placement.stack_lanes) == 4
    linked = {end for link in placement.links for end in (link.a, link.b)}
    for lane in placement.stack_lanes:
        bottom, bottom_normal = endpoint(placement, *lane.bottom, registry)
        top, top_normal = endpoint(placement, *lane.top, registry)
        assert lane.bottom not in linked
        assert lane.top not in linked
        assert bottom_normal == pytest.approx((0, 0, -1), abs=1e-6)
        assert top_normal == pytest.approx((0, 0, 1), abs=1e-6)
        assert bottom[:2] == pytest.approx(top[:2], abs=0.001)
        assert bottom[2] < top[2]
        assert bottom[2] + placement.stack_height_cm - top[2] == pytest.approx(400)
    report = validate(placement, spec, registry)
    assert not report.errors, [finding.message for finding in report.errors]


@pytest.mark.parametrize("machines_per_recipe", [1, 3])
def test_transport_uses_demand_not_the_available_upgrade_ceiling(
    machines_per_recipe: int,
) -> None:
    registry = sfy_registry()
    original = flow_spec("iron-plate-60")
    spec = original.model_copy(
        update={
            "groups": tuple(
                group.model_copy(update={"count": machines_per_recipe}) for group in original.groups
            ),
            "external_inputs": {"iron-ore": Fraction(machines_per_recipe, 2)},
            "outputs": {"iron-plate": Fraction(machines_per_recipe, 3)},
        }
    )
    placement = SectionLayout().lay_out(
        spec,
        designer("mk1" if machines_per_recipe == 1 else "mk3", registry),
        registry=registry,
        lab_map=load_lab_map(),
    )
    lanes = {lane.item_id: lane for lane in placement.stack_lanes}
    expected_capacity = Fraction(1 if machines_per_recipe == 1 else 2)
    assert lanes["iron-ore"].capacity_per_second == expected_capacity
    assert lanes["iron-plate"].capacity_per_second == 1
    allowed_belts = {"Build_ConveyorBeltMk1_C"}
    allowed_lifts = {"Build_ConveyorLiftMk1_C"}
    if machines_per_recipe == 3:
        allowed_belts.add("Build_ConveyorBeltMk2_C")
        allowed_lifts.add("Build_ConveyorLiftMk2_C")
    assert {belt.class_name for belt in placement.belts} <= allowed_belts
    assert {lift.class_name for lift in placement.lifts} <= allowed_lifts
    report = validate(placement, spec, registry)
    assert not report.errors, [finding.message for finding in report.errors]
