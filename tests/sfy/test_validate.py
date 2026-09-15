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

import pytest

from flab2bp.sfy.geometry import port_forward, world_port
from flab2bp.sfy.layout.model import (
    AttachmentObj,
    BeltRun,
    FoundationObj,
    LiftObj,
    Link,
    MachineObj,
    PoleObj,
    Pose,
    SfyPlacement,
    Vector,
    WireObj,
    belt_ends,
    lift_geometry,
)
from flab2bp.sfy.layout.splines import concat, incline, quarter_turn, straight
from flab2bp.sfy.layout.validate import (
    CHECKS,
    NEEDS_SPEC,
    PROJECT,
    RULE_FOR,
    Severity,
    _first_difference,
    _place_box,
    _round_to_int,
    lift_box,
    validate,
)
from flab2bp.sfy.registry import Port, Registry, load_registry
from flab2bp.sfy.rules import load_rules
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.sfy.templates import TemplateLibrary
from tests.sfy.conftest import fixture_paths

FOUNDATION = "Build_Foundation_8x1_01_C"
CONSTRUCTOR = "Build_ConstructorMk1_C"
ASSEMBLER = "Build_AssemblerMk1_C"
BEAM = "Build_Beam_C"
SPLITTER = "Build_ConveyorAttachmentSplitter_C"
MERGER = "Build_ConveyorAttachmentMerger_C"
BELT = "Build_ConveyorBeltMk1_C"
POLE = "Build_PowerPoleMk1_C"
POWER_LINE = "Build_PowerLine_C"
ROD = "Recipe_IronRod_C"
SCREW = "Recipe_Screw_C"
AI_LIMITER = "Recipe_AILimiter_C"


@cache
def _library() -> TemplateLibrary:
    """The fixture library, built once: scanning 49 blueprints costs seconds."""
    return TemplateLibrary.from_fixtures(fixture_paths())


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

    Two halves.  A check may name ONLY a rule whose effect is ``refuse``: every
    other effect describes something the game works out or moves, so a refusal
    citing one would be this project's own judgement dressed up as the game's,
    and such a check declares itself :data:`PROJECT` instead.  And a check that
    names a ``partial`` rule has to list itself in ``skipped`` with the reason,
    because the part of the comparison that was never read is coverage it does
    not have.
    """
    rules = load_rules()
    named = {cid: rules[rule] for cid, rule in RULE_FOR.items() if rule != PROJECT}
    assert {cid: r.effect for cid, r in named.items() if r.effect not in ("refuse",)} == {}
    partial = {cid for cid, rule in named.items() if rule.status == "partial"}
    assert partial == {"geom.hard_clearance"}

    report = validate(_placement(), _spec(), _registry())
    for cid in partial:
        assert cid in report.skipped
        reasons = [f for f in report.by_check(cid) if f.severity is Severity.INFO]
        assert reasons and "TestClearanceOverlap" in reasons[0].message


def test_validate_refuses_to_report_a_refusal_a_check_has_no_authority_for() -> None:
    """The discipline is enforced by the module, not only by the test above.

    A check registered against a rule the instructions show SNAPPING rather than
    refusing may not turn a placement away, and ``validate`` raises rather than
    quietly publishing the finding.
    """
    snapped = dict(load_rules())
    moved = replace(snapped["belt.min_length"], effect="snap")
    snapped["belt.min_length"] = moved
    floor = _registry().limits.belt_min_length_cm
    assert floor is not None
    short = _belt(_placement(), _run_from_output(floor / 2.0))
    with pytest.raises(ValueError, match="belt.min_length"):
        validate(short, None, _registry(), rules=snapped, only={"belt.min_length"})


def test_a_placement_that_is_right_passes_every_check_it_can_run() -> None:
    report = validate(_placement(), _spec(), _registry())
    assert report.ok, [f.message for f in report.errors]
    assert set(report.checks_run) | set(report.skipped) == set(CHECKS)
    # The five that say what they could not cover stand aside; the rest ran.
    # ``flow.boundary`` and ``flow.capacity`` are among them because this
    # placement is a fragment: no belt in it flags an end as a boundary end, so
    # there is nothing to hold to the designer wall and nothing carries the
    # ingots the spec belts in (Task 8's strategy is what lays both).
    assert set(report.skipped) == {
        "geom.hard_clearance",
        "belt.capsule",
        "power.wires",
        "flow.boundary",
        "flow.capacity",
    }
    assert "fragment rather than a build" in report.by_check("flow.capacity")[0].message


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


def test_power_wires_stands_aside_on_a_placement_with_no_wires() -> None:
    """A fragment, not a build: nothing in it says whether power was left out."""
    report = validate(_placement(), _spec(), _registry(), only={"power.wires"})
    assert report.checks_run == ()
    assert report.skipped == ("power.wires",)
    assert "carries no wires" in report.by_check("power.wires")[0].message


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


def test_power_wires_reports_an_end_it_cannot_find_rather_than_passing_over_it() -> None:
    """A wire whose length cannot be measured is a wire nobody has checked."""
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
    astray = replace(
        powered,
        wires=(
            replace(powered.wires[0], link=Link((20, "PowerConnection"), (ROD_ID, "Nowhere"))),
            powered.wires[1],
        ),
    )
    report = validate(astray, None, _registry(), only={"power.wires"})
    assert [f.check for f in report.errors] == ["power.wires"]
    assert "which is not a connection the registry gives it" in report.errors[0].message
    assert report.errors[0].detail["end"] == "Nowhere"


def test_power_wires_reports_a_class_the_registry_gives_no_reach() -> None:
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
    registry = _registry()
    reachless = replace(registry, limits=replace(registry.limits, wire_max_cm={}))
    report = validate(powered, None, reachless, only={"power.wires"})
    assert {f.check for f in report.errors} == {"power.wires"}
    assert all("no wire_max_cm" in f.message for f in report.errors)


def test_flow_capacity_feeds_the_last_machine_of_a_group_at_its_own_clock() -> None:
    """The odd machine at the end of a row runs at ``last_clock`` and eats less.

    FactorioLab's own count is ``count - 1`` machines at ``clock`` and one at
    ``last_clock``; a check that wanted the full rate from every machine would
    report a row it costed correctly as starved.
    """
    registry = _registry()
    _, exit_end = belt_ends(registry, BELT)
    output = _port(CONSTRUCTOR, "Output0")
    poses = (Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0), Pose(900.0, ROD_Y_CM, SLAB_TOP_CM, 0.0))
    feeders = tuple(
        BeltRun(
            40 + index,
            BELT,
            straight(world_port(pose.transform(), output), (0.0, 1.0, 0.0), BELT_LENGTH_CM),
            "iron-ingot",
            share,
        )
        for index, (pose, share) in enumerate(
            zip(poses, (Fraction(1, 4), Fraction(1, 8)), strict=True)
        )
    )
    half = SfyPlacement(
        designer=designer("mk1", registry),
        machines=(
            MachineObj(ROD_ID, CONSTRUCTOR, poses[0], ROD, clock=Fraction(1)),
            MachineObj(SCREW_ID, CONSTRUCTOR, poses[1], ROD, clock=Fraction(1, 2)),
        ),
        belts=feeders,
        links=(
            Link((feeders[0].id, exit_end), (ROD_ID, "Input0")),
            Link((feeders[1].id, exit_end), (SCREW_ID, "Input0")),
        ),
    )
    spec = SfyBuildSpec(
        groups=(
            _group(
                "iron-rod", ROD, {"iron-ingot": Fraction(1, 4)}, {"iron-rod": Fraction(1, 4)}, 2
            ),
        ),
        external_inputs={"iron-ingot": Fraction(3, 8)},
        outputs={"iron-rod": Fraction(3, 8)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        label="one underclocked machine",
    )
    spec = spec.model_copy(
        update={"groups": (spec.groups[0].model_copy(update={"last_clock": Fraction(1, 2)}),)}
    )
    assert _findings(half, "flow.capacity", spec) == []
    # And it is the machine's OWN share, not a free pass: a quarter of what it
    # needs is still starved.
    starved = replace(
        half,
        belts=(feeders[0], replace(feeders[1], items_per_second=Fraction(1, 16))),
    )
    report = validate(starved, spec, _registry(), only={"flow.capacity"})
    assert [f.check for f in report.errors] == ["flow.capacity"]
    assert report.errors[0].detail["needed"] == "1/8"


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


# --- what the checks do not know -------------------------------------------


def test_belt_capsule_says_which_parts_of_the_clearance_rule_were_never_read() -> None:
    """``belt.clearance`` is read whole; two things inside it are not.

    The flag bytes of ``FFGClearanceData``, where a ``CT_Soft`` marking would
    live, and the tolerance arithmetic that decides how many boxes a spline
    gets.  The check runs anyway and says so.
    """
    report = validate(_placement(), None, _registry(), only={"belt.capsule"})
    assert report.skipped == ("belt.capsule",)
    assert report.checks_run == ()
    reason = report.by_check("belt.capsule")[0]
    assert reason.severity is Severity.INFO
    assert "GetNextDistanceExceedingTolerance" in reason.message
    assert "flag bytes" in reason.message


def _assembler_with_a_belt_over_it() -> SfyPlacement:
    """An Assembler whose own belt climbs back across its upper clearance box.

    ``Output0`` sits at ``(0, 500, 100)``, inside box 0 (``y`` to 750, ``z`` to
    300) and outside box 1 (``y`` to 350, ``z`` from 300).  So the belt wired to
    that port is forgiven box 0 and not box 1.
    """
    registry = _registry()
    pose = Pose(0.0, 0.0, 0.0, 0.0)
    output = _port(ASSEMBLER, "Output0")
    start = world_port(pose.transform(), output)
    entry, _ = belt_ends(registry, BELT)
    climb = incline(start, (0.0, -1.0, 0.0), run=400.0, rise=450.0)
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(1, ASSEMBLER, pose, AI_LIMITER),),
        belts=(BeltRun(BELT_ID, BELT, climb, "iron-rod", Fraction(1, 4)),),
        links=(Link((1, "Output0"), (BELT_ID, entry)),),
    )


def test_belt_capsule_forgives_only_the_box_the_wired_port_sits_inside() -> None:
    placement = _assembler_with_a_belt_over_it()
    report = validate(placement, None, _registry(), only={"belt.capsule"})
    assert [f.check for f in report.errors] == ["belt.capsule"]
    # Box 0 holds Output0 and is forgiven; box 1 is a different box.
    assert "box 1" in report.errors[0].message
    assert "box 0" not in report.errors[0].message


def _two_runs_end_to_end(second_start: Vector, heading: Vector) -> SfyPlacement:
    registry = _registry()
    entry, exit_end = belt_ends(registry, BELT)
    first = BeltRun(60, BELT, straight((0.0, 0.0, 200.0), (1.0, 0.0, 0.0), 400.0))
    second = BeltRun(61, BELT, straight(second_start, heading, 400.0))
    return SfyPlacement(
        designer=designer("mk1", registry),
        belts=(first, second),
        links=(Link((60, exit_end), (61, entry)),),
    )


def test_belt_capsule_forgives_five_centimetres_only_at_the_point_two_runs_meet() -> None:
    """The slack is for the seam, not for the whole run.

    Two belts wired end to end lap by a sliver where their tangents differ, and
    :data:`BELT_CONNECTION_CM` covers that.  The same five centimetres ten
    metres down the belt is a belt through a belt, so the tolerance only applies
    to boxes within one box length of the shared point.
    """
    seam = _two_runs_end_to_end((398.0, 0.0, 200.0), (1.0, 0.0, 0.0))
    assert _findings(seam, "belt.capsule") == []
    doubled_back = _two_runs_end_to_end((400.0, 0.0, 200.0), (-1.0, 0.0, 0.0))
    assert _findings(doubled_back, "belt.capsule") == ["belt.capsule"]


def test_geom_hard_clearance_tests_a_box_the_game_only_excludes_from_snapping() -> None:
    """``ExcludeForSnapping`` excludes a box from SNAPPING, not from clearance.

    ``Build_AssemblerMk1_C``'s box 1 is hard and carries the flag, so a pair it
    is half of is reported like any other -- and the finding says the flag was
    there, rather than the check quietly reading past it.
    """
    registry = _registry()
    stacked = SfyPlacement(
        designer=designer("mk1", registry),
        machines=(
            MachineObj(1, ASSEMBLER, Pose(0.0, 0.0, 0.0, 0.0), AI_LIMITER),
            MachineObj(2, ASSEMBLER, Pose(0.0, 0.0, 300.0, 0.0), AI_LIMITER),
        ),
    )
    report = validate(stacked, None, _registry(), only={"geom.hard_clearance"})
    assert report.errors
    assert any(f.detail["exclude_for_snapping"] for f in report.errors)


def test_a_rotated_clearance_box_stands_where_its_rotation_puts_it() -> None:
    """``Build_Beam_C``'s box is turned, and reading only Min/Max loses that.

    Its rotator is ``(pitch 0, yaw 90, roll -90)``, which sends the box's local
    ``Z`` -- the 400 cm one -- along world ``+X``.  A beam at the origin
    therefore reaches 400 cm out along ``X`` and only 50 cm in ``Z``, not the
    other way round.
    """
    beam = _registry().buildables[BEAM].clearance[0]
    box = _place_box(beam, Pose(0.0, 0.0, 100.0, 0.0).transform(), 1, "beam")
    assert [round(v, 4) for v in box.axes[2]] == [1.0, 0.0, 0.0]
    corners = box.corners()
    assert round(max(c[0] for c in corners), 3) == 400.0
    assert round(min(c[0] for c in corners), 3) == 0.0
    assert round(max(c[2] for c in corners), 3) == 150.0


def test_geom_bounds_reads_a_rotated_box_where_the_rotation_puts_it() -> None:
    """A beam 1500 cm out is inside the designer unrotated and outside turned.

    The model has no beam object; ``PoleObj`` carries a class name and a pose,
    which is all ``geom.bounds`` reads, and this test is about the BOX.
    """
    registry = _registry()
    placement = SfyPlacement(
        designer=designer("mk1", registry),
        poles=(PoleObj(1, BEAM, Pose(1500.0, 0.0, 100.0, 0.0)),),
    )
    assert _findings(placement, "geom.bounds") == ["geom.bounds"]


# --- the game's own arithmetic, spelled out --------------------------------


def test_round_to_int_is_the_games_own_sse_rounding_and_not_a_ceiling() -> None:
    """``addss xmm2,xmm2; addss 0.5; cvtss2si; sar esi,1``.

    ``cvtss2si`` rounds half to EVEN under the default ``MXCSR``, which on its
    own would send 0.5 to 0 and 2.5 to 2.  Doubling first is what stops that:
    the tie lands on ``2x + 0.5``, and the shift back recovers
    ``floor(x + 0.5)`` -- half UP -- for every ``x``.  Getting this wrong
    changes the sample count on exactly the lengths where it matters, and a
    ceiling gets 1.02 wrong in the other direction.
    """
    assert [_round_to_int(v) for v in (0.4, 0.5, 1.02, 1.5, 2.5, 3.5, 20.0)] == [
        0,
        1,
        1,
        2,
        3,
        4,
        20,
    ]
    assert all(_round_to_int(v / 8) == math.floor(v / 8 + 0.5) for v in range(200))


def test_belt_curvature_refuses_a_sample_whose_tangent_has_no_horizontal_part() -> None:
    """``GetSafeNormal2D`` hands back ``ZeroVector``, and the loop uses it.

    The dot product is then 0, ``acos(0)`` is ``PI/2`` and the radius comes out
    ``step / (PI/2)`` -- so a belt going straight up is refused by the curvature
    rule, not passed over by it.  ``belt.incline`` refuses it as well, on the
    chord, which is a different test.
    """
    registry = _registry()
    climb = straight((0.0, 0.0, 200.0), (0.0, 0.0, 1.0), 1000.0)
    upright = SfyPlacement(
        designer=designer("mk1", registry),
        belts=(BeltRun(70, BELT, climb),),
    )
    report = validate(upright, None, _registry(), only={"belt.curvature"})
    assert [f.check for f in report.errors] == ["belt.curvature"]
    step = 1000.0 / 20
    assert report.errors[0].detail["radius_cm"] == round(step / (math.pi / 2.0), 3)


# --- the branches the happy path does not reach ----------------------------


def test_ports_position_refuses_a_belt_that_leaves_off_its_ports_facing() -> None:
    placement = _placement()
    pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(pose.transform(), output)
    sideways = BeltRun(BELT_ID, BELT, straight(start, (1.0, 0.0, 0.0), BELT_LENGTH_CM))
    turned = replace(placement, belts=(sideways,), links=placement.links[:1])
    report = validate(turned, None, _registry(), only={"ports.position"})
    assert [f.check for f in report.errors] == ["ports.position"]
    assert "off its facing" in report.errors[0].message


def test_power_wires_refuses_a_wire_longer_than_its_class_reaches() -> None:
    registry = _registry()
    limit = registry.limits.wire_max_cm[POWER_LINE]
    far = SfyPlacement(
        designer=designer("mk3", registry),
        poles=(
            PoleObj(1, POLE, Pose(0.0, 0.0, 0.0, 0.0)),
            PoleObj(2, POLE, Pose(limit + 200.0, 0.0, 0.0, 0.0)),
        ),
        wires=(WireObj(3, POWER_LINE, Link((1, "PowerConnection"), (2, "PowerConnection"))),),
    )
    report = validate(far, None, _registry(), only={"power.wires"})
    assert [f.check for f in report.errors] == ["power.wires"]
    assert report.errors[0].detail["limit_cm"] == limit


def test_power_wires_refuses_a_connection_carrying_more_wires_than_it_accepts() -> None:
    """``Build_PowerPoleMk1_C``'s ``PowerConnection`` states four."""
    registry = _registry()
    accepted = _port(POLE, "PowerConnection").max_connections
    assert accepted == 4
    crowded = SfyPlacement(
        designer=designer("mk1", registry),
        poles=(
            PoleObj(1, POLE, Pose(0.0, 0.0, 0.0, 0.0)),
            PoleObj(2, POLE, Pose(400.0, 0.0, 0.0, 0.0)),
        ),
        wires=tuple(
            WireObj(10 + i, POWER_LINE, Link((1, "PowerConnection"), (2, "PowerConnection")))
            for i in range(accepted + 1)
        ),
    )
    report = validate(crowded, None, _registry(), only={"power.wires"})
    assert report.errors
    assert any(f.detail.get("wires") == accepted + 1 for f in report.errors)


def test_a_splitter_and_a_merger_come_back_out_of_the_blueprint_they_went_into() -> None:
    registry = _registry()
    attached = SfyPlacement(
        designer=designer("mk1", registry),
        attachments=(
            AttachmentObj(1, SPLITTER, Pose(0.0, 0.0, SLAB_TOP_CM, 0.0)),
            AttachmentObj(2, MERGER, Pose(600.0, 0.0, SLAB_TOP_CM, 90.0)),
        ),
    )
    assert _findings(attached, "roundtrip") == []


def test_a_belt_on_a_turned_machine_starts_on_the_port_the_turn_put_there() -> None:
    """A Constructor at 90 degrees faces its ``Output0`` along ``-X``."""
    registry = _registry()
    pose = Pose(800.0, 0.0, SLAB_TOP_CM, 90.0)
    output = _port(CONSTRUCTOR, "Output0")
    start = world_port(pose.transform(), output)
    facing = port_forward(pose.transform(), output)
    assert [round(v, 6) for v in start] == [500.0, 0.0, 200.0]
    assert [round(v, 6) for v in facing] == [-1.0, 0.0, 0.0]
    entry, _ = belt_ends(registry, BELT)
    turned = SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(1, CONSTRUCTOR, pose, ROD),),
        belts=(BeltRun(BELT_ID, BELT, straight(start, facing, 400.0)),),
        links=(Link((1, "Output0"), (BELT_ID, entry)),),
    )
    assert _findings(turned, "ports.position") == []


def test_first_difference_names_the_field_two_placements_differ_in() -> None:
    """What ``roundtrip`` says when a placement does not survive the file."""
    placement = _placement()
    assert "machines differ" in _first_difference(placement, replace(placement, machines=()))
    assert "links differ" in _first_difference(placement, replace(placement, links=()))
    assert "does not name" in _first_difference(placement, placement)


def test_validate_writes_the_round_trip_with_the_library_it_is_given() -> None:
    """``library=`` is the seam; without it the repo's own corpus is the fallback."""
    report = validate(_placement(), None, _registry(), only={"roundtrip"}, library=_library())
    assert report.checks_run == ("roundtrip",)
    assert not report.errors
    bare = validate(
        _placement(), None, _registry(), only={"roundtrip"}, library=TemplateLibrary({})
    )
    assert bare.skipped == ("roundtrip",)


# --- the boundary ----------------------------------------------------------


def _at_the_wall(
    *, entry_y: float | None = None, item_in: str = "iron-ingot", item_out: str = "screw"
) -> SfyPlacement:
    """The spec's ingots in at the ``-Y`` wall and its screws out at the ``+Y``.

    Two belts joined to each other where they meet, each with its far end open
    on the wall it claims, because what a boundary belt meets there is outside
    the blueprint.  ``entry_y`` moves the entry belt's open end off that wall,
    which also opens a gap at the join -- ``ports.position``'s business, and
    this fixture is never handed to it.
    """
    registry = _registry()
    half = designer("mk1", registry).half_cm
    entry, exit_end = belt_ends(registry, BELT)
    arriving = BeltRun(
        BELT_ID,
        BELT,
        straight((0.0, -half if entry_y is None else entry_y, 200.0), (0.0, 1.0, 0.0), 400.0),
        item_in,
        Fraction(1, 4),
        boundary_start=True,
    )
    leaving = BeltRun(
        BELT_ID + 1,
        BELT,
        straight((0.0, -half + 400.0, 200.0), (0.0, 1.0, 0.0), 2.0 * half - 400.0),
        item_out,
        Fraction(1, 4),
        boundary_end=True,
    )
    return SfyPlacement(
        designer=designer("mk1", registry),
        belts=(arriving, leaving),
        links=(Link((arriving.id, exit_end), (leaving.id, entry)),),
    )


def test_ports_connected_once_forgives_an_end_flagged_as_a_boundary_end() -> None:
    """The flag is the author saying an end is meant to be open, and it is exact.

    An unflagged loose end is still a belt that silently does not run, and a
    flagged end that someone then wired to something is a contradiction: the
    check wants zero links there, not "at most one".
    """
    open_ended = _at_the_wall()
    assert _findings(open_ended, "ports.connected_once") == []
    plain = replace(
        open_ended,
        belts=tuple(
            replace(run, boundary_start=False, boundary_end=False) for run in open_ended.belts
        ),
    )
    assert _findings(plain, "ports.connected_once") == ["ports.connected_once"]


def test_flow_boundary_refuses_an_open_end_that_is_not_on_the_wall_it_claims() -> None:
    assert _findings(_at_the_wall(), "flow.boundary", _spec()) == []
    inland = _at_the_wall(entry_y=-1300.0)
    report = validate(inland, _spec(), _registry(), only={"flow.boundary"})
    assert [f.check for f in report.errors] == ["flow.boundary"]
    assert "300.0 cm off the y = -1600 wall" in report.errors[0].message


def test_flow_boundary_holds_the_items_at_the_wall_to_the_specs_own() -> None:
    """A build that belts in something the spec never asked for is not that build."""
    report = validate(
        _at_the_wall(item_in="iron-plate"), _spec(), _registry(), only={"flow.boundary"}
    )
    messages = [f.message for f in report.errors]
    assert messages == [
        "the build enters at the -Y wall on ['iron-plate'] and the spec says ['iron-ingot']"
    ]


def test_flow_boundary_refuses_a_build_that_belts_an_external_input_in_nowhere() -> None:
    """Which is why ``flow.capacity`` no longer passes a starved external input over.

    An item the spec funds from ``external_inputs`` that no belt carries in used
    to be excused there, from before the boundary belts were laid.  It is this
    check's business, it has always been refused here, and refusing it once and
    naming it once is the honest answer.
    """
    at_the_wall = _at_the_wall()
    arriving, leaving = at_the_wall.belts
    unfed = replace(at_the_wall, belts=(replace(arriving, boundary_start=False), leaving))
    report = validate(unfed, _spec(), _registry(), only={"flow.boundary"})
    assert [f.message for f in report.errors] == [
        "the build enters at the -Y wall on [] and the spec says ['iron-ingot']"
    ]


def test_flow_boundary_stands_aside_on_a_fragment_and_without_a_spec() -> None:
    fragment = validate(_placement(), _spec(), _registry(), only={"flow.boundary"})
    assert fragment.skipped == ("flow.boundary",)
    assert "fragment" in fragment.by_check("flow.boundary")[0].message
    unspecified = validate(_at_the_wall(), None, _registry(), only={"flow.boundary"})
    assert unspecified.skipped == ("flow.boundary",)
    assert "no spec was given" in unspecified.by_check("flow.boundary")[0].message


# --- conveyor lifts --------------------------------------------------------

LIFT = "Build_ConveyorLiftMk1_C"
LIFT_ID, LIFT_BELT_ID = 7, 8
LIFT_HEIGHT_CM = 600.0
WALL_CM = 1600.0
"""The lift is 600 cm rather than the 400 cm minimum so that the belt on its far
end clears the Constructor's own clearance box, which is 600 cm tall on a machine
standing at z = 100.  Both numbers are the game's: ``lift_min_cm`` and the box in
``registry.json``."""


def _foundations() -> tuple[FoundationObj, ...]:
    return tuple(
        FoundationObj(10 + i, FOUNDATION, Pose(0.0, y, FOUNDATION_Z_CM, 0.0))
        for i, y in enumerate((-1200.0, -400.0, 400.0, 1200.0))
    )


def _lift_out_of_a_machine(height_cm: float = LIFT_HEIGHT_CM) -> SfyPlacement:
    """A lift standing on the rod machine's output, and a belt off its top.

    The lift's BOTTOM is on the port -- ``mConnection0`` is at the actor
    transform and is the end items enter by -- so this is a lift carrying items
    upward, and the belt leaves the top along the top's own facing.
    """
    registry = _registry()
    rod_pose = Pose(0.0, ROD_Y_CM, SLAB_TOP_CM, 0.0)
    foot = world_port(rod_pose.transform(), _port(CONSTRUCTOR, "Output0"))
    lift = LiftObj(LIFT_ID, LIFT, Pose(foot[0], foot[1], foot[2], 90.0), height_cm)
    top, facing = lift.top_end(lift_geometry(registry, LIFT))
    entry, exit_end = belt_ends(registry, LIFT)
    belt_entry, _ = belt_ends(registry, BELT)
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(ROD_ID, CONSTRUCTOR, rod_pose, ROD),),
        lifts=(lift,),
        belts=(
            BeltRun(
                LIFT_BELT_ID,
                BELT,
                straight(top, facing, WALL_CM - top[1]),
                "iron-rod",
                Fraction(1, 4),
                boundary_end=True,
            ),
        ),
        foundations=_foundations(),
        links=(
            Link((ROD_ID, "Output0"), (LIFT_ID, entry)),
            Link((LIFT_ID, exit_end), (LIFT_BELT_ID, belt_entry)),
        ),
    )


def _lift_into_a_machine(height_cm: float = LIFT_HEIGHT_CM) -> SfyPlacement:
    """A belt along the top, down a lift, into the screw machine's input.

    The reverse of :func:`_lift_out_of_a_machine`, and the case the geometry
    makes awkward: items leave a lift by ``mConnection1`` at ``mTopTransform``,
    so a lift that delivers DOWNWARD into a port has its actor at the top --
    where the belt arrives -- and a negative height.
    """
    registry = _registry()
    screw_pose = Pose(0.0, SCREW_Y_CM, SLAB_TOP_CM, 0.0)
    mouth = world_port(screw_pose.transform(), _port(CONSTRUCTOR, "Input0"))
    lift = LiftObj(LIFT_ID, LIFT, Pose(mouth[0], mouth[1], mouth[2] + height_cm, 90.0), -height_cm)
    head, _ = lift.bottom_end(lift_geometry(registry, LIFT))
    entry, exit_end = belt_ends(registry, LIFT)
    _, belt_exit = belt_ends(registry, BELT)
    return SfyPlacement(
        designer=designer("mk1", registry),
        machines=(MachineObj(SCREW_ID, CONSTRUCTOR, screw_pose, SCREW),),
        lifts=(lift,),
        belts=(
            BeltRun(
                LIFT_BELT_ID,
                BELT,
                straight((head[0], -WALL_CM, head[2]), (0.0, 1.0, 0.0), head[1] + WALL_CM),
                "iron-rod",
                Fraction(1, 4),
                boundary_start=True,
            ),
        ),
        foundations=_foundations(),
        links=(
            Link((LIFT_BELT_ID, belt_exit), (LIFT_ID, entry)),
            Link((LIFT_ID, exit_end), (SCREW_ID, "Input0")),
        ),
    )


def _lift(placement: SfyPlacement, **changes: float) -> SfyPlacement:
    return replace(placement, lifts=(replace(placement.lifts[0], **changes),))


def test_a_lift_out_of_a_port_and_one_into_a_port_both_pass_every_check() -> None:
    """The two shapes M3's router will lay, judged by everything this module has."""
    for placement in (_lift_out_of_a_machine(), _lift_into_a_machine()):
        report = validate(placement, None, _registry())
        assert report.ok, [f.message for f in report.errors]
        assert {"lift.height", "lift.step", "lift.placement"} <= set(report.checks_run)


def test_a_lift_box_is_the_rules_span_and_the_binarys_width() -> None:
    """The box's length is the rule's, and its width is now the game's too.

    ``lift.clearance`` states the span; how WIDE the box is used to be this
    project's own reading of the connector clearance, because
    ``AFGBuildableConveyorLift::FitClearance`` takes the half-extent from a
    module global and ``sfy-native``'s constant annotation trusts only
    ``.rdata``. The tool now quotes that global through its PDB symbol, so the
    registry carries ``lift_clearance_half_extent_cm`` -- the initialiser's
    100 cm less the 5 cm ``FitClearance`` shrinks each axis by -- and the box
    is the game's in both directions.
    """
    registry = _registry()
    assert registry.limits.lift_clearance_half_extent_cm == 95.0
    assert registry.limits_sources["lift_clearance_half_extent_cm"] == "binary-derived"

    height_cm = 400.0
    placement = _lift_out_of_a_machine(height_cm)
    box = lift_box(placement.lifts[0], registry)
    assert box.half == pytest.approx((height_cm / 2.0, 95.0, 95.0))


def test_lift_height_refuses_a_lift_shorter_or_taller_than_the_game_clamps_to() -> None:
    """``lift.height_range`` CLAMPS, so the refusal is ours and the numbers theirs.

    ``mMinimumHeight`` and ``mMaximumHeight`` are 400 and 4800 in the shipped
    build, worked out by ``BeginPlay`` from the lift's mesh height.
    """
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "lift.height") == []
    assert _findings(_lift(placement, height_cm=300.0), "lift.height") == ["lift.height"]
    assert _findings(_lift(placement, height_cm=5000.0), "lift.height") == ["lift.height"]
    # The sign is the flow direction, not a second range: a downward lift is
    # held to the same window on the size of the drop.
    down = _lift_into_a_machine()
    assert _findings(down, "lift.height") == []
    assert _findings(_lift(down, height_cm=-300.0), "lift.height") == ["lift.height"]


def test_lift_step_refuses_a_height_the_hologram_would_move() -> None:
    """``lift.step`` snaps; we refuse, because a snapped lift is not the one costed."""
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "lift.step") == []
    assert _findings(_lift(placement, height_cm=450.0), "lift.step") == ["lift.step"]
    assert _findings(_lift(placement, height_cm=450.0), "lift.height") == []


def test_lift_placement_refuses_an_end_on_a_connection_that_is_already_wired() -> None:
    """``lift.placement`` is the game's own refusal, read at 0xa681fa/0xa68253.

    ``CheckValidPlacement`` tests ``mHasConnectedComponent`` on each of the two
    connections the lift snapped to and adds ``UFGCDInvalidPlacement`` when one
    is set, so a lift that ends on an occupied port is a build the game turns
    away rather than one it moves.
    """
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "lift.placement") == []
    belt_entry, _ = belt_ends(_registry(), BELT)
    stolen = replace(
        placement,
        links=(
            *placement.links,
            Link((ROD_ID, "Output0"), (LIFT_BELT_ID, belt_entry)),
        ),
    )
    assert _findings(stolen, "lift.placement") == ["lift.placement"]


def test_a_lifts_ends_are_held_to_the_ports_they_are_wired_to() -> None:
    """``ports.position`` over a lift: the bottom is the actor, the top the height above."""
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "ports.position") == []
    moved = _lift(placement, height_cm=700.0)
    assert _findings(moved, "ports.position") == ["ports.position"]


def test_both_ends_of_a_lift_are_wired_exactly_once() -> None:
    """``ports.connected_once`` over a lift: a dangling end is a build that does not run."""
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "ports.connected_once") == []
    loose = replace(placement, links=placement.links[1:])
    assert _findings(loose, "ports.connected_once") == ["ports.connected_once"]


def test_a_link_runs_out_of_a_lifts_exit_and_into_its_entry() -> None:
    """``ports.direction`` over a lift: ``flow`` says which end is which, not the sign."""
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "ports.direction") == []
    entry, exit_end = belt_ends(_registry(), LIFT)
    belt_entry, _ = belt_ends(_registry(), BELT)
    backwards = replace(
        placement,
        links=(
            Link((ROD_ID, "Output0"), (LIFT_ID, exit_end)),
            Link((LIFT_ID, entry), (LIFT_BELT_ID, belt_entry)),
        ),
    )
    assert _findings(backwards, "ports.direction") == ["ports.direction"]


def test_a_lifts_clearance_is_judged_against_a_hard_box_it_is_not_wired_to() -> None:
    """The lift's own box is live, not excluded away by the two forgivenesses.

    ``belt.capsule`` forgives the one box a wired port sits inside and the
    conveyor a lift is wired to, both for the unread ``TestClearanceOverlap``.
    A machine the lift has nothing to do with is neither, so a lift standing in
    its clearance is reported.
    """
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "belt.capsule") == []
    crowded = replace(
        placement,
        machines=(
            *placement.machines,
            MachineObj(99, CONSTRUCTOR, Pose(0.0, -500.0, SLAB_TOP_CM, 0.0), ROD),
        ),
    )
    assert _findings(crowded, "belt.capsule") == ["belt.capsule"]


def test_lift_top_yaw_refuses_a_turn_the_build_gun_cannot_make() -> None:
    """``lift.top_yaw`` computes the yaw in 90 degree steps; off the lattice is ours.

    ``GetRotationStep`` returns 90 once the first placement point is down
    (``0xa7c1a2``), and ``ApplyScrollRotationTo`` rounds onto that lattice, so a
    lift whose top is turned 37 degrees is not a lift any player could build --
    even one that ends straight in a port, where the turn changes nothing about
    where the end sits.
    """
    placement = _lift_out_of_a_machine()
    assert _findings(placement, "lift.top_yaw") == []
    for yaw in (90.0, 180.0, -90.0):
        assert _findings(_lift(placement, top_yaw_deg=yaw), "lift.top_yaw") == []
    assert _findings(_lift(placement, top_yaw_deg=37.0), "lift.top_yaw") == ["lift.top_yaw"]
    into = _lift_into_a_machine()
    assert _findings(into, "lift.top_yaw") == []
    assert _findings(_lift(into, top_yaw_deg=37.0), "lift.top_yaw") == ["lift.top_yaw"]
