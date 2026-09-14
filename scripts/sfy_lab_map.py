"""Derive the FactorioLab-to-game id tables into the committed ``lab_map.json``.

Two halves, two sources. The **game** half is the registry: Docs.json states an
item descriptor's display name, a buildable's display name and every recipe's
producer, duration, ingredients and products, and
``scripts/sfy_registry.py`` merges them into ``data/registry.json``. The **lab**
half is FactorioLab's own vendored ``data.json``. This script joins the two and
writes the table :func:`flab2bp.sfy.labmap.load_lab_map` reads.

**Nothing here is guessed.** A lab id with zero game candidates, or with more
than one, is not given the likeliest answer: the run fails and names the
candidates, and a human resolves it by adding a commented override or -- when
the game genuinely has no such class -- a stated reason. No blueprint corpus is
consulted: what a community blueprint contains is not a fact about the game.

**FactorioLab's chosen flow is authoritative.** Amounts and durations are used
only to *identify* a recipe, never to correct one. Where the two sides state
different amounts for the same recipe, the lab's numbers stand and the
correspondence is recorded as an override with the drift spelled out.

The three tables:

1. **Machines.** The lab's nineteen producer ids plus its belt and pipe marks,
   written out in :data:`MACHINES` -- twenty-seven rows from section 3.7 of the
   M2 interface reference, each checked against ``registry.buildables``. No
   convention produces ``refinery -> Build_OilRefinery_C`` or
   ``particle-accelerator -> Build_HadronCollider_C``, so the table is the only
   honest form for these.

2. **Items.** By display name, over two pools, because Docs.json puts an item's
   name in two different places:

   * an item descriptor states its own ``mDisplayName``
     (``Desc_IronScrew_C`` -> ``Screws``), collected into
     ``data/lab_item_names.json`` by ``--refresh-names``;
   * a *building* descriptor's ``mDisplayName`` is empty -- the buildable
     carries the name instead -- so a machine, belt or pipe item is matched on
     ``registry.buildables[registry.descriptors[desc]].display_name``.

   Matching is case-insensitive and tolerates one side's trailing ``s`` when the
   other side has none, only after the exact name has found nothing. **No row
   needs that licence today** -- the run prints the count, so a version of the
   game or the lab that starts leaning on it says so -- and the two lab names
   that still miss are in :data:`ITEM_OVERRIDES` with both names on the line.
   Eight lab ids have no game class at all and are in :data:`ITEMS_NOT_IN_GAME`
   with the reason; every other lab item must land in exactly one class or the
   run fails.

3. **Recipes.** By exact signature over *mapped* classes:
   ``(producer Build_*_C, duration, sorted (Desc_*_C, amount) ingredients,
   sorted products)``. Fluid amounts are multiplied by 1000 first -- the lab
   states m3 and the game centilitres -- and a fluid is a lab item with no
   ``stack``. Over the 291 game recipes that name a producer this signature is
   unique, which is what removes the 41 ambiguities a signature over *unmapped*
   items leaves behind. A lab recipe with no hit or several fails the run unless
   it is in :data:`UNMAPPED` with a reason, or carries the lab's ``mining`` flag
   -- a mining recipe is an extractor's output, and the game has no
   ``Recipe_*_C`` for it.

``--refresh-names`` is the only mode that reads the game install. It rewrites
``data/lab_item_names.json`` from
``$FLAB2BP_SATISFACTORY_DIR/CommunityResources/Docs/en-US.json`` and refuses if
that dump is not the one ``data/docs.json`` was built from, so the two never
drift apart; re-run step 1 of ``docs/sfy-regenerating-game-data.md`` first after
a game update. The default run reads only committed files, which is what lets
``tests/sfy/test_labmap.py`` re-derive the map and catch drift.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from flab2bp.lab.data import VENDORED_DIR, load_vendored
from flab2bp.lab.schema import Dataset
from flab2bp.lab.url import Game
from flab2bp.sfy.labmap import LAB_MAP_PATH
from flab2bp.sfy.registry import Registry, load_registry

DATA = Path(__file__).resolve().parents[1] / "src" / "flab2bp" / "sfy" / "data"
ITEM_NAMES_PATH = DATA / "lab_item_names.json"
DOCS_PATH = DATA / "docs.json"
LAB_DATASET_PATH = VENDORED_DIR / "sfy" / "data.json"

# The Docs.json property an item descriptor states its name in. A building
# descriptor leaves it empty and its ``Build_*_C`` carries the name instead,
# which is why :func:`_display_names` reads two pools rather than one.
DISPLAY_NAME_FIELD = "mDisplayName"

# The lab states fluid amounts in m3 and the game in centilitres.
FLUID_FACTOR = 1000

# Lab machine, belt and pipe item id -> the game class it builds, from section
# 3.7 of the M2 interface reference. Every row is checked against
# ``registry.buildables`` before the map is written.
MACHINES = {
    "constructor-id": "Build_ConstructorMk1_C",
    "assembler": "Build_AssemblerMk1_C",
    "manufacturer": "Build_ManufacturerMk1_C",
    "smelter": "Build_SmelterMk1_C",
    "foundry": "Build_FoundryMk1_C",
    "refinery": "Build_OilRefinery_C",
    "packager": "Build_Packager_C",
    "blender": "Build_Blender_C",
    "particle-accelerator": "Build_HadronCollider_C",
    "converter": "Build_Converter_C",
    "quantum-encoder": "Build_QuantumEncoder_C",
    "miner-mk1": "Build_MinerMk1_C",
    "miner-mk2": "Build_MinerMk2_C",
    "miner-mk3": "Build_MinerMk3_C",
    "water-extractor": "Build_WaterPump_C",
    "oil-extractor": "Build_OilPump_C",
    "resource-well-extractor": "Build_FrackingExtractor_C",
    "nuclear-power-plant": "Build_GeneratorNuclear_C",
    "space-elevator": "Build_SpaceElevator_C",
    "conveyor-belt-mk1": "Build_ConveyorBeltMk1_C",
    "conveyor-belt-mk2": "Build_ConveyorBeltMk2_C",
    "conveyor-belt-mk3": "Build_ConveyorBeltMk3_C",
    "conveyor-belt-mk4": "Build_ConveyorBeltMk4_C",
    "conveyor-belt-mk5": "Build_ConveyorBeltMk5_C",
    "conveyor-belt-mk6": "Build_ConveyorBeltMk6_C",
    # Docs.json names ``Build_Pipeline_C`` "Pipeline Mk.1" and
    # ``Build_PipelineMK2_C`` "Pipeline Mk.2", which is what the lab calls
    # ``pipeline-mk1`` and ``pipeline-mk2``. The ``_NoIndicator_C`` classes are
    # separate buildables the game names "Clean Pipeline Mk.1" and "Clean
    # Pipeline Mk.2", and the lab has no id for either.
    "pipeline-mk1": "Build_Pipeline_C",
    "pipeline-mk2": "Build_PipelineMK2_C",
}

# The lab names these two differently from the game, so the display-name rule
# finds nothing. Each line states both names; the run checks that the class
# exists and that its own display name is the one quoted.
ITEM_OVERRIDES = {
    # lab "SAM Ore" -> game "SAM"
    "sam-ore": ("Desc_SAM_C", "SAM"),
    # lab "Iodine Infused Filter" -> game "Iodine-Infused Filter" (hyphenated)
    "iodine-infused-filter": ("Desc_HazmatFilter_C", "Iodine-Infused Filter"),
}

# Lab ids the game has no descriptor for at all. These are not drift to chase:
# the lab invented them, so no override could point anywhere.
ITEMS_NOT_IN_GAME = {
    "purity-0": "a FactorioLab module standing for an Impure resource node, not a game item",
    "purity-1": "a FactorioLab module standing for a Normal resource node, not a game item",
    "purity-2": "a FactorioLab module standing for a Pure resource node, not a game item",
    "phase-1": "a Project Assembly milestone, which the game keeps as a schematic",
    "phase-2": "a Project Assembly milestone, which the game keeps as a schematic",
    "phase-3": "a Project Assembly milestone, which the game keeps as a schematic",
    "phase-4": "a Project Assembly milestone, which the game keeps as a schematic",
    "phase-5": "a Project Assembly milestone, which the game keeps as a schematic",
}

# Lab recipes with no ``Recipe_*_C``, beyond the thirteen the lab flags
# ``mining`` (which :data:`MINING_REASON` covers as a class). Each says why.
UNMAPPED = {
    "phase-1": "the Space Elevator consumes a Project Assembly phase as a schematic, not a recipe",
    "phase-2": "the Space Elevator consumes a Project Assembly phase as a schematic, not a recipe",
    "phase-3": "the Space Elevator consumes a Project Assembly phase as a schematic, not a recipe",
    "phase-4": "the Space Elevator consumes a Project Assembly phase as a schematic, not a recipe",
    "phase-5": "the Space Elevator consumes a Project Assembly phase as a schematic, not a recipe",
    "uranium-waste": (
        "the Nuclear Power Plant burns a fuel rod rather than crafting: no game recipe "
        "names Build_GeneratorNuclear_C as a producer"
    ),
    "plutonium-waste": (
        "the Nuclear Power Plant burns a fuel rod rather than crafting: no game recipe "
        "names Build_GeneratorNuclear_C as a producer"
    ),
}

MINING_REASON = (
    "a FactorioLab mining recipe: the extractor's own output, which the game states on the "
    "extractor rather than as a Recipe_*_C"
)

# Lab recipes whose game class is certain but whose signature does not match,
# because FactorioLab states the amounts at a different multiple. The lab's
# numbers stand -- its flow is authoritative -- and the run still checks that
# the named class has the same producer, the same duration and the same item
# classes on both sides, so an override cannot quietly point at the wrong
# recipe.
RECIPE_OVERRIDES = {
    # FactorioLab doubles this one: 2 packaged -> 2 acid + 2 canisters, where
    # the game states 1 -> 1000 cL + 1. Same recipe, stated per two units.
    "unpackage-sulfuric-acid": "Recipe_UnpackageSulfuricAcid_C",
}


class DerivationError(SystemExit):
    """The derivation could not resolve an id, and refuses to guess one."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalise(name: str) -> str:
    return " ".join(name.split()).casefold()


def _name_keys(name: str) -> tuple[str, str]:
    """The normalised name, then its one-``s`` plural or singular counterpart.

    The lab writes ``Screw`` where the game writes ``Screws``, and ``Alien
    Protein`` where the game writes ``Alien Protein``: tolerating a trailing
    ``s`` on one side when the other has none -- never stripping or adding one
    on both, and only after the exact name has found nothing -- is the whole of
    the licence taken here. The run reports how many rows needed it.
    """
    key = _normalise(name)
    other = key[:-1] if key.endswith("s") else key + "s"
    return (key, other)


def _display_names(registry: Registry) -> dict[str, str]:
    """Every game display name an item descriptor can be found under.

    An item descriptor states its own ``mDisplayName``, collected by
    ``--refresh-names``; a *building* descriptor leaves it empty and its
    buildable carries the name, so ``registry.descriptors`` supplies the second
    pool. A class in both keeps the descriptor's own name, which is the more
    specific of the two.
    """
    stated = _load_item_names()
    pool: dict[str, str] = {}
    for descriptor, buildable in registry.descriptors.items():
        if descriptor not in registry.item_paths:
            continue
        entry = registry.buildables.get(buildable)
        if entry is not None and entry.display_name:
            pool[descriptor] = entry.display_name
    for class_name, name in stated.items():
        if class_name in registry.item_paths and name:
            pool[class_name] = name
    return pool


def _load_item_names() -> dict[str, str]:
    try:
        payload = json.loads(ITEM_NAMES_PATH.read_text(encoding="utf-8"))
    except OSError:
        raise DerivationError(
            f"no item display names at {ITEM_NAMES_PATH}; run "
            "`uv run python scripts/sfy_lab_map.py --refresh-names` against a game install"
        ) from None
    names = payload.get("display_names")
    if not isinstance(names, dict) or not names:
        raise DerivationError(f"{ITEM_NAMES_PATH} carries no 'display_names' section")
    return {str(k): str(v) for k, v in names.items()}


def _machines(registry: Registry) -> dict[str, str]:
    missing = sorted(c for c in MACHINES.values() if c not in registry.buildables)
    if missing:
        raise DerivationError(
            f"the machine table names {len(missing)} classes registry.buildables does not "
            f"carry: {missing}; the game has renamed them, so section 3.7's table is stale"
        )
    return dict(MACHINES)


def _check_machines(
    machines: Mapping[str, str],
    items: Mapping[str, str],
    dataset: Dataset,
    registry: Registry,
) -> None:
    """Derive each machine row a second way, and require the two to agree.

    The table is written out by hand because no convention produces
    ``refinery -> Build_OilRefinery_C``. It is still derivable, though, once the
    item table exists: a lab machine id is also a lab *item*, so it has a
    ``Desc_*_C``, and ``registry.descriptors`` says which ``Build_*_C`` that
    descriptor builds. Every row must come out the same both ways, and a row
    naming a lab id the dataset no longer carries is stale.
    """
    known = {item.id for item in dataset.items}
    stale = sorted(lab_id for lab_id in machines if lab_id not in known)
    if stale:
        raise DerivationError(f"the machine table names lab ids the dataset no longer has: {stale}")
    disagree: list[str] = []
    for lab_id, build_class in sorted(machines.items()):
        descriptor = items.get(lab_id)
        if descriptor is None:
            disagree.append(f"{lab_id!r} has no item class, so nothing corroborates {build_class}")
            continue
        built = registry.descriptors.get(descriptor)
        if built != build_class:
            disagree.append(
                f"{lab_id!r} -> {descriptor} -> {built}, but the table says {build_class}"
            )
    if disagree:
        raise DerivationError(
            f"{len(disagree)} machine rows do not survive being derived from the item table "
            "and registry.descriptors:\n  " + "\n  ".join(disagree)
        )


def _items(dataset: Dataset, registry: Registry) -> tuple[dict[str, str], tuple[str, ...]]:
    """The item table, and the lab ids that needed the trailing-``s`` licence."""
    pool = _display_names(registry)
    by_name: dict[str, set[str]] = defaultdict(set)
    for class_name, name in pool.items():
        by_name[_normalise(name)].add(class_name)

    items: dict[str, str] = {}
    plural_matched: list[str] = []
    unresolved: list[str] = []
    for item in dataset.items:
        if item.id in ITEM_OVERRIDES:
            class_name, stated = ITEM_OVERRIDES[item.id]
            if class_name not in registry.item_paths:
                raise DerivationError(
                    f"the {item.id!r} override names {class_name}, which registry.item_paths "
                    "does not carry"
                )
            found = pool.get(class_name)
            if found is None or _normalise(found) != _normalise(stated):
                raise DerivationError(
                    f"the {item.id!r} override says {class_name} is called {stated!r}, but the "
                    f"game calls it {found!r}; the override is stale"
                )
            items[item.id] = class_name
            continue
        if item.id in ITEMS_NOT_IN_GAME:
            continue
        exact, plural = _name_keys(item.name)
        candidates = by_name.get(exact, set())
        by_licence = not candidates
        if by_licence:
            candidates = by_name.get(plural, set())
        if len(candidates) == 1:
            items[item.id] = next(iter(candidates))
            if by_licence:
                plural_matched.append(item.id)
        else:
            unresolved.append(f"{item.id!r} ({item.name!r}) -> {sorted(candidates)}")
    if unresolved:
        raise DerivationError(
            f"{len(unresolved)} lab items match no game descriptor, or more than one. Add a "
            "commented ITEM_OVERRIDES entry for each, or an ITEMS_NOT_IN_GAME reason:\n  "
            + "\n  ".join(unresolved)
        )
    stale = sorted(k for k in ITEM_OVERRIDES if k not in items)
    stale += sorted(k for k in ITEMS_NOT_IN_GAME if k not in {i.id for i in dataset.items})
    if stale:
        raise DerivationError(f"these item entries name lab ids the dataset no longer has: {stale}")
    return items, tuple(plural_matched)


def _fluids(dataset: Dataset) -> frozenset[str]:
    """The lab ids that are fluids, which is the ids with no ``stack``."""
    return frozenset(item.id for item in dataset.items if item.stack is None)


def _amount(lab_item_id: str, quantity: Fraction, fluids: frozenset[str]) -> int:
    scaled = quantity * FLUID_FACTOR if lab_item_id in fluids else quantity
    if scaled.denominator != 1:
        raise DerivationError(
            f"the lab states a fractional amount for {lab_item_id!r} ({quantity}), which no "
            "game recipe can carry"
        )
    return int(scaled)


Signature = tuple[str, Fraction, tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]
#: A signature with the amounts dropped: what an override is checked against.
RelaxedSignature = tuple[str, Fraction, frozenset[str], frozenset[str]]


def _duration(seconds: float) -> Fraction:
    """A game duration as the exact decimal the game data states.

    Both sides are compared as :class:`~fractions.Fraction`, never as floats.
    ``Fraction(seconds)`` would be wrong: the game states 2.4 s for four
    recipes, whose ``float`` is 2.399999999999999911182…, while the lab states
    ``Fraction(12, 5)``. ``repr`` of a float is the shortest decimal that reads
    back as that float, so ``Fraction(str(...))`` recovers the literal the JSON
    carried, which is the number both files are quoting.
    """
    return Fraction(str(seconds))


def _signature(
    producer: str,
    duration: Fraction,
    ingredients: tuple[tuple[str, int], ...],
    products: tuple[tuple[str, int], ...],
) -> Signature:
    return (producer, duration, tuple(sorted(ingredients)), tuple(sorted(products)))


def _relax(signature: Signature) -> RelaxedSignature:
    producer, duration, ingredients, products = signature
    return (
        producer,
        duration,
        frozenset(class_name for class_name, _ in ingredients),
        frozenset(class_name for class_name, _ in products),
    )


def _game_signatures(registry: Registry) -> tuple[dict[Signature, list[str]], dict[Any, list[str]]]:
    """Every game recipe that names a producer, keyed by signature and relaxed one.

    A recipe with no producer is a manual or workshop craft: nothing places a
    building for it, so it cannot be what a lab producer recipe means.
    """
    exact: dict[Signature, list[str]] = defaultdict(list)
    relaxed: dict[Any, list[str]] = defaultdict(list)
    for class_name, recipe in registry.recipes.items():
        if not recipe.producers:
            continue
        key = _signature(
            recipe.producers[0],
            _duration(recipe.duration_s),
            recipe.ingredients,
            recipe.products,
        )
        exact[key].append(class_name)
        relaxed[_relax(key)].append(class_name)
    return exact, relaxed


def _override_recipe(
    lab_id: str, class_name: str, signature: Signature, relaxed: Mapping[Any, list[str]]
) -> None:
    """Check an override names the *only* recipe the lab row can be.

    An override exists because the two sides state the same recipe at different
    multiples, so the amounts are dropped and producer, duration and the item
    classes on both sides must still pick out exactly one game recipe. Checking
    only that the *named* class fits would let an override point at the wrong
    sibling of a shared relaxed signature and pass, so the rule is uniqueness
    first and the name second: if more than one recipe fits, the override is not
    evidence of anything and the run fails.
    """
    candidates = relaxed.get(_relax(signature), [])
    if len(candidates) != 1:
        raise DerivationError(
            f"the {lab_id!r} override cannot be checked: dropping the amounts leaves "
            f"{len(candidates)} game recipes with this producer, duration and item classes "
            f"({sorted(candidates)}), so nothing says which one the lab row is"
        )
    if candidates[0] != class_name:
        raise DerivationError(
            f"the {lab_id!r} override names {class_name}, but the only recipe with this "
            f"producer, duration and item classes is {candidates[0]}"
        )


def _recipes(
    dataset: Dataset, registry: Registry, items: Mapping[str, str], machines: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    fluids = _fluids(dataset)
    index, relaxed = _game_signatures(registry)
    ambiguous_game = {k: v for k, v in index.items() if len(v) > 1}
    if ambiguous_game:
        raise DerivationError(
            f"{len(ambiguous_game)} game signatures name more than one recipe, so no lab recipe "
            f"can be resolved by one: {sorted(v for v in ambiguous_game.values())[:5]}"
        )

    mapped: dict[str, str] = {}
    unmapped: dict[str, str] = {}
    unresolved: list[str] = []
    for recipe in dataset.recipes:
        if "mining" in recipe.flags:
            unmapped[recipe.id] = MINING_REASON
            continue
        if recipe.id in UNMAPPED:
            unmapped[recipe.id] = UNMAPPED[recipe.id]
            continue
        if len(recipe.producers) != 1:
            raise DerivationError(
                f"the lab recipe {recipe.id!r} names {len(recipe.producers)} producers "
                f"({recipe.producers}); a signature needs exactly one"
            )
        producer = machines.get(recipe.producers[0])
        if producer is None:
            raise DerivationError(
                f"the lab recipe {recipe.id!r} is made by {recipe.producers[0]!r}, which the "
                "machine table does not carry"
            )
        missing = sorted({*recipe.inputs, *recipe.outputs} - set(items))
        if missing:
            raise DerivationError(
                f"the lab recipe {recipe.id!r} names items with no game class: {missing}"
            )
        signature = _signature(
            producer,
            recipe.time,
            tuple((items[i], _amount(i, q, fluids)) for i, q in recipe.inputs.items()),
            tuple((items[i], _amount(i, q, fluids)) for i, q in recipe.outputs.items()),
        )
        if recipe.id in RECIPE_OVERRIDES:
            class_name = RECIPE_OVERRIDES[recipe.id]
            _override_recipe(recipe.id, class_name, signature, relaxed)
            mapped[recipe.id] = class_name
            continue
        candidates = index.get(signature, [])
        if len(candidates) == 1:
            mapped[recipe.id] = candidates[0]
        else:
            unresolved.append(f"{recipe.id!r} ({recipe.name!r}) -> {sorted(candidates)}")
    if unresolved:
        raise DerivationError(
            f"{len(unresolved)} lab recipes match no game recipe, or more than one. Add a "
            "commented RECIPE_OVERRIDES entry or an UNMAPPED reason for each:\n  "
            + "\n  ".join(unresolved)
        )
    stale = sorted(
        k for k in (*UNMAPPED, *RECIPE_OVERRIDES) if k not in unmapped and k not in mapped
    )
    if stale:
        raise DerivationError(
            f"these recipe entries name lab ids the dataset no longer has: {stale}"
        )
    # Two lab recipes landing on one game class would mean the signature is not
    # the identity it is being used as.
    seen: dict[str, str] = {}
    for lab_id, class_name in mapped.items():
        if class_name in seen:
            raise DerivationError(
                f"{class_name} is claimed by both {seen[class_name]!r} and {lab_id!r}"
            )
        seen[class_name] = lab_id
    return mapped, unmapped


def _refresh_names() -> int:
    """Rewrite ``lab_item_names.json`` from the game's own Docs.json dump."""
    from flab2bp.sfy import docs

    dump = docs.docs_path()
    if not dump.exists():
        raise DerivationError(
            f"no Docs.json at {dump}; set FLAB2BP_SATISFACTORY_DIR to a game install"
        )
    digest = _sha256(dump)
    committed = json.loads(DOCS_PATH.read_text(encoding="utf-8"))["provenance"]["docs_sha256"]
    if digest != committed:
        raise DerivationError(
            f"the install's Docs.json ({digest[:12]}) is not the dump data/docs.json was built "
            f"from ({committed[:12]}); re-run step 1 of docs/sfy-regenerating-game-data.md first"
        )
    registry = load_registry()
    names: dict[str, str] = {}
    for entries in docs.load_docs(dump).values():
        for entry in entries:
            class_name = str(entry.get("ClassName", ""))
            name = str(entry.get(DISPLAY_NAME_FIELD, ""))
            if class_name in registry.item_paths and name:
                names[class_name] = name
    payload = {
        "display_names": dict(sorted(names.items())),
        "provenance": {
            "docs_sha256": digest,
            "extracted": dt.date.today().isoformat(),
            "field": DISPLAY_NAME_FIELD,
            "source": str(docs.DOCS_RELATIVE_PATH),
        },
    }
    ITEM_NAMES_PATH.write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"item display names: {len(names)} of {len(registry.item_paths)} item descriptors")
    return 0


@dataclass(frozen=True, slots=True)
class Derived:
    """The map, and what the run wants to say about how it got there."""

    payload: dict[str, Any]
    plural_matched: tuple[str, ...]


def _check_item_names_are_current() -> str:
    """Refuse a names file taken from a different Docs.json than ``docs.json``.

    The two are extracts of the same dump and the map joins them, so a stale
    names file would name items from one build of the game against a registry
    from another. Returns the digest both agree on.
    """
    names = json.loads(ITEM_NAMES_PATH.read_text(encoding="utf-8"))["provenance"]["docs_sha256"]
    docs = json.loads(DOCS_PATH.read_text(encoding="utf-8"))["provenance"]["docs_sha256"]
    if names != docs:
        raise DerivationError(
            f"{ITEM_NAMES_PATH.name} was taken from Docs.json {names[:12]} but data/docs.json "
            f"was built from {docs[:12]}; re-run `--refresh-names` against the install "
            "data/docs.json now describes"
        )
    return str(names)


def derive() -> Derived:
    """The whole map, ready to write."""
    docs_sha256 = _check_item_names_are_current()
    registry = load_registry()
    dataset = load_vendored(Game.SFY)
    machines = _machines(registry)
    items, plural_matched = _items(dataset, registry)
    _check_machines(machines, items, dataset, registry)
    recipes, unmapped = _recipes(dataset, registry, items, machines)

    covered = set(items) | set(ITEMS_NOT_IN_GAME)
    dropped = sorted(item.id for item in dataset.items if item.id not in covered)
    if dropped:
        raise DerivationError(f"{len(dropped)} lab items would be dropped silently: {dropped}")
    stray = sorted(set(items.values()) - set(registry.item_paths))
    if stray:
        raise DerivationError(f"these item classes are not in registry.item_paths: {stray}")
    stray = sorted(set(recipes.values()) - set(registry.recipes))
    if stray:
        raise DerivationError(f"these recipe classes are not in registry.recipes: {stray}")

    return Derived(
        payload={
            "items": dict(sorted(items.items())),
            "machines": dict(sorted(machines.items())),
            "provenance": {
                "derived": dt.date.today().isoformat(),
                "item_names_docs_sha256": docs_sha256,
                "lab_dataset_sha256": _sha256(LAB_DATASET_PATH),
                "registry_inputs_sha256": registry.provenance["inputs_sha256"],
            },
            "recipes": dict(sorted(recipes.items())),
            "unmapped_recipes": dict(sorted(unmapped.items())),
        },
        plural_matched=plural_matched,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="where to write the map")
    parser.add_argument(
        "--refresh-names",
        action="store_true",
        help="rewrite data/lab_item_names.json from the game install, then stop",
    )
    args = parser.parse_args(argv)
    if args.refresh_names:
        return _refresh_names()

    derived = derive()
    payload = derived.payload
    target = LAB_MAP_PATH if args.out is None else Path(args.out)
    target.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"items: {len(payload['items'])} mapped, {len(ITEMS_NOT_IN_GAME)} with no game class "
        f"({len(ITEM_OVERRIDES)} overrides, "
        f"{len(derived.plural_matched)} through the trailing-'s' licence)"
    )
    print(
        f"recipes: {len(payload['recipes'])} mapped "
        f"({len(RECIPE_OVERRIDES)} overrides), "
        f"{len(payload['unmapped_recipes'])} unmapped"
    )
    for reason in sorted(set(payload["unmapped_recipes"].values())):
        ids = sorted(k for k, v in payload["unmapped_recipes"].items() if v == reason)
        print(f"  {len(ids)}: {reason}\n    {ids}")
    print(f"machines: {len(payload['machines'])} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
