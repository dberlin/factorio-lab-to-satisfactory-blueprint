"""Cloning fixture objects into a blueprint we author ourselves."""

from __future__ import annotations

import json
import sys
from functools import cache
from pathlib import Path

import pytest

from flab2bp.sfy.codec import read_sbp, read_sbp_file, write_sbp
from flab2bp.sfy.objects import ACTOR, COMPONENT, Transform
from flab2bp.sfy.properties import Array, Int, Object, Struct, Value, Vector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import DATA_DIR, load_registry
from flab2bp.sfy.rules import load_rules
from flab2bp.sfy.templates import (
    ITEM_DESCRIPTOR_CLASS,
    SPLINE_POINT_FIELD_TAGS,
    STRAIGHT_TANGENT_MAX_CM,
    STRAIGHT_TANGENT_MIN_CM,
    TEMPLATE_MIN_SAVE_VERSION,
    TemplateLibrary,
    apply_recipe,
    assemble,
    connect,
    set_recipe,
    set_spline,
    straight_spline,
)
from flab2bp.sfy.trailers import trailer_for_new
from tests.sfy.conftest import fixture_paths

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

IRON_PLATE = "/Game/FactoryGame/Recipes/Constructor/Recipe_IronPlate.Recipe_IronPlate_C"


def _reference_names(value: Value) -> list[str]:
    """Every object-reference path the value tree exposes."""
    if isinstance(value, Object):
        return [value.ref.path]
    if isinstance(value, Array):
        return [name for item in value.items for name in _reference_names(item)]
    if isinstance(value, Struct):
        return [name for p in value.fields for name in _reference_names(p.value)]
    return []


@cache
def _lib() -> TemplateLibrary:
    """The library, built once: scanning 49 fixtures costs about 2.5 s a time.

    Nothing here mutates it -- ``instantiate`` copies -- so one is enough.
    """
    return TemplateLibrary.from_fixtures(fixture_paths())


def test_library_has_the_checkpoint_classes() -> None:
    lib = _lib()
    for cls in (
        "Build_ConstructorMk1_C",
        "Build_ConveyorBeltMk1_C",
        "Build_PowerPoleMk1_C",
        "Build_Foundation_8x1_01_C",
    ):
        assert cls in lib.classes, cls


def test_instantiate_renames_actor_and_components_consistently() -> None:
    lib = _lib()
    objs = lib.instantiate("Build_ConstructorMk1_C", 42, IDENTITY)
    actor_h, actor_d = objs[0]
    assert actor_h.name == "Build_ConstructorMk1_C_42"
    assert actor_h.transform == IDENTITY
    for h, d in objs[1:]:
        assert h.kind == COMPONENT and h.parent == actor_h.path
        assert h.path.startswith(actor_h.path + ".")
        if h.class_name == "FGFactoryConnectionComponent":
            assert connected(d) is None
    assert {c.path for c in actor_d.components or ()} == {h.path for h, _ in objs[1:]}


def test_assembled_blueprint_round_trips_and_links_belt_to_constructor() -> None:
    lib = _lib()
    reg = load_registry()
    ctor = lib.instantiate("Build_ConstructorMk1_C", 1, IDENTITY)
    belt = lib.instantiate(
        "Build_ConveyorBeltMk1_C",
        2,
        Transform((0.0, 0.0, 0.0, 1.0), (0.0, 600.0, 0.0), (1.0, 1.0, 1.0)),
    )
    out_h, out_port = next((h, d) for h, d in ctor[1:] if h.name == "Output0")
    in_h, belt_in = next((h, d) for h, d in belt[1:] if h.name == "ConveyorAny0")
    out_port2, belt_in2 = connect(out_port, out_h.path, belt_in, in_h.path)
    belt_actor = set_spline(
        belt[0][1],
        (
            (Vector(0, 0, 0), Vector(0, 400, 0), Vector(0, 400, 0)),
            (Vector(0, 400, 0), Vector(0, 400, 0), Vector(0, 400, 0)),
        ),
    )
    objects = [(ctor[0][0], set_recipe(ctor[0][1], IRON_PLATE))]
    objects += [(h, out_port2 if h.name == "Output0" else d) for h, d in ctor[1:]]
    objects.append((belt[0][0], belt_actor))
    objects += [(h, belt_in2 if h.name == "ConveyorAny0" else d) for h, d in belt[1:]]
    sample = read_sbp(fixture_paths()[0].read_bytes())
    bp = assemble(
        tuple(objects),
        (4, 4, 4),
        reg,
        build_version=sample.header.build_version,
        version_data=sample.header.version_data,
    )
    again = read_sbp(write_sbp(bp))
    assert again == bp
    index = object_index(again)
    # The brief indexes the belt's first component here; a template belt lists
    # ``ConveyorAny1`` first, so the end we wired is looked up by name instead.
    wired = next(h.path for h, _ in belt[1:] if h.name == "ConveyorAny0")
    peer = connected(index[wired][1])
    assert peer is not None
    assert peer.path.endswith("Build_ConstructorMk1_C_1.Output0")
    assert len(spline_points(index[belt[0][0].path][1])) == 2
    assert bp.header.cost and all(c.amount > 0 for c in bp.header.cost)
    assert {r.name for r in bp.header.recipes} == {
        "Recipe_ConstructorMk1_C",
        "Recipe_ConveyorBeltMk1_C",
    }
    # The recipe we set survives the round trip as an asset reference.
    recipe = find(index[ctor[0][0].path][1].properties, "mCurrentRecipe")
    assert isinstance(recipe, Object)
    assert recipe.ref.path == IRON_PLATE
    assert recipe.ref.level == ""


def test_connect_wires_both_sides_and_creates_the_missing_property() -> None:
    """A template connection component may carry no ``mConnectedComponent`` at all."""
    lib = _lib()
    ctor = lib.instantiate("Build_ConstructorMk1_C", 7, IDENTITY)
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 8, IDENTITY)
    out_h, out_d = next((h, d) for h, d in ctor[1:] if h.name == "Output0")
    in_h, in_d = next((h, d) for h, d in belt[1:] if h.name == "ConveyorAny0")
    assert connected(out_d) is None and connected(in_d) is None
    out2, in2 = connect(out_d, out_h.path, in_d, in_h.path)
    assert connected(out2) is not None
    assert connected(out2).path == in_h.path  # type: ignore[union-attr]
    assert connected(in2).path == out_h.path  # type: ignore[union-attr]
    # Neither side gained anything but that one property.
    assert [p.tag.name for p in out2.properties].count("mConnectedComponent") == 1
    assert [p.tag.name for p in in2.properties].count("mConnectedComponent") == 1


def test_instantiate_drops_every_reference_into_the_source_blueprint() -> None:
    """Wires, connections and splines name objects the new blueprint does not have."""
    lib = _lib()
    pole = lib.instantiate("Build_PowerPoleMk1_C", 9, IDENTITY)
    _, wires_d = next((h, d) for h, d in pole[1:] if h.name == "PowerConnection")
    wires = find(wires_d.properties, "mWires")
    assert isinstance(wires, Array) and wires.items == ()
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 10, IDENTITY)
    assert spline_points(belt[0][1]) == ()
    # References that stay inside the actor are rewritten, not dropped.
    ctor = lib.instantiate("Build_ConstructorMk1_C", 11, IDENTITY)
    inventory = find(ctor[0][1].properties, "mOutputInventory")
    assert isinstance(inventory, Object)
    assert inventory.ref.path == ctor[0][0].path + ".OutputInventory"


def test_instantiate_builds_a_fresh_trailer_that_matches_the_templates() -> None:
    lib = _lib()
    template = lib.templates["Build_ConveyorBeltMk1_C"]
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 12, IDENTITY)
    actor_h, actor_d = belt[0]
    assert actor_h.kind == ACTOR
    assert actor_d.parent == template.data.parent
    assert actor_d.control == template.data.control
    assert actor_d.trailer == trailer_for_new("Build_ConveyorBeltMk1_C", ACTOR)
    assert actor_d.trailer == template.data.trailer
    for (h, d), (_, td) in zip(belt[1:], template.components, strict=True):
        assert d.trailer == trailer_for_new(h.class_name, COMPONENT) == td.trailer


def test_set_recipe_keeps_the_templates_own_tag() -> None:
    lib = _lib()
    ctor = lib.instantiate("Build_ConstructorMk1_C", 13, IDENTITY)
    before = next(p.tag for p in ctor[0][1].properties if p.tag.name == "mCurrentRecipe")
    after_d = set_recipe(ctor[0][1], IRON_PLATE)
    after = next(p for p in after_d.properties if p.tag.name == "mCurrentRecipe")
    assert after.tag == before
    assert isinstance(after.value, Object) and after.value.ref.path == IRON_PLATE


def test_authored_spline_point_tags_match_the_tags_a_fixture_belt_carries() -> None:
    """``set_spline`` authors the tag the game writes, field for field."""
    for path in fixture_paths():
        bp = read_sbp_file(path)
        if bp.header.save_version < TEMPLATE_MIN_SAVE_VERSION:
            continue
        for h, d in bp.objects:
            if not h.class_name.startswith("Build_ConveyorBelt"):
                continue
            value = find(d.properties, "mSplineData")
            if not isinstance(value, Array) or not value.items:
                continue
            got = tuple(p.tag for p in value.items[0].fields)  # type: ignore[union-attr]
            assert got == SPLINE_POINT_FIELD_TAGS, (path.stem, h.name)
            return
    pytest.fail("no current-family belt with a spline in the corpus")


def test_item_paths_are_the_paths_the_cooked_descriptor_assets_carry() -> None:
    """The registry's item paths, held to a reading of the game they did not come from.

    ``item_paths`` come from Docs.json: ``tools/sfy-extract`` matches the whole
    paths the dump spells out wherever one entry refers to another, and the
    merge keeps the ones the registry needs (``assets.json``'s ``class_paths``).
    ``assets.json``'s ``descriptor_paths`` is the *other* reading of the same
    game -- every cooked Blueprint whose class chain reaches ``FGItemDescriptor``
    in the shipped usmap, keyed by the class name its
    ``BlueprintGeneratedClass`` export carries and valued at the package path
    that export's own outer states. Nothing in either leg is a blueprint, and
    the two have to agree entry for entry.
    """
    reg = load_registry()
    assets = json.loads((DATA_DIR / "assets.json").read_text(encoding="utf-8"))
    cooked: dict[str, str] = assets["descriptor_paths"]
    assert len(cooked) > 700
    assert {item: cooked.get(item) for item in reg.item_paths} == reg.item_paths
    # The other direction: every item descriptor Docs.json lists is authorable.
    assert set(reg.descriptors) <= set(reg.item_paths)


def test_the_checkpoint_takes_the_belt_end_it_wires_from_the_registry() -> None:
    """``scripts/sfy_checkpoint1.py`` names no port; the game's flow order does.

    Which of a conveyor's two connections items enter by is
    ``registry.json``'s ``flow``, read out of ``Factory_Tick`` and named from
    the cooked class default object. The script used to spell ``ConveyorAny0``
    out twice, which is a port name taken on trust.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import sfy_checkpoint1

    reg = load_registry()
    flow = reg.buildables[sfy_checkpoint1.BELT].flow
    assert flow is not None and flow.name_source == "asset"
    assert sfy_checkpoint1._belt_entry(reg) == flow.entry
    source = Path(sfy_checkpoint1.__file__).read_text(encoding="utf-8")
    assert flow.entry not in source and flow.exit not in source


def test_authoring_reaches_every_item_a_recipe_names() -> None:
    """Every ingredient and product in the registry needs a path to author with."""
    reg = load_registry()
    wanted = {
        item
        for recipe in reg.recipes.values()
        for item, _ in (*recipe.ingredients, *recipe.products)
    }
    assert wanted and not wanted - set(reg.item_paths)


def test_assemble_puts_every_actor_before_every_component() -> None:
    """47 of the 49 fixtures are written that way; the other two are single actors."""
    lib = _lib()
    reg = load_registry()
    objects = lib.instantiate("Build_ConstructorMk1_C", 1, IDENTITY) + lib.instantiate(
        "Build_PowerPoleMk1_C", 2, IDENTITY
    )
    sample = read_sbp_file(fixture_paths()[0])
    bp = assemble(
        objects,
        (4, 4, 4),
        reg,
        build_version=sample.header.build_version,
        version_data=sample.header.version_data,
    )
    kinds = [h.kind for h, _ in bp.objects]
    assert kinds == sorted(kinds, reverse=True)
    assert kinds.count(ACTOR) == 2
    assert [h.name for h, _ in bp.objects if h.kind == ACTOR] == [
        "Build_ConstructorMk1_C_1",
        "Build_PowerPoleMk1_C_2",
    ]


def _belt_cost(length: float) -> list[tuple[str, int]]:
    """The blueprint bill for one straight Mk1 belt of ``length`` centimetres."""
    lib = _lib()
    reg = load_registry()
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 1, IDENTITY)
    actor = set_spline(
        belt[0][1],
        (
            (Vector(0, 0, 0), Vector(1, 0, 0), Vector(length, 0, 0)),
            (Vector(length, 0, 0), Vector(length, 0, 0), Vector(1, 0, 0)),
        ),
    )
    sample = read_sbp_file(fixture_paths()[0])
    bp = assemble(
        ((belt[0][0], actor), *belt[1:]),
        (4, 4, 4),
        reg,
        build_version=sample.header.build_version,
        version_data=sample.header.version_data,
    )
    return [(c.item.name, c.amount) for c in bp.header.cost]


def test_a_belt_is_costed_by_the_cost_segment_the_registry_read_from_the_game() -> None:
    """``AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier``'s segment.

    The mark's own ``mMeshLength``, which Docs.json states as 200 cm for every
    belt in 1.2.0; the registry carries it as ``length_per_cost_cm`` with the
    source ``docs``, and nothing here hard-codes it.
    """
    reg = load_registry()
    segment = reg.buildables["Build_ConveyorBeltMk1_C"].length_per_cost_cm
    assert segment is not None
    assert reg.buildables["Build_ConveyorBeltMk1_C"].length_per_cost_source == "docs"
    assert _belt_cost(3 * segment) == [("Desc_IronPlate_C", 3)]
    # Nothing that is not costed by length carries a segment at all.
    assert reg.buildables["Build_ConstructorMk1_C"].length_per_cost_cm is None


def test_a_part_used_belt_segment_is_rounded_the_way_the_game_rounds_it() -> None:
    """``max(1, RoundToInt(length / segment))``, not a ceiling.

    ``AFGBuildable::GetCostMultiplierForLength`` at 0x4a6bb0 divides, doubles,
    adds a half, converts and shifts -- UE's ``RoundToInt`` -- and takes the
    larger of that and 1. So three-quarters of a segment still costs one unit
    and one-and-a-quarter segments costs one, where a ceiling would have
    charged two.
    """
    segment = load_registry().buildables["Build_ConveyorBeltMk1_C"].length_per_cost_cm
    assert segment == 200.0
    assert _belt_cost(0.75 * segment) == [("Desc_IronPlate_C", 1)]
    assert _belt_cost(1.25 * segment) == [("Desc_IronPlate_C", 1)]
    # A half goes away from zero, which is what the SSE form does.
    assert _belt_cost(1.5 * segment) == [("Desc_IronPlate_C", 2)]
    assert _belt_cost(2.5 * segment) == [("Desc_IronPlate_C", 3)]
    # Shorter than a whole segment still costs one: the floor is the cmovl.
    assert _belt_cost(1.0) == [("Desc_IronPlate_C", 1)]


def test_cost_counts_a_belt_once_per_conveyor_segment() -> None:
    lib = _lib()
    reg = load_registry()
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 1, IDENTITY)
    length = 3 * 200.0
    actor = set_spline(
        belt[0][1],
        (
            (Vector(0, 0, 0), Vector(1, 0, 0), Vector(length, 0, 0)),
            (Vector(length, 0, 0), Vector(length, 0, 0), Vector(1, 0, 0)),
        ),
    )
    sample = read_sbp_file(fixture_paths()[0])
    bp = assemble(
        ((belt[0][0], actor), *belt[1:]),
        (4, 4, 4),
        reg,
        build_version=sample.header.build_version,
        version_data=sample.header.version_data,
    )
    # One iron plate per 200 cm of belt, and nothing else on the bill.
    assert [(c.item.name, c.amount) for c in bp.header.cost] == [("Desc_IronPlate_C", 3)]
    assert bp.header.cost[0].item.path == reg.item_paths["Desc_IronPlate_C"]


def test_assemble_refuses_an_actor_whose_build_recipe_is_unknown() -> None:
    lib = _lib()
    reg = load_registry()
    ctor = lib.instantiate("Build_ConstructorMk1_C", 1, IDENTITY)
    stripped = (
        (
            ctor[0][0],
            ctor[0][1].__class__(
                ctor[0][1].parent,
                ctor[0][1].components,
                ctor[0][1].control,
                tuple(p for p in ctor[0][1].properties if p.tag.name != "mBuiltWithRecipe"),
                ctor[0][1].trailer,
            ),
        ),
        *ctor[1:],
    )
    sample = read_sbp_file(fixture_paths()[0])
    with pytest.raises(ValueError, match="mBuiltWithRecipe"):
        assemble(
            stripped,
            (4, 4, 4),
            reg,
            build_version=sample.header.build_version,
            version_data=sample.header.version_data,
        )


def _filter_items(objects, data, name: str) -> tuple[str, ...]:
    """The ``mAllowedItemDescriptors`` of the inventory ``data`` names as ``name``."""
    ref = find(data.properties, name)
    assert isinstance(ref, Object)
    inventory = next(d for h, d in objects if h.path == ref.ref.path)
    allowed = find(inventory.properties, "mAllowedItemDescriptors")
    assert isinstance(allowed, Array)
    return tuple(item.ref.name for item in allowed.items if isinstance(item, Object))


def _slot_sizes(objects, data, name: str) -> tuple[int, ...]:
    ref = find(data.properties, name)
    assert isinstance(ref, Object)
    inventory = next(d for h, d in objects if h.path == ref.ref.path)
    sizes = find(inventory.properties, "mArbitrarySlotSizes")
    assert isinstance(sizes, Array)
    return tuple(item.v for item in sizes.items if isinstance(item, Int))


def test_apply_recipe_sets_the_recipe_and_both_inventory_filters() -> None:
    """The template constructor makes Biofuel; after this it must make iron plates."""
    lib = _lib()
    reg = load_registry()
    before = lib.instantiate("Build_ConstructorMk1_C", 20, IDENTITY)
    assert _filter_items(before, before[0][1], "mInputInventory") == ("Desc_GenericBiomass_C",)
    assert _filter_items(before, before[0][1], "mOutputInventory")[0] == "Desc_Biofuel_C"

    after = apply_recipe(before, IRON_PLATE, reg)
    recipe = find(after[0][1].properties, "mCurrentRecipe")
    assert isinstance(recipe, Object) and recipe.ref.path == IRON_PLATE
    assert _filter_items(after, after[0][1], "mInputInventory") == ("Desc_IronIngot_C",)
    assert _filter_items(after, after[0][1], "mOutputInventory")[0] == "Desc_IronPlate_C"


def test_apply_recipe_leaves_no_trace_of_the_templates_own_recipe() -> None:
    """No property anywhere in the emitted objects may still name the old items."""
    lib = _lib()
    after = apply_recipe(
        lib.instantiate("Build_ConstructorMk1_C", 21, IDENTITY), IRON_PLATE, load_registry()
    )
    stale = [
        (h.name, p.tag.name, ref)
        for h, d in after
        for p in d.properties
        for ref in _reference_names(p.value)
        if "Biomass" in ref or "Biofuel" in ref
    ]
    assert stale == [], stale


def test_apply_recipe_keeps_the_machines_own_slot_count() -> None:
    """Slots belong to the machine; only the entries the recipe names are rewritten."""
    lib = _lib()
    before = lib.instantiate("Build_ConstructorMk1_C", 22, IDENTITY)
    after = apply_recipe(before, IRON_PLATE, load_registry())
    for name in ("mInputInventory", "mOutputInventory"):
        assert len(_filter_items(after, after[0][1], name)) == len(
            _filter_items(before, before[0][1], name)
        )
        assert _slot_sizes(after, after[0][1], name) == _slot_sizes(before, before[0][1], name)
    # The spare output slot keeps the wildcard the game leaves in it.
    assert _filter_items(after, after[0][1], "mOutputInventory")[1] == "FGItemDescriptor"


def test_apply_recipe_refuses_a_recipe_the_machine_cannot_run() -> None:
    lib = _lib()
    ctor = lib.instantiate("Build_ConstructorMk1_C", 23, IDENTITY)
    with pytest.raises(ValueError, match="does not produce"):
        apply_recipe(
            ctor,
            "/Game/FactoryGame/Recipes/Smelter/Recipe_IngotIron.Recipe_IngotIron_C",
            load_registry(),
        )
    with pytest.raises(ValueError, match="no recipe"):
        apply_recipe(
            ctor,
            "/Game/FactoryGame/Recipes/Recipe_NoSuchThing.Recipe_NoSuchThing_C",
            load_registry(),
        )


def test_apply_recipe_writes_the_filters_the_game_writes() -> None:
    """``SetUpInventoryFilters``' shape: recipe order, then the wildcard, exactly.

    Slot i allows the recipe's i-th ingredient (i-th product, for the output
    inventory) while there is one, and every slot past the last allows
    ``UFGItemDescriptor``. The rule ``manufacturer.inventory_filters`` quotes
    the loops; this holds what we author to them, over the machines the
    templates cover.
    """
    reg = load_registry()
    rules = load_rules()
    assert rules["manufacturer.inventory_filters"].effect == "compute"
    lib = _lib()
    recipe = reg.recipes[IRON_PLATE.rsplit(".", 1)[-1]]
    ingredients = [item for item, _ in recipe.ingredients]
    products = [item for item, _ in recipe.products]
    after = apply_recipe(lib.instantiate("Build_ConstructorMk1_C", 24, IDENTITY), IRON_PLATE, reg)
    got_in = _filter_items(after, after[0][1], "mInputInventory")
    got_out = _filter_items(after, after[0][1], "mOutputInventory")
    assert list(got_in[: len(ingredients)]) == ingredients
    assert list(got_out[: len(products)]) == products
    assert set(got_in[len(ingredients) :]) <= {"FGItemDescriptor"}
    assert set(got_out[len(products) :]) <= {"FGItemDescriptor"}
    assert ITEM_DESCRIPTOR_CLASS.endswith(".FGItemDescriptor")


def test_a_spare_slot_gets_the_wildcard_and_not_what_the_template_had() -> None:
    """The game rewrites every slot, so a class left over from before is a bug.

    ``SetUpInventoryFilters`` writes ``UFGItemDescriptor`` on every slot past
    the last ingredient (``0x549f83``/``0x549fb0``) rather than leaving it as it
    was, so applying a one-ingredient recipe over a two-ingredient one has to
    clear the second slot. A refinery runs both kinds.
    """
    reg = load_registry()
    lib = _lib()
    machine = "Build_OilRefinery_C"
    two = reg.recipe_paths["Recipe_Alternate_CoatedCable_C"]
    one = reg.recipe_paths["Recipe_Alternate_PolymerResin_C"]
    assert len(reg.recipes["Recipe_Alternate_CoatedCable_C"].ingredients) == 2
    assert len(reg.recipes["Recipe_Alternate_PolymerResin_C"].ingredients) == 1
    after_two = apply_recipe(lib.instantiate(machine, 25, IDENTITY), two, reg)
    assert "FGItemDescriptor" not in _filter_items(after_two, after_two[0][1], "mInputInventory")[1]
    after_one = apply_recipe(after_two, one, reg)
    got = _filter_items(after_one, after_one[0][1], "mInputInventory")
    assert got[0] == reg.recipes["Recipe_Alternate_PolymerResin_C"].ingredients[0][0]
    assert set(got[1:]) == {"FGItemDescriptor"}


def test_a_straight_spline_is_shaped_the_way_the_game_shapes_one() -> None:
    """``belt.straight_tangents``: unit vectors outside, clamp(L/2, 50, 600) inside.

    ``FSplineBuilder::Start`` normalises the tangent it is given into both of
    the first point's tangents, ``BuildStraightSpline2D`` scales the run
    direction by half the run's length clamped to [50, 600], and ``AddSegment``
    gives the second point that tangent to arrive on and its unit direction to
    leave by.
    """
    assert load_rules()["belt.straight_tangents"].effect == "compute"
    points = straight_spline((1.0, 0.0, 0.0), 400.0)
    assert len(points) == 2
    (first_loc, first_arrive, first_leave), (last_loc, last_arrive, last_leave) = points
    assert (first_loc.x, first_loc.y, first_loc.z) == (0.0, 0.0, 0.0)
    assert (last_loc.x, last_loc.y, last_loc.z) == (400.0, 0.0, 0.0)
    # The outer tangents are the unit direction; the inner ones carry the length.
    assert (first_arrive.x, last_leave.x) == (1.0, 1.0)
    assert (first_leave.x, last_arrive.x) == (200.0, 200.0)
    assert first_arrive != first_leave and last_arrive != last_leave


def test_a_short_straight_spline_keeps_the_games_fifty_centimetre_floor() -> None:
    """``maxsd 50.0`` at 0xafcf1d: a 60 cm run still gets 50 cm tangents."""
    short = straight_spline((0.0, 1.0, 0.0), 60.0)
    assert short[0][2].y == STRAIGHT_TANGENT_MIN_CM
    assert short[1][1].y == STRAIGHT_TANGENT_MIN_CM
    # And the 600 cm cap at the other end: minsd 600.0 at 0xafcf15.
    long = straight_spline((0.0, 0.0, 1.0), 5000.0)
    assert long[0][2].z == STRAIGHT_TANGENT_MAX_CM
    assert long[1][1].z == STRAIGHT_TANGENT_MAX_CM


def test_a_straight_spline_scales_by_the_full_three_dimensional_length() -> None:
    """The clamp is on the 3D distance even in the 2D builder (0xafcf03/0xafcf08)."""
    diagonal = (0.6, 0.0, 0.8)
    points = straight_spline(diagonal, 1000.0)
    assert points[0][2].x == 0.6 * 500.0
    assert points[0][2].z == 0.8 * 500.0
