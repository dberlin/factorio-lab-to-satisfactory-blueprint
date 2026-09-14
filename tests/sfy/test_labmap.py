"""The FactorioLab id tables, the drift test that re-derives them, and the refusals.

The derivation is only worth its committed output if it fails loudly wherever the
data stops deciding for it, so the refusal paths are tested by doctoring the
script's own tables and pools in process -- no fixture files, and nothing on disk
is touched.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator, Mapping
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.games import Game
from flab2bp.sfy.labmap import (
    LAB_MAP_PATH,
    LabMapError,
    item_class,
    load_lab_map,
    machine_class,
    recipe_class,
)
from flab2bp.sfy.registry import Registry, load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sfy_lab_map.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    """``scripts/sfy_lab_map.py`` as a module: it is not on the import path."""
    spec = importlib.util.spec_from_file_location("sfy_lab_map", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs because ``@dataclass`` resolves a field's
    # annotation through ``sys.modules[cls.__module__]``, which is None for a
    # module that was only ever exec'd.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry()


@pytest.fixture
def patch() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


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


def test_an_item_name_two_game_classes_share_is_refused_naming_both(
    script: ModuleType, registry: Registry, patch: pytest.MonkeyPatch
) -> None:
    real = script._display_names

    def two_classes_called_iron_ingot(reg: Registry) -> dict[str, str]:
        pool = dict(real(reg))
        pool["Desc_SteelIngot_C"] = pool["Desc_IronIngot_C"]
        return pool

    patch.setattr(script, "_display_names", two_classes_called_iron_ingot)
    with pytest.raises(SystemExit) as caught:
        script._items(load_vendored(Game.SFY), registry)
    message = str(caught.value)
    assert "'iron-ingot'" in message
    assert "Desc_IronIngot_C" in message and "Desc_SteelIngot_C" in message


def test_an_item_the_override_list_forgets_is_refused(
    script: ModuleType, registry: Registry, patch: pytest.MonkeyPatch
) -> None:
    patch.setattr(script, "ITEM_OVERRIDES", {})
    with pytest.raises(SystemExit) as caught:
        script._items(load_vendored(Game.SFY), registry)
    message = str(caught.value)
    assert "'sam-ore'" in message and "'iodine-infused-filter'" in message


def test_an_override_naming_a_class_the_game_calls_something_else_is_refused(
    script: ModuleType, registry: Registry, patch: pytest.MonkeyPatch
) -> None:
    patch.setattr(script, "ITEM_OVERRIDES", {"sam-ore": ("Desc_SAM_C", "SAM Ore")})
    with pytest.raises(SystemExit, match="stale"):
        script._items(load_vendored(Game.SFY), registry)


def test_an_override_more_than_one_game_recipe_fits_is_refused(script: ModuleType) -> None:
    signature = script._signature(
        "Build_Packager_C", Fraction(1), (("Desc_A_C", 1),), (("Desc_B_C", 1),)
    )
    relaxed: Mapping[Any, list[str]] = {script._relax(signature): ["Recipe_One_C", "Recipe_Two_C"]}
    with pytest.raises(SystemExit) as caught:
        script._override_recipe("packaged-a", "Recipe_One_C", signature, relaxed)
    message = str(caught.value)
    assert "Recipe_One_C" in message and "Recipe_Two_C" in message


def test_an_override_aimed_at_a_sibling_recipe_is_refused(
    script: ModuleType, patch: pytest.MonkeyPatch
) -> None:
    patch.setattr(
        script,
        "RECIPE_OVERRIDES",
        {"unpackage-sulfuric-acid": "Recipe_PackagedSulfuricAcid_C"},
    )
    with pytest.raises(SystemExit, match="Recipe_UnpackageSulfuricAcid_C"):
        script.derive()


def test_a_machine_row_the_game_data_contradicts_is_refused(
    script: ModuleType, registry: Registry, patch: pytest.MonkeyPatch
) -> None:
    dataset = load_vendored(Game.SFY)
    items, _ = script._items(dataset, registry)
    machines = {**script.MACHINES, "refinery": "Build_AssemblerMk1_C"}
    with pytest.raises(SystemExit) as caught:
        script._check_machines(machines, items, dataset, registry)
    message = str(caught.value)
    assert "'refinery'" in message
    assert "Build_OilRefinery_C" in message and "Build_AssemblerMk1_C" in message


def test_a_machine_row_for_a_lab_id_the_dataset_dropped_is_refused(
    script: ModuleType, registry: Registry
) -> None:
    dataset = load_vendored(Game.SFY)
    items, _ = script._items(dataset, registry)
    machines = {**script.MACHINES, "quantum-constructor": "Build_ConstructorMk1_C"}
    with pytest.raises(SystemExit, match="quantum-constructor"):
        script._check_machines(machines, items, dataset, registry)


def test_item_names_taken_from_another_docs_dump_are_refused(
    script: ModuleType, patch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = json.loads(script.ITEM_NAMES_PATH.read_text())
    payload["provenance"]["docs_sha256"] = "0" * 64
    stale = tmp_path / "lab_item_names.json"
    stale.write_text(json.dumps(payload))
    patch.setattr(script, "ITEM_NAMES_PATH", stale)
    with pytest.raises(SystemExit, match="refresh-names"):
        script.derive()


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
