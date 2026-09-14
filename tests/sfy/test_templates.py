"""Cloning fixture objects into a blueprint we author ourselves."""

from __future__ import annotations

from functools import cache

import pytest

from flab2bp.sfy.codec import read_sbp, read_sbp_file, write_sbp
from flab2bp.sfy.objects import ACTOR, COMPONENT, Transform
from flab2bp.sfy.properties import Array, Int, Object, Struct, Value, Vector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.templates import (
    CONVEYOR_SEGMENT_CM,
    SPLINE_POINT_FIELD_TAGS,
    TEMPLATE_MIN_SAVE_VERSION,
    TemplateLibrary,
    apply_recipe,
    assemble,
    connect,
    set_recipe,
    set_spline,
)
from flab2bp.sfy.trailers import trailer_for_new
from tests.sfy.conftest import fixture_paths

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

IRON_PLATE = "/Game/FactoryGame/Recipes/Constructor/Recipe_IronPlate.Recipe_IronPlate_C"

_PARTS = "/Game/FactoryGame/Resource/Parts"

# The asset path of every item descriptor the fixture corpus's own header costs
# name, transcribed by hand from the corpus when ``templates.py`` carried this
# table itself. It is an oracle now, and only an oracle: the registry's
# ``item_paths`` are extracted from the game's Docs.json, and this says the
# extraction agrees with what the game wrote into 49 blueprints.
_ITEM_FOLDERS: dict[str, str] = {
    "Desc_AluminumCasing_C": f"{_PARTS}/AluminumCasing",
    "Desc_AluminumPlate_C": f"{_PARTS}/AluminumPlate",
    "Desc_Cable_C": f"{_PARTS}/Cable",
    "Desc_Cement_C": f"{_PARTS}/Cement",
    "Desc_CircuitBoardHighSpeed_C": f"{_PARTS}/CircuitBoardHighSpeed",
    "Desc_Computer_C": f"{_PARTS}/Computer",
    "Desc_CopperIngot_C": f"{_PARTS}/CopperIngot",
    "Desc_CopperSheet_C": f"{_PARTS}/CopperSheet",
    "Desc_CrystalOscillator_C": f"{_PARTS}/CrystalOscillator",
    "Desc_CrystalShard_C": "/Game/FactoryGame/Resource/Environment/Crystal",
    "Desc_FicsiteMesh_C": f"{_PARTS}/FicsiteMesh",
    "Desc_Fuel_C": f"{_PARTS}/Fuel",
    "Desc_HighSpeedWire_C": f"{_PARTS}/HighSpeedWire",
    "Desc_IronIngot_C": f"{_PARTS}/IronIngot",
    "Desc_IronPlateReinforced_C": f"{_PARTS}/IronPlateReinforced",
    "Desc_IronPlate_C": f"{_PARTS}/IronPlate",
    "Desc_IronRod_C": f"{_PARTS}/IronRod",
    "Desc_Leaves_C": f"{_PARTS}/GenericBiomass",
    "Desc_ModularFrameHeavy_C": f"{_PARTS}/ModularFrameHeavy",
    "Desc_ModularFrame_C": f"{_PARTS}/ModularFrame",
    "Desc_Motor_C": f"{_PARTS}/Motor",
    "Desc_Plastic_C": f"{_PARTS}/Plastic",
    "Desc_QuartzCrystal_C": f"{_PARTS}/QuartzCrystal",
    "Desc_Rotor_C": f"{_PARTS}/Rotor",
    "Desc_Rubber_C": f"{_PARTS}/Rubber",
    "Desc_SAMFluctuator_C": f"{_PARTS}/SAMFluctuator",
    "Desc_Silica_C": f"{_PARTS}/Silica",
    "Desc_SteelPipe_C": f"{_PARTS}/SteelPipe",
    "Desc_SteelPlateReinforced_C": f"{_PARTS}/SteelPlateReinforced",
    "Desc_SteelPlate_C": f"{_PARTS}/SteelPlate",
    "Desc_TimeCrystal_C": f"{_PARTS}/TimeCrystal",
    "Desc_WAT2_C": "/Game/FactoryGame/Prototype/WAT",
    "Desc_Wire_C": f"{_PARTS}/Wire",
}

ITEM_CLASS_PATHS: dict[str, str] = {
    item: f"{folder}/{item.removesuffix('_C')}.{item}" for item, folder in _ITEM_FOLDERS.items()
}


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


def test_item_paths_agree_with_every_fixture_cost_entry() -> None:
    """The registry's descriptor asset paths are what the game wrote in the corpus."""
    reg = load_registry()
    seen = 0
    for path in fixture_paths():
        for entry in read_sbp_file(path).header.cost:
            assert reg.item_paths.get(entry.item.name) == entry.item.path, entry.item.name
            seen += 1
    assert seen > 100


def test_the_extracted_item_paths_match_the_hand_transcribed_oracle() -> None:
    """What the extractor read out of Docs.json equals what was typed by hand.

    The 33 entries in :data:`ITEM_CLASS_PATHS` were transcribed from the fixture
    corpus before ``tools/sfy-extract`` collected any of them; they are here to
    hold the extraction to a source it did not come from.
    """
    reg = load_registry()
    assert {item: reg.item_paths.get(item) for item in ITEM_CLASS_PATHS} == ITEM_CLASS_PATHS
    assert len(reg.item_paths) > 700


def test_authoring_reaches_every_item_a_recipe_names() -> None:
    """The hand table covered 33 descriptors; every recipe in the registry needs one."""
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


def test_cost_counts_a_belt_once_per_conveyor_segment() -> None:
    lib = _lib()
    reg = load_registry()
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 1, IDENTITY)
    length = 3 * CONVEYOR_SEGMENT_CM
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


def test_the_corpus_fills_inventory_filters_from_the_recipe() -> None:
    """The rule ``apply_recipe`` implements, measured on every fixture manufacturer.

    Ingredients fill the input inventory's leading slots in recipe order and
    products the output inventory's; whatever slots the machine has left over
    hold the ``FGItemDescriptor`` wildcard. 160 of 160 agree.
    """
    reg = load_registry()
    checked = 0
    for path in fixture_paths():
        bp = read_sbp_file(path)
        if bp.header.save_version < TEMPLATE_MIN_SAVE_VERSION:
            continue
        objects = bp.objects
        for h, d in objects:
            if h.kind != ACTOR:
                continue
            value = find(d.properties, "mCurrentRecipe")
            if not isinstance(value, Object) or value.ref.is_null:
                continue
            recipe = reg.recipes.get(value.ref.name)
            if recipe is None or find(d.properties, "mInputInventory") is None:
                continue
            ingredients = tuple(item for item, _ in recipe.ingredients)
            products = tuple(item for item, _ in recipe.products)
            got_in = _filter_items(objects, d, "mInputInventory")
            got_out = _filter_items(objects, d, "mOutputInventory")
            assert got_in[: len(ingredients)] == ingredients, (path.stem, h.name)
            assert got_out[: len(products)] == products, (path.stem, h.name)
            assert set(got_out[len(products) :]) <= {"FGItemDescriptor"}, (path.stem, h.name)
            checked += 1
    assert checked > 100, checked
