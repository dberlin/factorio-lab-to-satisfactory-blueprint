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
status, the effect, a transcription of the comparison and what it means for a
placer; :data:`EVIDENCE` carries the addresses those sentences were read at.
Everything else is the tool's. The status obeys one rule:

``extracted``      the comparison's operands and its branch -- or, for a rule
                   that answers with a number, the returned value -- are in
                   the evidence
``partial``        the members are seen being read, but the comparison is in a
                   callee this did not follow, or past the end of what
                   ``sfy-native disasm`` can prove is the function -- the
                   ``comparison`` text says which, and names it
``unextractable``  nothing was read, and ``interpretation`` says why

A number from a header comment is not evidence and a number from the corpus is
not evidence. If a comparison cannot be read, the rule says ``partial`` and the
placer treats the bound as unknown.

The effect obeys a second rule: it is what the read instructions *do* --
``refuse``, ``clamp``, ``snap``, ``compute`` -- and ``none`` when they do none
of those. ``none`` cannot sit beside ``extracted``, and a rule is never
downgraded to make an effect fit.

Both an ``extracted`` status and an effect of ``none`` say something about what
the function does *not* do, so neither survives a function ``sfy-native`` could
not read to an end the game states. Whatever ``size_source`` the tool reports,
:func:`sfy_disasm.unbounded` is what decides: a ``ret`` or a ``truncated``
downgrades the rule to ``partial`` and says so in its ``comparison``, and it
fails the run outright for an effect of ``none``, which is an absence claim and
nothing else.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sfy_disasm import disasm, unbounded

from flab2bp.sfy import docs
from flab2bp.sfy.rules import RULE_EFFECTS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "flab2bp" / "sfy" / "data"

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
    "lift.connectors": (
        "AFGBuildableConveyorLift",
        "SetupConnections",
        "Buildables/FGBuildableConveyorLift.h:148",
    ),
    "lift.top_yaw": (
        "AFGConveyorLiftHologram",
        "SetHologramLocationAndRotation",
        "Hologram/FGConveyorLiftHologram.h:157",
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
    "belt.cost": (
        "AFGBuildable",
        "GetCostMultiplierForLength",
        "Buildables/FGBuildable.h:475",
    ),
    "manufacturer.inventory_filters": (
        "AFGBuildableManufacturer",
        "SetUpInventoryFilters",
        "Buildables/FGBuildableManufacturer.h:220",
    ),
    "belt.straight_tangents": (
        "AFGConveyorBeltHologram",
        "AutoRouteSpline",
        "Hologram/FGConveyorBeltHologram.h:97",
    ),
    "factory.potential": (
        "AFGBuildableFactory",
        "GetCurrentMaxPotential",
        "Buildables/FGBuildableFactory.h:244",
    ),
    "manufacturer.production_boost": (
        "AFGBuildableManufacturer",
        "SetCurrentProductionBoost",
        "Buildables/FGBuildableManufacturer.h:106",
    ),
}

# Rules the game spreads over more than one function. The entry above is the one
# the rule is named for -- its RVA and its header are the rule's -- and these are
# disassembled beside it, their instructions merged into the same pool so the
# evidence can quote a caller or a callee by address. ``also_read`` in the
# written rule lists each with the RVA it was found at.
# A ``Class::Method@0xRVA`` names one of several bodies the linker gave the same
# ``Class::Method`` name -- two overloads, or a constructor emitted twice -- and
# the run fails if nothing sits at that RVA.
# A bare ``0xRVA`` is a function the PDB publishes under a mangling with no
# ``Class::Method`` form at all -- a templated member such as
# ``TTransform<double>``'s, which ``sfy-native`` has no demangler for. Such a
# function is asked for by address; ``.pdata`` still bounds it, and the evidence
# quotes it by address like any other.
ALSO_READ: dict[str, tuple[str, ...]] = {
    "belt.cost": (
        "AFGBlueprintSubsystem::CalculateBlueprintCost@0x6738d0",
        "AFGBuildable::GetDismantleRefund_Implementation",
        "AFGBuildable::GetDismantleRefundReturns",
        "AFGBuildable::GetDismantleRefundReturnsMultiplier",
        "AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier",
        "AFGBuildableConveyorLift::GetDismantleRefundReturnsMultiplier",
        "AFGBuildableConveyorBelt::AFGBuildableConveyorBelt@0x1b9cb0",
    ),
    "manufacturer.inventory_filters": (
        "AFGBuildableManufacturer::SetRecipe",
        "AFGBuildableManufacturer::AFGBuildableManufacturer@0x1dd590",
    ),
    "belt.straight_tangents": (
        "FSplineBuilder::Start",
        "FSplineBuilder::AddSegment",
        "FSplineUtils::BuildStraightSpline2D",
        "FSplineUtils::BuildStraightSpline3D",
    ),
    "belt.clearance": ("AFGBuildableConveyorBelt::CreateClearanceData",),
    "lift.clearance": ("AFGBuildableConveyorLift::FitClearance",),
    "lift.connectors": (
        # The transform helper SetupConnections hands each connection to. The
        # PDB names it only by a templated mangling, so it is read by address.
        "0x4f9850",
        "AFGBuildableConveyorLift::GetConveyorLiftFlowDirection",
        "AFGBuildableConveyorBase::Factory_Tick",
        # Which byte of a connection component mDirection is, from the setter
        # the game writes it through, so the two stores above name themselves.
        "UFGFactoryConnectionComponent::SetDirection",
    ),
    "lift.top_yaw": (
        "AFGConveyorLiftHologram::UpdateTopTransform",
        "AFGConveyorLiftHologram::GetRotationStep",
        "AFGHologram::ApplyScrollRotationTo",
        "AFGConveyorLiftHologram::DoMultiStepPlacement",
    ),
    "factory.potential": (
        "AFGBuildableFactory::GetCurrentMaxPotentialForType",
        "AFGBuildableFactory::GetSlotsForPowerShardType",
        "AFGBuildableFactory::BeginPlay",
        "AFGBuildableFactory::CalcProducingPowerConsumptionForPotential",
        # The linker folded CalcProductionBoostPowerConsumption onto this body
        # -- one address, one surviving name -- so the two static helpers the
        # header declares separately are the same eleven instructions.
        "AFGBuildableFactory::CalcOverclockPowerConsumption",
        "AFGBuildableManufacturer::CalcProductionCycleTimeForPotential",
    ),
    "manufacturer.production_boost": (
        "AFGBuildableFactory::GetCurrentMaxProductionBoost",
        "AFGBuildableFactory::GetCurrentMaxPotentialForType",
        "AFGBuildableFactory::GetSlotsForPowerShardType",
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
        "0xaa5356", "0xaa535b", "0xaa53a4", "0xaa53a9", "0xaa53ab", "0xaa53b2",
        "0xaa53d6", "0xaa53df", "0xaa53e3", "0xaa53e7",
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
        # UpdateClearanceData: empty mClearanceData, then hand the spline, the
        # spline data, the root transform and mMaxSplineLength to the buildable.
        "0xaa16c2", "0xaa16c7", "0xaa1714", "0xaa171b", "0xaa1736", "0xaa173d",
        "0xaa1747", "0xaa174d",
        # CreateClearanceData: a spline of one point or fewer builds nothing.
        "0x4d78e7", "0x4d78f4", "0x4d7949",
        # The walk: stop at mMaxSplineLength, and one segment per call to
        # GetNextDistanceExceedingTolerance with its three tolerances.
        "0x4d7a3b", "0x4d7a47", "0x4d7a4f", "0x4d7a57", "0x4d7a61", "0x4d7a65",
        "0x4d7aac",
        # The box: +-(segment/2, 79, 15), and the segment's transform relative
        # to the root component.
        "0x4d7d31", "0x4d7d4a", "0x4d7d51", "0x4d7d58", "0x4d7d63", "0x4d7d74",
        "0x4d7d7b", "0x4d7d86", "0x4d7d8d", "0x4d7d91", "0x4d7da4", "0x4d7e04",
        # One 0xC0-byte FFGClearanceData appended per segment, then round again.
        "0x4d7ed9", "0x4d7eea", "0x4d7eee", "0x4d7ef1", "0x4d7ef5", "0x4d7ef9",
        "0x4d7efd", "0x4d7f01", "0x4d7f05", "0x4d7f9d",
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
        # The floor the function writes for itself when the connection the top
        # snapped to faces up or down: the normal, the |Z| > 0.5 test, the two
        # multiples of the step, and the store into mMinimumHeight.
        "0xaa4871", "0xaa487b", "0xaa4885", "0xaa488a", "0xaa4896", "0xaa489d",
        "0xaa48a1", "0xaa48a3", "0xaa48a8", "0xaa48b2", "0xaa48ba", "0xaa48c2",
    ),
    "lift.step": (
        # The step itself, and the five instructions that quantise the raw
        # height with it: divide, add a half, floor, multiply back.
        "0xaa46e8", "0xaa474f", "0xaa4754", "0xaa4769", "0xaa476d", "0xaa4772",
        "0xaa4776", "0xaa477c",
        # What reads the snapped height afterwards: the zero test, the sign
        # agreement, and the two ends of lift.height_range's clamp.
        "0xaa48ca", "0xaa48db", "0xaa4965", "0xaa4979", "0xaa497d",
        # The passthrough case: the thickness modulo 100 that rides through the
        # clamp in edi and is added back after it.
        "0xaa480f", "0xaa482c", "0xaa4830", "0xaa4843", "0xaa4859", "0xaa4863",
        "0xaa4867", "0xaa4970", "0xaa4974", "0xaa4981", "0xaa49a3",
    ),
    "lift.connectors": (
        # SetupConnections: the two connection members, the directions it gives
        # them, and the top transform it reads.
        "0x505af5", "0x505afc", "0x505b0d", "0x505b14", "0x505b1b",
        # The two relative transforms it builds: FTransform::Identity for the
        # bottom and mTopTransform for the top, each moved
        # CONNECTION_RELATIVE_FORWARD (0) along its own forward axis.
        "0x505b03", "0x505b22", "0x505b96", "0x505b9b", "0x505b9e", "0x505ba5",
        "0x505ba8",
        # The helper: the 96-byte copy, the quaternion's forward row, and the
        # translation it adds the scaled forward to.
        "0x4f9854", "0x4f98f7", "0x4f9915", "0x4f9922", "0x4f992a", "0x4f992f",
        # Which way the two ends face when the lift meets a passthrough: the
        # up/down vector chosen on the sign of the top transform's Z, and the
        # -1 that makes the other end face the opposite way.
        "0x505bad", "0x505bdf", "0x505bf9", "0x505bfb", "0x505ca0", "0x505f4f",
        "0x505f6f", "0x505f73", "0x505f83", "0x505f93",
        # The passthrough tests each end is behind, and the plain
        # SetRelativeTransform each falls back to.
        "0x505c8b", "0x505c8e", "0x505e74", "0x505e7e", "0x505f45", "0x505f49",
        "0x50614b", "0x506155", "0x5061cb", "0x5061d8",
        # GetConveyorLiftFlowDirection: the sign of mTopTransform's Z, and the
        # two enum values it returns.
        "0x4ed5e4", "0x4ed5ee", "0x4ed5f2", "0x4ed5f4", "0x4ed603", "0x4ed609",
        # Factory_Tick, which is the base class's and which a lift does not
        # override: the grab through mConnection0 and the push into mConnection1.
        "0x4e1611", "0x4e1635", "0x4e1743",
        # SetDirection, which is where +258h is mDirection.
        "0x197e50",
    ),
    "lift.top_yaw": (
        # SetHologramLocationAndRotation: the branch on mActivePointIdx, the
        # zero rotator the first point passes, and the yaw the second builds
        # from mFirstStepYaw before calling UpdateTopTransform.
        "0xa87932", "0xa87938", "0xa88081", "0xa8808e", "0xa88095", "0xa880a3",
        "0xa886e8", "0xa886fa", "0xa8870b", "0xa8871f", "0xa88728", "0xa8872c",
        "0xa88733",
        # GetRotationStep: 0 while the first point is live, 90 once it is not.
        "0xa7c15b", "0xa7c162", "0xa7c19a", "0xa7c1a2",
        # ApplyScrollRotationTo: the step, the scroll count, and the rounding
        # that lands the yaw on a multiple of the step off the base's residue.
        "0xaafe2c", "0xaafe3d", "0xaafe4f", "0xaafe53", "0xaafe5e", "0xaafe64",
        "0xaafe68", "0xaafe71", "0xaafe85", "0xaafe8d", "0xaafe93", "0xaafe97",
        # UpdateTopTransform: the rotator becomes mTopTransform's rotation, and
        # the height times FVector::UpVector its translation.
        "0xaa49a7", "0xaa49c2", "0xaa49d1", "0xaa49ee", "0xaa4a01", "0xaa4a0c",
        "0xaa4a10", "0xaa4a14", "0xaa4a20", "0xaa4a2f", "0xaa4a36", "0xaa4a3d",
        # DoMultiStepPlacement: where mFirstStepYaw is written, and the step
        # counter that makes the next call the top's.
        "0xa7289f", "0xa72f72", "0xa72f7a",
    ),
    "lift.placement": (
        "0xa68189", "0xa681c0", "0xa681cd", "0xa681db", "0xa681e7", "0xa681f3",
        "0xa681fa", "0xa68201", "0xa68203", "0xa6822f", "0xa68234", "0xa6824c",
        "0xa68253", "0xa6825a", "0xa6825c", "0xa68288",
    ),
    "lift.clearance": (
        # UpdateClearance: the -5 extent trim, the two passthrough tests, the
        # lift's height and mesh height, and the call that builds the box.
        "0xaa141d", "0xaa142e", "0xaa1433", "0xaa143b", "0xaa147b", "0xaa14de",
        "0xaa14ff", "0xaa1506", "0xaa150d", "0xaa1530", "0xaa1539", "0xaa1541",
        # FitClearance: the top and bottom of the span, from the height and the
        # mesh height, with 200 off the top for a passthrough and 50 on for one
        # at the other end, then the box around the midpoint.
        "0x4e5e0c", "0x4e5e33", "0x4e5e3b", "0x4e5e46", "0x4e5e4e", "0x4e5e52",
        "0x4e5e5c", "0x4e5e70", "0x4e5e78", "0x4e5e81", "0x4e5e86", "0x4e5e91",
        "0x4e5e99", "0x4e5ea2", "0x4e5ea6", "0x4e5ebf", "0x4e5ece", "0x4e5edb",
        "0x4e5ef5", "0x4e5f03", "0x4e5f19", "0x4e5f26",
    ),
    "buildable.grid_snap": (
        "0xa8f41d", "0xa8f428", "0xa8f42b",
    ),
    "buildable.rotation_step": (
        "0xa7c050", "0xa7c057", "0xa7c059", "0xa7c060", "0xa7c062", "0xa7c06a",
        "0xa7c06c", "0xa7c073", "0xa7c075", "0xa7c07a", "0xa7c07b", "0xa7c083",
        "0xa7c085", "0xa7c08c", "0xa7c08e", "0xa7c093", "0xa7c094", "0xa7c096",
        "0xa7c097", "0xa7c09c",
    ),
    "buildable.clearance": (
        "0xab8012", "0xab8242", "0xab827a", "0xab83ea", "0xab844b",
    ),
    "belt.cost": (
        # AFGBuildable::GetCostMultiplierForLength: the whole function.
        "0x4a6bb0", "0x4a6bb7", "0x4a6bb9", "0x4a6bbd", "0x4a6bc2", "0x4a6bc6",
        "0x4a6bce", "0x4a6bd2", "0x4a6bd4", "0x4a6bd6", "0x4a6bd9", "0x4a6bda",
        "0x4a6bdf",
        # AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier: the two
        # members it divides, and the tail jump into the helper above.
        "0x4edf70", "0x4edf78", "0x4edf80",
        # The lift's, which divides its height by mMeshHeight instead.
        "0x4edfa5", "0x4edfb5",
        # AFGBuildable::GetDismantleRefundReturnsMultiplier: 1 for everything else.
        "0x2434e0", "0x2434e5",
        # AFGBuildable::GetDismantleRefundReturns: the virtual multiplier, the
        # build recipe, its ingredients, and amount * multiplier per ingredient.
        "0x4a773d", "0x4a774a", "0x4a774c", "0x4a77d6", "0x4a7816", "0x4a781d",
        "0x4a7826", "0x4a7831", "0x4a7840",
        # AFGBuildableConveyorBelt's constructor storing its primary vtable, the
        # table whose slot at +8C8h holds the belt's multiplier override.
        "0x1b9cbe", "0x1b9ccf",
        # AFGBuildable::GetDismantleRefund_Implementation calls the returns.
        "0x4a78ee",
        # AFGBlueprintSubsystem::CalculateBlueprintCost asks each buildable.
        "0x67394f", "0x67396a",
    ),
    "manufacturer.inventory_filters": (
        # AFGBuildableManufacturer's constructor stores its primary vtable, the
        # table the three virtual calls below index into.
        "0x1dd59e", "0x1dd5a5",
        # SetRecipe: the two access-index assignments it gates on, the store
        # into mCurrentRecipe, and the call to SetUpInventoryFilters.
        "0x548d5e", "0x548e81", "0x548eb6", "0x548edf", "0x548ee7", "0x548ef5",
        "0x548f5b",
        # SetUpInventoryFilters: the recipe's class default object.
        "0x549a8b", "0x549ac9",
        # The input loop: one pass per slot of mInputInventory, the ingredient
        # at the same index while there is one.
        "0x549ae4", "0x549b0f", "0x549b30", "0x549b34", "0x549b37", "0x549b80",
        "0x549b87", "0x549b8a", "0x549bb3", "0x549bb9",
        # Past the last ingredient: UFGItemDescriptor itself on every spare slot.
        "0x549f83", "0x549faa", "0x549fb0",
        # The two indices step together, and the loop runs to the slot count.
        "0x54a1cf", "0x54a1d2", "0x54a1dc", "0x54a1e3",
        # The output loop over mOutputInventory and mProduct, the same shape.
        "0x54a1e9", "0x54a1fc", "0x54a212", "0x54a216", "0x54a219", "0x54a25f",
        "0x54a263", "0x54a269", "0x54a28c", "0x54a292",
        "0x54a6a1", "0x54a6c8", "0x54a6ce",
        "0x54a8e8", "0x54a8eb", "0x54a8f5", "0x54a8fc",
    ),
    "belt.straight_tangents": (
        # AutoRouteSpline: the builder over mSplineData, its first point and the
        # tangent handed with it, then one straight segment.
        "0xa63791", "0xa6379e", "0xa637a6", "0xa637af", "0xa63833", "0xa63912",
        "0xa6399b",
        # FSplineBuilder::Start: the location, then the tangent normalised into
        # BOTH of point 0's tangents.
        "0xb221e6", "0xb221ec", "0xb221ef", "0xb221f4", "0xb22257", "0xb22261",
        "0xb22265", "0xb22269", "0xb2226d", "0xb22291", "0xb22295", "0xb22299",
        "0xb2229e",
        # FSplineUtils::BuildStraightSpline2D: |delta| * 0.5, clamped to
        # [50, 600] and rounded through a float, times the unit direction.
        "0xafcf03", "0xafcf08", "0xafcf0d", "0xafcf15", "0xafcf1d", "0xafcf25",
        "0xafcf29", "0xafcf32", "0xafcf36", "0xafcf3a", "0xafcf50",
        # The 3D builder does the same arithmetic with the same three constants.
        "0xafd295", "0xafd2a5", "0xafd2ad", "0xafd2b5", "0xafd2e4",
        # FSplineBuilder::AddSegment: the new tangent's length, the previous
        # point's leave tangent rescaled to it, and the new point's three fields.
        "0xaf206b", "0xaf2076", "0xaf20ce", "0xaf20db", "0xaf20df", "0xaf20e3",
        "0xaf20e7", "0xaf20fa", "0xaf2101", "0xaf2108", "0xaf2111", "0xaf2117",
        "0xaf211d", "0xaf2123", "0xaf2129", "0xaf212c", "0xaf213c", "0xaf213f",
        "0xaf214c", "0xaf2156", "0xaf215c", "0xaf2164", "0xaf216f", "0xaf2175",
    ),
    "factory.potential": (
        # GetCurrentMaxPotential: the overclock shard type, the two bounds and
        # the multiplier of 1 it asks GetCurrentMaxPotentialForType for.
        "0x4ed7c4", "0x4ed7cc", "0x4ed7ce", "0x4ed7d6", "0x4ed7de", "0x4ed7e4",
        # GetCurrentMaxPotentialForType: one pass per slot of mInventoryPotential
        # that holds a shard of the asked-for type, adding that shard's own boost
        # value times the stack count times the multiplier, floored at the
        # minimum.
        "0x4ed7fd", "0x4ed804", "0x4ed80b", "0x4ed813", "0x4ed83e", "0x4ed867",
        "0x4ed886", "0x4ed908", "0x4ed959", "0x4ed95e", "0x4ed988", "0x4ed98d",
        "0x4ed993", "0x4ed996", "0x4ed99a", "0x4ed99e", "0x4ed9b3", "0x4ed9e9",
        # GetSlotsForPowerShardType: mPotentialShardSlots overclock slots, then
        # exactly one production-boost slot after them.
        "0x4f1af0", "0x4f1af9", "0x4f1bb5", "0x4f1bbb", "0x4f1bd1", "0x4f1bda",
        "0x4f1be7", "0x4f1c86",
        # BeginPlay: where mPotentialShardSlots and mProductionShardSlotSize come
        # from when the buildable does not override them.
        "0x4d40d5", "0x4d40df", "0x4d40e6", "0x4d40e9", "0x4d40eb", "0x4d40f1",
        "0x4d40f7", "0x4d40fa", "0x4d40fc", "0x4d4102",
        # CalcProducingPowerConsumptionForPotential: the potential rounded to a
        # whole percent, raised to mPowerConsumptionExponent, times the virtual
        # producing consumption.
        "0x4d58b6", "0x4d58c6", "0x4d58ca", "0x4d58d2", "0x4d58d6", "0x4d58de",
        "0x4d58e7", "0x4d58ef", "0x4d58fd", "0x4d5903",
        # CalcOverclockPowerConsumption(power, overclock, exponent).
        "0x4d5895", "0x4d589a",
        # AFGBuildableManufacturer::CalcProductionCycleTimeForPotential: the
        # recipe's duration over mManufacturingSpeed, divided by the same
        # rounded potential.
        "0x524948", "0x524953", "0x524958", "0x524960", "0x524964", "0x524968",
        "0x524970", "0x524974", "0x52497a", "0x52497e",
    ),
    "manufacturer.production_boost": (
        # SetCurrentProductionBoost: the recipe's products, each amount scaled by
        # mCurrentProductionBoost and rounded to a whole item.
        "0x54780c", "0x54785c", "0x54788d", "0x5478ba", "0x54790e", "0x547919",
        "0x547922", "0x547925", "0x547929", "0x54792d", "0x547931", "0x547936",
        "0x547939", "0x5479ab", "0x5479b3", "0x5479be",
        # GetCurrentMaxProductionBoost: the production-boost shard type, the base
        # boost as both bounds, and this class's own per-slot multiplier.
        "0x4eda14", "0x4eda1c", "0x4eda1e", "0x4eda26", "0x4eda29", "0x4eda2f",
        # The same accumulator GetCurrentMaxPotentialForType runs for a shard.
        "0x4ed83e", "0x4ed988", "0x4ed996", "0x4ed99a", "0x4ed99e", "0x4ed9e9",
        # And the single production-boost slot it walks.
        "0x4f1bd1", "0x4f1bda", "0x4f1be7", "0x4f1c86",
    ),
}

# status, effect, comparison, interpretation. The comparison is a transcription
# of the evidence above and nothing else; the interpretation is what a placer
# should do about it. Neither is ever filled in from a header comment or from the
# corpus.
#
# ``effect`` is what the instructions show the hologram *doing* with the value,
# and it is the field a validator reads before it refuses anything:
#
# ``refuse``  the branch disqualifies the hologram (it adds a construct
#             disqualifier, or returns the false that makes a caller add one)
# ``clamp``   the value is forced into range and the placement goes ahead
# ``snap``    the value is quantised or aligned and the placement goes ahead
# ``none``    nothing in the instructions that were read enforces it at all
# ``compute`` the function is not a validation: it works out a value the game
#             then writes, and turns no placement away
#
# ``none`` is only allowed beside a ``partial`` or ``unextractable`` status, and
# :func:`flab2bp.sfy.rules.load_rules` refuses the pair: a branch that was read
# says what it does. ``compute`` is the opposite case and is not that pair:
# the function was read in full and does nothing to a placement because it is
# not a validator at all. Where the instructions hand the value to a callee that
# was not followed -- ``FHologramHelpers::SnapToFloor`` -- the effect is what the
# call does with it, not what the callee's arithmetic turns out to be.
INTERPRETATIONS: dict[str, tuple[str, str, str, str]] = {
    "belt.curvature": (
        "extracted",
        "refuse",
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
        "belt.incline judges that separately. Four details of the arithmetic "
        "have to be reproduced exactly or the number comes out wrong. The "
        "numerator is `step` itself -- the sample spacing, identical for every "
        "sample -- and not the distance between the two sampled points: "
        "0xaa54da reloads xmm14, which 0xaa5300 set to length/n, and 0xaa54de "
        "divides it by acos's answer. RoundToInt is UE's SSE form -- `addss "
        "xmm2,xmm2; addss 0.5; cvtss2si; sar esi,1` -- and the doubling is not "
        "decoration: cvtss2si rounds half to EVEN under the default MXCSR, and "
        "doubling first puts the tie on 2n+0.5 rather than on n, so the shift "
        "recovers floor(n + 0.5), half UP. A straight pair gives "
        "theta == 0, and the hardware division yields +inf, which passes the "
        "`jb` -- so a straight belt is legal by arithmetic rather than by a "
        "guard. And a sample whose tangent has no horizontal part at all is "
        "NOT skipped: 0xaa53a4 compares the squared 2-D length against 1e-8 and "
        "0xaa53ab loads FVector::ZeroVector through the global at 0xee7288 when "
        "it is under, so the dot product is 0, acos(0) is PI/2, and the radius "
        "comes out step / (PI/2) -- about 32 cm at the 50 cm sampling, far "
        "inside the floor. A vertical belt is refused by THIS rule, not by "
        "belt.incline. Two further bounds are part of "
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
        "refuse",
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
        "refuse",
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
        "refuse",
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
        "extracted",
        "compute",
        "UpdateClearanceData empties mClearanceData (0xaa16c7, 0xaa16c2 "
        "DestructItems) and hands mSplineComponent (0xaa1736), mSplineData "
        "(0xaa173d), the root component's transform (0xaa1714) and "
        "mMaxSplineLength (0xaa171b, stored as the fifth argument at 0xaa1747) "
        "to AFGBuildableConveyorBelt::CreateClearanceData (0xaa174d), which is "
        "four chained .pdata chunks (0x4d78c0 +96, 0x4d7920 +275, 0x4d7a33 "
        "+1495, 0x4d800a +28) and was read whole. It returns at once when "
        "mSplineData.Num() <= 1 (0x4d78e7 cmp, 0x4d78f4 jle). Otherwise it "
        "walks the spline: the loop stops once the distance reaches the "
        "mMaxSplineLength it was handed (0x4d7949 loads it, 0x4d7a3b/0x4d7a61 "
        "comiss, 0x4d7a65 jae leaves), and each pass calls "
        "UFGSplineMeshGenerationLibrary::GetNextDistanceExceedingTolerance "
        "(0x4d7aac) with the three tolerances 0.5, 20.0 and 50.0 "
        "(0x4d7a57/0x4d7a4f/0x4d7a47) to get the next segment's length. Per "
        "segment it builds one FFGClearanceData: half the segment length "
        "(0x4d7d31 mulss 0.5) negated at 0x4d7d74 (xorps -0.0) and the two "
        ".rdata pairs (79.0, 15.0) at 0x4d7d4a and (-79.0, -15.0) at 0x4d7d58 "
        "are assembled into the six doubles at [rbp+58h..80h] -- Min = "
        "(-length/2, -79, -15), Max = (length/2, 79, 15) -- by the stores at "
        "0x4d7d51, 0x4d7d63, 0x4d7d7b, 0x4d7d86, 0x4d7d8d, 0x4d7d91 and "
        "0x4d7da4; its RelativeTransform is the segment's own transform against "
        "the root component (0x4d7e04 TTransform<double>::GetRelativeTransform). "
        "The element is then appended whole (0x4d7ed9 Num+1, 0x4d7eea/0x4d7eee "
        "the type byte, 0x4d7ef1..0x4d7f05 the box, 0xC0 bytes a piece) and the "
        "walk goes round again (0x4d7f9d). No comparison is made in either "
        "function.",
        "A belt's clearance is a **chain of boxes along its spline**, one per "
        "sampled segment, each 158 cm wide and 30 cm tall about the segment's "
        "own axis and as long as that segment: Min = (-L/2, -79, -15) and "
        "Max = (L/2, 79, 15) in the segment's frame, placed by the segment's "
        "transform relative to the belt's root. The half-width of 79 cm is "
        "narrower than the Mk1 belt mesh's own 89.1 cm "
        "(registry.json's mesh_bounds_cm), so the game's clearance is not the "
        "mesh and a placer must not substitute one for the other. The segment "
        "length is not fixed: GetNextDistanceExceedingTolerance decides it from "
        "the spline's curvature, so a straight belt gets few long boxes and a "
        "curve many short ones, and the walk stops at mMaxSplineLength. Nothing "
        "here refuses anything -- the effect is compute -- and whether one of "
        "these boxes overlaps a neighbour is decided by buildable.clearance. "
        "Two things are still unread: the two flag bytes of FFGClearanceData "
        "(0x4d7eea copies the one at [rbp+50h], set to 1 before the loop, and "
        "0x4d7f61/0x4d7f6e copy a further pair), which is where a CT_Soft "
        "marking would live, and the tolerance arithmetic inside "
        "GetNextDistanceExceedingTolerance, so the *number* of boxes on a given "
        "spline cannot be reproduced from this rule alone.",
    ),
    "belt.snap_directions": (
        "extracted",
        "snap",
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
        "refuse",
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
        "refuse",
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
        "refuse",
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
        "refuse",
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
        "clamp",
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
        "the lift meets a passthrough and mMinimumHeight otherwise -- and "
        "mMinimumHeight itself is not always the BeginPlay value: when the top "
        "has snapped to a connection whose normal is vertical, "
        "UpdateTopTransform overwrites it for the length of the call with 2.5 "
        "or 3.5 times mStepHeight, i.e. 250 or 350 cm rather than 400. It "
        "takes the snapped connection (0xaa4871, skipped when null at "
        "0xaa487b), asks it for GetConnectorNormal (0xaa4885), reads that "
        "vector's Z (0xaa488a) and tests |Z| against 0.5 (0xaa4896 andps, "
        "0xaa489d comiss, 0xaa48a1 jbe); past that test 0xaa48a3 compares the "
        "Z against xmm7 -- which 0xaa47c0 `xorps xmm7, xmm7` has zeroed on the "
        "path through, so this is the sign of the normal -- and takes 2.5 "
        "(0xaa48b2, 0x12bd258) or 3.5 (0xaa48a8, 0x12d6478), multiplies by "
        "mStepHeight (0xaa48ba) and stores it into mMinimumHeight (0xaa48c2). "
        "The saved value goes back at 0xaa4a6f, so the rewrite lasts one call. "
        "A placer that refuses below 400 cm is therefore stricter than the "
        "game for a lift whose top meets a vertical connection. The "
        "numbers are not constructor immediates: "
        "AFGConveyorLiftHologram::BeginPlay computes all three from the "
        "buildable's mesh height H as H*2, H*24 and H-50, which "
        "tools/sfy-native read out of the machine code at 0xa64e70 -- see "
        "data/native.json and the LIFT_HEIGHT_FORMULAS block in "
        "scripts/sfy_registry.py. With H = 200 that is 400, 4800 and 150 cm.",
    ),
    "lift.step": (
        "extracted",
        "snap",
        "UpdateTopTransform loads mStepHeight into xmm7 once, at 0xaa46e8, and "
        "quantises the height with it: FHologramHelpers::CalcPoleHeight hands "
        "the raw height back in xmm0 (0xaa474f), 0xaa4754 loads 0.5 out of "
        ".rdata at 0xf6dee8, 0xaa4769 `divss xmm0, xmm7` divides by the step, "
        "0xaa476d `addss xmm0, xmm8` adds the half, 0xaa4772 moves the sum "
        "into xmm1 and 0xaa4776 `roundps xmm6, xmm1, 1` floors it (imm8 1 is "
        "round-toward-minus-infinity), and 0xaa477c `mulss xmm6, xmm7` "
        "multiplies the whole number of steps back by the step -- "
        "floor(raw / mStepHeight + 0.5) * mStepHeight. xmm6 is the snapped "
        "height, and it is what every later use reads: the zero test at "
        "0xaa48ca, the sign agreement at 0xaa48db, and both ends of "
        "lift.height_range's clamp (0xaa4965 comiss, 0xaa4979 minss, 0xaa497d "
        "maxss). The passthrough case at 0xaa480f..0xaa4867 is separate "
        "arithmetic on the same step: it takes the passthrough's own thickness "
        "modulo 100 into edi (0xaa4830 cvttss2si, 0xaa4834 imul / 0xaa4836 "
        "sar edx,5 / 0xaa4843 sub, the compiler's divide by 100), subtracts it "
        "from mMinimumHeightWithVerticalConnection (0xaa4859) and adds "
        "mStepHeight to that floor (0xaa4863/0xaa4867); edi rides through the "
        "clamp as xmm4 (0xaa4970/0xaa4974) and is added back at 0xaa4981, or "
        "subtracted at 0xaa49a3 on the downward branch.",
        "A conveyor lift's height is SNAPPED: the hologram rounds the raw "
        "height half-up onto a multiple of mStepHeight before it clamps it "
        "into lift.height_range's window, so a height that is not a multiple "
        "of the step is a height the game silently moves rather than one it "
        "refuses. mStepHeight is 100.0 in the shipped build (a BeginPlay "
        "store, data/native.json), which is why registry.json's lift_step_cm "
        "is governed by this rule with the effect snap. Nothing here turns a "
        "placement away, so a validator that refuses a height off the step is "
        "stating this project's own rule, with this snap as its reason: a lift "
        "we author off the lattice would be built somewhere other than where "
        "it was costed. The one exception the instructions state is a lift "
        "snapped to a passthrough, which carries that passthrough's thickness "
        "modulo 100 through the clamp and back and so sits off the lattice by "
        "exactly that remainder; this project authors no passthroughs.",
    ),
    "lift.connectors": (
        "extracted",
        "compute",
        "SetupConnections gives the two connections their directions outright: "
        "`mov byte ptr [rax+258h], 0` at 0x505b0d on mConnection0 (0x505af5) "
        "and `mov byte ptr [rax+258h], 1` at 0x505b1b on mConnection1 "
        "(0x505b14). 0x258 is UFGFactoryConnectionComponent::mDirection -- it is "
        "the byte UFGFactoryConnectionComponent::SetDirection writes, at "
        "0x197e50 -- and 0 and 1 are FCD_INPUT and FCD_OUTPUT, the first two of "
        "EFactoryConnectionDirection. Neither store is behind a "
        "branch, and the whole function (1950 bytes over four chained .pdata "
        "chunks) reads mIsReversed at +810h nowhere. It then places them. "
        "0x505afc takes &mTopTransform (+7B0h) and the helper at 0x4f9850 is "
        "called twice: with FTransform::Identity, the data import at 0x505b22, "
        "into [rbp+180h] (0x505b96) and with mTopTransform into [rbp+1E0h] "
        "(0x505ba5/0x505ba8). The helper copies its input whole (0x4f9854) and "
        "adds its third argument times the rotation's forward row -- "
        "1-2y^2-2z^2, 2(xy+zw), 2(zx-yw), with the +1.0 at 0x4f98f7 -- to the "
        "translation at +20h/+30h (0x4f9915..0x4f992f). That argument is xmm2, "
        "zeroed at 0x505b03 and 0x505b9b: "
        "AFGBuildableConveyorLift::CONNECTION_RELATIVE_FORWARD, which the "
        "header declares `static constexpr float ... = 0.f`. So the copies are "
        "the inputs unchanged, and where mSnappedPassthroughs[0] is null "
        "(0x505c8b/0x505c8e) mConnection0 gets Identity "
        "(0x505e74/0x505e7e SetRelativeTransform) and where "
        "mSnappedPassthroughs[1] is null (0x505f45/0x505f49) mConnection1 gets "
        "mTopTransform (0x50614b/0x506155); both are then registered "
        "(0x5061cb/0x5061d8). The passthrough branches instead orient each end "
        "along the vertical: 0x505bdf compares the two transforms' Z and "
        "0x505bf9 picks FVector::UpVector (0x505bad) or FVector::DownVector "
        "(0x505bfb) -- both named by the module's own import table -- which "
        "0x505ca0 turns into mConnection0's rotation via ToOrientationQuat and "
        "which mConnection1 gets negated first (0x505f4f loads -1.0, "
        "0x505f6f/0x505f73/0x505f83 multiply, 0x505f93 converts). "
        "GetConveyorLiftFlowDirection (0x4ed5e0, 56 bytes) reads "
        "mTopTransform's translation Z at +7E0h (0x505afc + 0x30) at 0x4ed5e4 "
        "and returns LD_Upwards for >= 0 and LD_Downwards for < 0 "
        "(0x4ed5ee comisd, 0x4ed5f2 jbe, 0x4ed5f4 and 0x4ed603/0x4ed609). "
        "AFGBuildableConveyorBase::Factory_Tick, which a lift does not "
        "override, grabs through mConnection0 (0x4e1611, call at 0x4e1635) and "
        "pushes into mConnection1 (0x4e1743).",
        "A conveyor lift's two ports are at the actor origin with no rotation "
        "in the cooked class default object, and this is the runtime geometry "
        "that replaces them. The bottom end is mConnection0: at the actor "
        "transform exactly, facing the actor's own forward (+X), and always the "
        "input. The top end is mConnection1: at mTopTransform, which "
        "UpdateTopTransform makes (0, 0, height) with the top's yaw (see "
        "lift.top_yaw), and always the output. Reversal does not swap them. "
        "mIsReversed is a SaveGame bool the header marks DEPRECATED 2023-01-30 "
        "with 'Instead build lifts where mConnector0 is always input, and the "
        "other always output', GetIsReversed() is documented LEGACY and returns "
        "IsFlowUpwards(), and SetupConnections -- read whole -- never looks at "
        "it. A downward lift is one whose actor sits at the top and whose "
        "mTopTransform.Z is negative; the items still enter by mConnection0. "
        "The one case where the ends do not face the actor's forward is a "
        "passthrough snap, where each is turned to face straight up or straight "
        "down instead, opposite ways. A placer that wants a lift to carry items "
        "upward puts the actor at the bottom; one that wants it to carry them "
        "downward puts the actor at the top and gives mTopTransform a negative "
        "Z.",
    ),
    "lift.top_yaw": (
        "extracted",
        "compute",
        "A lift is placed in two steps, counted by mActivePointIdx "
        "(0xa7289f sets it to 1, 0xa72f7a increments it). "
        "SetHologramLocationAndRotation branches on it at 0xa87932/0xa87938. "
        "The first point passes UpdateTopTransform the zero rotator "
        "(0xa88081 xorps, 0xa8808e and 0xa88095 store pitch/yaw and roll, "
        "0xa880a3 calls). The second reads mFirstStepYaw at 0xa886e8 (+980h), "
        "hands it to AFGHologram::ApplyScrollRotationTo (0xa886fa), widens the "
        "answer (0xa8870b) into an FRotator whose pitch and roll are zero "
        "(0xa8871f, 0xa88728, 0xa8872c) and calls UpdateTopTransform with it "
        "(0xa88733). mFirstStepYaw itself is written once, at the end of the "
        "first step (0xa72f72). ApplyScrollRotationTo asks the hologram for its "
        "rotation step through the vtable (0xaafe2c), floors it at 1 "
        "(0xaafe4f) and takes its reciprocal (0xaafe53); it splits the base "
        "yaw into a whole number of steps and a residue "
        "(0xaafe5e roundps, 0xaafe64 mulss, 0xaafe68 subss), adds the player's "
        "scroll count mScrollRotation (0xaafe3d, 0xaafe71) and rounds the sum "
        "back onto the step lattice (0xaafe85 addss 0.5, 0xaafe8d roundps, "
        "0xaafe93 mulss, 0xaafe97 addss the residue). "
        "AFGConveyorLiftHologram::GetRotationStep returns 0 only while the "
        "first point is still live -- `cmp dword ptr [rcx+984h], 0; jg` at "
        "0xa7c15b/0xa7c162 and the 0 at 0xa7c19a -- and 90 otherwise "
        "(0xa7c1a2 `mov eax,5Ah`). UpdateTopTransform then writes the rotator's "
        "quaternion (0xaa49d1 FRotator::Quaternion, stored at "
        "0xaa49ee/0xaa4a01) into mTopTransform at +880h, and its translation "
        "(+8A0h/+8B0h) as the clamped height times FVector::UpVector -- the "
        "data import at 0xaa49a7, loaded at 0xaa49c2, multiplied at "
        "0xaa4a0c/0xaa4a10/0xaa4a14 and stored at 0xaa4a20/0xaa4a2f -- with a "
        "unit scale (0xaa4a36/0xaa4a3d).",
        "The top of a lift may face any of the four compass directions, "
        "independently of the bottom. Once the first click is down, the lift "
        "hologram's rotation step is 90 degrees, and the yaw the top is built "
        "with is the bottom's own yaw plus whatever multiple of 90 the player "
        "has scrolled to. The bottom's yaw is fixed at the first click and kept "
        "in mFirstStepYaw; the top's is that number re-quantised, so a top yaw "
        "the game can produce is always the bottom yaw plus 0, 90, 180 or 270. "
        "mTopTransform is the top end's transform in the actor's own frame: its "
        "translation is (0, 0, height) -- the height, positive or negative, "
        "along FVector::UpVector and nothing sideways -- its rotation is that "
        "yaw, and its scale is 1. A placer reproducing this picks the bottom "
        "yaw from where the lift's input has to face, then the top yaw from "
        "where its output has to face, and is free to pick them independently "
        "as long as both are multiples of 90 apart. The height itself is not "
        "this rule's: lift.height_range states the clamp UpdateTopTransform "
        "applies before the multiply.",
    ),
    "lift.placement": (
        "extracted",
        "refuse",
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
        "compute",
        "UpdateClearance builds the three doubles (-5, -5, -5) at [rsp+40h] "
        "from the .rdata pairs at 0xaa141d and 0xaa1433 (0xaa142e/0xaa143b "
        "store them), tests each of mSnappedPassthroughs (0xaa147b and "
        "0xaa14de) into a byte, and hands the lift's height ([rbx+8B0h] at "
        "0xaa14ff, narrowed at 0xaa1530), mMeshHeight (0xaa150d), a module "
        "global at 0x19B8118 and that -5 vector to "
        "AFGBuildableConveyorLift::FitClearance (0xaa1539), storing the 56-byte "
        "box it returns on the hologram (0xaa1541). FitClearance (0x4e5dd0, "
        "375 bytes, read whole) works the span out in floats: top = "
        "(height + mMeshHeight - 100 - P) / 2 + Q and bottom = "
        "(|height| + mMeshHeight + 100 - P) / 2 - 30, where P is 200.0 when "
        "the first flag is set and 0 otherwise (0x4e5e0c, 0x4e5e81/0x4e5e86) "
        "and Q is 50.0 when the second is (0x4e5e33, 0x4e5ea2) -- 0x4e5e4e "
        "addss mMeshHeight, 0x4e5e46 andps the sign mask, 0x4e5e70/0x4e5e78 "
        "the 100.0, 0x4e5e91/0x4e5e99 the halving, 0x4e5ea6 the 30.0. It then "
        "reads two doubles from the 0x19B8118 vector (0x4e5e52, 0x4e5e5c), adds "
        "the -5 vector to them and to the bottom (0x4e5edb and the two "
        "following), multiplies the top by a third vector loaded through the "
        "pointer at 0xEE7CC0 (0x4e5ebf and the two following) and writes "
        "Max = centre + extent (0x4e5ef5, 0x4e5f19) and Min = extent - centre "
        "(0x4e5f03, 0x4e5f26). No comparison is made in either function. THE "
        "TWO VECTORS ARE NOT IN THE EVIDENCE: 0x19B8118 and the target of "
        "0xEE7CC0 are in .data, which sfy-native refuses to read as constants "
        "because a mutable global is not one, so the box's half-width and its "
        "axis are unknown here and this rule stays partial.",
        "A lift's clearance is **one box** spanning the lift, not a chain along "
        "a spline as for a belt, and the span is read: it runs from "
        "(|height| + mMeshHeight + 100) / 2 - 30 to "
        "(height + mMeshHeight - 100) / 2, with 200 cm taken off when the lift "
        "meets a passthrough at one end and 50 cm added when it meets one at "
        "the other -- the same mMeshHeight of 200 cm that drives the lift "
        "height limits. What is *not* read is how wide the box is: the "
        "half-extent comes from a module global at 0x19B8118, shrunk by 5 cm on "
        "each axis, and the centre is scaled by a second global. FitClearance "
        "reads exactly two doubles from the first, and the class declares one "
        "static two-component constant, "
        "AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D "
        "(Buildables/FGBuildableConveyorLift.h:211) -- but a static initialised "
        "at start-up lives in .data, which sfy-native will not quote, so that "
        "is a consistent reading and not a value this rule states. Reading the "
        "image at 0x19B8118 by hand gives (100.0, 100.0) -- a 2 m square "
        "footprint, 95 cm each way after FitClearance's -5 shrink -- and that "
        "is recorded here as what a reader will see and NOT as something this "
        "rule states: it is a mutable global, the tool will not quote it, and "
        "nothing in this project's own geometry may be justified by it until "
        "it comes out of the tool's own output. A placer must therefore not "
        "compute a lift's footprint from "
        "this rule; take the boxes registry.json already carries per buildable, "
        "or the mesh box, and treat the width as unknown. Nothing here refuses "
        "anything -- the effect is compute -- and whether the box overlaps is "
        "decided by buildable.clearance.",
    ),
    "buildable.grid_snap": (
        "partial",
        "snap",
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
        "extracted",
        "compute",
        "GetRotationStep is a leaf -- 0xa7c050 has no RUNTIME_FUNCTION, the "
        "neighbouring .pdata entries being 0xa7bfc0..0xa7c04a and "
        "0xa7c0d0..0xa7c140 -- so its 77 bytes come from the PDB's procedure "
        "record rather than from .pdata, and all four returns decode. The "
        "ladder in order: `cmp byte ptr [rcx+6A0h], 0; jne 0xa7c097` on "
        "mDidSnapDuetoClearance (0xa7c050/0xa7c057) and the same test on the "
        "unnamed byte at [rcx+5D8h] (0xa7c059/0xa7c060) both land on "
        "`mov eax, 5Ah; ret` at 0xa7c097/0xa7c09c -- 90. Otherwise "
        "`cmp qword ptr [rcx+6B8h], 0; je 0xa7c07b` on mSnappedAttachmentPoint "
        "(0xa7c062/0xa7c06a) and `cmp byte ptr [rcx+378h], 0; je 0xa7c07b` on "
        "mSnapToGuideLines (0xa7c06c/0xa7c073); with both set, "
        "`mov eax, 0Ah; ret` at 0xa7c075/0xa7c07a -- 10. At 0xa7c07b, "
        "`cmp qword ptr [rcx+5E0h], 0; je 0xa7c094` on mSnappedBuilding "
        "(0xa7c07b/0xa7c083) reaches `xor eax, eax; ret` at 0xa7c094/0xa7c096 "
        "-- 0 -- when it is null; when it is not, "
        "`cmp byte ptr [rcx+4E8h], 0; je 0xa7c097` on "
        "mUseGradualFoundationRotations (0xa7c085/0xa7c08c) gives "
        "`mov eax, 2Dh; ret` at 0xa7c08e/0xa7c093 -- 45 -- or the 90 again.",
        "The build gun's rotation step is a four-way ladder, and every rung is "
        "now read rather than inferred. **Nothing in this function quantises "
        "anything**: it is a compare-and-return ladder that hands a number of "
        "degrees back, and whichever caller asked applies it -- which is why "
        "the effect is compute and not snap. A validator must not treat it as "
        "a bound, and a placer reproduces it rather than being refused by it. "
        "**0** (no quantisation) is what a "
        "hologram gets when mSnappedBuilding is null -- that is, when it is "
        "*not* snapped to a building, which is also what the base "
        "AFGHologram::GetRotationStep returns outright (0x13b010, `xor eax, "
        "eax; ret`). **90** is the snapped default, and also what a hologram "
        "that had to move for clearance gets (mDidSnapDuetoClearance). **45** "
        "is the snapped-to-a-building case with mUseGradualFoundationRotations "
        "set. **10** is an attachment point with guide-line snapping on. Note "
        "that this corrects the reading Task 3 could only infer from the "
        "reachable branch: 0 is the *un*snapped case and 45 the snapped one, "
        "not the other way round. Subclasses override freely -- "
        "AFGConveyorLiftHologram::GetRotationStep (0xa7c140) shows the same 90 "
        "at 0xa7c1a2 and the same 0 at 0xa7c19a; others return 180, 15, 5 or 1 "
        "-- so 90 is the buildable default, not a universal constant. One "
        "operand has no name: the byte at [rcx+5D8h] tested at 0xa7c059 falls "
        "on no member of this class chain in the PDB's type stream, so the "
        "rule reports the offset and does not guess a field. registry.json "
        "still tags hologram_rotation_step_deg as a project constant; this "
        "rule is the evidence that 90 is the game's own default for a snapped "
        "buildable, and re-sourcing that limit is a separate change.",
    ),
    "buildable.clearance": (
        "partial",
        "refuse",
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
    "belt.cost": (
        "extracted",
        "compute",
        "AFGBuildable::GetCostMultiplierForLength(totalLength, costSegmentLength) "
        "returns 1 when costSegmentLength <= 1e-4 (0x4a6bb0 comiss against "
        "0.0001, 0x4a6bb7 jbe, 0x4a6bda mov eax,1); otherwise r = "
        "totalLength / costSegmentLength (0x4a6bb9 divss) and the answer is "
        "max(1, RoundToInt(r)) -- 0x4a6bc2 addss xmm0,xmm0, 0x4a6bc6 addss 0.5, "
        "0x4a6bce cvtss2si, 0x4a6bd2 sar eax,1 (UE's RoundToInt on SSE), then "
        "0x4a6bd4 cmp/0x4a6bd6 cmovl against the 1 loaded at 0x4a6bbd. "
        "AFGBuildableConveyorBelt::GetDismantleRefundReturnsMultiplier "
        "(0x4edf70) calls it with mMeshLength as the segment (0x4edf70) and "
        "mLength as the total (0x4edf78, tail jump at 0x4edf80); the lift's "
        "(0x4edf90) passes mMeshHeight (0x4edfa5, jump at 0x4edfb5) and its own "
        "height; AFGBuildable's (0x2434e0) returns 1 for everything else. "
        "AFGBuildable::GetDismantleRefundReturns reads that multiplier through "
        "the primary vtable at +8C8h (0x4a773d call, 0x4a774a mov ebp,eax), "
        "takes mBuiltWithRecipe (0x4a774c) and its ingredients (0x4a77d6 "
        "UFGRecipe::GetIngredients) and merges one stack per ingredient of "
        "multiplier * FItemAmount::Amount (0x4a7816 edx = multiplier, 0x4a781d "
        "imul edx,[rbx+8], 0x4a7826 FInventoryStack, 0x4a7831 MergeInventoryItem, "
        "0x4a7840 add rbx,10h for the 16-byte stride). "
        "AFGBuildable::GetDismantleRefund_Implementation calls it (0x4a78ee) and "
        "AFGBlueprintSubsystem::CalculateBlueprintCost asks every buildable in "
        "the designer for that refund (0x67394f Execute_CanDismantle, 0x67396a "
        "Execute_GetDismantleRefund), summing the result into the blueprint's "
        "cost. Which override slot +8C8h holds is not an instruction: the belt's "
        "constructor stores its primary vtable 0xF79290 (0x1b9cbe lea, 0x1b9ccf "
        "mov [rbx],rax) and the qword at 0xF79290 + 8C8h is 0x4edf70, the RVA "
        "this rule's also_read reports for the belt's override.",
        "A conveyor is charged its build recipe once per cost segment, and the "
        "segment is the mark's own mesh: mMeshLength for a belt (200 cm for all "
        "six marks in 1.2.0) and mMeshHeight for a lift (200 cm as well). The "
        "count is a ROUND, not a ceiling -- a 300 cm Mk1 belt costs 2 iron "
        "plates and a 299 cm one costs 1 -- with a floor of 1, and RoundToInt "
        "here is UE's SSE form -- double, add a half, cvtss2si, shift right -- "
        "which takes a half towards positive infinity (1.5 -> 2, -1.5 -> -1), "
        "in single precision throughout. "
        "Everything that is not a spline buildable costs its recipe once. A "
        "blueprint's header cost is the same arithmetic summed over the "
        "buildables in the designer, so authoring a belt and costing it this "
        "way reproduces what the game writes. Two things this does not cover: "
        "the interface dispatch from CalculateBlueprintCost lands on "
        "AFGBuildable::GetDismantleRefund_Implementation, which also adds "
        "GetDismantleBlueprintReturns and the contents of the building's "
        "inventories, and lightweight buildables are costed through "
        "GetDismantleRefundReturnsMultiplierForLightweight instead. Neither "
        "matters for a blueprint of machines and belts we authored empty.",
    ),
    "manufacturer.inventory_filters": (
        "extracted",
        "compute",
        "AFGBuildableManufacturer::SetRecipe (0x548d10) assigns the recipe's "
        "ingredients and products to its factory connections through two "
        "virtual calls -- the primary vtable 0xFAB4A8 (0x1dd59e lea, 0x1dd5a5 "
        "mov [rbx],rax) at +A88h (0x548e81) and +A90h (0x548eb6), which are "
        "AssignInputAccessIndices and AssignOutputAccessIndices -- and only if "
        "both answered true (0x548edf/0x548ee7) stores the recipe into "
        "mCurrentRecipe (0x548d5e, 0x548ef5) and calls the slot at +A80h "
        "(0x548f5b), which is SetUpInventoryFilters @ 0x549a70. "
        "That function takes the recipe's class default object (0x549a8b "
        "mCurrentRecipe, 0x549ac9 UClass::GetDefaultObject<UFGRecipe>) and "
        "walks mInputInventory (0x549ae4) once per *slot* -- the loop counter "
        "r12d runs to mInventoryStacks.Num at [inventory+1C8h] (0x549b0f, "
        "0x54a1dc/0x54a1e3). For slot i, if i is below mIngredients.Num "
        "([recipe+48h], 0x549b30/0x549b34/0x549b37) it reads "
        "mIngredients[i].ItemClass -- the array data at [recipe+40h] "
        "(0x549b80) indexed with a 16-byte stride (0x549b87 add r15,r15, "
        "0x549b8a mov rcx,[rax+r15*8]), FItemAmount's first field -- and calls "
        "UFGInventoryComponent::SetAllowedItemOnIndex(i, that) (0x549bb3 edx = "
        "i, 0x549bb9); otherwise it calls the same with UFGItemDescriptor "
        "itself (0x549f83 Z_Construct_UClass_UFGItemDescriptor_NoRegister, "
        "0x549faa, 0x549fb0). Slot index and ingredient index step together "
        "(0x54a1cf inc rbx, 0x54a1d2 inc r12d). mOutputInventory (0x54a1e9) "
        "gets the identical loop against mProduct -- Num at [recipe+58h] "
        "(0x54a212/0x54a216/0x54a219), data at [recipe+50h] (0x54a25f), same "
        "stride (0x54a263/0x54a269), SetAllowedItemOnIndex at 0x54a292, the "
        "UFGItemDescriptor fallback at 0x54a6a1/0x54a6c8/0x54a6ce, the two "
        "indices stepping at 0x54a8e8/0x54a8eb and the slot-count bound at "
        "0x54a8f5/0x54a8fc. The member offsets [recipe+40h]/[recipe+48h], "
        "[recipe+50h]/[recipe+58h] and [inventory+1C0h]/[inventory+1C8h] are "
        "UFGRecipe::mIngredients, UFGRecipe::mProduct and "
        "UFGInventoryComponent::mInventoryStacks in the PDB's type stream "
        "(sfy-native --class UFGRecipe:mIngredients,mProduct --class "
        "UFGInventoryComponent:mInventoryStacks), a TArray being data then Num.",
        "A manufacturer's inventory filters are positional and exhaustive. The "
        "input inventory's slot i allows the recipe's i-th ingredient while "
        "there is one, and every slot past the last ingredient allows "
        "UFGItemDescriptor -- the base class, which is the wildcard, written "
        "explicitly rather than left as it was. The output inventory is the "
        "same against the recipe's products. The number of slots belongs to the "
        "machine and is never changed here: the loop is bounded by "
        "mInventoryStacks.Num, so an oil refinery keeps the slot a solid recipe "
        "does not fill, filled with the wildcard. mArbitrarySlotSizes is not "
        "touched at all. A blueprint we author reproduces exactly this: the "
        "ingredients in recipe order, then the products in recipe order, and "
        "UFGItemDescriptor in whatever is left over. What this does not settle "
        "is the *save* shape -- SetAllowedItemOnIndex writes "
        "mAllowedItemDescriptors, which is a SaveGame UPROPERTY "
        "(FGInventoryComponent.h:660), so what a blueprint carries is whatever "
        "the array held when it was saved, and a machine whose recipe was never "
        "set carries whatever its Blueprint default was.",
    ),
    "belt.straight_tangents": (
        "extracted",
        "compute",
        "AutoRouteSpline builds mSplineData (0xa63791) through an FSplineBuilder: "
        "FSplineBuilder::Start(location, tangent) at 0xa63833, with the location "
        "at [rbp-30h] (0xa637a6) and the tangent at [rsp+78h] (0xa6379e), then "
        "one FSplineUtils::BuildStraightSpline2D (0xa63912) or, for the curved "
        "modes, a bend builder and FSplineBuilder::AddSegment (0xa6399b). "
        "Start (0xb220b0) copies the location into point 0 (0xb221e6/0xb221ec, "
        "0xb221ef/0xb221f4), normalises the tangent it was given (0xb22257 "
        "sqrtsd, 0xb22261 divsd 1.0, 0xb22265/0xb22269/0xb2226d mulsd) and "
        "stores that UNIT vector into both of point 0's tangents -- "
        "ArriveTangent at +18h (0xb22291, 0xb22299) and LeaveTangent at +30h "
        "(0xb22295, 0xb2229e) of the 72-byte FSplinePointData. "
        "BuildStraightSpline2D (0xafcac0) takes the 3D distance to the new point "
        "(0xafcf03 dz^2 + dxy^2, 0xafcf08 sqrtpd), halves it (0xafcf0d mulsd "
        "0.5), clamps it to [50, 600] (0xafcf15 minsd 600.0, 0xafcf1d maxsd "
        "50.0), rounds it through a float (0xafcf25 cvtpd2ps, 0xafcf29 "
        "cvtps2pd) and multiplies the unit run direction by it "
        "(0xafcf32/0xafcf36/0xafcf3a) before calling AddSegment (0xafcf50). "
        "BuildStraightSpline3D does the same with the same three constants "
        "(0xafd295 sqrtpd, 0xafd2a5 mulsd 0.5, 0xafd2ad minsd 600.0, 0xafd2b5 "
        "maxsd 50.0, 0xafd2e4 AddSegment). "
        "AddSegment (0xaf1e80) takes the new tangent's length as a float "
        "(0xaf206b sqrtpd, 0xaf2076 cvtpd2ps), normalises the PREVIOUS point's "
        "LeaveTangent (0xaf20ce sqrtsd, 0xaf20db divsd, "
        "0xaf20df/0xaf20e3/0xaf20e7 mulsd) and rescales it to that length "
        "(0xaf20fa cvtps2pd, 0xaf2101/0xaf2108, 0xaf2111/0xaf2117, "
        "0xaf211d/0xaf2123), then writes the new point: Location from the "
        "caller (0xaf2129/0xaf212c), ArriveTangent the full tangent it was "
        "given (0xaf213c/0xaf213f) and LeaveTangent that tangent divided by its "
        "own length (0xaf214c comiss against 0, 0xaf215c divsd, 0xaf2164 mulpd, "
        "0xaf216f/0xaf2175) -- a unit vector again, or the zero vector when the "
        "tangent has no length (0xaf2156 jbe).",
        "A straight conveyor run of length L along a unit direction d gets "
        "exactly two points. Point 0: Location at the start, ArriveTangent = d, "
        "LeaveTangent = d * T. Point 1: Location at the end, ArriveTangent = "
        "d * T, LeaveTangent = d. T is clamp(L * 0.5, 50, 600) in centimetres, "
        "so arrive and leave differ at both ends: the OUTER tangents are unit "
        "vectors and the INNER ones carry the length. A 400 cm belt is "
        "(1, 200, 200, 1), and the 50 cm floor means a 60 cm belt is "
        "(1, 50, 50, 1) rather than (1, 30, 30, 1). The clamp is on the 3D "
        "distance even in the 2D builder, and both builders use the same three "
        "constants, so an inclined straight run is scaled by its true length. "
        "What the start tangent IS is the hologram's business, not this rule's: "
        "AutoRouteSpline is declared as AutoRouteSpline(startConnectionPos, "
        "startConnectionNormal, endConnectionPos, endConnectionNormal) "
        "(Hologram/FGConveyorBeltHologram.h:97) and the vector it hands Start is "
        "built by inlined vector code this did not unpick, so that a belt leaves "
        "a port along the port's facing is this project's own rule rather than "
        "one quoted here. Nothing refuses a spline for leaving off-facing "
        "either: ValidateConveyorBelt's four checks are length, minimum length, "
        "incline and -- in the curve mode only -- curvature, and none of them "
        "looks at the first segment's direction.",
    ),
    "factory.potential": (
        "extracted",
        "compute",
        "GetCurrentMaxPotential is a three-line forward: it passes the shard "
        "type 1 (0x4ed7cc mov dl,1), mMinPotential as the floor (0x4ed7d6), "
        "mMaxPotential as the starting value (0x4ed7ce) and a per-shard "
        "multiplier of 1.0 (0x4ed7c4, stored as the fifth argument at "
        "0x4ed7de) to GetCurrentMaxPotentialForType (0x4ed7e4). That function "
        "(six chained .pdata chunks, read whole) returns the floor untouched "
        "when mInventoryPotential is null (0x4ed7fd), and otherwise asks "
        "GetSlotsForPowerShardType for the slot indices of that type "
        "(0x4ed83e) and runs one pass per slot: "
        "UFGInventoryComponent::GetStackFromIndex (0x4ed886), the stack's item "
        "class tested against UFGPowerShardDescriptor (0x4ed908) and its "
        "GetPowerShardType against the asked-for type (0x4ed959/0x4ed95e), "
        "then `total += GetBoostValue(itemClass) * NumItems * multiplier` "
        "(0x4ed988 the call, 0x4ed98d/0x4ed993 the count as a float, 0x4ed996 "
        "and 0x4ed99a the two multiplies, 0x4ed99e the add), looping at "
        "0x4ed9b3 and returning `maxss total, floor` at 0x4ed9e9. "
        "GetSlotsForPowerShardType gives type 1 the indices [0, "
        "mPotentialShardSlots) (0x4f1af0 cmp dl,1, 0x4f1af9 and 0x4f1bb5 the "
        "member, 0x4f1bbb the back edge) and type 2 the single index "
        "mPotentialShardSlots (0x4f1bd1 cmp dl,2, 0x4f1bda a flag byte at "
        "[rcx+707h], 0x4f1be7 the member, 0x4f1c86 the one store). "
        "mPotentialShardSlots itself is not the class default: "
        "AFGBuildableFactory::BeginPlay calls AFGBuildableSubsystem::Get "
        "(0x4d40d5) and, when bit 0 of the bitfield byte at [rdi+770h] is clear "
        "(0x4d40df/0x4d40e6/0x4d40e9), copies the subsystem's dword at +350h "
        "onto it (0x4d40eb/0x4d40f1); bit 1 and the dword at +354h do the same "
        "for mProductionShardSlotSize (0x4d40f7..0x4d4102). The potential is "
        "then spent in two places, both rounding it to a whole percent first: "
        "CalcProducingPowerConsumptionForPotential does "
        "`powf(RoundToInt(p * 100) * 0.01, mPowerConsumptionExponent)` "
        "(0x4d58b6 mulss 100.0, 0x4d58c6/0x4d58ca/0x4d58d2/0x4d58de UE's "
        "RoundToInt on SSE, 0x4d58e7 mulss 0.01, 0x4d58d6 the exponent, "
        "0x4d58ef call powf) and multiplies the virtual producing consumption "
        "by it (0x4d58fd the vtable call at +950h, 0x4d5903 mulss); the static "
        "CalcOverclockPowerConsumption(power, overclock, exponent) is the same "
        "`powf` then multiply with no rounding (0x4d5895, 0x4d589a). "
        "AFGBuildableManufacturer::CalcProductionCycleTimeForPotential divides "
        "the recipe class default's duration at [recipe+6Ch] by "
        "mManufacturingSpeed (0x524953/0x524958) and then by the same rounded "
        "potential (0x524960..0x524974 the RoundToInt, 0x52497a "
        "divss 100.0 by it, 0x52497e the multiply).",
        "Overclocking is a **computation, never a refusal**: nothing in this "
        "path turns a placement or a setting away, which is why the effect is "
        "compute. Four numbers follow, and all four are in registry.json. "
        "First, the ceiling: a buildable's maximum potential is its "
        "mMaxPotential plus, for each power shard sitting in one of its "
        "potential slots, that shard's own mExtraPotential -- 0.5 for "
        "Desc_CrystalShard_C, limits.potential_per_shard -- with the class's "
        "per-shard multiplier of 1, floored at mMinPotential (0.01 on every "
        "machine). Second, how many slots: not the class's own "
        "mPotentialShardSlots, which is 0 on all 62 classes Docs.json dumps, "
        "but the buildable subsystem's default, because no shipped class sets "
        "mOverridePotentialShardSlots -- limits.potential_shard_slots_default, "
        "3. So a machine runs between 1 % and 250 %, and a registry reader that "
        "took potential_shard_slots at face value would say a Constructor "
        "cannot be overclocked at all. Third, what potential does: it divides "
        "the production cycle time, after being rounded to a whole percent -- a "
        "6 s cycle at 2.5 is 2.4 s -- and that rounding means the slider's 1 % "
        "steps are the only values that exist. Fourth, what it costs: power is "
        "multiplied by "
        "potential raised to the class's own mPowerConsumptionExponent, which "
        "is 1.321929 on every manufacturer and extractor and 1.6 on everything "
        "else, so there is no global exponent and buildables.power_exponent is "
        "per class rather than a limit. The production-boost half of the same "
        "machinery is the manufacturer.production_boost rule.",
    ),
    "manufacturer.production_boost": (
        "extracted",
        "compute",
        "GetCurrentMaxProductionBoost passes the shard type 2 (0x4eda1c mov "
        "dl,2), mBaseProductionBoost as both the floor and the starting value "
        "(0x4eda14, 0x4eda26) and mProductionShardBoostMultiplier as the "
        "per-shard multiplier (0x4eda1e, stored as the fifth argument at "
        "0x4eda29) to the same GetCurrentMaxPotentialForType (0x4eda2f) the "
        "factory.potential rule quotes: `total = mBaseProductionBoost + sum "
        "over the shards in the slot of GetBoostValue(shard) * NumItems * "
        "mProductionShardBoostMultiplier` (0x4ed988, 0x4ed996, 0x4ed99a, "
        "0x4ed99e, floored at 0x4ed9e9), over the one slot "
        "GetSlotsForPowerShardType hands out for type 2 (0x4f1bd1, 0x4f1bda, "
        "0x4f1be7, 0x4f1c86). What the resulting multiplier does is in "
        "AFGBuildableManufacturer::SetCurrentProductionBoost, which after the "
        "base class's (0x54780c) walks mCachedRecipe's product list "
        "(0x54785c) and, per product, takes the amount at [product+8] "
        "(0x547919), converts it (0x547922), multiplies it by "
        "mCurrentProductionBoost (0x54788d loads it, 0x547925) and rounds it to "
        "a whole item -- 0x5478ba loads -0.5, 0x547929 doubles the product, "
        "0x54792d subtracts, 0x547931 cvtss2si, 0x547936 sar 1, 0x547939 neg, "
        "UE's RoundToInt on SSE through a negation -- then asks "
        "mOutputInventory whether a stack of that size would fit (0x5479ab the "
        "count, 0x5479b3 the stack, 0x5479be HasEnoughSpaceForStack).",
        "A somersloop in a machine's production-boost slot multiplies **the "
        "recipe's product amounts**, not its cycle time: a boosted cycle takes "
        "exactly as long and yields RoundToInt(amount * boost) of each product. "
        "The multiplier for N somersloops in a machine whose slot holds S is "
        "mBaseProductionBoost + N * mExtraProductionBoost * "
        "mProductionShardBoostMultiplier: 1.0 + N * 1.0 * the class's own "
        "multiplier, since Desc_WAT1_C states an mExtraProductionBoost of 1.0 "
        "(limits.production_boost_per_slot). The multiplier is 1.0 on a "
        "Constructor and a Smelter, 0.5 on an Assembler and a Foundry and 0.25 "
        "on a Manufacturer, and S is the class's own mProductionShardSlotSize "
        "where mOverrideProductionShardSlotSize is set and "
        "limits.production_boost_slots_default otherwise -- 1, 2, 2 and 4 "
        "respectively -- so N * multiplier reaches exactly 1 on all of them and "
        "a fully sloop'd machine doubles its output, whatever its slot count. "
        "The power that costs is the other exponent: "
        "buildables.production_boost_power_exponent, 2.0 on every class that "
        "carries one. Two caveats for a blueprint author. The rounding is on "
        "each product separately and to a whole item, so a recipe producing an "
        "odd amount does not scale linearly at fractional boosts. And nothing "
        "here refuses a setting -- the effect is compute -- but the output "
        "inventory is re-checked for space at the new amount, which is a "
        "production stall and not a placement rule.",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _rule(
    rule_id: str, function: dict[str, Any], also: Sequence[tuple[str, dict[str, Any]]] = ()
) -> dict[str, Any]:
    """Turn one disassembled function into the rule record, evidence and all.

    ``also`` are the other functions this rule was read across, as
    ``(symbol, disassembly)``: their instructions join the same pool, so the
    evidence can quote a caller or a callee by address, and they are listed in
    ``also_read`` with the RVA each was found at. The rule's own ``rva``,
    ``class`` and ``function`` stay the entry in :data:`TARGETS`.
    """
    cls, name, header = TARGETS[rule_id]
    status, effect, comparison, interpretation = INTERPRETATIONS[rule_id]
    if effect not in RULE_EFFECTS:
        raise SystemExit(f"{rule_id}: {effect!r} is not one of {RULE_EFFECTS}")
    if effect == "none" and status == "extracted":
        raise SystemExit(
            f"{rule_id}: an extracted rule cannot have the effect 'none' -- either the "
            "branch that was read does something, or the status is not 'extracted'"
        )
    # A rule is a claim about what a function does, and an `extracted` status or
    # an effect of `none` is also a claim about what it does *not* do. Neither
    # survives a function `sfy-native` could not read to an end the game states:
    # the comparison, the clamp or the quantisation may be in the part that was
    # never decoded. See :data:`sfy_disasm.BOUNDED_SIZE_SOURCES`.
    short = unbounded([function, *(other for _symbol, other in also)])
    if short:
        if effect == "none":
            raise SystemExit(
                f"{rule_id}: an effect of 'none' says the instructions that were read "
                "enforce nothing, which is an absence, and it cannot be claimed from a "
                f"function that was not read to its end: {'; '.join(short)}"
            )
        status = "partial" if status == "extracted" else status
        comparison = (
            f"{comparison} NOT READ IN FULL: {'; '.join(short)}. Whatever the "
            "function does past that point is unknown, so this rule is partial "
            "however much of the comparison is quoted above."
        )
    instructions = list(function["instructions"])
    for _symbol, other in also:
        instructions += other["instructions"]
    by_rva = {i["rva"]: i for i in instructions}
    missing = [rva for rva in EVIDENCE[rule_id] if rva not in by_rva]
    if missing:
        raise SystemExit(
            f"{rule_id}: {cls}::{name} has no instruction at {missing} -- the function "
            "moved, and its interpretation has to be re-read before this can be written"
        )
    rule = {
        "id": rule_id,
        "class": cls,
        "function": name,
        "rva": function["rva"],
        "status": status,
        "effect": effect,
        "reads": sorted({i["member"]["name"] for i in instructions if "member" in i}),
        "constants": sorted(
            {
                f"{i['constant']['at']} f32={i['constant']['f32']} f64={i['constant']['f64']}"
                for i in instructions
                if "constant" in i
            }
        ),
        "calls": sorted(
            {i["call"] for i in instructions if "call" in i}
            | {i["import"] for i in instructions if "import" in i}
        ),
        "comparison": comparison,
        "interpretation": interpretation,
        "evidence": [_line(by_rva[rva]) for rva in sorted(EVIDENCE[rule_id], key=_rva)],
        "header": header,
    }
    if also:
        rule["also_read"] = [f"{symbol} @ {other['rva']}" for symbol, other in also]
    return rule


def _one(functions: list[dict[str, Any]], spec: str) -> dict[str, Any]:
    """The single disassembly ``spec`` names, refusing an ambiguous match.

    ``spec`` is ``Class::Method`` or ``Class::Method@0xRVA``; the second form is
    how one of several bodies the linker gave the same name -- two overloads, a
    constructor emitted twice -- is picked out.
    """
    symbol, _, rva = spec.partition("@")
    found = [f for f in functions if f["symbol"] == symbol and (not rva or f["rva"] == rva)]
    if len(found) != 1:
        raise SystemExit(
            f"{spec} matched {len(found)} of the symbols sfy-native reported "
            f"({[f['rva'] for f in functions if f['symbol'] == symbol]}), wanted one"
        )
    return found[0]


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
            primary = _one(disasm(dll, pdb, symbol, scratch), symbol)
            also = []
            for spec in ALSO_READ.get(rule_id, ()):
                other = spec.partition("@")[0]
                also.append((other, _one(disasm(dll, pdb, other, scratch), spec)))
            rule = _rule(rule_id, primary, also)
            rules.append(rule)
            across = f" (+{len(also)} functions)" if also else ""
            print(
                f"{rule_id:<26} {symbol} @ {primary['rva']}{across}  "
                f"{rule['status']}, {rule['effect']}"
            )
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
