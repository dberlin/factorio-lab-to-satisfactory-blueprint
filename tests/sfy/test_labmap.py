"""The FactorioLab id tables, and the drift test that re-derives them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from flab2bp.sfy.labmap import (
    LAB_MAP_PATH,
    LabMapError,
    item_class,
    load_lab_map,
    machine_class,
    recipe_class,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_three_worked_examples_map_the_way_the_game_names_them() -> None:
    m = load_lab_map()
    assert recipe_class(m, "iron-ingot") == "Recipe_IngotIron_C"
    assert recipe_class(m, "screw-cast") == "Recipe_Alternate_Screw_C"
    assert (
        recipe_class(m, "reinforced-iron-plate-bolted")
        == "Recipe_Alternate_ReinforcedIronPlate_1_C"
    )
    assert item_class(m, "screw") == "Desc_IronScrew_C"
    assert item_class(m, "iron-ore") == "Desc_OreIron_C"
    assert machine_class(m, "refinery") == "Build_OilRefinery_C"
    assert machine_class(m, "particle-accelerator") == "Build_HadronCollider_C"


def test_every_craftable_lab_recipe_maps_or_says_why_not() -> None:
    from flab2bp.lab.data import load_vendored
    from flab2bp.lab.url import Game

    m = load_lab_map()
    for recipe in load_vendored(Game.SFY).recipes:
        assert recipe.id in m.recipes or recipe.id in m.unmapped_recipes, recipe.id
    assert set(m.unmapped_recipes) >= {"iron-ore", "phase-1", "uranium-waste"}


def test_every_mapped_class_exists_in_the_registry() -> None:
    from flab2bp.sfy.registry import load_registry

    m, reg = load_lab_map(), load_registry()
    assert set(m.recipes.values()) <= set(reg.recipes)
    assert set(m.items.values()) <= set(reg.item_paths)
    assert set(m.machines.values()) <= set(reg.buildables)


def test_every_item_a_mapped_recipe_names_has_an_item_class() -> None:
    from flab2bp.lab.data import load_vendored
    from flab2bp.lab.url import Game

    m = load_lab_map()
    for recipe in load_vendored(Game.SFY).recipes:
        if recipe.id not in m.recipes:
            continue
        for lab_item in (*recipe.inputs, *recipe.outputs):
            assert lab_item in m.items, f"{recipe.id} names the unmapped item {lab_item}"


def test_a_lookup_of_an_unknown_id_names_the_id_it_was_asked_for() -> None:
    m = load_lab_map()
    with pytest.raises(KeyError, match="not-an-item"):
        item_class(m, "not-an-item")
    with pytest.raises(KeyError, match="not-a-recipe"):
        recipe_class(m, "not-a-recipe")
    with pytest.raises(KeyError, match="not-a-machine"):
        machine_class(m, "not-a-machine")


def test_a_payload_missing_a_section_is_refused(tmp_path: Path) -> None:
    payload = json.loads(LAB_MAP_PATH.read_text())
    del payload["machines"]
    broken = tmp_path / "lab_map.json"
    broken.write_text(json.dumps(payload))
    with pytest.raises(LabMapError, match="machines"):
        load_lab_map(broken)


def test_the_committed_map_is_what_the_script_derives(tmp_path: Path) -> None:
    out = tmp_path / "lab_map.json"
    subprocess.run(
        [sys.executable, "scripts/sfy_lab_map.py", "--out", str(out)],
        check=True,
        cwd=REPO_ROOT,
    )
    fresh, committed = json.loads(out.read_text()), json.loads(LAB_MAP_PATH.read_text())
    fresh.pop("provenance"), committed.pop("provenance")
    assert fresh == committed
