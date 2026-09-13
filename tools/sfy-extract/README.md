# sfy-extract

Reads the installed Satisfactory game data and writes
`src/flab2bp/sfy/data/assets.json`: the connection ports of every buildable and
whatever placement limits the game's assets carry. Docs.json — the other half of
the registry, handled by `flab2bp.sfy.docs` — has neither.

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
| Belt/pipe port direction | `mDirection` (`EFactoryConnectionDirection`) and `mPipeConnectionType` (`EPipeConnectionType`) on the template — but only when it differs from the class default. The defaults are the enum zeroes, `FCD_INPUT` and `PCT_ANY`: `Build_ConstructorMk1_C`'s `Input0` carries no `mDirection` while every output carries `FCD_OUTPUT`, and the ten genuinely bidirectional connections in the whole game spell `FCD_ANY` out. Power connections are circuit connections and have no direction. |
| `mHologramClass` | The buildable's class default object. 454 of 595 buildables name one. |
| `pipe_bend_radius_cm` | A hologram Blueprint CDO: `Holo_Pipeline_C.mBendRadius = 100`. The only spline limit any asset overrides, along with `Holo_PipeHyper_C` (300 / 10000) and `Holo_RailroadTrack_C.mMinBendRadius` (1500). |
| `belt_max_spline_cm`, `pipe_max_spline_cm`, `pipe_bend_radius_2d_cm`, `pipe_min_bend_radius_cm` | C++ only, but with in-class initialisers, so the headers state them outright. Transcribed into `HEADER_DEFAULTS` in `scripts/sfy_registry.py` with file and line. |
| `wire_max_cm` | C++ only. `AFGBuildableWire::mMaxLength` has no in-class initialiser, so `Build_PowerLine_C`'s cooked class default object omits it — but Docs.json is a reflection dump of those same class defaults and gives 10000. The extractor reads it from `CommunityResources/Docs/en-US.json` for that reason. |
| `belt_bend_radius_cm`, `belt_max_incline_deg`, the four conveyor-lift heights, `hologram_grid_cm` | **Nowhere.** They are native constructor values the install does not ship. The lift heights are not even UPROPERTYs — `AFGConveyorLiftHologram` declares them as plain members "fetched and calculated from the buildable" — so no reflection dump can carry them either. A `props` sweep over all 48573 cooked files finds no asset that sets any of them. They stay `None` in `registry.json`; `scripts/sfy_registry.py` lists them in `UNFILLABLE` with the reason and `tests/sfy/test_registry.py` has a strict `xfail` for each, which will fail loudly if a future build does ship them. |
