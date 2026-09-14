"""``native.json`` is what ``tools/sfy-native`` reads out of the shipped DLL.

The seven hologram limits are C++ constructor immediates: no cooked asset, no
header initialiser and no Docs.json entry carries them, so the only source left
is the machine code. These tests hold the committed extraction to the four
values the public headers state outright -- the oracle -- and to the evidence
every unknown value must carry with it.
"""

import json
from pathlib import Path

import pytest

from flab2bp.sfy import docs

NATIVE = Path(docs.__file__).parent / "data" / "native.json"


def _native():
    return json.loads(NATIVE.read_text())


def test_oracle_values_reproduced_from_binary():
    """Members whose values the public headers state must come back exactly."""
    c = _native()["classes"]
    assert c["AFGConveyorBeltHologram"]["members"]["mMaxSplineLength"]["value"] == pytest.approx(
        5600.1, abs=1e-3
    )
    assert c["AFGPipelineHologram"]["members"]["mMaxSplineLength"]["value"] == pytest.approx(
        5600.1, abs=1e-3
    )
    assert c["AFGPipelineHologram"]["members"]["mBendRadius2D"]["value"] == pytest.approx(199.0)
    assert c["AFGPipelineHologram"]["members"]["mMinBendRadius"]["value"] == pytest.approx(75.0)


def test_unknown_limits_are_present_with_evidence():
    c = _native()["classes"]
    for cls, member in [
        ("AFGConveyorBeltHologram", "mBendRadius"),
        ("AFGConveyorBeltHologram", "mMaxIncline"),
        ("AFGConveyorLiftHologram", "mStepHeight"),
        ("AFGBuildableHologram", "mGridSnapSize"),
    ]:
        m = c[cls]["members"][member]
        assert m["value"] is not None and m["value"] > 0, (cls, member, m)
        assert m["evidence"] and m["set_in"]
        assert isinstance(m["offset"], int) and m["offset"] > 0


def test_the_three_lift_heights_are_computed_and_say_so():
    """The game works them out from the lift's mesh height; no constant exists.

    ``AFGConveyorLiftHologram::BeginPlay`` reads the buildable's mesh height and
    stores ``2 x`` it as the minimum, ``24 x`` it as the maximum and ``it - 50``
    as the minimum with a vertical connection. The tool refuses to report the
    constructor's zero as the value and names the store that overwrites it; the
    registry falls through to the measured envelope for these three.
    """
    members = _native()["classes"]["AFGConveyorLiftHologram"]["members"]
    for member in ("mMinimumHeight", "mMaximumHeight", "mMinimumHeightWithVerticalConnection"):
        m = members[member]
        assert m["value"] is None, (member, m)
        assert "AFGConveyorLiftHologram::BeginPlay" in m["reason"], m["reason"]
        assert "computed" in m["reason"], m["reason"]
        # The zero the constructor left is still on record, with its evidence.
        zeroed = [f for f in m["also_set_in"] if f["value"] == 0.0]
        assert zeroed and all(f["evidence"] for f in zeroed), m


def test_provenance_names_the_matching_pdb():
    p = _native()["provenance"]
    assert p["pdb_guid"].upper().replace("-", "") == "A2691F7CB45E794765C6535DE373BA04"
    assert p["pdb_age"] == 1
