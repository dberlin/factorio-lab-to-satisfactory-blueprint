"""Observable failures in authored fluid networks and their vertical seams."""

from dataclasses import replace
from fractions import Fraction

from flab2bp.sfy.geometry import Vector
from flab2bp.sfy.layout.model import (
    FoundationObj,
    LiftObj,
    Link,
    MachineObj,
    PassthroughObj,
    PipeAttachmentObj,
    PipeRun,
    Pose,
    SfyPlacement,
    StackLane,
    belt_ends,
    pipe_ends,
)
from flab2bp.sfy.layout.splines import SplinePoint
from flab2bp.sfy.layout.validate import required_input_head_m, validate
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import PipeTier, SfyBuildSpec, SfyMachineGroup, designer

PIPE = "Build_Pipeline_C"
REFINERY = "Build_OilRefinery_C"


def straight(start: Vector, end: Vector) -> tuple[SplinePoint, ...]:
    delta = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    return ((start, delta, delta), (end, delta, delta))


def _plastic() -> tuple[Registry, SfyBuildSpec, SfyPlacement]:
    registry = load_registry()
    group = SfyMachineGroup(
        recipe_id="plastic",
        recipe_class="Recipe_Plastic_C",
        machine_item_id="refinery",
        machine_class=REFINERY,
        count=1,
        clock=Fraction(1),
        last_clock=Fraction(1),
        max_clock=Fraction(5, 2),
        somersloops=0,
        power_shards_per_machine=0,
        last_power_shards=0,
        inputs_per_machine={"crude-oil": Fraction(1, 2)},
        outputs_per_machine={"plastic": Fraction(1, 3), "heavy-oil-residue": Fraction(1, 3)},
        power_mw_per_machine=30.0,
        last_power_mw=30.0,
    )
    spec = SfyBuildSpec(
        groups=(group,),
        external_inputs={"crude-oil": Fraction(1, 2)},
        outputs={"plastic": Fraction(1, 3)},
        surplus_outputs={"heavy-oil-residue": Fraction(1, 3)},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(1),
        fluid_items=frozenset({"crude-oil", "heavy-oil-residue"}),
        pipe_tiers=(PipeTier(item_id="pipeline-mk1", cubic_metres_per_second=Fraction(5)),),
    )
    feed = PipeRun(
        2,
        PIPE,
        straight((-200, 1400, 275), (-200, 900, 275)),
        "crude-oil",
        Fraction(1, 2),
        boundary_start=True,
    )
    drain = PipeRun(
        3,
        PIPE,
        straight((-200, -900, 275), (-200, -1400, 275)),
        "heavy-oil-residue",
        Fraction(1, 3),
        boundary_end=True,
    )
    start, end = pipe_ends(registry, PIPE)
    placement = SfyPlacement(
        designer=designer("mk3", registry),
        machines=(MachineObj(1, REFINERY, Pose(0, 0, 100, 0), "Recipe_Plastic_C"),),
        pipes=(feed, drain),
        links=(Link((2, end), (1, "PipeInputFactory")), Link((1, "PipeOutputFactory"), (3, start))),
    )
    return registry, spec, placement


def test_fluid_positions_use_dynamic_spline_ends_and_ignore_link_order() -> None:
    registry, spec, placement = _plastic()
    placement = replace(placement, links=tuple(Link(link.b, link.a) for link in placement.links))
    checks = {"ports.position", "ports.direction", "ports.connected_once"}
    assert validate(placement, spec, registry, only=checks).errors == ()
    moved = replace(placement.pipes[0], points=straight((-200, 1400, 275), (-200, 903, 275)))
    report = validate(
        replace(placement, pipes=(moved, placement.pipes[1])), spec, registry, only=checks
    )
    assert any(f.check == "ports.position" and 2 in f.objects for f in report.errors)


def test_removing_internal_pipe_link_is_not_an_intentional_boundary() -> None:
    registry, spec, placement = _plastic()
    report = validate(
        replace(placement, links=placement.links[1:]),
        spec,
        registry,
        only={"ports.connected_once", "flow.capacity"},
    )
    assert any(f.check == "ports.connected_once" and 2 in f.objects for f in report.errors)
    assert any(f.detail.get("item") == "crude-oil" for f in report.errors)


def test_wrong_fluid_cannot_feed_recipe_input() -> None:
    registry, spec, placement = _plastic()
    wrong = replace(placement.pipes[0], item_id="water")
    report = validate(
        replace(placement, pipes=(wrong, placement.pipes[1])),
        spec,
        registry,
        only={"flow.capacity", "pipe.fluid_requirements"},
    )
    assert any(f.detail.get("item") == "crude-oil" for f in report.errors)


def test_machine_feed_must_meet_actual_clock_rate() -> None:
    registry, spec, placement = _plastic()
    slow = replace(placement.pipes[0], cubic_metres_per_second=Fraction(1, 4))
    report = validate(
        replace(placement, pipes=(slow, placement.pipes[1])), spec, registry, only={"flow.capacity"}
    )
    assert any(
        f.detail.get("item") == "crude-oil" and f.detail.get("needed") == "1/2"
        for f in report.errors
    )


def test_recipe_residue_requires_connected_drain_even_when_not_objective() -> None:
    registry, spec, placement = _plastic()
    report = validate(
        replace(placement, pipes=placement.pipes[:1], links=placement.links[:1]),
        spec,
        registry,
        only={"flow.capacity"},
    )
    assert any(
        f.detail.get("item") == "heavy-oil-residue" and "drain" in f.message for f in report.errors
    )


def test_pipe_mark_capacity_is_not_conveyor_speed() -> None:
    registry, spec, placement = _plastic()
    overloaded = replace(placement.pipes[0], cubic_metres_per_second=Fraction(6))
    report = validate(
        replace(placement, pipes=(overloaded, placement.pipes[1])),
        spec,
        registry,
        only={"flow.capacity"},
    )
    assert any(2 in f.objects and f.detail.get("capacity") == "5" for f in report.errors)


def test_vertical_pipe_is_legal_but_native_minimum_and_maximum_apply() -> None:
    registry = load_registry()
    upright = PipeRun(1, PIPE, straight((0, 0, 100), (0, 0, 1100)))
    placement = SfyPlacement(designer("mk3", registry), pipes=(upright,))
    checks = {"pipe.min_length", "pipe.max_length", "pipe.curvature"}
    assert validate(placement, None, registry, only=checks).errors == ()
    short = replace(upright, points=straight((0, 0, 100), (0, 0, 200)))
    assert any(
        f.check == "pipe.min_length"
        for f in validate(replace(placement, pipes=(short,)), None, registry, only=checks).errors
    )
    long = replace(upright, points=straight((0, 0, 100), (0, 0, 5800)))
    assert any(
        f.check == "pipe.max_length"
        for f in validate(replace(placement, pipes=(long,)), None, registry, only=checks).errors
    )


def test_pipe_mesh_envelope_must_stay_inside_designer() -> None:
    registry = load_registry()
    volume = designer("mk3", registry)
    run = PipeRun(1, PIPE, straight((volume.half_cm - 10, 0, 200), (volume.half_cm - 10, 0, 800)))
    report = validate(SfyPlacement(volume, pipes=(run,)), None, registry, only={"geom.bounds"})
    assert any(1 in f.objects for f in report.errors)


def test_pump_without_wire_is_unpowered_not_an_unjudged_fragment() -> None:
    registry = load_registry()
    pump = PipeAttachmentObj(1, "Build_PipelinePump_C", Pose(0, 0, 200, 0))
    report = validate(
        SfyPlacement(designer("mk3", registry), pipe_attachments=(pump,)),
        None,
        registry,
        only={"power.wires"},
    )
    assert any(1 in f.objects for f in report.errors)


def test_any_junction_cannot_merge_two_distinct_fluids() -> None:
    registry = load_registry()
    a, b = pipe_ends(registry, PIPE)
    junction = PipeAttachmentObj(1, "Build_PipelineJunction_Cross_C", Pose(0, 0, 500, 0))
    water = PipeRun(2, PIPE, straight((-500, 0, 500), (-100, 0, 500)), "water", Fraction(1))
    oil = PipeRun(3, PIPE, straight((100, 0, 500), (500, 0, 500)), "crude-oil", Fraction(1))
    placement = SfyPlacement(
        designer("mk3", registry),
        pipes=(water, oil),
        pipe_attachments=(junction,),
        links=(Link((1, "Connection0"), (2, b)), Link((3, a), (1, "Connection1"))),
    )
    report = validate(placement, None, registry, only={"pipe.fluid_requirements"})
    assert any(set(f.objects) == {2, 3} for f in report.errors)


def test_vertical_turn_is_curvature_checked_in_three_dimensions() -> None:
    registry = load_registry()
    points = (
        ((0.0, 0.0, 500.0), (0.0, 0.0, 500.0), (0.0, 0.0, 500.0)),
        ((0.0, 0.0, 1000.0), (0.0, 0.0, 500.0), (500.0, 0.0, 0.0)),
        ((500.0, 0.0, 1000.0), (500.0, 0.0, 0.0), (500.0, 0.0, 0.0)),
    )
    run = PipeRun(1, PIPE, points)
    report = validate(
        SfyPlacement(designer("mk3", registry), pipes=(run,)),
        None,
        registry,
        only={"pipe.curvature"},
    )
    assert report.errors and report.errors[0].check == "pipe.curvature"


def _through_hole() -> tuple[Registry, SfyPlacement]:
    registry = load_registry()
    start, _ = pipe_ends(registry, PIPE)
    pipe = PipeRun(
        1,
        PIPE,
        straight((0, 0, 250), (0, 0, 1000)),
        "water",
        Fraction(1),
        boundary_start=True,
        boundary_end=True,
        snapped_passthroughs=(2, None),
    )
    hole = PassthroughObj(
        2, "Build_FoundationPassthrough_Pipe_C", Pose(0, 0, 250, 0), 100, top_connection=(1, start)
    )
    slab = FoundationObj(3, "Build_Foundation_8x1_01_C", Pose(0, 0, 250, 0))
    return registry, SfyPlacement(
        designer("mk3", registry), pipes=(pipe,), passthroughs=(hole,), foundations=(slab,)
    )


def test_hole_must_reciprocally_own_exact_endpoint_at_its_center() -> None:
    registry, placement = _through_hole()
    assert validate(placement, None, registry, only={"ports.passthrough"}).errors == ()
    wrong = replace(placement.passthroughs[0], top_connection=None)
    report = validate(
        replace(placement, passthroughs=(wrong,)), None, registry, only={"ports.passthrough"}
    )
    assert report.errors
    shifted = replace(placement.passthroughs[0], pose=Pose(10, 0, 250, 0))
    report = validate(
        replace(placement, passthroughs=(shifted,)), None, registry, only={"ports.passthrough"}
    )
    assert any("displaced" in f.message for f in report.errors)


def test_hole_thickness_must_match_the_slab_it_crosses() -> None:
    registry, placement = _through_hole()
    wrong = replace(placement.passthroughs[0], thickness_cm=200)
    report = validate(
        replace(placement, passthroughs=(wrong,)), None, registry, only={"ports.passthrough"}
    )
    assert any("thickness" in f.message for f in report.errors)


def test_pump_head_is_measured_forward_from_output_not_link_order() -> None:
    registry = load_registry()
    start, _ = pipe_ends(registry, PIPE)
    pump = PipeAttachmentObj(1, "Build_PipelinePump_C", Pose(0, 0, 500, 0, 90))
    # Native Mk1 output is actor +195 cm along its fully rotated +X.
    run = PipeRun(2, PIPE, straight((0, 0, 695), (0, 0, 3000)), "water", Fraction(1))
    placement = SfyPlacement(
        designer("mk3", registry),
        pipe_attachments=(pump,),
        pipes=(run,),
        links=(Link((2, start), (1, "Connection1")),),
    )
    report = validate(placement, None, registry, only={"pipe.head"})
    assert any(f.detail.get("required_head_m") == 23.05 for f in report.errors)
    shorter = replace(run, points=straight((0, 0, 695), (0, 0, 2500)))
    assert (
        validate(replace(placement, pipes=(shorter,)), None, registry, only={"pipe.head"}).errors
        == ()
    )


def test_external_source_must_declare_required_priming_head() -> None:
    registry = load_registry()
    a, b = pipe_ends(registry, PIPE)
    run = PipeRun(
        1,
        PIPE,
        straight((0, 0, 250), (0, 0, 1250)),
        "water",
        Fraction(1),
        boundary_start=True,
        boundary_end=True,
    )
    lane = StackLane("water", "pipe", (1, a), (1, b), Fraction(1), Fraction(), Fraction(5))
    placement = SfyPlacement(designer("mk3", registry), pipes=(run,), stack_lanes=(lane,))
    report = validate(placement, None, registry, only={"pipe.head"})
    assert any(f.detail.get("required_head_m") == 10 for f in report.errors)
    declared = replace(lane, required_input_head_m=10)
    report = validate(
        replace(placement, stack_lanes=(declared,)), None, registry, only={"pipe.head"}
    )
    assert report.errors == ()


def test_manual_bridge_between_two_holes_uses_their_exact_separation() -> None:
    registry = load_registry()
    cls = "Build_ConveyorLiftMk1_C"
    entry, exit_ = belt_ends(registry, cls)
    lift = LiftObj(1, cls, Pose(0, 0, 50, 0), 400, snapped_passthroughs=(2, 3))
    lower = PassthroughObj(
        2,
        "Build_FoundationPassthrough_Lift_C",
        Pose(0, 0, 50, 0),
        100,
        top_connection=(1, entry),
    )
    upper = PassthroughObj(
        3,
        "Build_FoundationPassthrough_Lift_C",
        Pose(0, 0, 450, 0),
        100,
        bottom_connection=(1, exit_),
    )
    placement = SfyPlacement(
        designer("mk3", registry),
        lifts=(lift,),
        passthroughs=(lower, upper),
        foundations=(
            FoundationObj(4, "Build_Foundation_8x1_01_C", Pose(0, 0, 50, 0)),
            FoundationObj(5, "Build_Foundation_8x1_01_C", Pose(0, 0, 450, 0)),
        ),
    )
    checks = {"lift.step", "lift.height", "ports.passthrough", "belt.capsule"}
    assert validate(placement, None, registry, only=checks).errors == ()
    displaced = replace(lift, height_cm=450)
    assert any(
        finding.check == "ports.passthrough"
        for finding in validate(
            replace(placement, lifts=(displaced,)), None, registry, only=checks
        ).errors
    )


def test_snapped_lift_remainder_is_half_thickness_in_either_serialized_slot() -> None:
    registry = load_registry()
    hole = PassthroughObj(2, "Build_FoundationPassthrough_Lift_C", Pose(0, 0, 250, 0), 100)
    for slots in ((2, None), (None, 2)):
        lift = LiftObj(
            1, "Build_ConveyorLiftMk1_C", Pose(0, 0, 250, 0), 450, snapped_passthroughs=slots
        )
        placement = SfyPlacement(designer("mk3", registry), lifts=(lift,), passthroughs=(hole,))
        assert validate(placement, None, registry, only={"lift.step", "lift.height"}).errors == ()
        off_grid = replace(lift, height_cm=430)
        assert any(
            f.check == "lift.step"
            for f in validate(
                replace(placement, lifts=(off_grid,)), None, registry, only={"lift.step"}
            ).errors
        )
    plain = replace(lift, snapped_passthroughs=(None, None))
    assert validate(replace(placement, lifts=(plain,)), None, registry, only={"lift.step"}).errors


def test_cosmetic_pipe_variant_shares_funded_tier_but_faster_mark_does_not() -> None:
    registry, spec, placement = _plastic()
    cosmetic = replace(
        placement,
        pipes=tuple(
            replace(pipe, class_name="Build_Pipeline_NoIndicator_C") for pipe in placement.pipes
        ),
    )
    assert validate(cosmetic, spec, registry, only={"flow.capacity"}).errors == ()
    upgraded = replace(
        cosmetic,
        pipes=tuple(
            replace(pipe, class_name="Build_PipelineMK2_NoIndicator_C") for pipe in placement.pipes
        ),
    )
    assert any(
        "not funded" in finding.message
        for finding in validate(upgraded, spec, registry, only={"flow.capacity"}).errors
    )


def test_external_priming_head_stops_at_first_pump_not_its_downstream_peak() -> None:
    registry = load_registry()
    start, end = pipe_ends(registry, PIPE)
    pump = PipeAttachmentObj(1, "Build_PipelinePump_C", Pose(0, 0, 1000, 0, 90))
    inlet = PipeRun(2, PIPE, straight((0, 0, 250), (0, 0, 850)), "water", Fraction(1))
    outlet = PipeRun(3, PIPE, straight((0, 0, 1195), (0, 0, 4195)), "water", Fraction(1))
    placement = SfyPlacement(
        designer("mk3", registry),
        pipe_attachments=(pump,),
        pipes=(inlet, outlet),
        links=(Link((1, "Connection0"), (2, end)), Link((3, start), (1, "Connection1"))),
    )
    assert required_input_head_m(placement, (2, start), registry) == 6


def test_head_uses_pipe_ends_not_an_interior_hump() -> None:
    registry = load_registry()
    start, end = pipe_ends(registry, PIPE)
    points: tuple[SplinePoint, ...] = (
        ((-1000, 0, 250), (1000, 0, 0), (1000, 0, 0)),
        ((0, 0, 1750), (1000, 0, 0), (1000, 0, 0)),
        ((1000, 0, 250), (1000, 0, 0), (1000, 0, 0)),
    )
    run = PipeRun(1, PIPE, points, "water", Fraction(1))
    placement = SfyPlacement(designer("mk3", registry), pipes=(run,))
    assert required_input_head_m(placement, (1, start), registry) == 0

    # A joint at the crest makes that elevation a real pipe endpoint.
    split = replace(
        placement,
        pipes=(replace(run, points=points[:2]), replace(run, id=2, points=points[1:])),
        links=(Link((1, end), (2, start)),),
    )
    assert required_input_head_m(split, (1, start), registry) == 15


def test_unused_upper_junction_port_does_not_add_head() -> None:
    registry = load_registry()
    start, end = pipe_ends(registry, PIPE)
    junction = PipeAttachmentObj(1, "Build_PipelineJunction_Cross_C", Pose(0, 0, 500, 0, 0, 90))
    inlet = PipeRun(2, PIPE, straight((-600, 0, 500), (-100, 0, 500)))
    outlet = PipeRun(3, PIPE, straight((0, 0, 400), (0, 0, 100)))
    placement = SfyPlacement(
        designer("mk2", registry),
        pipe_attachments=(junction,),
        pipes=(inlet, outlet),
        links=(Link((2, end), (1, "Connection0")), Link((1, "Connection2"), (3, start))),
    )
    assert not validate(placement, None, registry, only={"ports.position"}).errors
    assert required_input_head_m(placement, (2, start), registry) == 0

    # Connecting the upper mouth makes the new rising pipe's ends count.
    upper = PipeRun(4, PIPE, straight((0, 0, 600), (0, 0, 900)))
    connected = replace(
        placement,
        pipes=(*placement.pipes, upper),
        links=(*placement.links, Link((1, "Connection3"), (4, start))),
    )
    assert not validate(connected, None, registry, only={"ports.position"}).errors
    assert required_input_head_m(connected, (2, start), registry) == 4


def test_fluid_export_capacity_must_reach_the_bottom_not_just_the_top() -> None:
    registry, spec, placement = _plastic()
    start, end = pipe_ends(registry, PIPE)
    drain = replace(placement.pipes[1], boundary_end=False)
    bottom = PipeRun(
        4,
        PIPE,
        straight(drain.end, (-200, -1400, 50)),
        "heavy-oil-residue",
        Fraction(1, 3),
        boundary_end=True,
    )
    placement = replace(
        placement,
        pipes=(placement.pipes[0], drain, bottom),
        links=(*placement.links, Link((3, end), (4, start))),
        stack_lanes=(
            StackLane(
                "crude-oil",
                "pipe",
                (2, start),
                (2, end),
                Fraction(1, 2),
                Fraction(),
                Fraction(1, 2),
            ),
            StackLane(
                "heavy-oil-residue",
                "pipe",
                (4, end),
                (3, start),
                Fraction(),
                Fraction(1, 3),
                Fraction(1, 3),
            ),
        ),
    )
    assert not validate(placement, spec, registry, only={"flow.capacity"}).errors
    restricted = replace(bottom, cubic_metres_per_second=Fraction(1, 6))
    report = validate(
        replace(placement, pipes=(*placement.pipes[:2], restricted)),
        spec,
        registry,
        only={"flow.capacity"},
    )
    assert any(
        finding.detail.get("item") == "heavy-oil-residue"
        and finding.detail.get("supplied") == "1/6"
        and finding.detail.get("needed") == "1/3"
        for finding in report.errors
    )


def test_output_gravity_drain_does_not_need_head_to_fill_the_roof_pass_through() -> None:
    registry, spec, placement = _plastic()
    start, end = pipe_ends(registry, PIPE)
    junction = PipeAttachmentObj(
        6, "Build_PipelineJunction_Cross_C", Pose(-200, -1400, 275, 0, 0, 90)
    )
    collector = replace(
        placement.pipes[1],
        points=straight(placement.pipes[1].start, (-300, -1400, 275)),
        boundary_end=False,
    )
    upper = PipeRun(
        4,
        PIPE,
        straight((-200, -1400, 1000), (-200, -1400, 375)),
        "heavy-oil-residue",
        Fraction(1, 3),
        boundary_start=True,
    )
    # The lower elbow may rise again, provided it stays below the producer.
    lower = PipeRun(
        5,
        PIPE,
        straight((-200, -1400, 175), (-400, -1400, 200)),
        "heavy-oil-residue",
        Fraction(1, 3),
    )
    bottom = PipeRun(
        7,
        PIPE,
        straight((-400, -1400, 200), (-400, -1400, 50)),
        "heavy-oil-residue",
        Fraction(1, 3),
        boundary_end=True,
    )
    placement = replace(
        placement,
        pipes=(placement.pipes[0], collector, upper, lower, bottom),
        pipe_attachments=(junction,),
        links=(
            *placement.links,
            Link((3, end), (6, "Connection0")),
            Link((4, end), (6, "Connection3")),
            Link((6, "Connection2"), (5, start)),
            Link((5, end), (7, start)),
        ),
        stack_lanes=(
            StackLane(
                "heavy-oil-residue",
                "pipe",
                (7, end),
                (4, start),
                Fraction(),
                Fraction(1, 3),
                Fraction(1, 3),
            ),
        ),
    )
    assert not validate(placement, spec, registry, only={"pipe.head"}).skipped
    disconnected = replace(placement, links=placement.links[:-1])
    assert "pipe.head" in validate(disconnected, spec, registry, only={"pipe.head"}).skipped

    # A gravity export must not certify a second, elevated first-pump inlet.
    pump = PipeAttachmentObj(8, "Build_PipelinePumpMk2_C", Pose(0, 0, 1000, 0))
    inlet = next(
        port
        for port in registry.buildables[pump.class_name].ports
        if port.kind == "pipe" and port.direction == "input"
    )
    elevated = replace(
        placement,
        pipe_attachments=(*placement.pipe_attachments, pump),
        links=(*placement.links, Link((4, start), (pump.id, inlet.name))),
    )
    assert "pipe.head" in validate(elevated, spec, registry, only={"pipe.head"}).skipped
