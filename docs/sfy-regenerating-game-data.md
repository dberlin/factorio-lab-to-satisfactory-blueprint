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
below: steps 3 and 4 both need step 2's output and refuse without it, step 5
needs step 4's, and the merge at the end is what the package actually reads.

> **Using it, rather than regenerating it.** Nothing below is needed to build a
> blueprint. `flab2bp '<factoriolab.github.io/sfy/... URL>' --flow <csv>
> --designer mk1|mk2|mk3 -o <dir>` writes a `.sbp`/`.sbpcfg` pair from the
> committed data; FactorioLab's own solved flow is required (`--flow` or
> `--fetch-flow`), and fluids, a run past the fastest belt the save can build,
> and anything that does not fit the chosen designer in one level of rows are
> refused by name. The README's "Satisfactory" section is the fuller account,
> including the exit codes.

Every command below is run **from the repository root**, and the ones that `cd`
into a tool directory `cd` back out again.

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
| 8 | `uv run python scripts/sfy_lab_map.py` (after `--refresh-names`) | steps 1 and 7, the install's Docs dump | `data/lab_item_names.json`, `data/lab_map.json` |

Then re-run the tests, which read the committed files and are the acceptance for
all eight steps:

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
graph and the build recipes. It also keeps everything the overclocking rules
need: each buildable's `mMinPotential`/`mMaxPotential`, its two shard slot counts
*and the `mOverride*` flags that say whether those counts are the ones it runs
on*, `mBaseProductionBoost`, `mProductionShardBoostMultiplier` and the two power
exponents — plus a `power_shards` section, the `FGPowerShardDescriptor` class
defaults, because what a power shard or a somersloop is worth is stated on the
item and not on the machine. It records the dump's
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

Three more constructor values ride along in the same run, and none of them is a
hologram's. `UFGCircuitConnectionComponent::mMaxNumConnectionLinks` is how many
wires may end on a power connection when no Blueprint in the chain overrides it
— **1**, which is every machine's power input — and
`AFGBuildableSubsystem::mDefaultPotentialShardSlots` /
`mDefaultProductionShardSlotSize` are the shard slot counts
`AFGBuildableFactory::BeginPlay` copies onto every buildable that does not
override its own (the `factory.potential` rule quotes the two branches). All
three are archetype defaults the paks do not carry, so without them a machine's
power input and every machine's overclock slots would ship as unknown.

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
the game *does* with it. It drives `sfy-native disasm` once per rule over
`AFGConveyorBeltHologram::ValidateCurvature` and its siblings (and, for a rule
the game spreads over several functions, once per function — `ALSO_READ`), and
writes each one's entry RVA, the members it reads, the `.rdata` constants it
uses, the calls it makes, and the instructions the comparison was read at. Each
rule is `extracted`, `partial` or `unextractable`, and a `partial` is a bound the
placer must not assume it knows. `docs/sfy-hologram-rules.md` is the map, and
`src/flab2bp/sfy/rules.py` is the reader.

Not every rule is a hologram's. `belt.clearance` and `lift.clearance` are read
across into the **buildable** that builds the boxes
(`AFGBuildableConveyorBelt::CreateClearanceData`,
`AFGBuildableConveyorLift::FitClearance`), and `factory.potential` and
`manufacturer.production_boost` are read out of `AFGBuildableFactory` and
`AFGBuildableManufacturer` — what a power shard and a somersloop do, which
`registry.json`'s four overclocking limits are governed by.

The script fails rather than quoting stale instructions: if a function moved, the
addresses in its `EVIDENCE` table no longer decode and the run stops, which is
the signal to re-read that validator and rewrite its interpretation.

It also refuses to over-claim when `sfy-native` could not read a function to an
end the game states (a `size_source` of `ret` or `truncated`): the rule is
downgraded to `partial` with that reason written into its `comparison`, and an
effect of `none` — which says the read instructions enforce nothing, an absence
— stops the run outright.

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

Two of those defaults are an *absence* of a store (nothing in the pipe-connection
chain writes `mPipeConnectionType`, so it keeps the zero `UObject` construction
leaves). An absence read out of half a function is worth nothing, so the script
stops unless every constructor in the chain came back bounded by `.pdata`, its
chain, or the PDB's procedure length.

Each of those absences is looked for at **that member's own offset**, which the
script gets by running `sfy-native`'s `extract` mode once for the class the PDB
declares the member on (`UFGPipeConnectionComponentBase::mPipeConnectionType`,
`UFGFactoryConnectionComponent::mDirection`). The two sit at the same offset in
the shipped build, which is exactly why neither is allowed to stand in for the
other. That extra run is why the step needs the PDB as well as the DLL.

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
`mMaxNumConnectionLinks` with the `max_connections_source` saying which link of
the archetype chain stated it), each buildable's hologram class and any placement
limit that hologram's Blueprint overrides, the wire lengths, the full asset
path of every class Docs.json states one for — which is how a blueprint names an
item descriptor or a recipe — and `conveyor_connections`, the component each
conveyor class's `mConnection0`/`mConnection1` points at, read off its class
default object. That last one is what turns step 4's member order into two port
names without anyone reading a name that ends in 0 as evidence.

It also writes `mesh_bounds` and `subsystem_defaults`. `mesh_bounds` is the
local-space box of the static mesh a spline buildable repeats along itself —
`Origin ± BoxExtent` of the cooked `UStaticMesh`'s `RenderData.Bounds` — reached
through the class default object's `mMesh` (a belt) or `mMidMesh` (a lift's
repeated mid section), walking up the Blueprint chain because Mk2 to Mk6 restate
only `mMidMesh`; each entry names the property and the asset path it read.
`subsystem_defaults` is the cooked `AFGBuildableSubsystem` Blueprint's shard slot
counts, found by its super chain and not by its package's name: the shipped
content overrides `mDefaultProductionShardSlotSize` to 1 and leaves
`mDefaultPotentialShardSlots` to the constructor's 3, which is the same
asset-beats-binary hand-off `mGridSnapSize` makes.

It also writes `descriptor_paths`: the same item-descriptor asset paths as
`class_paths`, read the other way round — every cooked package is loaded, and
every `BlueprintGeneratedClass` whose super chain reaches `FGItemDescriptor`
contributes its own class name and its own outer. Docs.json and the cooked
assets are then two independent statements of one fact, and
`tests/sfy/test_templates.py` holds `registry.json`'s `item_paths` to both. That
scan is why this step takes about 30 s rather than 5.

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
direction and each power port's wire count was resolved, how many classes got a
conveyor flow order or a mesh box, and how many
asset paths it kept. Each belt and lift mark carries
`flow: {"entry", "exit", "source"}` — the two port names items enter and leave
by, from step 4 — and `provenance.conveyor_flow` carries the header line, the
functions, their RVAs and the instructions behind it. Every limit also gets
`provenance.limits[key].governed_by` —
`{"rule": <rule id>, "effect": <effect>, "status": <status>}` for the hologram
rule that governs it, with the effect and the status copied from that rule, or
`null` and an `ungoverned` sentence saying why no rule does. The copied status
is how a `partial` rule — one whose function the tool could not read to the end —
is visible on the limit without opening `hologram_rules.json`; `hologram_grid_cm`
is the one in the shipped file. **Only the effect
`refuse` turns a placement away**: `clamp` and `snap` move the hologram instead
and `compute` only works a number out, so a validator that treats any of the
three as a bound refuses builds the game accepts. A
rule whose effect is `none` governs nothing and is never named here — the limit
it was read beside is `ungoverned`, with the rule's id in the reason (this is
`lift_step_cm` and `lift.step`). The merge refuses to write when a limit's
copied effect or copied status is not the one its rule states.

It refuses to write when the sources contradict each other: a header against the
binary, a port whose `direction_source` is not one of the four game sources, an
item or recipe with no asset path, or a limit naming a hologram rule that does
not exist. A port whose direction is `unknown` is *not* a refusal — it is the
honest answer when no part of the game gives that port a direction, and it is
shipped as `unknown` so that **the validator refuses to route to it** rather
than inventing one from the port's name. A refusal means the extraction is
wrong, not that the registry needs an edit — `registry.json` is generated, never
hand-edited.

Two of the limits are neither an asset value nor a constructor immediate but a
**formula the machine code applies**, tagged `binary-derived` and carrying the
formula, its input and the instruction in `provenance.limits[key]`: the three
lift heights, and `belt_min_length_cm`, which is
`AFGConveyorBeltHologram::ValidateMinLength`'s `0.5001 × mMeshLength` (100.02 cm
on every belt mark). The merge holds that 0.5001 to the `belt.min_length` rule's
own evidence line and stops if the two ever disagree, and it refuses outright if
the belt marks stop sharing one `mMeshLength` — a per-mark floor would have to
move onto the buildable.

The four overclocking limits — `potential_per_shard`,
`production_boost_per_slot`, `potential_shard_slots_default` and
`production_boost_slots_default` — are governed by `factory.potential` and
`manufacturer.production_boost`, whose effect is `compute`: the game works each
of them out and refuses nothing over any of them. The two per-class numbers they
need, `mPowerConsumptionExponent` and `mProductionShardBoostMultiplier`, are
**not** limits: the game states a different exponent for manufacturers and
extractors (1.321929) than for everything else (1.6), so a single global would be
a number nobody stated, and both ride on the buildable that states them.

## 8. The lab map → `lab_item_names.json` and `lab_map.json`

```bash
uv run python scripts/sfy_lab_map.py --refresh-names
uv run python scripts/sfy_lab_map.py
```

What a FactorioLab id means in the game: lab item id → `Desc_*_C`, lab recipe id
→ `Recipe_*_C`, lab machine, belt and pipe id → `Build_*_C`, plus the lab recipes
the game has no `Recipe_*_C` for and the reason for each.
`flab2bp.sfy.labmap.load_lab_map` reads the result.

**Order matters: step 1, then `--refresh-names`, then the derive.** The first
command is the only one here that reads the install. It collects
`mDisplayName` for every class in `registry.item_paths` that states one, out of
the same `CommunityResources/Docs/en-US.json` step 1 reads, and writes
`data/lab_item_names.json`. It is a separate file because `data/docs.json` keeps
buildables, recipes and the `Desc_X_C → Build_X_C` descriptor map but no item
display name, and because a *building* descriptor leaves `mDisplayName` empty —
its buildable carries the name, which is where the machine, belt and pipe rows
are matched from instead.

Both halves refuse rather than let the two drift apart: `--refresh-names` stops
if the install's dump is not the one `data/docs.json` was built from, and the
derive stops if `lab_item_names.json`'s `docs_sha256` is not `docs.json`'s. So
after a game update, re-run step 1 **before** `--refresh-names`, and step 7
before the derive.

The derive itself reads only committed files — the registry, the names file and
FactorioLab's vendored `data.json` — and **guesses nothing**. Items match on
display name, exactly (case and runs of whitespace aside); a name with no match
or more than one refuses the run unless a commented override names the class.
Recipes match on an exact signature over the *mapped* item classes: producer
`Build_*_C`, duration as a `Fraction`, and the sorted `(Desc_*_C, amount)` pairs
on both sides, with fluid amounts multiplied by 1000 because the lab states m3
and the game centilitres. A lab id with no candidate, or with more than one,
fails the run with the candidates listed; the two item overrides, the one recipe
override and the twenty unmapped recipes are each spelled out in the script with
the reason, and each is itself re-checked against game data. The machine table is
written out by hand — no convention produces `refinery → Build_OilRefinery_C` —
and then derived a second way, through the item table and
`registry.descriptors`, with a refusal if the two disagree.

`tests/sfy/test_labmap.py` is the acceptance: it re-runs this step into a
temporary file and diffs it against what is committed (ignoring `provenance`),
holds every mapped class to `registry.recipes` / `item_paths` / `buildables`,
checks that every lab recipe either maps or says why not and that every item a
mapped recipe names has a class, and doctors the script's own tables in process
to prove each refusal path still refuses.

## After a game update

1. Run all eight steps. Step 3 is the one most likely to fail: a validator that
   moved stops it by name and address, which is the point.
2. `uv run pytest tests/sfy` — four things in it are the gate:
   - the **drift test**,
     `test_registry.py::test_the_committed_registry_is_what_the_merge_produces`,
     which re-runs step 7 into a temporary file and diffs it against what is
     committed;
   - the **fixture byte-identity suite**, `test_codec.py`'s
     `test_full_file_round_trip_is_byte_identical` and its siblings over every
     fixture, which is what says the format reader still reads the format;
   - the **registry's own consistency tests**, the rest of
     `tests/sfy/test_registry.py`: every limit filled and sourced, every port's
     direction traced to the game link that answered, every conveyor's flow and
     cost segment naming its source, and nothing anywhere coming from the
     blueprint corpus.
   - the **lab map's drift test**,
     `test_labmap.py::test_the_committed_map_is_what_the_script_derives`, which
     does the same for step 8 — and will refuse outright if `--refresh-names`
     was not re-run against the dump step 1 read.
3. `git diff --stat src/flab2bp/sfy/data/` — a game update should move the build
   version and whatever the patch notes say it moved, and nothing else.
4. Rebuild the checkpoint blueprint and load it in the game:
   `uv run python scripts/sfy_checkpoint1.py` writes `out/sfy/checkpoint1.sbp`
   and `.sbpcfg` (not committed) and checks them; its docstring says where to
   copy them and what to look at in game.
