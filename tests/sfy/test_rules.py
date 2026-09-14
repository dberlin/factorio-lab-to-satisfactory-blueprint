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
