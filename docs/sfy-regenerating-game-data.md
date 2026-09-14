# Regenerating the Satisfactory game data

Everything `flab2bp.sfy` knows about the game — what can be built, out of what,
with which connection ports, under which placement limits, and what the build
gun's hologram refuses — is extracted from an installed copy of Satisfactory
into committed JSON files under `src/flab2bp/sfy/data/`. Nothing there is typed
in by hand, and **nothing there comes from a blueprint corpus**: a community
blueprint can carry clipped geometry, a hacked save or an older game version, so
what one contains is not a source, not a cross-check and not evidence, for
legality or for anything else.

The files are committed so that the tests, and anyone building the project, need
no game install. **Re-run this whole sequence after a game update**, in the order
below: each step's output is the next one's input, and the merge at the end is
what the package actually reads.

```bash
export FLAB2BP_SATISFACTORY_DIR="$HOME/Satisfactory"   # the default, if unset
```

| # | Step | Needs | Writes |
| --- | --- | --- | --- |
| 1 | `uv run python -m flab2bp.sfy.docs` | the install's Docs dump | `data/docs.json` |
| 2 | `tools/sfy-extract` | .NET 10, the paks and the usmap | `data/assets.json` |
| 3 | `tools/sfy-native` | cargo, the shipped DLL **and its PDB** | `data/native.json` |
| 4 | `uv run python scripts/sfy_native_rules.py` | cargo, the same DLL and PDB, `Headers.zip` | `data/hologram_rules.json` |
| 5 | `uv run python scripts/sfy_registry.py` | steps 1–4 | `data/registry.json` |

`scripts/sfy_measure_limits.py` is *not* in this sequence. It describes the
fixture corpus and writes `data/measured.json`, which nothing reads; step 4 says
why.

Then re-run the tests, which read the committed files and are the acceptance for
all five steps:

```bash
uv run pytest tests/sfy
```

`tests/sfy/test_registry.py` re-runs step 5 into a temporary file and diffs it
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
dimensions, the recipe graph and the build recipes. It records the dump's
sha256 in the file's provenance.

## 2. Cooked assets → `assets.json`

```bash
cd tools/sfy-extract
dotnet run -- "$FLAB2BP_SATISFACTORY_DIR" extract ../../src/flab2bp/sfy/data/assets.json
cd ../..
```

Needs the .NET 10 SDK and an install carrying
`CommunityResources/FactoryGame.usmap`. Reads the paks and IoStore containers
through CUE4Parse and writes: every buildable's connection ports (position,
rotation, kind, direction, clearance, and a power connection's
`mMaxNumConnectionLinks`), each buildable's hologram class and any placement
limit that hologram's Blueprint overrides, the wire lengths, and the full asset
path of every class Docs.json states one for — which is how a blueprint names an
item descriptor or a recipe.

The run is deterministic: re-running it produces a byte-identical file apart
from `provenance.extracted`. `tools/sfy-extract/README.md` has the discovery
modes (`list`, `props`) for when a game update moves something, and a table of
where each value lives.

## 3. The shipped binary → `native.json`

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

## 4. The hologram rules → `hologram_rules.json`

```bash
uv run python scripts/sfy_native_rules.py
```

Needs cargo and the same game install as step 3 — the shipped DLL, its PDB and
`CommunityResources/Headers.zip` — and refuses if the DLL has moved since
`native.json` was written, because the rules quote the member offsets that file
resolved.

A number in `registry.json` says what the game's data contains; this says what
the hologram *does* with it. It drives `sfy-native disasm` once per rule over
`AFGConveyorBeltHologram::ValidateCurvature` and its sixteen siblings, and
writes each one's entry RVA, the members it reads, the `.rdata` constants it
uses, the calls it makes, and the instructions the comparison was read at. Each
rule is `extracted`, `partial` or `unextractable`, and a `partial` is a bound the
placer must not assume it knows. `docs/sfy-hologram-rules.md` is the map, and
`src/flab2bp/sfy/rules.py` is the reader.

The script fails rather than quoting stale instructions: if a function moved, the
addresses in its `EVIDENCE` table no longer decode and the run stops, which is
the signal to re-read that validator and rewrite its interpretation.

### Not a step: `measured.json`

`uv run python scripts/sfy_measure_limits.py` measures the fixture corpus and
writes `data/measured.json`. **Nothing reads it.** It is not in the merge, not in
`registry.json` and not in any validator, because what a blueprint contains is
not a fact about the game. Re-run it when fixtures are added if you want the
corpus described; skip it otherwise.

## 5. The merge → `registry.json`

```bash
uv run python scripts/sfy_registry.py
```

Joins steps 1–4 into the file `flab2bp.sfy.registry.load_registry` reads. It
prints where every limit came from, any it could not fill, how each port
direction was resolved, and how many asset paths it kept. Every limit also gets
`provenance.limits[key].enforced_by` — the hologram rule that turns it into a
refusal, or `null` with the reason nothing does.

It refuses to write when the sources contradict each other: a header against the
binary, an asset's stated port direction against the naming convention, a port no
source gives a direction to, an item or recipe with no asset path, or a limit
naming a hologram rule that does not exist. A refusal means the extraction is
wrong, not that the registry needs an edit — `registry.json` is generated, never
hand-edited.

## After a game update

1. Run all five steps. Step 4 is the one most likely to fail: a validator that
   moved stops it by name and address, which is the point.
2. `uv run pytest tests/sfy` — the drift test and the port cross-checks are the
   gate.
3. `git diff --stat src/flab2bp/sfy/data/` — a game update should move the build
   version and whatever the patch notes say it moved, and nothing else.
4. Rebuild the checkpoint blueprint and load it in the game:
   `uv run python scripts/sfy_checkpoint1.py` writes `out/sfy/checkpoint1.sbp`
   and `.sbpcfg` (not committed) and checks them; its docstring says where to
   copy them and what to look at in game.
