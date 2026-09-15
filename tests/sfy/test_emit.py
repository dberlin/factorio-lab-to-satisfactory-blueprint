"""Turning a placement into a blueprint, and reading the placement back out.

The reference the emitter is held to is the blueprint ``scripts/sfy_checkpoint1.py``
authors object by object: the same four foundations, Constructor, belt and pole,
built here through the template API the way that script builds them. What the
emitter writes must be that file, not merely something like it.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from functools import cache

import pytest

from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import Blueprint
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.header import BlueprintHeader, read_header
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.emit import FIRST_NAME_ID, EmitError, decode, emit
from flab2bp.sfy.layout.model import (
    BeltRun,
    FoundationObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    SfyPlacement,
    WireObj,
    belt_ends,
)
from flab2bp.sfy.layout.splines import straight
from flab2bp.sfy.objects import ObjectData, ObjectHeader, Transform
from flab2bp.sfy.query import connected, object_index
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.spec import designer
from flab2bp.sfy.templates import (
    ACTOR_PATH_PREFIX,
    TemplateError,
    TemplateLibrary,
    apply_recipe,
    assemble,
    connect,
    set_spline,
    straight_spline,
)
from tests.sfy.conftest import fixture_paths

FOUNDATION = "Build_Foundation_8x1_01_C"
CONSTRUCTOR = "Build_ConstructorMk1_C"
BELT = "Build_ConveyorBeltMk1_C"
POLE = "Build_PowerPoleMk1_C"
POWER_LINE = "Build_PowerLine_C"
IRON_PLATE = "Recipe_IronPlate_C"

DIMENSIONS = (4, 4, 4)
FOUNDATION_HALF_CM = 400.0
FOUNDATION_Z_CM = 50.0
SLAB_TOP_CM = 100.0
POLE_X_CM = 700.0
BELT_LENGTH_CM = 400.0


@cache
def _library() -> TemplateLibrary:
    """The fixture library, built once: scanning 49 blueprints costs seconds."""
    return TemplateLibrary.from_fixtures(fixture_paths())


@cache
def _newest_fixture_header() -> BlueprintHeader:
    """The newest corpus header, whose build version the authored file claims."""
    headers = [read_header(Reader(p.read_bytes())) for p in fixture_paths()]
    return max(headers, key=lambda h: (h.save_version, h.build_version))


def _port(registry: Registry, class_name: str, port_name: str) -> Port:
    return next(p for p in registry.buildables[class_name].ports if p.name == port_name)


def _placement() -> SfyPlacement:
    """Checkpoint 1 as a placement: four foundations, a Constructor, a belt, a pole."""
    registry = load_registry()
    machine_pose = Pose(0.0, 0.0, SLAB_TOP_CM, 0.0)
    output = _port(registry, CONSTRUCTOR, "Output0")
    start = world_port(machine_pose.transform(), output)
    facing = port_forward(machine_pose.transform(), output)
    foundations = tuple(
        FoundationObj(i, FOUNDATION, Pose(x, y, FOUNDATION_Z_CM, 0.0))
        for i, (x, y) in enumerate(
            (x, y)
            for x in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM)
            for y in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM)
        )
    )
    belt = BeltRun(5, BELT, straight(start, facing, BELT_LENGTH_CM), "iron-plate", Fraction(1))
    entry, _ = belt_ends(registry, BELT)
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(4, CONSTRUCTOR, machine_pose, IRON_PLATE),),
        belts=(belt,),
        poles=(PoleObj(6, POLE, Pose(POLE_X_CM, 0.0, SLAB_TOP_CM, 0.0)),),
        foundations=foundations,
        links=(Link((4, "Output0"), (5, entry)),),
    )


def _emit(placement: SfyPlacement) -> Blueprint:
    newest = _newest_fixture_header()
    return emit(
        placement,
        load_registry(),
        _library(),
        load_lab_map(),
        build_version=newest.build_version,
        version_data=newest.version_data,
    )


def _checkpoint_by_hand() -> Blueprint:
    """``scripts/sfy_checkpoint1.py``'s ``build()``, object for object."""
    library = _library()
    registry = load_registry()
    newest = _newest_fixture_header()
    ids = iter(range(FIRST_NAME_ID, FIRST_NAME_ID + 16))

    def at(x: float, y: float, z: float) -> Transform:
        return Transform((0.0, 0.0, 0.0, 1.0), (x, y, z), (1.0, 1.0, 1.0))

    objects: list[tuple[ObjectHeader, ObjectData]] = []
    for x in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM):
        for y in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM):
            objects += library.instantiate(FOUNDATION, next(ids), at(x, y, FOUNDATION_Z_CM))

    constructor_at = at(0.0, 0.0, SLAB_TOP_CM)
    constructor = apply_recipe(
        library.instantiate(CONSTRUCTOR, next(ids), constructor_at),
        registry.recipe_paths[IRON_PLATE],
        registry,
    )
    output = _port(registry, CONSTRUCTOR, "Output0")
    start = world_port(constructor_at, output)
    facing = port_forward(constructor_at, output)
    belt = library.instantiate(BELT, next(ids), at(*start))
    belt = ((belt[0][0], set_spline(belt[0][1], straight_spline(facing, BELT_LENGTH_CM))),) + tuple(
        belt[1:]
    )
    entry, _ = belt_ends(registry, BELT)
    port_header, port_data = next((h, d) for h, d in constructor[1:] if h.name == "Output0")
    belt_header, belt_data = next((h, d) for h, d in belt[1:] if h.name == entry)
    port_data, belt_data = connect(port_data, port_header.path, belt_data, belt_header.path)
    constructor = tuple((h, port_data if h.path == port_header.path else d) for h, d in constructor)
    belt = tuple((h, belt_data if h.path == belt_header.path else d) for h, d in belt)

    objects += constructor
    objects += belt
    objects += library.instantiate(POLE, next(ids), at(POLE_X_CM, 0.0, SLAB_TOP_CM))
    return assemble(
        tuple(objects),
        DIMENSIONS,
        registry,
        build_version=newest.build_version,
        version_data=newest.version_data,
    )


def test_the_emitter_writes_the_blueprint_the_checkpoint_script_writes_by_hand() -> None:
    assert _emit(_placement()) == _checkpoint_by_hand()


def test_the_emitted_cost_is_the_checkpoint_cost() -> None:
    built = _emit(_placement())
    assert built.header.cost == _checkpoint_by_hand().header.cost
    # What the four foundations, the Constructor, two belt segments and the pole
    # cost, each actor's own build recipe through the registry.
    assert [(entry.item.name, entry.amount) for entry in built.header.cost] == [
        ("Desc_Cement_C", 21),
        ("Desc_IronPlateReinforced_C", 2),
        ("Desc_Cable_C", 8),
        ("Desc_IronPlate_C", 2),
        ("Desc_Wire_C", 3),
        ("Desc_IronRod_C", 1),
    ]
    assert [recipe.name for recipe in built.header.recipes] == [
        "Recipe_Foundation_8x1_01_C",
        "Recipe_ConstructorMk1_C",
        "Recipe_ConveyorBeltMk1_C",
        "Recipe_PowerPoleMk1_C",
    ]


def test_a_placement_comes_back_out_of_the_blueprint_it_was_written_into() -> None:
    placement = _placement()
    assert decode(_emit(placement), load_registry()) == placement


def test_the_belt_is_wired_to_the_port_by_the_end_the_registry_calls_the_entry() -> None:
    """The Constructor feeds the belt, so the end on its output is ``flow.entry``."""
    registry = load_registry()
    entry, exit_end = belt_ends(registry, BELT)
    built = _emit(_placement())
    index = object_index(built)
    belt = next(h for h, _ in built.objects if h.class_name == BELT)
    machine = next(h for h, _ in built.objects if h.class_name == CONSTRUCTOR)
    wired = connected(index[f"{belt.path}.{entry}"][1])
    assert wired is not None
    assert wired.path == f"{machine.path}.Output0"
    assert connected(index[f"{belt.path}.{exit_end}"][1]) is None
    back = connected(index[f"{machine.path}.Output0"][1])
    assert back is not None and back.path == f"{belt.path}.{entry}"


def test_the_decoded_links_name_the_upstream_port_first() -> None:
    placement = decode(_emit(_placement()), load_registry())
    entry, _ = belt_ends(load_registry(), BELT)
    assert placement.links == (Link((4, "Output0"), (5, entry)),)


def test_a_machines_clock_is_not_written_because_no_property_for_it_was_read() -> None:
    """Task 4 found no save property the game writes a requested potential into.

    Until one is read, the clock lives in the placement's description and
    nowhere in the file, which is exactly what this asserts: two placements that
    differ only in their clocks emit the same bytes.
    """
    placement = _placement()
    machine = placement.machines[0]
    underclocked = MachineObj(
        machine.id, machine.class_name, machine.pose, machine.recipe_class, Fraction(1, 2), 1
    )
    assert _emit(replace(placement, machines=(underclocked,))) == _emit(placement)


def test_emit_refuses_a_class_the_fixture_library_has_no_template_for() -> None:
    registry = load_registry()
    placement = SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(1, "Build_HadronCollider_C", Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE),),
    )
    with pytest.raises(TemplateError, match="Build_HadronCollider_C"):
        _emit(placement)


def _two_poles() -> SfyPlacement:
    """Two poles a wire apart, which is the smallest placement with a wire in it."""
    registry = load_registry()
    return SfyPlacement(
        designer=designer("mk1", registry),
        poles=(
            PoleObj(1, POLE, Pose(-POLE_X_CM, 0.0, SLAB_TOP_CM, 0.0)),
            PoleObj(2, POLE, Pose(POLE_X_CM, 0.0, SLAB_TOP_CM, 0.0)),
        ),
        wires=(WireObj(3, POWER_LINE, Link((1, "PowerConnection"), (2, "PowerConnection"))),),
    )


def test_a_wire_carries_the_two_connection_references_the_game_writes_in_its_trailer() -> None:
    """The trailer is the wire's whole record of what it joins -- Task 9a's reading."""
    blueprint = _emit(_two_poles())
    wire = next(d for h, d in blueprint.objects if h.class_name == POWER_LINE)
    assert [ref.path for ref in wire.trailer.connections] == [
        f"{ACTOR_PATH_PREFIX}{POLE}_{FIRST_NAME_ID + 1}.PowerConnection",
        f"{ACTOR_PATH_PREFIX}{POLE}_{FIRST_NAME_ID + 2}.PowerConnection",
    ]


def test_a_wire_stands_on_the_second_connection_it_names() -> None:
    """The corpus's own convention; ``emit._wire_stand`` gives the figures."""
    registry = load_registry()
    placement = _two_poles()
    blueprint = _emit(placement)
    header = next(h for h, _ in blueprint.objects if h.class_name == POWER_LINE)
    port = _port(registry, POLE, "PowerConnection")
    assert header.transform is not None
    assert header.transform.translation == pytest.approx(
        world_port(placement.poles[1].pose.transform(), port)
    )


def test_a_placement_with_a_wire_comes_back_out_of_the_file_unchanged() -> None:
    placement = _two_poles()
    assert decode(_emit(placement), load_registry()) == placement


def test_emit_refuses_a_wire_that_joins_a_connection_to_itself() -> None:
    registry = load_registry()
    placement = SfyPlacement(
        designer=designer("mk1", registry),
        poles=(PoleObj(1, POLE, Pose(0.0, 0.0, SLAB_TOP_CM, 0.0)),),
        wires=(WireObj(2, POWER_LINE, Link((1, "PowerConnection"), (1, "PowerConnection"))),),
    )
    with pytest.raises(EmitError, match="itself"):
        _emit(placement)


def test_emit_refuses_a_link_to_a_port_the_object_does_not_have() -> None:
    placement = _placement()
    broken = replace(placement, links=(Link((4, "Output9"), (5, "ConveyorAny0")),))
    with pytest.raises(EmitError, match="Output9"):
        _emit(broken)


def test_emit_refuses_two_objects_that_share_an_id() -> None:
    """Ids name the actors, and a link names an id; two of one would wire the wrong thing."""
    placement = _placement()
    twin = replace(placement, poles=(PoleObj(4, POLE, Pose(POLE_X_CM, 0.0, SLAB_TOP_CM, 0.0)),))
    with pytest.raises(EmitError, match="share the id 4"):
        _emit(twin)


def test_decode_refuses_an_actor_the_placement_model_has_no_object_for() -> None:
    """Nothing is dropped quietly: a ladder in the file is an error, not an absence."""
    registry = load_registry()
    newest = _newest_fixture_header()
    ladder = _library().instantiate(
        "Build_Ladder_C", FIRST_NAME_ID, Pose(0.0, 0.0, 100.0, 0.0).transform()
    )
    built = assemble(
        ladder,
        DIMENSIONS,
        registry,
        build_version=newest.build_version,
        version_data=newest.version_data,
    )
    with pytest.raises(EmitError, match="Build_Ladder_C"):
        decode(built, registry)


@pytest.mark.parametrize("yaw", [45.5, 22.25, 7.125, 123.456, -30.75])
def test_a_fractional_yaw_comes_back_out_of_the_file_it_went_into(yaw: float) -> None:
    """An ``FQuat`` of four ``f32`` cannot hold an ``f64`` angle.

    ``Pose`` therefore states its yaw at the width the object table writes, and
    ``decode`` hands it back at the same width: the ``f64`` angle the stored
    components encode misses by about a part in ten million, and rounding it to
    a ``float`` lands back on the number that went in.
    """
    placement = replace(
        _placement(),
        machines=(MachineObj(4, CONSTRUCTOR, Pose(0.0, 0.0, SLAB_TOP_CM, yaw), IRON_PLATE),),
        belts=(),
        links=(),
    )
    assert placement.machines[0].pose.yaw_deg == pytest.approx(yaw, abs=1e-4)
    assert decode(_emit(placement), load_registry()) == placement
