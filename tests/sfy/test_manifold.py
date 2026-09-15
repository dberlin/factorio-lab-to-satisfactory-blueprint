"""One manifold row, judged by the Task 6 validator rather than by a number here.

Every distance a row stands on comes from ``registry.json``, from
``hologram_rules.json`` or from FactorioLab's own dataset, and these tests take
them the same way: the machine pitch is recomputed from the clearance boxes, the
chain heights from the belt clearance rule's own box, the belt speeds from the
vendored dataset.  Nothing here is measured off a blueprint and nothing is typed
in from memory of the game -- the only literals are counts (three machines, two
chains) and the names of game classes.

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
from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.header import read_header
from flab2bp.sfy.labmap import load_lab_map, machine_class
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.manifold import (
    SPLITTER_CLASS,
    ChainEnd,
    RowError,
    RowGeometry,
    build_row,
    crossing_gap_cm,
    grid_ceil,
    hard_footprint_cm,
    machine_pitch_cm,
    slab_top_cm,
)
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    MachineObj,
    Pose,
    SfyPlacement,
)
from flab2bp.sfy.layout.splines import spline_length
from flab2bp.sfy.layout.validate import CHECKS, Severity, validate
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import FOUNDATION_CLASS, Designer, SfyMachineGroup, designer, foundation_cm
from flab2bp.sfy.templates import TemplateLibrary
from flab2bp.spec import BeltTier
from tests.sfy.conftest import fixture_paths

REGISTRY: Registry = load_registry()
LAB_MAP = load_lab_map()
_GRID = REGISTRY.limits.hologram_grid_cm
assert _GRID is not None, "the registry states the hologram grid; these tests need it"
GRID: float = _GRID

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
    return build_row(
        group,
        REGISTRY,
        designer=designer(mark, REGISTRY),
        belt_tiers=belt_tiers,
        flip=flip,
    )


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


def test_a_constructor_row_stands_its_machines_one_pitch_apart() -> None:
    row = _row(_rods(3))
    pitch = machine_pitch_cm(REGISTRY.buildables[CONSTRUCTOR], REGISTRY.limits)
    x0, _, x1, _ = hard_footprint_cm(REGISTRY.buildables[CONSTRUCTOR])
    assert pitch == grid_ceil(x1 - x0 + GRID, GRID)
    assert [machine.pose.x for machine in row.machines] == [0.0, pitch, 2 * pitch]
    assert {machine.pose.z for machine in row.machines} == {slab_top_cm(REGISTRY)}
    assert {machine.pose.yaw_deg for machine in row.machines} == {0.0}


def test_a_constructor_row_lays_one_splitter_and_one_merger_per_machine() -> None:
    row = _row(_rods(3))
    splitters = [a for a in row.attachments if "Splitter" in a.class_name]
    mergers = [a for a in row.attachments if "Merger" in a.class_name]
    assert len(splitters) == 3
    assert len(mergers) == 3
    # two belts along each chain, three feeders in and three feeders out
    assert len(row.belts) == 2 + 3 + 2 + 3


def test_a_chain_belt_spans_one_pitch_less_the_two_attachment_ports() -> None:
    row = _row(_rods(3))
    pitch = machine_pitch_cm(REGISTRY.buildables[CONSTRUCTOR], REGISTRY.limits)
    splitter = REGISTRY.buildables["Build_ConveyorAttachmentSplitter_C"]
    reach = max(abs(p.translation[0]) for p in splitter.ports)
    along = [
        spline_length(belt.points)
        for belt in row.belts
        if abs(belt.points[0][0][1] - belt.points[-1][0][1]) < 1.0
    ]
    assert along and all(length == pytest.approx(pitch - 2 * reach) for length in along)


def test_an_assembler_row_of_two_is_a_placement_the_validator_passes() -> None:
    _clean(_row(_plates(2), "mk1"), "mk1")


def test_an_assembler_row_stacks_its_second_chain_one_crossing_gap_higher() -> None:
    row = _row(_plates(2), "mk1")
    gap = crossing_gap_cm(REGISTRY)
    heights = sorted({end.pose.z for end in row.chain_in})
    assert heights == [row.belt_z_cm, row.belt_z_cm + gap]
    assert gap == grid_ceil(4 * 15.0, GRID)


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
    row = _row(_batteries(1), "mk2")
    assert len(row.chain_in) == 4
    assert {end.item_id for end in row.chain_in} == set(_batteries(1).inputs_per_machine)
    _clean(row, "mk2")


def test_a_manufacturer_row_refuses_the_smallest_designer_as_too_deep() -> None:
    with pytest.raises(RowError) as caught:
        _row(_batteries(1), "mk1")
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


# --- the frame Task 8 composes ---------------------------------------------


def test_every_object_stands_on_the_hologram_grid_or_on_a_registry_port() -> None:
    grid = GRID
    row = _row(_batteries(1), "mk2")
    machine_port_y = {
        round(port.translation[1], 6)
        for port in REGISTRY.buildables[MANUFACTURER].ports
        if port.kind == "belt"
    }
    for placed in _standing(row):
        assert placed.pose.x % grid == 0
        assert placed.pose.z % grid == 0
        offsets = {round((placed.pose.y - y) % grid, 3) for y in machine_port_y}
        assert placed.pose.y % grid == 0 or 0.0 in offsets
    for belt in row.belts:
        for location, _, _ in belt.points:
            assert location[0] % grid == 0 or _on_a_port(row, location)


def _on_a_port(row: RowGeometry, location: tuple[float, float, float]) -> bool:
    for placed in _standing(row):
        buildable = REGISTRY.buildables[placed.class_name]
        transform = placed.pose.transform()
        for port in buildable.ports:
            if math.dist(world_port(transform, port), location) <= 1.0:
                return True
    return False


def test_a_flipped_row_mirrors_x_and_takes_the_other_side_port() -> None:
    row = _row(_plates(2), "mk1", flip=True)
    assert [machine.pose.x for machine in row.machines] == [
        0.0,
        -machine_pitch_cm(REGISTRY.buildables[ASSEMBLER], REGISTRY.limits),
    ]
    assert {a.pose.yaw_deg for a in row.attachments} == {180.0}
    _clean(row, "mk1")


def test_every_feeder_leaves_its_port_along_the_port_facing() -> None:
    row = _row(_batteries(1), "mk2")
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


def test_a_row_reports_the_band_it_occupies() -> None:
    row = _row(_rods(3))
    assert row.depth_cm == row.y_max_cm - row.y_min_cm
    assert row.width_cm == row.x_max_cm - row.x_min_cm
    assert row.depth_cm > 0 and row.width_cm > 0
    fits: Designer = designer("mk3", REGISTRY)
    assert row.depth_cm <= 2 * fits.half_cm


def test_the_band_covers_the_splitters_soft_box_and_not_only_the_hard_ones() -> None:
    """A row's band is every clearance box in it, soft ones included.

    It has to be.  A splitter's only box is SOFT -- the game lets a belt and a
    machine share it -- and it reaches 200 cm past the attachment it belongs to,
    further out than any belt's own clearance in the row.  The band is what
    Task 8 stands a corridor clear of, so a band measured over the hard boxes
    alone would put a corridor column through the splitters.
    """
    row = _row(_rods(3))
    splitter = REGISTRY.buildables[SPLITTER_CLASS]
    box = splitter.clearance[0]
    assert box.soft, "this test is about a soft box; the registry says this one is not"
    chain = min(a.pose.y for a in row.attachments if a.class_name == SPLITTER_CLASS)
    reach = chain + box.min[1] + box.translation[1]
    assert row.y_min_cm == pytest.approx(reach)
    # And the hard boxes alone would not reach it: the machines stop at -500 and
    # the chain belt's own clearance at -679, so dropping the soft boxes out of
    # the band would move this edge and this assertion would fail.
    machines = (
        min(m.pose.y for m in row.machines) + hard_footprint_cm(REGISTRY.buildables[CONSTRUCTOR])[1]
    )
    assert reach < machines


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
