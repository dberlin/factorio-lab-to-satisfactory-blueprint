"""Read the hologram's placement rules out of the shipped binary.

Legality is what the build gun's hologram does or allows. Nothing in a community
blueprint corpus establishes it -- a corpus carries clipped geometry, hacked
saves and older game versions -- so every rule in
``src/flab2bp/sfy/data/hologram_rules.json`` comes from the machine code of the
validator the hologram itself runs.

Run it after ``tools/sfy-native`` and before ``scripts/sfy_registry.py``::

    uv run python scripts/sfy_native_rules.py

It needs cargo and the game install (the shipped DLL *and* its PDB), because it
drives ``sfy-native disasm`` once per rule: for
``AFGConveyorBeltHologram::ValidateCurvature`` and its sixteen siblings it takes
the function's entry RVA, the ``this``-relative members it reads, the ``.rdata``
constants its operands point at and the call targets that resolved to a symbol
-- all four straight from the tool -- and copies the instructions listed in
:data:`EVIDENCE` out of the same output, verbatim. Since Task 5 the tool
stitches a split function's chained ``.pdata`` chunks back together, so a
validator MSVC cut into pieces is read whole rather than to the end of its
entry chunk.

**What is written by hand and what is not.** :data:`INTERPRETATIONS` carries a
status, a transcription of the comparison and what it means for a placer;
:data:`EVIDENCE` carries the addresses those sentences were read at. Everything
else is the tool's. The status obeys one rule:

``extracted``      the comparison's operands and its branch are in the evidence
``partial``        the members are seen being read, but the comparison is in a
                   callee this did not follow, or past the end of what
                   ``sfy-native disasm`` can prove is the function -- the
                   ``comparison`` text says which, and names it
``unextractable``  nothing was read, and ``interpretation`` says why

A number from a header comment is not evidence and a number from the corpus is
not evidence. If a comparison cannot be read, the rule says ``partial`` and the
placer treats the bound as unknown.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flab2bp.sfy import docs

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "flab2bp" / "sfy" / "data"
NATIVE_TOOL = ROOT / "tools" / "sfy-native"

WIN64 = "FactoryGame/Binaries/Win64"
MODULE = "FactoryGameEGS-FactoryGame-Win64-Shipping"
HEADERS = "CommunityResources/Headers.zip"

# Rule id -> the function that decides it, and the header that declares it.
# The class is the one the symbol belongs to, which is not always the class the
# rule is about: a belt's maximum length is decided in ``ValidateConveyorBelt``,
# and every hologram's clearance in ``AFGHologram::CheckClearance``.
TARGETS: dict[str, tuple[str, str, str]] = {
    "belt.curvature": (
        "AFGConveyorBeltHologram",
        "ValidateCurvature",
        "Hologram/FGConveyorBeltHologram.h:105",
    ),
    "belt.incline": (
        "AFGConveyorBeltHologram",
        "ValidateIncline",
        "Hologram/FGConveyorBeltHologram.h:103",
    ),
    "belt.min_length": (
        "AFGConveyorBeltHologram",
        "ValidateMinLength",
        "Hologram/FGConveyorBeltHologram.h:104",
    ),
    "belt.max_length": (
        "AFGConveyorBeltHologram",
        "ValidateConveyorBelt",
        "Hologram/FGConveyorBeltHologram.h:72",
    ),
    "belt.clearance": (
        "AFGConveyorBeltHologram",
        "UpdateClearanceData",
        "Hologram/FGConveyorBeltHologram.h:84",
    ),
    "belt.snap_directions": (
        "AFGConveyorBeltHologram",
        "SetupSnappedConnectionDirections",
        "Hologram/FGConveyorBeltHologram.h:79",
    ),
    "pipe.min_length": (
        "AFGPipelineHologram",
        "ValidateMinLength",
        "Hologram/FGPipelineHologram.h:137",
    ),
    "pipe.curvature": (
        "AFGPipelineHologram",
        "ValidateCurvatureAndReturnFaultyPosition",
        "Hologram/FGPipelineHologram.h:145",
    ),
    "pipe.max_length": (
        "AFGPipelineHologram",
        "ValidatePipeline",
        "Hologram/FGPipelineHologram.h:70",
    ),
    "pipe.fluid_requirements": (
        "AFGPipelineHologram",
        "ValidateFluidRequirements",
        "Hologram/FGPipelineHologram.h:151",
    ),
    "lift.height_range": (
        "AFGConveyorLiftHologram",
        "UpdateTopTransform",
        "Hologram/FGConveyorLiftHologram.h:65",
    ),
    "lift.step": (
        "AFGConveyorLiftHologram",
        "UpdateTopTransform",
        "Hologram/FGConveyorLiftHologram.h:107",
    ),
    "lift.placement": (
        "AFGConveyorLiftHologram",
        "CheckValidPlacement",
        "Hologram/FGConveyorLiftHologram.h:62",
    ),
    "lift.clearance": (
        "AFGConveyorLiftHologram",
        "UpdateClearance",
        "Hologram/FGConveyorLiftHologram.h:70",
    ),
    "buildable.grid_snap": (
        "AFGBuildableHologram",
        "SnapToFloor",
        "Hologram/FGBuildableHologram.h:445",
    ),
    "buildable.rotation_step": (
        "AFGBuildableHologram",
        "GetRotationStep",
        "Hologram/FGBuildableHologram.h:263",
    ),
    "buildable.clearance": (
        "AFGHologram",
        "CheckClearance",
        "Hologram/FGHologram.h:545",
    ),
}

# The instructions each rule was read at. Every one is copied out of the tool's
# output by address, so a rule whose function moved in a game update fails the
# run here rather than shipping a stale quotation. Order here is for reading;
# ``_rule`` sorts the evidence it writes by RVA, so the file is always in
# address order whatever order a branch actually runs in.
EVIDENCE: dict[str, tuple[str, ...]] = {
    "belt.curvature": (
        "0xaa529a", "0xaa52d0", "0xaa52dd", "0xaa52ea", "0xaa52ee", "0xaa52f6",
        "0xaa52fa", "0xaa52fc", "0xaa5300", "0xaa5305", "0xaa5307", "0xaa5340",
        "0xaa5356", "0xaa535b", "0xaa53d6", "0xaa53df", "0xaa53e3", "0xaa53e7",
        "0xaa540a", "0xaa540f", "0xaa54a3", "0xaa54a7", "0xaa54ab", "0xaa54b0",
        "0xaa54b4", "0xaa54b8", "0xaa54bf", "0xaa54c5", "0xaa54cd", "0xaa54d2",
        "0xaa54da", "0xaa54de", "0xaa54e2", "0xaa54ea", "0xaa54ee", "0xaa54f6",
        "0xaa54f9", "0xaa54fb", "0xaa54fd", "0xaa5503", "0xaa554e",
    ),
    "belt.incline": (
        "0xaa556d", "0xaa5676", "0xaa568b", "0xaa5691", "0xaa56e2", "0xaa56e9",
        "0xaa56ef", "0xaa56fc", "0xaa5709", "0xaa5711", "0xaa5744", "0xaa576a",
        "0xaa579b", "0xaa579f", "0xaa57a3", "0xaa57b6", "0xaa57bc", "0xaa57c1",
        "0xaa57c9", "0xaa57ce", "0xaa57d2", "0xaa57d6", "0xaa57de", "0xaa57e3",
        "0xaa57e7", "0xaa57eb", "0xaa57ee", "0xaa5804", "0xaa5864",
    ),
    "belt.min_length": (
        "0xaa587d", "0xaa589b", "0xaa58b2", "0xaa592f", "0xaa5944", "0xaa594a",
        "0xaa5994", "0xaa599b", "0xaa59ae", "0xaa59b4", "0xaa59c0", "0xaa59c4",
        "0xaa59c8", "0xaa59cc", "0xaa59d0", "0xaa59d8", "0xaa59e0", "0xaa59e4",
        "0xaa59e8", "0xaa59ed", "0xaa59f1", "0xaa5a07", "0xaa5a35",
    ),
    "belt.max_length": (
        "0xaa4e5a", "0xaa4e65", "0xaa5097", "0xaa509e", "0xaa50a1", "0xaa50a3",
        "0xaa50a9", "0xaa50b0", "0xaa50b2", "0xaa50dc", "0xaa50e4", "0xaa50eb",
        "0xaa50ed", "0xaa511f", "0xaa5126", "0xaa5128", "0xaa515e", "0xaa5180",
        "0xaa5187", "0xaa518c", "0xaa5193", "0xaa5195",
    ),
    "belt.clearance": (
        "0xaa16c2", "0xaa1714", "0xaa171b", "0xaa1736", "0xaa173d", "0xaa1747",
        "0xaa174d",
    ),
    "belt.snap_directions": (
        "0xa8b96f", "0xa8b994", "0xa8b9a4", "0xa8b9d1", "0xa8b9d8", "0xa8b9da",
        "0xa8b9e4", "0xa8b9e9", "0xa8b9ef", "0xa8b9f6", "0xa8b9fd",
    ),
    "pipe.min_length": (
        "0xae98ad", "0xae98cb", "0xae98e2", "0xae9a00", "0xae9a08", "0xae9a10",
        "0xae9a14", "0xae9a1d", "0xae9a21", "0xae9a37", "0xae9a65",
    ),
    "pipe.curvature": (
        "0xae939a", "0xae93d0", "0xae93d6", "0xae93e0", "0xae93ef", "0xae93f4",
        "0xae93f8", "0xae9400", "0xae9404", "0xae940a", "0xae940f", "0xae95ab",
        "0xae95af", "0xae95b3", "0xae95b8", "0xae95c3", "0xae95c8", "0xae95cc",
        "0xae95d0", "0xae95d4", "0xae95d8", "0xae95df", "0xae95e5", "0xae95ed",
        "0xae95f2", "0xae95fe", "0xae9609", "0xae960d", "0xae9611", "0xae9614",
        "0xae961e", "0xae966f", "0xae9678",
    ),
    "pipe.max_length": (
        "0xae9a80", "0xae9a8b", "0xae9c46", "0xae9c4d", "0xae9c53", "0xae9c5a",
        "0xae9c5c", "0xae9c8d", "0xae9c9e", "0xae9ca8", "0xae9caa", "0xae9cdc",
        "0xae9ce3", "0xae9ce5", "0xae9d29", "0xae9d30", "0xae9d35", "0xae9d3d",
        "0xae9d40", "0xae9d42",
    ),
    "pipe.fluid_requirements": (
        "0xae9698", "0xae969f", "0xae96a5", "0xae96ab", "0xae96b2", "0xae96b8",
        "0xae96be", "0xae96c5", "0xae96cb", "0xae96d2", "0xae96d8", "0xae96e3",
        "0xae96fc", "0xae9707", "0xae9719", "0xae971c", "0xae9722", "0xae9725",
        "0xae973d", "0xae9748", "0xae974b", "0xae9781", "0xae9784", "0xae9792",
        "0xae979d", "0xae97a0", "0xae97d6", "0xae97d9", "0xae97e7", "0xae97f2",
        "0xae9818", "0xae981d", "0xae9828", "0xae9833", "0xae985c", "0xae985f",
        "0xae9862", "0xae9869", "0xae987d", "0xae988c",
    ),
    "lift.height_range": (
        "0xaa46b8", "0xaa46cf", "0xaa46e8", "0xaa4946", "0xaa494d", "0xaa4953",
        "0xaa495d", "0xaa4965", "0xaa4968", "0xaa4979", "0xaa497d", "0xaa4987",
        "0xaa4998", "0xaa499f", "0xaa4a6f", "0xaa4a7d",
    ),
    "lift.step": (
        "0xaa46e8", "0xaa48ca", "0xaa48db", "0xaa4965", "0xaa480f", "0xaa482c",
        "0xaa4830", "0xaa4843", "0xaa4859", "0xaa4863", "0xaa4867",
    ),
    "lift.placement": (
        "0xa68189", "0xa681c0", "0xa681cd", "0xa681db", "0xa681e7", "0xa681f3",
        "0xa681fa", "0xa68201", "0xa68203", "0xa6822f", "0xa68234", "0xa6824c",
        "0xa68253", "0xa6825a", "0xa6825c", "0xa68288",
    ),
    "lift.clearance": (
        "0xaa141d", "0xaa1433", "0xaa147b", "0xaa150d",
    ),
    "buildable.grid_snap": (
        "0xa8f41d", "0xa8f428", "0xa8f42b",
    ),
    "buildable.rotation_step": (
        "0xa7c050", "0xa7c057", "0xa7c059", "0xa7c062", "0xa7c06a", "0xa7c06c",
        "0xa7c073", "0xa7c075", "0xa7c07a",
    ),
    "buildable.clearance": (
        "0xab8012", "0xab8242", "0xab827a", "0xab83ea", "0xab844b",
    ),
}

# status, comparison, interpretation. The comparison is a transcription of the
# evidence above and nothing else; the interpretation is what a placer should do
# about it. Neither is ever filled in from a header comment or from the corpus.
INTERPRETATIONS: dict[str, tuple[str, str, str]] = {
    "belt.curvature": (
        "extracted",
        "n = FMath::RoundToInt(GetSplineLength() * 0.02) samples "
        "(0xaa52dd mulss 0.02, 0xaa52ea addss xmm2,xmm2, 0xaa52ee addss 0.5, "
        "0xaa52f6 cvtss2si, 0xaa52fa sar esi,1 -- UE's RoundToInt on SSE); "
        "step = GetSplineLength() / n (0xaa5300 divss). For i in [0,n): "
        "A = GetTangentAtDistanceAlongSpline(i*step).GetSafeNormal2D(), "
        "B = the same at (i+1)*step (0xaa535b, and again at 0xaa540f; the "
        "1.0 / 1e-8 / 1-over-sqrt dance at 0xaa53d6..0xaa53e7 is GetSafeNormal2D, "
        "which zeroes Z); theta = acos(clamp(A.B, -1, 1)) "
        "(0xaa54a3..0xaa54b4 dot, 0xaa54b8/0xaa54c5 clamp, 0xaa54cd call acos); "
        "then `comiss xmm3, xmm2; jb` at 0xaa54f6/0xaa54f9 returns false when "
        "(float)(step / theta) < mBendRadius * 1.5 - 15.0 "
        "(0xaa54d2 mBendRadius, 0xaa54e2 mulss 1.5, 0xaa54ee subss 15.0).",
        "The belt hologram rejects a turn whose horizontal radius of curvature "
        "is below mBendRadius * 1.5 - 15.0 -- 283.5 cm at the shipped "
        "mBendRadius of 199.0, not 199.0. The radius is estimated per sample as "
        "arc length over the angle between the two tangents, both flattened to "
        "the XY plane by GetSafeNormal2D, so a climb is not curvature here; "
        "belt.incline judges that separately. Two further bounds are part of "
        "the rule. First, sampling is one point per 50 cm of spline, so a turn "
        "tighter than the sample spacing can hide between samples. Second, and "
        "decisively, ValidateConveyorBelt only calls this when the build gun is "
        "in the *curve* build mode (0xaa515e reads mBuildModeCurve, 0xaa5180 "
        "AFGHologram::IsCurrentBuildMode, 0xaa5187 je skips the call): in the "
        "straight and zoop modes the game never checks curvature at all. A "
        "placer that lays its own arcs should use mBendRadius as the radius to "
        "lay them on and 283.5 cm as the floor it must not go under.",
    ),
    "belt.incline": (
        "extracted",
        "For i in [1, mSplineData.Num()): d = mSplineData[i].Location - "
        "mSplineData[i-1].Location (0xaa56fc/0xaa5709/0xaa5711 subsd on the "
        "three doubles), u = d.GetSafeNormal() (0xaa5744 == 1.0, 0xaa576a < "
        "1e-8 -> ZeroVector, 0xaa579b..0xaa57a3 scale by 1/sqrt); "
        "elevation = |PI/2 - acos(clamp(u.Z, -1, 1))| (0xaa57b6/0xaa57c1 clamp, "
        "0xaa57c9 call acos, 0xaa57ce/0xaa57d2 subsd from 1.5707963705062866, "
        "0xaa57e3 andps with the 0x7fff... sign mask); then `comiss xmm1, xmm0; "
        "ja` at 0xaa57eb/0xaa57ee returns false when it exceeds "
        "mMaxIncline * 0.017453292 (0xaa57d6 mMaxIncline, 0xaa57de mulss "
        "degrees-to-radians).",
        "Every straight run between two consecutive spline points must rise at "
        "no more than mMaxIncline degrees above horizontal -- 35.0 in the "
        "shipped build, and exactly 35.0 is legal because the branch is a "
        "strict `ja`. The test is on the chord between stored points, not on "
        "the curve, and |asin| makes it symmetric: a descent is bounded the "
        "same way. mSplineData.Num() < 2 traps on the array bound check at "
        "0xaa556d rather than returning, so a one-point spline never reaches "
        "here.",
    ),
    "belt.min_length": (
        "extracted",
        "total = sum over i in [1, mSplineData.Num()) of "
        "|mSplineData[i].Location - mSplineData[i-1].Location| "
        "(0xaa59ae..0xaa59d0 squared deltas, 0xaa59e0 sqrtsd, 0xaa59e4 addsd "
        "into the running total); `comiss xmm8, xmm7; ja` at 0xaa59ed/0xaa59f1 "
        "returns true as soon as total > mMeshLength * 0.5001 (0xaa589b "
        "mMeshLength, 0xaa58b2 mulss 0.5001); falling out of the loop returns "
        "false (0xaa5a07 xor al,al).",
        "A belt is long enough exactly when its polyline is longer than "
        "0.5001 * mMeshLength -- a hair over half of one belt mesh segment. "
        "mMeshLength is not a constructor immediate: AFGConveyorBeltHologram::"
        "BeginPlay caches it from the default buildable's mesh (0xaa4b60 reads "
        "mMesh and stores mMeshLength), so the numeric floor depends on the "
        "belt mark being placed and is not in native.json. The comparison is "
        "strict, so a belt exactly half a mesh long is too short.",
    ),
    "belt.max_length": (
        "extracted",
        "ValidateConveyorBelt is three .pdata chunks (0xaa4e50 +27, 0xaa4e6b "
        "+370, 0xaa4fdd +668) stitched back together through their chained "
        "UNWIND_INFO. An upgrade skips the validator outright: `cmp qword ptr "
        "[rcx+828h], 0; jne` at 0xaa4e5a/0xaa4e65 on mUpgradedConveyorBelt. "
        "Otherwise 0xaa5097 loads mSplineComponent, 0xaa50a1 `je` skips a null "
        "one, 0xaa50a3 `call qword ptr [0EEB7C8h]` calls it -- the import slot "
        "for USplineComponent::GetSplineLength, which the tool reports as the "
        "IAT constant rather than a resolved call -- and `comiss xmm0, dword "
        "ptr [rbx+84Ch]; jbe` at 0xaa50a9/0xaa50b0 adds UFGCDConveyorTooLong "
        "(0xaa50b2 StaticClass, 0xaa50dc AFGHologram::AddConstructDisqualifier) "
        "when that float is strictly greater than mMaxSplineLength. The other "
        "three bounds follow in order: ValidateMinLength at 0xaa50e4 -> "
        "UFGCDConveyorTooShort (0xaa50eb jne, 0xaa50ed), ValidateIncline at "
        "0xaa511f -> UFGCDConveyorTooSteep (0xaa5126 jne, 0xaa5128), and -- "
        "only when AFGHologram::IsCurrentBuildMode(mBuildModeCurve) is true "
        "(0xaa515e, 0xaa5180, 0xaa5187 je) -- ValidateCurvature at 0xaa518c -> "
        "UFGCDConveyorInvalidShape (0xaa5193 jne, 0xaa5195).",
        "A belt is too long exactly when its spline component's arc length "
        "exceeds mMaxSplineLength -- 5600.1 cm in the shipped build (a "
        "constructor immediate, data/native.json). Three things follow. The "
        "measure is arc length along the spline, not the distance between the "
        "two poles, so a curve reaches the bound before its endpoints suggest. "
        "The branch is a strict `ja`-equivalent (`jbe` past the disqualifier), "
        "so exactly 5600.1 is legal. And the belt is refused, not clipped: "
        "UFGCDConveyorTooLong is a construct disqualifier. This function is "
        "also where the belt's four bounds are ordered and where "
        "belt.curvature is gated behind the curve build mode, so a placer that "
        "never uses that mode is never curvature-checked by the game.",
    ),
    "belt.clearance": (
        "partial",
        "UpdateClearanceData empties mClearanceData and hands the spline "
        "component, mSplineData, the root component's transform and "
        "mMaxSplineLength to AFGBuildableConveyorBelt::CreateClearanceData "
        "(0xaa174d), which builds the boxes. No comparison is made here.",
        "A belt's clearance is whatever the buildable's own "
        "CreateClearanceData lays along the spline; the hologram only supplies "
        "the inputs. Whether a given box overlaps a neighbour is then decided "
        "by buildable.clearance. Extracting the box geometry means reading "
        "AFGBuildableConveyorBelt::CreateClearanceData, which this did not do.",
    ),
    "belt.snap_directions": (
        "extracted",
        "When snapped to a wall passthrough, `cmp byte ptr [rsi+258h], 3; je` "
        "at 0xa8b9d1/0xa8b9d8 leaves both ends alone if the other connection's "
        "mDirection is FCD_SNAP_ONLY (3); otherwise "
        "mConnectionComponents[0]->mDirection = "
        "other->GetCompatibleSnapDirection() (0xa8b9e4 call, 0xa8b9e9 store) "
        "and mConnectionComponents[1]->mDirection = other->mDirection "
        "(0xa8b9f6 load, 0xa8b9fd store).",
        "The hologram does not choose a belt's direction freely: it takes the "
        "direction the connection it snapped to is compatible with and mirrors "
        "it onto the far end. A connection marked FCD_SNAP_ONLY (3 in "
        "EFactoryConnectionDirection, FGFactoryConnectionComponent.h:32) hands "
        "out no direction at all, which is what a conveyor pole is. A placer "
        "must take a port's direction from the port, never assign one.",
    ),
    "pipe.min_length": (
        "extracted",
        "The same shape as the belt's, with a different factor: total = sum of "
        "|mSplineData[i].Location - mSplineData[i-1].Location| (0xae9a10 "
        "sqrtsd, 0xae9a14 addsd); `comiss xmm8, xmm7; ja` at "
        "0xae9a1d/0xae9a21 returns true when total > mMeshLength * 0.5 "
        "(0xae98cb mMeshLength, 0xae98e2 mulss 0.5), and falling out returns "
        "false (0xae9a37).",
        "A pipeline is long enough when its polyline exceeds half of one pipe "
        "mesh segment -- 0.5, where the belt uses 0.5001. mMeshLength is "
        "cached from the default buildable in BeginPlay, so the numeric floor "
        "follows the pipe mark. Failing it adds UFGCDPipeTooShort.",
    ),
    "pipe.curvature": (
        "extracted",
        "n = FMath::RoundToInt(GetSplineLength() * (2.0 / mMinBendRadius)) "
        "(0xae93d6 movss 2.0, 0xae93e0 divss by mMinBendRadius, 0xae93ef mulss "
        "by the length, then the RoundToInt sequence at 0xae93f4..0xae9404); "
        "step = length / n (0xae940a). Per sample theta = acos(clamp(A.B,-1,1)) "
        "over two *three-dimensional* normalised tangents (0xae95af..0xae95b8 "
        "scale all three components, 0xae95c3..0xae95d4 the three-term dot, "
        "0xae95ed call acos); `comiss xmm3, xmm2; jb` at 0xae9611/0xae9614 "
        "leaves the loop when (float)(step / theta) < mMinBendRadius * 1.05 "
        "(0xae95f2 mMinBendRadius, 0xae95fe mulss 1.05), returning "
        "(i + 0.5) * step as the faulty distance (0xae966f/0xae9678); a clean "
        "spline returns -1.0 (0xae961e).",
        "A pipeline is rejected where its radius of curvature falls below "
        "mMinBendRadius * 1.05 -- 78.75 cm at the shipped mMinBendRadius of "
        "75.0. Two things differ from the belt. The tangents are normalised in "
        "3D, so a pipe's climb counts as curvature and there is no separate "
        "incline rule; and the sample spacing scales with the radius "
        "(mMinBendRadius / 2, i.e. 37.5 cm) instead of being fixed. The return "
        "value is a distance along the spline, so the caller can point at the "
        "offending place: ValidatePipeline turns a positive return into "
        "UFGCDPipeInvalidShape.",
    ),
    "pipe.max_length": (
        "extracted",
        "The same shape as belt.max_length, in a function of five chained "
        ".pdata chunks (0xae9a70 +33, 0xae9a91 +17, 0xae9aa2 +244, 0xae9b96 "
        "+403, 0xae9d29 +450). An upgrade skips it: `cmp qword ptr [rcx+7E8h], "
        "0; jne` at 0xae9a80/0xae9a8b on mUpgradedPipeline. Otherwise 0xae9c46 "
        "loads mSplineComponent, 0xae9c4d calls [0EEB7C8h] "
        "(USplineComponent::GetSplineLength's import slot) and `comiss xmm0, "
        "dword ptr [rbx+80Ch]; jbe` at 0xae9c53/0xae9c5a adds UFGCDPipeTooLong "
        "(0xae9c5c StaticClass, 0xae9c8d AddUnique into mConstructDisqualifiers) "
        "when the length is strictly greater than mMaxSplineLength. Then "
        "ValidateMinLength at 0xae9c9e -> UFGCDPipeTooShort (0xae9ca8 jne, "
        "0xae9caa), ValidateFluidRequirements at 0xae9cdc -> "
        "UFGCDPipeFluidTypeMismatch (0xae9ce3 jne, 0xae9ce5) and, once "
        "mBuildStep is non-zero (0xae9d29/0xae9d30), "
        "ValidateCurvatureAndReturnFaultyPosition at 0xae9d35 with `comiss "
        "xmm0, 0.0; jbe` at 0xae9d3d/0xae9d40 -> UFGCDPipeInvalidShape "
        "(0xae9d42).",
        "A pipeline is too long exactly when its spline's arc length exceeds "
        "mMaxSplineLength -- 5600.1 cm, the same number the belt uses (a "
        "constructor immediate, data/native.json), and again strict, so "
        "exactly 5600.1 is legal. The four pipe bounds run in this order: "
        "length, minimum length, fluid requirements, curvature -- and the "
        "curvature check only once the second placement step has begun, "
        "because a positive faulty-distance return is what raises "
        "UFGCDPipeInvalidShape.",
    ),
    "pipe.fluid_requirements": (
        "extracted",
        "Four chained .pdata chunks (0xae9690 +155, 0xae972b +333, 0xae9878 "
        "+20, 0xae988c +10). The preconditions are all `return true`: both "
        "mSnappedConnectionComponents must exist (0xae969f/0xae96a5 and "
        "0xae96b2/0xae96b8), neither may have mDirection 3 "
        "(0xae96be/0xae96c5 and 0xae96cb/0xae96d2), and both must pass "
        "FObjectPtr::IsA(UFGPipeConnectionComponent) (0xae96d8/0xae96e3 and "
        "0xae96fc/0xae9707, then 0xae9719/0xae971c and 0xae9722/0xae9725) -- "
        "every failure jumps to 0xae988c `mov al, 1`. "
        "UFGPipeConnectionComponent::GetFluidDescriptor is then called on each "
        "end (0xae973d, 0xae9792) and an end with no fluid is also legal: "
        "0xae9748/0xae974b and 0xae979d/0xae97a0 on the returned class, "
        "0xae9781/0xae9784 and 0xae97d6/0xae97d9 on the TSubclassOf slot, all "
        "jump to 0xae9878 `mov al, 1`. With a fluid at both ends the two "
        "descriptors are fetched once more -- 0xae97e7 into rdi (via "
        "0xae97f2/0xae9818/0xae981d) and 0xae9828 into rbx (via "
        "0xae9833/0xae985c) -- and `cmp rbx, rdi; je 0xae9878` at "
        "0xae985f/0xae9862 returns true when they are the same class; falling "
        "through returns false at 0xae9869 `xor al, al`.",
        "Two pipes may be joined only when the fluids already committed to "
        "both ends are the *same* item descriptor class: the test is identity, "
        "`cmp rbx, rdi`, with no notion of a compatible pair, and "
        "ValidatePipeline turns a false into UFGCDPipeFluidTypeMismatch. An "
        "end with nothing committed is not a conflict -- every path where "
        "either GetFluidDescriptor comes back null returns true -- so joining "
        "a carrying network to an empty one is legal, as is a pipe whose ends "
        "are not both UFGPipeConnectionComponents or where either is "
        "FCD_SNAP_ONLY (3). What GetFluidDescriptor itself reads, and so how a "
        "network's committed fluid is decided and what an empty network takes "
        "on, is in that callee and was not read here.",
    ),
    "lift.height_range": (
        "extracted",
        "UpdateTopTransform saves mMinimumHeightWithVerticalConnection and "
        "mMinimumHeight on entry (0xaa46b8/0xaa46cf) and restores them on exit "
        "(0xaa4a6f/0xaa4a7d). The height it then writes is clamped, not "
        "validated: floor = mMinimumHeightWithVerticalConnection when "
        "mSnappedPassthroughs[0] is set, else mMinimumHeight "
        "(0xaa494d..0xaa495d); 0xaa4979 `minss xmm2, xmm6` takes the smaller of "
        "mMaximumHeight (0xaa4968) and the wanted height, and 0xaa497d "
        "`maxss xmm2, xmm3` lifts the result to the floor. The downward branch "
        "at 0xaa4987..0xaa499f does the same through a sign flip.",
        "A conveyor lift cannot be given an illegal height: the hologram "
        "clamps into [minimum, mMaximumHeight] rather than refusing, so there "
        "is no disqualifier for it and no blueprint can carry a lift outside "
        "the range. The minimum is mMinimumHeightWithVerticalConnection when "
        "the lift meets a passthrough and mMinimumHeight otherwise. The "
        "numbers are not constructor immediates: "
        "AFGConveyorLiftHologram::BeginPlay computes all three from the "
        "buildable's mesh height H as H*2, H*24 and H-50, which "
        "tools/sfy-native read out of the machine code at 0xa64e70 -- see "
        "data/native.json and the LIFT_HEIGHT_FORMULAS block in "
        "scripts/sfy_registry.py. With H = 200 that is 400, 4800 and 150 cm.",
    ),
    "lift.step": (
        "partial",
        "mStepHeight is read once, at 0xaa46e8, and every use of it in "
        "UpdateTopTransform is a comparison against the wanted height "
        "(0xaa48ca ucomiss, 0xaa48db and 0xaa4965 comiss) choosing the upward "
        "or downward branch -- not a quantisation. The one rounding in the "
        "function is the passthrough case at 0xaa4830..0xaa4859, which takes "
        "the passthrough's own thickness modulo 100 "
        "(0xaa4834 imul / 0xaa4836 sar edx,5 / 0xaa4843 sub, the compiler's "
        "divide by 100) and adds mStepHeight to it "
        "(0xaa4863/0xaa4867). No instruction here snaps a free height to a "
        "multiple of mStepHeight.",
        "mStepHeight is 100.0 in the shipped build (a BeginPlay store, "
        "data/native.json), but this extraction does not show the hologram "
        "quantising a lift's height to it. The claim that every legal height "
        "is a whole number of steps above the minimum is arithmetic on the "
        "BeginPlay values, not a rule read from the binary. Until the "
        "quantisation is found -- most likely in the mesh-building path that "
        "UpdateTopTransform feeds -- a placer should keep to multiples of "
        "mStepHeight because they are known-good, not because the game was "
        "seen to require them.",
    ),
    "lift.placement": (
        "extracted",
        "After the base AFGHologram::CheckValidPlacement (0xa68189), and only "
        "once mActivePointIdx > 0 and no upgrade is in progress "
        "(0xa681c0/0xa681cd), each of the two mSnappedConnectionComponents is "
        "tested: `cmp byte ptr [rax+268h], 0; je` at 0xa681fa/0xa68201 and "
        "again at 0xa68253/0xa6825a reads "
        "UFGFactoryConnectionComponent::mHasConnectedComponent and, when it is "
        "set, adds UFGCDInvalidPlacement (0xa68203/0xa6825c StaticClass, "
        "0xa6822f/0xa68288 AFGHologram::AddConstructDisqualifier).",
        "A lift may not end on a connection that is already wired to "
        "something. The same check runs on a belt, in ValidateConveyorBelt at "
        "0xaa51c4 and 0xaa521b. Note the guard: it applies only from the "
        "second placement step onward, so the first click may land on an "
        "occupied port and only the second refuses.",
    ),
    "lift.clearance": (
        "partial",
        "UpdateClearance builds one FFGClearanceData from two -5.0 constants "
        "(0xaa141d/0xaa1433, the box's near corner) and the lift's own "
        "mMeshHeight (0xaa150d), with mSnappedPassthroughs deciding which end "
        "is trimmed (0xaa147b). The stored box is what "
        "AFGConveyorLiftHologram::GetClearanceData hands out; no overlap "
        "decision is made here.",
        "A lift's clearance is one box spanning its height, not a chain along "
        "a spline as for a belt. Whether it overlaps is decided by "
        "buildable.clearance. The exact corner arithmetic was not read.",
    ),
    "buildable.grid_snap": (
        "partial",
        "SnapToFloor loads mGridSnapSize (0xa8f41d) and passes it as the float "
        "argument of FHologramHelpers::SnapToFloor (0xa8f42b); the rounding "
        "happens in that callee. SnapToWall and SnapToFoundationSide read the "
        "same member the same way.",
        "mGridSnapSize is 100.0 by default (a constructor immediate, "
        "data/native.json) and three classes override it to 50.0 in their "
        "Blueprints -- the two power poles and the street light, which "
        "registry.json carries per buildable. What the game does with it is a "
        "snap, not a refusal: a hologram is moved onto the grid rather than "
        "rejected off it, so a placer should place on the grid and expect no "
        "disqualifier if it does not. Which axes are snapped, and whether the "
        "origin is the world or the surface, is inside "
        "FHologramHelpers::SnapToFloor and was not read.",
    ),
    "buildable.rotation_step": (
        "partial",
        "GetRotationStep returns 10 degrees when the hologram is on an "
        "attachment point with guide-line snapping on "
        "(0xa7c062 mSnappedAttachmentPoint, 0xa7c06c mSnapToGuideLines, "
        "0xa7c075 `mov eax, 0Ah`). The other three returns are past the first "
        "`ret` at 0xa7c07a, which is where `sfy-native disasm` stops for a "
        "symbol with no .pdata entry, so they are not quoted here. Chained "
        ".pdata chunks do not reach them either: 0xa7c050 has no "
        "RUNTIME_FUNCTION at all -- the neighbouring entries are "
        "0xa7bfc0..0xa7c04a and 0xa7c0d0..0xa7c140 -- so there is no chain to "
        "follow, only the `ret` bound.",
        "The build gun's rotation step is not one number. The reachable branch "
        "gives 10 degrees on an attachment point; the tail of the same "
        "function returns 90 by default, 45 under one further flag and 0 "
        "(free rotation) while snapped to a building. "
        "AFGConveyorLiftHologram::GetRotationStep, whose .pdata entry does "
        "cover the whole body, shows the same 90 (0xa7c1a2 `mov eax, 5Ah`) and "
        "the same 0 (0xa7c19a). registry.json still tags "
        "hologram_rotation_step_deg as a project constant; this rule is the "
        "evidence that 90 is the game's own default, and re-sourcing that "
        "limit is a separate change.",
    ),
    "buildable.clearance": (
        "partial",
        "CheckClearance builds a sphere over the combined clearance data "
        "(0xab8012 FHologramHelpers::CreateSphereFromCombinedClearanceData), "
        "sweeps it, asks each candidate for its own boxes through the "
        "clearance interface (0xab8242/0xab827a "
        "IFGClearanceInterface::Execute_GetClearanceData), tests them in "
        "parallel (0xab83ea ParallelFor) and adds whatever disqualifier the "
        "test returned (0xab844b AddUnique into mConstructDisqualifiers). The "
        "box-against-box decision is in AFGHologram::TestClearanceOverlap "
        "(0xad6790, 11457 bytes), which this did not follow.",
        "Clearance is the general placement rule: every hologram's boxes are "
        "swept against every overlapping actor's, and an overlap becomes a "
        "construct disqualifier. registry.json already carries each "
        "buildable's boxes with their full transform, including the 37 that "
        "are rotated and the ones flagged exclude_for_snapping, so a placer "
        "can do the same test; what it cannot yet do is reproduce the game's "
        "tolerance and its soft-versus-hard distinction, which live in "
        "TestClearanceOverlap.",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _disasm(dll: Path, pdb: Path, symbol: str, out: Path) -> list[dict[str, Any]]:
    """Run ``sfy-native disasm`` for one symbol and return its function records."""
    subprocess.run(
        [
            "cargo", "run", "--release", "--quiet", "--",
            str(dll), str(pdb), "disasm", symbol, "--out", str(out),
        ],
        cwd=NATIVE_TOOL,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def _rva(text: str) -> int:
    """``"0xaa5280"`` as a number, so evidence sorts by address and not by text."""
    return int(text, 16)


def _line(instruction: dict[str, Any]) -> str:
    """One evidence line: the tool's own text, plus whatever it annotated."""
    text = f"{instruction['rva']}: {instruction['text']}"
    member = instruction.get("member")
    if member:
        return f"{text}  ; {member['class']}::{member['name']} @{member['offset']}"
    imported = instruction.get("import")
    if imported:
        # An indirect call through an import address table slot: the slot's
        # `constant` is a pointer, so the name is the useful half.
        return f"{text}  ; -> {imported}"
    constant = instruction.get("constant")
    if constant:
        shown = [f"{key}={constant[key]}" for key in ("f32", "f64") if constant[key] is not None]
        return f"{text}  ; const {constant['at']} {' '.join(shown)}".rstrip()
    call = instruction.get("call")
    if call:
        return f"{text}  ; -> {call}"
    return text


def _rule(rule_id: str, function: dict[str, Any]) -> dict[str, Any]:
    """Turn one disassembled function into the rule record, evidence and all."""
    cls, name, header = TARGETS[rule_id]
    status, comparison, interpretation = INTERPRETATIONS[rule_id]
    by_rva = {i["rva"]: i for i in function["instructions"]}
    missing = [rva for rva in EVIDENCE[rule_id] if rva not in by_rva]
    if missing:
        raise SystemExit(
            f"{rule_id}: {cls}::{name} has no instruction at {missing} -- the function "
            "moved, and its interpretation has to be re-read before this can be written"
        )
    return {
        "id": rule_id,
        "class": cls,
        "function": name,
        "rva": function["rva"],
        "status": status,
        "reads": sorted({i["member"]["name"] for i in function["instructions"] if "member" in i}),
        "constants": sorted(
            {
                f"{i['constant']['at']} f32={i['constant']['f32']} f64={i['constant']['f64']}"
                for i in function["instructions"]
                if "constant" in i
            }
        ),
        "calls": sorted(
            {i["call"] for i in function["instructions"] if "call" in i}
            | {i["import"] for i in function["instructions"] if "import" in i}
        ),
        "comparison": comparison,
        "interpretation": interpretation,
        "evidence": [_line(by_rva[rva]) for rva in sorted(EVIDENCE[rule_id], key=_rva)],
        "header": header,
    }


def main(out: Path | None = None) -> int:
    """Write ``data/hologram_rules.json`` from the installed game's binary."""
    install = docs.satisfactory_dir()
    win64 = install / WIN64
    dll, pdb = win64 / f"{MODULE}.dll", win64 / f"{MODULE}.pdb"
    for path in (dll, pdb, install / HEADERS):
        if not path.is_file():
            raise SystemExit(f"no {path}: this needs the game install, DLL, PDB and headers")

    native = json.loads((DATA / "native.json").read_text(encoding="utf-8"))["provenance"]
    dll_sha256 = _sha256(dll)
    if dll_sha256 != native["dll_sha256"]:
        raise SystemExit(
            "the DLL has moved since native.json was written "
            f"({dll_sha256} vs {native['dll_sha256']}): re-run tools/sfy-native first, "
            "because these rules quote member offsets that file resolved"
        )

    scratch = DATA / ".hologram_rules_disasm.json"
    rules = []
    try:
        for rule_id, (cls, name, _header) in TARGETS.items():
            symbol = f"{cls}::{name}"
            found = [f for f in _disasm(dll, pdb, symbol, scratch) if f["symbol"] == symbol]
            if len(found) != 1:
                raise SystemExit(f"{rule_id}: {symbol} matched {len(found)} symbols, wanted one")
            rules.append(_rule(rule_id, found[0]))
            status = INTERPRETATIONS[rule_id][0]
            print(f"{rule_id:<26} {symbol} @ {found[0]['rva']}  {status}")
    finally:
        scratch.unlink(missing_ok=True)

    payload = {
        "provenance": {
            "dll": dll.name,
            "dll_sha256": dll_sha256,
            "pdb_guid": native["pdb_guid"],
            "headers_sha256": _sha256(install / HEADERS),
            "extracted": datetime.now(UTC).date().isoformat(),
            "tool": native["tool"],
            "method": (
                "sfy-native disasm, one run per rule; reads, constants, calls and the "
                "entry RVA are the tool's, the evidence lines are copied from it by "
                "address, and only comparison/interpretation/status are written by hand. "
                "The blueprint corpus is not an input and is never evidence for a rule."
            ),
        },
        "rules": rules,
    }
    target = DATA / "hologram_rules.json" if out is None else Path(out)
    target.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for rule in rules:
        counts[rule["status"]] = counts.get(rule["status"], 0) + 1
    tally = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    print(f"\n{len(rules)} rules -> {target}: {tally}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
