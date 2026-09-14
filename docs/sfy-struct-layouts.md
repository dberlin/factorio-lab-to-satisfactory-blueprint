# Struct layouts from the game

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
legacy branch treats `ServiceProvider == Null` as invalid (line 339). The corpus
agrees: every one of the 5,751 handles carries provider `6`, which counting from
`Null = 0` is `Steam`, and only two index values ever appear (0 in 5,634 of
them, 1 in 117).

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
