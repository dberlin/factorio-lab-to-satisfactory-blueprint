"""The seams two Satisfactory strategies share: protocol, causes, names, floor, measure.

Nothing here lays a build out differently.  These are the five things a SECOND
strategy needs to exist beside ``ManifoldRows`` -- a shape to satisfy, one table
of causes neither may invent from, the names the race is run under, the floor
both stand their machines on, and the number the race is decided by -- and every
test is about the seam rather than about either strategy's geometry.

The one build in this file is the manifold's own ``iron-plate-60`` in an mk1,
because a measure has to be measured against something real: a placement the
strategy actually returned, not a hand-written one whose numbers a test chose.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import replace
from pathlib import Path

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.sfy import strategy_names
from flab2bp.sfy.geometry import Vector, box_bounds
from flab2bp.sfy.layout.floor import foundations
from flab2bp.sfy.layout.measure import Measure, measure, race_key
from flab2bp.sfy.layout.model import AttachmentObj, MachineObj, SfyPlacement
from flab2bp.sfy.layout.protocol import SfyLayoutStrategy
from flab2bp.sfy.layout.refusals import REFUSALS, refuse
from flab2bp.sfy.layout.splines import spline_length
from flab2bp.sfy.layout.strategy import ManifoldRows
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, designer
from flab2bp.sfy.strategy_names import SFY_PRODUCTION_STRATEGIES, SFY_STRATEGY_CHOICES
from tests.sfy.conftest import flow_spec, sfy_registry

#: The mark every build here is laid out in.  ``iron-plate-60`` is the corpus's
#: smallest entry and the one the spec measures at 2763 cm of band, so it is the
#: build that fits the smallest designer there is.
MARK = "mk1"

#: mypy's half of the protocol test, and the reason it is a module-level
#: annotation rather than a local: a strategy that does not satisfy the protocol
#: fails ``uv run mypy`` on THIS line, which is the check a runtime
#: ``isinstance`` cannot make -- ``runtime_checkable`` looks at attribute names
#: and never at a signature.
_MANIFOLD: SfyLayoutStrategy = ManifoldRows()


# --- the protocol -----------------------------------------------------------


def test_manifold_rows_satisfies_the_strategy_protocol() -> None:
    assert isinstance(_MANIFOLD, SfyLayoutStrategy)
    assert _MANIFOLD.name == "manifold-rows"
    assert _MANIFOLD.name in SFY_PRODUCTION_STRATEGIES


# --- the vocabulary of refusal ----------------------------------------------


def test_every_refusal_is_named_once_and_only_refuse_builds_one() -> None:
    """One table, no cause in it twice, and one constructor for the whole target.

    The last assertion is the one that keeps the table honest as the second
    strategy is written: a module that builds its own ``NoValidLayout`` can name
    a cause nobody declared, and the caller would never know.
    """
    assert len(set(REFUSALS)) == len(REFUSALS)
    spec = flow_spec("iron-plate-60")

    built = refuse(spec, "fluids are M5", "this build moves water")
    assert isinstance(built, NoValidLayout)
    assert built.reason == "fluids are M5"
    assert built.attempt_reasons == ("this build moves water",)
    assert refuse(spec, "fluids are M5").attempt_reasons == ()

    with pytest.raises(ValueError, match="not one of the named refusals"):
        refuse(spec, "the solver did not feel like it")

    package = Path(strategy_names.__file__).resolve().parent
    builders = sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if "NoValidLayout(" in path.read_text(encoding="utf-8")
    )
    assert builders == ["layout/refusals.py"]


def test_the_grid_strategys_causes_are_named_before_the_grid_strategy_exists() -> None:
    """Task 10 may not add a cause; the corpus has to be able to pin one now."""
    for cause in (
        "a port is off the hologram lattice",
        "the packer found no arrangement",
        "a belt could not be routed",
        "a belt could not be laid",
        "a tap has no room for a splitter",
        "routing exceeded the budget",
        "packing exceeded the budget",
    ):
        assert cause in REFUSALS
    for shared in (
        "no room for a power pole",
        "a machine has no power connection",
        "wire exceeds the maximum length",
        "run exceeds the belt ceiling",
        "fluids are M5",
    ):
        assert REFUSALS.count(shared) == 1


# --- the names --------------------------------------------------------------


def test_the_strategy_names_leaf_imports_nothing_from_flab2bp() -> None:
    """A CLI choice list must not drag a registry, a solver or a game dataset in."""
    assert SFY_STRATEGY_CHOICES == ("best", "manifold-rows", "grid-routed")
    assert SFY_PRODUCTION_STRATEGIES == ("manifold-rows", "grid-routed")
    assert set(SFY_PRODUCTION_STRATEGIES) < set(SFY_STRATEGY_CHOICES)
    assert "best" not in SFY_PRODUCTION_STRATEGIES

    tree = ast.parse(Path(strategy_names.__file__).read_text(encoding="utf-8"))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert [name for name in sorted(imported) if name.startswith("flab2bp")] == []


# --- the floor --------------------------------------------------------------


def test_floor_covers_the_designer() -> None:
    """Every cell of the designer, one slab, standing on its own box's half."""
    registry = sfy_registry()
    frame = designer(MARK, registry)
    side = frame.foundation_cm
    half = frame.half_cm
    slabs = foundations(frame, registry, itertools.count(1))

    assert {slab.class_name for slab in slabs} == {FOUNDATION_CLASS}
    assert len(slabs) == (2 * half / side) ** 2
    assert {slab.pose.z for slab in slabs} == {side / 16.0}  # 50 cm: the box's own half
    assert min(slab.pose.x for slab in slabs) == -half + side / 2.0
    assert max(slab.pose.x for slab in slabs) == half - side / 2.0
    assert min(slab.pose.y for slab in slabs) == -half + side / 2.0
    assert max(slab.pose.y for slab in slabs) == half - side / 2.0
    assert [slab.id for slab in slabs] == list(range(1, len(slabs) + 1))


def test_the_manifold_stands_on_the_shared_floor() -> None:
    """The strategy's own floor is this function's, rather than a second one."""
    registry = sfy_registry()
    frame = designer(MARK, registry)
    placement = ManifoldRows().lay_out(flow_spec("iron-plate-60"), frame)
    shared = foundations(frame, registry, itertools.count(1))
    assert {(slab.class_name, slab.pose) for slab in placement.foundations} == {
        (slab.class_name, slab.pose) for slab in shared
    }


# --- the measure ------------------------------------------------------------


def _corners(placement: SfyPlacement, registry: Registry) -> list[Vector]:
    """The extent this test expects, worked out again rather than borrowed.

    A machine is its HARD clearance boxes where the registry gives it any; a
    conveyor attachment's box is soft -- shared ground, which is why a belt may
    run through it -- so it is measured at its own origin.  A belt is its spline
    points.
    """
    corners: list[Vector] = []
    standing: list[AttachmentObj | MachineObj] = [*placement.machines, *placement.attachments]
    for obj in standing:
        boxes = [box for box in registry.buildables[obj.class_name].clearance if not box.soft]
        if not boxes:
            corners.append(obj.pose.location)
        for box in boxes:
            low, high = box_bounds(box, obj.pose.transform())
            corners += [low, high]
    for run in placement.belts:
        corners += [point[0] for point in run.points]
    return corners


def test_measure_of_the_iron_plate_manifold_is_its_bounding_volume_and_belt_length() -> None:
    registry = sfy_registry()
    placement = ManifoldRows().lay_out(flow_spec("iron-plate-60"), designer(MARK, registry))
    got = measure(placement, registry)

    corners = _corners(placement, registry)
    volume = 1.0
    for axis in range(3):
        volume *= max(c[axis] for c in corners) - min(c[axis] for c in corners)

    assert got.blueprints == 1
    assert got.volume_cm3 == pytest.approx(volume)
    assert got.belt_cm == pytest.approx(sum(spline_length(run.points) for run in placement.belts))
    assert got.lifts == 0
    assert got.attachments == len(placement.attachments) == 8


def test_the_floor_and_the_pole_line_are_not_what_a_build_occupies() -> None:
    """A full floor is the same slab count in every build, so it decides nothing.

    Measuring it would make every build in a mark identical on volume and hand
    the race to the tie-break.  The poles stand outside the rows for the same
    reason the floor is everywhere: neither is what the build is.
    """
    registry = sfy_registry()
    placement = ManifoldRows().lay_out(flow_spec("iron-plate-60"), designer(MARK, registry))
    assert placement.foundations and placement.poles
    stripped = replace(placement, foundations=(), poles=(), wires=())
    assert measure(stripped, registry) == measure(placement, registry)


def test_a_placement_with_nothing_in_it_is_refused_rather_than_measured_as_zero() -> None:
    """A zero is a number a race would happily pick; there is no build here."""
    registry = sfy_registry()
    empty = SfyPlacement(designer=designer(MARK, registry))
    with pytest.raises(ValueError, match="no machine, attachment, lift or belt"):
        measure(empty, registry)


# --- the race key -----------------------------------------------------------


def _measure(
    *,
    blueprints: int = 1,
    volume_cm3: float = 100.0,
    belt_cm: float = 10.0,
    lifts: int = 0,
    attachments: int = 0,
) -> Measure:
    return Measure(
        blueprints=blueprints,
        volume_cm3=volume_cm3,
        belt_cm=belt_cm,
        lifts=lifts,
        attachments=attachments,
    )


def test_race_key_orders_by_blueprints_then_volume_then_belt_then_strategy() -> None:
    base = _measure()
    assert race_key(base, 1) == (1, 100.0, 10.0, 1)

    assert race_key(base, 0) < race_key(_measure(blueprints=2), 0)
    assert race_key(base, 0) < race_key(_measure(volume_cm3=100.5), 0)
    assert race_key(base, 0) < race_key(_measure(belt_cm=10.5), 0)
    assert race_key(base, 0) < race_key(base, 1)

    # Each field beats every field under it: a second blueprint loses however
    # small the build inside it is, and a shorter belt never buys a bigger box.
    assert race_key(base, 0) < race_key(_measure(blueprints=2, volume_cm3=1.0, belt_cm=1.0), 0)
    assert race_key(base, 0) < race_key(_measure(volume_cm3=100.5, belt_cm=1.0), 0)
    assert race_key(base, 0) < race_key(_measure(belt_cm=10.5), 0)

    # The tie-break is the production race order, so a dead heat goes to the
    # strategy named first rather than to whichever finished first.
    assert SFY_PRODUCTION_STRATEGIES.index("manifold-rows") == 0
    winner = min(
        (race_key(base, index), name) for index, name in enumerate(SFY_PRODUCTION_STRATEGIES)
    )
    assert winner[1] == "manifold-rows"


def test_lifts_and_attachments_are_reported_but_do_not_decide_the_race() -> None:
    """They are what a reader wants in a report, not what the spec races on."""
    assert race_key(_measure(lifts=9, attachments=9), 0) == race_key(_measure(), 0)
