"""What a FactorioLab sfy id means in the game.

FactorioLab and Satisfactory name the same thing twice. The lab calls an item
``screw``, the game calls its descriptor ``Desc_IronScrew_C``; the lab calls a
recipe ``reinforced-iron-plate-bolted``, the game calls it
``Recipe_Alternate_ReinforcedIronPlate_1_C``. Neither name is derivable from the
other -- the game swaps the words (``Recipe_IngotIron_C`` for ``Iron Ingot``),
prefixes alternates (``Alternate: Cast Screws``), numbers their classes
(``_1_C``, ``_2_C``) and pluralises display names the lab keeps singular -- so
this package carries the correspondence as **data**, derived once by
``scripts/sfy_lab_map.py`` and committed to ``data/lab_map.json``.

Both sides of every row come from a stated source, never from a guess:

* the **game** side is the registry -- ``Docs.json`` by way of
  ``data/lab_item_names.json`` for an item descriptor's display name,
  ``registry.buildables`` for a building's, and ``registry.recipes`` for a
  recipe's producer, duration, ingredients and products;
* the **lab** side is FactorioLab's own vendored dataset.

No blueprint corpus is consulted, for this or for anything else. A lab id that
the derivation cannot resolve to exactly one game class does not get a plausible
answer: ``scripts/sfy_lab_map.py`` fails, and a human adds a commented override
or a stated reason. The two overrides and the twenty unmapped recipes in the
committed file are each spelled out there.

:attr:`LabMap.unmapped_recipes` is the other half of the account. Twenty lab
recipes have no ``Recipe_*_C`` at all -- the thirteen ``mining`` recipes are an
extractor's output rather than a recipe, the five Project Assembly phases are
schematic milestones, and the two fuel-rod ``waste`` rows are what the Nuclear
Power Plant leaves behind rather than something it crafts -- so each maps to the
reason instead. A caller that needs a recipe class must consult it before
concluding that an id is unknown.

FactorioLab's chosen flow is authoritative: nothing here re-rates a recipe or
second-guesses an amount. This module only says what the lab's ids are called in
the game.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "LAB_MAP_PATH",
    "LabMap",
    "LabMapError",
    "item_class",
    "load_lab_map",
    "machine_class",
    "recipe_class",
]

DATA_DIR = Path(__file__).resolve().parent / "data"
LAB_MAP_PATH = DATA_DIR / "lab_map.json"

# The sections ``data/lab_map.json`` must carry. A payload missing one is
# refused outright rather than loaded half-built: a caller that silently got an
# empty ``recipes`` would map every lab id to nothing and blame the dataset.
REQUIRED_SECTIONS = ("items", "recipes", "machines", "unmapped_recipes", "provenance")


class LabMapError(ValueError):
    """The lab-map payload was missing a section or carried a malformed one."""


@dataclass(frozen=True, slots=True)
class LabMap:
    """The FactorioLab-to-game id tables, as committed.

    ``items`` covers solids and fluids alike, and the building descriptors of
    the machines, belts and pipes the lab lists as items. ``machines`` is the
    other half of that: the ``Build_*_C`` a lab machine, belt or pipe id builds,
    which is what a placer needs and what ``items`` deliberately does not carry.

    Eight lab ids are in neither table because the game has no class for them:
    ``purity-0``..``purity-2`` are the lab's own mining-purity modules, and
    ``phase-1``..``phase-5`` are Project Assembly milestones. They are named in
    ``scripts/sfy_lab_map.py``, which refuses to write a map that drops any
    other id.
    """

    items: dict[str, str]  # lab item id -> Desc_*_C (solid and fluid alike)
    recipes: dict[str, str]  # lab recipe id -> Recipe_*_C
    machines: dict[str, str]  # lab machine item id -> Build_*_C
    unmapped_recipes: dict[str, str]  # lab recipe id -> why
    provenance: dict[str, Any]


def _table(data: dict[str, Any], section: str) -> dict[str, str]:
    raw = data[section]
    if not isinstance(raw, dict):
        raise LabMapError(f"the lab map's {section!r} section is not an object")
    table: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value:
            raise LabMapError(f"the lab map's {section}[{key!r}] is not a non-empty string")
        table[str(key)] = value
    return table


def load_lab_map(path: Path | None = None) -> LabMap:
    """Read the committed lab map from ``data/lab_map.json``.

    That file is generated, never hand-edited: ``scripts/sfy_lab_map.py``
    derives it from the registry and FactorioLab's vendored dataset, and
    ``tests/sfy/test_labmap.py`` re-runs the derivation to catch drift.

    Raises :class:`LabMapError` if the file is missing, is not JSON, or lacks
    one of :data:`REQUIRED_SECTIONS`; the caller gets no half-built map.
    """
    path = LAB_MAP_PATH if path is None else Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise LabMapError(f"no lab map at {path}") from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LabMapError(f"the lab map at {path} is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LabMapError(f"the lab map at {path} is not an object")
    missing = [section for section in REQUIRED_SECTIONS if section not in data]
    if missing:
        raise LabMapError(f"the lab map at {path} is missing: {missing}")
    provenance = data["provenance"]
    if not isinstance(provenance, dict):
        raise LabMapError(f"the lab map at {path} has a malformed 'provenance' section")
    return LabMap(
        items=_table(data, "items"),
        recipes=_table(data, "recipes"),
        machines=_table(data, "machines"),
        unmapped_recipes=_table(data, "unmapped_recipes"),
        provenance=dict(provenance),
    )


def item_class(m: LabMap, lab_item_id: str) -> str:
    """The ``Desc_*_C`` of a lab item id, or :class:`KeyError` naming the id."""
    try:
        return m.items[lab_item_id]
    except KeyError:
        raise KeyError(f"the lab map has no item class for {lab_item_id!r}") from None


def recipe_class(m: LabMap, lab_recipe_id: str) -> str:
    """The ``Recipe_*_C`` of a lab recipe id, or :class:`KeyError` naming the id.

    The error says why when the id is a known one the game has no recipe for --
    a mining recipe, a Project Assembly phase or a fuel-rod waste row -- so that
    a caller is not left hunting for a class that does not exist.
    """
    try:
        return m.recipes[lab_recipe_id]
    except KeyError:
        why = m.unmapped_recipes.get(lab_recipe_id)
        if why is not None:
            raise KeyError(
                f"the game has no recipe class for the lab recipe {lab_recipe_id!r}: {why}"
            ) from None
        raise KeyError(f"the lab map has no recipe class for {lab_recipe_id!r}") from None


def machine_class(m: LabMap, lab_machine_id: str) -> str:
    """The ``Build_*_C`` of a lab machine id, or :class:`KeyError` naming the id."""
    try:
        return m.machines[lab_machine_id]
    except KeyError:
        raise KeyError(f"the lab map has no machine class for {lab_machine_id!r}") from None
