"""The committed registry carries the ports and limits the placer needs.

These read ``data/registry.json`` as shipped, so they are the acceptance for the
extractor in ``tools/sfy-extract`` and the merge in ``scripts/sfy_registry.py``.
"""

import pytest

from flab2bp.sfy.registry import load_registry

# Several hologram limits are set by native C++ constructors that the install
# ships neither as cooked asset defaults nor as header initialisers; see
# ``UNFILLABLE`` in ``scripts/sfy_registry.py``. The tests below are strict
# xfails so that a game build which does start shipping them fails loudly here.
NO_NATIVE_DEFAULTS = (
    "the value is a native C++ constructor default, in neither the assets nor the headers"
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


@pytest.mark.xfail(
    strict=True, reason=f"AFGConveyorBeltHologram::mBendRadius -- {NO_NATIVE_DEFAULTS}"
)
def test_belt_bend_radius_is_in_the_game_data():
    lim = load_registry().limits
    assert lim.belt_bend_radius_cm is not None and lim.belt_bend_radius_cm > 0


@pytest.mark.xfail(
    strict=True, reason=f"AFGConveyorLiftHologram height members -- {NO_NATIVE_DEFAULTS}"
)
def test_conveyor_lift_heights_are_in_the_game_data():
    lim = load_registry().limits
    assert (
        lim.lift_step_cm is not None and lim.lift_min_cm is not None and lim.lift_max_cm is not None
    )


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
