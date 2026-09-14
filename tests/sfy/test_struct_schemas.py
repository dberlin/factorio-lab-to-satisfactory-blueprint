"""``struct_schemas.json`` is the game's own field list for every struct we decode.

The codec reads a tagged struct by following the bytes: a name, a type, a size,
a value, repeat. Nothing in that loop knows what the struct is *supposed* to
contain, so a field read as the wrong type -- or a struct whose layout a game
update changes -- would round-trip byte for byte and still be wrong to anyone
reading the tree. The schemas are the independent check: they come from the
shipped ``FactoryGame.usmap`` mappings, which are the reflection data the engine
itself uses, and every field the corpus actually carries has to appear in them
with the same property type.
"""

import json
from collections import Counter
from pathlib import Path

from flab2bp.sfy import docs
from flab2bp.sfy.codec import read_sbp_file
from flab2bp.sfy.properties import Array, Struct
from tests.sfy.conftest import fixture_paths

SCHEMA_PATH = Path(docs.__file__).parent / "data" / "struct_schemas.json"
SCHEMAS = json.loads(SCHEMA_PATH.read_text())["structs"]

TAGGED_STRUCTS = frozenset(
    {
        "FactoryCustomizationColorSlot",
        "FactoryCustomizationData",
        "FeetOffset",
        "GlobalPrefabIconElementSaveData",
        "InventoryStack",
        "LocalUserNetIdBundle",
        "PersistentGlobalIconId",
        "PrefabIconElementSaveData",
        "PrefabTextElementSaveData",
        "SplinePointData",
        "SplitterSortRule",
        "TopLevelAssetPath",
        "Transform",
        "WireInstance",
    }
)
"""The corpus's structs that are a nested property list. The rest of the names in
the schema file are reached some other way: a fixed engine layout (``Vector``,
``Quat``, ``LinearColor``, ``Vector2D``) or a custom serializer
(``InventoryItem``, ``PlayerInfoHandle``), neither of which carries field tags to
check."""


def _fields(name):
    """Every field of ``name``, its own first, then each ancestor's."""
    seen, cur = {}, name
    while cur is not None:
        s = SCHEMAS[cur]
        for f in s["fields"]:
            seen.setdefault(f["name"], f)
        cur = s["super"]
    return seen


def test_every_tagged_struct_field_in_the_corpus_is_in_the_usmap_schema():
    missing = set()
    checked = Counter()

    def visit(v):
        if isinstance(v, Struct):
            fields = _fields(v.name)
            for p in v.fields:
                checked[v.name] += 1
                if p.tag.name not in fields or fields[p.tag.name]["type"] != p.tag.type:
                    missing.add((v.name, p.tag.name, p.tag.type))
                visit(p.value)
        elif isinstance(v, Array):
            for it in v.items:
                visit(it)

    for path in fixture_paths():
        for _, d in read_sbp_file(path).objects:
            for p in d.properties:
                visit(p.value)
    assert not missing, sorted(missing)
    # Not a vacuous pass: the corpus's tagged structs really were walked.
    assert set(checked) == TAGGED_STRUCTS
    assert sum(checked.values()) > 90_000


def test_every_struct_name_the_corpus_uses_has_a_schema():
    """The schema file covers the corpus, so the check above can never vacuously pass."""
    names = set()

    def visit(v):
        if isinstance(v, Struct):
            names.add(v.name)
            for p in v.fields:
                visit(p.value)
        elif isinstance(v, Array):
            for it in v.items:
                visit(it)

    for path in fixture_paths():
        for _, d in read_sbp_file(path).objects:
            for p in d.properties:
                visit(p.value)
    assert names
    assert not sorted(names - set(SCHEMAS))


def test_every_super_chain_in_the_schema_file_resolves():
    """A schema may name a super; the extractor has to have emitted that one too."""
    dangling = sorted(
        (name, s["super"])
        for name, s in SCHEMAS.items()
        if s["super"] is not None and s["super"] not in SCHEMAS
    )
    assert not dangling


def test_schema_provenance_names_the_usmap_it_came_from():
    provenance = json.loads(SCHEMA_PATH.read_text())["provenance"]
    assert len(provenance["usmap_sha256"]) == 64
    assert provenance["extracted"]
