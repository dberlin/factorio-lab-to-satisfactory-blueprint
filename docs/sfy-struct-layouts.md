# Struct layouts from the game

Two kinds of bytes in a blueprint carry no tags and so cannot be read off the
file: a `StructProperty` whose struct serialises itself, and the class trailer
an object writes after its property list. Both sections below are the game's
own answer, read out of the headers and the shipped binary.

Every `StructProperty` in a blueprint file is one of three things: a nested
tagged property list, a fixed engine layout (`Vector`, `Quat`, `Box`, …), or a
struct the game serialises itself, member by member, with no tags at all. The
third kind is the interesting one: nothing in the file says what those bytes
mean, so the layout has to come from the game. This file is where that evidence
is written down.

Two such structs occur in the 49-fixture corpus, and `flab2bp.sfy.properties`
decodes both — `KNOWN_CUSTOM_STRUCTS`. Anything else with a custom serializer
still keeps its bytes as a `BinaryStruct`, and
`tests/sfy/test_properties.py::test_no_binary_struct_survives_in_the_corpus` is
the gate that says the corpus has none left.

Beside them, `src/flab2bp/sfy/data/struct_schemas.json` holds the game's own
field list for every struct name the corpus carries — the shipped
`FactoryGame.usmap` mappings, which are the engine's reflection data. It is not
used to *decode* anything; it is the independent check that a tagged struct's
fields are the fields the game declares, with the types it declares
(`tests/sfy/test_struct_schemas.py`). Regenerate it with:

```bash
uv run python scripts/sfy_struct_names.py > /tmp/struct-names.txt
cd tools/sfy-extract
dotnet run -- "$HOME/Satisfactory" structs /tmp/struct-names.txt \
    ../../src/flab2bp/sfy/data/struct_schemas.json
```

## `FPlayerInfoHandle` — the `BuiltBy` property

**Source:** `Source/FactoryGame/Public/Online/PlayerInfoCache.h` lines 277-346,
from `CommunityResources/Headers.zip`. The serializer is inline, so the header
is the whole story — and it has to be, because both members are private and
carry no `UPROPERTY`: the usmap schema for `PlayerInfoHandle` has zero fields.

```cpp
struct FACTORYGAME_API FPlayerInfoHandle              // line 277
{
    uint8 ServiceProvider{0};                        // line 281
    int32 PlayerInfoTableIndex{INDEX_NONE};          // line 282

    friend FArchive& operator<<( FArchive& ar, FPlayerInfoHandle& handle )   // line 313
    {
        ar.UsingCustomVersion( FSaveCustomVersion::GUID );
        const int32 saveCustomVersion = ar.CustomVer( FSaveCustomVersion::GUID );
        if ( saveCustomVersion >= FSaveCustomVersion::NewPlayerInfoHandleSerializationFormat &&
             saveCustomVersion < FSaveCustomVersion::FixNewPlayerInfoHandleSerializationFormat )
        {                                            // lines 320-324
            ar << handle.ServiceProvider;
            ar << handle.PlayerInfoTableIndex;
        }
        if ( ar.CustomVer( FSaveCustomVersion::GUID ) >=
             FSaveCustomVersion::NewPlayerInfoHandleSerializationFormat )
        {                                            // lines 326-330
            ar << handle.ServiceProvider;
            ar << handle.PlayerInfoTableIndex;
        }
        else
        {                                            // lines 332-343
            ar << handle.ServiceProvider;
            uint8 LegacyPlayerInfoTableIndex{};
            ar << LegacyPlayerInfoTableIndex;
            handle.PlayerInfoTableIndex = LegacyPlayerInfoTableIndex;
            if ( handle.ServiceProvider == (uint8)UE::Online::EOnlineServices::Null )
            {
                handle.PlayerInfoTableIndex = INDEX_NONE;
            }
        }
        return ar;
    }
};
```

So three shapes, selected by the file's save custom version:

| save custom version | bytes | layout |
| --- | --- | --- |
| below 57 (`NewPlayerInfoHandleSerializationFormat`) | 2 | `uint8 ServiceProvider`, `uint8 LegacyPlayerInfoTableIndex` |
| exactly 57 | 10 | the 5-byte shape, twice — see below |
| 58 (`FixNewPlayerInfoHandleSerializationFormat`) or later | 5 | `uint8 ServiceProvider`, `int32 PlayerInfoTableIndex` |

At exactly 57 both `if` bodies run and the handle is written twice; that is the
mis-serialisation version 58 exists to fix. No fixture carries one, so
`PlayerInfoHandle` does not decode it — a 10-byte handle keeps its bytes.

The reader is handed the tag's `size`, not a version, so the size selects the
shape. `test_player_info_handle_shape_follows_the_save_custom_version` ties the
two together over the whole corpus: 1,304 handles at save version 53 in the
2-byte shape, 331 at 58 and 4,116 at 60 in the 5-byte shape — 5,751 in all, and
the split is exactly the version-57 boundary. One fixture carries the 2-byte
shape, 35 the 5-byte one, none both; the other 13 have no `BuiltBy` at all.

One asymmetry is deliberate. `PlayerInfoHandle.table_index` is the index *as the
wire carries it*, which is what makes the write byte-identical;
`player_info_table_index` is the value the game would load, with the legacy
rule from lines 338-342 applied (a `Null` provider means `INDEX_NONE`).

`EOnlineServices` itself is an engine enum and is in none of the shipped
headers, but `Null == 0` is pinned by this header twice — a default-constructed
handle has `ServiceProvider{0}` and is the invalid one (line 281), and the
legacy branch treats `ServiceProvider == Null` as invalid (line 339).

## `FInventoryItem` — inside every `InventoryStack`

**Sources:** `Source/FactoryGame/Public/FGInventoryComponent.h` lines 23-74 for
the members and their names; `FInventoryItem::Serialize` in
`FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.dll` for
the wire layout, because the header only declares it (`WithSerializer = true`,
lines 76-85). Read with `tools/sfy-native`:

```bash
cd tools/sfy-native
cargo run --release --quiet -- <dll> <pdb> disasm "FInventoryItem::Serialize" --out item.json
```

`FInventoryItem::Serialize` is at RVA `0x894c50`, 166 bytes from `.pdata`, 44
instructions. `rcx` is `this` (moved to `rdi`), `rdx` is the `FArchive&` (moved
to `rbx`); annotations are the tool's.

```
0x894c5a  mov rax,qword ptr [rdx]                          ; the archive's vtable
0x894c63  lea rdx,[12CC840h]      CONST c11de6f4ce9a027c61d5d7853d6a2fe4
0x894c6d  call qword ptr [rax+228h]                        ; ar.UsingCustomVersion(...)
0x894c76  lea rdx,[0F17818h]      CONST 2f3e0421d61fe613519d3b5130a23636
0x894c80  call qword ptr [rax+228h]                        ; ar.UsingCustomVersion(FSaveCustomVersion::GUID)
0x894c86  lea rdx,[12CC840h]      CONST c11de6f4ce9a027c61d5d7853d6a2fe4
0x894c90  call qword ptr [0EE6E38h]                        ; ar.CustomVer(...) -> eax
0x894c96  cmp eax,2
0x894c99  jl short 0000000000894CE9h                       ; below version 2: write nothing
0x894c9e  lea rdx,[rdi+8]         MEMBER FInventoryItem::ItemClass @8
0x894ca5  call qword ptr [rax+148h]                        ; ar << (UObject*&)ItemClass
0x894cab  lea rdx,[0F17818h]      CONST 2f3e0421d61fe613519d3b5130a23636
0x894cb5  call qword ptr [0EE6E38h]                        ; ar.CustomVer(FSaveCustomVersion::GUID)
0x894cbb  cmp eax,2Bh                                      ; 43 = RefactoredInventoryItemState
0x894cbe  jl short 0000000000894CD9h
0x894cc0  lea rcx,[rdi+10h]       MEMBER FInventoryItem::ItemState @16
0x894cc7  call 00000000007E7470h  CALL FFGDynamicStruct::Serialize
0x894ccc  mov al,1                                         ; return true
...
0x894cd9  mov rax,qword ptr [rbx]                          ; the pre-43 branch
0x894cdc  lea rdx,[rdi+20h]                                ; LegacyItemStateActor
0x894ce3  call qword ptr [rax+148h]                        ; ar << (UObject*&)LegacyItemStateActor
```

Two of those constants decide everything:

- `0xf17818` is the 16 bytes `2f 3e 04 21 d6 1f e6 13 51 9d 3b 51 30 a2 36 36`,
  which is `flab2bp.sfy.versions.SAVE_CUSTOM_VERSION_GUID` exactly — so the
  `CustomVer` at `0x894cb5` is the save custom version the header enum names,
  and `0x2b` is `RefactoredInventoryItemState` (43).
- `0x12cc840` is a second, unrelated custom version GUID whose value the branch
  at `0x894c96` compares against 2; below it the struct writes nothing.

`[rax+148h]` is the archive's `UObject*&` operator. In a *save* archive that is
an `FObjectReferenceDisc`: a level `FString` then a path `FString` — the same
pair `Reader.object_ref` already reads for an `ObjectProperty`.

The state flag comes from `FFGDynamicStruct::Serialize` at RVA `0x7e7470` (800
bytes, 237 instructions). Its saving path starts at `0x7e7709`:

```
0x7e7709  test al,4                                        ; ar.IsSaving()
0x7e7715  mov r8,qword ptr [rcx]  MEMBER FFGDynamicStruct::ScriptStruct @0
0x7e7718  test r8,r8
0x7e771e  setne r14b                                       ; hasStruct = ScriptStruct != nullptr
0x7e7743  mov r8d,4                                        ; four bytes
0x7e7749  setne sil
0x7e774d  mov dword ptr [rbp+38h],esi                      ; the flag, as an int32
0x7e7750  call qword ptr [rax+180h]                        ; FArchive::Serialize(&flag, 4)
0x7e7776  test r14b,r14b
0x7e7779  je 00000000007E78A5h                             ; no struct: that is the whole value
```

So the layout at save custom version 43 or later is:

| field | bytes |
| --- | --- |
| `ItemClass` | `FObjectReferenceDisc`: level `FString`, path `FString` |
| `ItemState` | `int32` flag, then the `FFGDynamicStruct` body when it is 1 |

which is exactly what the corpus shows: 24,270 items, of which 24,161 are the
12 zero bytes (an empty level, an empty path, a zero flag) and 109 carry a
descriptor path — the commonest being
`/Game/FactoryGame/Resource/Environment/Crystal/Desc_CrystalShard.Desc_CrystalShard_C`
in 42 of them and
`/Game/FactoryGame/Resource/Parts/Fuel/Desc_Fuel.Desc_Fuel_C` in 40.
Not one has the state flag set, so `InventoryItem.state` keeps whatever follows
a set flag as bytes rather than pretending to decode a `UScriptStruct`
reference no fixture exercises.

The pre-43 branch — an `FObjectReferenceDisc` for `LegacyItemStateActor` in
place of the dynamic struct — is not decoded either: the oldest fixture is save
version 46, and such a value would keep its bytes as a `BinaryStruct`.

## `AFGBuildableWire` — the `Build_PowerLine_C` class trailer

**Sources:** `Source/FactoryGame/Public/Buildables/FGBuildableWire.h` from
`CommunityResources/Headers.zip` for the members and their names;
`AFGBuildableWire::Serialize` in
`FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.dll` for
the wire layout, because the header only declares the override (line 39,
`virtual void Serialize( FArchive& ar ) override;`). Read with
`tools/sfy-native`:

```bash
cd tools/sfy-native
cargo run --release --quiet -- <dll> <pdb> disasm "AFGBuildableWire::Serialize" --out wire.json
```

### What the header says to expect

```cpp
class FACTORYGAME_API AFGBuildableWire : public AFGBuildable   // line 32
{
    virtual void Serialize( FArchive& ar ) override;           // line 39

    UPROPERTY( ReplicatedUsing = OnRep_Connections )           // line 150
    TWeakObjectPtr< class UFGCircuitConnectionComponent > mConnections[ 2 ];

    UPROPERTY( Replicated )                                    // line 153
    FVector mConnectionLocations[ 2 ];

    UPROPERTY( SaveGame )                                      // line 156
    TArray< FWireInstance > mWireInstances;

    UPROPERTY( SaveGame )                                      // line 159
    float mCachedLength;
};
```

That split is the whole reason the trailer exists. `mWireInstances` and
`mCachedLength` are `SaveGame`, so they go in the tagged property list like any
other saved member — and every one of the corpus's 508 wires carries exactly
those two names in its property list. `mConnections` is `Replicated` and *not*
`SaveGame`, so no tag ever names it; the class's own `Serialize` has to write
it, and that is what lands after the property list.

### What the disassembly says is written

`AFGBuildableWire::Serialize` is at RVA `0x586900`, 420 bytes from
`.pdata-chained` in four chunks, 94 instructions. (The 42-byte function at
`0x216700` is the `FStructuredArchive` thunk: it calls
`FSlotBase::GetUnderlyingArchive` and tail-calls `0x586900`.) `rcx` is `this`
(moved to `rdi`), `rdx` is the `FArchive&` (moved to `rbx`); annotations are the
tool's, except the `;` comments.

```
0x58690d  call 00000000004B7300h  CALL AFGBuildable::Serialize  ; the property list and the actor's int32 0
0x586912  test byte ptr [rbx+2Bh],20h                           ; ar.IsSaveGame()
0x586916  je 0000000000586A9Dh                                  ; not a save archive: nothing more
0x586921  lea rcx,[rdi+630h]                                    ; mConnections[0]
0x586928  call qword ptr [0EE8A78h]  IMP ?Get@FWeakObjectPtr@@QEBAPEAVUObject@@XZ
0x586933  lea rcx,[rdi+638h]                                    ; mConnections[1]
0x586942  call qword ptr [0EE8A78h]  IMP ?Get@FWeakObjectPtr@@QEBAPEAVUObject@@XZ
0x58695b  call qword ptr [rax+160h]                             ; ar << (UObject*&)mConnections[0]
0x58696c  call qword ptr [rax+160h]                             ; ar << (UObject*&)mConnections[1]
0x5869a8  call qword ptr [0EE8A88h]  IMP ??4FWeakObjectPtr@@QEAAXUFObjectPtr@@@Z   ; mConnections[0] =
0x5869e6  call qword ptr [0EE8A88h]  IMP ??4FWeakObjectPtr@@QEAAXUFObjectPtr@@@Z   ; mConnections[1] =
0x5869ec  test byte ptr [rbx+28h],1                             ; ar.IsLoading()
0x5869f5  je 0000000000586A9Dh                                  ; saving: that is the whole trailer
0x5869fb  lea rdx,[0F17818h]      CONST 2f3e0421d61fe613519d3b5130a23636
0x586a05  call qword ptr [0EE6E38h]  IMP ?CustomVer@FArchiveState@@QEBAHAEBUFGuid@@@Z
0x586a0b  cmp eax,21h                                           ; 33 = AddedCachedLocationsForWire
0x586a0e  jl short 0000000000586A33h
0x586a10  cmp eax,28h                                           ; 40 = MultipleWireMeshRefactor
0x586a13  jge 0000000000586A9Dh
0x586a21  call 000000000055CDA0h  CALL ??6Math@UE@@YAAEAVFArchive@@AEAV2@AEAU?$TVector@N@01@@Z
0x586a2e  call 000000000055CDA0h  CALL ??6Math@UE@@YAAEAVFArchive@@AEAV2@AEAU?$TVector@N@01@@Z
0x586a65  call 0000000000568BC0h  CALL AFGBuildableWire::DestroyWireInstances
0x586a98  call 0000000000567730h  CALL AFGBuildableWire::CreateWireInstancesBetweenConnections
```

So the writer's whole contribution is two object references, and everything
after `0x5869ec` is the load side rebuilding the wire meshes.

Four things in that listing had to be pinned down before it could be read, and
each was pinned down from game data rather than assumed:

- **`[ar+0x2B] & 0x20` is `ArIsSaveGame`.** Both save archives set exactly that
  bit in their constructors — `FObjectWriterFName::FObjectWriterFName` at RVA
  `0xb39280` does `or byte ptr [r14+2Bh],20h` at `0xb3931a`, and
  `FObjectReaderFName::FObjectReaderFName` at `0xb39000` does the same at
  `0xb39097` — while calling the imported `SetIsSaving` / `SetIsLoading` for
  those two flags separately, so the bit is neither of them. Its name comes
  from `FProperty::ShouldSerializeValue` in
  `Engine/Binaries/Win64/FactoryGameEGS-CoreUObject-Win64-Shipping.dll` at RVA
  `0x27b670`, which is the engine's
  `if( !(PropertyFlags & CPF_SaveGame) && Ar.IsSaveGame() ) return false;`:
  `0x27b697` loads `PropertyFlags`, `0x27b69e`-`0x27b6a4` tests bit 24
  (`CPF_SaveGame`) inverted, and `0x27b6a8` is `test byte ptr [rbx+2Bh],20h`.
  Blueprint object bodies are written with `FObjectWriterFName`, so this branch
  is always taken and the two references are always present.
- **`[ar+0x28] & 0x01` is `ArIsLoading`.** `FArchiveState::SetIsLoading` in
  `Engine/Binaries/Win64/FactoryGameEGS-Core-Win64-Shipping.dll` at RVA
  `0x314d50` is twelve bytes and writes precisely that bit:
  `movzx eax,byte ptr [rcx+28h]; and al,0FEh; or al,dl; mov byte ptr [rcx+28h],al`.
- **`[archive vtable + 0x160]` is the save archive's `operator<<(UObject*&)`.**
  `FObjectWriterFName`'s constructor installs its final vtable at `0x13333C8`
  (`lea rax,[13333C8h]` at `0xb3931f`), whose `+0x160` slot holds
  `0x180b3b2b0`, inside the FactoryGame module rather than the engine's
  `FArchive`. That override builds an `FObjectReferenceDisc` — it calls
  `FObjectReferenceDisc::Set` (RVA `0x8db300`) with the `UObject*`, then the
  free `operator<<(FArchive&, FObjectReferenceDisc&)` at RVA `0x8b4260`, which
  is six instructions: `ar << r.LevelName` (the struct at `+0x00`) then
  `ar << r.PathName` (at `+0x10`, one `FString` further on), both through the
  imported `??6@YAAEAVFArchive@@AEAV0@AEAVFString@@@Z`. A level `FString` then a
  path `FString` — the same pair `Reader.object_ref` already reads.
- **The `CustomVer` GUID at `0xf17818`** is the 16 bytes
  `2f 3e 04 21 d6 1f e6 13 51 9d 3b 51 30 a2 36 36`, which is
  `flab2bp.sfy.versions.SAVE_CUSTOM_VERSION_GUID` exactly, so `0x21` and `0x28`
  are `AddedCachedLocationsForWire` (33) and `MultipleWireMeshRefactor` (40) —
  the two enum members that bracket the life of the old cached locations, named
  for exactly this.

`AFGBuildable::Serialize` itself (RVA `0x4b7300`, 58 bytes) adds nothing to the
bytes: it registers one custom version and tail-calls `AActor::Serialize`. The
`int32 0` that opens the trailer is therefore the same four bytes
`UObject::Serialize` writes for every other actor — the ones `BuildableTrailer`
models — and not something the wire contributes.

### The layout

| field | bytes |
| --- | --- |
| (the base actor's) | `int32 0` |
| `mConnections[0]` | `FObjectReferenceDisc`: level `FString`, path `FString` |
| `mConnections[1]` | `FObjectReferenceDisc`: level `FString`, path `FString` |

`flab2bp.sfy.trailers.PowerLineTrailer` is that, and
`trailer_for_new("Build_PowerLine_C", ACTOR, connections)` authors one.
Because the layout is now known in full, the power line is decoded strictly:
bytes that are not this raise `ArchiveError` instead of surviving as opaque
bytes.

### What the corpus shows

All 508 wires in the 49 fixtures decode into the two references and re-encode
byte for byte (`tests/sfy/test_trailers.py`), across save versions 46 (67
wires), 52 (131) and 60 (310). Every reference names `Persistent_Level`, and
the connection components they name are `PowerConnection` (848), `PowerInput`
(158), `FGPowerConnection` (5), `PowerConnection2` (4) and `PowerConnection1`
(1) — all `UFGCircuitConnectionComponent` subobjects of the buildable at that
end, which is what the member's declared type says they must be. Not one wire
has bytes left over.

The legacy branch — two `FVector` in place of nothing, at save custom version
33 to 39 — is not decoded, and does not need to be twice over: it is guarded by
`ar.IsLoading()`, so no writer has ever emitted it (the values are read into
stack slots at `[rsp+20h]` and `[rsp+38h]` and then never used, which is what
reading a field the game has since dropped looks like), and the oldest fixture
is save version 46. A blueprint saved by a build in that range would raise
`ArchiveError` naming those bytes rather than being silently re-encoded wrong.
