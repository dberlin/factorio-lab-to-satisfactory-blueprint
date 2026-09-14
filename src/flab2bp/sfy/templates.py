"""Clone objects out of the fixture blueprints into a blueprint we author.

Nothing here invents an object. A buildable the game writes carries properties
this project does not model -- a ``PlayerInfoHandle``, a customization struct,
cached factory-leg offsets -- so the way to author one is to copy an object the
game wrote and change the few things that are ours: where it stands, what it is
called, what it is wired to, and which recipe it runs.

:class:`TemplateLibrary` collects one such object per buildable class from the
fixture corpus and :meth:`TemplateLibrary.instantiate` stamps out copies of it.
A copy keeps everything but the links back into the blueprint it came from:
those name objects the new file does not contain, so every reference into the
source level is rewritten (when it points inside the copied actor) or dropped
(when it points anywhere else), and splines start empty. "Every reference" means
every one the property tree exposes -- object values, array elements and struct
fields. A ``MapProperty`` or ``SetProperty`` value is carried as raw bytes by
:mod:`flab2bp.sfy.properties`, so a reference inside one would be invisible
here; no object in the fixture corpus has either.

Everything authored here is in the modern (save version 58 and up) tag format,
because that is the family :data:`TEMPLATE_MIN_SAVE_VERSION` admits as a
template. ``write_object_data`` refuses a property whose tag is in the file's
other format, so a tag built wrongly fails loudly at write time.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from flab2bp.sfy.archive import ObjectRef
from flab2bp.sfy.codec import Blueprint, read_sbp_file
from flab2bp.sfy.header import BlueprintHeader, ItemAmount, SaveObjectVersionData
from flab2bp.sfy.objects import ACTOR, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import (
    TAG_NATIVE_SERIALIZE,
    Array,
    Object,
    Property,
    PropertyList,
    Struct,
    Tag,
    Value,
    Vector,
)
from flab2bp.sfy.query import find
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.trailers import trailer_for_new

__all__ = [
    "BLUEPRINT_HEADER_VERSION",
    "CONVEYOR_SEGMENT_CM",
    "DEFAULT_SAVE_VERSION",
    "ITEM_CLASS_PATHS",
    "LEVEL",
    "SPLINE_POINT_FIELD_TAGS",
    "TEMPLATE_MIN_SAVE_VERSION",
    "Template",
    "TemplateError",
    "TemplateLibrary",
    "apply_recipe",
    "assemble",
    "connect",
    "set_recipe",
    "set_spline",
]

LEVEL = "Persistent_Level"
"""The only level a blueprint's objects live in."""

ACTOR_PATH_PREFIX = f"{LEVEL}:PersistentLevel."

TEMPLATE_MIN_SAVE_VERSION = 58
"""Templates come from this save version up: the 1.0-and-later class family,
with the modern property tag, which is what a blueprint written today needs."""

BLUEPRINT_HEADER_VERSION = 2
DEFAULT_SAVE_VERSION = 60

CONVEYOR_SEGMENT_CM = 200.0
"""How much belt one unit of the belt's build recipe pays for.

Measured on the corpus: ``production-4`` has six Mk1 belts of 300, 200, 200,
300, 200 and 200 cm and its header charges 8 iron plates over the non-spline
buildings, which is ``sum(ceil(length / 200))``; ``logistics-9``'s twelve belts
come to 34 the same way. A fixture's whole bill is not reproducible -- it also
counts items sitting in inventories and lightweight buildables that are not
saved as objects at all -- but the belts in it are."""

_PARTS = "/Game/FactoryGame/Resource/Parts"

# Which folder each item descriptor's asset lives in. Nothing in Docs.json
# survives into ``registry.json`` with its asset path, so these are harvested
# from the fixture corpus's own header costs, where the game wrote them;
# ``tests/sfy/test_templates.py`` checks every path against every fixture.
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
"""Item descriptors, as the game spells them in a blueprint's cost list."""

SPLINE_POINT_FIELD_TAGS: tuple[Tag, ...] = tuple(
    Tag(name, "StructProperty", 0, struct_name="Vector").as_modern(TAG_NATIVE_SERIALIZE)
    for name in ("Location", "ArriveTangent", "LeaveTangent")
)
"""The three tags inside one ``SplinePointData``, as the game writes them.

``Vector`` is natively serialised, hence the flag, and it lives in
``/Script/CoreUObject``, which :data:`~flab2bp.sfy.properties.TYPE_PACKAGES`
knows. A test compares these against a fixture belt's own tags."""

CONNECTED_COMPONENT = "mConnectedComponent"
SPLINE_DATA = "mSplineData"
BUILT_WITH_RECIPE = "mBuiltWithRecipe"
CURRENT_RECIPE = "mCurrentRecipe"
ALLOWED_ITEMS = "mAllowedItemDescriptors"

_CONNECTED_COMPONENT_TAG = Tag(CONNECTED_COMPONENT, "ObjectProperty", 0).as_modern()


class TemplateError(ValueError):
    """A template could not be built, copied or assembled."""


@dataclass(frozen=True, slots=True)
class Template:
    """One actor the game wrote, with the components it owns, ready to copy."""

    header: ObjectHeader
    data: ObjectData
    components: tuple[tuple[ObjectHeader, ObjectData], ...]


@dataclass(frozen=True, slots=True)
class TemplateLibrary:
    """One template per buildable class, taken from the fixture blueprints."""

    templates: dict[str, Template]

    @property
    def classes(self) -> tuple[str, ...]:
        """Every class the library can stamp out, in insertion order."""
        return tuple(self.templates)

    @classmethod
    def from_fixtures(cls, paths: Iterable[Path]) -> TemplateLibrary:
        """Scan blueprints and keep the first actor seen of each class.

        Only files at :data:`TEMPLATE_MIN_SAVE_VERSION` or newer are scanned:
        an older blueprint's objects are in the classic tag format and describe
        a pre-1.0 class family, so they are not a template for a file the
        current game will load.
        """
        templates: dict[str, Template] = {}
        for path in paths:
            bp = read_sbp_file(path)
            if bp.header.save_version < TEMPLATE_MIN_SAVE_VERSION:
                continue
            index = {h.path: (h, d) for h, d in bp.objects}
            for h, d in bp.objects:
                if h.kind != ACTOR or h.class_name in templates:
                    continue
                refs = d.components or ()
                if any(ref.path not in index for ref in refs):
                    continue  # an actor whose components are not all in the file
                templates[h.class_name] = Template(h, d, tuple(index[r.path] for r in refs))
        return cls(templates)

    def instantiate(
        self, class_name: str, name_id: int, transform: Transform
    ) -> tuple[tuple[ObjectHeader, ObjectData], ...]:
        """A fresh copy of one template actor and its components.

        The actor is named ``<class>_<name_id>`` the way the game names one and
        its components ``<actor path>.<component name>``. Everything else is the
        template's, except the transform, which is the caller's, the references,
        which are remapped (see :func:`_map_value`), and the trailer, which is
        built fresh rather than copied so that nothing the template happened to
        be carrying -- items on a belt, a power line's wire state -- rides along.
        """
        try:
            template = self.templates[class_name]
        except KeyError:
            raise TemplateError(f"no template for {class_name}") from None
        old_path = template.header.path
        new_path = f"{ACTOR_PATH_PREFIX}{class_name}_{name_id}"
        out: list[tuple[ObjectHeader, ObjectData]] = [
            (
                replace(template.header, path=new_path, transform=transform),
                replace(
                    template.data,
                    components=tuple(
                        ObjectRef(LEVEL, f"{new_path}.{h.name}") for h, _ in template.components
                    ),
                    properties=_instance_properties(template.data.properties, old_path, new_path),
                    trailer=trailer_for_new(class_name, ACTOR),
                ),
            )
        ]
        for h, d in template.components:
            out.append(
                (
                    replace(h, path=f"{new_path}.{h.name}", parent=new_path),
                    replace(
                        d,
                        properties=_instance_properties(d.properties, old_path, new_path),
                        trailer=trailer_for_new(h.class_name, h.kind),
                    ),
                )
            )
        return tuple(out)


def connect(
    a: ObjectData, a_path: str, b: ObjectData, b_path: str
) -> tuple[ObjectData, ObjectData]:
    """Wire two connection components to each other.

    Each side's ``mConnectedComponent`` is set to the other's instance path.
    ``ObjectData`` carries no path of its own, which is why both are passed in.
    """
    return (
        _set_property(
            a, CONNECTED_COMPONENT, Object(ObjectRef(LEVEL, b_path)), _CONNECTED_COMPONENT_TAG
        ),
        _set_property(
            b, CONNECTED_COMPONENT, Object(ObjectRef(LEVEL, a_path)), _CONNECTED_COMPONENT_TAG
        ),
    )


def set_spline(belt: ObjectData, points: Sequence[tuple[Vector, Vector, Vector]]) -> ObjectData:
    """Replace a conveyor's ``mSplineData`` with ``(location, arrive, leave)`` triples.

    The locations are in the belt actor's own frame, so a belt placed at its
    first point starts its spline at the origin. The array keeps the template's
    own tag; the points are built with :data:`SPLINE_POINT_FIELD_TAGS`.
    """
    current = find(belt.properties, SPLINE_DATA)
    if not isinstance(current, Array):
        raise TemplateError(f"{SPLINE_DATA} is not an array on this object")
    items = tuple(
        Struct(
            "SplinePointData",
            tuple(
                Property(tag, value)
                for tag, value in zip(SPLINE_POINT_FIELD_TAGS, triple, strict=True)
            ),
        )
        for triple in points
    )
    return _set_property(belt, SPLINE_DATA, replace(current, items=items))


def set_recipe(machine: ObjectData, recipe_class_path: str) -> ObjectData:
    """Point a manufacturer's ``mCurrentRecipe`` at a recipe asset.

    This is half of changing a machine's recipe: the inventory filters live on
    the machine's inventory *components*, so use :func:`apply_recipe` over a
    whole instantiated actor unless you are certain the filters already agree.
    """
    return _set_property(machine, CURRENT_RECIPE, Object(ObjectRef("", recipe_class_path)))


def apply_recipe(
    objects: Sequence[tuple[ObjectHeader, ObjectData]],
    recipe_class_path: str,
    registry: Registry,
) -> tuple[tuple[ObjectHeader, ObjectData], ...]:
    """Run ``recipe_class_path`` on an instantiated manufacturer.

    A machine's recipe is stated in three places, not one: ``mCurrentRecipe`` on
    the actor, and the ``mAllowedItemDescriptors`` filter on each of its input
    and output inventory components. Copying a template and setting only the
    first leaves a constructor that says it makes iron plates while its
    inventories still only accept what the template made.

    The corpus says what the filters hold, over 160 manufacturers at save
    version 58 and up: **the input filter is the recipe's ingredients, in recipe
    order**, and **the output filter is its products**, in recipe order, padded
    out with the ``FGItemDescriptor`` wildcard the game leaves in the machine's
    spare output slots. How many slots there are belongs to the machine, not to
    the recipe -- an oil refinery keeps a slot a solid recipe does not fill --
    so the slots and their parallel ``mArbitrarySlotSizes`` are left exactly as
    the template has them and only the entries the recipe names are rewritten.
    """
    if not objects:
        raise TemplateError("apply_recipe needs the actor and its components")
    actor_header, actor_data = objects[0]
    recipe = registry.recipes.get(recipe_class_path.rsplit(".", 1)[-1])
    if recipe is None:
        raise TemplateError(f"the registry has no recipe {recipe_class_path}")
    if recipe.producers and actor_header.class_name not in recipe.producers:
        raise TemplateError(
            f"{actor_header.class_name} does not produce {recipe.class_name}; "
            f"the registry says {', '.join(recipe.producers)} do"
        )
    actor_data = set_recipe(actor_data, recipe_class_path)
    wanted = {
        _inventory_path(actor_header, actor_data, "mInputInventory"): [
            item for item, _ in recipe.ingredients
        ],
        _inventory_path(actor_header, actor_data, "mOutputInventory"): [
            item for item, _ in recipe.products
        ],
    }
    out = [(actor_header, actor_data)]
    for header, data in objects[1:]:
        items = wanted.get(header.path)
        out.append((header, data if items is None else _set_filter(header, data, items)))
    return tuple(out)


def _inventory_path(header: ObjectHeader, data: ObjectData, name: str) -> str:
    value = find(data.properties, name)
    if not isinstance(value, Object) or value.ref.is_null:
        raise TemplateError(f"{header.path} has no {name} to filter")
    return value.ref.path


def _set_filter(header: ObjectHeader, data: ObjectData, items: Sequence[str]) -> ObjectData:
    """Rewrite the leading ``mAllowedItemDescriptors`` entries, keeping the slots."""
    current = find(data.properties, ALLOWED_ITEMS)
    if not isinstance(current, Array):
        raise TemplateError(f"{header.path} has no {ALLOWED_ITEMS} array")
    if len(items) > len(current.items):
        raise TemplateError(
            f"{header.path} has {len(current.items)} inventory slots, the recipe needs {len(items)}"
        )
    filled: tuple[Value, ...] = tuple(Object(_item_ref(item)) for item in items)
    return _set_property(
        data, ALLOWED_ITEMS, replace(current, items=filled + current.items[len(items) :])
    )


def assemble(
    objects: Sequence[tuple[ObjectHeader, ObjectData]],
    dimensions: tuple[int, int, int],
    registry: Registry,
    *,
    build_version: int,
    version_data: SaveObjectVersionData | None,
    save_version: int = DEFAULT_SAVE_VERSION,
) -> Blueprint:
    """Put objects into a blueprint, with the header the game expects.

    ``cost`` is what the contents would take out of the player's inventory: each
    actor's ``mBuiltWithRecipe`` costed through the registry, and a conveyor
    costed once per :data:`CONVEYOR_SEGMENT_CM` of spline. ``recipes`` is the
    distinct build recipes in first-seen order. The objects come out actors
    first and components after, in the order they were handed in, which is how
    47 of the 49 corpus fixtures are written.
    """
    actors = [(h, d) for h, d in objects if h.kind == ACTOR]
    components = [(h, d) for h, d in objects if h.kind != ACTOR]
    cost: dict[str, int] = {}
    recipes: dict[str, ObjectRef] = {}
    for h, d in actors:
        ref = _recipe_ref(h, d)
        recipes.setdefault(ref.name, ref)
        recipe = registry.recipes.get(ref.name)
        if recipe is None:
            raise TemplateError(f"{h.path}: the registry has no recipe {ref.name}")
        count = _segments(d)
        for item, amount in recipe.ingredients:
            cost[item] = cost.get(item, 0) + amount * count
    header = BlueprintHeader(
        header_version=BLUEPRINT_HEADER_VERSION,
        save_version=save_version,
        build_version=build_version,
        dimensions=dimensions,
        cost=tuple(ItemAmount(_item_ref(name), amount) for name, amount in cost.items()),
        recipes=tuple(recipes.values()),
        version_data=version_data,
    )
    return Blueprint(header, tuple(actors) + tuple(components))


def _item_ref(item: str) -> ObjectRef:
    try:
        return ObjectRef("", ITEM_CLASS_PATHS[item])
    except KeyError:
        raise TemplateError(f"no asset path known for the item descriptor {item}") from None


def _recipe_ref(h: ObjectHeader, d: ObjectData) -> ObjectRef:
    value = find(d.properties, BUILT_WITH_RECIPE)
    if not isinstance(value, Object) or value.ref.is_null:
        raise TemplateError(f"{h.path}: no {BUILT_WITH_RECIPE} to cost the actor with")
    return value.ref


def _segments(d: ObjectData) -> int:
    """How many units of its build recipe this actor costs.

    One, unless it is a spline buildable: a conveyor is charged by length, and
    a part-used segment is charged whole.
    """
    value = find(d.properties, SPLINE_DATA)
    if not isinstance(value, Array) or len(value.items) < 2:
        return 1
    length = 0.0
    previous: tuple[float, float, float] | None = None
    for item in value.items:
        location = find(item.fields, "Location") if isinstance(item, Struct) else None
        if not isinstance(location, Vector):
            return 1
        current = (location.x, location.y, location.z)
        if previous is not None:
            length += math.dist(previous, current)
        previous = current
    return max(1, math.ceil(length / CONVEYOR_SEGMENT_CM - 1e-9))


def _set_property(d: ObjectData, name: str, value: Value, tag: Tag | None = None) -> ObjectData:
    """``d`` with the property called ``name`` carrying ``value``.

    An existing property keeps its own tag -- a tag the game wrote, with the
    type tree, flags and package it wrote -- and only its value changes. A
    property that is not there yet is appended with ``tag``, which the caller
    must supply for exactly that case.
    """
    for i, p in enumerate(d.properties):
        if p.tag.name == name:
            return replace(
                d, properties=d.properties[:i] + (Property(p.tag, value),) + d.properties[i + 1 :]
            )
    if tag is None:
        raise TemplateError(f"this object has no {name} and no tag was given to author one")
    return replace(d, properties=(*d.properties, Property(tag, value)))


def _instance_properties(props: PropertyList, old_path: str, new_path: str) -> PropertyList:
    """The template's properties, with the source blueprint edited out of them."""
    out = []
    for p in props:
        value = _map_value(p.value, old_path, new_path)
        if p.tag.name == SPLINE_DATA and isinstance(value, Array):
            value = replace(value, items=())
        out.append(Property(p.tag, value))
    return tuple(out)


def _map_value(value: Value, old_path: str, new_path: str) -> Value:
    """Rewrite the references in one value for the new actor.

    A reference into the copied actor (its own inventories, power info and
    connection components) is moved to the new actor's path. A reference to
    anything else in the source blueprint's level -- the machine at the far end
    of a belt, the power lines on a pole -- names an object the new blueprint
    does not have, so it is nulled, and dropped outright when it is an element
    of an array, where a null would claim a link that is not there. References
    with no level are asset paths (recipes, item descriptors) and are untouched.
    """
    if isinstance(value, Object):
        ref, dangling = _map_ref(value.ref, old_path, new_path)
        return Object(ObjectRef.NULL) if dangling else Object(ref)
    if isinstance(value, Array):
        items: list[Value] = []
        for item in value.items:
            if isinstance(item, Object):
                ref, dangling = _map_ref(item.ref, old_path, new_path)
                if dangling:
                    continue
                items.append(Object(ref))
            else:
                items.append(_map_value(item, old_path, new_path))
        return replace(value, items=tuple(items))
    if isinstance(value, Struct):
        return replace(value, fields=_instance_properties(value.fields, old_path, new_path))
    return value


def _map_ref(ref: ObjectRef, old_path: str, new_path: str) -> tuple[ObjectRef, bool]:
    """``(reference, is dangling)`` for one reference under the copied actor."""
    if ref.is_null or ref.level == "":
        return ref, False
    if ref.path == old_path:
        return replace(ref, path=new_path), False
    if ref.path.startswith(old_path + "."):
        return replace(ref, path=new_path + ref.path[len(old_path) :]), False
    return ObjectRef.NULL, True
