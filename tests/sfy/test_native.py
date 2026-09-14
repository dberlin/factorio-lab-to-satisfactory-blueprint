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

WIN64 = "FactoryGame/Binaries/Win64"
MODULE = "FactoryGameEGS-FactoryGame-Win64-Shipping"


def _native():
    return json.loads(NATIVE.read_text())


def _dll_and_pdb():
    """The shipped module DLL and its PDB, or ``(None, None)`` without a game install."""
    win64 = docs.satisfactory_dir() / WIN64
    dll = win64 / f"{MODULE}.dll"
    pdb = win64 / f"{MODULE}.pdb"
    if dll.is_file() and pdb.is_file():
        return dll, pdb
    return None, None


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
    merge then applies those three formulas to Docs.json's ``mMeshHeight``, so
    the registry sources these three ``binary-derived``.
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


def _disasm(tmp_path, symbol):
    """Run ``sfy-native disasm`` for one symbol, or skip without cargo and a game."""
    import shutil
    import subprocess

    dll, pdb = _dll_and_pdb()
    if shutil.which("cargo") is None or dll is None:
        pytest.skip("cargo or the game install is not available")
    out = tmp_path / "disasm.json"
    subprocess.run(
        [
            "cargo",
            "run",
            "--release",
            "--quiet",
            "--",
            str(dll),
            str(pdb),
            "disasm",
            symbol,
            "--out",
            str(out),
        ],
        cwd="tools/sfy-native",
        check=True,
        timeout=600,
    )
    return json.loads(out.read_text())


def test_disasm_of_validate_curvature_reads_the_bend_radius(tmp_path):
    """The disasm mode must find the belt hologram's curvature check and see it read mBendRadius."""
    data = _disasm(tmp_path, "AFGConveyorBeltHologram::ValidateCurvature")
    assert len(data) == 1
    fn = data[0]
    assert fn["size_source"] == "pdata"
    members = {i["member"]["name"] for i in fn["instructions"] if i.get("member")}
    assert "mBendRadius" in members


def test_a_single_chunk_function_disassembles_exactly_as_it_did_before_chaining(tmp_path):
    """Following chained .pdata must not disturb a function MSVC did not split.

    ``data/disasm_validate_curvature_pre_chunks.json`` is the tool's output for
    ``ValidateCurvature`` from before Task 5. Two keys were *added* since, both
    deliberately: ``chunks`` on the function and ``import`` on an instruction
    whose ``call qword ptr [rip+K]`` goes through an import address table slot.
    Drop those two and the output has to be identical, instruction for
    instruction -- otherwise the chunk work changed something it should not
    have.
    """
    before = json.loads(
        (Path(__file__).parent / "data" / "disasm_validate_curvature_pre_chunks.json").read_text()
    )
    after = _disasm(tmp_path, "AFGConveyorBeltHologram::ValidateCurvature")

    imports = {}
    for fn in after:
        assert fn.pop("chunks") == [{"rva": fn["rva"], "size": fn["size"]}]
        assert fn["size_source"] == "pdata"
        for instruction in fn["instructions"]:
            name = instruction.pop("import", None)
            if name is not None:
                imports[instruction["rva"]] = name
    assert after == before

    # The one additive annotation, pinned: an indirect call now carries the
    # imported symbol the interpretations name, not just its IAT address.
    assert imports == {
        "0xaa52d0": "?GetSplineLength@USplineComponent@@QEBAMXZ",
        "0xaa535b": (
            "?GetTangentAtDistanceAlongSpline@USplineComponent@@QEBA?AU?$TVector@N@Math@UE@@"
            "MW4Type@ESplineCoordinateSpace@@@Z"
        ),
        "0xaa540f": (
            "?GetTangentAtDistanceAlongSpline@USplineComponent@@QEBA?AU?$TVector@N@Math@UE@@"
            "MW4Type@ESplineCoordinateSpace@@@Z"
        ),
    }


def test_a_leaf_without_a_pdata_entry_is_bounded_by_the_pdbs_procedure_length(tmp_path):
    """GetRotationStep is a leaf: MSVC emits no .pdata, the PDB states the length."""
    data = _disasm(tmp_path, "GetRotationStep")
    fn = next(f for f in data if f["symbol"] == "AFGBuildableHologram::GetRotationStep")
    assert fn["rva"] == "0xa7c050"
    assert fn["size_source"] == "pdb-procedure-length"
    assert fn["size"] == 77
    assert fn["chunks"] == [{"rva": "0xa7c050", "size": 77}]
    # 20 instructions, not the 10 the first `ret` at 0xa7c07a would have given.
    assert len(fn["instructions"]) == 20
    returns = {
        i["text"] for i in fn["instructions"] if i["text"].startswith(("mov eax", "xor eax"))
    }
    assert returns == {"mov eax,0Ah", "mov eax,2Dh", "mov eax,5Ah", "xor eax,eax"}

    # And .pdata still wins wherever it has an entry.
    lift = next(f for f in data if f["symbol"] == "AFGConveyorLiftHologram::GetRotationStep")
    assert lift["size_source"] == "pdata"


def test_a_split_function_comes_back_whole_through_its_chained_pdata(tmp_path):
    """MSVC cut ValidateConveyorBelt into three chunks; all three must decode."""
    data = _disasm(tmp_path, "AFGConveyorBeltHologram::ValidateConveyorBelt")
    assert len(data) == 1
    fn = data[0]
    assert fn["size_source"] == "pdata-chained"
    assert fn["chunks"] == [
        {"rva": "0xaa4e50", "size": 27},
        {"rva": "0xaa4e6b", "size": 370},
        {"rva": "0xaa4fdd", "size": 668},
    ]
    assert fn["size"] == 1065 == sum(c["size"] for c in fn["chunks"])

    rvas = [int(i["rva"], 16) for i in fn["instructions"]]
    assert rvas == sorted(rvas), "chunks must be decoded in RVA order"
    # The bound the entry chunk cannot reach: the max-length comparison.
    comparison = [i for i in fn["instructions"] if i["rva"] == "0xaa50a9"]
    assert comparison and comparison[0]["member"]["name"] == "mMaxSplineLength"
