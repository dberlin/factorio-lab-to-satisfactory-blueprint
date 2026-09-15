# sfy-native — the hologram limits, read out of the shipped binary

Seven of the limits the placer needs are C++ constructor immediates. They are in
no cooked asset (Task 10 swept all 48573 cooked files and found five unrelated
hits), in no header (they have no in-class initialiser) and in no Docs.json
entry (which dumps buildable class defaults, and holograms are not buildables).
The only place left to read them is the machine code of the shipped module DLL.

This tool takes the member offsets from the PDB's type stream, finds each
class's member functions among the PDB's public symbols, disassembles them and
traces the constant stores through `this`. Its output is
`src/flab2bp/sfy/data/native.json`, which `scripts/sfy_registry.py` merges into
the registry with the source tag `binary`.

## Prerequisites

- `cargo` (built and tested against 1.98).
- A Satisfactory install with the shipped module DLL **and its PDB** beside it:
  `~/Satisfactory/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.{dll,pdb}`.
  The launcher's own `FactoryGameEGS-Win64-Shipping.exe` is not the right file:
  the `FactoryGame` module's code lives in that DLL. The tool reads the DLL's
  CodeView debug record and refuses any PDB whose GUID and age do not match it.

## Running it

```
cargo build --release
./target/release/sfy-native \
  ~/Satisfactory/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.dll \
  ~/Satisfactory/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.pdb \
  ../../src/flab2bp/sfy/data/native.json \
  --expect AFGConveyorBeltHologram.mMaxSplineLength=5600.1 \
  --expect AFGPipelineHologram.mBendRadius2D=199 \
  --expect AFGPipelineHologram.mMinBendRadius=75 \
  --expect AFGPipelineHologram.mMaxSplineLength=5600.1 \
  --expect AFGBuildableWire.mMaxLength=10000
```

Then `uv run python scripts/sfy_registry.py` to fold the result into
`registry.json`. **Re-run both after every game update**; `native.json` is
committed so nobody needs a game install to build the project.

Other options:

- `--class Name` restricts the run to one class (with its default member list,
  if it has one). `--class Name:member,member` names the members outright.
- `--dump Class` or `--dump Class::Function` prints the disassembly the tracer
  saw. This is the tool for working out why a member came back `null`.

## Reading a function: the `disasm` mode

The constants above are the easy half of what the binary knows. The rules —
what a hologram accepts as a legal placement, what a struct's `Serialize`
writes — are *code*, and reading them means reading the disassembly. `disasm`
prints any function the PDB names, annotated with everything the tool can work
out about each instruction:

```
./target/release/sfy-native game.dll game.pdb \
  disasm AFGConveyorBeltHologram::ValidateCurvature --out vc.json
```

The argument is a case-sensitive substring of a `Class::Method` name, **or** a
hexadecimal RVA. The second form is the only way to reach a function the PDB
publishes under a mangling with no `Class::Method` form — a free function, an
operator, or a templated member such as `UE::Math::TTransform<double>`'s — since
no demangler is in the crate graph. An RVA the PDB publishes *nothing* at is
still read: `.pdata` bounds it just the same, and the output names it by the
address it was asked for rather than by a guess at what it is. That is how
`AFGBuildableConveyorLift::SetupConnections`' transform helper at `0x4f9850` was
read for the `lift.connectors` rule.

```json
[{"symbol": "AFGConveyorBeltHologram::ValidateCurvature",
  "mangled": "?ValidateCurvature@AFGConveyorBeltHologram@@AEAA_NXZ",
  "rva": "0xaa5280", "size": 722, "size_source": "pdata",
  "chunks": [{"rva": "0xaa5280", "size": 722}], "this": "rcx",
  "instructions": [
    {"rva": "0xaa52d0", "bytes": "ff15f2641400",
     "text": "call qword ptr [0EEB7C8h]",
     "constant": {"at": "0xeeb7c8", "...": "the IAT slot's bytes"},
     "import": "?GetSplineLength@USplineComponent@@QEBAMXZ"},
    {"rva": "0xaa52dd", "bytes": "f30f5915873b8100",
     "text": "mulss xmm2,dword ptr [12B8E6Ch]",
     "constant": {"at": "0x12b8e6c", "f32": 0.02, "f64": 7.105428962728437e-15,
                  "i32": 1017370378, "bytes": "0ad7a33c0000003dcdcc4c3dcdcccc3d"}},
    {"rva": "0xaa54cd", "bytes": "e842b34100", "text": "call 0000000000EC0814h",
     "call": "acos"},
    {"rva": "0xaa54d2", "bytes": "f30f109748080000",
     "text": "movss xmm2,dword ptr [rdi+848h]",
     "member": {"class": "AFGConveyorBeltHologram", "name": "mBendRadius", "offset": 2120}}]}]
```

**Which functions.** The substring is matched **case-sensitively** against the
`Class::Method` name, and every match is disassembled — `FInventoryItem::`
gives all fourteen of that struct's functions, `AFGConveyorBeltHologram::ValidateCurvature`
gives one. There is no demangler in this tool's crate graph, so that name is
derived from the MSVC mangling's leading `?Method@Class@@` (and `??0Class@@`
for a constructor) by the same `member_function` the `extract` mode uses. The
consequence is that a symbol whose mangling has no plain `Class::Method` form —
a free function, an operator, a destructor, a templated or nested scope — has
no name to match against and can never be selected. Public symbols and, where
a PDB carries them in the global stream, procedure symbols both count; one
address is reported once, however many symbols the linker folded onto it —
identical-COMDAT folding gives several functions one body, and the surviving
name is the one reported, so the members are annotated against *that* class.

**Where a function ends.** Three sources, tried in that order, and
`size_source` always says which one answered. **Both modes use them**: the
constructor tracer behind `native.json` is bounded exactly as `disasm` is, and
every record in `native.json` that names a function carries the `size_source`
that bounded it. (It did not always. Until fix round 3 `extract` stopped at a
function's first `ret` even inside a `.pdata` range, so a store MSVC emitted
after an early return, or in a chained chunk, was invisible to it —
`AFGConveyorLiftHologram::UpdateTopTransform`'s two stores were.)

1. **`.pdata`** — authoritative. The `RUNTIME_FUNCTION` entry covering the RVA
   gives `[begin, end)` and `size_source` is `pdata` (or `pdata-chained`, see
   below).
2. **The PDB's procedure record** (`pdb-procedure-length`). MSVC emits no
   `.pdata` entry for a leaf function, and cutting such a function at its first
   `ret` is wrong whenever it has more than one: `AFGBuildableHologram::
   GetRotationStep` is 77 bytes with four `return` statements, of which the
   first `ret` at `0xa7c07a` leaves three unread. Every `S_GPROC32` /
   `S_LPROC32` record carries the function's byte length, which is game data of
   exactly the kind the member offsets are, so the tool takes `[rva, rva + len)`
   from it. Procedure records live in the per-module symbol streams (the global
   stream publishes mostly `S_PUB32`, which has no length), so both streams are
   walked once when the symbol list is asked for — about 1.7 s on this module.
3. **The first `ret`** (`ret`) — only when neither of those knows the function.

A range the section or the 64 KiB cap cuts short says `truncated` whichever
source it came from, rather than passing for the function it promised; for
those `size` is the byte range asked for and can exceed what actually decoded.

**Functions MSVC split.** The linker hands several `.pdata` entries to one
function, and only the first is the function: every other chunk sets
`UNW_FLAG_CHAININFO` (bit 2 of the flags in `UNWIND_INFO`'s first byte) and
stores the parent's `RUNTIME_FUNCTION` after its unwind codes, padded to an
even count. The tool resolves that chain transitively once per module and
groups the entries by the primary they land on, so a split function comes back
whole: `size_source` is `pdata-chained`, `chunks` lists every piece sorted by
RVA, `size` is their sum and `instructions` covers all of them in address
order. `.pdata` order does not matter — a chunk can and does sort before the
entry it belongs to. `AFGConveyorBeltHologram::ValidateConveyorBelt` is three
chunks (27 + 370 + 668 = 1065 bytes) whose entry alone gives 27;
`AFGPipelineHologram::ValidatePipeline` is five. A function that was not split
has one `chunks` entry — itself — and `size_source` stays `pdata`, byte for
byte what it produced before chaining existed.

The `this` tracker is *one* tracker stepped across the chunks in RVA order, not
restarted per chunk. MSVC allocates registers over the whole function, so the
callee-saved register holding `this` in the entry chunk is the same register in
the others; the tracker is already linear rather than flow-sensitive within a
chunk, and this is the same approximation across one.

Chaining cannot help a function with **no** `.pdata` entry at all, because
there is no chain to follow. `AFGBuildableHologram::GetRotationStep` at
`0xa7c050` is one — the neighbouring entries are `0xa7bfc0..0xa7c04a` and
`0xa7c0d0..0xa7c140` and neither covers it. That is what the PDB's procedure
length is for, and the two together mean every function this tool reports is
bounded by game data rather than by a `ret` it guessed at.

**Where `this` is.** The `this` field says which register the annotator seeded,
and it comes from the mangling's access code:

- A **static** member function (access `C`, `D`, `K`, `L`, `S`, `T`) has no
  `this` at all: `"this": null`, and **no** `member` annotation is emitted.
  `rcx` there is an ordinary argument — in a UE `exec` thunk it is a `UObject*`
  of a different class entirely, and annotating it would be fiction. Seven of
  `AFGConveyorBeltHologram`'s 54 published functions are static.
- Everything else has `"this": "rcx"`, **including** a function that returns an
  object by value. MSVC's x64 convention passes `this` first and the caller's
  hidden return slot *second*, so an sret function has `this` in `rcx` and the
  slot in `rdx`: `AFGConveyorBeltHologram::GetAnyConnectedBuildables` reads
  `[rcx+800h]` (`mSnappedConnectionComponents`) while building the returned
  `TArray` through `[rdx]`, and `FInventoryItem::GetItemClass` reads `[rcx+8]`
  and stores it to `[rdx]`. `rdx` is never seeded, so those return-slot writes
  are left unannotated instead of being labelled with the wrong object's
  members.

**The four annotations.** Each is present only when the tool is sure of it:

- `member` — the instruction's operand is `[reg+disp]`, `reg` is an alias of
  `this`, and `disp` falls on a member of the function's class or one of its
  bases, per the PDB type stream. *Every* base is walked, not just the primary
  one, and an inherited member's offset is its base's offset plus its own: a UE
  actor multiply-inherits its interfaces, and a secondary base sits at a
  non-zero offset in the derived object, so taking its members at face value
  would read them as members of whatever the primary chain has there. The
  `this` tracking is the constructor
  tracer's: `this` arrives in `rcx`, `mov reg, alias` carries it on, and an
  alias dies the moment its register is written otherwise or a call clobbers
  it — so a `[rcx+X]` *after* a call is left unannotated rather than guessed
  at. A displacement inside a member (a field of an embedded struct) reports
  the member it lands in, with that member's own offset.
- `constant` — the operand is `[rip+K]` pointing into `.rdata`. The tool does
  not know the type, so it reports all of them: `f32`, `f64`, `i32` and the raw
  16 bytes. `.data` and `.bss` are excluded: a mutable global is not a
  constant. Note that unlike the store tracer, *every* rip-relative operand is
  reported, not only moves — `mulss xmm2,[12B8E6Ch]` naming its multiplier is
  the whole point here.
- `call` — a `call rel32` whose target is a known symbol, named the same way
  (`Class::Method`, else the mangling).
- `import` — a `call` or `jmp qword ptr [rip+K]` where `K` is an import address
  table slot, named from the PE import directory as the *exporting* module
  mangles it: `?GetSplineLength@USplineComponent@@QEBAMXZ`. Most of what a
  hologram validator calls lives in the Engine or CoreUObject DLL and reaches
  it this way, so without this an interpretation naming a callee could not be
  reproduced from the tool's own output. The `constant` annotation for the slot
  is still emitted alongside it — its `f32` is a pointer's bytes and means
  nothing, the name is the useful half — and a plain `mov` that merely loads a
  slot is *not* annotated, because loading a pointer is not calling it.

Output is deterministic: functions sorted by RVA, instructions in address
order, fixed key order, so two runs give byte-identical files.

## Reading a global: the `data` mode

The `constant` annotation above trusts `.rdata` and nothing else, because a
writable global is not a constant: quoting one as if it were would put a number
in a rule that the running game may have moved on from. Some of what a validator
reads *is* a writable global, though —
`AFGBuildableConveyorLift::FitClearance` takes its clearance box's half-extent
from one — and "the tool will not quote it" left the value to be read by hand,
which is not a source. `data` is the way to read one and say what it is:

```
./target/release/sfy-native game.dll game.pdb \
  data AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D --bytes 16 --out extent.json
```

```
AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D @ 0x19b8118 in .data (16 bytes): 00000000000059400000000000005940 = 100, 100
```

```json
{"symbol": "AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D",
 "mangled": "AFGBuildableConveyorLift::CLEARANCE_EXTENT_2D",
 "rva": "0x19b8118", "section": ".data", "bytes": 16,
 "hex": "00000000000059400000000000005940", "doubles": [100.0, 100.0]}
```

The argument is a case-sensitive substring of the symbol's name, and it has to
match **exactly one** of them — several matches are a refusal listing them, as
is none. The symbols it matches are the PDB's *data* symbols, never a function's:
a `Public` record with `function` clear, which is mangled and gets the same
`Class::Method`-style name derivation `disasm` uses, or an
`S_GDATA32`/`S_LDATA32` `Data` record, whose name the compiler already wrote out
as `Class::Name`. Both spellings are matched, because which kind a given static
is published as is the PDB's business and not the caller's; `CLEARANCE_EXTENT_2D`
is the second kind here, which is why its `mangled` above is readable. This mode
decodes no instructions at all: it resolves a symbol, reads the section table
and reads the file.

Where the RVA lands decides whether it is read: `.data` and `.rdata` are the
initialised sections and are the only two accepted, and anything else — `.text`,
a resource section, an address in no section, or a read that runs past what the
file holds for the section — is refused **with the section it did land in**. The
`--out` file is optional; the line above is printed either way, and `--bytes` is
not.

**What such a read is, and is not.** It is the initialiser: what the image holds
*before the game runs*. `FitClearance` reads the global at hologram time, so if
anything in the running game ever wrote to it, this reading would be stale, and
nothing the tool can see proves nothing does. Every caller has to carry that
caveat with the number — `scripts/sfy_native_rules.py` writes it into the
`lift.clearance` rule's interpretation, once, beside the bytes.

## The oracle

Four members' values are stated in the public headers, and a fifth is in
Docs.json. The tool reproduces all five from the machine code alone, and
`--expect` makes a disagreement fail the run **before** anything is written, so
a broken extraction can never replace the committed `native.json`:

| Member | Stated | Where the game states it |
| --- | --- | --- |
| `AFGConveyorBeltHologram::mMaxSplineLength` | 5600.1 | `Hologram/FGConveyorBeltHologram.h:174` |
| `AFGPipelineHologram::mMaxSplineLength` | 5600.1 | `Hologram/FGPipelineHologram.h:206` |
| `AFGPipelineHologram::mBendRadius2D` | 199.0 | `Hologram/FGPipelineHologram.h:198` |
| `AFGPipelineHologram::mMinBendRadius` | 75.0 | `Hologram/FGPipelineHologram.h:202` |
| `AFGBuildableWire::mMaxLength` | 10000.0 | Docs.json, via `assets.json` |

`scripts/sfy_registry.py` holds the same four header values in
`HEADER_DEFAULTS` and refuses to merge a `native.json` that contradicts them, so
the oracle is checked on both sides of the hand-off.

## What it found

Offsets are decimal, from the PDB's type stream; RVAs are into the DLL.

| Registry key | Class | Member | Offset | Value | Set in | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `belt_bend_radius_cm` | `AFGConveyorBeltHologram` | `mBendRadius` | 2120 | **199.0** | constructor | `mov dword ptr [rbx+848h],43470000h @ 0xa5f940` |
| `belt_max_incline_deg` | `AFGConveyorBeltHologram` | `mMaxIncline` | 2128 | **35.0** | constructor | `mov dword ptr [rbx+850h],420C0000h @ 0xa5f954` |
| `belt_max_spline_cm` | `AFGConveyorBeltHologram` | `mMaxSplineLength` | 2124 | 5600.1 | constructor | `mov dword ptr [rbx+84Ch],45AF00CDh @ 0x25a0af` |
| `lift_step_cm` | `AFGConveyorLiftHologram` | `mStepHeight` | 2272 | **100.0** | `BeginPlay` | `mov dword ptr [rbx+8E0h],42C80000h @ 0xa64f10` |
| `lift_min_cm` | `AFGConveyorLiftHologram` | `mMinimumHeight` | 2276 | *computed* | `BeginPlay` | `movss dword ptr [rbx+8E4h],xmm0 @ 0xa64f39` |
| `lift_max_cm` | `AFGConveyorLiftHologram` | `mMaximumHeight` | 2280 | *computed* | `BeginPlay` | `movss dword ptr [rbx+8E8h],xmm1 @ 0xa64f31` |
| `lift_min_vertical_cm` | `AFGConveyorLiftHologram` | `mMinimumHeightWithVerticalConnection` | 2284 | *computed* | `BeginPlay` | `movss dword ptr [rbx+8ECh],xmm0 @ 0xa64f1a` |
| `hologram_grid_cm` | `AFGBuildableHologram` | `mGridSnapSize` | 1260 | **100.0** | constructor | `mov dword ptr [rbx+4ECh],42C80000h @ 0xa5f2b0` |
| `pipe_bend_radius_cm` | `AFGPipelineHologram` | `mBendRadius` | 2048 | 199.0 | constructor | `mov dword ptr [rbx+800h],43470000h @ 0xaac409` |
| `pipe_bend_radius_2d_cm` | `AFGPipelineHologram` | `mBendRadius2D` | 2052 | 199.0 | constructor | `mov dword ptr [rbx+804h],43470000h @ 0x3551b6` |
| `pipe_min_bend_radius_cm` | `AFGPipelineHologram` | `mMinBendRadius` | 2056 | 75.0 | constructor | `mov dword ptr [rbx+808h],42960000h @ 0x3551c0` |
| `pipe_max_spline_cm` | `AFGPipelineHologram` | `mMaxSplineLength` | 2060 | 5600.1 | constructor | `mov dword ptr [rbx+80Ch],45AF00CDh @ 0x3551ca` |
| `Port.max_connections` | `UFGCircuitConnectionComponent` | `mMaxNumConnectionLinks` | 640 | **1** | constructor | `mov dword ptr [rbx+280h],1 @ 0x6f41c1` |
| `potential_shard_slots_default` | `AFGBuildableSubsystem` | `mDefaultPotentialShardSlots` | 848 | **3** | constructor | `mov dword ptr [rdi+350h],3 @ 0x667003` |
| `production_boost_slots_default` | `AFGBuildableSubsystem` | `mDefaultProductionShardSlotSize` | 852 | 4 | constructor | `mov dword ptr [rdi+354h],4 @ 0x66700d` |

The **bold** rows are the ones no other source has. The last three are not
hologram members, and they are here for the same reason the rest are: they are
archetype defaults the cooked assets omit. A power connection's
`mMaxNumConnectionLinks` is serialised only where a Blueprint overrides it, so
every machine's power input would otherwise ship with an unknown wire count;
`UFGPowerConnectionComponent`'s own constructor (0x8f1160, `.pdata`-bounded)
writes nothing at offset 640, so the circuit connection's **1** is what a machine
runs on. The two subsystem defaults are what `AFGBuildableFactory::BeginPlay`
copies onto a buildable whose `mOverride*` bit is clear (0x4d40eb, 0x4d40fc);
the cooked `BP_BuildableSubsystem_C` overrides the production one to 1, which
`tools/sfy-extract` reads and the merge prefers, so only the **3** survives into
`registry.json` as `binary`. `pipe_bend_radius_cm` is
listed because `Holo_Pipeline_C` overrides the native 199 down to 100 and the
asset wins; the binary is its fallback.

### The three lift heights the binary does not state either

`AFGConveyorLiftHologram::BeginPlay` at `0xa64e70` works all three out from the
lift buildable's own mesh height, so there is no constant to read:

```
0x00a64eed  call 0000000000A5D960h            ; -> the lift buildable
0x00a64ef5  movss xmm1,dword ptr [rax+760h]   ; H = its mesh height
0x00a64f00  movss dword ptr [rbx+8F0h],xmm1   ; mMeshHeight                = H
0x00a64f08  subss xmm0,dword ptr [12B8F00h]   ;   [12B8F00h] = 50.0f
0x00a64f10  mov dword ptr [rbx+8E0h],42C80000h; mStepHeight                = 100.0f
0x00a64f1a  movss dword ptr [rbx+8ECh],xmm0   ; mMinimumHeightWith...      = H - 50
0x00a64f25  addss xmm0,xmm1                   ;
0x00a64f29  mulss xmm1,dword ptr [1329DE8h]   ;   [1329DE8h] = 24.0f
0x00a64f31  movss dword ptr [rbx+8E8h],xmm1   ; mMaximumHeight             = H * 24
0x00a64f39  movss dword ptr [rbx+8E4h],xmm0   ; mMinimumHeight             = H * 2
```

`H` is a per-lift-mark property the tool has no way to evaluate, so the three
stay `"value": null` with that reason. The *formula* is game data all the same,
so `scripts/sfy_registry.py` applies it to Docs.json's `mMeshHeight` — 200 cm on
all six lift marks — and tags the results `"binary-derived"`: `lift_min_cm` 400,
`lift_max_cm` 4800, `lift_min_vertical_cm` 150. `lift_step_cm` is the one
constant in the block.

### The grid snap size

`AFGBuildableHologram::mGridSnapSize` is a `UPROPERTY( EditDefaultsOnly )`
(`Hologram/FGBuildableHologram.h:445`, the `float mGridSnapSize;` declaration
itself — the `UPROPERTY` macro is the line above it, the doc comment two above),
so a hologram Blueprint can override it,
and three do: `assets.json` has `Holo_PowerPole_C`, `Holo_PowerTower_C` and
`Holo_StreetLight_C` at 50, which the merge puts on each buildable as
`grid_snap_cm`. The native constructor default — the value every
other hologram uses — is **100**, which is what the registry carries. So the
grid is 100 cm, and the only 50s in the game's data are the three pole and
street-light holograms' own overrides, which travel with the buildables that
carry them.

## The codegen patterns the store tracer handles

`this` arrives in `rcx`; MSVC copies it into a callee-saved register before
calling the base constructor, so the tracer keeps a set of `this` aliases that
grows on `mov reg, alias` and shrinks whenever a register is written otherwise
or clobbered by a call. Alongside it, the constant last loaded into each
register:

| Pattern | Example |
| --- | --- |
| Immediate store | `mov dword ptr [rbx+848h],43470000h` |
| Constant-pool load and store | `movss xmm0,[rip+K]` … `movss [rbx+848h],xmm0` |
| Zeroed float | `xorps xmm1,xmm1` … `movss [rcx+10h],xmm1` |
| Zeroed integer | `xor eax,eax` … `mov [rbx+8E0h],rax` |
| Merged 16-byte store | `movups xmm2,[rip+K]` … `movups [rcx+20h],xmm2` (four floats at once) |
| Merged 8-byte store | `mov rax,imm64` … `mov [rcx+30h],rax` (two floats at once) |
| `this` copied to a saved register | `mov rbx,rcx`, `mov rdi,rcx` |

Three things it deliberately refuses to treat as a constant:

- **A rip-relative operand that is not a move.** `mulss xmm1,[rip+K]` reads the
  same kind of operand as `movss` but leaves a product behind, not the constant.
  Missing this reported the lift's maximum height as 24.
- **Any store it cannot prove constant.** These are recorded too, and they veto
  the member: a constructor's zero that `BeginPlay` overwrites with a computed
  value is not the number the game runs on. `native.json` names the overwriting
  instruction as the reason.
- **A value put straight back where it came from.** MSVC emits
  `movss xmm11,[rcx+8ECh]` … `movss [rcx+8ECh],xmm11` around a callee-saved
  spill; counting that as a write would veto a member nothing overwrites.

Two more store shapes write the object without naming an offset off an alias,
and both are vetoes rather than silences:

- **An indexed store off a `this` alias** — `mov [rdi+rax*8+860h],rsi`, which is
  `AFGConveyorLiftHologram::SetHologramLocationAndRotation` writing an array
  element. The index is a run-time value, so *which* member it lands on is not
  in the instruction; every constant the function has recorded could be the one
  it overwrote, so all of them are vetoed. The same shape off a register that is
  not a `this` alias is ignored: it says nothing about this object.
- **A store through a pointer `lea`'d off an alias** —
  `lea rbx,[r14+810h]` … `mov [rbx-10h],rsi`, which is
  `AFGConveyorBeltHologram::ConfigureComponents` filling
  `mSnappedConnectionComponents` at 0x800. `rbx` is not an alias (nothing is
  read through it), so the store used to be invisible. The `lea`'s offset and
  the store's signed displacement place it exactly; a `lea` with a run-time
  index in it cannot be placed and vetoes every recorded constant instead.

The conservative direction is always `null` with a reason: the tool never
guesses a value, and the tests hold it to the oracle.

### What the tracer cannot see, and what catches it

The tracer walks a function's chunks **in RVA order as a straight line**. It is
not a walk of the control-flow graph, and that costs four things. None of them
is a bug to be fixed by reading the disassembly harder; each is a limit of what
a linear trace can state.

1. **The last store on any branch wins.** Two arms of an `if` that store
   different constants at one offset are read as the second one overwriting the
   first, and a store inside a branch that never runs for a class default is
   read as if it always runs. Where two *functions* disagree the tool says so
   (`is given two different constants`); inside one function it does not.
2. **A chained chunk's entry state is assumed to fall through.** The alias set
   and the loaded constants at the top of a chunk MSVC split off are whatever
   the previous chunk in RVA order left behind, which is right for a function
   laid out in one straight line and a guess for a chunk jumped to from
   elsewhere. A stale alias here could attribute a store to the wrong object.
3. **An indexed store's target is a run-time value.** The veto above is over the
   constants the function itself recorded; a constant some *other* function of
   the chain recorded is not vetoed, because this function never tracked those
   bytes. `mov [rdi+rax*8+860h],rsi` could in principle reach `mStepHeight` at
   0x8E0, and nothing here would say so.
4. **A read-modify-write is not a move**, so `add dword ptr [rbx+8],1` and its
   kin are neither a value nor a veto.

What catches a misattribution is the **oracle**: `--expect Class.member=value`
compares what was traced against the value the public headers or Docs.json state
for the same member, and a disagreement fails the run *before* anything is
written. The committed regeneration command in
`docs/sfy-regenerating-game-data.md` carries five of them, and they are what
stands between a straight-line trace and a wrong number in the registry. Keep
them on every run; add one whenever a member's value is stated anywhere outside
the binary.

One limit worth knowing: a member is resolved against its own class chain, which
walks *up* through the base classes. A value a **derived** class overrode
natively would not be seen — `AFGBuildableHologram::mGridSnapSize` is that
class's own default, not a promise that no subclass changes it. The overrides
that exist today are Blueprint ones, and `assets.json` has those.

## Layout of `native.json`

```json
{"provenance": {"dll", "pdb", "pdb_guid", "pdb_age", "dll_sha256", "tool"},
 "classes": {"<Class>": {
    "size", "base", "chain": ["<Class>", "<base>", ...],
    "ctor_rva": {"<Class>::<Class>": ["0x...", "0x..."]},
    "members": {"<member>": {
       "offset", "type",
       "value" | null, "set_in", "evidence", "size_source",  // a constant found
       "entry_offset", "size_reason",             // when the bound needs them
       "reason",                                  // when no constant was found
       "also_set_in": [{"set_in", "value", "evidence", "size_source",
                        "entry_offset", "size_reason"}]}}}}}
```

`size_source` is the bound on the function named beside it, from the same
three-source ladder as `disasm`'s. Every entry in the committed file says
`pdata`; a `ret` or a `truncated` there would mean the value was read out of a
function whose end the game does not state, and the rest of it never looked at.

Two keys qualify it, and both are absent whenever they have nothing to say:

- **`entry_offset`** — the symbol is not the entry point of the function that was
  disassembled. `.pdata` says which function an RVA belongs to, so a symbol
  *inside* an entry, or on a chunk MSVC chained onto another function, is read as
  the whole primary function from that function's own `begin`; `entry_offset` is
  how far into it the name sits. Without it a `pdata` could mean "part of a
  function" while reading like the whole of one. No entry in the committed file
  has it: every function the tool is asked for is its own entry point.
- **`size_reason`** — present exactly when `size_source` is `truncated`, saying
  what stopped the read: a range the section or the 64 KiB cap cut short, or a
  byte partway through the function that begins no instruction the decoder
  knows. A length check cannot see that second one, because the bytes are all
  there and only the decode stopped.

A class has two constructors whenever UE emits its `FVTableHelper` one as well.
That one carries only the in-class initialisers; the real one also carries the
constructor body's assignments. Both RVAs are listed, and `evidence` names the
one the reported value came from.

## Tests

`cargo test` covers the store tracer's patterns against hand-assembled byte
sequences (each test asserts the disassembly text too, so a wrong encoding fails
loudly rather than passing vacuously), the constructor manglings, the CodeView
GUID formatting and the float decoding. The `disasm` mode's annotations are
tested the same way — a read through `this` against a hand-built layout with a
primary base at 0 and a secondary base at 2300, a dead alias after a call, a
static function annotating nothing against `rcx`, an sret function's return
slot in `rdx` staying unannotated, a `call rel32` against a fake symbol map, a
`.rdata` constant and the `.data` global it refuses, an indirect call named from
a seeded import table (and the `mov` off the same slot that is not), the
hexadecimal-RVA selector (a named function, a templated one no name reaches, an
address the PDB publishes nothing at, and a needle that only looks like one), the
`.pdata`/`ret`/`truncated` bounds, a leaf with two `ret`s that only the PDB's
stated length gets right (with `.pdata` still winning where it has an entry,
and an over-long stated length still saying `truncated`), and a synthetic
three-chunk function whose
chained `UNWIND_INFO` — listed out of `.pdata` order, one link deep and two,
with an odd unwind-code count so the parent entry sits past its padding — has to
come back as one function in RVA order.

`tests/sfy/test_native.py` holds the committed `native.json` to the oracle and
to the evidence each value carries, and runs `disasm` against the installed
game — skipping without one — to check that
`AFGConveyorBeltHologram::ValidateCurvature` is found through `.pdata` and seen
reading `mBendRadius`, that `ValidateConveyorBelt` comes back as all three of
its chunks with the `mMaxSplineLength` comparison in the last one, that
`AFGBuildableHologram::GetRotationStep` comes back as all 77 bytes with all
four of its returns while the lift's override still reports `pdata`, and that
`ValidateCurvature`'s output is still byte-for-byte what it was before chunk
chaining existed. That last test compares against the committed
`tests/sfy/data/disasm_validate_curvature_pre_chunks.json` after dropping the
two keys that were *added* since — `chunks` on the function and `import` on an
instruction — and then pins those import names, so an additive annotation stays
a deliberate edit rather than silent drift.
