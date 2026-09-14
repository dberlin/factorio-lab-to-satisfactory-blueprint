"""Judging a Satisfactory placement.

Every fixture here is built with Task 5's builders -- a slab of foundations, two
Constructors, a belt on their ports -- and every failing case mutates exactly one
thing about it.  No blueprint in ``tests/fixtures/sfy`` is read for a bound or a
tolerance: the numbers the validator refuses by come from ``registry.json`` and
``hologram_rules.json``, and a community blueprint is not evidence of either.
"""

from __future__ import annotations

import math
from dataclasses import replace
from fractions import Fraction
from functools import cache

from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.layout.model import (
    BeltRun,
    FoundationObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    SfyPlacement,
    WireObj,
    belt_ends,
)
from flab2bp.sfy.layout.splines import concat, incline, quarter_turn, straight
from flab2bp.sfy.layout.validate import (
    CHECKS,
    NEEDS_SPEC,
    PROJECT,
    RULE_FOR,
    Severity,
    validate,
)
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.rules import load_rules
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer

FOUNDATION = "Build_Foundation_8x1_01_C"
CONSTRUCTOR = "Build_ConstructorMk1_C"
BELT = "Build_ConveyorBeltMk1_C"
POLE = "Build_PowerPoleMk1_C"
POWER_LINE = "Build_PowerLine_C"
ROD = "Recipe_IronRod_C"
SCREW = "Recipe_Screw_C"

FOUNDATION_Z_CM = 50.0
SLAB_TOP_CM = 100.0
ROD_Y_CM = -800.0
SCREW_Y_CM = 800.0
BELT_LENGTH_CM = 1000.0

ROD_ID, SCREW_ID, BELT_ID = 1, 2, 5


@cache
def _registry() -> Registry:
    return load_registry()


def _port(class_name: str, port_name: str) -> Port:
    return next(p for p in _registry().buildables[class_name].ports if p.name == port_name)


def _placement() -> SfyPlacement:
    """Two Constructors on a strip of foundations, one belt between them.

    The rod machine feeds the screw machine: its ``Output0`` sits at
    ``(0, -500, 200)`` and the screw machine's ``Input0`` at ``(0, 500, 200)``,
    so a straight 1000 cm run joins them along ``+Y``.
    """
    registry = _registry()
    rod_pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    screw_pose = Pose(0.0, SCREW_Y_CM, SLAB_TOP_CM, 0.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(rod_pose.transform(), output)
    facing = port_forward(rod_pose.transform(), output)
    entry, exit_end = belt_ends(registry, BELT)
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(
            MachineObj(ROD_ID, CONSTRUCTOR, rod_pose, ROD),
            MachineObj(SCREW_ID, CONSTRUCTOR, screw_pose, SCREW),
        ),
        belts=(
            BeltRun(
                BELT_ID,
                BELT,
                straight(start, facing, BELT_LENGTH_CM),
                "iron-rod",
                Fraction(1, 4),
            ),
        ),
        foundations=tuple(
            FoundationObj(10 + i, FOUNDATION, Pose(0.0, y, FOUNDATION_Z_CM, 0.0))
            for i, y in enumerate((-1200.0, -400.0, 400.0, 1200.0))
        ),
        links=(
            Link((ROD_ID, "Output0"), (BELT_ID, entry)),
            Link((BELT_ID, exit_end), (SCREW_ID, "Input0")),
        ),
    )


def _group(
    recipe_id: str,
    recipe_class: str,
    inputs: dict[str, Fraction],
    outputs: dict[str, Fraction],
    count: int = 1,
) -> SfyMachineGroup:
    return SfyMachineGroup(
        recipe_id=recipe_id,
        recipe_class=recipe_class,
        machine_item_id="constructor",
        machine_class=CONSTRUCTOR,
        count=count,
        clock=Fraction(1),
        last_clock=Fraction(1),
        max_clock=Fraction(5, 2),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine=inputs,
        outputs_per_machine=outputs,
        power_mw_per_machine=4.0,
        last_power_mw=4.0,
    )


def _spec(external_ingot: Fraction = Fraction(1, 4)) -> SfyBuildSpec:
    """Iron ingots in, screws out: one rod machine feeding one screw machine."""
    return SfyBuildSpec(
        groups=(
            _group("iron-rod", ROD, {"iron-ingot": Fraction(1, 4)}, {"iron-rod": Fraction(1, 4)}),
            _group("screw", SCREW, {"iron-rod": Fraction(1, 6)}, {"screw": Fraction(2, 3)}),
        ),
        external_inputs={"iron-ingot": external_ingot},
        outputs={"screw": Fraction(2, 3)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        label="rods and screws",
    )


def _belt(placement: SfyPlacement, run: BeltRun) -> SfyPlacement:
    return replace(placement, belts=(run,))


def _findings(placement: SfyPlacement, check: str, spec: SfyBuildSpec | None = None) -> list[str]:
    report = validate(placement, spec, _registry(), only={check})
    return sorted({f.check for f in report.errors})


# --- the registry of checks ------------------------------------------------


def test_every_check_names_the_rule_it_enforces_or_says_it_is_ours() -> None:
    """A bound with no source is a bound this project invented.

    Every check either names a rule in ``hologram_rules.json`` -- and says so in
    its docstring, so a reader can go and read the instructions it came from --
    or declares itself :data:`PROJECT`, in which case the docstring has to say
    that it is ours and why.
    """
    rules = load_rules()
    assert set(RULE_FOR) == set(CHECKS)
    for cid, fn in CHECKS.items():
        doc = fn.__doc__
        assert doc, f"{cid} has no docstring, so it states no source"
        rule = RULE_FOR[cid]
        if rule == PROJECT:
            assert "this project's own" in doc.lower(), f"{cid} is ours and does not say so"
        else:
            assert rule in rules, f"{cid} names {rule}, which is not a rule"
            assert rule in doc, f"{cid} enforces {rule} and does not quote it"


def test_a_partial_rule_is_never_enforced_as_a_bound_it_does_not_state() -> None:
    """A ``partial`` rule is a lead for the next extraction, not a bound.

    Two halves.  A check may not name a rule whose effect is ``compute``,
    ``clamp`` or ``none`` -- none of those turns a placement away, so a refusal
    built on one would be this project's own dressed up as the game's.  And a
    check that names a ``partial`` rule has to list itself in ``skipped`` with
    the reason, because the part of the comparison that was never read is
    coverage it does not have.
    """
    rules = load_rules()
    named = {cid: rules[rule] for cid, rule in RULE_FOR.items() if rule != PROJECT}
    assert {cid: r.effect for cid, r in named.items() if r.effect not in ("refuse", "snap")} == {}
    partial = {cid for cid, rule in named.items() if rule.status == "partial"}
    assert partial == {"geom.hard_clearance"}

    report = validate(_placement(), _spec(), _registry())
    for cid in partial:
        assert cid in report.skipped
        reasons = [f for f in report.by_check(cid) if f.severity is Severity.INFO]
        assert reasons and "TestClearanceOverlap" in reasons[0].message


def test_a_placement_that_is_right_passes_every_check_it_can_run() -> None:
    report = validate(_placement(), _spec(), _registry())
    assert report.ok, [f.message for f in report.errors]
    assert set(report.checks_run) | set(report.skipped) == set(CHECKS)
    # Only the two that say why they cannot cover everything stand aside.
    assert set(report.skipped) == {"geom.hard_clearance", "power.wires"}


def test_a_check_that_needs_a_spec_is_skipped_rather_than_silently_passing() -> None:
    report = validate(_placement(), None, _registry())
    assert set(report.skipped) >= NEEDS_SPEC
    assert {"flow.capacity", "flow.balance", "spec.machines"} == NEEDS_SPEC


# --- geometry --------------------------------------------------------------


def test_geom_bounds_refuses_an_object_outside_the_designer_volume() -> None:
    placement = _placement()
    assert _findings(placement, "geom.bounds") == []
    strayed = replace(
        placement,
        foundations=(
            FoundationObj(99, FOUNDATION, Pose(2000.0, 0.0, FOUNDATION_Z_CM, 0.0)),
            *placement.foundations,
        ),
    )
    assert _findings(strayed, "geom.bounds") == ["geom.bounds"]


def test_geom_hard_clearance_refuses_two_constructors_seven_metres_apart() -> None:
    """The Constructor's hard box is 1000 cm deep, so 700 cm apart is a clash."""
    placement = _placement()
    assert _findings(placement, "geom.hard_clearance") == []
    crowded = replace(
        placement,
        machines=(
            placement.machines[0],
            replace(
                placement.machines[1],
                pose=Pose(0.0, ROD_Y_CM + 700.0, SLAB_TOP_CM, 0.0),
            ),
        ),
    )
    assert _findings(crowded, "geom.hard_clearance") == ["geom.hard_clearance"]


def test_belt_capsule_refuses_a_belt_driven_through_another_belt() -> None:
    placement = _placement()
    assert _findings(placement, "belt.capsule") == []
    crossing = BeltRun(
        50, BELT, straight((-500.0, 0.0, 200.0), (1.0, 0.0, 0.0), 1000.0), "screw", Fraction(1, 4)
    )
    assert _findings(replace(placement, belts=(*placement.belts, crossing)), "belt.capsule") == [
        "belt.capsule"
    ]


def test_slab_under_every_foot_refuses_a_machine_with_a_foundation_missing() -> None:
    placement = _placement()
    assert _findings(placement, "slab.under_every_foot") == []
    gapped = replace(placement, foundations=placement.foundations[1:])
    assert _findings(gapped, "slab.under_every_foot") == ["slab.under_every_foot"]


# --- the belt's own four bounds --------------------------------------------


def _run_from_output(points_length: float) -> BeltRun:
    pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(pose.transform(), output)
    facing = port_forward(pose.transform(), output)
    run = straight(start, facing, points_length)
    return BeltRun(BELT_ID, BELT, run, "iron-rod", Fraction(1, 4))


def test_belt_max_length_refuses_a_run_longer_than_the_hologram_allows() -> None:
    placement = _placement()
    assert _findings(placement, "belt.max_length") == []
    limit = _registry().limits.belt_max_spline_cm
    too_long = _belt(placement, _run_from_output(limit + 100.0))
    assert _findings(too_long, "belt.max_length") == ["belt.max_length"]


def test_belt_min_length_refuses_a_run_shorter_than_half_a_mesh() -> None:
    placement = _placement()
    assert _findings(placement, "belt.min_length") == []
    floor = _registry().limits.belt_min_length_cm
    assert floor is not None
    too_short = _belt(placement, _run_from_output(floor / 2.0))
    assert _findings(too_short, "belt.min_length") == ["belt.min_length"]


def test_belt_incline_refuses_a_forty_degree_climb() -> None:
    placement = _placement()
    assert _findings(placement, "belt.incline") == []
    pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(pose.transform(), output)
    facing = port_forward(pose.transform(), output)
    run = incline(start, facing, run=400.0, rise=400.0 * math.tan(math.radians(40.0)))
    steep = _belt(placement, BeltRun(BELT_ID, BELT, run, "iron-rod", Fraction(1, 4)))
    assert _findings(steep, "belt.incline") == ["belt.incline"]


def test_belt_curvature_refuses_a_turn_tighter_than_the_hologram_builds() -> None:
    placement = _placement()
    assert _findings(placement, "belt.curvature") == []
    pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(pose.transform(), output)
    facing = port_forward(pose.transform(), output)
    run = concat(
        straight(start, facing, 300.0),
        quarter_turn((start[0], start[1] + 300.0, start[2]), facing, left=True, radius=100.0),
    )
    tight = _belt(placement, BeltRun(BELT_ID, BELT, run, "iron-rod", Fraction(1, 4)))
    assert _findings(tight, "belt.curvature") == ["belt.curvature"]


def test_belt_curvature_lets_a_turn_on_the_hologram_s_own_radius_through() -> None:
    """The floor is ``mBendRadius * 1.5 - 15``, and the number is not typed here."""
    placement = _placement()
    pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(pose.transform(), output)
    facing = port_forward(pose.transform(), output)
    bend = _registry().limits.belt_bend_radius_cm
    assert bend is not None
    run = concat(
        straight(start, facing, 300.0),
        quarter_turn((start[0], start[1] + 300.0, start[2]), facing, left=True, radius=2.0 * bend),
    )
    wide = _belt(placement, BeltRun(BELT_ID, BELT, run, "iron-rod", Fraction(1, 4)))
    assert _findings(wide, "belt.curvature") == []


# --- ports -----------------------------------------------------------------


def test_ports_connected_once_refuses_a_belt_end_that_hangs() -> None:
    placement = _placement()
    assert _findings(placement, "ports.connected_once") == []
    loose = replace(placement, links=placement.links[:1])
    assert _findings(loose, "ports.connected_once") == ["ports.connected_once"]


def test_ports_direction_refuses_a_belt_run_into_an_output() -> None:
    placement = _placement()
    assert _findings(placement, "ports.direction") == []
    backwards = replace(
        placement,
        links=(placement.links[0], Link(placement.links[1].a, (SCREW_ID, "Output0"))),
    )
    assert _findings(backwards, "ports.direction") == ["ports.direction"]


def test_ports_position_refuses_a_belt_three_centimetres_off_its_port() -> None:
    placement = _placement()
    assert _findings(placement, "ports.position") == []
    run = placement.belts[0]
    shifted = BeltRun(
        run.id,
        run.class_name,
        tuple(((p[0] + 3.0, p[1], p[2]), arrive, leave) for p, arrive, leave in run.points),
        run.item_id,
        run.items_per_second,
    )
    assert _findings(_belt(placement, shifted), "ports.position") == ["ports.position"]


# --- rates -----------------------------------------------------------------


def test_flow_capacity_refuses_two_items_a_second_on_a_mark_one_belt() -> None:
    placement = _placement()
    spec = _spec()
    assert _findings(placement, "flow.capacity", spec) == []
    run = placement.belts[0]
    overloaded = BeltRun(run.id, run.class_name, run.points, run.item_id, Fraction(2))
    assert _findings(_belt(placement, overloaded), "flow.capacity", spec) == ["flow.capacity"]


def test_flow_balance_refuses_a_spec_that_does_not_fund_what_it_consumes() -> None:
    placement = _placement()
    assert _findings(placement, "flow.balance", _spec()) == []
    starved = _spec(external_ingot=Fraction(1, 8))
    assert _findings(placement, "flow.balance", starved) == ["flow.balance"]


def test_spec_machines_refuses_a_row_the_placement_did_not_build() -> None:
    placement = _placement()
    spec = _spec()
    assert _findings(placement, "spec.machines", spec) == []
    wanted_two = spec.model_copy(
        update={"groups": (spec.groups[0].model_copy(update={"count": 2}), spec.groups[1])}
    )
    assert _findings(placement, "spec.machines", wanted_two) == ["spec.machines"]


# --- power and the round trip ----------------------------------------------


def test_power_wires_stands_aside_until_a_placement_has_wires() -> None:
    report = validate(_placement(), _spec(), _registry(), only={"power.wires"})
    assert report.checks_run == ()
    assert report.skipped == ("power.wires",)
    assert "Task 9" in report.by_check("power.wires")[0].message


def test_power_wires_refuses_a_machine_no_pole_reaches() -> None:
    placement = _placement()
    pole = PoleObj(20, POLE, Pose(600.0, ROD_Y_CM, SLAB_TOP_CM, 0.0))
    powered = replace(
        placement,
        poles=(pole,),
        wires=(
            WireObj(30, POWER_LINE, Link((20, "PowerConnection"), (ROD_ID, "PowerInput"))),
            WireObj(31, POWER_LINE, Link((20, "PowerConnection"), (SCREW_ID, "PowerInput"))),
        ),
    )
    assert _findings(powered, "power.wires") == []
    assert _findings(replace(powered, wires=powered.wires[:1]), "power.wires") == ["power.wires"]


def test_roundtrip_refuses_a_placement_the_emitter_cannot_write() -> None:
    placement = _placement()
    assert _findings(placement, "roundtrip") == []
    twinned = replace(
        placement,
        poles=(PoleObj(ROD_ID, POLE, Pose(600.0, 0.0, SLAB_TOP_CM, 0.0)),),
    )
    assert _findings(twinned, "roundtrip") == ["roundtrip"]


def test_roundtrip_stands_aside_when_the_library_has_no_template() -> None:
    placement = replace(
        _placement(),
        machines=(
            MachineObj(ROD_ID, "Build_HadronCollider_C", Pose(0.0, 0.0, SLAB_TOP_CM, 0.0), ROD),
        ),
        belts=(),
        links=(),
    )
    report = validate(placement, None, _registry(), only={"roundtrip"})
    assert report.skipped == ("roundtrip",)
    assert "Build_HadronCollider_C" in report.by_check("roundtrip")[0].message
