"""The ``ManifoldRows`` strategy: rows, corridors, trunks, bridges, a floor.

Every layout here is handed to Task 6's validator with the spec it realises, and
the test asserts the report is clean AND that the only checks standing aside are
the four that always do.  The two fixture flows are FactorioLab's own, read
through ``spec_from_flow``: nothing about a rate, a count or a belt tier is
written here.

Where a spec refuses, the test names the cause.  A refusal is a result: the
strategy either hands back a placement the validator passes or says why it will
not, and "it laid something out" is not evidence that it laid out the build.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from unittest.mock import patch

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import BudgetExhausted, WorkBudget
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.header import read_header
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout import nets, strategy
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.manifold import RowError, crossing_gap_cm
from flab2bp.sfy.layout.model import AttachmentObj, SfyPlacement
from flab2bp.sfy.layout.refusals import GAME_DATA, REFUSALS
from flab2bp.sfy.layout.rows import _Row
from flab2bp.sfy.layout.strategy import ManifoldRows
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import (
    SfyBuildSpec,
    SfyMachineGroup,
    designer,
    direct_pairs,
)
from flab2bp.sfy.templates import TemplateLibrary
from tests.sfy.conftest import fixture_paths, flow_spec, sfy_registry

#: What a clean report stands aside on, and nothing else: the two partial rules.
#: ``flow.boundary`` is NOT here -- every build this strategy lays out has its
#: entries and exits on the walls -- and neither is ``power.wires``, since Task 9
#: gave every row a pole line and a wire from every machine onto it.
ALWAYS_SKIPPED = {"geom.hard_clearance", "belt.capsule"}

#: The designer every build in this file is laid out in.  Two rows and the room
#: their trunks need to turn between them is 42 m of band, which is more than the
#: mk1's 32 and the mk2's 40, so every build here is an mk3 one; the tests that
#: name a smaller mark assert the refusal.
MARK = "mk3"


def _registry() -> Registry:
    return sfy_registry()


def _spec(name: str) -> SfyBuildSpec:
    return flow_spec(name)


def _lay_out(spec: SfyBuildSpec, mark: str) -> SfyPlacement:
    return ManifoldRows().lay_out(spec, designer(mark, _registry()))


def _refusal(spec: SfyBuildSpec, mark: str) -> str:
    with pytest.raises(NoValidLayout) as caught:
        _lay_out(spec, mark)
    return caught.value.reason


def _assert_clean(placement: SfyPlacement, spec: SfyBuildSpec) -> None:
    report = validate(placement, spec, _registry())
    assert [f.message for f in report.errors] == []
    assert report.ok
    assert set(report.skipped) == ALWAYS_SKIPPED
    for cid in report.skipped:
        assert report.by_check(cid), f"{cid} stood aside without saying why"


def _corridor(placement: SfyPlacement, kind: str) -> list[AttachmentObj]:
    """The attachments of ``kind`` standing in a corridor rather than in a row.

    Which they are is not a threshold to write down: a corridor runs along ``Y``
    and a row along ``X``, so an attachment in a corridor is turned a quarter
    turn against the rows' own and the yaw says which is which.
    """
    return [
        obj
        for obj in placement.attachments
        if obj.class_name.endswith(kind) and obj.pose.yaw_deg == 90.0
    ]


def _group(spec: SfyBuildSpec, recipe_id: str) -> SfyMachineGroup:
    return next(g for g in spec.groups if g.recipe_id == recipe_id)


def _fragment(
    groups: tuple[SfyMachineGroup, ...],
    external_inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
    surplus: dict[str, Fraction] | None = None,
    label: str = "",
) -> SfyBuildSpec:
    """A spec built out of the fixture flow's own groups, belts and rates.

    Every group is one FactorioLab chose, at the rates and on the belts the
    flow's URL asked for.  What a fragment chooses is which rows stand together
    and what crosses the boundary -- the external inputs, the outputs and the
    surplus are this test's own, and each one is the number that balances the
    rows it is written beside.  That is the only way to put a net with two sinks
    or two sources in front of the strategy at all: a two-row build is all a
    Blueprint Designer holds, and both shapes need one row to meet something else
    in the same corridor.
    """
    source = _spec("reinforced-iron-plate-10")
    return SfyBuildSpec(
        groups=groups,
        external_inputs=external_inputs,
        outputs=outputs,
        surplus_outputs=surplus or {},
        belt_item_id=source.belt_item_id,
        belt_items_per_second=source.belt_items_per_second,
        belt_upgrades=source.belt_upgrades,
        label=label,
    )


# --- the two fixture flows -------------------------------------------------


def test_iron_plate_at_sixty_lays_out_and_validates_clean() -> None:
    spec = _spec("iron-plate-60")
    placement = _lay_out(spec, MARK)
    _assert_clean(placement, spec)
    assert len(placement.machines) == spec.machine_count == 6
    assert {machine.class_name for machine in placement.machines} == {
        "Build_SmelterMk1_C",
        "Build_ConstructorMk1_C",
    }


def test_a_two_row_chain_the_rates_do_not_pair_still_wants_a_bigger_designer() -> None:
    """``iron-rod*60`` is two rows of three and the manifold is what serves it.

    Its smelters make 1/2 an ingot each and its constructors eat 1/4, so no belt
    joins one machine to one machine: the rates have to be merged and split
    again, which is a chain pair, a trunk and a corridor -- and that is still
    more band than either smaller designer has.  The pairing is a property of the
    SPEC, and a spec that does not have it does not get it.
    """
    spec = _spec("iron-rod-60")
    assert direct_pairs(spec) == ()
    assert _refusal(spec, "mk1") == "rows exceed the designer depth"
    assert _refusal(spec, "mk2") == "rows exceed the designer depth"
    _assert_clean(_lay_out(spec, "mk3"), spec)


def test_ten_reinforced_plates_a_minute_needs_five_rows_and_no_designer_holds_them() -> None:
    """The five-row build the brief predicted, measured rather than assumed.

    Smelter, plate, rod, screw and assembler rows are 88 m of band between them,
    and 110 m with the gaps their trunks turn in and the room at the two walls;
    the largest Blueprint Designer is 48 m deep.  The cause is named for every
    mark the game ships, which is the honest answer to "lay this out".
    """
    spec = _spec("reinforced-iron-plate-10")
    assert len(spec.groups) == 5
    for mark in ("mk1", "mk2", "mk3"):
        assert _refusal(spec, mark) == "rows exceed the designer depth"


# --- what the build is made of ---------------------------------------------


def test_the_rows_stand_in_production_order_and_face_about_in_turn() -> None:
    """A producing row comes before the row that eats what it makes.

    The smelter row is laid at the ``-Y`` end and the constructor row after it,
    so iron ingots run one way up the corridor; and the second row is flipped,
    so its chain input meets the first row's merger output in the same corridor
    instead of across the build.
    """
    placement = _lay_out(_spec("iron-plate-60"), MARK)
    smelters = [m for m in placement.machines if m.class_name == "Build_SmelterMk1_C"]
    constructors = [m for m in placement.machines if m.class_name == "Build_ConstructorMk1_C"]
    assert max(m.pose.y for m in smelters) < min(m.pose.y for m in constructors)
    assert min(m.pose.x for m in smelters) < 0 < max(m.pose.x for m in smelters)


def test_every_external_input_arrives_at_the_minus_y_wall_and_every_output_leaves_at_the_plus() -> (
    None
):
    spec = _spec("iron-plate-60")
    placement = _lay_out(spec, MARK)
    half = placement.designer.half_cm
    entries = {run.item_id: run.start for run in placement.belts if run.boundary_start}
    exits = {run.item_id: run.end for run in placement.belts if run.boundary_end}
    assert set(entries) == set(spec.external_inputs) == {"iron-ore"}
    assert set(exits) == set(spec.outputs) == {"iron-plate"}
    assert entries["iron-ore"][1] == -half
    assert exits["iron-plate"][1] == half
    assert entries["iron-ore"][2] == exits["iron-plate"][2] == 200.0


def test_the_whole_build_survives_the_blueprint_round_trip() -> None:
    """``roundtrip`` inside ``validate`` says the same thing; both are kept.

    This one names the failure in the terms Task 5 left: the placement that
    comes back out of the file is the placement that went in, links and all.
    """
    placement = _lay_out(_spec("iron-plate-60"), MARK)
    newest = max(
        (read_header(Reader(path.read_bytes())) for path in fixture_paths()),
        key=lambda header: (header.save_version, header.build_version),
    )
    again = decode(
        emit(
            placement,
            _registry(),
            TemplateLibrary.from_fixtures(fixture_paths()),
            load_lab_map(),
            build_version=newest.build_version,
            version_data=newest.version_data,
        ),
        _registry(),
    )
    assert again == placement


# --- splitting, merging and bridging ---------------------------------------


def test_a_source_with_two_sinks_gets_a_splitter_chain_in_the_corridor() -> None:
    """Ingots feed the rod row and the rest leaves the build: one source, two sinks."""
    flow = _spec("reinforced-iron-plate-10")
    spec = _fragment(
        groups=(_group(flow, "iron-ingot"), _group(flow, "iron-rod")),
        external_inputs={"iron-ore": Fraction(2)},
        outputs={"iron-rod": Fraction(1, 2)},
        surplus={"iron-ingot": Fraction(3, 2)},
        label="ingots split between a row and the boundary",
    )
    placement = _lay_out(spec, MARK)
    _assert_clean(placement, spec)
    corridor = _corridor(placement, "Splitter_C")
    assert corridor, "a source with two sinks is split in the corridor, not in a row"


def test_two_sources_into_one_sink_get_a_merger_before_the_chain_end() -> None:
    """The rod row eats what the smelters make and what the wall belts in."""
    flow = _spec("reinforced-iron-plate-10")
    spec = _fragment(
        groups=(_group(flow, "iron-ingot"), _group(flow, "iron-rod")),
        external_inputs={"iron-ore": Fraction(2), "iron-ingot": Fraction(1, 4)},
        outputs={"iron-rod": Fraction(1, 2)},
        surplus={"iron-ingot": Fraction(7, 4)},
        label="two sources of one item",
    )
    placement = _lay_out(spec, MARK)
    _assert_clean(placement, spec)
    assert _corridor(placement, "Merger_C")


def test_a_trunk_that_must_cross_an_occupied_column_rides_over_it() -> None:
    """Two trunks up one corridor, one crossing the other.

    The smelter row's ingots leave at the ``+Y`` wall and the screw row's rods
    arrive from the ``-Y`` wall, both up the ``+X`` corridor.  The rod trunk turns
    out first, so it takes the inner column and runs the whole way from the wall
    past the smelter row; the ingot trunk therefore leaves its row INSIDE the rod
    trunk's span and has to get over it.  It climbs a crossing gap before the
    column it crosses, crosses at that height, and comes down in its own column.
    """
    flow = _spec("reinforced-iron-plate-10")
    spec = _fragment(
        groups=(_group(flow, "iron-ingot"), _group(flow, "screw")),
        external_inputs={"iron-ore": Fraction(2), "iron-rod": Fraction(1, 2)},
        outputs={"screw": Fraction(2)},
        surplus={"iron-ingot": Fraction(2)},
        label="a trunk that crosses a trunk",
    )
    placement = _lay_out(spec, MARK)
    _assert_clean(placement, spec)
    gap = crossing_gap_cm(_registry())
    bridged = [
        run
        for run in placement.belts
        if {round(point[2]) for point, _, _ in run.points} == {200, round(200 + gap)}
    ]
    assert {run.item_id for run in bridged} == {"iron-ingot"}


def test_a_paired_build_runs_three_straight_belts_and_nothing_between_the_rows() -> None:
    """Machine to machine, because the spec's own rates already balance them.

    Three smelters each make half an ingot a second and three constructors each
    eat half: FactorioLab has already solved that, and merging six half-rates
    into one trunk and splitting them back out again moves the same items over
    more floor.  So the two lines stand facing each other with one straight belt
    between each pair -- and NO attachment stands between them at all.
    """
    spec = _spec("iron-plate-60")
    (pair,) = direct_pairs(spec)
    assert pair.item == "iron-ingot"
    placement = _lay_out(spec, MARK)
    smelters = [m for m in placement.machines if m.class_name == "Build_SmelterMk1_C"]
    constructors = [m for m in placement.machines if m.class_name == "Build_ConstructorMk1_C"]
    # Each pair faces the other across one belt, at the same X.
    assert sorted(m.pose.x for m in smelters) == sorted(m.pose.x for m in constructors)
    wired = {m.id for m in smelters} | {m.id for m in constructors}
    joining = [
        run
        for run in placement.belts
        if run.item_id == pair.item
        and {link.a[0] for link in placement.links if link.b[0] == run.id} <= wired
        and {link.b[0] for link in placement.links if link.a[0] == run.id} <= wired
    ]
    assert len(joining) == 3, "one straight belt per pair of machines"
    for run in joining:
        assert len(run.points) == 2, "and it is straight"
        assert run.start[0] == pytest.approx(run.end[0], abs=1.0)
        assert run.start[1] < run.end[1], "and it runs from the maker to the eater"
    # Nothing stands between the two lines.
    between = [
        obj
        for obj in placement.attachments
        if min(m.pose.y for m in constructors) > obj.pose.y > max(m.pose.y for m in smelters)
    ]
    assert between == [], "no chain pair and no trunk between a paired row's two lines"
    assert "paired on iron-ingot" in placement.description


# --- refusals --------------------------------------------------------------


def test_a_belt_the_spec_cannot_fund_is_refused_by_the_ceiling() -> None:
    spec = _spec("iron-plate-60")
    slow = spec.model_copy(update={"belt_upgrades": ()})
    assert _refusal(slow, MARK) == "run exceeds the belt ceiling"


def test_a_fluid_in_the_spec_is_refused_as_a_later_milestone() -> None:
    flow = _spec("iron-plate-60")
    wet = flow.model_copy(
        update={"external_inputs": {**flow.external_inputs, "water": Fraction(1)}}
    )
    assert _refusal(wet, MARK) == "fluids are M5"


def test_the_strategy_names_itself() -> None:
    assert ManifoldRows().name == "manifold-rows"


# --- the causes nothing else reaches ---------------------------------------


def test_every_cause_this_module_maps_onto_is_one_it_may_refuse_with() -> None:
    """A mapping table that named a cause ``REFUSALS`` has not got would turn a
    refusal into a ``ValueError`` at the worst moment.

    The table is :mod:`flab2bp.sfy.layout.refusals`, shared with the other
    strategy; this module maps its stages' errors onto entries in it.
    """
    mapped = {cause for _, cause in strategy._ROW_CAUSES}
    mapped |= set(strategy._CORRIDOR_CAUSES.values())
    mapped |= set(strategy._POWER_CAUSES.values())
    assert mapped <= set(REFUSALS)


def test_an_unmapped_row_error_raises_rather_than_wearing_another_causes_name() -> None:
    with pytest.raises(ValueError, match="no named refusal"):
        strategy._row_cause(RowError("something nobody has thought of yet"))


def test_a_row_builder_cause_keeps_its_own_name() -> None:
    """Two of the row builder's causes, mapped where a reader would look for them."""
    assert strategy._row_cause(RowError("row too tall: the row reaches z = 4000")) == "row too tall"
    assert (
        strategy._row_cause(RowError("the lab map has no conveyor class for 'conveyor-belt-mk9'"))
        == GAME_DATA
    )


def test_a_product_the_spec_never_sends_out_is_refused_by_its_own_cause() -> None:
    """Iron plate made by a row, eaten by nothing, and named in neither the
    outputs nor the surplus: the merger chain would end on an unwired belt."""
    flow = _spec("reinforced-iron-plate-10")
    spec = _fragment(
        groups=(_group(flow, "iron-ingot"), _group(flow, "iron-plate")),
        external_inputs={"iron-ore": Fraction(3, 2)},
        outputs={},
        label="a row nobody drains",
    )
    assert _refusal(spec, MARK) == "a row makes something the spec never sends out"


# --- the budget bounds the whole of lay_out --------------------------------


def test_a_clock_that_has_already_run_out_refuses_before_a_row_is_built() -> None:
    spec = _spec("iron-plate-60")
    with pytest.raises(NoValidLayout) as caught:
        ManifoldRows().lay_out(spec, designer(MARK, _registry()), time_budget_s=-1.0)
    assert caught.value.reason == "layout exceeded the budget"


def test_a_column_search_that_runs_out_of_tries_says_so_instead() -> None:
    """The clock and the column allowance are two bounds and two answers."""
    spec = _spec("iron-plate-60")
    with patch.object(strategy, "COLUMN_TRIES", 1), pytest.raises(NoValidLayout) as caught:
        _lay_out(spec, MARK)
    assert caught.value.reason == "corridor assignment exceeded the budget"


def test_the_clock_is_read_again_once_the_columns_are_planned() -> None:
    """The budget bounds the DRAWING as well as the search.

    Every net turns into belts, turns and links after the columns are settled,
    and a clock that ran out between the two would otherwise not be looked at
    until the build was finished.  The fake clock here runs out at exactly that
    moment: the rows are built, the columns are assigned, and the first net to be
    drawn finds the time gone.
    """
    layout = strategy._Layout(
        spec=_spec("iron-plate-60"),
        designer=designer(MARK, _registry()),
        registry=_registry(),
        lab_map=load_lab_map(),
        budget=WorkBudget(),
    )
    settled: dict[str, object] = {"rows": (), "columns": False}
    settle = nets.NetPlanner._plan_columns

    def watched(planner: nets.NetPlanner, rows: Sequence[_Row], x_edge: float) -> nets.CorridorPlan:
        plan = settle(planner, rows, x_edge)
        settled["rows"], settled["columns"] = tuple(rows), True
        return plan

    layout.budget = WorkBudget(deadline=1.0, clock=lambda: 100.0 if settled["columns"] else 0.0)
    with patch.object(nets.NetPlanner, "_plan_columns", watched), pytest.raises(BudgetExhausted):
        layout.build()
    assert settled["rows"], "the rows were built before the clock ran out"
    assert settled["columns"], "and so were the columns"
