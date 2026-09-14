"""The committed registry carries the ports and limits the placer needs.

These read ``data/registry.json`` as shipped, so they are the acceptance for the
extractor in ``tools/sfy-extract`` and the merge in ``scripts/sfy_registry.py``.
"""

from dataclasses import fields

from flab2bp.sfy.registry import LIMIT_SOURCES, Limits, load_registry

# The seven limits that are native C++ constructor defaults the install ships
# neither as a cooked asset nor as a header initialiser (see ``UNFILLABLE`` in
# ``scripts/sfy_registry.py``). They come from ``measured.json``, the envelope
# ``scripts/sfy_measure_limits.py`` takes from the blueprint corpus.
MEASURED = (
    "belt_bend_radius_cm",
    "belt_max_incline_deg",
    "hologram_grid_cm",
    "lift_max_cm",
    "lift_min_cm",
    "lift_min_vertical_cm",
    "lift_step_cm",
)


def test_registry_has_ports_for_the_core_machines():
    reg = load_registry()
    ctor = reg.buildables["Build_ConstructorMk1_C"]
    belts = [p for p in ctor.ports if p.kind == "belt"]
    assert {p.direction for p in belts} == {"input", "output"}
    assert len(belts) == 2
    power = [p for p in ctor.ports if p.kind == "power"]
    assert len(power) == 1
    smelter = reg.buildables["Build_SmelterMk1_C"]
    assert len([p for p in smelter.ports if p.kind == "belt"]) == 2
    assembler = reg.buildables["Build_AssemblerMk1_C"]
    assert len([p for p in assembler.ports if p.kind == "belt" and p.direction == "input"]) == 2


def test_registry_limits_are_filled_from_the_assets():
    lim = load_registry().limits
    assert lim.belt_max_spline_cm == 5600.1
    assert lim.pipe_max_spline_cm == 5600.1
    assert lim.pipe_bend_radius_cm == 100.0
    assert lim.hologram_rotation_step_deg == 90.0
    assert lim.wire_max_cm["Build_PowerLine_C"] > 0


def test_every_limit_is_filled_and_says_where_it_came_from():
    reg = load_registry()
    unset = [f.name for f in fields(Limits) if getattr(reg.limits, f.name) in (None, {})]
    assert unset == []
    assert set(reg.limits_sources) == {f.name for f in fields(Limits)}
    assert set(reg.limits_sources.values()) <= set(LIMIT_SOURCES)
    assert {k for k, v in reg.limits_sources.items() if v == "measured"} == set(MEASURED)


def test_belt_bend_radius_and_incline_are_measured_from_the_corpus():
    lim = load_registry().limits
    # The tightest horizontal turn in 617 curved belts. The bulk of the corpus
    # sits at 197-199 cm, which is the game's own bend radius; five belts in
    # one pre-1.0 blueprint go tighter, and 111 cm is the tightest of those.
    assert lim.belt_bend_radius_cm is not None
    assert 100.0 < lim.belt_bend_radius_cm <= 200.0
    # A belt climbs; it does not go vertical -- that is what a lift is for.
    assert lim.belt_max_incline_deg is not None
    assert 0.0 < lim.belt_max_incline_deg < 90.0


def test_conveyor_lift_heights_are_measured_from_the_corpus():
    lim = load_registry().limits
    assert lim.lift_min_cm is not None and lim.lift_min_cm > 0
    assert lim.lift_max_cm is not None and lim.lift_max_cm > lim.lift_min_cm
    assert lim.lift_step_cm is not None and lim.lift_step_cm > 0
    assert lim.lift_min_cm % lim.lift_step_cm == 0
    assert lim.lift_max_cm % lim.lift_step_cm == 0
    # A lift wired straight to a machine or splitter port cannot be shorter
    # than one wired to a belt, which is free to meet it anywhere.
    assert lim.lift_min_vertical_cm is not None
    assert lim.lift_min_vertical_cm >= lim.lift_min_cm


def test_the_hologram_grid_is_the_measured_snap_size():
    lim = load_registry().limits
    # 50 cm: the gcd of every axis-aligned grid building's coordinates across
    # the corpus. 100 is what most of them are on, but foundation origins are
    # mesh-centred at z = 50 and a handful of machines sit on a 50 cm offset
    # horizontally too, so 50 is the coarsest grid that holds all of them.
    assert lim.hologram_grid_cm == 50.0
    assert lim.hologram_rotation_step_deg == 90.0


def test_splitter_has_one_input_and_three_outputs():
    reg = load_registry()
    sp = reg.buildables["Build_ConveyorAttachmentSplitter_C"]
    belts = [p for p in sp.ports if p.kind == "belt"]
    assert sum(p.direction == "input" for p in belts) == 1
    assert sum(p.direction == "output" for p in belts) == 3


def test_pipe_ports_carry_their_own_direction_enum():
    refinery = load_registry().buildables["Build_OilRefinery_C"]
    pipes = {p.name: p.direction for p in refinery.ports if p.kind == "pipe"}
    assert pipes == {"PipeInputFactory": "input", "PipeOutputFactory": "output"}
