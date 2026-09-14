"""Print every struct name the fixture corpus carries, one per line, sorted.

``tools/sfy-extract``'s ``structs`` mode takes this list and looks each name up
in the game's shipped ``FactoryGame.usmap`` mappings, writing
``src/flab2bp/sfy/data/struct_schemas.json``. Regenerate both after adding a
fixture or updating the game:

.. code-block:: bash

    uv run python scripts/sfy_struct_names.py > /tmp/struct-names.txt
    cd tools/sfy-extract && dotnet run -- "$HOME/Satisfactory" structs \\
        /tmp/struct-names.txt ../../src/flab2bp/sfy/data/struct_schemas.json

Three kinds of name reach the list: a tagged struct's own name
(:class:`~flab2bp.sfy.properties.Struct`), a struct decoded from a fixed or
custom binary layout (which is a value class, so the name comes off the tag),
and a struct array's element name. The walk is over the decoded tree rather than
over the tags alone, so a struct that only ever appears nested inside another is
found too.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flab2bp.sfy.codec import read_sbp_file  # noqa: E402
from flab2bp.sfy.properties import (  # noqa: E402
    Array,
    BinaryStruct,
    Property,
    Struct,
    Value,
)

FIXTURES = ROOT / "tests" / "fixtures" / "sfy"


def struct_names(value: Value) -> Iterator[str]:
    """Every struct name reachable from one property value."""
    if isinstance(value, Struct | BinaryStruct):
        yield value.name
    if isinstance(value, Struct):
        for field in value.fields:
            yield from struct_names(field.value)
    elif isinstance(value, Array):
        if value.inner_tag is not None and value.inner_tag.struct_name:
            yield value.inner_tag.struct_name
        for item in value.items:
            yield from struct_names(item)


def tag_names(properties: tuple[Property, ...]) -> Iterator[str]:
    """Struct names the *tags* name, which a degraded value may not carry."""
    for p in properties:
        if p.tag.struct_name:
            yield p.tag.struct_name
        if p.tag.inner_type == "StructProperty" and p.tag.struct_name:
            yield p.tag.struct_name
        if isinstance(p.value, Struct):
            yield from tag_names(p.value.fields)


def corpus_struct_names(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        for _header, data in read_sbp_file(path).objects:
            names.update(tag_names(data.properties))
            for p in data.properties:
                names.update(struct_names(p.value))
    return names


def main() -> int:
    paths = sorted(FIXTURES.glob("*.sbp"))
    if not paths:
        print(f"no fixtures under {FIXTURES}", file=sys.stderr)
        return 1
    names = corpus_struct_names(paths)
    for name in sorted(names):
        print(name)
    print(f"{len(names)} struct names from {len(paths)} fixtures", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
