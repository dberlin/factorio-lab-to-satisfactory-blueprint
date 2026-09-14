"""Cloning fixture objects into a blueprint we author ourselves."""

from __future__ import annotations

from functools import cache

import pytest

from flab2bp.sfy.codec import read_sbp, read_sbp_file, write_sbp
from flab2bp.sfy.objects import ACTOR, COMPONENT, Transform
from flab2bp.sfy.properties import Array, Object, Vector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.templates import (
    CONVEYOR_SEGMENT_CM,
    ITEM_CLASS_PATHS,
    SPLINE_POINT_FIELD_TAGS,
    TEMPLATE_MIN_SAVE_VERSION,
    TemplateLibrary,
    assemble,
    connect,
    set_recipe,
    set_spline,
)
from flab2bp.sfy.trailers import trailer_for_new
from tests.sfy.conftest import fixture_paths

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

IRON_PLATE = "/Game/FactoryGame/Recipes/Constructor/Recipe_IronPlate.Recipe_IronPlate_C"


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


def test_item_class_paths_agree_with_every_fixture_cost_entry() -> None:
    """The descriptor asset paths are the game's own, taken from the corpus."""
    seen = 0
    for path in fixture_paths():
        for entry in read_sbp_file(path).header.cost:
            assert ITEM_CLASS_PATHS.get(entry.item.name) == entry.item.path, entry.item.name
            seen += 1
    assert seen > 100


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
    assert bp.header.cost[0].item.path == ITEM_CLASS_PATHS["Desc_IronPlate_C"]


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
