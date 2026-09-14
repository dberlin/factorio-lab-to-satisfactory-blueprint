"""Build checkpoint 1: the first Satisfactory blueprint this project authors.

The blueprint is a 2x2 slab of 8x1 concrete foundations with a Constructor in
the middle running the Iron Plate recipe, a 4 m Conveyor Belt Mk.1 leaving its
output port, and a Power Pole Mk.1 beside it. Every object is a copy of an
object the game itself wrote into one of the fixture blueprints, so nothing in
the file is invented; what is ours is where things stand, what they are called,
what they are wired to and which recipe runs.

The script writes ``out/sfy/checkpoint1.sbp`` and ``out/sfy/checkpoint1.sbpcfg``
(neither is committed), reads the .sbp back, and checks that it decodes to
exactly what was assembled and that the belt starts on the Constructor's output
port.

To open it in the game, on the Windows machine:

  1. Copy both files into
     %LOCALAPPDATA%\\FactoryGame\\Saved\\SaveGames\\blueprints\\<session name>\\
     -- the session name is the save you are playing, and the folder already
     holds the blueprints that save has.
  2. In game, walk into a Blueprint Designer Mk.1 and open it.
  3. Load "checkpoint1" from the blueprint list.

Then report back: (a) does it load, (b) does anything stick out of the designer,
(c) does the belt show as connected to the Constructor's output, (d) the exact
text of any error. The game's build version is on the main menu; the outcome
goes in docs/superpowers/evidence/<date>-sfy-checkpoint1/RESULT.md.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import (
    Blueprint,
    read_sbp_file,
    read_sbpcfg,
    write_sbp_file,
    write_sbpcfg,
)
from flab2bp.sfy.geometry import distance, port_forward, quat_rotate, world_port
from flab2bp.sfy.header import BlueprintHeader, BlueprintRecord, read_header
from flab2bp.sfy.objects import ACTOR, ObjectData, ObjectHeader, Transform
from flab2bp.sfy.properties import Array, Object, Vector
from flab2bp.sfy.query import object_index, spline_points
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.templates import (
    TemplateLibrary,
    apply_recipe,
    assemble,
    connect,
    set_spline,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "sfy"
OUT_DIR = REPO / "out" / "sfy"
NAME = "checkpoint1"
DESCRIPTION = "flab2bp checkpoint 1"

FOUNDATION = "Build_Foundation_8x1_01_C"
CONSTRUCTOR = "Build_ConstructorMk1_C"
BELT = "Build_ConveyorBeltMk1_C"
POLE = "Build_PowerPoleMk1_C"

# The recipe the Constructor runs. A machine names its recipe by asset path, and
# ``Registry.recipe_paths`` has that path from the game's own Docs.json.
IRON_PLATE_RECIPE = "Recipe_IronPlate_C"

# A Blueprint Designer Mk.1 is 4x4x4 foundations of 8 m, with its origin at the
# centre of the floor, so everything must sit inside +-1600 cm horizontally and
# 0..3200 cm up.
DIMENSIONS = (4, 4, 4)
DESIGNER_HALF_CM = 1600.0
DESIGNER_HEIGHT_CM = 3200.0

# An 8x1 foundation is an 800 cm square, 100 cm thick, and its transform sits at
# the middle of the slab -- the fixtures put them at z = 50 with machines at
# z = 100, 50 cm higher, which is the top face.
FOUNDATION_HALF_CM = 400.0
FOUNDATION_Z_CM = 50.0
SLAB_TOP_CM = 100.0

# 300 cm clear of the Constructor's clearance box, which is 800 x 1000 cm.
POLE_X_CM = 700.0

BELT_LENGTH_CM = 400.0

# The game names actors ``<Class>_<10 digits>``. Numbering from 2e9 keeps every
# name in this file clear of every name in every fixture template.
FIRST_NAME_ID = 2_000_000_000

IDENTITY_ROTATION = (0.0, 0.0, 0.0, 1.0)
UNIT_SCALE = (1.0, 1.0, 1.0)

PORT_TOLERANCE_CM = 1.0


def _ids() -> Iterator[int]:
    n = FIRST_NAME_ID
    while True:
        yield n
        n += 1


def _at(x: float, y: float, z: float) -> Transform:
    """An unrotated transform at one point, which is how the slab is laid out."""
    return Transform(IDENTITY_ROTATION, (x, y, z), UNIT_SCALE)


def _newest_fixture_header() -> tuple[str, BlueprintHeader]:
    """The header of the newest blueprint in the corpus.

    Its build version and engine-version block go into the file we write, so the
    game is told this blueprint came from the same family it has installed.
    """
    headers = [
        (p.name, read_header(Reader(p.read_bytes()))) for p in sorted(FIXTURES.glob("*.sbp"))
    ]
    return max(headers, key=lambda row: (row[1].save_version, row[1].build_version, row[0]))


def _port(registry: Registry, class_name: str, port_name: str) -> Port:
    ports = registry.buildables[class_name].ports
    return next(p for p in ports if p.name == port_name)


def _straight_spline(
    direction: tuple[float, float, float], length: float
) -> tuple[tuple[Vector, Vector, Vector], ...]:
    """A two-point straight conveyor spline in the belt actor's own frame.

    The shape is the one the template blueprints this script clones from write:
    outer tangents that are unit vectors along the run, inner tangents that are
    the run scaled to half its length (capped at 600 cm) -- 56 straight belts of
    exactly this length in those files carry ``(1, 200, 200, 1)``. It is the
    shape of the files we copy rather than a rule read out of the game; what
    ``AFGConveyorBeltHologram::AutoRouteSpline`` builds has not been read.
    The belt actor stands at the first point, so that point is the local origin.
    """
    x, y, z = direction
    half = min(length / 2, 600.0)
    unit = Vector(x, y, z)
    inner = Vector(x * half, y * half, z * half)
    end = Vector(x * length, y * length, z * length)
    return ((Vector(0.0, 0.0, 0.0), unit, inner), (end, inner, unit))


def build() -> Blueprint:
    """Assemble the checkpoint blueprint from fixture templates."""
    library = TemplateLibrary.from_fixtures(sorted(FIXTURES.glob("*.sbp")))
    registry = load_registry()
    _, newest = _newest_fixture_header()
    ids = _ids()

    objects: list[tuple[ObjectHeader, ObjectData]] = []
    for x in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM):
        for y in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM):
            objects += library.instantiate(FOUNDATION, next(ids), _at(x, y, FOUNDATION_Z_CM))

    constructor_at = _at(0.0, 0.0, SLAB_TOP_CM)
    constructor = apply_recipe(
        library.instantiate(CONSTRUCTOR, next(ids), constructor_at),
        registry.recipe_paths[IRON_PLATE_RECIPE],
        registry,
    )

    output = _port(registry, CONSTRUCTOR, "Output0")
    start = world_port(constructor_at, output)
    facing = port_forward(constructor_at, output)
    belt = library.instantiate(BELT, next(ids), _at(*start))
    belt = (
        (belt[0][0], set_spline(belt[0][1], _straight_spline(facing, BELT_LENGTH_CM))),
    ) + tuple(belt[1:])

    port_header, port_data = next((h, d) for h, d in constructor[1:] if h.name == "Output0")
    belt_header, belt_data = next((h, d) for h, d in belt[1:] if h.name == "ConveyorAny0")
    port_data, belt_data = connect(port_data, port_header.path, belt_data, belt_header.path)
    constructor = tuple((h, port_data if h.path == port_header.path else d) for h, d in constructor)
    belt = tuple((h, belt_data if h.path == belt_header.path else d) for h, d in belt)

    objects += constructor
    objects += belt
    objects += library.instantiate(POLE, next(ids), _at(POLE_X_CM, 0.0, SLAB_TOP_CM))
    return assemble(
        tuple(objects),
        DIMENSIONS,
        registry,
        build_version=newest.build_version,
        version_data=newest.version_data,
    )


def check(built: Blueprint, path: Path, config: Path) -> None:
    """Decode the files we wrote and hold them to what we meant to write."""
    again = read_sbp_file(path)
    if again != built:
        raise SystemExit(f"{path} does not decode to the blueprint that was assembled")
    raw = config.read_bytes()
    if write_sbpcfg(read_sbpcfg(raw)) != raw:
        raise SystemExit(f"{config} does not decode and re-encode to itself")

    registry = load_registry()
    index = object_index(again)
    constructor = next(h for h, _ in again.objects if h.class_name == CONSTRUCTOR)
    belt_header, belt_data = next((h, d) for h, d in again.objects if h.class_name == BELT)
    assert constructor.transform is not None and belt_header.transform is not None
    expected = world_port(constructor.transform, _port(registry, CONSTRUCTOR, "Output0"))
    first = spline_points(belt_data)[0][0]
    rotated = quat_rotate(belt_header.transform.rotation, (first.x, first.y, first.z))
    origin = belt_header.transform.translation
    actual = (rotated[0] + origin[0], rotated[1] + origin[1], rotated[2] + origin[2])
    residual = distance(expected, actual)
    if residual > PORT_TOLERANCE_CM:
        raise SystemExit(f"belt starts {residual:.3f} cm from the Constructor's output port")

    wired = index[f"{belt_header.path}.ConveyorAny0"][1]
    peer = next((p.value for p in wired.properties if p.tag.name == "mConnectedComponent"), None)
    if peer is None:
        raise SystemExit("the belt's ConveyorAny0 is not wired to anything")

    _check_filters(again, registry)
    for point in _occupied_points(again):
        x, y, z = point[1]
        if max(abs(x), abs(y)) > DESIGNER_HALF_CM or not 0.0 <= z <= DESIGNER_HEIGHT_CM:
            raise SystemExit(f"{point[0]} reaches ({x}, {y}, {z}), outside the designer")
    print(f"decoded {path.name}: identical to what was assembled")
    print(f"belt start to Output0: {residual:.3f} cm")
    print(f"inventory filters match {IRON_PLATE_RECIPE}")


def _check_filters(built: Blueprint, registry: Registry) -> None:
    """The constructor's inventories must accept what its recipe needs and makes."""
    index = object_index(built)
    header, data = next((h, d) for h, d in built.objects if h.class_name == CONSTRUCTOR)
    recipe = registry.recipes[IRON_PLATE_RECIPE]
    for name, wanted in (
        ("mInputInventory", [item for item, _ in recipe.ingredients]),
        ("mOutputInventory", [item for item, _ in recipe.products]),
    ):
        ref = next(p.value for p in data.properties if p.tag.name == name)
        assert isinstance(ref, Object)
        allowed = next(
            p.value
            for p in index[ref.ref.path][1].properties
            if p.tag.name == "mAllowedItemDescriptors"
        )
        assert isinstance(allowed, Array)
        got = [item.ref.name for item in allowed.items[: len(wanted)] if isinstance(item, Object)]
        if got != wanted:
            raise SystemExit(f"{header.name}.{name} allows {got}, the recipe needs {wanted}")


def _occupied_points(built: Blueprint) -> list[tuple[str, tuple[float, float, float]]]:
    """Every point the blueprint's contents actually reach, not just their origins.

    A foundation's transform is at the middle of an 800 cm slab and a belt's is
    at one end of a spline, so an origin inside the designer says nothing about
    the object being inside it.
    """
    points: list[tuple[str, tuple[float, float, float]]] = []
    for header, data in built.objects:
        if header.kind != ACTOR or header.transform is None:
            continue
        origin = header.transform.translation
        points.append((header.name, origin))
        if header.class_name == FOUNDATION:
            for dx in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM):
                for dy in (-FOUNDATION_HALF_CM, FOUNDATION_HALF_CM):
                    corner = quat_rotate(header.transform.rotation, (dx, dy, 0.0))
                    points.append(
                        (
                            f"{header.name} corner",
                            (corner[0] + origin[0], corner[1] + origin[1], origin[2]),
                        )
                    )
        for location, _, _ in spline_points(data):
            turned = quat_rotate(header.transform.rotation, (location.x, location.y, location.z))
            points.append(
                (
                    f"{header.name} spline point",
                    (turned[0] + origin[0], turned[1] + origin[1], turned[2] + origin[2]),
                )
            )
    return points


def report(built: Blueprint) -> None:
    print(
        f"{len(built.objects)} objects "
        f"({sum(1 for h, _ in built.objects if h.kind == ACTOR)} actors)"
    )
    for header, _ in built.objects:
        if header.kind == ACTOR and header.transform is not None:
            x, y, z = header.transform.translation
            print(f"  {header.name:<34} {x:>8.1f} {y:>8.1f} {z:>8.1f}")
    print("cost:")
    for entry in built.header.cost:
        print(f"  {entry.item.name:<28} {entry.amount:>4}")
    print("recipes:")
    for recipe in built.header.recipes:
        print(f"  {recipe.name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=OUT_DIR, help=f"directory to write into (default {OUT_DIR})"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    built = build()
    sbp = args.out / f"{NAME}.sbp"
    cfg = args.out / f"{NAME}.sbpcfg"
    write_sbp_file(sbp, built)
    cfg.write_bytes(write_sbpcfg(BlueprintRecord.new(DESCRIPTION)))
    print(f"wrote {sbp} ({sbp.stat().st_size:,} bytes)")
    print(f"wrote {cfg} ({cfg.stat().st_size:,} bytes)")
    report(built)
    check(built, sbp, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
