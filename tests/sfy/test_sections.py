"""Rates and separated ingredient networks of complete production sections."""

from collections import Counter, defaultdict
from dataclasses import replace
from fractions import Fraction
from itertools import count
from math import inf

import pytest

from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.model import BeltRun
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.sections.construction import build_section
from flab2bp.sfy.sections.model import ProductionSection, SectionError
from flab2bp.sfy.spec import Designer, SfyMachineGroup, designer
from flab2bp.spec import BeltTier

type Resources = tuple[Registry, LabMap, Designer]


@pytest.fixture(scope="module")
def resources() -> Resources:
    registry = load_registry()
    return registry, load_lab_map(), designer("mk3", registry)


def group(
    machine: str,
    recipe: str,
    machines: int,
    inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
    last_clock: Fraction = Fraction(1),
) -> SfyMachineGroup:
    return SfyMachineGroup(
        recipe_id=recipe,
        recipe_class=recipe,
        machine_item_id="machine",
        machine_class=machine,
        count=machines,
        clock=Fraction(1),
        last_clock=last_clock,
        max_clock=Fraction(5, 2),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine=inputs,
        outputs_per_machine=outputs,
        power_mw_per_machine=0,
        last_power_mw=0,
    )


def build(
    group_: SfyMachineGroup, resources: Resources, capacity: Fraction = Fraction(20)
) -> ProductionSection:
    registry, lab_map, box = resources
    return build_section(
        group_,
        box,
        registry,
        lab_map,
        belt_tiers=(BeltTier(item_id="conveyor-belt-mk6", items_per_second=capacity),),
        ids=count(1),
        deadline=inf,
    )


def test_odd_opposing_rows_preserve_last_machine_and_collected_rate(resources: Resources) -> None:
    requested = group(
        "Build_ConstructorMk1_C",
        "Recipe_IronRod_C",
        5,
        {"iron-ingot": Fraction(1, 4)},
        {"iron-rod": Fraction(1, 4)},
        last_clock=Fraction(1, 3),
    )
    section = build(requested, resources)
    clocks = Counter(machine.clock for machine in section.placement.machines)
    assert clocks == {Fraction(1): 4, Fraction(1, 3): 1}
    assert sorted(
        Counter(machine.pose.yaw_deg for machine in section.placement.machines).values()
    ) == [2, 3]
    assert [(port.item_id, port.items_per_second) for port in section.inputs] == [
        ("iron-ingot", Fraction(13, 12))
    ]
    assert [(port.item_id, port.items_per_second) for port in section.outputs] == [
        ("iron-rod", Fraction(13, 12))
    ]
    # Every machine port is supplied/drained at that machine's exact clock.
    neighbours: defaultdict[int, list[int]] = defaultdict(list)
    for link in section.placement.links:
        neighbours[link.a[0]].append(link.b[0])
        neighbours[link.b[0]].append(link.a[0])
    for machine in section.placement.machines:
        rates = [
            obj.items_per_second
            for n in neighbours[machine.id]
            if isinstance(obj := section.placement.by_id(n), BeltRun)
        ]
        assert rates == [machine.clock / 4, machine.clock / 4]


def test_manufacturer_ingredients_remain_disjoint_until_machine_ports(resources: Resources) -> None:
    requested = group(
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
    )
    section = build(requested, resources)
    machines = {machine.id for machine in section.placement.machines}
    adjacency: defaultdict[int, set[int]] = defaultdict(set)
    machine_ports: defaultdict[int, set[tuple[int, str]]] = defaultdict(set)
    for link in section.placement.links:
        for here, there in ((link.a, link.b), (link.b, link.a)):
            if there[0] in machines:
                machine_ports[here[0]].add(there)
            elif here[0] not in machines:
                adjacency[here[0]].add(there[0])
    networks: list[set[int]] = []
    all_machine_inputs: set[tuple[int, str]] = set()
    for port in section.inputs:
        reached: set[int] = set()
        pending = [port.object_id]
        while pending:
            node = pending.pop()
            if node in reached:
                continue
            reached.add(node)
            pending.extend(adjacency[node] - reached)
        assert all(not (reached & previous) for previous in networks)
        networks.append(reached)
        ports = {ref for node in reached for ref in machine_ports[node]}
        assert {object_id for object_id, _ in ports} == machines
        assert len(ports) == len(machines)
        assert not (ports & all_machine_inputs)
        all_machine_inputs.update(ports)
        assert {belt.item_id for belt in section.placement.belts if belt.id in reached} == {
            port.item_id
        }
        assert port.items_per_second == requested.row_inputs[port.item_id]
    assert len(section.outputs) == 1


def test_capacity_refuses_combined_spine_even_when_each_row_fits(resources: Resources) -> None:
    requested = group(
        "Build_ConstructorMk1_C",
        "Recipe_IronRod_C",
        6,
        {"iron-ingot": Fraction(1, 4)},
        {"iron-rod": Fraction(1, 4)},
    )
    with pytest.raises(SectionError) as caught:
        build(requested, resources, capacity=Fraction(1))
    assert caught.value.cause == "capacity"


def test_multiple_supported_product_ports_do_not_duplicate_production(resources: Resources) -> None:
    registry, lab_map, box = resources
    machine = registry.buildables["Build_ConstructorMk1_C"]
    output = next(
        port for port in machine.ports if port.kind == "belt" and port.direction == "output"
    )
    # An otherwise ordinary production building with two actual solid outputs.
    # The shipped target machines have one; the contract must not clone machines
    # if a registry-backed machine provides more than one.
    machine = replace(
        machine,
        ports=(
            *(port for port in machine.ports if port != output),
            replace(output, name="ProductA", translation=(-200, 300, 100)),
            replace(output, name="ProductB", translation=(200, 300, 100)),
        ),
    )
    registry = replace(registry, buildables={**registry.buildables, machine.class_name: machine})
    requested = group(
        machine.class_name,
        "Recipe_IronRod_C",
        2,
        {"iron-ingot": Fraction(1, 4)},
        {"iron-rod": Fraction(1, 8), "iron-plate": Fraction(1, 16)},
    )
    section = build(requested, (registry, lab_map, box))
    assert len(section.placement.machines) == requested.count
    assert {
        port.item_id: port.items_per_second for port in section.outputs
    } == requested.row_outputs
    assert len({port.object_id for port in section.outputs}) == 2
