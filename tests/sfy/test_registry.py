"""The committed registry carries the ports and limits the placer needs.

These read ``data/registry.json`` as shipped, so they are the acceptance for the
extractor in ``tools/sfy-extract`` and the merge in ``scripts/sfy_registry.py``.
"""

import json
from dataclasses import fields
from pathlib import Path

import pytest

from flab2bp.sfy import docs
from flab2bp.sfy.registry import LIMIT_SOURCES, Limits, load_registry

# The limits that are native C++ constructor immediates: no cooked asset and no
# header initialiser carries them, so ``tools/sfy-native`` reads them out of the
# shipped DLL (see ``NATIVE_LIMITS`` in ``scripts/sfy_registry.py``).
BINARY = (
    "belt_bend_radius_cm",
    "belt_max_incline_deg",
    "belt_max_spline_cm",
    "hologram_grid_cm",
    "lift_step_cm",
    "pipe_bend_radius_2d_cm",
    "pipe_max_spline_cm",
    "pipe_min_bend_radius_cm",
)

# The three the binary does not state either: ``AFGConveyorLiftHologram``
# computes all three in ``BeginPlay`` from the lift buildable's own mesh height,
# so they fall through to ``measured.json``, the envelope
# ``scripts/sfy_measure_limits.py`` takes from the blueprint corpus.
MEASURED = ("lift_max_cm", "lift_min_cm", "lift_min_vertical_cm")

MEASURED_JSON = Path(docs.__file__).parent / "data" / "measured.json"


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


def test_registry_limits_carry_the_numbers_the_game_ships():
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


def test_the_native_limits_come_from_the_binary():
    reg = load_registry()
    assert {k for k, v in reg.limits_sources.items() if v == "binary"} == set(BINARY)
    for key in BINARY:
        assert getattr(reg.limits, key) > 0, key
    # Nothing falls back to the header any more: the binary states all four the
    # headers do, and ``scripts/sfy_registry.py`` holds the two to each other.
    assert "header" not in reg.limits_sources.values()


def test_belt_bend_radius_and_incline_come_from_the_hologram_constructor():
    lim = load_registry().limits
    # AFGConveyorBeltHologram's constructor stores both as immediates:
    # mBendRadius = 199.0f and mMaxIncline = 35.0f. See tools/sfy-native's
    # README for the two instructions.
    assert lim.belt_bend_radius_cm == 199.0
    assert lim.belt_max_incline_deg == 35.0


def test_conveyor_lift_heights_are_measured_because_the_game_computes_them():
    reg = load_registry()
    lim = reg.limits
    # Only the step is a constant. AFGConveyorLiftHologram::BeginPlay works the
    # three heights out from the lift buildable's mesh height, so the corpus
    # envelope is the only number there is for them.
    assert reg.limits_sources["lift_step_cm"] == "binary"
    assert lim.lift_step_cm == 100.0
    for key in ("lift_min_cm", "lift_max_cm", "lift_min_vertical_cm"):
        assert reg.limits_sources[key] == "measured", key
    assert 0 < lim.lift_min_cm < lim.lift_max_cm
    # A lift wired straight to a machine or splitter port cannot be shorter
    # than one wired to a belt, which is free to meet it anywhere.
    assert lim.lift_min_vertical_cm >= lim.lift_min_cm


def test_the_hologram_grid_is_the_native_snap_size():
    lim = load_registry().limits
    # AFGBuildableHologram's constructor stores mGridSnapSize = 100.0f. The
    # corpus gcd said 50, which Task 11 flagged as possibly mesh offsets rather
    # than the snap size; the binary settles it. Individual holograms still
    # override the UPROPERTY -- assets.json has Holo_PowerPole_C and
    # Holo_StreetLight_C at 50 -- so this is the global default, not a rule
    # every hologram obeys.
    assert lim.hologram_grid_cm == 100.0
    assert lim.hologram_rotation_step_deg == 90.0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The corpus is wider than the game's own limits, on both belt keys. "
        "measured.json's belt_bend_radius_cm is 111.0758 cm against the "
        "binary's 199.0, and belt_max_incline_deg is 39.8167 degrees against "
        "the binary's 35.0. Both extremes come from pre-1.0 blueprints (the "
        "radius from five belts in production-8.sbp, the incline from "
        "production-2.sbp), so the likeliest reading is that 1.2.0 tightened "
        "the limits and the corpus still holds blueprints built under the old "
        "ones. The binary values are what the registry carries. Pending a "
        "ruling on whether to drop those fixtures or keep the envelope wider "
        "than the game."
    ),
)
def test_measured_envelope_lies_inside_binary_limits():
    """Task 11's corpus measurements can never be wider than the game's constants."""
    measured = json.loads(MEASURED_JSON.read_text())["limits"]
    lim = load_registry().limits
    assert measured["belt_bend_radius_cm"]["value"] >= lim.belt_bend_radius_cm - 1.0
    assert measured["belt_max_incline_deg"]["value"] <= lim.belt_max_incline_deg + 0.5
    assert measured["lift_min_cm"]["value"] >= lim.lift_min_cm - 1.0
    assert measured["lift_max_cm"]["value"] <= lim.lift_max_cm + 1.0


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
