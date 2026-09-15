"""The placement model: where things stand, at the width the file stores it."""

from __future__ import annotations

from fractions import Fraction
from functools import cache
from unittest.mock import patch

import pytest

from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.model import (
    BeltRun,
    FoundationObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    SfyPlacement,
    belt_ends,
    stored_float,
)
from flab2bp.sfy.layout.splines import straight, yaw_quaternion
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.spec import designer
from flab2bp.sfy.templates import TemplateLibrary
from tests.sfy.conftest import fixture_paths

CONSTRUCTOR = "Build_ConstructorMk1_C"
BELT = "Build_ConveyorBeltMk1_C"
FOUNDATION = "Build_Foundation_8x1_01_C"
POLE = "Build_PowerPoleMk1_C"
IRON_PLATE = "Recipe_IronPlate_C"


@cache
def _library() -> TemplateLibrary:
    """The fixture library, built once: scanning 49 blueprints costs seconds."""
    return TemplateLibrary.from_fixtures(fixture_paths())


def _machine_placement(yaw: float) -> SfyPlacement:
    registry = load_registry()
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(4, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, yaw), IRON_PLATE),),
    )


def test_a_pose_becomes_the_transform_the_object_table_writes() -> None:
    pose = Pose(100.0, -200.0, 50.0, 90.0)
    transform = pose.transform()
    assert transform.rotation == yaw_quaternion(90.0)
    assert transform.translation == (100.0, -200.0, 50.0)
    assert transform.scale == (1.0, 1.0, 1.0)


def test_a_pose_states_its_position_at_the_width_the_file_stores_it() -> None:
    """An actor's transform is ten 32-bit floats, so a pose carries no more."""
    pose = Pose(100.000016237143, 0.0, 0.0, 0.0)
    assert pose.x == 100.00001525878906
    assert Pose(*pose.location, 0.0) == pose


def test_a_belt_runs_local_points_start_at_the_actor_origin() -> None:
    run = BeltRun(1, BELT, straight((0.0, 300.0, 200.0), (0.0, 1.0, 0.0), 400.0))
    assert run.start == (0.0, 300.0, 200.0)
    assert run.end == (0.0, 700.0, 200.0)
    assert run.pose == Pose(0.0, 300.0, 200.0, 0.0)
    local = run.local_points()
    assert local[0][0] == (0.0, 0.0, 0.0)
    assert local[-1][0] == (0.0, 400.0, 0.0)
    assert [point[1:] for point in local] == [point[1:] for point in run.points]


def test_a_belt_run_needs_two_points_to_be_a_run() -> None:
    with pytest.raises(ValueError, match="two points"):
        BeltRun(1, BELT, (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0)),))


def test_the_ends_of_a_belt_are_the_ones_the_registry_names() -> None:
    """Which end is the entry is the registry's ``flow``, never a typed name."""
    registry = load_registry()
    flow = registry.buildables[BELT].flow
    assert flow is not None
    assert belt_ends(registry, BELT) == (flow.entry, flow.exit)
    with pytest.raises(KeyError, match="flow"):
        belt_ends(registry, CONSTRUCTOR)


def test_by_id_finds_every_kind_of_object_and_says_so_when_it_cannot() -> None:
    registry = load_registry()
    machine = MachineObj(1, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE)
    belt = BeltRun(2, BELT, straight((0.0, 300.0, 200.0), (0.0, 1.0, 0.0), 400.0))
    pole = PoleObj(3, POLE, Pose(700.0, 0.0, 100.0, 0.0))
    slab = FoundationObj(4, FOUNDATION, Pose(0.0, 0.0, 50.0, 0.0))
    placement = SfyPlacement(
        designer=designer("mk1", registry),
        machines=(machine,),
        belts=(belt,),
        poles=(pole,),
        foundations=(slab,),
    )
    assert [placement.by_id(n) for n in (1, 2, 3, 4)] == [machine, belt, pole, slab]
    with pytest.raises(KeyError, match="99"):
        placement.by_id(99)


def test_a_link_names_a_port_on_each_side() -> None:
    link = Link((1, "Output0"), (2, "ConveyorAny0"))
    assert link.a == (1, "Output0")
    assert link.b == (2, "ConveyorAny0")


@pytest.mark.parametrize("yaw", [0.0, 90.0, 180.0, -90.0])
def test_a_machines_pose_comes_back_out_of_the_file_it_went_into(yaw: float) -> None:
    placement = _machine_placement(yaw)
    built = emit(
        placement,
        load_registry(),
        _library(),
        load_lab_map(),
        # The build version and the engine-version block ride in the header and
        # nothing here reads them back; test_emit.py holds them to a fixture's.
        build_version=0,
        version_data=None,
    )
    back = decode(built, load_registry())
    assert back.machines[0].pose == placement.machines[0].pose
    assert back.machines[0].pose.transform() == placement.machines[0].pose.transform()
    assert back == placement


def test_what_the_file_cannot_carry_is_left_out_of_what_equality_compares() -> None:
    """A belt's rate has no property in the file, so ``decode`` cannot hand it
    back and equality never claimed it could.  A somersloop count is not like
    that: the production boost that encodes it is exact at the stored width, so
    it IS compared."""
    plain = MachineObj(1, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE)
    sloops = MachineObj(1, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE, Fraction(1), 2)
    assert sloops != plain
    carried = BeltRun(
        2, BELT, straight((0.0, 0.0, 200.0), (0.0, 1.0, 0.0), 400.0), "iron-plate", Fraction(2)
    )
    empty = BeltRun(2, BELT, straight((0.0, 0.0, 200.0), (0.0, 1.0, 0.0), 400.0))
    assert carried == empty
    assert carried.item_id != empty.item_id


def test_a_machine_at_the_wrong_clock_is_not_equal_to_one_at_the_right_clock() -> None:
    """The file holds the potential, so equality must ask about it.

    It asks at the width the file holds -- one ``f32`` -- rather than about the
    exact :class:`~fractions.Fraction`, which is what lets ``moc=133``'s 133/100
    survive a round trip while a machine left at some fixture's inherited
    overclock does not.
    """
    fast = MachineObj(1, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE, Fraction(5, 2), 0)
    plain = MachineObj(1, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE)
    assert fast != plain
    assert fast.stored_clock == 2.5 and plain.stored_clock == 1.0

    inexact = MachineObj(1, CONSTRUCTOR, Pose(0.0, 0.0, 100.0, 0.0), IRON_PLATE, Fraction(133, 100))
    read_back = MachineObj(
        1,
        CONSTRUCTOR,
        Pose(0.0, 0.0, 100.0, 0.0),
        IRON_PLATE,
        Fraction(stored_float(1.33)),
    )
    assert inexact.clock != read_back.clock, "the f32 is not 133/100"
    assert inexact == read_back, "but it is the same number in the file"


def test_by_id_answers_from_an_index_rather_than_walking_every_object() -> None:
    """A validator asks this per link and per wire; a scan per question is quadratic."""
    registry = load_registry()
    poles = tuple(PoleObj(i, POLE, Pose(float(i) * 100.0, 0.0, 0.0, 0.0)) for i in range(50))
    placement = SfyPlacement(designer=designer("mk1", registry), poles=poles)
    assert placement.by_id(37) is poles[37]
    assert placement.by_id(0) is poles[0]
    with pytest.raises(KeyError, match="no object with id 99"):
        placement.by_id(99)

    calls = 0
    original = SfyPlacement.objects.fget
    assert original is not None

    def counted(self: SfyPlacement) -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        return original(self)

    with patch.object(SfyPlacement, "objects", property(counted)):
        for _ in range(10):
            placement.by_id(37)
    assert calls == 0, "the index was built on the first lookup and is kept"


def test_links_are_held_in_one_canonical_order_whatever_order_they_were_written_in() -> None:
    """Two placements that differ only in the sequence of ``links`` are one placement.

    A blueprint records a connection on both actors and records no sequence at
    all, so the order ``decode`` hands back is the order the connection
    components stand in the file.  Comparing the tuple as written would make
    ``decode(emit(p)) == p`` false for every build with more than one belt, over
    a difference the file does not hold.
    """
    registry = load_registry()
    entry, exit_end = belt_ends(registry, BELT)
    first = Link((1, "Output0"), (5, entry))
    second = Link((5, exit_end), (2, "Input0"))
    written = SfyPlacement(designer=designer("mk1", registry), links=(first, second))
    backwards = SfyPlacement(designer=designer("mk1", registry), links=(second, first))
    assert written.links == backwards.links
    assert written == backwards
    assert set(written.links) == {first, second}


def test_a_boundary_end_is_outside_equality_because_no_file_property_carries_it() -> None:
    """The flag says an open end is intended; a blueprint has nowhere to say so.

    ``decode`` hands a belt back unflagged, so a flag inside equality would make
    the round trip a statement about this dataclass rather than about the file.
    """
    points = straight((0.0, -1600.0, 200.0), (0.0, 1.0, 0.0), 400.0)
    flagged = BeltRun(2, BELT, points, "iron-ore", Fraction(1), boundary_start=True)
    plain = BeltRun(2, BELT, points, "iron-ore", Fraction(1))
    assert flagged == plain
    assert flagged.boundary_start and not flagged.boundary_end
    assert not plain.boundary_start
