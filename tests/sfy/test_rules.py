"""The hologram rules are what the game allows, and every one says how we know.

``data/hologram_rules.json`` is written by ``scripts/sfy_native_rules.py`` out of
the shipped DLL's machine code. These are the acceptance for that file: a rule
that claims to be ``extracted`` has to carry the instructions it was read from,
and one that could not be read has to say why.
"""

import json

import pytest

from flab2bp.sfy.rules import (
    REQUIRED_RULE_IDS,
    RULE_EFFECTS,
    RulesError,
    load_rules,
)


def test_every_required_rule_is_present_with_a_status_and_evidence():
    rules = load_rules()
    for rule_id in REQUIRED_RULE_IDS:
        r = rules[rule_id]
        assert r.status in ("extracted", "partial", "unextractable"), rule_id
        assert r.header, rule_id
        if r.status != "unextractable":
            assert r.evidence and r.rva, rule_id
        else:
            assert r.interpretation, rule_id


def test_every_rule_says_what_the_hologram_does_with_the_number():
    """``effect`` is the whole point of a rule: refuse, clamp, snap, nothing, or
    -- for a function that is not a validation at all -- compute."""
    assert RULE_EFFECTS == ("refuse", "clamp", "snap", "none", "compute")
    for rule in load_rules().values():
        assert rule.effect in RULE_EFFECTS, rule.id


def test_a_rule_that_was_extracted_cannot_have_no_effect():
    """``none`` means "no enforcement was found", which contradicts ``extracted``.

    A rule whose comparison and branch were read says what the branch *does*;
    if it does nothing, the reading is not finished and the status is
    ``partial``. :func:`load_rules` refuses the combination rather than let a
    registry claim a limit is governed by a rule that governs nothing.
    """
    for rule in load_rules().values():
        assert not (rule.status == "extracted" and rule.effect == "none"), rule.id


def _payload(**overrides):
    rule = {
        "id": "belt.curvature",
        "class": "AFGConveyorBeltHologram",
        "function": "ValidateCurvature",
        "rva": "0xaa5280",
        "status": "extracted",
        "effect": "refuse",
        "reads": [],
        "constants": [],
        "calls": [],
        "comparison": "",
        "interpretation": "",
        "evidence": [],
        "header": "Hologram/FGConveyorBeltHologram.h:105",
    }
    rule.update(overrides)
    return {"provenance": {}, "rules": [rule]}


def test_load_rules_refuses_an_effect_outside_the_vocabulary(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(_payload(effect="warn")), encoding="utf-8")
    with pytest.raises(RulesError, match="effect"):
        load_rules(path)


def test_load_rules_refuses_an_extracted_rule_that_enforces_nothing(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(_payload(effect="none")), encoding="utf-8")
    with pytest.raises(RulesError, match="none"):
        load_rules(path)


def test_the_effects_the_shipped_rules_state():
    """One assertion per rule, so a regenerated file cannot quietly change one."""
    effects = {rule_id: rule.effect for rule_id, rule in load_rules().items()}
    assert effects == {
        "belt.curvature": "refuse",
        "belt.incline": "refuse",
        "belt.min_length": "refuse",
        "belt.max_length": "refuse",
        "belt.clearance": "none",
        "belt.snap_directions": "snap",
        "pipe.min_length": "refuse",
        "pipe.curvature": "refuse",
        "pipe.max_length": "refuse",
        "pipe.fluid_requirements": "refuse",
        "lift.height_range": "clamp",
        "lift.step": "none",
        "lift.placement": "refuse",
        "lift.clearance": "none",
        "buildable.grid_snap": "snap",
        "buildable.rotation_step": "compute",
        "buildable.clearance": "refuse",
        "belt.cost": "compute",
        "manufacturer.inventory_filters": "compute",
        "belt.straight_tangents": "compute",
    }


def test_the_cost_rule_is_a_computation_and_not_a_bound():
    """``belt.cost`` says what the game *works out*, not what it refuses.

    It is ``extracted`` with no refusal, clamp or snap behind it, and that is
    not the ``extracted``/``none`` contradiction: the function was read in full
    and it is not a validator. A caller must never treat it as a bound.
    """
    rule = load_rules()["belt.cost"]
    assert (rule.status, rule.effect) == ("extracted", "compute")
    assert rule.cls == "AFGBuildable"
    assert rule.function == "GetCostMultiplierForLength"
    assert rule.rva == "0x4a6bb0"
    assert rule.header == "Buildables/FGBuildable.h:475"
    # The two mesh members the conveyor overrides divide by, and the 0.5 the
    # SSE RoundToInt adds, are in what the tool reported.
    assert {"mMeshLength", "mMeshHeight", "mLength"} <= set(rule.reads)
    assert any("f32=0.5" in constant for constant in rule.constants)


def test_the_cost_rule_names_every_function_it_was_read_across():
    """A rule the game spreads over several functions lists all of them."""
    rule = load_rules()["belt.cost"]
    symbols = {line.split(" @ ")[0] for line in rule.also_read}
    assert symbols == {
        "AFGBlueprintSubsystem::CalculateBlueprintCost",
        "AFGBuildable::GetDismantleRefund_Implementation",
        "AFGBuildable::GetDismantleRefundReturns",
        "AFGBuildable::GetDismantleRefundReturnsMultiplier",
        "AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier",
        "AFGBuildableConveyorLift::GetDismantleRefundReturnsMultiplier",
        "AFGBuildableConveyorBelt::AFGBuildableConveyorBelt",
    }
    assert all(line.split(" @ ")[1].startswith("0x") for line in rule.also_read)
    # The belt's own override, whose body is the two members it divides.
    assert "AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier @ 0x4edf70" in (
        rule.also_read
    )
    # Every rule that was read from one function says so.
    spread = {r.id for r in load_rules().values() if r.also_read}
    assert spread == {"belt.cost", "manufacturer.inventory_filters", "belt.straight_tangents"}


def test_the_cost_rules_evidence_carries_the_rounding_and_the_multiply():
    """The arithmetic a placer has to reproduce, quoted instruction by instruction."""
    evidence = load_rules()["belt.cost"].evidence
    at = {line.split(":")[0]: line for line in evidence}
    assert "divss" in at["0x4a6bb9"]
    assert "addss" in at["0x4a6bc2"] and "addss" in at["0x4a6bc6"]
    assert "cvtss2si" in at["0x4a6bce"] and "sar eax,1" in at["0x4a6bd2"]
    assert "cmovl" in at["0x4a6bd6"]
    # amount * multiplier, per ingredient of the build recipe.
    assert "imul" in at["0x4a781d"]
    assert "mBuiltWithRecipe" in at["0x4a774c"]
    assert "mMeshLength" in at["0x4edf70"]
    assert "mMeshHeight" in at["0x4edfa5"]


def test_the_inventory_filter_rule_states_the_slot_to_ingredient_assignment():
    """What a manufacturer's filters hold, read out of the game rather than a file."""
    rule = load_rules()["manufacturer.inventory_filters"]
    assert (rule.status, rule.effect) == ("extracted", "compute")
    assert rule.cls == "AFGBuildableManufacturer"
    assert rule.function == "SetUpInventoryFilters"
    assert rule.rva == "0x549a70"
    assert rule.header == "Buildables/FGBuildableManufacturer.h:220"
    assert "UFGInventoryComponent::SetAllowedItemOnIndex" in rule.calls
    assert {"mInputInventory", "mOutputInventory", "mCurrentRecipe"} <= set(rule.reads)
    assert {line.split(" @ ")[0] for line in rule.also_read} == {
        "AFGBuildableManufacturer::SetRecipe",
        "AFGBuildableManufacturer::AFGBuildableManufacturer",
    }
    at = {line.split(":")[0]: line for line in rule.evidence}
    # Slot i takes the i-th ingredient...
    assert "SetAllowedItemOnIndex" in at["0x549bb9"]
    assert "mInputInventory" in at["0x549ae4"]
    # ...and a slot past the last one takes UFGItemDescriptor, the wildcard.
    assert "UFGItemDescriptor" in at["0x549f83"]
    assert "SetAllowedItemOnIndex" in at["0x549fb0"]
    # The output inventory gets the identical loop against the products.
    assert "SetAllowedItemOnIndex" in at["0x54a292"]
    assert "UFGItemDescriptor" in at["0x54a6a1"]
    # SetRecipe is what calls it, after storing the recipe.
    assert "mCurrentRecipe" in at["0x548d5e"]
    assert "0A80h" in at["0x548f5b"]


def test_the_straight_tangent_rule_states_the_tangent_of_a_straight_run():
    """Half the length, clamped to [50, 600], and unit vectors on the outside."""
    rule = load_rules()["belt.straight_tangents"]
    assert (rule.status, rule.effect) == ("extracted", "compute")
    assert rule.cls == "AFGConveyorBeltHologram"
    assert rule.function == "AutoRouteSpline"
    assert rule.header == "Hologram/FGConveyorBeltHologram.h:97"
    assert {line.split(" @ ")[0] for line in rule.also_read} == {
        "FSplineBuilder::Start",
        "FSplineBuilder::AddSegment",
        "FSplineUtils::BuildStraightSpline2D",
        "FSplineUtils::BuildStraightSpline3D",
    }
    at = {line.split(":")[0]: line for line in rule.evidence}
    # The three constants the run length is scaled and clamped by.
    assert "f64=0.5" in at["0xafcf0d"]
    assert "f64=600.0" in at["0xafcf15"]
    assert "f64=50.0" in at["0xafcf1d"]
    # And the same three in the 3D builder.
    assert "f64=0.5" in at["0xafd2a5"] and "f64=600.0" in at["0xafd2ad"]
    assert "f64=50.0" in at["0xafd2b5"]
    # Start writes one normalised vector into both of point 0's tangents.
    assert "rcx+18h" in at["0xb22291"] and "rcx+30h" in at["0xb22295"]
    # AddSegment gives the new point the full tangent to arrive on...
    assert "movups" in at["0xaf213f"]
    # ...and rescales the previous point's leave tangent to its length.
    assert "mulsd" in at["0xaf2101"]


def test_the_straight_tangent_rule_says_the_port_facing_rule_is_ours():
    """The game does not constrain which way a belt leaves a port, and it says so."""
    rule = load_rules()["belt.straight_tangents"]
    assert "this project's own rule" in rule.interpretation
    assert "startConnectionNormal" in rule.interpretation
    # And nothing refuses a spline for it: the four checks are elsewhere.
    assert "none of them" in rule.interpretation


def test_belt_curvature_rule_reads_the_bend_radius():
    r = load_rules()["belt.curvature"]
    assert r.status in ("extracted", "partial")
    assert "mBendRadius" in r.reads


def test_the_split_functions_are_extracted_past_their_entry_chunk():
    """MSVC cut these three into chained .pdata chunks; the tool stitches them back."""
    rules = load_rules()
    for rule_id in ("belt.max_length", "pipe.max_length"):
        r = rules[rule_id]
        assert r.status == "extracted", (rule_id, r.status)
        assert "mMaxSplineLength" in r.reads, rule_id
        # The comparison lives past the entry chunk, so the evidence must too.
        assert any("comiss" in line and "mMaxSplineLength" in line for line in r.evidence), rule_id

    fluid = rules["pipe.fluid_requirements"]
    assert fluid.status == "extracted"
    assert any("GetFluidDescriptor" in line for line in fluid.evidence)
    assert any("cmp rbx,rdi" in line for line in fluid.evidence)


def test_an_indirect_call_is_named_in_the_evidence_not_left_as_an_address():
    """A rule that names a callee must be reproducible from the tool's own output."""
    r = load_rules()["belt.curvature"]
    named = [line for line in r.evidence if "GetSplineLength" in line]
    assert named, r.evidence
    assert any("call qword ptr" in line for line in named)
    assert "?GetSplineLength@USplineComponent@@QEBAMXZ" in r.calls


def test_a_leaf_functions_rule_is_extracted_through_the_pdbs_procedure_length():
    """GetRotationStep has no .pdata entry; the PDB's length still bounds it."""
    r = load_rules()["buildable.rotation_step"]
    assert r.status == "extracted"
    # It quantises nothing: a compare-and-return ladder, and the caller applies
    # the step it hands back. A validator must not read it as a bound.
    assert r.effect == "compute"
    assert "compute and not snap" in r.interpretation
    for member in ("mSnappedBuilding", "mUseGradualFoundationRotations"):
        assert member in r.reads, member
    # All four returns, not just the one before the first `ret` at 0xa7c07a.
    for value in ("0Ah", "5Ah", "2Dh", "xor eax,eax"):
        assert any(value in line for line in r.evidence), value


def test_a_rule_that_stays_partial_says_which_callee_hides_the_comparison():
    for rule_id, callee in [
        ("buildable.grid_snap", "FHologramHelpers::SnapToFloor"),
        ("buildable.clearance", "TestClearanceOverlap"),
        ("belt.clearance", "CreateClearanceData"),
    ]:
        r = load_rules()[rule_id]
        assert r.status == "partial", rule_id
        assert callee in r.comparison, rule_id


def test_the_fourth_partial_rule_is_partial_for_a_different_reason():
    """``lift.clearance`` names no callee: the function makes no decision at all.

    The other three are ``partial`` because the comparison is inside something
    they call. ``AFGConveyorLiftHologram::UpdateClearance`` was read to its end
    and has no comparison in it to hide -- it builds one ``FFGClearanceData``
    from two constants and the lift's own mesh height and stores it, and the
    hologram that later tests an overlap is somewhere else. That is why the
    effect is ``none`` (these instructions turn no placement away) while the
    status stays ``partial`` (what the game does with the box is unread), and
    why a validator must not read this rule as "a lift's clearance is not
    checked".
    """
    r = load_rules()["lift.clearance"]
    assert (r.status, r.effect) == ("partial", "none")
    assert r.function == "UpdateClearance"
    assert "no overlap decision is made here" in r.comparison
    # The box it does build, out of the constants the evidence quotes.
    assert "mMeshHeight" in r.comparison and "-5" in r.comparison
    assert not any(
        callee in r.comparison
        for callee in ("SnapToFloor", "TestClearanceOverlap", "CreateClearanceData")
    )


def test_every_rules_evidence_is_in_address_order():
    for rule in load_rules().values():
        rvas = [int(line.split(":", 1)[0], 16) for line in rule.evidence]
        assert rvas == sorted(rvas), rule.id
