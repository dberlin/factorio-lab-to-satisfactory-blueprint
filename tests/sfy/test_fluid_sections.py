"""Consumer-visible mixed recipe networks retain every material and its rate."""

from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from itertools import count
from math import inf

import pytest

from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.model import BeltRun, PipeRun
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.sections.compose import SectionLayout
from flab2bp.sfy.sections.fluids import build_fluid_section
from flab2bp.sfy.sections.model import ProductionSection, SectionError, endpoint
from flab2bp.sfy.spec import PipeTier, SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.spec import BeltTier


@pytest.fixture(scope="module")
def resources() -> tuple[Registry, LabMap]:
    return load_registry(), load_lab_map()


def plastic(*, machines: int = 1, last_clock: Fraction = Fraction(1)) -> SfyMachineGroup:
    return SfyMachineGroup(
        recipe_id="plastic",
        recipe_class="Recipe_Plastic_C",
        machine_item_id="refinery",
        machine_class="Build_OilRefinery_C",
        count=machines,
        clock=Fraction(1),
        last_clock=last_clock,
        max_clock=Fraction(5, 2),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine={"crude-oil": Fraction(1, 2)},
        outputs_per_machine={"plastic": Fraction(1, 3), "heavy-oil-residue": Fraction(1, 6)},
        power_mw_per_machine=30,
        last_power_mw=30,
    )


def build(
    group: SfyMachineGroup, resources: tuple[Registry, LabMap], *, capacity: Fraction = Fraction(5)
) -> ProductionSection:
    registry, lab_map = resources
    return build_fluid_section(
        group,
        designer("mk3", registry),
        registry,
        lab_map,
        belt_tiers=(BeltTier(item_id="conveyor-belt-mk6", items_per_second=Fraction(20)),),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=capacity),),
        fluid_items=frozenset({"crude-oil", "heavy-oil-residue", "fuel"}),
        ids=count(1),
        deadline=inf,
    )


def material_networks(section: ProductionSection) -> dict[str, set[tuple[int, str]]]:
    machines = {actor.id for actor in section.placement.machines}
    neighbours: defaultdict[int, set[int]] = defaultdict(set)
    terminal: defaultdict[int, set[tuple[int, str]]] = defaultdict(set)
    for link in section.placement.links:
        for here, there in ((link.a, link.b), (link.b, link.a)):
            if there[0] in machines:
                terminal[here[0]].add(there)
            elif here[0] not in machines:
                neighbours[here[0]].add(there[0])
    result = {}
    occupied: set[int] = set()
    for port in (*section.inputs, *section.outputs):
        reached: set[int] = set()
        pending = [port.object_id]
        while pending:
            node = pending.pop()
            if node not in reached:
                reached.add(node)
                pending.extend(neighbours[node] - reached)
        assert not occupied.intersection(reached), "different materials share a transport network"
        occupied.update(reached)
        result[port.item_id] = {ref for node in reached for ref in terminal[node]}
        runs = [section.placement.by_id(node) for node in reached]
        assert {run.item_id for run in runs if isinstance(run, (BeltRun, PipeRun))} == {
            port.item_id
        }
    return result


def test_standard_plastic_retains_independent_residue_collector(
    resources: tuple[Registry, LabMap],
) -> None:
    section = build(plastic(), resources)
    assert {(port.item_id, port.kind, port.items_per_second) for port in section.inputs} == {
        ("crude-oil", "pipe", Fraction(1, 2)),
    }
    assert {(port.item_id, port.kind, port.items_per_second) for port in section.outputs} == {
        ("plastic", "belt", Fraction(1, 3)),
        ("heavy-oil-residue", "pipe", Fraction(1, 6)),
    }
    machine = section.placement.machines[0]
    assert material_networks(section) == {
        "crude-oil": {(machine.id, "PipeInputFactory")},
        "plastic": {(machine.id, "Output1")},
        "heavy-oil-residue": {(machine.id, "PipeOutputFactory")},
    }
    registry, _ = resources
    for link in section.placement.links:
        a, normal_a = endpoint(section.placement, *link.a, registry)
        b, normal_b = endpoint(section.placement, *link.b, registry)
        assert a == pytest.approx(b, abs=0.01)
        assert sum(x * y for x, y in zip(normal_a, normal_b, strict=True)) < -0.999
    assert section.bounds[1][2] >= machine.pose.z + 3000


@pytest.mark.parametrize(
    "product,residue_rate", [("plastic", Fraction(1, 6)), ("rubber", Fraction(1, 3))]
)
def test_four_refineries_fit_mk2_with_connected_material_networks(
    resources: tuple[Registry, LabMap], product: str, residue_rate: Fraction
) -> None:
    registry, lab_map = resources
    group = plastic(machines=4).model_copy(
        update={
            "recipe_id": product,
            "recipe_class": f"Recipe_{product.capitalize()}_C",
            "outputs_per_machine": {product: Fraction(1, 3), "heavy-oil-residue": residue_rate},
        }
    )
    frame = designer("mk2", registry)
    section = build_fluid_section(
        group,
        frame,
        registry,
        lab_map,
        belt_tiers=(BeltTier(item_id="conveyor-belt-mk6", items_per_second=Fraction(20)),),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),),
        fluid_items=frozenset({"crude-oil", "heavy-oil-residue"}),
        ids=count(1),
        deadline=inf,
    )
    low, high = section.bounds
    assert all(high[axis] - low[axis] <= 2 * frame.half_cm for axis in (0, 1))
    assert 0 <= low[2] <= high[2] <= frame.height_cm
    machines = {actor.id for actor in section.placement.machines}
    assert len(machines) == 4
    assert material_networks(section) == {
        "crude-oil": {(actor, "PipeInputFactory") for actor in machines},
        product: {(actor, "Output1") for actor in machines},
        "heavy-oil-residue": {(actor, "PipeOutputFactory") for actor in machines},
    }
    assert {port.item_id: port.items_per_second for port in section.inputs} == group.row_inputs
    assert {port.item_id: port.items_per_second for port in section.outputs} == group.row_outputs
    report = validate(
        section.placement,
        None,
        registry,
        only={
            "geom.bounds",
            "geom.hard_clearance",
            "belt.capsule",
            "belt.min_length",
            "pipe.capsule",
            "pipe.min_length",
            "pipe.max_length",
            "pipe.curvature",
            "pipe.fluid_requirements",
            "ports.position",
            "ports.direction",
        },
    )
    assert report.ok, report.errors


def test_four_refinery_factory_retains_legal_routes_inside_mk2(
    resources: tuple[Registry, LabMap],
) -> None:
    registry, lab_map = resources
    group = plastic(machines=4)
    spec = SfyBuildSpec(
        groups=(group,),
        external_inputs=group.row_inputs,
        outputs={"plastic": group.row_outputs["plastic"]},
        surplus_outputs={"heavy-oil-residue": group.row_outputs["heavy-oil-residue"]},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        belt_upgrades=(BeltTier(item_id="conveyor-belt-mk2", items_per_second=Fraction(2)),),
        fluid_items=frozenset({"crude-oil", "heavy-oil-residue"}),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),),
    )
    placement = SectionLayout().lay_out(
        spec, designer("mk2", registry), registry=registry, lab_map=lab_map
    )
    assert len(placement.machines) == 4
    assert len({machine.pose.z for machine in placement.machines}) == 1
    assert {
        lane.item_id: lane.input_per_second
        for lane in placement.stack_lanes
        if lane.input_per_second
    } == spec.external_inputs
    assert {
        lane.item_id: lane.output_per_second
        for lane in placement.stack_lanes
        if lane.output_per_second
    } == {**spec.outputs, **spec.surplus_outputs}
    report = validate(placement, spec, registry)
    assert report.ok, report.errors


def test_half_clock_last_refinery_changes_every_connected_material_rate(
    resources: tuple[Registry, LabMap],
) -> None:
    section = build(plastic(machines=3, last_clock=Fraction(1, 2)), resources)
    assert [machine.clock for machine in section.placement.machines] == [1, 1, Fraction(1, 2)]
    assert {
        port.item_id: port.items_per_second for port in (*section.inputs, *section.outputs)
    } == {
        "crude-oil": Fraction(5, 4),
        "plastic": Fraction(5, 6),
        "heavy-oil-residue": Fraction(5, 12),
    }
    machines = {machine.id for machine in section.placement.machines}
    assert all({ref[0] for ref in refs} == machines for refs in material_networks(section).values())


def test_recipe_order_is_not_material_dictionary_order(resources: tuple[Registry, LabMap]) -> None:
    registry, lab_map = resources
    recipe = registry.recipes["Recipe_Plastic_C"]
    recipe = replace(
        recipe,
        ingredients=(("Desc_Rubber_C", 6), ("Desc_LiquidFuel_C", 6)),
        products=(("Desc_Plastic_C", 12),),
    )
    registry = replace(registry, recipes={**registry.recipes, recipe.class_name: recipe})
    requested = plastic().model_copy(
        update={
            "inputs_per_machine": {"fuel": Fraction(1, 2), "rubber": Fraction(1, 2)},
            "outputs_per_machine": {"plastic": Fraction(1)},
        }
    )
    section = build(requested, (registry, lab_map))
    machine = section.placement.machines[0]
    assert material_networks(section) == {
        "fuel": {(machine.id, "PipeInputFactory")},
        "rubber": {(machine.id, "Input0")},
        "plastic": {(machine.id, "Output1")},
    }


def test_fluid_trunk_capacity_accounts_for_all_machines(resources: tuple[Registry, LabMap]) -> None:
    with pytest.raises(SectionError) as caught:
        build(plastic(machines=3), resources, capacity=Fraction(1))
    assert caught.value.cause == "capacity"


def test_missing_recipe_byproduct_is_refused_not_discarded(
    resources: tuple[Registry, LabMap],
) -> None:
    requested = plastic().model_copy(update={"outputs_per_machine": {"plastic": Fraction(1, 3)}})
    with pytest.raises(SectionError) as caught:
        build(requested, resources)
    assert caught.value.cause == "unsupported"


def test_plastic_ten_uses_half_clock_without_losing_residue(
    resources: tuple[Registry, LabMap],
) -> None:
    section = build(plastic(last_clock=Fraction(1, 2)), resources)
    assert [machine.clock for machine in section.placement.machines] == [Fraction(1, 2)]
    assert {
        port.item_id: port.items_per_second for port in (*section.inputs, *section.outputs)
    } == {
        "crude-oil": Fraction(1, 4),
        "plastic": Fraction(1, 6),
        "heavy-oil-residue": Fraction(1, 12),
    }
    assert set(material_networks(section)) == {"crude-oil", "plastic", "heavy-oil-residue"}


def test_multiple_fluid_ports_keep_recipe_order_and_disjoint_collectors(
    resources: tuple[Registry, LabMap],
) -> None:
    registry, lab_map = resources
    machine = registry.buildables["Build_OilRefinery_C"]
    fluid = next(port for port in machine.ports if port.name == "PipeInputFactory")
    extra = replace(fluid, name="SecondPipeInput", translation=(200, 900, 175))
    machine = replace(machine, ports=(*machine.ports, extra))
    recipe = registry.recipes["Recipe_Plastic_C"]
    recipe = replace(
        recipe,
        ingredients=(("Desc_LiquidFuel_C", 1000), ("Desc_LiquidOil_C", 3000)),
    )
    registry = replace(
        registry,
        buildables={**registry.buildables, machine.class_name: machine},
        recipes={**registry.recipes, recipe.class_name: recipe},
    )
    requested = plastic().model_copy(
        update={
            "inputs_per_machine": {"crude-oil": Fraction(1, 2), "fuel": Fraction(1, 6)},
        }
    )
    section = build(requested, (registry, lab_map))
    actor = section.placement.machines[0]
    assert material_networks(section) == {
        "fuel": {(actor.id, fluid.name)},
        "crude-oil": {(actor.id, extra.name)},
        "plastic": {(actor.id, "Output1")},
        "heavy-oil-residue": {(actor.id, "PipeOutputFactory")},
    }


def test_unsupported_pipe_face_refuses_instead_of_guessing(
    resources: tuple[Registry, LabMap],
) -> None:
    registry, lab_map = resources
    machine = registry.buildables["Build_OilRefinery_C"]
    machine = replace(
        machine,
        ports=tuple(
            replace(port, rotation=(0, 0, 0)) if port.name == "PipeInputFactory" else port
            for port in machine.ports
        ),
    )
    registry = replace(registry, buildables={**registry.buildables, machine.class_name: machine})
    with pytest.raises(SectionError) as caught:
        build(plastic(), (registry, lab_map))
    assert caught.value.cause == "unsupported"


def test_multiple_fluid_collectors_do_not_require_unproven_factory_head(
    resources: tuple[Registry, LabMap],
) -> None:
    registry, lab_map = resources
    machine = registry.buildables["Build_OilRefinery_C"]
    fluid = next(port for port in machine.ports if port.name == "PipeOutputFactory")
    extra = replace(fluid, name="SecondPipeOutput", translation=(0, -900, 175))
    machine = replace(machine, ports=(*machine.ports, extra))
    recipe = registry.recipes["Recipe_Plastic_C"]
    recipe = replace(recipe, products=(*recipe.products, ("Desc_LiquidFuel_C", 500)))
    registry = replace(
        registry,
        buildables={**registry.buildables, machine.class_name: machine},
        recipes={**registry.recipes, recipe.class_name: recipe},
    )
    requested = plastic().model_copy(
        update={
            "outputs_per_machine": {
                "plastic": Fraction(1, 3),
                "heavy-oil-residue": Fraction(1, 6),
                "fuel": Fraction(1, 12),
            },
        }
    )
    section = build(requested, (registry, lab_map))
    actor = section.placement.machines[0]
    networks = material_networks(section)
    assert networks["heavy-oil-residue"] == {(actor.id, fluid.name)}
    assert networks["fuel"] == {(actor.id, extra.name)}
    pipe = registry.buildables["Build_Pipeline_NoIndicator_C"]
    assert pipe.mesh_length_cm is not None
    foundation = registry.buildables["Build_Foundation_8x1_01_C"]
    assert foundation.height_cm is not None
    junction = registry.buildables["Build_PipelineJunction_Cross_C"]
    span = max(p.translation[0] for p in junction.ports) - min(
        p.translation[0] for p in junction.ports
    )
    minimum_inlet = foundation.height_cm / 2 + pipe.mesh_length_cm / 2 + span
    for port in section.outputs:
        if port.kind != "pipe":
            continue
        point, _ = endpoint(section.placement, port.object_id, port.port, registry)
        assert point[2] > minimum_inlet
        assert all(
            p[0][2] <= actor.pose.z + fluid.translation[2]
            for run in section.placement.pipes
            if run.item_id == port.item_id
            for p in run.points
        )
