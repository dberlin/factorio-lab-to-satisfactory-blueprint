"""The Satisfactory build spec: designers, machine groups, and their invariants."""

from __future__ import annotations

from fractions import Fraction

import pytest
from pydantic import ValidationError

from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec, SfyMachineGroup, designer


def _group(**overrides: object) -> SfyMachineGroup:
    base: dict[str, object] = {
        "recipe_id": "iron-plate",
        "recipe_class": "Recipe_IronPlate_C",
        "machine_item_id": "constructor-id",
        "machine_class": "Build_ConstructorMk1_C",
        "count": 3,
        "clock": Fraction(1),
        "last_clock": Fraction(1),
        "somersloops": 0,
        "power_shards_per_machine": 0,
        "inputs_per_machine": {"iron-ingot": Fraction(1, 2)},
        "outputs_per_machine": {"iron-plate": Fraction(1, 3)},
        "power_mw_per_machine": Fraction(4),
    }
    base.update(overrides)
    return SfyMachineGroup(**base)  # type: ignore[arg-type]


def test_each_designer_mark_takes_its_dimensions_from_the_games_own_buildable() -> None:
    registry = load_registry()
    assert designer("mk1", registry).dims == (4, 4, 4)
    assert designer("mk2", registry).dims == (5, 5, 5)
    assert designer("mk3", registry).dims == (6, 6, 6)


def test_a_designer_states_its_half_width_and_height_in_centimetres() -> None:
    mk2 = Designer(mark="mk2", dims=(5, 5, 5))
    assert mk2.half_cm == 2000.0
    assert mk2.height_cm == 4000.0


def test_an_unknown_designer_mark_is_refused_rather_than_guessed() -> None:
    registry = load_registry()
    with pytest.raises(ValueError, match="mk4"):
        designer("mk4", registry)


def test_a_group_reports_the_row_rate_of_its_underclocked_last_machine() -> None:
    group = _group(count=3, clock=Fraction(1), last_clock=Fraction(1, 2))
    # Two machines at clock 1 plus one at half speed.
    assert group.row_outputs["iron-plate"] == Fraction(2, 3) + Fraction(1, 6)
    assert group.row_inputs["iron-ingot"] == Fraction(1) + Fraction(1, 4)


def test_a_group_at_a_uniform_clock_is_simply_count_times_the_machine_rate() -> None:
    group = _group(count=3, clock=Fraction(1), last_clock=Fraction(1))
    assert group.row_outputs["iron-plate"] == Fraction(1)
    assert group.row_inputs["iron-ingot"] == Fraction(3, 2)


def test_a_last_machine_faster_than_its_group_is_refused() -> None:
    with pytest.raises(ValidationError, match="last_clock"):
        _group(clock=Fraction(1), last_clock=Fraction(3, 2))


def test_a_clock_above_two_and_a_half_is_refused_because_the_game_caps_it_there() -> None:
    with pytest.raises(ValidationError):
        _group(clock=Fraction(3))


def test_a_spec_whose_groups_consume_something_nobody_supplies_is_refused() -> None:
    with pytest.raises(ValidationError, match="iron-ingot"):
        SfyBuildSpec(
            groups=(_group(),),
            external_inputs={},
            outputs={"iron-plate": Fraction(1)},
            belt_item_id="conveyor-belt-mk1",
            belt_items_per_second=Fraction(1),
        )


def test_belt_upgrades_must_be_strictly_faster_than_the_floor_and_ordered() -> None:
    from flab2bp.spec import BeltTier

    with pytest.raises(ValidationError, match="conveyor-belt-mk1"):
        SfyBuildSpec(
            groups=(_group(),),
            external_inputs={"iron-ingot": Fraction(3, 2)},
            outputs={"iron-plate": Fraction(1)},
            belt_item_id="conveyor-belt-mk2",
            belt_items_per_second=Fraction(2),
            belt_upgrades=(BeltTier(item_id="conveyor-belt-mk1", items_per_second=Fraction(1)),),
        )


def test_a_spec_totals_its_machines_belt_tiers_and_power() -> None:
    from flab2bp.spec import BeltTier

    spec = SfyBuildSpec(
        groups=(_group(count=3), _group(recipe_id="iron-rod", count=2)),
        external_inputs={"iron-ingot": Fraction(5, 2)},
        outputs={"iron-plate": Fraction(1)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        belt_upgrades=(BeltTier(item_id="conveyor-belt-mk2", items_per_second=Fraction(2)),),
    )
    assert spec.machine_count == 5
    assert [t.item_id for t in spec.belt_tiers] == ["conveyor-belt-mk1", "conveyor-belt-mk2"]
    assert spec.power_mw == Fraction(20)
