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

The **bold** rows are the ones no other source has. `pipe_bend_radius_cm` is
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
stay `"value": null` with that reason and the registry falls through to
`measured.json` for them. `lift_step_cm` is the one constant in the block.

### The grid snap size

`AFGBuildableHologram::mGridSnapSize` is a `UPROPERTY( EditDefaultsOnly )`
(`Hologram/FGBuildableHologram.h:445`), so a hologram Blueprint can override it,
and two do: Task 10's `assets.json` has `Holo_PowerPole_C` and
`Holo_StreetLight_C` at 50. The native constructor default — the value every
other hologram uses — is **100**, which is what the registry carries. Task 11's
corpus gcd of 50 was mesh offsets, exactly as its concern 4 suspected.

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

The conservative direction is always `null` with a reason: the tool never
guesses a value, and the tests hold it to the oracle.

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
       "value" | null, "set_in", "evidence",      // when a constant was found
       "reason",                                  // when it was not
       "also_set_in": [{"set_in", "value", "evidence"}]}}}}}
```

A class has two constructors whenever UE emits its `FVTableHelper` one as well.
That one carries only the in-class initialisers; the real one also carries the
constructor body's assignments. Both RVAs are listed, and `evidence` names the
one the reported value came from.

## Tests

`cargo test` covers the store tracer's patterns against hand-assembled byte
sequences (each test asserts the disassembly text too, so a wrong encoding fails
loudly rather than passing vacuously), the constructor manglings, the CodeView
GUID formatting and the float decoding. `tests/sfy/test_native.py` holds the
committed `native.json` to the oracle and to the evidence each value carries.
