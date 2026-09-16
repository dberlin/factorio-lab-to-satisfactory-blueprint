"""Turning a placement into a blueprint, and reading the placement back out.

The reference the emitter is held to is the blueprint ``scripts/sfy_checkpoint1.py``
authors object by object: the same four foundations, Constructor, belt and pole,
built here through the template API the way that script builds them. What the
emitter writes must be that file, not merely something like it.
"""

from __future__ import annotations

import math
from dataclasses import replace
from fractions import Fraction
from functools import cache

import pytest

from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import Blueprint, read_sbp_file
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.header import BlueprintHeader, read_header
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.emit import (
    CURRENT_POTENTIAL,
    CURRENT_PRODUCTION_BOOST,
    FIRST_NAME_ID,
    IS_REVERSED,
    PENDING_POTENTIAL,
    PENDING_PRODUCTION_BOOST,
    ROTATION,
    SNAPPED_PASSTHROUGHS,
    TOP_TRANSFORM,
    TRANSLATION,
    EmitError,
    _lift_top,
    decode,
    emit,
)
from flab2bp.sfy.layout.model import (
    BeltRun,
    FoundationObj,
    LiftObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    SfyPlacement,
    WireObj,
    belt_ends,
)
from flab2bp.sfy.layout.splines import straight, yaw_quaternion
from flab2bp.sfy.objects import ACTOR, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import Array, Float, Object, Property, Quat, Struct, Tag, Vector
from flab2bp.sfy.query import connected, find, object_index
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
from tests.sfy.conftest import FIXTURES, fixture_paths

FOUNDATION = "Build_Foundation_8x1_01_C"
CONSTRUCTOR = "Build_ConstructorMk1_C"
BELT = "Build_ConveyorBeltMk1_C"
LIFT = "Build_ConveyorLiftMk1_C"
POLE = "Build_PowerPoleMk1_C"
POWER_LINE = "Build_PowerLine_C"
IRON_PLATE = "Recipe_IronPlate_C"

DIMENSIONS = (4, 4, 4)
FOUNDATION_HALF_CM = 400.0
FOUNDATION_Z_CM = 50.0
SLAB_TOP_CM = 100.0
POLE_X_CM = 700.0
BELT_LENGTH_CM = 400.0
LIFT_HEIGHT_CM = 400.0


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


def _with_machine(clock: Fraction, somersloops: int) -> SfyPlacement:
    """Checkpoint 1 with its Constructor set to one clock and one sloop count."""
    placement = _placement()
    machine = placement.machines[0]
    return replace(
        placement,
        machines=(
            MachineObj(
                machine.id,
                machine.class_name,
                machine.pose,
                machine.recipe_class,
                clock,
                somersloops,
            ),
        ),
    )


def _constructor(built: Blueprint) -> ObjectData:
    return next(d for h, d in built.objects if h.class_name == CONSTRUCTOR and h.kind == ACTOR)


def _floats(built: Blueprint, name: str) -> list[float]:
    """Every value of one property on the Constructor in an emitted blueprint."""
    return [
        p.value.v
        for p in _constructor(built).properties
        if p.tag.name == name and isinstance(p.value, Float)
    ]


def test_a_machine_at_100_percent_with_no_sloop_writes_no_potential_at_all() -> None:
    """It is the class default, and the game does not serialise an unchanged one."""
    built = _emit(_placement())
    for name in (
        CURRENT_POTENTIAL,
        PENDING_POTENTIAL,
        CURRENT_PRODUCTION_BOOST,
        PENDING_PRODUCTION_BOOST,
    ):
        assert _floats(built, name) == []


def test_an_overclocked_machine_writes_its_potential_into_both_saved_floats() -> None:
    """250 % is ``2.5``: ``mMaxPotential`` 1.0 plus three shards of ``mExtraPotential``.

    ``FGBuildableFactory.h`` declares ``mCurrentPotential`` and
    ``mPendingPotential`` as ``SaveGame`` floats (lines 670 and 609), and both
    are written -- the pending one is what the slider sets, the current one what
    ``GetCurrentPotential`` returns before a cycle has finished.
    """
    built = _emit(_with_machine(Fraction(5, 2), 0))
    assert _floats(built, CURRENT_POTENTIAL) == [2.5]
    assert _floats(built, PENDING_POTENTIAL) == [2.5]


def test_a_somersloop_writes_the_production_boost_the_game_computes_for_it() -> None:
    """One somersloop in a Constructor is a boost of 2.0, from the registry alone.

    ``base_production_boost`` 1.0 plus ``production_boost_per_slot`` 1.0 times
    the class's ``production_boost_multiplier`` 1.0, which is
    ``GetCurrentMaxProductionBoost`` adding ``GetBoostValue`` once per shard.
    """
    registry = load_registry()
    constructor = registry.buildables[CONSTRUCTOR]
    assert constructor.base_production_boost == 1.0
    assert constructor.production_boost_multiplier == 1.0
    assert registry.limits.production_boost_per_slot == 1.0

    built = _emit(_with_machine(Fraction(1), 1))
    assert _floats(built, CURRENT_PRODUCTION_BOOST) == [2.0]
    assert _floats(built, PENDING_PRODUCTION_BOOST) == [2.0]


def test_a_somersloop_count_comes_back_out_of_the_blueprint_it_went_into() -> None:
    """Which is why ``MachineObj.somersloops`` is part of equality again."""
    placement = _with_machine(Fraction(1), 1)
    assert decode(_emit(placement), load_registry()) == placement


def test_a_clock_the_stored_float_can_hold_comes_back_as_the_fraction_it_was() -> None:
    placement = _with_machine(Fraction(5, 2), 0)
    assert decode(_emit(placement), load_registry()).machines[0].clock == Fraction(5, 2)


def test_a_clock_the_stored_float_cannot_hold_comes_back_at_the_stored_width() -> None:
    """Why the exact ``clock`` stays out of equality: ``moc=133`` is 133/100 and
    the file is f32.

    The placement still compares equal, because equality asks about
    ``stored_clock`` -- the one number the file actually holds -- and the exact
    figure survives in the ``.sbpcfg`` description.
    """
    placement = _with_machine(Fraction(133, 100), 0)
    again = decode(_emit(placement), load_registry())
    assert again == placement
    assert again.machines[0].clock != Fraction(133, 100)
    assert float(again.machines[0].clock) == pytest.approx(1.33, abs=1e-6)


def _overclocked_library() -> TemplateLibrary:
    """A library whose Constructor template is a fixture machine somebody overclocked.

    ``from_fixtures`` keeps the FIRST actor it sees of each class, so putting
    ``production-17`` in front of the rest makes its 4/3 Constructor the
    template and leaves every other class's template alone.  147 machines
    across ten corpus fixtures carry a non-default ``mPendingPotential``; this
    is one of them, and it is the case ``_set_potential`` strips for.
    """
    return TemplateLibrary.from_fixtures([FIXTURES / "production-17.sbp", *fixture_paths()])


def _emit_with(placement: SfyPlacement, library: TemplateLibrary) -> Blueprint:
    newest = _newest_fixture_header()
    return emit(
        placement,
        load_registry(),
        library,
        load_lab_map(),
        build_version=newest.build_version,
        version_data=newest.version_data,
    )


def test_a_machine_built_from_an_overclocked_template_runs_at_the_groups_clock() -> None:
    """A template carries whatever the player who built the fixture set.

    At a group clock of 1 the inherited potential is stripped and nothing is
    written, because 100 % is the class default; at any other clock the group's
    own value is written over it.  Either way what comes out is the build's
    number, never the fixture's.
    """
    library = _overclocked_library()
    inherited = find(library.templates[CONSTRUCTOR].data.properties, PENDING_POTENTIAL)
    assert isinstance(inherited, Float) and inherited.v != 1.0, (
        "this test says nothing unless the template really carries a potential"
    )

    at_default = _emit_with(_placement(), library)
    assert _floats(at_default, PENDING_POTENTIAL) == []
    assert _floats(at_default, CURRENT_POTENTIAL) == []

    underclocked = _with_machine(Fraction(3, 2), 0)
    built = _emit_with(underclocked, library)
    assert _floats(built, PENDING_POTENTIAL) == [1.5]
    assert _floats(built, CURRENT_POTENTIAL) == [1.5]
    assert decode(built, load_registry()) == underclocked


def test_the_round_trip_catches_a_machine_left_at_its_templates_own_potential() -> None:
    """What makes the strip load-bearing rather than decorative.

    Equality compares the clock at the width the file holds it, so a machine
    that kept the fixture's 4/3 is a different placement from the one that was
    asked for -- which is the failure a round-trip test is there to see.
    """
    library = _overclocked_library()
    placement = _placement()  # the group clock is 1
    built = _emit_with(placement, library)
    assert decode(built, load_registry()) == placement

    inherited = find(library.templates[CONSTRUCTOR].data.properties, PENDING_POTENTIAL)
    assert isinstance(inherited, Float)
    tag = Tag(PENDING_POTENTIAL, "FloatProperty", 0).as_modern()
    leaked = replace(
        built,
        objects=tuple(
            (header, replace(data, properties=(*data.properties, Property(tag, inherited))))
            if header.class_name == CONSTRUCTOR and header.kind == ACTOR
            else (header, data)
            for header, data in built.objects
        ),
    )
    assert decode(leaked, load_registry()) != placement


def test_decode_refuses_a_boost_that_is_not_a_whole_number_of_somersloops() -> None:
    """A count is what the model holds, so half a sloop is a file we do not understand."""
    registry = load_registry()
    built = _emit(_with_machine(Fraction(1), 1))
    objects = tuple(
        (header, _replace_float(data, CURRENT_PRODUCTION_BOOST, 1.4))
        if header.class_name == CONSTRUCTOR and header.kind == ACTOR
        else (header, data)
        for header, data in built.objects
    )
    with pytest.raises(EmitError, match="somersloops"):
        decode(replace(built, objects=objects), registry)


def _replace_float(data: ObjectData, name: str, value: float) -> ObjectData:
    return replace(
        data,
        properties=tuple(
            Property(p.tag, Float(value)) if p.tag.name == name else p for p in data.properties
        ),
    )


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


def _lift_placement(height_cm: float = LIFT_HEIGHT_CM, top_yaw_deg: float = 0.0) -> SfyPlacement:
    """A Constructor with a conveyor lift standing on its output."""
    registry = load_registry()
    machine_pose = Pose(0.0, 0.0, SLAB_TOP_CM, 0.0)
    foot = world_port(machine_pose.transform(), _port(registry, CONSTRUCTOR, "Output0"))
    entry, _ = belt_ends(registry, LIFT)
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(4, CONSTRUCTOR, machine_pose, IRON_PLATE),),
        lifts=(LiftObj(7, LIFT, Pose(*foot, 0.0), height_cm, top_yaw_deg),),
        links=(Link((4, "Output0"), (7, entry)),),
    )


def _top_transform(built: Blueprint) -> dict[str, object]:
    """The fields of the emitted lift's ``mTopTransform``, by name."""
    lift = next(d for h, d in built.objects if h.class_name == LIFT and h.kind == ACTOR)
    struct = find(lift.properties, TOP_TRANSFORM)
    assert isinstance(struct, Struct) and struct.name == "Transform"
    return {f.tag.name: f.value for f in struct.fields}


def test_a_lift_is_written_as_its_actor_plus_the_top_transform_beside_it() -> None:
    """``mTopTransform`` is the save property the game keeps a lift's top in.

    ``UPROPERTY( SaveGame, ReplicatedUsing=OnRep_TopTransform ) FTransform
    mTopTransform`` (``Buildables/FGBuildableConveyorLift.h:266-267``), and
    ``lift.top_yaw`` reads what the hologram puts in it: the height along
    ``FVector::UpVector`` and nothing sideways.  A straight lift writes no
    rotation at all, because an identity rotation is the struct's default and
    the game serialises a struct's non-default members only -- which is what the
    corpus's own lifts show, 276 of 757 carrying a translation alone.
    """
    fields = _top_transform(_emit(_lift_placement()))
    assert fields == {TRANSLATION: Vector(0.0, 0.0, LIFT_HEIGHT_CM, wide=True)}


def test_a_lift_that_turns_at_the_top_writes_that_yaw_as_the_quaternion() -> None:
    """``UpdateTopTransform`` stores ``FRotator(0, yaw, 0).Quaternion()`` (0xaa49d1)."""
    fields = _top_transform(_emit(_lift_placement(top_yaw_deg=90.0)))
    assert fields == {
        ROTATION: Quat(*yaw_quaternion(90.0), wide=True),
        TRANSLATION: Vector(0.0, 0.0, LIFT_HEIGHT_CM, wide=True),
    }


def test_a_lift_of_ours_claims_no_passthrough_and_no_deprecated_reversal() -> None:
    """Two properties a lift template carries that a lift of ours must not inherit.

    ``mSnappedPassthroughs`` names the passthroughs the fixture's own lift was
    built through, which this blueprint has not got -- and the flags decide
    where ``SetupConnections`` puts the two ends (``lift.connectors``) and what
    ``lift.height_range``'s floor is, so a stale one would be a claim about
    geometry.  ``mIsReversed`` is marked ``DEPRECATED 2023-01-30`` in the header
    (``:269-272``) with "Instead build lifts where mConnector0 is always input",
    and ``SetupConnections`` -- 1950 bytes, read whole -- never reads it, so a
    lift of ours does not write one.
    """
    built = _emit(_lift_placement())
    lift = next(d for h, d in built.objects if h.class_name == LIFT and h.kind == ACTOR)
    passthroughs = find(lift.properties, SNAPPED_PASSTHROUGHS)
    assert isinstance(passthroughs, Array) and len(passthroughs.items) == 2
    assert all(isinstance(slot, Object) and slot.ref.is_null for slot in passthroughs.items)
    assert find(lift.properties, IS_REVERSED) is None


def test_a_placement_with_a_lift_going_up_and_one_going_down_comes_back_whole() -> None:
    """``decode(emit(p)) == p`` over both signs: the height is what the file holds.

    A lift carrying items downward is one whose actor stands at the TOP with a
    negative height -- ``reversed_swaps_flow`` is false, so the end items enter
    by is ``mConnection0`` at the actor either way.
    """
    for height in (LIFT_HEIGHT_CM, -LIFT_HEIGHT_CM):
        placement = _lift_placement(height)
        assert decode(_emit(placement), load_registry()) == placement


def test_a_lift_that_turns_at_the_top_comes_back_with_that_yaw() -> None:
    for yaw in (90.0, 180.0, -90.0):
        placement = _lift_placement(top_yaw_deg=yaw)
        assert decode(_emit(placement), load_registry()) == placement


def test_the_link_onto_a_lift_comes_back_naming_the_machine_first() -> None:
    """A lift's ports are its two connection components, wired like any other."""
    registry = load_registry()
    entry, _ = belt_ends(registry, LIFT)
    placement = decode(_emit(_lift_placement()), registry)
    assert placement.links == (Link((4, "Output0"), (7, entry)),)


def test_every_conveyor_lift_the_game_wrote_reads_back_as_a_height_and_a_yaw() -> None:
    """The corpus as a FORMAT fixture: a file the game wrote has to decode.

    Not as evidence of what is legal -- a community blueprint carries clipped
    geometry and older game versions, and no bound here comes from one.  What
    757 lifts written by the game itself do establish is the shape and the
    NOISE of the property: 12 of them sit 8.5e-05 cm off their own offset axis,
    which is float conversion rather than a lift leaning sideways, and a reader
    that refuses them is a reader that cannot open the game's own files.
    """
    registry = load_registry()
    seen = 0
    for path in fixture_paths():
        for header, data in read_sbp_file(path).objects:
            if header.kind != ACTOR or "ConveyorLift" not in header.class_name:
                continue
            seen += 1
            height, top_yaw = _lift_top(registry, header, data)
            assert math.isfinite(height) and math.isfinite(top_yaw)
            # Every one of them lands on the 90 degree lattice
            # ``GetRotationStep`` hands out.  That is a corroboration and not
            # the source: ``lift.top_yaw`` is what the validator cites.
            assert top_yaw % 90.0 == 0.0, (path.name, header.name, top_yaw)
    assert seen > 700, "the corpus should carry hundreds of lifts to read"
