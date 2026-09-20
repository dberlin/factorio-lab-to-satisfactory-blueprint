"""Native boundary labels describe local module rates without occupying stack seams."""

from dataclasses import replace
from fractions import Fraction
from itertools import count
from math import inf
from typing import Literal

import pytest

from flab2bp.sfy.geometry import quat_rotate
from flab2bp.sfy.layout.model import (
    BeamObj,
    FoundationObj,
    LiftObj,
    Pose,
    SfyPlacement,
    StackLane,
    belt_ends,
)
from flab2bp.sfy.layout.validate import TOUCH_CM, Context, _penetration
from flab2bp.sfy.sections.model import SectionError, placement_bounds, transform_placement
from flab2bp.sfy.sections.signs import add_connection_signs
from flab2bp.sfy.spec import FOUNDATION_CLASS, designer
from tests.sfy.conftest import sfy_registry


def boundary(
    *,
    item: str = "iron-ore",
    kind: Literal["belt", "pipe"] = "belt",
    consumed: Fraction = Fraction(1, 2),
    produced: Fraction = Fraction(),
) -> SfyPlacement:
    registry = sfy_registry()
    lift_class = "Build_ConveyorLiftMk1_C"
    entry, exit_ = belt_ends(registry, lift_class)
    return SfyPlacement(
        designer=designer("mk1", registry),
        foundations=tuple(
            FoundationObj(i + 10, FOUNDATION_CLASS, Pose(x, y, z, 0))
            for i, (x, y, z) in enumerate(
                (x, y, z) for x in (-400, 400) for y in (-400, 400) for z in (50, 1050)
            )
        ),
        beams=tuple(
            BeamObj(30 + i, "Build_Beam_Painted_C", Pose(750, -750, z, 90), 1500)
            for i, z in enumerate((50, 1050))
        ),
        lifts=(
            LiftObj(
                1,
                lift_class,
                Pose(0, 0, 50 if consumed else 1050, 0),
                1000 if consumed else -1000,
            ),
        ),
        stack_lanes=(
            StackLane(
                item,
                kind,
                (1, entry if consumed else exit_),
                (1, exit_ if consumed else entry),
                consumed,
                produced,
                Fraction(20),
            ),
        ),
        stack_height_cm=1200,
        stack_connection_gap_cm=150,
    )


def test_local_rates_are_not_trunk_capacity_and_unused_ends_are_honest() -> None:
    placement = add_connection_signs(boundary(), sfy_registry(), ids=count(100), deadline=inf)
    assert [sign.text for sign in placement.signs] == [
        "INPUT",
        "Iron Ore 30/min",
        "OUTPUT",
        "Iron Ore PASS THROUGH",
    ]
    assert {sign.label for sign in placement.signs} == {"A"}
    assert placement.signs[0].pose.z < placement.signs[2].pose.z
    assert placement.signs[0].pose.z - placement.signs[1].pose.z == 50
    assert placement.signs[2].pose.z - placement.signs[3].pose.z == 50


def test_output_labels_put_local_extraction_below_the_incoming_pass_through() -> None:
    placement = add_connection_signs(
        boundary(item="iron-rod", consumed=Fraction(), produced=Fraction(1, 2)),
        sfy_registry(),
        ids=count(100),
        deadline=inf,
    )
    headers = {sign.text: sign for sign in placement.signs if sign.text in {"INPUT", "OUTPUT"}}
    details = {sign.text: sign for sign in placement.signs if sign.text not in {"INPUT", "OUTPUT"}}
    assert headers["OUTPUT"].pose.z < headers["INPUT"].pose.z
    assert details["Iron Rod 30/min"].pose.z == headers["OUTPUT"].pose.z - 50
    assert details["Iron Rod PASS THROUGH"].pose.z == headers["INPUT"].pose.z - 50


@pytest.mark.parametrize(
    ("item", "consumed", "produced", "expected"),
    [
        ("crude-oil", Fraction(1, 2), Fraction(), "Crude Oil 30 m³/min"),
        ("heavy-oil-residue", Fraction(), Fraction(1, 6), "Heavy Oil Residue 10 m³/min"),
        ("water", Fraction(1, 48), Fraction(), "Water 1.25 m³/min"),
        ("fuel", Fraction(), Fraction(1, 7), "Fuel 60/7 m³/min"),
    ],
)
def test_fluid_and_byproduct_labels_keep_exact_module_rates(
    item: str, consumed: Fraction, produced: Fraction, expected: str
) -> None:
    placement = add_connection_signs(
        boundary(item=item, kind="pipe", consumed=consumed, produced=produced),
        sfy_registry(),
        ids=count(100),
        deadline=inf,
    )
    assert expected in {sign.text for sign in placement.signs}


def test_pairs_and_native_supports_fit_without_crossing_transport_or_slabs() -> None:
    registry = sfy_registry()
    original = boundary()
    placement = add_connection_signs(original, registry, ids=count(100), deadline=inf)
    context = Context(placement, None, registry, {})
    sign_ids = {sign.id for sign in placement.signs}
    support_ids = {beam.id for beam in placement.beams} - {beam.id for beam in original.beams}
    added = [box for box in context.boxes if box.owner in sign_ids | support_ids]
    obstacles = [box for box in context.boxes if box.owner not in sign_ids | support_ids]
    obstacles.extend(box for boxes in context.conveyor_boxes.values() for box in boxes)
    for i, box in enumerate(added):
        for other in (*obstacles, *added[i + 1 :]):
            assert _penetration(box, other) <= TOUCH_CM
    low, high = placement_bounds(placement, registry)
    assert all(
        -placement.designer.half_cm <= low[i] <= high[i] <= placement.designer.half_cm
        for i in (0, 1)
    )
    assert 0 <= low[2] <= high[2] <= placement.designer.height_cm
    for sign in placement.signs:
        front = quat_rotate(sign.pose.transform().rotation, (0, 1, 0))
        assert front == pytest.approx((1, 0, 0), abs=0.001)
        assert sign.pose.x > 750


def test_front_sign_words_are_not_hidden_behind_lifts() -> None:
    registry = sfy_registry()
    original = boundary()
    original = replace(
        original,
        lifts=(*original.lifts, replace(original.lifts[0], id=2, pose=Pose(1000, 0, 50, 0))),
    )
    placement = add_connection_signs(original, registry, ids=count(100), deadline=inf)
    context = Context(placement, None, registry, {})
    for sign in placement.signs:
        assert sign.pose.x > 750
        # Looking straight inward from the front, no lift can cover the text face.
        for boxes in context.conveyor_boxes.values():
            for box in boxes:
                overlaps_text = (
                    abs(box.centre[1] - sign.pose.y) < box.reach[1] + 150
                    and abs(box.centre[2] - sign.pose.z) < box.reach[2] + 25
                )
                assert not overlaps_text or box.centre[0] + box.reach[0] <= sign.pose.x + 9


def test_signs_transform_with_complete_section_geometry() -> None:
    registry = sfy_registry()
    placement = add_connection_signs(boundary(), registry, ids=count(100), deadline=inf)
    moved = transform_placement(placement, (100, 200, 300), 90)
    for original, sign in zip(placement.signs, moved.signs, strict=True):
        assert sign.text == original.text
        assert sign.label == original.label
        assert sign.pose.location == pytest.approx(
            (100 - original.pose.y, 200 + original.pose.x, 300 + original.pose.z), abs=0.001
        )


def test_missing_mount_space_is_refused_not_silently_unlabelled() -> None:
    with pytest.raises(SectionError, match="sign"):
        add_connection_signs(
            replace(boundary(), beams=()), sfy_registry(), ids=count(100), deadline=inf
        )


def test_expired_shared_deadline_stops_label_placement() -> None:
    with pytest.raises(SectionError) as exc:
        add_connection_signs(boundary(), sfy_registry(), ids=count(100), deadline=0)
    assert exc.value.cause == "deadline"
