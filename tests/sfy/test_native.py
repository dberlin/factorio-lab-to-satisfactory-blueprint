"""``native.json`` is what ``tools/sfy-native`` reads out of the shipped DLL.

The seven hologram limits are C++ constructor immediates: no cooked asset, no
header initialiser and no Docs.json entry carries them, so the only source left
is the machine code. These tests hold the committed extraction to the four
values the public headers state outright -- the oracle -- and to the evidence
every unknown value must carry with it.
"""

import json
import sys
from pathlib import Path

import pytest

from flab2bp.sfy import docs

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import sfy_disasm  # noqa: E402
import sfy_native_directions  # noqa: E402
import sfy_native_rules  # noqa: E402

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


#: How a function's end may have been established for its stores to be
#: trusted. ``ret`` and ``truncated`` are the two that mean the rest of the
#: function was never read, so a value read out of one is a value read out of
#: half a function.
BOUNDED = {"pdata", "pdata-chained", "pdb-procedure-length"}


def test_every_reported_store_says_how_its_function_was_bounded():
    """``extract`` takes the same three-source bound ``disasm`` does, and says so.

    It used to cut a function at its first ``ret`` even inside a ``.pdata``
    range, so a store MSVC emitted after an early return, or in a chained chunk,
    was never traced. Every record in ``native.json`` that names a function now
    carries the ``size_source`` that bounded it, and none of them is a guess.
    """
    seen = 0
    for cls in _native()["classes"].values():
        for name, member in cls["members"].items():
            assert (member.get("size_source") is None) == (member.get("set_in") is None), name
            for record in (member, *member.get("also_set_in", ())):
                if record.get("set_in") is None:
                    continue
                assert record["size_source"] in BOUNDED, (name, record)
                seen += 1
    assert seen > 10


def test_the_lift_heights_name_both_functions_that_overwrite_them():
    """``UpdateTopTransform``'s stores sit past its first ``ret``.

    They are the ones the old first-``ret`` bound could not see: the rule that
    the lift heights are computed rather than constant was right, but only one
    of the two functions that compute them was on record.
    """
    members = _native()["classes"]["AFGConveyorLiftHologram"]["members"]
    for member in ("mMinimumHeight", "mMinimumHeightWithVerticalConnection"):
        reason = members[member]["reason"]
        assert "AFGConveyorLiftHologram::BeginPlay" in reason, reason
        assert "AFGConveyorLiftHologram::UpdateTopTransform" in reason, reason


def _function(size_source: str, rvas=(), symbol: str = "UFGThing::UFGThing") -> dict:
    """A synthetic ``sfy-native disasm`` record, bounded however the caller says."""
    return {
        "symbol": symbol,
        "rva": "0x1000",
        "size": 4,
        "size_source": size_source,
        "instructions": [{"rva": rva, "bytes": "90", "text": "nop"} for rva in rvas],
    }


def test_a_truncated_function_is_the_only_kind_no_absence_may_be_claimed_from():
    whole = [_function(source) for source in sfy_disasm.BOUNDED_SIZE_SOURCES]
    assert sfy_disasm.unbounded(whole) == []
    sfy_disasm.require_bounded(whole, "nothing writes offset 600")

    for source in ("ret", "truncated"):
        short = [*whole, _function(source)]
        assert sfy_disasm.unbounded(short) == [f"UFGThing::UFGThing @ 0x1000: size_source {source}"]
        with pytest.raises(SystemExit) as caught:
            sfy_disasm.require_bounded(short, "nothing writes offset 600")
        assert "nothing writes offset 600" in str(caught.value)
        assert source in str(caught.value)
    # A record from a tool too old to say is unbounded, not assumed whole.
    assert sfy_disasm.unbounded([{"symbol": "X::Y", "rva": "0x20"}]) == [
        "X::Y @ 0x20: size_source absent"
    ]


def _directions_run(factory_offset: int = 600):
    """A fake ``run`` whose only whole function is the factory constructor."""
    factory = sfy_native_directions.COMPONENTS["FGFactoryConnectionComponent"]
    store = {
        "rva": factory["evidence"][0],
        "bytes": "c6",
        "text": f"mov byte ptr [rbx+{factory_offset:X}h],0",
        "member": {"class": factory["class"], "name": "mDirection", "offset": factory_offset},
    }
    seed = _function("pdata", symbol=f"{factory['class']}::{factory['class']}")
    seed["instructions"] = [store]

    def run(symbol: str) -> list[dict]:
        if symbol == f"{factory['class']}::{factory['class']}":
            return [seed]
        # Every constructor of every other chain comes back cut short.
        return [_function("truncated", symbol=symbol)]

    return run


def _offsets(**by_member: int) -> dict[tuple[str, str], int]:
    """The PDB offsets ``_components`` asks for, keyed the way it keys them."""
    return {
        (spec["layout_class"], spec["member"]): by_member[spec["member"]]
        for spec in sfy_native_directions.COMPONENTS.values()
        if spec["member"]
    }


def test_the_directions_script_will_not_call_a_truncated_constructor_a_proven_absence():
    """``_components`` claims no constructor in the pipe chain writes the member."""
    spec = sfy_native_directions.COMPONENTS["FGPipeConnectionFactory"]
    with pytest.raises(SystemExit) as caught:
        sfy_native_directions._components(
            _directions_run(), _offsets(mDirection=600, mPipeConnectionType=600)
        )
    message = str(caught.value)
    assert "no constructor in the chain writes mPipeConnectionType at offset 600" in message
    assert spec["member"] in message
    assert "truncated" in message


def test_the_pipe_entries_take_their_offset_from_the_pipe_classes_own_member():
    """``mPipeConnectionType``'s offset is read for *that* member, never borrowed.

    It and ``mDirection`` both sit at 600 in the shipped build, which is exactly
    why the two must be read separately: move one and the other must not follow.
    """
    pipe = ("UFGPipeConnectionComponentBase", "mPipeConnectionType")
    for name in ("FGPipeConnectionComponent", "FGPipeConnectionFactory"):
        spec = sfy_native_directions.COMPONENTS[name]
        assert (spec["layout_class"], spec["member"]) == pipe
        # The class the PDB declares the member on is where the chain starts.
        assert spec["constructors"][0].split("::")[0] == pipe[0]

    # Every constructor whole, so the absence may be claimed and the entries
    # come back; the pipe member is deliberately put somewhere else from
    # `mDirection`, and the pipe entries follow it rather than the factory's.
    def run(symbol: str) -> list[dict]:
        factory = sfy_native_directions.COMPONENTS["FGFactoryConnectionComponent"]
        if symbol == f"{factory['class']}::{factory['class']}":
            return _directions_run(600)(symbol)
        return [_function("pdata", symbol=symbol)]

    entries, offset = sfy_native_directions._components(
        run, _offsets(mDirection=600, mPipeConnectionType=904)
    )
    assert offset == 600
    by_class = {entry["component_class"]: entry for entry in entries}
    assert by_class["FGFactoryConnectionComponent"]["offset"] == 600
    for name in ("FGPipeConnectionComponent", "FGPipeConnectionFactory"):
        assert by_class[name]["offset"] == 904
    # A power connection has no direction member, so it gets no offset at all.
    assert by_class["FGPowerConnectionComponent"]["offset"] is None


def test_a_store_is_found_at_its_offset_however_the_formatter_prints_it():
    """``_stores_at`` reads the displacement out of the operand, not one spelling.

    Below 0x0A the formatter drops the ``h``, at 0 it drops the displacement
    altogether, and an indexed form works the address out at run time -- which
    is a computation, and so not the absence of a write.
    """
    at = sfy_native_directions._written_at
    assert at("mov byte ptr [rbx+258h],0") == (600, False)
    assert at("mov dword ptr [rcx+8],1") == (8, False)
    assert at("mov byte ptr [rcx+0Ah],1") == (10, False)
    assert at("mov qword ptr [rcx],rax") == (0, False)
    assert at("mov qword ptr [rbx-10h],rsi") == (-16, False)
    assert at("mov qword ptr [rdi+rax*8+258h],rsi") == (600, True)
    # A read is not a write, and a `lea` computes an address rather than storing.
    assert at("movss xmm0,dword ptr [rbx+258h]") is None
    assert at("lea rbx,[r14+810h]") is None

    def function(*texts: str) -> dict:
        return {
            "symbol": "UFGThing::UFGThing",
            "instructions": [{"rva": f"0x{n:x}", "text": text} for n, text in enumerate(texts)],
        }

    found = sfy_native_directions._stores_at(
        [function("mov qword ptr [rcx],rax", "mov dword ptr [rcx+8],1")], 0
    )
    assert found == ["UFGThing::UFGThing 0x0: mov qword ptr [rcx],rax"]
    assert sfy_native_directions._stores_at([function("mov dword ptr [rcx+8],1")], 8)
    assert sfy_native_directions._stores_at([function("mov byte ptr [rcx+0Ah],1")], 10)
    # The indexed form counts: it is a computation, not a proven absence.
    assert sfy_native_directions._stores_at([function("mov qword ptr [rdi+rax*8+258h],rsi")], 600)


def test_a_rule_read_from_a_function_that_was_cut_short_cannot_stay_extracted():
    """``belt.max_length`` is ``extracted``/``refuse`` -- until its bound goes."""
    rule_id = "belt.max_length"
    evidence = sfy_native_rules.EVIDENCE[rule_id]
    assert sfy_native_rules.INTERPRETATIONS[rule_id][:2] == ("extracted", "refuse")

    whole = sfy_native_rules._rule(rule_id, _function("pdata-chained", evidence))
    assert whole["status"] == "extracted"
    assert "NOT READ IN FULL" not in whole["comparison"]

    cut = sfy_native_rules._rule(rule_id, _function("ret", evidence))
    assert cut["status"] == "partial"
    assert "NOT READ IN FULL" in cut["comparison"]
    assert "size_source ret" in cut["comparison"]
    # Everything the rule does quote is still there; only the claim shrank.
    assert cut["evidence"] == whole["evidence"]


def test_an_effect_of_none_is_an_absence_and_fails_on_a_truncated_function(monkeypatch):
    """No shipped rule claims ``none`` any more, and the guard still holds.

    ``lift.step`` was the last one, and it was wrong: ``UpdateTopTransform``
    snaps the height onto a multiple of ``mStepHeight``, so the rule is
    ``extracted``/``snap`` now.  The guard it used to exercise is about the
    claim rather than about that rule, so the claim is made here instead: an
    effect of ``none`` says the instructions that were read enforce nothing,
    which cannot be said of a function the tool could not read to its end.
    """
    rule_id = "lift.step"
    evidence = sfy_native_rules.EVIDENCE[rule_id]
    status, _effect, comparison, interpretation = sfy_native_rules.INTERPRETATIONS[rule_id]
    monkeypatch.setitem(
        sfy_native_rules.INTERPRETATIONS, rule_id, ("partial", "none", comparison, interpretation)
    )
    assert status == "extracted"

    assert sfy_native_rules._rule(rule_id, _function("pdata", evidence))["effect"] == "none"
    with pytest.raises(SystemExit) as caught:
        sfy_native_rules._rule(rule_id, _function("truncated", evidence))
    assert "an effect of 'none'" in str(caught.value)
    assert "truncated" in str(caught.value)


def test_the_step_rule_quotes_the_instructions_that_snap_a_lifts_height():
    """``lift.step`` is the hologram's own quantisation, read at five addresses.

    ``floor(raw / mStepHeight + 0.5) * mStepHeight``: the divide, the half, the
    floor and the multiply back, plus the load of ``mStepHeight`` they all use.
    A rule that no longer quotes them is one this project may not refuse a
    height on, so the addresses are pinned here as well as in the payload.
    """
    rule_id = "lift.step"
    assert sfy_native_rules.INTERPRETATIONS[rule_id][:2] == ("extracted", "snap")
    evidence = sfy_native_rules.EVIDENCE[rule_id]
    for rva in ("0xaa46e8", "0xaa4769", "0xaa476d", "0xaa4776", "0xaa477c"):
        assert rva in evidence, rva


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
