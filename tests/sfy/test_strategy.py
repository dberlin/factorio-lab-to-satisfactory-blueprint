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

from fractions import Fraction
from functools import cache
from pathlib import Path

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.flow import load_flow
from flab2bp.lab.url import Game, parse_url
from flab2bp.layout.base import NoValidLayout
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.header import read_header
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.manifold import crossing_gap_cm
from flab2bp.sfy.layout.model import SfyPlacement
from flab2bp.sfy.layout.strategy import ManifoldRows
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.rates import spec_from_flow
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.sfy.templates import TemplateLibrary
from tests.sfy.conftest import fixture_paths

FLOWS = Path(__file__).resolve().parents[1] / "fixtures" / "sfy_flows"

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


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _spec(name: str) -> SfyBuildSpec:
    url = (FLOWS / f"{name}.csv").read_text(encoding="utf-8").splitlines()[0].strip().strip('"')
    return spec_from_flow(
        load_vendored(Game.SFY),
        parse_url(url),
        load_flow(FLOWS / f"{name}.csv", url=url),
        _registry(),
        load_lab_map(),
    )


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

    Nothing here invents a rate: every group is one FactorioLab chose, and the
    belt floor and upgrades are the ones the flow's URL asked for.  What the
    fragment chooses is which rows stand together, which is how a net with two
    sinks or two sources is put in front of the strategy at all -- a two-row
    build is all a Blueprint Designer holds, and both shapes need one row to
    meet something else in the same corridor.
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


def test_iron_plate_at_sixty_does_not_fit_the_two_smaller_designers() -> None:
    """Two rows of 16 m, and neither smaller designer is deep enough for them.

    The brief expected this build in an mk1, and the measurement says otherwise:
    the two rows are 32 m of band before anything is between them, and 42 m with
    the gap a trunk needs to turn out of one row and into the next and the room a
    belt needs to turn in at each wall.  mk1 is 32 m deep and mk2 is 40.  The
    refusal names the depth and says what it wanted, for both.
    """
    spec = _spec("iron-plate-60")
    assert _refusal(spec, "mk1") == "rows exceed the designer depth"
    assert _refusal(spec, "mk2") == "rows exceed the designer depth"


def test_ten_reinforced_plates_a_minute_needs_five_rows_and_no_designer_holds_them() -> None:
    """The five-row build the brief predicted, measured rather than assumed.

    Smelter, plate, rod, screw and assembler rows stack to 92 m of band; the
    largest Blueprint Designer is 48 m deep.  The cause is named for every mark
    the game ships, which is the honest answer to "lay this out".
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


def test_a_full_floor_of_foundations_stands_under_the_whole_designer() -> None:
    placement = _lay_out(_spec("iron-plate-60"), MARK)
    half = placement.designer.half_cm
    side = placement.designer.foundation_cm
    slabs = placement.foundations
    assert {slab.class_name for slab in slabs} == {FOUNDATION_CLASS}
    assert len(slabs) == (2 * half / side) ** 2
    assert {slab.pose.z for slab in slabs} == {side / 16.0}  # 50 cm: the box's own half
    assert min(slab.pose.x for slab in slabs) == -half + side / 2.0
    assert max(slab.pose.y for slab in slabs) == half - side / 2.0


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
    splitters = [a for a in placement.attachments if a.class_name.endswith("Splitter_C")]
    corridor = [a for a in splitters if abs(a.pose.x) > 1300.0]
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
    mergers = [a for a in placement.attachments if a.class_name.endswith("Merger_C")]
    assert [a for a in mergers if abs(a.pose.x) > 1300.0]


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
    assert [run.item_id for run in bridged] == ["iron-ingot"]
    # It is over the crossing before it turns, and down again inside its column.
    over = next(iter(bridged))
    assert over.start[2] == 200.0 and over.end[2] == 200.0


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
    assert _refusal(wet, MARK) == "fluids are M4"


def test_the_strategy_names_itself() -> None:
    assert ManifoldRows().name == "manifold-rows"
