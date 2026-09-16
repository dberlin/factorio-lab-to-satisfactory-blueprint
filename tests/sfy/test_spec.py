"""The Satisfactory build spec: designers, machine groups, and their invariants."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest
from pydantic import ValidationError

from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.spec import (
    FOUNDATION_CLASS,
    Designer,
    PipeTier,
    SfyBuildSpec,
    SfyMachineGroup,
    designer,
    direct_pairs,
    foundation_cm,
)


def _group(**overrides: object) -> SfyMachineGroup:
    base: dict[str, object] = {
        "recipe_id": "iron-plate",
        "recipe_class": "Recipe_IronPlate_C",
        "machine_item_id": "constructor-id",
        "machine_class": "Build_ConstructorMk1_C",
        "count": 3,
        "clock": Fraction(1),
        "last_clock": Fraction(1),
        "max_clock": Fraction(5, 2),
        "somersloops": 0,
        "power_shards_per_machine": 0,
        "last_power_shards": 0,
        "inputs_per_machine": {"iron-ingot": Fraction(1, 2)},
        "outputs_per_machine": {"iron-plate": Fraction(1, 3)},
        "power_mw_per_machine": 4.0,
        "last_power_mw": 4.0,
    }
    base.update(overrides)
    return SfyMachineGroup(**base)  # type: ignore[arg-type]


def test_each_designer_mark_takes_its_dimensions_from_the_games_own_buildable() -> None:
    registry = load_registry()
    assert designer("mk1", registry).dims == (4, 4, 4)
    assert designer("mk2", registry).dims == (5, 5, 5)
    assert designer("mk3", registry).dims == (6, 6, 6)


def test_a_designer_cell_is_one_of_the_games_own_foundations() -> None:
    registry = load_registry()
    foundation = registry.buildables[FOUNDATION_CLASS]
    assert foundation.width_cm == foundation.depth_cm  # square, or the size is one number short
    assert foundation_cm(registry) == foundation.width_cm
    assert designer("mk2", registry).foundation_cm == foundation.width_cm


def test_a_foundation_that_is_not_square_is_refused_rather_than_halved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A designer's size is cells times ONE number, so the cell must be square.

    Today's foundation is 800 x 800, so nothing in the shipped data exercises
    this.  Skewing the registry entry is the only way to show the guard is real
    rather than a comment.
    """
    registry = load_registry()
    skewed = replace(registry.buildables[FOUNDATION_CLASS], depth_cm=1200.0)
    monkeypatch.setitem(registry.buildables, FOUNDATION_CLASS, skewed)
    with pytest.raises(ValueError, match="not square"):
        foundation_cm(registry)
    with pytest.raises(ValueError, match="not square"):
        designer("mk1", registry)


def test_a_foundation_with_no_footprint_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = load_registry()
    sizeless = replace(registry.buildables[FOUNDATION_CLASS], width_cm=None, depth_cm=None)
    monkeypatch.setitem(registry.buildables, FOUNDATION_CLASS, sizeless)
    with pytest.raises(ValueError, match="declares no footprint"):
        foundation_cm(registry)


def test_a_designer_states_its_half_width_and_height_in_centimetres() -> None:
    mk2 = Designer(mark="mk2", dims=(5, 5, 5), foundation_cm=foundation_cm(load_registry()))
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


def test_a_clock_above_the_machines_own_ceiling_is_refused() -> None:
    with pytest.raises(ValidationError, match="ceiling"):
        _group(clock=Fraction(3))
    # The ceiling is per machine, not a constant: a group whose buildable takes
    # no power shards at all refuses any overclock.
    with pytest.raises(ValidationError, match="ceiling"):
        _group(clock=Fraction(3, 2), max_clock=Fraction(1))


def test_a_row_buys_shards_and_power_for_its_underclocked_last_machine_apart() -> None:
    group = _group(
        count=3,
        clock=Fraction(5, 2),
        last_clock=Fraction(1, 2),
        power_shards_per_machine=3,
        last_power_shards=0,
        power_mw_per_machine=13.4,
        last_power_mw=1.6,
    )
    assert group.row_power_shards == 6  # two full machines, none for the last
    assert group.row_power_mw == pytest.approx(2 * 13.4 + 1.6)


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


@pytest.mark.parametrize("capacity", [Fraction(0), Fraction(-1)])
def test_a_pipe_with_nonpositive_capacity_is_refused(capacity: Fraction) -> None:
    with pytest.raises(ValidationError):
        PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=capacity)


def test_pipe_tiers_must_be_strictly_increasing_in_capacity() -> None:
    with pytest.raises(ValidationError):
        SfyBuildSpec(
            groups=(),
            belt_item_id="conveyor-belt-mk1",
            belt_items_per_second=Fraction(1),
            pipe_tiers=(
                PipeTier(item_id="pipeline-mk2", cubic_metres_per_second=Fraction(10)),
                PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),
            ),
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
    assert spec.power_mw == pytest.approx(20.0)
    assert spec.power_shards == 0


# --- which groups a belt can join machine to machine ------------------------


def _paired_spec(**consumer: object) -> SfyBuildSpec:
    """Smelters making exactly what Constructors eat, with the consumer tweaked.

    The base pair is the shape ``iron-plate*60`` has: three machines each side,
    each Smelter making half an ingot a second and each Constructor eating half.
    Every test below changes ONE thing about the consumer and asks whether the
    pair survives it.
    """
    maker = _group(
        recipe_id="iron-ingot",
        recipe_class="Recipe_IngotIron_C",
        machine_item_id="smelter-id",
        machine_class="Build_SmelterMk1_C",
        inputs_per_machine={"iron-ore": Fraction(1, 2)},
        outputs_per_machine={"iron-ingot": Fraction(1, 2)},
    )
    return SfyBuildSpec(
        groups=(maker, _group(**consumer)),
        external_inputs={"iron-ore": Fraction(3, 2)},
        outputs={"iron-plate": Fraction(1)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
    )


def test_two_groups_whose_rates_match_machine_for_machine_are_a_direct_pair() -> None:
    (pair,) = direct_pairs(_paired_spec())
    assert pair.item == "iron-ingot"
    assert (pair.producer.recipe_id, pair.consumer.recipe_id) == ("iron-ingot", "iron-plate")


def test_an_odd_last_machine_that_runs_a_different_share_is_not_a_direct_pair() -> None:
    """The one comparison the whole-build tests cannot reach.

    A group's LAST machine runs at ``last_clock`` rather than ``clock``, so what
    it makes -- or eats -- is its own share of the group's per-machine rate. Two
    groups can therefore agree on every other number and still not pair: three
    Smelters whose last runs at half make 1/2, 1/2, 1/4 a second, and three
    Constructors all at full clock eat 1/2, 1/2, 1/2, so the last belt of such a
    pair would be short by a quarter for ever.

    The equality is ``producer.last_clock / producer.clock ==
    consumer.last_clock / consumer.clock``, cross-multiplied to stay in exact
    integers, and it is checked in both directions: an underclocked PRODUCER
    starves its partner and an underclocked CONSUMER backs its own up.
    """
    maker, eater = _paired_spec().groups
    starved = _paired_spec(last_clock=Fraction(1, 2))
    assert direct_pairs(starved) == ()
    assert maker.count == eater.count
    assert maker.outputs_per_machine["iron-ingot"] == eater.inputs_per_machine["iron-ingot"]
    # And the mirror: the producer's last machine underclocked instead.
    backed_up = SfyBuildSpec(
        groups=(maker.model_copy(update={"last_clock": Fraction(1, 2)}), eater),
        external_inputs={"iron-ore": Fraction(3, 2)},
        outputs={"iron-plate": Fraction(1)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
    )
    assert direct_pairs(backed_up) == ()
    # Underclock BOTH last machines by the same share and they pair again, which
    # is what says the rule is about the share and not about the clock.
    together = SfyBuildSpec(
        groups=(
            maker.model_copy(update={"last_clock": Fraction(1, 2)}),
            eater.model_copy(update={"last_clock": Fraction(1, 2)}),
        ),
        external_inputs={"iron-ore": Fraction(3, 2)},
        outputs={"iron-plate": Fraction(1)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
    )
    assert [pair.item for pair in direct_pairs(together)] == ["iron-ingot"]
