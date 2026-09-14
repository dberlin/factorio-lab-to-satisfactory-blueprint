# Regenerating the Satisfactory game data

Everything `flab2bp.sfy` knows about the game — what can be built, out of what,
with which connection ports, under which placement limits, and what the build
gun's hologram refuses — is extracted from an installed copy of Satisfactory
into committed JSON files under `src/flab2bp/sfy/data/`. Nothing there is typed
in by hand, and **nothing there comes from a blueprint corpus, and nothing from
the name of anything**: a community blueprint can carry clipped geometry, a
hacked save or an older game version, so what one contains is not a source, not
a cross-check and not evidence, for legality or for anything else; and what a
component happens to be called is a convention, not something the game reads.

The files are committed so that the tests, and anyone building the project, need
no game install. **Re-run this whole sequence after a game update**, in the order
below: step 4 needs step 2's output and step 5 needs step 4's, and the merge at
the end is what the package actually reads.

```bash
export FLAB2BP_SATISFACTORY_DIR="$HOME/Satisfactory"   # the default, if unset
```

| # | Step | Needs | Writes |
| --- | --- | --- | --- |
| 1 | `uv run python -m flab2bp.sfy.docs` | the install's Docs dump | `data/docs.json` |
| 2 | `tools/sfy-native` | cargo, the shipped DLL **and its PDB** | `data/native.json` |
| 3 | `uv run python scripts/sfy_native_rules.py` | cargo, the same DLL and PDB, `Headers.zip` | `data/hologram_rules.json` |
| 4 | `uv run python scripts/sfy_native_directions.py` | cargo, the same DLL and PDB | `data/native_directions.json` |
| 5 | `tools/sfy-extract extract` | .NET 10, the paks, the usmap, step 4 | `data/assets.json` |
| 6 | `tools/sfy-extract structs` | .NET 10 and the usmap | `data/struct_schemas.json` |
| 7 | `uv run python scripts/sfy_registry.py` | steps 1–5 | `data/registry.json` |

Then re-run the tests, which read the committed files and are the acceptance for
all seven steps:

```bash
uv run pytest tests/sfy
```

`tests/sfy/test_registry.py` re-runs step 7 into a temporary file and diffs it
against what is committed, so a registry that was edited by hand, or one whose
sources have moved on, fails there.

## 1. Docs.json → `docs.json`

```bash
uv run python -m flab2bp.sfy.docs
```

Reads `$FLAB2BP_SATISFACTORY_DIR/CommunityResources/Docs/en-US.json`, the game's
own reflection dump of every buildable, recipe and descriptor class default
(UTF-16, despite the extension), and keeps the fields the placer needs:
clearance boxes, power draw, manufacturing speed, belt speed, mesh and designer
dimensions (including `mMeshLength` and `mMeshHeight`, which are what a conveyor
is charged its build recipe once per — see the `belt.cost` rule), the recipe
graph and the build recipes. It records the dump's
sha256 in the file's provenance.

## 2. The shipped binary → `native.json`

```bash
cd tools/sfy-native
cargo build --release
./target/release/sfy-native \
  "$FLAB2BP_SATISFACTORY_DIR/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.dll" \
  "$FLAB2BP_SATISFACTORY_DIR/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.pdb" \
  ../../src/flab2bp/sfy/data/native.json \
  --expect AFGConveyorBeltHologram.mMaxSplineLength=5600.1 \
  --expect AFGPipelineHologram.mBendRadius2D=199 \
  --expect AFGPipelineHologram.mMinBendRadius=75 \
  --expect AFGPipelineHologram.mMaxSplineLength=5600.1 \
  --expect AFGBuildableWire.mMaxLength=10000
cd ../..
```

Seven hologram limits are C++ constructor immediates: in no cooked asset, no
shipped header and no Docs.json entry. This disassembles the module DLL, guided
by its PDB, and traces the constant stores. The PDB must sit beside the DLL and
match its CodeView GUID, or the tool refuses.

The `--expect` flags are the oracle: five of the values it reads are also stated
in the public headers or in Docs.json, and a disagreement fails the run **before**
anything is written, so a broken extraction cannot replace the committed file.
Keep them on every run. `tools/sfy-native/README.md` explains each limit and
what to do when one comes back `null`.

## 3. The hologram rules → `hologram_rules.json`

```bash
uv run python scripts/sfy_native_rules.py
```

Needs cargo and the same game install as step 2 — the shipped DLL, its PDB and
`CommunityResources/Headers.zip` — and refuses if the DLL has moved since
`native.json` was written, because the rules quote the member offsets that file
resolved.

A number in `registry.json` says what the game's data contains; this says what
the hologram *does* with it. It drives `sfy-native disasm` once per rule over
`AFGConveyorBeltHologram::ValidateCurvature` and its siblings (and, for a rule
the game spreads over several functions, once per function — `ALSO_READ`), and
writes each one's entry RVA, the members it reads, the `.rdata` constants it
uses, the calls it makes, and the instructions the comparison was read at. Each
rule is `extracted`, `partial` or `unextractable`, and a `partial` is a bound the
placer must not assume it knows. `docs/sfy-hologram-rules.md` is the map, and
`src/flab2bp/sfy/rules.py` is the reader.

The script fails rather than quoting stale instructions: if a function moved, the
addresses in its `EVIDENCE` table no longer decode and the run stops, which is
the signal to re-read that validator and rewrite its interpretation.

## 4. The connection directions → `native_directions.json`

```bash
uv run python scripts/sfy_native_directions.py
```

Needs cargo and the same DLL and PDB as step 2, and refuses if the DLL has moved
since `native.json` was written.

A cooked asset leaves a component template's property out whenever it equals the
**archetype**'s value, and for a connection component the archetype is a native
constructor the pak does not carry. 177 of the 288 connection templates in the
content say nothing about their direction for that reason. This disassembles the
constructors that do say — each connection component class's own, and the four
buildables whose constructors create their connections and set them — and writes
what each stores, with the instruction it was read at.

`tools/sfy-extract` reads the result and resolves every port with it;
`tools/sfy-extract/README.md` has the table of what was found and why both ends
of a conveyor are `FCD_ANY` rather than the input and output a header comment
suggests.

The same run writes `conveyor_flow`: **which end of a conveyor items enter by**,
which `mDirection` cannot answer precisely because both ends are `FCD_ANY`.
`Buildables/FGBuildableConveyorBase.h:380` states the order in a comment, and
the binary makes it explicit — `AFGBuildableConveyorBase::Factory_Tick` grabs an
item through one of the two connections by calling
`UFGFactoryConnectionComponent::Factory_GrabOutput` on it, and that callee's
grab is the branch taken when the connection's own `mDirection` is `FCD_INPUT`.
Which member that load names is the tool's annotation, not a name typed into the
script, and `BeginPlay` is quoted binding each member to the component of that
name. The one thing the disassembly cannot show is the text behind the two
`FName` globals those lookups use (a module initialiser with no `Class::Method`
symbol fills them in at start-up), so this file states the order over the two
**members** and says nothing about what the components are called. Step 5 reads
that from the cooked class default object instead (`conveyor_connections`), and
step 7 joins the two and attaches the result to every class that inherits the
constructor, refusing if such a class does not carry both ports.

## 5. Cooked assets → `assets.json`

```bash
cd tools/sfy-extract
dotnet run -- "$FLAB2BP_SATISFACTORY_DIR" extract \
    ../../src/flab2bp/sfy/data/assets.json \
    ../../src/flab2bp/sfy/data/native_directions.json
cd ../..
```

Needs the .NET 10 SDK and an install carrying
`CommunityResources/FactoryGame.usmap`. Reads the paks and IoStore containers
through CUE4Parse and writes: every buildable's connection ports (position,
rotation, kind, direction, clearance, and a power connection's
`mMaxNumConnectionLinks`), each buildable's hologram class and any placement
limit that hologram's Blueprint overrides, the wire lengths, the full asset
path of every class Docs.json states one for — which is how a blueprint names an
item descriptor or a recipe — and `conveyor_connections`, the component each
conveyor class's `mConnection0`/`mConnection1` points at, read off its class
default object. That last one is what turns step 4's member order into two port
names without anyone reading a name that ends in 0 as evidence.

Step 4's file is the second argument and is required: it is what resolves the
177 ports whose own asset says nothing about their direction. Each port carries
a `direction_source` saying which link of the archetype chain answered —
`asset`, `asset-inherited`, `native`, or `unknown` when none did.

The run is deterministic: re-running it produces a byte-identical file apart
from `provenance.extracted`. `tools/sfy-extract/README.md` has the discovery
modes (`list`, `props`) for when a game update moves something, and a table of
where each value lives.

## 6. The usmap's struct schemas → `struct_schemas.json`

```bash
uv run python scripts/sfy_struct_names.py > /tmp/struct-names.txt
cd tools/sfy-extract
dotnet run -- "$FLAB2BP_SATISFACTORY_DIR" structs /tmp/struct-names.txt \
    ../../src/flab2bp/sfy/data/struct_schemas.json
cd ../..
```

The game's own field list for every struct name a blueprint's property tags
carry, straight out of `FactoryGame.usmap` — this mode mounts no paks and loads
no package. `tests/sfy/test_struct_schemas.py` checks the codec's decoded
structs against it, so a game update that adds or reorders a struct's fields
shows up there. A name with no usmap struct is listed in `provenance.missing`
and makes the exit code 1.

## 7. The merge → `registry.json`

```bash
uv run python scripts/sfy_registry.py
```

Joins steps 1–5 into the file `flab2bp.sfy.registry.load_registry` reads. It
prints where every limit came from, any it could not fill, how each port
direction was resolved, how many classes got a conveyor flow order, and how many
asset paths it kept. Each belt and lift mark carries
`flow: {"entry", "exit", "source"}` — the two port names items enter and leave
by, from step 4 — and `provenance.conveyor_flow` carries the header line, the
functions, their RVAs and the instructions behind it. Every limit also gets
`provenance.limits[key].governed_by` — `{"rule": <rule id>, "effect": <effect>}`
for the hologram rule that governs it, with the effect copied from that rule, or
`null` and an `ungoverned` sentence saying why no rule does. **Only the effect
`refuse` turns a placement away**: `clamp` and `snap` move the hologram instead,
so a validator that treats either as a bound refuses builds the game accepts. A
rule whose effect is `none` governs nothing and is never named here — the limit
it was read beside is `ungoverned`, with the rule's id in the reason (this is
`lift_step_cm` and `lift.step`). The merge refuses to write when a limit's
copied effect is not the one its rule states.

It refuses to write when the sources contradict each other: a header against the
binary, a port whose `direction_source` is not one of the four game sources, an
item or recipe with no asset path, or a limit naming a hologram rule that does
not exist. A port whose direction is `unknown` is *not* a refusal — it is the
honest answer when no part of the game gives that port a direction, and it is
shipped as `unknown` so that **the validator refuses to route to it** rather
than inventing one from the port's name. A refusal means the extraction is
wrong, not that the registry needs an edit — `registry.json` is generated, never
hand-edited.

## After a game update

1. Run all seven steps. Step 3 is the one most likely to fail: a validator that
   moved stops it by name and address, which is the point.
2. `uv run pytest tests/sfy` — the drift test and the port cross-checks are the
   gate.
3. `git diff --stat src/flab2bp/sfy/data/` — a game update should move the build
   version and whatever the patch notes say it moved, and nothing else.
4. Rebuild the checkpoint blueprint and load it in the game:
   `uv run python scripts/sfy_checkpoint1.py` writes `out/sfy/checkpoint1.sbp`
   and `.sbpcfg` (not committed) and checks them; its docstring says where to
   copy them and what to look at in game.
