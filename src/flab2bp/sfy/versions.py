"""Version constants transcribed from the game's public headers.

Source: CommunityResources/Headers.zip, Source/FactoryGame/Public/SaveCustomVersion.h
and FGFactoryBlueprintTypes.h. Member order is the enum order; values are
implicit and must not be reordered.
"""

from __future__ import annotations

import struct
from enum import IntEnum

__all__ = [
    "BLUEPRINT_CONFIG_VERSION",
    "BLUEPRINT_HEADER_VERSION",
    "LATEST",
    "SAVE_CUSTOM_VERSION_GUID",
    "SaveCustomVersion",
]

BLUEPRINT_HEADER_VERSION = 2  # FBlueprintHeader::AddedUsedRecipes
BLUEPRINT_CONFIG_VERSION = 6  # FBlueprintConfigVersion::RemovedFilteredProfanityName
SAVE_CUSTOM_VERSION_GUID = struct.pack("<4I", 0x21043E2F, 0x13E61FD6, 0x513B9D51, 0x3636A230)


class SaveCustomVersion(IntEnum):
    BeforeCustomVersionWasAdded = 0
    DROPPED_StoreTransform = 1
    DROPPED_ChangeObjectHeader = 2
    DROPPED_PropertyTagsAsStrings = 3
    DROPPED_StoreVehiclesBodyState = 4
    DROPPED_ActorPlacedInLevelSaved = 5
    DROPPED_MovedActorOuter = 6
    DROPPED_PowerConnectionComponents = 7
    DROPPED_SerializeTrainTimetable = 8
    DROPPED_FactoryConnectionWorldToLocal = 9
    DROPPED_CircuitObjects = 10
    DROPPED_DockingStationSingleInventory = 11
    DROPPED_SavingBuildShortcuts = 12
    DROPPED_GamePhaseManagerAdded = 13
    DROPPED_RemovedRelativeTransformsFromConnectionComponents = 14
    DROPPED_MCP_RestoreLostPawn = 15
    DROPPED_WireSpanFromConnnectionComponents = 16
    DROPPED_RenamedSaveSessionId = 17
    ChangedGeoThermalGeneratorSaved = 18
    OverwriteOldRailroadData = 19
    ResetFactoryLegs = 20
    SaveFileIsCompressed = 21
    BU3SaveCompatibility = 22
    BuildingColorConversion = 23
    RescuedFriendDoggos = 24
    CheckPickedUpItems = 25
    PerInstanceCustomColors = 26
    DoubleRampPositioning = 27
    TrainBlueprintClassAdded = 28
    AddedSublevelStreaming = 29
    AddedResourceSinkTrack = 30
    AddedColoringSupportToConcretePillars = 31
    AddedResourceSinkTrack2 = 32
    AddedCachedLocationsForWire = 33
    ReworkedSplittersAndMergers = 34
    ReworkedProductivityMonitor = 35
    NativizedShoppingList = 36
    UnrealEngine5 = 37
    IntroducedWorldPartition = 38
    DroneActionRefactor = 39
    MultipleWireMeshRefactor = 40
    SwitchTo64BitSaveArchive = 41
    ResetBrokenBlueprintSplines = 42
    RefactoredInventoryItemState = 43
    AddedPaintFinishes = 44
    NormalizeChainSplineArriveAndLeave = 45
    Version1 = 46
    PoleRefactor = 47
    LightweightBuildableSubsystemWritesRuntimeVersion = 48
    SerializeObjectFlags = 49
    BackToBackRailroadSwitches = 50
    SerializePerStreamableLevelTOCVersion = 51
    RailroadTrackConnectionCleanup = 52
    SerializeDataPackageVersionAndCustomVersions = 53
    DROPPED_RailroadSubsystemThirdRailConnectionsCleanup = 54
    RailroadSubsystemThirdRailConnectionsCleanupRedone = 55
    UnlockAvailableItemDescriptorsForPickedUpItems = 56
    NewPlayerInfoHandleSerializationFormat = 57
    FixNewPlayerInfoHandleSerializationFormat = 58
    FixedUpInvalidPatternRotationsBlueprintSupport = 59
    FixedMissingFICSITMaterials = 60


LATEST = SaveCustomVersion.FixedMissingFICSITMaterials
