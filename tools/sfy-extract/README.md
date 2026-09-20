# sfy-extract

Reads the installed Satisfactory game data and writes
`src/flab2bp/sfy/data/assets.json`: the connection ports of every buildable,
whatever placement limits the game's assets carry, the asset path of every
class the game's Docs.json states one for, and the asset path of every cooked
item descriptor read off the packages themselves. The first two are in no
Docs.json entry; the third is in Docs.json but not in the half of it
`flab2bp.sfy.docs` keeps, which is class names; the fourth is the same fact as
the third, read a second, independent way.

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
uv run python scripts/sfy_native_directions.py   # -> data/native_directions.json
cd tools/sfy-extract
dotnet run -- "$HOME/Satisfactory" extract \
    ../../src/flab2bp/sfy/data/assets.json \
    ../../src/flab2bp/sfy/data/native_directions.json
cd ../..
uv run python scripts/sfy_registry.py       # merges docs.json + assets.json -> registry.json
```

The second argument is required. It is what the game's own C++ constructors set
a connection's direction to, and without it no port whose asset omits its
direction could be resolved from the game at all — see
[port directions](#port-directions-and-the-archetype-chain).

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

## Viewer component geometry

The viewer's `viewer_geometry.json` is separate from placement clearance and
the routing registry. Regenerate its current Foundry component coverage with:

```bash
dotnet run -- "$HOME/Satisfactory" geometry \
    ../../src/flab2bp/sfy/data/viewer_geometry.json \
    FoundryMk1/Build_FoundryMk1.
```

`geometry <out.json> [package-filter]` reads each static-mesh component's cooked
`RenderData.Bounds`, transforms its eight corners through the Blueprint's SCS
parent chain, and retains a separate actor-local envelope per component. The
output includes mesh asset paths, original bounds, component transforms, game
build, archive names and usmap hash. It does not export triangles or animate
vertex meshes. Native root attachments are supported; an external non-root
parent is rejected rather than assigned an invented transform. The current
extract covers the Foundry's static body and vertex-animated component, including
the latter's inherited body rotation. Other buildables continue to use available
registry mesh bounds or clearance envelopes.

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

## Port directions and the archetype chain

Unreal leaves a template property out of a cooked asset whenever it equals the
**archetype**'s value. So an absent `mDirection` does not mean the enum's zero,
`FCD_INPUT`: it means "whatever my archetype has", and the chain of archetypes is

```
this Blueprint's template                                  -> "asset"
  the parent Blueprint's template of the same name         -> "asset-inherited"
    ...
      the subobject the native constructor made            -> "native"
        nothing answered                                   -> "unknown"
```

The extractor walks all of it and emits `direction` and `direction_source` per
port. `scripts/sfy_registry.py` only checks the vocabulary; nothing downstream
re-derives a direction, and **neither a blueprint corpus nor the component's own
name is a source** — a corpus says what somebody once built and a name is a
convention, and neither is a fact about the game. A port nothing answers for is
shipped `unknown`, and the validator refuses to route to it. (In 1.2.0 there are
none: 111 `asset`, 1 `asset-inherited`, 176 `native`.)

The last step is machine code, so it comes out of the shipped DLL.
`scripts/sfy_native_directions.py` drives `tools/sfy-native disasm` over the
constructors below and writes `src/flab2bp/sfy/data/native_directions.json`; the
extractor copies the whole file into `assets.json`'s
`provenance.native_direction_defaults`, so the evidence travels with the claim
and the registry can be read without a game install.

**What a component class defaults to**, which is the archetype of a Blueprint's
own component:

| Component | Class | Member | Value | Read at |
| --- | --- | --- | --- | --- |
| `FGFactoryConnectionComponent` | `UFGFactoryConnectionComponent` | `mDirection` | 0, `FCD_INPUT` → `input` | `mov byte ptr [rbx+258h],0` @ `0x7b5ff1`, in its constructor at `0x7b5fd0` |
| `FGPipeConnectionComponent`, `FGPipeConnectionFactory` | `UFGPipeConnectionComponentBase` and down | `mPipeConnectionType` | 0, `PCT_ANY` → `any` | **no store**: neither constructor in the chain writes offset 600, so the member keeps `UObject`'s zero |
| `FGPowerConnectionComponent` | `UFGPowerConnectionComponent` | — | `any` | a circuit connection: its constructor chains to `UFGCircuitConnectionComponent` (`0x8f116d`) and the class has no direction property at all |

**What a buildable that makes its own connections sets them to**, which is the
archetype of every template deriving from it. Four do, and they are exactly the
classes whose connection templates are outered to the class default object
rather than being construction-script components:

| Constructor | Applies to | Value |
| --- | --- | --- |
| `AFGBuildableConveyorBase::AFGBuildableConveyorBase` @ `0x4c96b0` | `AFGBuildableConveyorBelt`, `AFGBuildableConveyorLift` | 2, `FCD_ANY` → `any` (`mov byte ptr [rax+258h],2` @ `0x4c9914` for `mConnection0` and @ `0x4c9b59` for `mConnection1`) |
| `AFGBuildablePoleConveyor::AFGBuildablePoleConveyor` @ `0x51b4c0` | itself | 3, `FCD_SNAP_ONLY` → `snap_only` (`mov byte ptr [rbx+258h],3` @ `0x51b5bf`) |
| `AFGBuildablePipeline::AFGBuildablePipeline` @ `0x51ac10` | itself | 0, `PCT_ANY` → `any` (`mov byte ptr [rax+258h],bpl` @ `0x51aeab` and `0x51af4a`, with `xor ebp,ebp` @ `0x51ac2c`) |
| `AFGBuildablePolePipe::AFGBuildablePolePipe` @ `0x666c10` | itself | 3, `PCT_SNAP_ONLY` → `snap_only` (`mov byte ptr [rbx+258h],3` @ `0x666d75`) |

Three things are worth stating plainly.

1. **Both conveyor ends are `FCD_ANY`, not input and output.** That is what
   `ConveyorAny0` and `ConveyorAny1` are named after.
   `Buildables/FGBuildableConveyorBase.h:380` — "mConnection0 is the input,
   mConnection1 is the output" — is about which end items enter and leave the
   belt by; it is not `mDirection`, and the registry took it for `mDirection`
   until this. A placed belt gets its two directions from whatever it snapped
   to, under the `belt.snap_directions` rule in `data/hologram_rules.json`.
   Which end items *do* enter by is its own field now: `conveyor_flow` in the
   same `native_directions.json`, read out of `Factory_Tick`'s grab and carried
   into `registry.json` as each mark's `flow`. That file names the two C++
   *members*; which component sits in each is `conveyor_connections` here, read
   off the class default object's `mConnection0`/`mConnection1` object
   properties, so the two port names are the game's own statement and not an
   inference from a name that ends in 0.
2. **The component class default only applies to a Blueprint's own component.**
   A subobject a native constructor made takes that constructor's value, so a
   native subobject with no matching entry above is `unknown` rather than
   `FCD_INPUT` — which is what kept `Build_ConveyorPole_C.SnapOnly0` from being
   called an input.
3. **`mConnection1`'s store is in a second `.pdata` chunk.** MSVC split
   `AFGBuildableConveyorBase`'s constructor, and the store that sets the second
   connection is 600 bytes past the chunk the symbol is in. `sfy-native disasm`
   follows the chain (`size_source: pdata-chained`), so both stores are quoted
   from its output; a game update that stops the chain from resolving fails the
   script by address rather than shipping half the answer.

## The descriptor paths: a second reading

`class_paths` above is what Docs.json says. `descriptor_paths` is what the
cooked assets say, and the two are independent: a `BlueprintGeneratedClass`
export's name is the class (`Desc_IronPlate_C`) and its own outer is the package
(`/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate`), so the whole path
comes straight out of the asset with no Docs.json text matched and no folder
reconstructed. `tests/sfy/test_templates.py` holds every one of the registry's
750 `item_paths` to it.

**What counts as a descriptor is the game's class hierarchy, not a name.** Two
thirds of them are called `Desc_*`, but fifteen of the ones the registry needs
are not — `BP_EquipmentDescriptorGasmask_C`, `BP_ItemDescriptorPortableMiner_C`,
`Foundation_ConcretePolished_8x2_C` and their kin — so the filter walks the
super chain instead: the Blueprint supers by name, and then the shipped usmap's
native `SuperType` chain, until it reaches `FGItemDescriptor`
(`FGBuildingDescriptor → FGBuildDescriptor → FGItemDescriptor`,
`FGConsumableDescriptor → FGEquipmentDescriptor → FGItemDescriptor`,
`FGAmmoTypeProjectile → FGAmmoTypeHomingBase → FGAmmoType → FGItemDescriptor`).
That costs a load of every cooked package, because nothing narrower can answer
which classes derive from a native one.

A class name two packages both define cannot be resolved by name; it is listed
in `descriptor_paths_ambiguous` rather than guessed at. There are none in 1.2.0.

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
| Belt/pipe port direction | `mDirection` (`EFactoryConnectionDirection`) and `mPipeConnectionType` (`EPipeConnectionType`) on the template — but only when it differs from the component **archetype**'s value, which is not the enum zero and is not in the pak. 111 of the 288 ports spell theirs out; the other 177 are resolved up the archetype chain. See [port directions](#port-directions-and-the-archetype-chain). |
| Power connection counts | `mMaxNumConnectionLinks` on the power connection template, the `FGCircuitConnectionComponent` UPROPERTY for how many wires may end there. Like `mDirection` it is serialised only where a Blueprint in the chain overrides the archetype, so the chain is walked the same way and `max_connections_source` says which link answered (`asset`, `asset-inherited`, or `unknown`). Every pole overrides it: the three marks say **4, 7 and 10** and their wall variants repeat those, the power tower and its platform say 3, and the four lights and the battery say 2. The other power ports in the content — machine power inputs, all of them — say nothing anywhere in their chain, and are emitted as `null`/`unknown` rather than as a number this tool made up; `scripts/sfy_registry.py` then fills them from the `UFGCircuitConnectionComponent` constructor's **1** in `native.json` and retags them `native`. No pole carries a wire length of its own; `mMaxLength` is on the wire (`wire_max_cm`, one number for all of them). |
| Spline mesh boxes (`mesh_bounds`) | The cooked `UStaticMesh`'s own `RenderData.Bounds`, as `Origin` and `BoxExtent`, reached from the class default object's `mMesh` (a conveyor belt, `Buildables/FGBuildableConveyorBelt.h:146`) or `mMidMesh` (a lift's repeated mid section, `…ConveyorLift.h:224`). Both are `EditDefaultsOnly` UPROPERTYs, so a mark that does not restate one inherits its parent Blueprint's — lifts Mk2 to Mk6 state only `mMidMesh` — and the CDO chain is walked for that reason. 22 classes carry one in 1.2.0: the six belt marks, the six lift marks, the pipelines, the hypertube, the railway and the three foundation passthroughs. Each entry names the `property`, the `mesh` asset path and the class it was `stated_on`. **This is the mesh, not the clearance**: the game tests the boxes `AFGBuildableConveyorBelt::CreateClearanceData` lays along the spline (the `belt.clearance` rule), which are narrower than the Mk1 mesh. |
| Shard slot defaults (`subsystem_defaults`) | The cooked Blueprint whose super chain reaches the native `FGBuildableSubsystem` — found by that chain during the descriptor scan, never by its package's name — and the two `EditDefaultsOnly` counts on its class default object. `AFGBuildableFactory::BeginPlay` copies them onto every buildable whose own `mOverride*` bit is clear. `BP_BuildableSubsystem_C` overrides `mDefaultProductionShardSlotSize` to **1** and leaves `mDefaultPotentialShardSlots` alone, so that one comes back `null` and the merge falls through to the constructor's **3**. |
| Item and recipe asset paths | Docs.json, in the references one entry makes to another (`mIngredients`, `mProduct`, `mProducedIn`, a schematic's unlocked recipes). A blueprint names an item descriptor and a recipe by whole asset path — `/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C` — and the folder is not derivable from the class name, which is all `docs.json` keeps. Every `/Game/….<Name>_C` the dump mentions is collected into `class_paths` (2581 of them in 1.2.0); a name found under two packages cannot be resolved by name and is listed in `class_paths_ambiguous` instead (none in 1.2.0). `scripts/sfy_registry.py` keeps the ones the registry needs — 750 item descriptors and 872 recipes — and refuses if one is missing. |
| The same item paths, read a second way | `descriptor_paths`: the cooked assets themselves. Every `.uasset` in the content (23688 packages, about 30 s) is loaded, and each `BlueprintGeneratedClass` export whose class chain reaches `FGItemDescriptor` contributes its own name and its own outer — 1031 of them in 1.2.0, 0 ambiguous. See [the descriptor paths](#the-descriptor-paths-a-second-reading) below. |
| `mHologramClass` | The buildable's class default object. 454 of 595 buildables name one. |
| `pipe_bend_radius_cm` | A hologram Blueprint CDO: `Holo_Pipeline_C.mBendRadius = 100`. The only spline limit any asset overrides, along with `Holo_PipeHyper_C` (300 / 10000) and `Holo_RailroadTrack_C.mMinBendRadius` (1500). |
| `belt_max_spline_cm`, `pipe_max_spline_cm`, `pipe_bend_radius_2d_cm`, `pipe_min_bend_radius_cm` | C++ only, but with in-class initialisers, so the headers state them outright. Transcribed into `HEADER_DEFAULTS` in `scripts/sfy_registry.py` with file and line. |
| `wire_max_cm` | C++ only. `AFGBuildableWire::mMaxLength` has no in-class initialiser, so `Build_PowerLine_C`'s cooked class default object omits it — but Docs.json is a reflection dump of those same class defaults and gives 10000. The extractor reads it from `CommunityResources/Docs/en-US.json` for that reason. |
| `belt_bend_radius_cm`, `belt_max_incline_deg`, the four conveyor-lift heights, `hologram_grid_cm` | **In no asset.** They are native constructor values the install does not ship, and the lift heights are not even UPROPERTYs — `AFGConveyorLiftHologram` declares them as plain members "fetched and calculated from the buildable". A `props` sweep over all 48573 cooked files finds no asset that sets any of them. Task 13's `tools/sfy-native` reads four of them out of the shipped DLL and the disassembly of `BeginPlay` gives the formula for the other three, which `scripts/sfy_registry.py` applies to Docs.json's `mMeshHeight`. Every limit in the registry now has a game-data source; `hologram_grid_cm`'s per-hologram overrides (`Holo_PowerPole_C`, `Holo_PowerTower_C`, `Holo_StreetLight_C`, all 50) stay here in `holograms` and land on the buildable as `grid_snap_cm`. |
