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
    LIMIT_SOURCES,
    PORT_DIRECTION_SOURCES,
    Limits,
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


def test_every_limit_names_the_rule_that_enforces_it():
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
        assert "enforced_by" in entry, key
        assert entry["enforced_by"] or entry.get("reason"), key
    # And every one of them, not just the seven above.
    for field in fields(Limits):
        entry = reg.provenance["limits"][field.name]
        assert entry["enforced_by"] or entry.get("reason"), field.name


def test_every_rule_a_limit_names_exists_and_is_about_that_limit():
    """An ``enforced_by`` that names no rule would be a claim with nothing behind it."""
    reg = load_registry()
    rules = load_rules()
    named = {
        key: entry["enforced_by"]
        for key, entry in reg.provenance["limits"].items()
        if entry["enforced_by"]
    }
    assert named
    assert set(named.values()) <= set(rules)
    assert named["belt_bend_radius_cm"] == "belt.curvature"
    assert "mBendRadius" in rules[named["belt_bend_radius_cm"]].reads
    assert "mMaxIncline" in rules[named["belt_max_incline_deg"]].reads


def test_nothing_in_the_registry_comes_from_the_blueprint_corpus():
    """A corpus says what somebody once built, which is not a fact about the game.

    It carries clipped geometry, hacked saves and older game versions, so it is
    not a source, not a cross-check and not evidence -- for a limit or for
    anything else here. ``scripts/sfy_measure_limits.py`` still measures the
    fixtures; the merge does not read what it writes.
    """
    reg = load_registry()
    assert "measured" not in LIMIT_SOURCES
    assert "corpus" not in PORT_DIRECTION_SOURCES
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


def test_a_conveyor_takes_in_at_end_0_and_puts_out_at_end_1():
    """Both ends of every belt and lift mark, pinned.

    The cooked asset omits ``mDirection`` on these, so the registry used to call
    both ends inputs. ``FGBuildableConveyorBase.h:380`` states the order --
    ``mConnection0`` is the input, ``mConnection1`` the output -- and the
    corpus wires all 1119 belt-to-machine links that way.
    """
    reg = load_registry()
    marks = [
        c for c in reg.buildables if c.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))
    ]
    assert len(marks) == 12, sorted(marks)
    for class_name in marks:
        ends = {p.name: p for p in reg.buildables[class_name].ports if p.kind == "belt"}
        assert sorted(ends) == ["ConveyorAny0", "ConveyorAny1"], class_name
        assert ends["ConveyorAny0"].direction == "input", class_name
        assert ends["ConveyorAny1"].direction == "output", class_name
        for port in ends.values():
            assert port.direction_source == "header", class_name


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
    reg = load_registry()
    ports = [(c, p) for c, b in reg.buildables.items() for p in b.ports]
    assert {p.direction_source for _, p in ports} <= set(PORT_DIRECTION_SOURCES)
    assert [f"{c}.{p.name}" for c, p in ports if p.direction == "unknown"] == []
    # Pipes and power connections are never in doubt: a pipe spells its own
    # enum out and a power connection has no direction to begin with.
    assert {p.direction_source for _, p in ports if p.kind != "belt"} == {"asset"}


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
    ``docs.json``, ``assets.json``, ``native.json`` or ``measured.json`` has
    moved since the registry was written, or the merge itself has, this is where
    it shows up rather than in whatever consumes the registry next.
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
