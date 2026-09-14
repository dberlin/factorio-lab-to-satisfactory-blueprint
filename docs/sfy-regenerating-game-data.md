# Regenerating the Satisfactory game data

Everything `flab2bp.sfy` knows about the game — what can be built, out of what,
with which connection ports, under which placement limits — is extracted from an
installed copy of Satisfactory into five committed JSON files under
`src/flab2bp/sfy/data/`. Nothing there is typed in by hand, and nothing there is
measured from the blueprint corpus.

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
| 4 | `uv run python scripts/sfy_measure_limits.py` | the fixture corpus | `data/measured.json` |
| 5 | `uv run python scripts/sfy_registry.py` | steps 1–4 | `data/registry.json` |

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

## 4. The blueprint corpus → `measured.json`

```bash
uv run python scripts/sfy_measure_limits.py
```

**Cross-check only.** Every limit in the registry has a game-data source; this
fills none of them. It walks the save-version-58-and-up fixtures and records the
spread of what players actually built — tightest belt bend, steepest incline,
lift heights — so the game's constants can be held against it, and it records
which direction the corpus wires each belt port in, which is one of the things
the merge resolves an unstated port direction from. Each entry says
`"role": "cross-check"` for exactly this reason: an envelope is what the game
was seen to accept, never a constraint it enforces.

Re-run it when fixtures are added; it needs no game install.

## 5. The merge → `registry.json`

```bash
uv run python scripts/sfy_registry.py
```

Joins all four into the file `flab2bp.sfy.registry.load_registry` reads. It
prints where every limit came from, any it could not fill, how each port
direction was resolved, and how many asset paths it kept.

It refuses to write when the sources contradict each other: a header against the
binary, an asset's stated port direction against the corpus or against the
naming convention, a port no source gives a direction to, or an item or recipe
with no asset path. A refusal means the extraction is wrong, not that the
registry needs an edit — `registry.json` is generated, never hand-edited.

## After a game update

1. Run all five steps.
2. `uv run pytest tests/sfy` — the drift test and the port cross-checks are the
   gate.
3. `git diff --stat src/flab2bp/sfy/data/` — a game update should move the build
   version and whatever the patch notes say it moved, and nothing else.
4. Rebuild the checkpoint blueprint and load it in the game:
   `uv run python scripts/sfy_checkpoint1.py` writes `out/sfy/checkpoint1.sbp`
   and `.sbpcfg` (not committed) and checks them; its docstring says where to
   copy them and what to look at in game.
