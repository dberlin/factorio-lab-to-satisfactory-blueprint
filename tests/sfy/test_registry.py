"""The committed registry carries the ports and limits the placer needs.

These read ``data/registry.json`` as shipped, so they are the acceptance for the
extractor in ``tools/sfy-extract`` and the merge in ``scripts/sfy_registry.py``.
"""

import hashlib
import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from flab2bp.sfy import docs
from flab2bp.sfy.registry import (
    FLOW_SOURCES,
    LIMIT_SOURCES,
    MAX_CONNECTIONS_SOURCES,
    PORT_DIRECTION_SOURCES,
    Limits,
    RegistryError,
    load_registry,
)
from flab2bp.sfy.rules import RULE_STATUSES, load_rules

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
    # Not a hologram member: AFGBuildableSubsystem's constructor, which is what
    # every factory's overclock slot count comes from because no shipped class
    # overrides it and the cooked subsystem Blueprint does not either.
    "potential_shard_slots_default",
)

# The three the binary states no constant for: ``AFGConveyorLiftHologram``
# computes all three in ``BeginPlay`` from the lift buildable's mesh height H.
# The formula is in the machine code, H is in Docs.json, so the merge evaluates
# it rather than falling back to the corpus.
LIFT_DERIVED = ("lift_max_cm", "lift_min_cm", "lift_min_vertical_cm")
BINARY_DERIVED = ("belt_min_length_cm", *LIFT_DERIVED)

# Every lift mark's Docs.json mMeshHeight, and the three heights that follow.
MESH_HEIGHT_CM = 200.0

# Every belt mark's Docs.json mMeshLength, which is what
# AFGConveyorBeltHologram::ValidateMinLength multiplies by 0.5001.
MESH_LENGTH_CM = 200.0


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


def test_no_limit_is_filled_from_the_blueprint_corpus():
    """No limit is measured out of blueprints, because none may be.

    A measured number is an envelope -- what something was once observed to
    produce -- and using one as a limit lets an old blueprint's outlier become
    the rule. Every limit is read from game data, so nothing is tagged
    ``measured`` and ``measured`` is not a source a registry may carry.
    """
    assert [k for k, v in load_registry().limits_sources.items() if v == "measured"] == []


def test_the_two_limits_that_are_not_read_from_the_game_say_so():
    reg = load_registry()
    # 90 degrees is this project's constant: no asset, header or constructor
    # immediate states the build gun's rotation step.
    assert reg.limits_sources["hologram_rotation_step_deg"] == "constant"
    assert "reason" in reg.provenance["limits"]["hologram_rotation_step_deg"]
    # The wire lengths travel through assets.json, but tools/sfy-extract reads
    # them out of the install's own Docs.json dump, not out of a cooked asset.
    assert reg.limits_sources["wire_max_cm"] == "docs"


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


def test_conveyor_lift_heights_come_from_the_formula_in_the_binary():
    reg = load_registry()
    lim = reg.limits
    # Only the step is an immediate. AFGConveyorLiftHologram::BeginPlay works
    # the three heights out from the lift buildable's mesh height H, and the
    # merge applies that arithmetic to Docs.json's mMeshHeight.
    assert reg.limits_sources["lift_step_cm"] == "binary"
    assert lim.lift_step_cm == 100.0
    for key in LIFT_DERIVED:
        assert reg.limits_sources[key] == "binary-derived", key
        assert reg.provenance["limits"][key]["H"] == MESH_HEIGHT_CM
    assert lim.lift_min_cm == 2.0 * MESH_HEIGHT_CM
    assert lim.lift_max_cm == 24.0 * MESH_HEIGHT_CM
    assert lim.lift_min_vertical_cm == MESH_HEIGHT_CM - 50.0


def test_the_minimum_belt_length_is_what_validate_min_length_compares_against():
    """The floor is a product, not a constant: 0.5001 times the mark's mMeshLength.

    ``AFGConveyorBeltHologram::ValidateMinLength`` loads the belt's cached
    ``mMeshLength`` and multiplies it by the ``.rdata`` float 0.5001 before the
    ``comiss`` that decides (``mulss xmm7,[132F848h]`` at 0xaa58b2), so there is
    no centimetre immediate anywhere in the binary to read -- the limit has the
    same standing as the three lift heights, ``binary-derived``, and the merge
    holds the multiplier to the ``belt.min_length`` rule's own evidence line.
    """
    reg = load_registry()
    assert reg.limits_sources["belt_min_length_cm"] == "binary-derived"
    assert reg.limits.belt_min_length_cm == 0.5001 * MESH_LENGTH_CM
    where = reg.provenance["limits"]["belt_min_length_cm"]
    assert where["formula"] == "0.5001 * L"
    assert where["L"] == MESH_LENGTH_CM
    assert "mMeshLength" in where["L_from"]
    assert "0xaa58b2" in where["evaluated_in"] and "f32=0.5001" in where["evaluated_in"]
    # Every belt mark is charged against the same mesh length, which is why one
    # global floor is honest; a mark that differed would fail the merge.
    marks = {
        c: b.mesh_length_cm for c, b in reg.buildables.items() if c.startswith("Build_ConveyorBelt")
    }
    assert len(marks) == 6 and set(marks.values()) == {MESH_LENGTH_CM}


def test_the_conveyor_mesh_boxes_are_the_cooked_static_meshes_own_bounds():
    """``mesh_bounds_cm`` is ``Origin -+ BoxExtent`` of the mesh the class points at.

    A belt states its mesh in ``mMesh`` and a lift its repeated mid section in
    ``mMidMesh``, and the box is the cooked ``UStaticMesh``'s own
    ``RenderData.Bounds`` -- game data, never a box measured off a blueprint.
    The Mk1 belt's mesh runs the full 200 cm of one segment along X and is
    178.25 cm wide, which is **wider than the clearance the game actually
    tests**: ``belt.clearance`` lays boxes 158 cm wide along the spline. The two
    are different facts and the registry carries them separately.
    """
    reg = load_registry()
    belt = reg.buildables["Build_ConveyorBeltMk1_C"]
    assert belt.mesh_bounds_property == "mMesh"
    low, high = belt.mesh_bounds_cm
    assert (low[0], high[0]) == (0.0, MESH_LENGTH_CM)
    assert high[1] == pytest.approx(89.1260986328125) and low[1] == -high[1]
    lift = reg.buildables["Build_ConveyorLiftMk1_C"]
    assert lift.mesh_bounds_property == "mMidMesh"
    assert lift.mesh_bounds_cm[1][2] == pytest.approx(219.93637084960938)
    # Every belt and lift mark carries one, and each says which asset it read.
    marks = [f"Build_Conveyor{kind}Mk{n}_C" for kind in ("Belt", "Lift") for n in range(1, 7)]
    assert all(reg.buildables[c].mesh_bounds_cm is not None for c in marks)
    paths = reg.provenance["mesh_bounds"]["applied_to"]
    assert paths["Build_ConveyorBeltMk1_C"]["mesh"].endswith("SM_ConveyorBelt_Mk1")
    assert all(paths[c]["mesh"].startswith("/Game/") for c in marks)
    assert reg.provenance["mesh_bounds"]["source"] == "assets"


def test_a_machine_power_input_takes_the_wire_count_its_archetype_sets():
    """An absent ``mMaxNumConnectionLinks`` is the constructor's 1, not "unknown".

    A cooked asset omits the property wherever it equals the component
    archetype's value, and for a machine's power input the archetype is
    ``UFGCircuitConnectionComponent``'s own C++ constructor, which the pak does
    not carry. ``tools/sfy-native`` reads the store out of the shipped DLL, and
    the merge fills every silent power port from it -- the same hand-off the
    port directions make, and the reason a machine now says "one wire" instead
    of saying nothing.
    """
    reg = load_registry()
    native = reg.provenance["max_connections"]
    assert native["native_default"] == 1
    assert native["class"] == "UFGCircuitConnectionComponent"
    assert native["member"] == "mMaxNumConnectionLinks"
    assert "mov dword ptr [rbx+280h],1" in native["evidence"]
    assert native["size_source"] == "pdata"
    for machine in (
        "Build_ConstructorMk1_C",
        "Build_AssemblerMk1_C",
        "Build_SmelterMk1_C",
        "Build_FoundryMk1_C",
        "Build_ManufacturerMk1_C",
    ):
        (power,) = [p for p in reg.buildables[machine].ports if p.kind == "power"]
        assert (power.max_connections, power.max_connections_source) == (1, "native"), machine
    # The poles still win with their own Blueprint's number.
    (pole,) = [p for p in reg.buildables["Build_PowerPoleMk3_C"].ports if p.kind == "power"]
    assert (pole.max_connections, pole.max_connections_source) == (10, "asset")


def test_the_overclock_and_somersloop_numbers_come_from_the_games_own_data():
    """What a shard unlocks, and how many slots there are to put one in.

    ``UFGPowerShardDescriptor::GetBoostValue`` returns the descriptor's own
    ``mExtraPotential``/``mExtraProductionBoost``, which Docs.json states; the
    slot counts are the buildable subsystem's defaults, because
    ``AFGBuildableFactory::BeginPlay`` copies them onto every class whose
    ``mOverride*`` bit is clear, and no shipped class sets the overclock one.
    The class's own ``potential_shard_slots`` is 0 everywhere and is *not* the
    number of slots -- reading it as one would say nothing can be overclocked.
    """
    reg = load_registry()
    lim = reg.limits
    assert (lim.potential_per_shard, reg.limits_sources["potential_per_shard"]) == (0.5, "docs")
    assert (lim.production_boost_per_slot, reg.limits_sources["production_boost_per_slot"]) == (
        1.0,
        "docs",
    )
    shard = reg.provenance["limits"]["potential_per_shard"]
    assert (shard["descriptor"], shard["property"]) == ("Desc_CrystalShard_C", "mExtraPotential")
    sloop = reg.provenance["limits"]["production_boost_per_slot"]
    assert (sloop["descriptor"], sloop["property"]) == ("Desc_WAT1_C", "mExtraProductionBoost")
    # Three overclock slots from the native constructor, one somersloop slot
    # because the cooked subsystem Blueprint overrides the native four down to 1.
    assert lim.potential_shard_slots_default == 3
    assert reg.limits_sources["potential_shard_slots_default"] == "binary"
    assert lim.production_boost_slots_default == 1
    assert reg.limits_sources["production_boost_slots_default"] == "assets"
    where = reg.provenance["limits"]["production_boost_slots_default"]
    assert where["class"] == "BP_BuildableSubsystem_C" and where["overridden_by_the_blueprint"]
    assert not reg.provenance["limits"]["potential_shard_slots_default"][
        "overridden_by_the_blueprint"
    ]
    ctor = reg.buildables["Build_ConstructorMk1_C"]
    assert ctor.potential_shard_slots == 0 and ctor.potential_shard_slots_override is False
    # The ceiling the game works out: 1.0 + three shards of half a potential each.
    assert ctor.max_potential + lim.potential_shard_slots_default * lim.potential_per_shard == 2.5


def test_the_power_and_boost_exponents_are_per_class_and_not_a_limit():
    """The game states no global exponent, so neither exponent is in ``limits``.

    ``mPowerConsumptionExponent`` is 1.321929 on every manufacturer and
    extractor and 1.6 on everything else, so a single number in ``limits`` would
    be an invented one; both exponents ride on the buildable that states them.
    The somersloop multiplier is per class for the same reason, and it is what
    makes one slot and four slots both double a machine's output.
    """
    reg = load_registry()
    assert not any(f.name.endswith("exponent") for f in fields(Limits))
    ctor = reg.buildables["Build_ConstructorMk1_C"]
    assert ctor.power_exponent == pytest.approx(1.321929)
    assert ctor.production_boost_power_exponent == 2.0
    assert reg.buildables["Build_TradingPost_C"].power_exponent == 1.6
    boost = {
        c: (
            reg.buildables[c].production_boost_multiplier,
            reg.buildables[c].production_boost_slots,
            reg.buildables[c].production_boost_slots_override,
        )
        for c in (
            "Build_ConstructorMk1_C",
            "Build_AssemblerMk1_C",
            "Build_FoundryMk1_C",
            "Build_ManufacturerMk1_C",
        )
    }
    assert boost == {
        "Build_ConstructorMk1_C": (1.0, 1, False),
        "Build_AssemblerMk1_C": (0.5, 2, True),
        "Build_FoundryMk1_C": (0.5, 2, True),
        "Build_ManufacturerMk1_C": (0.25, 4, True),
    }
    # A full set of somersloops doubles the output whatever the slot count.
    for class_name, (multiplier, slots, override) in boost.items():
        filled = slots if override else reg.limits.production_boost_slots_default
        base = reg.buildables[class_name].base_production_boost
        assert base + filled * reg.limits.production_boost_per_slot * multiplier == 2.0


def test_the_lift_height_ladder_is_self_consistent():
    """The ladder of legal lift heights, pinned so it cannot drift apart again.

    A lift that meets a vertical connection may be shorter than the ordinary
    minimum -- that is the whole point of the third height -- and every height
    in between is a whole number of steps above the ordinary minimum.
    """
    lim = load_registry().limits
    assert lim.lift_min_vertical_cm < lim.lift_min_cm <= lim.lift_max_cm
    assert (lim.lift_max_cm - lim.lift_min_cm) % lim.lift_step_cm == 0


def test_the_hologram_grid_is_the_native_snap_size():
    reg = load_registry()
    # AFGBuildableHologram's constructor stores mGridSnapSize = 100.0f, so the
    # grid is 100. The only 50s in the game's data are three holograms'
    # own overrides -- the two power poles and the street light -- which
    # test_a_hologram_that_snaps_finer_carries_its_own_grid pins per buildable.
    assert reg.limits.hologram_grid_cm == 100.0
    assert reg.limits_sources["hologram_grid_cm"] == "binary"
    assert reg.limits.hologram_rotation_step_deg == 90.0


def test_a_hologram_that_snaps_finer_carries_its_own_grid():
    """The global snap size is a default, not a rule every hologram obeys."""
    reg = load_registry()
    overrides = {c: b.grid_snap_cm for c, b in reg.buildables.items() if b.grid_snap_cm is not None}
    assert overrides == {
        "Build_PowerPoleMk1_C": 50.0,
        "Build_PowerTower_C": 50.0,
        "Build_StreetLight_C": 50.0,
    }
    assert reg.buildables["Build_ConstructorMk1_C"].grid_snap_cm is None


def test_every_limit_names_the_rule_that_governs_it_or_why_none_does():
    reg = load_registry()
    for key in (
        "belt_bend_radius_cm",
        "belt_max_incline_deg",
        "belt_max_spline_cm",
        "lift_min_cm",
        "lift_max_cm",
        "hologram_grid_cm",
    ):
        entry = reg.provenance["limits"][key]
        assert entry["governed_by"], key
        assert set(entry["governed_by"]) == {"rule", "effect", "status"}, key
    # And every one of them, not just the seven above: each is either governed
    # by a rule or says why nothing governs it, never both and never neither.
    for field in fields(Limits):
        entry = reg.provenance["limits"][field.name]
        assert bool(entry["governed_by"]) != bool(entry.get("ungoverned")), field.name


def test_every_rule_a_limit_names_exists_and_states_the_copied_effect():
    """A ``governed_by`` that names no rule would be a claim with nothing behind it."""
    reg = load_registry()
    rules = load_rules()
    named = {
        key: entry["governed_by"]
        for key, entry in reg.provenance["limits"].items()
        if entry["governed_by"]
    }
    assert named
    assert {g["rule"] for g in named.values()} <= set(rules)
    # The effect and the status beside a limit are the rule's own, copied at
    # merge time; the merge refuses to write when either has drifted.
    for key, governed in named.items():
        assert governed["effect"] == rules[governed["rule"]].effect, key
        assert governed["status"] == rules[governed["rule"]].status, key
    assert named["belt_bend_radius_cm"]["rule"] == "belt.curvature"
    assert "mBendRadius" in rules[named["belt_bend_radius_cm"]["rule"]].reads
    assert "mMaxIncline" in rules[named["belt_max_incline_deg"]["rule"]].reads


def test_what_the_game_does_with_each_governed_limit():
    """A limit's governance says which of refuse/clamp/snap/none the rule does.

    ``enforced_by`` used to claim every one of these was turned away, which the
    rules themselves contradict: a lift's height is clamped into range, the grid
    and the rotation step are snapped to. ``lift_step_cm`` is not here at all:
    no instruction was seen quantising a lift's height to ``mStepHeight``, so
    nothing governs it and it says so under ``ungoverned``.
    """
    governed = {
        key: entry["governed_by"]["effect"]
        for key, entry in load_registry().provenance["limits"].items()
        if entry["governed_by"]
    }
    assert governed == {
        "belt_max_spline_cm": "refuse",
        "belt_bend_radius_cm": "refuse",
        "belt_max_incline_deg": "refuse",
        "belt_min_length_cm": "refuse",
        "lift_min_cm": "clamp",
        "lift_max_cm": "clamp",
        "lift_min_vertical_cm": "clamp",
        "pipe_max_spline_cm": "refuse",
        "pipe_min_bend_radius_cm": "refuse",
        "hologram_grid_cm": "snap",
        "hologram_rotation_step_deg": "compute",
        # The four overclock and somersloop numbers: the game works each of them
        # out and refuses nothing over any of them.
        "potential_per_shard": "compute",
        "potential_shard_slots_default": "compute",
        "production_boost_per_slot": "compute",
        "production_boost_slots_default": "compute",
    }


def test_a_limit_whose_rule_was_not_read_in_full_shows_it_without_the_rules_file():
    """``governed_by.status`` is the rule's own, so a ``partial`` is visible here.

    ``hologram_grid_cm`` is the one in the shipped registry: it is governed by
    ``buildable.grid_snap``, which is ``partial`` -- the snap the hologram does
    was read, and not every path through the function was. A reader who saw only
    the ``snap`` effect would take the rule for a complete account of it.
    """
    rules = load_rules()
    governed = {
        key: entry["governed_by"]
        for key, entry in load_registry().provenance["limits"].items()
        if entry["governed_by"]
    }
    assert governed["hologram_grid_cm"]["status"] == "partial"
    assert governed["hologram_grid_cm"]["rule"] == "buildable.grid_snap"
    assert {g["status"] for g in governed.values()} <= set(RULE_STATUSES)
    for key, entry in governed.items():
        assert entry["status"] == rules[entry["rule"]].status, key


def test_the_limits_no_rule_governs_say_why():
    reg = load_registry()
    ungoverned = {
        key: entry["ungoverned"]
        for key, entry in reg.provenance["limits"].items()
        if not entry["governed_by"]
    }
    assert set(ungoverned) == {
        "lift_step_cm",
        "pipe_bend_radius_cm",
        "pipe_bend_radius_2d_cm",
        "wire_max_cm",
    }
    assert all(reason for reason in ungoverned.values())
    # The lift step is still read out of the binary; what it is not is a bound
    # the game applies. The multiple this project keeps to is its own rule.
    assert "mStepHeight" in ungoverned["lift_step_cm"]
    assert "never quantises" in ungoverned["lift_step_cm"]
    assert load_registry().limits_sources["lift_step_cm"] == "binary"


def test_nothing_in_the_registry_comes_from_the_blueprint_corpus():
    """A corpus says what somebody once built, which is not a fact about the game.

    It carries clipped geometry, hacked saves and older game versions, so it is
    not a source, not a cross-check and not evidence -- for a limit, for a port
    direction, or for anything else here. Neither is a port's name: what a
    component is called is a convention, not something the game reads.
    """
    reg = load_registry()
    assert "measured" not in LIMIT_SOURCES
    assert "corpus" not in PORT_DIRECTION_SOURCES
    assert "name" not in PORT_DIRECTION_SOURCES
    assert set(reg.limits_sources.values()) <= set(LIMIT_SOURCES)
    ports = [p for b in reg.buildables.values() for p in b.ports]
    assert {p.direction_source for p in ports} <= set(PORT_DIRECTION_SOURCES)
    assert "measured" not in reg.provenance
    assert not hasattr(reg, "limits_measured")
    assert not hasattr(reg, "corpus_statistics")


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


def test_both_ends_of_a_conveyor_are_the_direction_the_constructor_sets():
    """Both ends of every belt and lift mark, from the game rather than a comment.

    The cooked asset omits ``mDirection`` on these, because it equals the
    archetype's -- and the archetype is the component
    ``AFGBuildableConveyorBase``'s constructor creates. That constructor sets
    **both** of them to ``FCD_ANY`` (2), which is also what the components are
    named after. ``FGBuildableConveyorBase.h:380``'s "mConnection0 is the input,
    mConnection1 is the output" is about which end items enter and leave by, not
    about ``mDirection``: a placed belt gets its two directions from whatever it
    snaps to, under the ``belt.snap_directions`` rule.
    """
    reg = load_registry()
    marks = [
        c for c in reg.buildables if c.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))
    ]
    assert len(marks) == 12, sorted(marks)
    for class_name in marks:
        ends = {p.name: p for p in reg.buildables[class_name].ports if p.kind == "belt"}
        assert sorted(ends) == ["ConveyorAny0", "ConveyorAny1"], class_name
        for port in ends.values():
            assert port.direction == "any", class_name
            assert port.direction_source == "native", class_name


def test_which_end_of_a_conveyor_items_enter_by():
    """Both ends are FCD_ANY, so the flow order is a separate fact -- and a needed one.

    A router has to know which end of a belt takes items and which gives them
    up. ``mDirection`` does not say (the constructor sets both to ``FCD_ANY``),
    so the registry carries it per conveyor class as ``flow``, from the game:
    ``AFGBuildableConveyorBase::Factory_Tick`` grabs through ``mConnection0``
    and the header's line 380 says the same.
    """
    reg = load_registry()
    marks = [
        c for c in reg.buildables if c.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))
    ]
    assert len(marks) == 12, sorted(marks)
    for class_name in marks:
        buildable = reg.buildables[class_name]
        flow = buildable.flow
        assert flow is not None, class_name
        assert (flow.entry, flow.exit) == ("ConveyorAny0", "ConveyorAny1"), class_name
        assert flow.source in FLOW_SOURCES, class_name
        # The two names are the class default object's own, not a convention
        # read off a component whose name ends in 0.
        assert flow.name_source == "asset", class_name
        # The two ends it names are ports this buildable actually has.
        assert {flow.entry, flow.exit} <= {p.name for p in buildable.ports}, class_name
    # Nothing else claims one: a pole or a machine has no item-flow order.
    assert sorted(c for c, b in reg.buildables.items() if b.flow) == sorted(marks)


def test_the_conveyor_flow_order_carries_the_game_it_was_read_from():
    """The claim travels with its evidence: the header line and the instructions."""
    flow = load_registry().provenance["conveyor_flow"]
    assert flow["source"] in FLOW_SOURCES
    assert flow["header"].endswith("FGBuildableConveyorBase.h:380")
    assert "mConnection0 is the input" in flow["header_text"]
    assert flow["entry"]["member"] == "mConnection0"
    assert flow["exit"]["member"] == "mConnection1"
    # The binary states the order over the two members and nothing about what
    # the components in them are called, so it claims nothing about names.
    assert "component" not in flow["entry"]
    assert "caveat" not in flow
    if flow["source"] == "native":
        # The grab that makes mConnection0 the entry, and the function it is in.
        assert any(
            "Factory_GrabOutput" in line for line in flow["instructions"]
        ), flow["instructions"]
        assert {f["symbol"] for f in flow["functions"]} >= {
            "AFGBuildableConveyorBase::Factory_Tick",
            "UFGFactoryConnectionComponent::Factory_GrabOutput",
        }
        assert all(f["rva"].startswith("0x") for f in flow["functions"])


def test_the_component_each_connection_member_holds_comes_from_the_class_default():
    """The pairing of ``mConnection0`` with a *named* port is read, not assumed.

    The binary and the header both say ``mConnection0`` is the end items enter
    by; neither says what the component in that member is called. The cooked
    class default object does -- ``mConnection0`` is an object property that
    refers to one of the CDO's own subobjects -- and ``tools/sfy-extract``
    reports that export's name per class.
    """
    flow = load_registry().provenance["conveyor_flow"]
    assert flow["name_source"] == "asset"
    components = flow["components"]
    assert len(components) == 12
    for class_name, members in components.items():
        assert class_name.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))
        assert members == {"mConnection0": "ConveyorAny0", "mConnection1": "ConveyorAny1"}


def test_a_flow_whose_names_come_from_no_asset_is_refused(tmp_path):
    """A port name is only a fact when the class default object states it."""
    payload = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    payload["buildables"]["Build_ConveyorBeltMk1_C"]["flow"]["name_source"] = "convention"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(RegistryError, match="convention"):
        load_registry(path)


def test_every_conveyor_carries_the_cost_segment_the_game_states():
    """How much run one unit of the build recipe pays for, from Docs.json.

    A belt divides by its own ``mMeshLength`` and a lift by its ``mMeshHeight``
    -- that is what each mark's ``GetDismantleRefundReturnsMultiplier`` hands
    to ``AFGBuildable::GetCostMultiplierForLength`` -- and both are class
    defaults in the game's own dump, so the number is read, never measured off
    a blueprint's cost list.
    """
    reg = load_registry()
    costed = {
        name: b.length_per_cost_cm for name, b in reg.buildables.items() if b.length_per_cost_cm
    }
    assert len(costed) == 12
    assert set(costed) == {
        name
        for name in reg.buildables
        if name.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))
    }
    assert set(costed.values()) == {200.0}
    for name in costed:
        assert reg.buildables[name].length_per_cost_source == "docs"
    # A belt's segment is its own mesh length, not a number chosen here.
    belt = reg.buildables["Build_ConveyorBeltMk1_C"]
    assert belt.length_per_cost_cm == belt.mesh_length_cm
    lift = reg.buildables["Build_ConveyorLiftMk1_C"]
    assert lift.length_per_cost_cm == lift.mesh_height_cm
    # Everything else is charged its recipe once, so it carries no segment.
    assert reg.buildables["Build_ConstructorMk1_C"].length_per_cost_cm is None
    assert reg.buildables["Build_ConstructorMk1_C"].length_per_cost_source is None


def test_the_cost_segment_says_which_property_and_which_rule_it_comes_from():
    provenance = load_registry().provenance["cost_segment"]
    assert provenance["source"] == "docs"
    assert provenance["rule"] == "belt.cost"
    assert provenance["properties"] == {
        "FGBuildableConveyorBelt": "mMeshLength",
        "FGBuildableConveyorLift": "mMeshHeight",
    }
    assert len(provenance["applied_to"]) == 12
    # The rule it names is the machine code that divides by the number.
    rule = load_rules()["belt.cost"]
    assert rule.effect == "compute"
    assert {"mMeshLength", "mMeshHeight"} <= set(rule.reads)


def test_a_cost_segment_from_no_game_source_is_refused(tmp_path):
    payload = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    entry = payload["buildables"]["Build_ConveyorBeltMk1_C"]
    entry["length_per_cost_source"] = "corpus"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(RegistryError, match="corpus"):
        load_registry(path)


def test_a_cost_segment_with_no_source_is_refused(tmp_path):
    """A number with nothing behind it is not a fact, so the loader turns it away."""
    payload = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    entry = payload["buildables"]["Build_ConveyorBeltMk1_C"]
    entry["length_per_cost_source"] = None
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(RegistryError, match="travel together"):
        load_registry(path)


def test_a_flow_that_names_a_port_the_buildable_does_not_have_is_refused(tmp_path):
    """``flow`` is a claim about two of this buildable's ports, checked on load."""
    payload = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    payload["buildables"]["Build_ConveyorBeltMk1_C"]["flow"]["entry"] = "ConveyorAny7"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(RegistryError, match="ConveyorAny7"):
        load_registry(path)


def test_a_flow_from_no_game_source_is_refused(tmp_path):
    payload = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    payload["buildables"]["Build_ConveyorBeltMk1_C"]["flow"]["source"] = "corpus"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(RegistryError, match="corpus"):
        load_registry(path)


def test_the_power_poles_carry_the_connection_counts_the_assets_state():
    """Spec 5.1 wants pole connection counts, and the pole Blueprints state them.

    ``FGCircuitConnectionComponent::mMaxNumConnectionLinks`` is serialised on
    every pole because each mark overrides the native default. The game's own
    poles take 4, 7 and 10 wires, and these are what the cooked assets say --
    the assertion is on the extraction, and it would fail rather than be edited
    if a game update changed the numbers.
    """
    reg = load_registry()
    counts = []
    for mark in ("Build_PowerPoleMk1_C", "Build_PowerPoleMk2_C", "Build_PowerPoleMk3_C"):
        (power,) = [p for p in reg.buildables[mark].ports if p.kind == "power"]
        assert power.max_connections is not None and power.max_connections > 0, mark
        counts.append(power.max_connections)
    assert counts == [4, 7, 10]
    assert counts == sorted(counts) and len(set(counts)) == 3


def test_only_power_ports_can_carry_a_connection_count():
    """The property is on the circuit connection; a belt or pipe has no such thing.

    So a belt or pipe port carries ``None`` and says ``unknown``, and every
    power port carries a number and says which link of the archetype chain
    answered -- the Blueprint, a parent Blueprint, or the native constructor.
    Every wall-mounted pole repeats its free-standing mark's count.
    """
    reg = load_registry()
    ports = [(c, p) for c, b in reg.buildables.items() for p in b.ports]
    assert [f"{c}.{p.name}" for c, p in ports if p.kind != "power" and p.max_connections] == []
    assert [
        f"{c}.{p.name}"
        for c, p in ports
        if (p.kind == "power") != (p.max_connections_source != "unknown")
    ] == []
    assert {p.max_connections_source for _, p in ports} <= set(MAX_CONNECTIONS_SOURCES)
    stated = {f"{c}.{p.name}": p.max_connections for c, p in ports if p.max_connections}
    assert stated["Build_PowerPoleWall_Mk2_C.PowerConnection"] == 7
    assert stated["Build_PowerTower_C.PowerTowerConnection"] == 3


def test_the_direction_provenance_counts_the_ports_the_registry_ships():
    """The count has to be of this registry, not of everything the extractor read.

    ``tools/sfy-extract`` reads ports off every cooked ``Build_*`` class, and
    Docs.json lists fewer buildables than that, so the wider count describes a
    registry nobody loads. Both are recorded, each under its own name.
    """
    reg = load_registry()
    resolved = reg.provenance["port_directions"]["resolved_from"]
    shipped: dict[str, int] = {}
    for buildable in reg.buildables.values():
        for port in buildable.ports:
            shipped[port.direction_source] = shipped.get(port.direction_source, 0) + 1
    assert {k: v for k, v in resolved.items() if v} == shipped
    assert sum(resolved.values()) == sum(len(b.ports) for b in reg.buildables.values())
    wider = reg.provenance["port_directions"]["resolved_from_all_extracted"]
    assert sum(wider.values()) > sum(resolved.values())


def test_every_item_a_recipe_names_has_an_asset_path():
    """A blueprint names an item by asset path, so authoring needs one per item."""
    reg = load_registry()
    wanted = {
        item for r in reg.recipes.values() for item, _ in (*r.ingredients, *r.products)
    } | set(reg.descriptors)
    assert not wanted - set(reg.item_paths)
    assert set(reg.recipe_paths) == set(reg.recipes)
    assert (
        reg.item_paths["Desc_IronPlate_C"]
        == "/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C"
    )
    assert all(path.endswith(f".{name}") for name, path in reg.item_paths.items())
    assert all(path.startswith("/Game/") for path in reg.recipe_paths.values())


def test_every_port_says_how_its_direction_was_established():
    """Four sources, all of them game data, and nothing else.

    ``corpus`` and ``name`` were both removed: what a blueprint contains is not
    a fact about the game, and neither is what a component happens to be called.
    A port the three game sources leave open is shipped ``unknown`` rather than
    guessed at, and :func:`test_the_ports_the_game_does_not_give_a_direction`
    lists those.
    """
    reg = load_registry()
    ports = [(c, p) for c, b in reg.buildables.items() for p in b.ports]
    assert set(PORT_DIRECTION_SOURCES) == {"asset", "asset-inherited", "native", "unknown"}
    assert {p.direction_source for _, p in ports} <= set(PORT_DIRECTION_SOURCES)
    assert [f"{c}.{p.name}" for c, p in ports if p.direction_source in ("corpus", "name")] == []
    # "unknown" is the source exactly when the direction is unknown.
    assert all((p.direction == "unknown") == (p.direction_source == "unknown") for _, p in ports)


def test_the_ports_the_game_does_not_give_a_direction():
    """The ``unknown`` ports, listed rather than counted.

    A port lands here when neither the buildable's own Blueprint, nor a parent
    Blueprint's template of the same name, nor the archetype's native
    constructor states a direction. The validator refuses to route to one, and
    the way to shrink this list is to read more of the game -- never to infer a
    direction from the port's name or from a blueprint corpus.
    """
    reg = load_registry()
    unknown = sorted(
        f"{c}.{p.name}"
        for c, b in reg.buildables.items()
        for p in b.ports
        if p.direction_source == "unknown"
    )
    assert unknown == []


def test_a_port_that_inherits_its_direction_says_so():
    """Unreal omits a template property that equals its archetype's value.

    So a Blueprint that derives from another Blueprint carries no ``mDirection``
    of its own for a port the parent already set, and reading the absence as the
    enum's zero would call that port an input. The extractor walks the parent
    chain, and the ports it resolves there are tagged ``asset-inherited``.
    """
    reg = load_registry()
    inherited = {
        f"{c}.{p.name}": p.direction
        for c, b in reg.buildables.items()
        for p in b.ports
        if p.direction_source == "asset-inherited"
    }
    assert inherited
    assert "Build_MinerMk3_C.Output0" in inherited
    assert inherited["Build_MinerMk3_C.Output0"] == "output"


def test_the_native_direction_defaults_carry_their_evidence():
    """A ``native`` direction is a claim about machine code, so it names it."""
    reg = load_registry()
    defaults = reg.provenance["assets"]["native_direction_defaults"]
    by_class = {entry["component_class"]: entry for entry in defaults["component_defaults"]}
    factory = by_class["FGFactoryConnectionComponent"]
    assert factory["direction"] == "input"
    assert factory["class"] == "UFGFactoryConnectionComponent"
    assert factory["member"] == "mDirection"
    assert factory["value"] == 0
    assert factory["function"] == "UFGFactoryConnectionComponent::UFGFactoryConnectionComponent"
    assert any("mDirection" in line for line in factory["instructions"])
    # Four buildables build their own connections and set them, so their
    # subobject -- not the component class default -- is the archetype.
    owners = {entry["set_in"]: entry for entry in defaults["owner_defaults"]}
    assert {name.split("::")[0] for name in owners} == {
        "AFGBuildableConveyorBase",
        "AFGBuildablePoleConveyor",
        "AFGBuildablePipeline",
        "AFGBuildablePolePipe",
    }
    conveyor = owners["AFGBuildableConveyorBase::AFGBuildableConveyorBase"]
    assert conveyor["direction"] == "any"
    assert conveyor["value"] == 2
    assert conveyor["enum_name"] == "FCD_ANY"
    assert set(conveyor["owner_classes"]) == {
        "AFGBuildableConveyorBelt",
        "AFGBuildableConveyorLift",
    }
    # Both stores are quoted: the second is 600 bytes past the chunk the symbol
    # is in, which sfy-native reaches by following the chained .pdata.
    assert sum("[rax+258h],2" in line for line in conveyor["instructions"]) == 2
    assert owners["AFGBuildablePoleConveyor::AFGBuildablePoleConveyor"]["direction"] == "snap_only"
    for entry in defaults["owner_defaults"]:
        assert entry["instructions"], entry["set_in"]
        assert len(entry["inheritance"]) == len(entry["owner_classes"]), entry["set_in"]


def test_a_rotated_clearance_box_keeps_its_rotation():
    """Docs.json exports the clearance transform, and all of it is now read.

    ``Build_Barrier_Low_01_C``'s one box is a quarter turn about Z: read as an
    axis-aligned Min/Max it is 400 cm along X and 60 along Y, when the game has
    it the other way round.
    """
    reg = load_registry()
    (box,) = reg.buildables["Build_Barrier_Low_01_C"].clearance
    assert box.rotation[1] == pytest.approx(90.0, abs=1e-3)
    assert box.rotation[0] == pytest.approx(0.0, abs=1e-3)
    assert box.rotation[2] == pytest.approx(0.0, abs=1e-3)
    assert box.scale == (1.0, 1.0, 1.0)
    # A beam is two quarter turns, and the second is in the roll.
    (beam,) = reg.buildables["Build_Beam_C"].clearance
    assert (round(beam.rotation[1]), round(beam.rotation[2])) == (90, -90)
    # The one box in the whole dump with a non-default scale.
    scaled = [b for b in reg.buildables["Build_MinerMk2_C"].clearance if b.scale != (1.0, 1.0, 1.0)]
    assert [b.scale for b in scaled] == [(1.0, 1.0, 0.0)]


def test_the_committed_registry_is_what_the_merge_produces(tmp_path, capsys):
    """Re-run the merge and diff it, so registry.json can never drift from its inputs.

    **Every** key has to come out identical, this file included: if
    ``docs.json``, ``assets.json``, ``native.json``, ``native_directions.json``
    or ``hologram_rules.json`` has moved since the registry was written, or the
    merge itself has, this is where it shows up rather than in whatever consumes
    the registry next. Nothing is popped before the diff -- the provenance used
    to carry the repository's ``HEAD``, which no re-run could reproduce, and it
    now carries the sha256 of each input, which every re-run over the same
    extraction does.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import sfy_registry

    out = tmp_path / "registry.json"
    assert sfy_registry.main(out=out) == 0
    capsys.readouterr()
    fresh = json.loads(out.read_text())
    committed = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    assert fresh == committed


def test_the_registry_says_which_extraction_it_was_merged_from():
    """``provenance.inputs_sha256`` is the digest of each file the merge read."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import sfy_registry

    data = Path(docs.__file__).parent / "data"
    committed = json.loads((data / "registry.json").read_text())["provenance"]["inputs_sha256"]
    assert set(committed) == set(sfy_registry.MERGE_INPUTS)
    for name, digest in committed.items():
        assert digest == hashlib.sha256((data / name).read_bytes()).hexdigest(), name


def test_boxes_the_game_ignores_when_snapping_are_marked():
    reg = load_registry()
    excluded = [
        (c, i)
        for c, b in sorted(reg.buildables.items())
        for i, box in enumerate(b.clearance)
        if box.exclude_for_snapping
    ]
    assert len(excluded) == 97
    assert all(
        not box.exclude_for_snapping for box in reg.buildables["Build_Barrier_Low_01_C"].clearance
    )
