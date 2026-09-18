"""One manifold row, judged by the Task 6 validator rather than by a number here.

The fixtures use FactorioLab rates and native machine, attachment and conveyor
geometry. Rows are centred in real designers before their complete physical
envelopes are judged; the four-input manufacturer needs the largest designer.

The judge is :func:`flab2bp.sfy.layout.validate.validate` with no spec: the three
spec checks are Task 8's, and every geometry check has to pass on the fragment a
row builds.  A test that asserted only positions would pass a row the game would
refuse to paste.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.url import Game
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.geometry import port_forward
from flab2bp.sfy.header import read_header
from flab2bp.sfy.labmap import load_lab_map, machine_class
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.manifold import (
    ChainEnd,
    RowError,
    RowGeometry,
    build_row,
    crossing_gap_cm,
    machine_pitch_cm,
    shortest_belt_cm,
)
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    MachineObj,
    Pose,
    SfyPlacement,
)
from flab2bp.sfy.layout.rows import _translate
from flab2bp.sfy.layout.validate import (
    CHECKS,
    Severity,
    attachment_boxes,
    validate,
)
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, SfyMachineGroup, designer, foundation_cm
from flab2bp.sfy.templates import TemplateLibrary
from flab2bp.spec import BeltTier
from tests.sfy.conftest import fixture_paths

REGISTRY: Registry = load_registry()
LAB_MAP = load_lab_map()

CONSTRUCTOR = "Build_ConstructorMk1_C"
ASSEMBLER = "Build_AssemblerMk1_C"
MANUFACTURER = "Build_ManufacturerMk1_C"

#: Exactly what ``validate(placement, None, registry)`` stands aside from on a
#: row fragment: the three spec checks, because a row is not a spec; the two
#: rules the extraction left partial; the power wires, which a row carries none
#: of; and the boundary, because a row flags no end as one.  Asserted as an
#: EQUALITY -- a check that quietly joined this set would be coverage a row
#: silently lost.
SKIPPED = frozenset(
    {
        "geom.hard_clearance",
        "belt.capsule",
        "flow.capacity",
        "flow.balance",
        "flow.boundary",
        "spec.machines",
        "power.wires",
    }
)

#: What a row is answerable for, so none of these may stand aside.  A row that
#: skipped one of them would be a row nobody finished looking at.
ANSWERABLE = frozenset(
    {
        "geom.bounds",
        "geom.attachment_body",
        "slab.under_every_foot",
        "belt.max_length",
        "belt.min_length",
        "belt.incline",
        "belt.curvature",
        "ports.connected_once",
        "ports.direction",
        "ports.position",
    }
)


def _tiers(*lab_ids: str) -> tuple[BeltTier, ...]:
    """Belt tiers at FactorioLab's own speeds, slowest first."""
    data = load_vendored(Game.SFY)
    return tuple(
        BeltTier(item_id=lab_id, items_per_second=data.belt_speed(lab_id)) for lab_id in lab_ids
    )


MK1 = _tiers("conveyor-belt-mk1")
MK1_TO_MK5 = _tiers(
    "conveyor-belt-mk1",
    "conveyor-belt-mk2",
    "conveyor-belt-mk3",
    "conveyor-belt-mk4",
    "conveyor-belt-mk5",
)


def _group(
    machine_class_name: str,
    recipe_class: str,
    count: int,
    inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
) -> SfyMachineGroup:
    """A group of ``count`` identical machines, all at full clock."""
    return SfyMachineGroup(
        recipe_id=recipe_class.lower(),
        recipe_class=recipe_class,
        machine_item_id="machine",
        machine_class=machine_class_name,
        count=count,
        clock=Fraction(1),
        last_clock=Fraction(1),
        max_clock=Fraction(1),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine=inputs,
        outputs_per_machine=outputs,
        power_mw_per_machine=0.0,
        last_power_mw=0.0,
    )


def _rods(count: int, rate: Fraction = Fraction(1, 4)) -> SfyMachineGroup:
    return _group(
        CONSTRUCTOR,
        "Recipe_IronRod_C",
        count,
        {"iron-ingot": rate},
        {"iron-rod": rate},
    )


def _plates(count: int) -> SfyMachineGroup:
    return _group(
        ASSEMBLER,
        "Recipe_IronPlateReinforced_C",
        count,
        {"iron-plate": Fraction(1, 2), "screw": Fraction(1, 5)},
        {"reinforced-iron-plate": Fraction(1, 10)},
    )


def _batteries(count: int) -> SfyMachineGroup:
    return _group(
        MANUFACTURER,
        "Recipe_Alternate_ClassicBattery_C",
        count,
        {
            "sulfur": Fraction(1, 10),
            "alclad-aluminum-sheet": Fraction(7, 60),
            "plastic": Fraction(2, 15),
            "wire": Fraction(1, 5),
        },
        {"battery": Fraction(1, 15)},
    )


def _centres(low: float, high: float, side: float) -> list[float]:
    """Where to stand square tiles of ``side`` so they cover ``[low, high]``."""
    if high - low <= side:
        return [(low + high) / 2.0]
    count = math.ceil((high - low) / side)
    step = (high - low - side) / (count - 1)
    return [low + side / 2.0 + i * step for i in range(count)]


def _slab(row: RowGeometry, start_id: int) -> tuple[FoundationObj, ...]:
    """Foundation under every machine foot, which Task 8 will lay for real.

    ``slab.under_every_foot`` is a check on the whole build and a row is a
    fragment, so the slab is the test's own: tiles of the shipped foundation,
    standing at mid-thickness so their tops meet the machines' feet.
    """
    box = REGISTRY.buildables[FOUNDATION_CLASS].clearance[0]
    side = foundation_cm(REGISTRY)
    stand = (box.max[2] - box.min[2]) / 2.0
    x0, y0, x1, y1 = row.machine_footprint_cm
    ids = iter(range(start_id, start_id + 10_000))
    return tuple(
        FoundationObj(
            id=next(ids),
            class_name=FOUNDATION_CLASS,
            pose=Pose(cx, cy, stand, 0.0),
        )
        for cx in _centres(x0, x1, side)
        for cy in _centres(y0, y1, side)
    )


def _standing(row: RowGeometry) -> list[MachineObj | AttachmentObj]:
    return [*row.machines, *row.attachments]


def _placement(row: RowGeometry, mark: str) -> SfyPlacement:
    ids = [obj.id for obj in _standing(row)] + [belt.id for belt in row.belts]
    highest = max(ids, default=0)
    return SfyPlacement(
        designer=designer(mark, REGISTRY),
        machines=row.machines,
        attachments=row.attachments,
        belts=row.belts,
        links=row.links,
        foundations=_slab(row, highest + 1),
    )


def _row(
    group: SfyMachineGroup,
    mark: str = "mk3",
    *,
    flip: bool = False,
    belt_tiers: tuple[BeltTier, ...] = MK1_TO_MK5,
) -> RowGeometry:
    frame = designer(mark, REGISTRY)
    row = build_row(
        group,
        REGISTRY,
        designer=frame.model_copy(update={"dims": tuple(2 * d for d in frame.dims)}),
        belt_tiers=belt_tiers,
        flip=flip,
    )
    # A row's local origin is its first machine; centre its complete physical
    # band in the real designer, as the production row composer does.
    return _translate(row, 0.0, -round((row.y_min_cm + row.y_max_cm) / 2))


def _clean(row: RowGeometry, mark: str) -> None:
    """Every check but ``roundtrip`` passes, and every skip says why it stood aside.

    ``roundtrip`` is left out of this helper because it writes and reads a
    blueprint per call, which is seconds rather than milliseconds;
    :func:`test_a_row_survives_the_blueprint_round_trip` runs it once, on the
    row that exercises the most of the builder.
    """
    report = validate(_placement(row, mark), None, REGISTRY, only=_GEOMETRY)
    assert [f.message for f in report.errors] == []
    assert report.ok
    assert set(report.skipped) == SKIPPED
    assert set(report.checks_run) >= ANSWERABLE
    for skipped in report.skipped:
        assert any(
            finding.check == skipped and finding.severity is Severity.INFO
            for finding in report.findings
        ), f"{skipped} stood aside without saying why"


_GEOMETRY = tuple(cid for cid in CHECKS if cid != "roundtrip")


# --- the three rows --------------------------------------------------------


def test_a_constructor_row_of_three_is_a_placement_the_validator_passes() -> None:
    _clean(_row(_rods(3)), "mk3")


def test_an_assembler_row_of_two_is_a_placement_the_validator_passes() -> None:
    _clean(_row(_plates(2), "mk1"), "mk1")


def test_an_outer_feeder_descends_no_steeper_than_the_registry_allows() -> None:
    row = _row(_plates(2), "mk1")
    limit = REGISTRY.limits.belt_max_incline_deg
    assert limit is not None
    for belt in row.belts:
        for here, there in zip(belt.points, belt.points[1:], strict=False):
            run = math.dist(here[0][:2], there[0][:2])
            rise = abs(there[0][2] - here[0][2])
            if rise:
                assert math.degrees(math.atan2(rise, run)) <= limit


def test_an_outer_feeder_clears_the_chain_it_crosses_by_a_full_crossing_gap() -> None:
    row = _row(_plates(2), "mk1")
    gap = crossing_gap_cm(REGISTRY)
    inner, outer = sorted(row.chain_in, key=lambda end: -end.pose.y)
    crossing = [
        belt
        for belt in row.belts
        if belt.points[0][0][2] == outer.pose.z and belt.points[-1][0][2] < outer.pose.z
    ]
    assert crossing
    for belt in crossing:
        assert _height_at(belt, inner.pose.y) >= inner.pose.z + gap


def _height_at(belt: BeltRun, y: float) -> float:
    """How high the belt's centreline runs where it crosses ``y``."""
    for here, there in zip(belt.points, belt.points[1:], strict=False):
        low, high = sorted((here[0][1], there[0][1]))
        if low <= y <= high and high > low:
            share = (y - here[0][1]) / (there[0][1] - here[0][1])
            return here[0][2] + share * (there[0][2] - here[0][2])
    raise AssertionError(f"this belt never reaches y = {y}")


def test_a_manufacturer_row_of_one_lays_a_chain_for_every_input_item() -> None:
    row = _row(_batteries(1), "mk3")
    assert len(row.chain_in) == 4
    assert {end.item_id for end in row.chain_in} == set(_batteries(1).inputs_per_machine)
    _clean(row, "mk3")


def test_a_manufacturer_row_refuses_the_smallest_designer_as_too_deep() -> None:
    with pytest.raises(RowError) as caught:
        build_row(
            _batteries(1), REGISTRY, designer=designer("mk1", REGISTRY), belt_tiers=MK1_TO_MK5
        )
    message = str(caught.value)
    assert message.startswith("row too deep")
    assert f"{2 * designer('mk1', REGISTRY).half_cm:.0f}" in message


# --- belt tiers ------------------------------------------------------------


def test_a_row_lays_the_slowest_belt_that_carries_each_segment() -> None:
    data = load_vendored(Game.SFY)
    mk1 = data.belt_speed("conveyor-belt-mk1")
    # Two machines, each eating two thirds of a Mk1 belt: the head of the chain
    # carries both and needs Mk2, the feeders carry one each and do not.
    row = _row(_rods(2, rate=mk1 * Fraction(2, 3)))
    assert row.chain_in[0].belt_class == machine_class(LAB_MAP, "conveyor-belt-mk2")
    feeders = [belt for belt in row.belts if belt.items_per_second == mk1 * Fraction(2, 3)]
    assert feeders
    assert {belt.class_name for belt in feeders} == {machine_class(LAB_MAP, "conveyor-belt-mk1")}


def test_a_row_refuses_a_demand_no_belt_in_the_spec_carries() -> None:
    data = load_vendored(Game.SFY)
    too_fast = data.belt_speed("conveyor-belt-mk1") * 2
    with pytest.raises(RowError) as caught:
        _row(_rods(1, rate=too_fast), belt_tiers=MK1)
    message = str(caught.value)
    assert message.startswith("run exceeds the belt ceiling")
    assert "iron-ingot" in message
    assert str(too_fast) in message


# --- how short a belt this project will author ------------------------------


def test_the_shortest_belt_is_the_first_whole_centimetre_the_game_allows() -> None:
    """``belt.min_length`` compares STRICTLY, so the bound itself is too short.

    The registry states 100.02 cm, extracted from
    ``AFGConveyorBeltHologram::ValidateMinLength``; the first whole centimetre
    above it is 101, and it is deliberately not a multiple of the 100 cm hologram
    grid -- rounding a belt up to the grid would double it.
    """
    assert REGISTRY.limits.belt_min_length_cm == 100.02
    assert shortest_belt_cm(REGISTRY.limits) == 101.0


def _is_feeder(row: RowGeometry, belt: BeltRun) -> bool:
    """A belt that ends on a machine's input port, which is what a feeder is."""
    inputs = {
        (machine.id, port.name)
        for machine in row.machines
        for port in REGISTRY.buildables[machine.class_name].ports
        if port.kind == "belt" and port.direction == "input"
    }
    return any(link.a[0] == belt.id and link.b in inputs for link in row.links)


# --- the frame Task 8 composes ---------------------------------------------


def test_a_flipped_row_mirrors_x_and_takes_the_other_side_port() -> None:
    row = _row(_plates(2), "mk1", flip=True)
    assert [machine.pose.x for machine in row.machines] == [
        0.0,
        -machine_pitch_cm(REGISTRY.buildables[ASSEMBLER], REGISTRY.limits),
    ]
    assert {a.pose.yaw_deg for a in row.attachments} == {180.0}
    _clean(row, "mk1")


def test_every_feeder_leaves_its_port_along_the_port_facing() -> None:
    row = _row(_batteries(1), "mk3")
    standing = {placed.id: placed for placed in _standing(row)}
    runs = {belt.id: belt for belt in row.belts}
    for link in row.links:
        upstream = standing.get(link.a[0])
        if upstream is None:
            continue  # a belt handing off to the next port: concat's business
        buildable = REGISTRY.buildables[upstream.class_name]
        port = next(p for p in buildable.ports if p.name == link.a[1])
        facing = port_forward(upstream.pose.transform(), port)
        belt = runs[link.b[0]]
        leave = belt.points[0][2]
        scale = math.dist((0.0, 0.0, 0.0), leave)
        cosine = sum(leave[i] * facing[i] for i in range(3)) / scale
        assert math.acos(min(1.0, cosine)) < 0.01


def test_a_row_names_its_two_ends_for_the_next_stage() -> None:
    group = _plates(2)
    row = _row(group, "mk1")
    assert isinstance(row.chain_out, ChainEnd)
    assert row.chain_out.items_per_second == group.row_outputs["reinforced-iron-plate"]
    assert row.chain_out.port == "Output1"
    rates = {end.item_id: end.items_per_second for end in row.chain_in}
    assert rates == group.row_inputs
    assert {end.port for end in row.chain_in} == {"Input1"}


def test_a_row_refuses_a_recipe_with_more_inputs_than_the_machine_has_ports() -> None:
    crowded = _group(
        CONSTRUCTOR,
        "Recipe_IronRod_C",
        1,
        {"iron-ingot": Fraction(1, 4), "screw": Fraction(1, 4)},
        {"iron-rod": Fraction(1, 4)},
    )
    with pytest.raises(RowError) as caught:
        _row(crowded)
    assert str(caught.value).startswith("more input items than")


def test_a_row_survives_the_blueprint_round_trip() -> None:
    """A row comes back out of a blueprint as the row that went in.

    It did not, when this row builder was written: ``links`` came back as the
    same pairs in a different sequence, because a decoded placement lists them
    in the order the connection components stand in the file and nothing an
    author writes chooses that order.  Task 8 fixed it where it belonged, in the
    model: ``SfyPlacement`` sorts ``links`` into a canonical order at
    construction, so both sides of the round trip are in it.
    """
    row = _row(_rods(3))
    placement = _placement(row, "mk3")
    library = TemplateLibrary.from_fixtures(fixture_paths())
    newest = max(
        (read_header(Reader(path.read_bytes())) for path in fixture_paths()),
        key=lambda header: (header.save_version, header.build_version),
    )
    again = decode(
        emit(
            placement,
            REGISTRY,
            library,
            LAB_MAP,
            build_version=newest.build_version,
            version_data=newest.version_data,
        ),
        REGISTRY,
    )
    assert again.machines == placement.machines
    assert again.attachments == placement.attachments
    assert again.belts == placement.belts
    assert again.foundations == placement.foundations
    assert again.links == placement.links
    assert again == placement


def test_the_row_band_contains_the_actual_attachment_bodies() -> None:
    """Packing a row by the smaller native soft box would clip its neighbour."""
    row = _row(_rods(3))
    for attachment in row.attachments:
        for box in attachment_boxes(attachment, REGISTRY):
            for corner in box.corners():
                assert row.x_min_cm <= corner[0] <= row.x_max_cm
                assert row.y_min_cm <= corner[1] <= row.y_max_cm


def test_a_chain_carries_the_last_machines_own_share_and_not_a_full_one() -> None:
    """The odd machine at the end of a row runs at ``last_clock`` and eats less.

    So the feeder into it carries ``rate * last_clock / clock`` and the chain
    belt upstream of it carries what is still to come -- not ``rate * (count - i)
    / count``, which is the same arithmetic only while every machine is at the
    same clock.  ``flow.capacity`` holds the placement to exactly this, machine
    by machine, and a tier chosen against a full-clock share would be a tier
    chosen for a belt that never carries it.
    """
    group = _rods(3).model_copy(update={"last_clock": Fraction(1, 2)})
    row = _row(group)
    rate = group.inputs_per_machine["iron-ingot"]
    shares = [rate, rate, rate / 2]
    belts = {belt.id: belt for belt in row.belts}
    machines = {machine.id: machine for machine in row.machines}
    feeders = sorted(
        (
            belts[link.a[0]]
            for link in row.links
            if link.b[0] in machines
            and link.a[0] in belts
            and belts[link.a[0]].item_id == "iron-ingot"
        ),
        key=lambda belt: belt.start[0],
    )
    assert [belt.items_per_second for belt in feeders] == shares
    fed = {belt.id for belt in feeders}
    chain = sorted(
        (belt for belt in row.belts if belt.item_id == "iron-ingot" and belt.id not in fed),
        key=lambda belt: belt.start[0],
    )
    assert [belt.items_per_second for belt in chain] == [sum(shares[1:]), shares[2]]
    assert row.chain_in[0].items_per_second == sum(shares, Fraction(0))
