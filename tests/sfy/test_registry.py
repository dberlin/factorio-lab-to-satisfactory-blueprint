"""The committed registry carries the ports and limits the placer needs.

These read ``data/registry.json`` as shipped, so they are the acceptance for the
extractor in ``tools/sfy-extract`` and the merge in ``scripts/sfy_registry.py``.
"""

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from flab2bp.sfy import docs
from flab2bp.sfy.registry import (
    FLOW_SOURCES,
    LIMIT_SOURCES,
    PORT_DIRECTION_SOURCES,
    Limits,
    RegistryError,
    load_registry,
)
from flab2bp.sfy.rules import load_rules

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

# The three the binary states no constant for: ``AFGConveyorLiftHologram``
# computes all three in ``BeginPlay`` from the lift buildable's mesh height H.
# The formula is in the machine code, H is in Docs.json, so the merge evaluates
# it rather than falling back to the corpus.
BINARY_DERIVED = ("lift_max_cm", "lift_min_cm", "lift_min_vertical_cm")

# Every lift mark's Docs.json mMeshHeight, and the three heights that follow.
MESH_HEIGHT_CM = 200.0


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
    """The corpus corroborates the registry; it is never a source for it.

    A measured number is an envelope -- what the game was observed to accept --
    and using one as a limit lets one old blueprint's outlier become the rule.
    Every limit has a game-data source now, so nothing is tagged ``measured``.
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
    for key in BINARY_DERIVED:
        assert reg.limits_sources[key] == "binary-derived", key
        assert reg.provenance["limits"][key]["H"] == MESH_HEIGHT_CM
    assert lim.lift_min_cm == 2.0 * MESH_HEIGHT_CM
    assert lim.lift_max_cm == 24.0 * MESH_HEIGHT_CM
    assert lim.lift_min_vertical_cm == MESH_HEIGHT_CM - 50.0


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
    # AFGBuildableHologram's constructor stores mGridSnapSize = 100.0f. The
    # corpus gcd said 50, which Task 11 flagged as possibly mesh offsets rather
    # than the snap size; the binary settles it, and the corpus no longer has a
    # say in it at all.
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
        "lift_step_cm",
        "hologram_grid_cm",
    ):
        entry = reg.provenance["limits"][key]
        assert entry["governed_by"], key
        assert set(entry["governed_by"]) == {"rule", "effect"}, key
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
    # The effect beside a limit is the rule's own, copied at merge time.
    for key, governed in named.items():
        assert governed["effect"] == rules[governed["rule"]].effect, key
    assert named["belt_bend_radius_cm"]["rule"] == "belt.curvature"
    assert "mBendRadius" in rules[named["belt_bend_radius_cm"]["rule"]].reads
    assert "mMaxIncline" in rules[named["belt_max_incline_deg"]["rule"]].reads


def test_what_the_game_does_with_each_governed_limit():
    """A limit's governance says which of refuse/clamp/snap/none the rule does.

    ``enforced_by`` used to claim every one of these was turned away, which the
    rules themselves contradict: a lift's height is clamped into range, the grid
    and the rotation step are snapped to, and no instruction was seen quantising
    a lift's height to ``mStepHeight`` at all.
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
        "lift_step_cm": "none",
        "lift_min_cm": "clamp",
        "lift_max_cm": "clamp",
        "lift_min_vertical_cm": "clamp",
        "pipe_max_spline_cm": "refuse",
        "pipe_min_bend_radius_cm": "refuse",
        "hologram_grid_cm": "snap",
        "hologram_rotation_step_deg": "snap",
    }


def test_the_limits_no_rule_governs_say_why():
    reg = load_registry()
    ungoverned = {
        key: entry["ungoverned"]
        for key, entry in reg.provenance["limits"].items()
        if not entry["governed_by"]
    }
    assert set(ungoverned) == {"pipe_bend_radius_cm", "pipe_bend_radius_2d_cm", "wire_max_cm"}
    assert all(reason for reason in ungoverned.values())


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
    assert flow["entry"]["component"] == "ConveyorAny0"
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

    A power port whose Blueprint does not override the native default carries
    ``None``: the value then lives in a native constructor the pak does not
    ship, and nothing here invents one. Every wall-mounted pole repeats its
    free-standing mark's count, which is how the null ones can be told from a
    number that went missing.
    """
    reg = load_registry()
    ports = [(c, p) for c, b in reg.buildables.items() for p in b.ports]
    assert [f"{c}.{p.name}" for c, p in ports if p.kind != "power" and p.max_connections] == []
    stated = {f"{c}.{p.name}": p.max_connections for c, p in ports if p.max_connections}
    assert stated["Build_PowerPoleWall_Mk2_C.PowerConnection"] == 7
    assert stated["Build_PowerTower_C.PowerTowerConnection"] == 3
    assert reg.buildables["Build_AssemblerMk1_C"].ports  # a machine has ports
    assert all(
        p.max_connections is None
        for p in reg.buildables["Build_AssemblerMk1_C"].ports
        if p.kind == "power"
    )


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

    Everything but ``merged_at_commit`` has to come out identical: if
    ``docs.json``, ``assets.json``, ``native.json`` or ``hologram_rules.json``
    has moved since the registry was written, or the merge itself has, this is
    where it shows up rather than in whatever consumes the registry next.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import sfy_registry

    out = tmp_path / "registry.json"
    assert sfy_registry.main(out=out) == 0
    capsys.readouterr()
    fresh = json.loads(out.read_text())
    committed = json.loads((Path(docs.__file__).parent / "data" / "registry.json").read_text())
    for payload in (fresh, committed):
        payload["provenance"].pop("merged_at_commit")
    assert fresh == committed


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
