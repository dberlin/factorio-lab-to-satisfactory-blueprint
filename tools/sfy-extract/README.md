# sfy-extract

Reads the installed Satisfactory game data and writes
`src/flab2bp/sfy/data/assets.json`: the connection ports of every buildable,
whatever placement limits the game's assets carry, and the asset path of every
class the game's Docs.json states one for. The first two are in no Docs.json
entry; the third is in Docs.json but not in the half of it `flab2bp.sfy.docs`
keeps, which is class names.

A second mode, `structs`, writes `src/flab2bp/sfy/data/struct_schemas.json` —
the game's own field list for the structs the blueprint corpus carries, taken
from the usmap mappings rather than from any asset. See
[the `structs` mode](#the-structs-mode-struct-schemas-from-the-usmap) below.

`docs/sfy-regenerating-game-data.md` is the whole-pipeline runbook; this file is
the tool.

## Prerequisites

- .NET 10 SDK (`dotnet --version` ≥ 10.0.400).
- A Satisfactory install with `CommunityResources/FactoryGame.usmap`. The tool
  reads `FactoryGame/Content/Paks` (`FactoryGame-Windows.pak` plus the
  `.utoc`/`.ucas` IoStore containers) and the usmap next to it. Neither archive
  is encrypted, so no AES key is needed; the tool warns if that ever changes.

## Running it

```bash
cd tools/sfy-extract
dotnet run -- "$HOME/Satisfactory" extract ../../src/flab2bp/sfy/data/assets.json
cd ../..
uv run python scripts/sfy_registry.py       # merges docs.json + assets.json -> registry.json
```

`assets.json` and `registry.json` are committed. Regenerate both after a game
update and re-run `uv run pytest tests/sfy`. The extractor is deterministic:
re-running it produces a byte-identical file apart from `provenance.extracted`.

Two discovery modes help when a game update moves something:

```bash
dotnet run -- "$HOME/Satisfactory" list  ConstructorMk1/Build_ConstructorMk1.  # exports
dotnet run -- "$HOME/Satisfactory" props ConstructorMk1/Build_ConstructorMk1.  # exports + properties
```

The argument is a case-insensitive substring of the package path. With one, any
package is searched, not just `Build_*`, which is how to look at a `Holo_*`
hologram class. `props` over a whole subtree (say
`FactoryGame/Content/FactoryGame/Buildable/`) is how to find out whether a value
exists in the assets at all.

## The `structs` mode: struct schemas from the usmap

```bash
uv run python scripts/sfy_struct_names.py > /tmp/struct-names.txt
cd tools/sfy-extract
dotnet run -- "$HOME/Satisfactory" structs /tmp/struct-names.txt \
    ../../src/flab2bp/sfy/data/struct_schemas.json
```

`struct_schemas.json` is the game's own field list for every struct name the
blueprint corpus carries, and `tests/sfy/test_struct_schemas.py` checks the
codec's decoded structs against it. The names file is one struct name per line,
`#` comments and blank lines ignored; `scripts/sfy_struct_names.py` prints the
corpus's, and the mode adds each struct's super chain to whatever it is given.
A name with no usmap struct is a warning on stderr, is listed in
`provenance.missing`, and makes the exit code 1.

This mode neither mounts the paks nor loads a package: the schemas come out of
the shipped `FactoryGame.usmap` alone, which is why it takes about a second. The
CUE4Parse members it uses, in this version (1.2.2.202609), are

- `FileUsmapTypeMappingsProvider(path).MappingsForGame` → `TypeMappings`,
- `TypeMappings.Types`: `Dictionary<string, Struct>`,
- `Struct.SuperType` (the parent's name, or null) and `Struct.Properties`:
  `Dictionary<int, PropertyInfo>`,
- `PropertyInfo.Name`, `.ArraySize` and `.MappingType`,
- `PropertyType.Type`, `.StructType`, `.EnumName`, `.InnerType`, `.ValueType`.

`PropertyType.Type` is written out verbatim: CUE4Parse builds it from
`EPropertyType.ToString()`, so it already reads `StructProperty`,
`ArrayProperty`, `ByteProperty` and so on — the same spellings a save file's
property tag carries. All 20 names the corpus uses resolve, and all 13,792
struct types in the 1.2.0 mappings are searched for them.

## Versions

- **CUE4Parse 1.2.2.202609**, the newest published package. Newtonsoft.Json is
  pinned to 13.0.4 rather than 13.0.3 because CUE4Parse requires ≥ 13.0.4.
- **`EGame.GAME_UE5_6`**. The enum offers up to `GAME_UE5_9`; 5.6 is the right
  one — the blueprint fixtures' engine block says `5.6.1` and the usmap's own
  package file version is `(522, 1017)`, UE 5.6's.

## The usmap compatibility patch

`UsmapCompat.cs` rewrites the mappings in memory before handing them to
CUE4Parse. The dumper that produced the shipped `FactoryGame.usmap` writes
`OptionalProperty` (type 28) as a leaf: the type byte is not followed by the
wrapped inner property type. UE 5.6's format, and therefore CUE4Parse, reads an
inner type there, so CUE4Parse's parser desynchronises on the first one
(`OptionalPropertyTestObject.OptionalString`) and dies with an
`ArgumentOutOfRangeException` inside `UsmapProperties.ParsePropertyInfo`.

All 44 occurrences in the 1.2.0 mappings are on Engine, editor or plugin classes
— `OptionalPropertyTestObject`, `ToolMenu`, `NiagaraSystem`, `FontFace` and the
like. No `FactoryGame` class has one. So each of those 44 type bytes is rewritten
to `Utf8StrProperty` (29), which CUE4Parse also reads as a leaf. The patch
changes one byte per occurrence, so no offsets move and the trailing extension
chunks stay valid. The patched copy is written to the temp directory; the
install is never modified.

## Where each value lives

Discovery findings, so nobody has to repeat the search:

| Value | Where it is |
| --- | --- |
| Connection ports (`RelativeLocation`, `RelativeRotation`, `mConnectorClearance`) | Package exports of the buildable, as either `<Name>_GEN_VARIABLE` construction-script templates outered to the generated class, or natively-constructed components outered to the class default object. Both shapes occur and both are read. A Blueprint deriving from another Blueprint inherits its templates, so the `SuperStruct` chain is walked too — in 1.2.0 the 38 buildables that do this are all doors and walls, none with a connection. |
| Which components are ports | Seven connection component classes exist in the content. `FGFactoryConnectionComponent` is a belt port, `FGPipeConnectionComponent` and `FGPipeConnectionFactory` are pipe ports, `FGPowerConnectionComponent` is a power port. `FGPipeConnectionComponentHyper`, `FGTrainPlatformConnection` and `FGRailroadTrackConnectionComponent` are left out on purpose — nothing routes hypertubes or railways yet, and the mapping is by exact class name so a new one is dropped loudly (the extractor prints the classes it left out) rather than silently mislabelled. |
| Belt/pipe port direction | `mDirection` (`EFactoryConnectionDirection`) and `mPipeConnectionType` (`EPipeConnectionType`) on the template — but only when it differs from the component **archetype**'s value, which is not the enum zero and is not in the pak. Every output carries `FCD_OUTPUT` and the ten genuinely bidirectional connections spell `FCD_ANY` out; an absent `mDirection` is emitted as `"unknown"` and `scripts/sfy_registry.py` resolves it (84 ports, from the conveyor header, the fixture corpus or the naming convention, recorded per port as `direction_source`). Reading the absence as `FCD_INPUT` is what made every belt and lift end an input in the first registry. Pipes are not affected — `PCT_ANY` is the archetype value on every pipe in the content. Power connections are circuit connections and have no direction. |
| Power connection counts | `mMaxNumConnectionLinks` on the power connection template, the `FGCircuitConnectionComponent` UPROPERTY for how many wires may end there. Serialised only where the Blueprint overrides the native default, which every pole does: the three marks say **4, 7 and 10** and their wall variants repeat those, the power tower and its platform say 3, and the four lights and the battery say 2. The other 53 power ports in the content — machine power inputs, all of them — say nothing, and are emitted as `null` rather than as a number this tool made up. No pole carries a wire length of its own; `mMaxLength` is on the wire (`wire_max_cm`, one number for all of them). |
| Item and recipe asset paths | Docs.json, in the references one entry makes to another (`mIngredients`, `mProduct`, `mProducedIn`, a schematic's unlocked recipes). A blueprint names an item descriptor and a recipe by whole asset path — `/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C` — and the folder is not derivable from the class name, which is all `docs.json` keeps. Every `/Game/….<Name>_C` the dump mentions is collected into `class_paths` (2581 of them in 1.2.0); a name found under two packages cannot be resolved by name and is listed in `class_paths_ambiguous` instead (none in 1.2.0). `scripts/sfy_registry.py` keeps the ones the registry needs — 750 item descriptors and 872 recipes — and refuses if one is missing. |
| `mHologramClass` | The buildable's class default object. 454 of 595 buildables name one. |
| `pipe_bend_radius_cm` | A hologram Blueprint CDO: `Holo_Pipeline_C.mBendRadius = 100`. The only spline limit any asset overrides, along with `Holo_PipeHyper_C` (300 / 10000) and `Holo_RailroadTrack_C.mMinBendRadius` (1500). |
| `belt_max_spline_cm`, `pipe_max_spline_cm`, `pipe_bend_radius_2d_cm`, `pipe_min_bend_radius_cm` | C++ only, but with in-class initialisers, so the headers state them outright. Transcribed into `HEADER_DEFAULTS` in `scripts/sfy_registry.py` with file and line. |
| `wire_max_cm` | C++ only. `AFGBuildableWire::mMaxLength` has no in-class initialiser, so `Build_PowerLine_C`'s cooked class default object omits it — but Docs.json is a reflection dump of those same class defaults and gives 10000. The extractor reads it from `CommunityResources/Docs/en-US.json` for that reason. |
| `belt_bend_radius_cm`, `belt_max_incline_deg`, the four conveyor-lift heights, `hologram_grid_cm` | **In no asset.** They are native constructor values the install does not ship, and the lift heights are not even UPROPERTYs — `AFGConveyorLiftHologram` declares them as plain members "fetched and calculated from the buildable". A `props` sweep over all 48573 cooked files finds no asset that sets any of them. Task 13's `tools/sfy-native` reads four of them out of the shipped DLL and the disassembly of `BeginPlay` gives the formula for the other three, which `scripts/sfy_registry.py` applies to Docs.json's `mMeshHeight`. Every limit in the registry now has a game-data source; `hologram_grid_cm`'s per-hologram overrides (`Holo_PowerPole_C`, `Holo_PowerTower_C`, `Holo_StreetLight_C`, all 50) stay here in `holograms` and land on the buildable as `grid_snap_cm`. |
