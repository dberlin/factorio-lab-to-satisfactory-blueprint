"""Read the game's own ``Docs.json`` dump into the registry's first half.

``CommunityResources/Docs/en-US.json`` ships with every Satisfactory install. It
is UTF-16 JSON: a list of ``{"NativeClass": ..., "Classes": [...]}`` groups whose
class dicts hold every UPROPERTY the game exports, as strings -- numbers are
``"4.000000"``, vectors are ``"(X=6,Y=6,Z=6)"`` and structs are Unreal text
exports such as ``mClearanceData``.

:func:`extract` walks that dump and produces the JSON written to
``data/docs.json``: buildables (with clearance boxes and the stats the placer
needs), recipes, the descriptor-to-buildable name map and each buildable's build
recipe. Ports and the spline/hologram limits are not in Docs.json at all; Task 10
harvests them from the install into ``data/registry.json``.

Regenerate the committed file with ``uv run python -m flab2bp.sfy.docs``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flab2bp.sfy.registry import DATA_DIR, ClearanceBox

__all__ = [
    "DOCS_JSON_PATH",
    "DOCS_RELATIVE_PATH",
    "DocsParseError",
    "docs_path",
    "extract",
    "load_docs",
    "parse_class_list",
    "parse_clearance",
    "parse_item_amounts",
    "parse_vector",
    "quaternion_to_rotator",
    "satisfactory_dir",
    "write_docs_json",
]

DOCS_RELATIVE_PATH = Path("CommunityResources") / "Docs" / "en-US.json"
DOCS_JSON_PATH = DATA_DIR / "docs.json"

_VECTOR_RE = re.compile(r"X=(-?[0-9.]+),Y=(-?[0-9.]+),Z=(-?[0-9.]+)")
_MIN_RE = re.compile(r"Min=\(([^)]*)\)")
_MAX_RE = re.compile(r"Max=\(([^)]*)\)")
_TRANSLATION_RE = re.compile(r"Translation=\(([^)]*)\)")
_SCALE_RE = re.compile(r"Scale3D=\(([^)]*)\)")
# The clearance transform's rotation is exported as a quaternion, unlike a
# component's ``RelativeRotation``, which is a rotator.
_QUAT_RE = re.compile(r"Rotation=\(X=(-?[0-9.]+),Y=(-?[0-9.]+),Z=(-?[0-9.]+),W=(-?[0-9.]+)\)")
# Every item entry names a blueprint class path; the class is the last dotted
# segment of the path, inside the quoted asset reference.
_ITEM_AMOUNT_RE = re.compile(r"ItemClass=\"[^\"]*\.([A-Za-z0-9_]+_C)'\",Amount=(\d+)")
_CLASS_PATH_RE = re.compile(r"\.([A-Za-z0-9_]+_C)\"")
_ZERO = (0.0, 0.0, 0.0)
_ONE = (1.0, 1.0, 1.0)
# ``FQuat::Rotator``'s own threshold: past it the pitch is at a pole and the yaw
# and roll stop being separable.
_SINGULARITY_THRESHOLD = 0.4999995


class DocsParseError(ValueError):
    """A Docs.json value did not have the shape this module knows how to read.

    Raised instead of returning a partial parse: a half-read clearance box or a
    dropped ingredient would silently produce a registry that disagrees with the
    game.
    """


def satisfactory_dir() -> Path:
    """The Satisfactory install root (``FLAB2BP_SATISFACTORY_DIR``, default ``~/Satisfactory``)."""
    return Path(os.environ.get("FLAB2BP_SATISFACTORY_DIR", "~/Satisfactory")).expanduser()


def docs_path() -> Path:
    """Path to the installed ``en-US.json`` documentation dump."""
    return satisfactory_dir() / DOCS_RELATIVE_PATH


def load_docs(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load the dump as ``{short native class name: [class dicts]}``.

    The file is UTF-16 with a BOM, and ``NativeClass`` is a full Unreal class
    path (``...FactoryGame.FGBuildableManufacturer'``) of which only the last
    segment is useful.
    """
    path = docs_path() if path is None else Path(path)
    entries = json.loads(path.read_bytes().decode("utf-16"))
    return {_short_class(entry["NativeClass"]): entry["Classes"] for entry in entries}


def _short_class(native_class: str) -> str:
    return native_class.rsplit(".", 1)[-1].removesuffix("'")


def parse_vector(text: str) -> tuple[float, float, float]:
    """Read ``(X=..,Y=..,Z=..)`` into a float triple, ignoring any other members."""
    match = _VECTOR_RE.search(text)
    if match is None:
        raise DocsParseError(f"no X/Y/Z vector in {text!r}")
    return (float(match[1]), float(match[2]), float(match[3]))


def quaternion_to_rotator(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    """An Unreal quaternion as its ``(pitch, yaw, roll)`` rotator, in degrees.

    This is ``FQuat::Rotator()``, including its singularity handling at the
    poles, so that a clearance box's rotation is spelled the same way as a
    port's ``RelativeRotation``, which the game exports as a rotator already.
    """
    singularity = z * x - w * y
    yaw = math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))
    if abs(singularity) > _SINGULARITY_THRESHOLD:
        sign = 1.0 if singularity > 0.0 else -1.0
        roll = _normalize_axis(sign * yaw - 2.0 * math.degrees(math.atan2(x, w)))
        return (90.0 * sign, yaw, roll)
    pitch = math.degrees(math.asin(2.0 * singularity))
    roll = math.degrees(math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
    return (pitch, yaw, roll)


def _normalize_axis(degrees: float) -> float:
    """Fold an angle into (-180, 180], as ``FRotator::NormalizeAxis`` does."""
    folded = math.fmod(degrees, 360.0)
    if folded > 180.0:
        folded -= 360.0
    elif folded <= -180.0:
        folded += 360.0
    return folded


def parse_clearance(text: str) -> tuple[ClearanceBox, ...]:
    """Read an ``mClearanceData`` text export into clearance boxes.

    Each element of the outer list is one ``FFGClearanceData``: a mandatory
    ``ClearanceBox`` with ``Min``/``Max``, an optional ``Type=CT_Soft`` marker,
    an optional ``ExcludeForSnapping`` flag and an optional ``RelativeTransform``
    that places the box on the buildable. Unreal's text export omits any member
    equal to its default, so an absent ``Translation`` is the origin, an absent
    ``Rotation`` is the identity and an absent ``Scale3D`` is ``(1, 1, 1)``.

    All three parts of the transform are read: 37 boxes in the shipped dump
    carry a rotation, and reading only ``Min``/``Max`` puts a barrier's box a
    quarter turn away from where the game has it.
    """
    text = text.strip()
    if not text or text == "()":
        return ()
    return tuple(_parse_clearance_box(element) for element in _split_top_level(text))


def _parse_clearance_box(element: str) -> ClearanceBox:
    low, high = _MIN_RE.search(element), _MAX_RE.search(element)
    if low is None or high is None:
        raise DocsParseError(f"clearance entry has no Min/Max box: {element!r}")
    translation = _TRANSLATION_RE.search(element)
    rotation = _QUAT_RE.search(element)
    scale = _SCALE_RE.search(element)
    return ClearanceBox(
        min=parse_vector(low[1]),
        max=parse_vector(high[1]),
        soft="Type=CT_Soft" in element,
        translation=_ZERO if translation is None else parse_vector(translation[1]),
        rotation=_ZERO
        if rotation is None
        else quaternion_to_rotator(*(float(v) for v in rotation.groups())),
        scale=_ONE if scale is None else parse_vector(scale[1]),
        exclude_for_snapping="ExcludeForSnapping=True" in element,
    )


def _split_top_level(text: str) -> list[str]:
    """Split ``(a,(b,c),d)`` into its top-level elements, ignoring nested commas."""
    if not (text.startswith("(") and text.endswith(")")):
        raise DocsParseError(f"not a parenthesised list: {text!r}")
    elements: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text[1:-1]:
        if char == "," and depth == 0:
            elements.append("".join(current))
            current = []
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise DocsParseError(f"unbalanced parentheses in {text!r}")
        current.append(char)
    if depth != 0:
        raise DocsParseError(f"unbalanced parentheses in {text!r}")
    elements.append("".join(current))
    return elements


def parse_item_amounts(text: str) -> tuple[tuple[str, int], ...]:
    """Read an ``mIngredients``/``mProduct`` export into ``(item class, amount)`` pairs.

    Amounts are the raw game numbers: whole items for solids, centilitres for
    fluids. Every ``ItemClass=`` in the text must parse, or the whole export is
    rejected -- a recipe missing an ingredient is worse than no recipe.
    """
    pairs = tuple((match[1], int(match[2])) for match in _ITEM_AMOUNT_RE.finditer(text))
    if len(pairs) != text.count("ItemClass="):
        raise DocsParseError(f"unreadable item entry in {text!r}")
    return pairs


def parse_class_list(text: str) -> tuple[str, ...]:
    """Read a parenthesised list of quoted class paths into blueprint class names.

    Entries that name a native class rather than a blueprint one (``mProducedIn``
    lists ``/Script/FactoryGame.FGBuildableAutomatedWorkBench``) have no ``_C``
    suffix and are not class names at all, so they are skipped.
    """
    return tuple(_CLASS_PATH_RE.findall(text))


def _number(entry: Mapping[str, Any], key: str, *, default: float | None = None) -> float | None:
    raw = entry.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise DocsParseError(f"{entry.get('ClassName')}.{key}: {raw!r} is not a number") from exc


def _integer(entry: Mapping[str, Any], key: str) -> int | None:
    value = _number(entry, key)
    return None if value is None else int(value)


def _designer_dims(entry: Mapping[str, Any]) -> list[int] | None:
    raw = entry.get("mDimensions")
    if raw is None or raw == "":
        return None
    try:
        return [int(v) for v in parse_vector(raw)]
    except DocsParseError as exc:
        raise DocsParseError(f"{entry.get('ClassName')}.mDimensions: {exc}") from exc


def _clearance(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        boxes = parse_clearance(entry.get("mClearanceData", ""))
    except DocsParseError as exc:
        raise DocsParseError(f"{entry.get('ClassName')}.mClearanceData: {exc}") from exc
    return [
        {
            "min": list(box.min),
            "max": list(box.max),
            "soft": box.soft,
            "translation": list(box.translation),
            "rotation": list(box.rotation),
            "scale": list(box.scale),
            "exclude_for_snapping": box.exclude_for_snapping,
        }
        for box in boxes
    ]


def _buildable(entry: Mapping[str, Any], native_class: str) -> dict[str, Any]:
    return {
        "display_name": entry["mDisplayName"],
        "native_class": native_class,
        "clearance": _clearance(entry),
        # Buildings with no mPowerConsumption draw nothing, not "unknown".
        "power_mw": _number(entry, "mPowerConsumption", default=0.0),
        "manufacturing_speed": _number(entry, "mManufacturingSpeed"),
        "belt_speed_per_min": _number(entry, "mSpeed"),
        "mesh_height_cm": _number(entry, "mMeshHeight"),
        # A conveyor belt's own cost segment: AFGBuildableConveyorBelt::
        # GetDismantleRefundReturnsMultiplier divides the belt's length by it
        # (a lift divides its height by mMeshHeight above). See the belt.cost
        # rule in data/hologram_rules.json.
        "mesh_length_cm": _number(entry, "mMeshLength"),
        "width_cm": _number(entry, "mWidth"),
        "depth_cm": _number(entry, "mDepth"),
        "height_cm": _number(entry, "mHeight"),
        "designer_dims": _designer_dims(entry),
        "max_potential": _number(entry, "mMaxPotential"),
        "potential_shard_slots": _integer(entry, "mPotentialShardSlots"),
        "production_boost_slots": _integer(entry, "mProductionShardSlotSize"),
    }


def _recipe(entry: Mapping[str, Any]) -> dict[str, Any]:
    class_name = entry["ClassName"]
    try:
        ingredients = parse_item_amounts(entry.get("mIngredients", ""))
        products = parse_item_amounts(entry.get("mProduct", ""))
    except DocsParseError as exc:
        raise DocsParseError(f"{class_name}: {exc}") from exc
    duration = _number(entry, "mManufactoringDuration", default=0.0)
    return {
        "display_name": entry["mDisplayName"],
        "ingredients": [[item, amount] for item, amount in ingredients],
        "products": [[item, amount] for item, amount in products],
        "duration_s": duration,
        # Hand-craft-only recipes keep an empty producer list; the layout ignores
        # them, but dropping them would hide them from the recipe index.
        "producers": [
            producer
            for producer in parse_class_list(entry.get("mProducedIn", ""))
            if producer.startswith("Build_")
        ],
        "variable_power_constant": _number(entry, "mVariablePowerConsumptionConstant", default=0.0),
        "variable_power_factor": _number(entry, "mVariablePowerConsumptionFactor", default=0.0),
    }


def extract(
    docs: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    docs_sha256: str | None = None,
    build_version_hint: int | None = None,
    extracted: str | None = None,
) -> dict[str, Any]:
    """Turn a loaded dump into the ``docs.json`` payload.

    Buildables come from every ``FGBuildable*`` native class, recipes from
    ``FGRecipe`` and the descriptor map from ``FGBuildingDescriptor``.
    Descriptors carry no link back to their buildable, so the map is by name
    (``Desc_X_C`` <-> ``Build_X_C``); a buildable's build recipe is the recipe
    whose single product is that descriptor.
    """
    buildables: dict[str, dict[str, Any]] = {}
    for native_class, classes in docs.items():
        if not native_class.startswith("FGBuildable"):
            continue
        for entry in classes:
            buildables[entry["ClassName"]] = _buildable(entry, native_class)

    recipes = {entry["ClassName"]: _recipe(entry) for entry in docs.get("FGRecipe", ())}

    descriptors: dict[str, str] = {}
    for entry in docs.get("FGBuildingDescriptor", ()):
        descriptor = entry["ClassName"]
        if not descriptor.startswith("Desc_"):
            continue
        buildable = "Build_" + descriptor.removeprefix("Desc_")
        if buildable in buildables:
            descriptors[descriptor] = buildable

    build_recipes: dict[str, str] = {}
    for recipe_class, recipe in recipes.items():
        products = recipe["products"]
        if len(products) != 1:
            continue
        buildable = descriptors.get(products[0][0])
        if buildable is not None:
            # First recipe wins; the shipped data has exactly one per buildable.
            build_recipes.setdefault(buildable, recipe_class)

    return {
        "provenance": {
            "docs_sha256": docs_sha256,
            "extracted": extracted or datetime.now(UTC).date().isoformat(),
            "build_version_hint": build_version_hint,
        },
        "buildables": buildables,
        "recipes": recipes,
        "descriptors": descriptors,
        "build_recipes": build_recipes,
    }


def newest_fixture_build_version(fixtures_dir: Path | None = None) -> int | None:
    """The highest game build version among the blueprint fixtures, if any.

    Docs.json does not state the build it came from, so the provenance records
    the newest build seen in the corpus as a hint: the dump was taken from an
    install at least that new.
    """
    from flab2bp.sfy.archive import Reader
    from flab2bp.sfy.header import read_header

    if fixtures_dir is None:
        fixtures_dir = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sfy"
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.is_dir():
        return None
    versions = [
        read_header(Reader(path.read_bytes())).build_version for path in fixtures_dir.glob("*.sbp")
    ]
    return max(versions, default=None)


def write_docs_json(out_path: Path | None = None, docs_file: Path | None = None) -> Path:
    """Regenerate the committed ``data/docs.json`` from the installed dump."""
    source = docs_path() if docs_file is None else Path(docs_file)
    target = DOCS_JSON_PATH if out_path is None else Path(out_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    payload = extract(
        load_docs(source),
        docs_sha256=digest,
        build_version_hint=newest_fixture_build_version(),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main() -> None:
    path = write_docs_json()
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
