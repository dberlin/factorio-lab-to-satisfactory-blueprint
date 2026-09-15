"""Poles and wires: where they stand, what they reach, and what the file holds.

Every number a pole or a wire stands on is the registry's -- the pole's port and
its ``max_connections``, the wire's ``wire_max_cm``, the clearance boxes both are
placed against -- and the two things that are ours (which pole class, and where
in a row the pole line stands) say so where they are made.  The wire's trailer
layout is Task 9a's reading of ``AFGBuildableWire::Serialize``.

Each layout here goes in front of the validator with ``power.wires`` RUNNING,
which is the point of the task: before it, that check stood aside on every
placement this project could author.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.flow import load_flow
from flab2bp.lab.url import Game, parse_url
from flab2bp.layout.base import NoValidLayout
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import Blueprint, read_sbp, read_sbp_file, write_sbp
from flab2bp.sfy.geometry import world_port
from flab2bp.sfy.header import BlueprintHeader, read_header
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout import power
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.manifold import RowGeometry, build_row
from flab2bp.sfy.layout.model import SfyPlacement
from flab2bp.sfy.layout.strategy import ManifoldRows
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.rates import spec_from_flow
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.sfy.templates import TemplateLibrary
from tests.sfy.conftest import fixture_paths

FLOWS = Path(__file__).resolve().parents[1] / "fixtures" / "sfy_flows"

#: What a clean report may stand aside on once wires are in it: the two partial
#: rules, and nothing else.  ``power.wires`` is deliberately NOT here.
ALWAYS_SKIPPED = {"geom.hard_clearance", "belt.capsule"}


@cache
def _registry() -> Registry:
    return load_registry()


@cache
def _library() -> TemplateLibrary:
    return TemplateLibrary.from_fixtures(fixture_paths())


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


def _constructor_group(count: int) -> SfyMachineGroup:
    """FactorioLab's own Constructor group out of ``iron-plate-60``, ``count`` of them.

    Only the machine count is this file's: every rate, clock and class comes
    through ``spec_from_flow``.
    """
    group = next(
        g for g in _spec("iron-plate-60").groups if g.machine_class == "Build_ConstructorMk1_C"
    )
    return group.model_copy(update={"count": count})


def _frame(count: int) -> Designer:
    """The designer a row of ``count`` machines is measured in.

    A row of six Constructors is 5300 cm wide and no designer the game ships is,
    so the wide rows here are built in an enlarged frame -- the same trick the
    strategy uses to measure a row before it moves it -- with room for a row that
    starts at the origin and runs one way.  Which frame a row is measured in
    changes nothing about where its poles stand relative to it.
    """
    mark = designer("mk3", _registry())
    if count <= 3:
        return mark
    return mark.model_copy(update={"dims": tuple(4 * d for d in mark.dims)})


def _constructor_row(count: int = 3) -> RowGeometry:
    return build_row(
        _constructor_group(count),
        _registry(),
        designer=_frame(count),
        belt_tiers=_spec("iron-plate-60").belt_tiers,
    )


def _plan(row: RowGeometry, registry: Registry | None = None) -> power.PowerPlan:
    return power.place(
        (power.PowerRow(row.machines, row.attachments),),
        _registry() if registry is None else registry,
        ids=itertools.count(10_000),
        designer=_frame(len(row.machines)),
    )


def test_a_row_of_three_constructors_gets_one_power_pole_and_a_wire_to_every_machine() -> None:
    row = _constructor_row()
    plan = _plan(row)
    assert [pole.class_name for pole in plan.poles] == [power.POLE_CLASS]
    machine_wires = [
        wire
        for wire in plan.wires
        if any(side[0] == machine.id for machine in row.machines for side in (wire.link.b,))
    ]
    assert len(machine_wires) == len(row.machines)
    assert all(wire.class_name == power.WIRE_CLASS for wire in plan.wires)


def test_one_pole_carries_five_machines_because_the_asset_gives_it_seven_links() -> None:
    port = power.power_port(_registry(), power.POLE_CLASS)
    assert port.max_connections == 7
    assert port.max_connections_source == "asset"
    assert power.machine_budget(_registry(), power.POLE_CLASS) == 5
    plan = _plan(_constructor_row(count=6))
    assert len(plan.poles) == 2


def test_the_pole_stands_on_the_grid_its_own_hologram_snaps_to() -> None:
    grid = power.pole_grid_cm(_registry(), power.POLE_CLASS)
    row = _constructor_row()
    plan = _plan(row)
    for pole in plan.poles:
        assert pole.pose.x % grid == pytest.approx(0.0, abs=1e-6)
        assert pole.pose.y % grid == pytest.approx(0.0, abs=1e-6)


def test_a_poles_soft_box_meets_no_hard_box_in_the_row() -> None:
    row = _constructor_row()
    plan = _plan(row)
    hard = [
        power.box_bounds(box, obj.pose)
        for obj in (*row.machines, *row.attachments)
        for box in _registry().buildables[obj.class_name].clearance
        if not box.soft
    ]
    for pole in plan.poles:
        for box in _registry().buildables[pole.class_name].clearance:
            low, high = power.box_bounds(box, pole.pose)
            for other_low, other_high in hard:
                apart = any(
                    high[i] <= other_low[i] + 1e-6 or low[i] >= other_high[i] - 1e-6
                    for i in range(3)
                )
                assert apart, f"the pole's box laps {other_low}..{other_high}"


def test_every_wire_is_inside_the_length_the_registry_gives_its_class() -> None:
    row = _constructor_row()
    plan = _plan(row)
    limit = _registry().limits.wire_max_cm[power.WIRE_CLASS]
    placed = {obj.id: obj for obj in (*row.machines, *plan.poles)}
    for wire in plan.wires:
        ends = [
            world_port(
                placed[side[0]].pose.transform(),
                next(
                    port
                    for port in _registry().buildables[placed[side[0]].class_name].ports
                    if port.name == side[1]
                ),
            )
            for side in (wire.link.a, wire.link.b)
        ]
        assert math.dist(*ends) <= limit


def test_a_wire_past_the_limit_is_refused_rather_than_authored() -> None:
    row = _constructor_row()
    short = replace(
        _registry(),
        limits=replace(_registry().limits, wire_max_cm={power.WIRE_CLASS: 100.0}),
    )
    with pytest.raises(power.PowerError) as caught:
        _plan(row, short)
    assert caught.value.cause == "wire"


def test_the_row_to_row_chain_puts_every_pole_and_machine_on_one_graph() -> None:
    placement = _laid_out()
    joined: dict[int, set[int]] = {}
    for wire in placement.wires:
        joined.setdefault(wire.link.a[0], set()).add(wire.link.b[0])
        joined.setdefault(wire.link.b[0], set()).add(wire.link.a[0])
    start = placement.poles[0].id
    seen = {start}
    queue = [start]
    while queue:
        here = queue.pop()
        for there in joined.get(here, ()):
            if there not in seen:
                seen.add(there)
                queue.append(there)
    assert {pole.id for pole in placement.poles} <= seen
    assert {machine.id for machine in placement.machines} <= seen


def test_no_connection_carries_more_wires_than_the_asset_says_it_takes() -> None:
    placement = _laid_out()
    counts: dict[tuple[int, str], int] = {}
    for wire in placement.wires:
        for side in (wire.link.a, wire.link.b):
            counts[side] = counts.get(side, 0) + 1
    for (oid, name), count in counts.items():
        obj = placement.by_id(oid)
        port = next(p for p in _registry().buildables[obj.class_name].ports if p.name == name)
        assert port.max_connections is not None
        assert count <= port.max_connections


@cache
def _laid_out() -> SfyPlacement:
    return ManifoldRows().lay_out(_spec("iron-plate-60"), designer("mk3", _registry()))


def test_iron_plate_60_in_an_mk3_validates_clean_with_power_wires_run() -> None:
    placement = _laid_out()
    report = validate(placement, _spec("iron-plate-60"), _registry(), library=_library())
    assert [f.message for f in report.errors] == []
    assert "power.wires" in report.checks_run
    assert set(report.skipped) == ALWAYS_SKIPPED


def test_the_strategy_says_where_it_stood_each_rows_pole_line() -> None:
    placement = _laid_out()
    lines = [line for line in placement.description.splitlines() if line.startswith("power:")]
    assert len(lines) == 2
    assert all(power.POLE_CLASS in line and "merger chain" in line for line in lines)


@cache
def _newest_fixture_header() -> BlueprintHeader:
    """The newest corpus header, whose build version an authored file claims."""
    return max(
        (read_header(Reader(path.read_bytes())) for path in fixture_paths()),
        key=lambda h: (h.save_version, h.build_version),
    )


def _emit(placement: SfyPlacement) -> Blueprint:
    newest = _newest_fixture_header()
    return emit(
        placement,
        _registry(),
        _library(),
        load_lab_map(),
        build_version=newest.build_version,
        version_data=newest.version_data,
    )


def test_a_placement_with_wires_survives_the_blueprint_round_trip() -> None:
    placement = _laid_out()
    assert decode(_emit(placement), _registry()) == placement


def test_a_build_with_wires_comes_back_out_of_the_bytes_it_was_written_to() -> None:
    """The format guarantee, through the file rather than through the object tree."""
    placement = _laid_out()
    again = read_sbp(write_sbp(_emit(placement)))
    assert decode(again, _registry()) == placement
    assert sum(1 for h, _ in again.objects if h.class_name == power.WIRE_CLASS) == len(
        placement.wires
    )


def test_an_authored_wire_carries_what_the_games_own_wires_carry() -> None:
    """The property list, the trailer and the transform, against three fixtures.

    ``mWireInstances`` is authored EMPTY: the game's own loader throws the
    meshes away and rebuilds them from the two connections
    (``AFGBuildableWire::Serialize`` calls ``DestroyWireInstances`` and then
    ``CreateWireInstancesBetweenConnections`` on the loading side), so a copy of
    some fixture's meshes would be bytes the game discards.
    """
    placement = _laid_out()
    blueprint = _emit(placement)
    wires = [(h, d) for h, d in blueprint.objects if h.class_name == power.WIRE_CLASS]
    assert len(wires) == len(placement.wires)
    fixture = _fixture_wire_properties()
    for _header, data in wires:
        assert tuple(p.tag.name for p in data.properties) in fixture
        instances = next(p.value for p in data.properties if p.tag.name == "mWireInstances")
        assert instances.items == ()
        assert len(data.trailer.connections) == 2


@cache
def _fixture_wire_properties() -> frozenset[tuple[str, ...]]:
    """Every property list the game's own power lines carry, from the corpus."""
    out = set()
    for path in fixture_paths():
        blueprint = read_sbp_file(path)
        for header, data in blueprint.objects:
            if header.class_name == power.WIRE_CLASS:
                out.add(tuple(p.tag.name for p in data.properties))
    return frozenset(out)


def test_both_connection_components_list_the_wire_the_way_the_game_does() -> None:
    """``mWires`` on the circuit connection, which the corpus carries on all 1016 ends."""
    placement = _laid_out()
    blueprint = _emit(placement)
    index = {h.path: (h, d) for h, d in blueprint.objects}
    for header, data in blueprint.objects:
        if header.class_name != power.WIRE_CLASS:
            continue
        for ref in data.trailer.connections:
            _, component = index[ref.path]
            listed = next(p.value for p in component.properties if p.tag.name == "mWires")
            assert header.path in [item.ref.path for item in listed.items]


def test_a_two_row_build_refuses_rather_than_authoring_a_wire_it_cannot_reach() -> None:
    spec = _spec("iron-plate-60")
    short = replace(
        _registry(),
        limits=replace(_registry().limits, wire_max_cm={power.WIRE_CLASS: 50.0}),
    )
    with pytest.raises(NoValidLayout) as caught:
        ManifoldRows().lay_out(spec, designer("mk3", short), registry=short)
    assert caught.value.reason == "wire exceeds the maximum length"


def test_a_pole_never_stands_outside_the_designer_the_row_is_in() -> None:
    """The end candidates are half a pitch OUTSIDE the machine line.

    A row whose machines reach the designer wall would put one of them past it,
    and ``geom.bounds`` would refuse the build -- so they are clamped to the
    floor the same way the band across ``Y`` is, and a midpoint between two
    machines is taken instead.
    """
    registry = _registry()
    row = _constructor_row(3)
    frame = designer("mk3", registry)
    # Stand the row hard against the +X wall: its last machine's own box ends
    # exactly on it, so the candidate half a pitch further out is off the floor.
    hard = frame.half_cm - 400.0
    shift = hard - max(machine.pose.x for machine in row.machines)
    moved = power.PowerRow(
        tuple(
            replace(machine, pose=replace(machine.pose, x=machine.pose.x + shift))
            for machine in row.machines
        ),
        tuple(
            replace(obj, pose=replace(obj.pose, x=obj.pose.x + shift)) for obj in row.attachments
        ),
    )
    plan = power.place((moved,), registry, ids=itertools.count(10_000), designer=frame)
    _inside(plan, frame)

    # And the clamp is applied to the SNAPPED candidate, not the midpoint that
    # suggested it: three Constructors at -1900, -1000 and -100 put the outer
    # candidate at -2350, which is inside an mk3's floor and rounds to -2400,
    # which is not -- the pole's box would reach -2440.
    line = tuple(
        replace(machine, pose=replace(machine.pose, x=x))
        for machine, x in zip(row.machines, (-1900.0, -1000.0, -100.0), strict=True)
    )
    edged = power.PowerRow(
        line,
        tuple(
            replace(obj, pose=replace(obj.pose, x=obj.pose.x - 1900.0)) for obj in row.attachments
        ),
    )
    plan = power.place(
        (edged,),
        registry,
        ids=itertools.count(10_000),
        designer=frame,
    )
    assert [pole.pose.x for pole in plan.poles] != [-2400.0]
    _inside(plan, frame)


def _inside(plan: power.PowerPlan, frame: Designer) -> None:
    pole = _registry().buildables[power.POLE_CLASS]
    assert plan.poles
    for placed in plan.poles:
        for box in pole.clearance:
            low, high = power.box_bounds(box, placed.pose)
            assert -frame.half_cm <= low[0] and high[0] <= frame.half_cm


def test_a_row_with_no_machines_in_it_refuses_rather_than_raising_from_max() -> None:
    """A build with nothing in it at all gets no power and no complaint; a build
    with an EMPTY row beside a real one used to raise a bare ``ValueError`` out
    of ``max()``, which is not a cause anybody can act on."""
    empty = power.PowerRow((), ())
    assert (
        power.place(
            (empty,),
            _registry(),
            ids=itertools.count(10_000),
            designer=designer("mk3", _registry()),
        ).poles
        == ()
    )
    with pytest.raises(power.PowerError) as caught:
        row = _constructor_row(3)
        power.place(
            (power.PowerRow(row.machines, row.attachments), empty),
            _registry(),
            ids=itertools.count(10_000),
            designer=designer("mk3", _registry()),
        )
    assert caught.value.cause == "room"


def test_a_gap_in_the_game_data_is_not_reported_as_a_pole_that_will_not_fit() -> None:
    """Two causes that were wearing other causes' names: a class the registry
    has not got, and a limit it does not state."""
    registry = _registry()
    with pytest.raises(power.PowerError) as missing:
        power.pole_grid_cm(registry, "Build_NotAThing_C")
    assert missing.value.cause == "data"
    gridless = replace(registry, limits=replace(registry.limits, hologram_grid_cm=None))
    with pytest.raises(power.PowerError) as ungridded:
        power.pole_grid_cm(gridless, power.POLE_CLASS)
    assert ungridded.value.cause == "limits"
    reachless = replace(registry, limits=replace(registry.limits, wire_max_cm={}))
    with pytest.raises(power.PowerError) as unreachable:
        power.wire_limit_cm(reachless, power.WIRE_CLASS)
    assert unreachable.value.cause == "limits"
