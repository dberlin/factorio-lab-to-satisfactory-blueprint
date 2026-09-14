"""The hologram rules are what the game allows, and every one says how we know.

``data/hologram_rules.json`` is written by ``scripts/sfy_native_rules.py`` out of
the shipped DLL's machine code. These are the acceptance for that file: a rule
that claims to be ``extracted`` has to carry the instructions it was read from,
and one that could not be read has to say why.
"""

from flab2bp.sfy.rules import REQUIRED_RULE_IDS, load_rules


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


def test_every_rules_evidence_is_in_address_order():
    for rule in load_rules().values():
        rvas = [int(line.split(":", 1)[0], 16) for line in rule.evidence]
        assert rvas == sorted(rvas), rule.id
