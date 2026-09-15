"""The row planner: how many rows a group is laid as, and what each one carries.

Splitting a group is arithmetic on the floor between the two corridors and on the
machine's own footprint, and a whole build is a poor place to read either of
them.  The whole-build tests that exercise the same code through ``lay_out`` --
``concrete*60`` laid as two rows in an mk2, and the depth refusals -- are in
``tests/sfy/test_strategy.py``, which is where the placement they produce is
judged.
"""

from __future__ import annotations

from fractions import Fraction
from functools import partial

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout import strategy
from flab2bp.sfy.layout.manifold import hard_footprint_cm, machine_pitch_cm
from flab2bp.sfy.layout.refusals import refuse
from flab2bp.sfy.layout.rows import RowPlanner, _split_group, _Unit
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer, direct_pairs
from tests.sfy.conftest import flow_spec, sfy_registry

#: The designer the numbers below are read against; see ``test_strategy.py``.
MARK = "mk3"


def _group(spec: SfyBuildSpec, recipe_id: str) -> SfyMachineGroup:
    return next(g for g in spec.groups if g.recipe_id == recipe_id)


def _planner(spec: SfyBuildSpec, mark: str = MARK) -> RowPlanner:
    """A row planner with its numbers read and nothing laid out yet."""
    frame = designer(mark, sfy_registry())
    return RowPlanner(
        spec=spec,
        designer=frame,
        registry=sfy_registry(),
        lab_map=load_lab_map(),
        budget=WorkBudget(),
        measures=strategy._measure(sfy_registry(), frame),
        refuse=partial(refuse, spec),
    )


def test_a_split_group_is_the_same_machines_at_the_same_clocks() -> None:
    """Seven machines in rows of at most four: four and three, the underclocked
    one in the last row, and the multiset of clocks the spec's own."""
    flow = flow_spec("reinforced-iron-plate-10")
    group = _group(flow, "screw").model_copy(update={"count": 7, "last_clock": Fraction(1, 4)})
    shares = _split_group(group, 2)
    assert [share.count for share in shares] == [4, 3]
    assert [share.clock for share in shares] == [Fraction(1), Fraction(1)]
    assert [share.last_clock for share in shares] == [Fraction(1), Fraction(1, 4)]
    # What spec.machines compares: count - 1 at clock and one at last_clock.
    clocks: list[Fraction] = []
    for share in shares:
        clocks += [share.clock] * (share.count - 1) + [share.last_clock]
    assert sorted(clocks) == sorted([Fraction(1, 4)] + [Fraction(1)] * 6)
    # And what each row's chain ends carry is that row's own share.
    assert sum(share.row_outputs["screw"] for share in shares) == group.row_outputs["screw"]


def test_a_split_shares_power_the_way_it_shares_clocks() -> None:
    """Only the LAST share's last machine is the underclocked one.

    ``last_power_shards`` and ``last_power_mw`` are stated for ``last_clock``,
    so a share whose last machine runs at the group's clock must carry the
    group's per-machine figures instead.  Summed back up, the shares order the
    shards the group ordered and draw the megawatts the group draws.
    """
    flow = flow_spec("reinforced-iron-plate-10")
    group = _group(flow, "screw").model_copy(
        update={
            "count": 7,
            "clock": Fraction(5, 2),
            "last_clock": Fraction(1, 4),
            "power_shards_per_machine": 3,
            "last_power_shards": 0,
            "power_mw_per_machine": 40.0,
            "last_power_mw": 1.0,
        }
    )
    shares = _split_group(group, 2)
    assert [share.last_power_shards for share in shares] == [3, 0]
    assert [share.last_power_mw for share in shares] == [40.0, 1.0]
    assert sum(share.row_power_shards for share in shares) == group.row_power_shards
    assert sum(share.row_power_mw for share in shares) == pytest.approx(group.row_power_mw)


def test_how_many_rows_a_group_is_laid_as_is_what_the_floor_holds() -> None:
    """``per_row`` is the floor between the corridors over the machine pitch, and
    a row of ``n`` is ``n - 1`` pitches plus one machine's own footprint."""
    planner = _planner(flow_spec("iron-plate-60"))
    group = _group(flow_spec("iron-plate-60"), "iron-plate").model_copy(update={"count": 7})
    units = [_Unit(groups=(group,))]
    machine = sfy_registry().buildables[group.machine_class]
    pitch = machine_pitch_cm(machine, sfy_registry().limits)
    x0, _, x1, _ = hard_footprint_cm(machine)
    # An allowance that leaves exactly four pitches' worth of floor.
    usable = 3.0 * pitch + (x1 - x0)
    allowance = planner.measures.half - usable / 2.0
    assert planner._splits(units, [1], allowance) == [2]
    # One machine wider than the floor is the refusal that remains, and it says
    # which class could not be stood.
    with pytest.raises(NoValidLayout) as caught:
        planner._splits(units, [1], planner.measures.half - (x1 - x0) / 2.0 + 100.0)
    assert caught.value.reason == "rows exceed the designer width"
    assert group.machine_class in caught.value.attempt_reasons[0]


def test_a_pair_is_measured_at_the_wider_of_its_two_machines() -> None:
    """Its two lines share one pitch, so that machine ``i`` faces machine ``i``.

    A Smelter's box is 500 cm across and a Constructor's 800, so the paired unit
    is laid at the Constructor's 900 cm pitch on both lines: a floor that holds
    three Smelters is not the floor this pair needs.
    """
    spec = flow_spec("iron-plate-60")
    (pair,) = direct_pairs(spec)
    planner = _planner(spec)
    units = [_Unit(groups=(pair.producer, pair.consumer), pair=pair)]
    registry = sfy_registry()
    constructor = registry.buildables[pair.consumer.machine_class]
    pitch = machine_pitch_cm(constructor, registry.limits)
    x0, _, x1, _ = hard_footprint_cm(constructor)
    # Exactly three Constructors' worth of floor: the pair is one row.
    allowance = planner.measures.half - (2.0 * pitch + (x1 - x0)) / 2.0
    assert planner._splits(units, [1], allowance) == [1]
    # A centimetre less and it is two, which is the point at which the pairing is
    # given up -- ``_plan_rows`` never lays half a pair.
    assert planner._splits(units, [1], allowance + 1.0) == [2]
