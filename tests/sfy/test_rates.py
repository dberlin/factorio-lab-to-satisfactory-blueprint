"""Turning a captured FactorioLab Satisfactory flow into a build spec.

Every expectation here is pinned to a committed flow export.  FactorioLab's
chosen flow is authoritative: where the brief's arithmetic and the export
disagree, these tests state what the export says.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.flow import load_flow
from flab2bp.lab.url import Game, parse_url
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.rates import RatesRefusal, spec_from_flow
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup

FLOWS = Path(__file__).resolve().parent.parent / "fixtures" / "sfy_flows"

#: Every fixture is exported at FactorioLab's per-minute display rate.
PER_MINUTE = Fraction(1, 60)


def _spec(name: str) -> SfyBuildSpec:
    path = FLOWS / f"{name}.csv"
    url = path.read_text(encoding="utf-8").splitlines()[0].strip().strip('"')
    return spec_from_flow(
        load_vendored(Game.SFY),
        parse_url(url),
        load_flow(path, url=url),
        load_registry(),
        load_lab_map(),
    )


def _group(spec: SfyBuildSpec, recipe_id: str) -> SfyMachineGroup:
    return next(g for g in spec.groups if g.recipe_id == recipe_id)


def test_iron_plate_at_sixty_is_three_constructors_at_full_clock() -> None:
    spec = _spec("iron-plate-60")
    plate = _group(spec, "iron-plate")
    assert plate.machine_class == "Build_ConstructorMk1_C"
    assert plate.recipe_class == "Recipe_IronPlate_C"
    assert plate.count == 3  # 60/min needs 3 constructors at 20/min
    assert plate.clock == 1 and plate.last_clock == 1
    assert plate.row_outputs["iron-plate"] == Fraction(1)  # 60/min = 1/s


def test_ten_reinforced_plates_a_minute_is_two_assemblers_at_full_clock() -> None:
    # The brief predicted an underclocked last assembler.  The captured flow
    # says Machines=2 exactly -- 10/min over 5/min per assembler divides -- and
    # the flow is authoritative, so the whole count is what this pins.
    spec = _spec("reinforced-iron-plate-10")
    rip = _group(spec, "reinforced-iron-plate")
    assert rip.machine_class == "Build_AssemblerMk1_C"
    assert rip.count == 2 and rip.last_clock == Fraction(1)
    assert rip.row_outputs["reinforced-iron-plate"] == Fraction(1, 6)


def test_a_fractional_machine_count_rounds_up_and_underclocks_the_last_machine() -> None:
    spec = _spec("iron-plate-50")
    plate = _group(spec, "iron-plate")
    assert plate.count == 3  # FactorioLab asks for 2.5 constructors
    assert plate.clock == Fraction(1) and plate.last_clock == Fraction(1, 2)
    # Two constructors at 20/min plus one at 10/min is the 50/min asked for.
    assert plate.row_outputs["iron-plate"] == Fraction(5, 6)


@pytest.mark.parametrize(
    "name",
    [
        "iron-plate-60",
        "reinforced-iron-plate-10",
        "iron-plate-50",
        "iron-plate-60-overclock-250",
        "iron-plate-60-somersloop",
        "iron-plate-60-belt-mk3",
    ],
)
def test_the_row_rates_agree_with_the_flow_to_the_fraction(name: str) -> None:
    path = FLOWS / f"{name}.csv"
    url = path.read_text(encoding="utf-8").splitlines()[0].strip().strip('"')
    flow = load_flow(path, url=url)
    spec = _spec(name)
    assert spec.groups, "the flow should have produced at least one group"
    for group in spec.groups:
        row = flow.by_recipe[group.recipe_id]
        assert row.machines is not None and row.items is not None
        produced = row.items + (row.surplus or 0)
        # FactorioLab's fractional machine count times our per-machine rate must
        # land exactly on the rate FactorioLab wrote for the row's own item.
        assert group.outputs_per_machine[row.item_id] * row.machines == produced * PER_MINUTE, (
            f"{name}: {group.recipe_id} disagrees with the flow"
        )


def test_external_inputs_are_the_flows_mined_items() -> None:
    spec = _spec("iron-plate-60")
    assert spec.external_inputs == {"iron-ore": Fraction(3, 2)}  # 90/min
    # The miner itself is never a group: a Blueprint Designer cannot hold one.
    assert [g.recipe_id for g in spec.groups] == ["iron-plate", "iron-ingot"]
    assert spec.outputs == {"iron-plate": Fraction(1)}
    assert spec.surplus_outputs == {}


def test_a_fluid_in_the_flow_refuses_with_the_cause_named() -> None:
    with pytest.raises(RatesRefusal) as caught:
        _spec("plastic-10")
    assert caught.value.cause == "fluids are M4"
    assert "crude-oil" in str(caught.value)


def test_the_belt_floor_and_ceiling_come_from_the_url_or_the_defaults() -> None:
    default = _spec("iron-plate-60")
    assert default.belt_item_id == "conveyor-belt-mk1"
    assert default.belt_items_per_second == Fraction(1)
    # Mk6 exists in the dataset but defaults.maxBelt stops at Mk5.
    assert [t.item_id for t in default.belt_upgrades] == [
        "conveyor-belt-mk2",
        "conveyor-belt-mk3",
        "conveyor-belt-mk4",
        "conveyor-belt-mk5",
    ]

    chosen = _spec("iron-plate-60-belt-mk3")
    assert chosen.belt_item_id == "conveyor-belt-mk3"
    assert chosen.belt_items_per_second == Fraction(9, 2)
    assert [t.item_id for t in chosen.belt_upgrades] == [
        "conveyor-belt-mk4",
        "conveyor-belt-mk5",
    ]


def test_overclock_above_one_is_carried_and_shards_are_counted() -> None:
    spec = _spec("iron-plate-60-overclock-250")
    plate = _group(spec, "iron-plate")
    assert plate.clock == Fraction(5, 2)  # the URL says moc=250, a percentage
    assert plate.count == 2  # FactorioLab asks for 1.2 constructors
    assert plate.last_clock == Fraction(1, 2)
    # Three shards take a machine from 100% to 250% at half a shard each.
    assert plate.power_shards_per_machine == 3
    assert plate.row_outputs["iron-plate"] == Fraction(1)


def test_a_group_at_the_default_clock_needs_no_power_shards() -> None:
    spec = _spec("iron-plate-60")
    assert all(g.power_shards_per_machine == 0 for g in spec.groups)


def test_somersloops_double_output_the_way_factoriolab_computes_it() -> None:
    spec = _spec("iron-plate-60-somersloop")
    plate = _group(spec, "iron-plate")
    assert plate.somersloops == 1
    # One sloop in the Constructor's one slot is +100% output: 40/min, not 20.
    assert plate.outputs_per_machine["iron-plate"] == Fraction(2, 3)
    # Inputs are per craft and untouched by the boost, so they fall with the
    # craft count: the flow asks for 45 ingots a minute, not 90.
    assert plate.inputs_per_machine["iron-ingot"] == Fraction(1, 2)
    assert plate.count == 2 and plate.last_clock == Fraction(1, 2)
    assert plate.row_outputs["iron-plate"] == Fraction(1)
    assert plate.row_inputs["iron-ingot"] == Fraction(3, 4)
    # Power is (1 + filled/total) squared, so a full Constructor draws 4x.
    assert plate.power_mw_per_machine == Fraction(16)
    # The smelters were left alone by the URL's machine setting.
    ingot = _group(spec, "iron-ingot")
    assert ingot.somersloops == 0
    assert ingot.power_mw_per_machine == Fraction(4)


def test_power_per_machine_is_the_games_draw_scaled_by_the_clock() -> None:
    plain = _group(_spec("iron-plate-60"), "iron-plate")
    assert plain.power_mw_per_machine == Fraction(4)  # registry Build_ConstructorMk1_C
    fast = _group(_spec("iron-plate-60-overclock-250"), "iron-plate")
    assert fast.power_mw_per_machine == Fraction(10)  # 4 MW linear in the clock


def test_a_flow_whose_rates_contradict_the_model_is_refused_with_the_row_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import flab2bp.sfy.rates as rates

    real = rates._crafts_per_second

    def wrong(*args: object, **kwargs: object) -> Fraction:
        return real(*args, **kwargs) * 2  # type: ignore[arg-type]

    monkeypatch.setattr(rates, "_crafts_per_second", wrong)
    with pytest.raises(RatesRefusal) as caught:
        _spec("iron-plate-60")
    assert caught.value.cause == "flow rate disagrees with the rate model"
    assert "iron-plate" in str(caught.value)
