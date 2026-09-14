# Satisfactory M2: lab game parameter, rates, manifold rows, validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FactorioLab `sfy` URL plus its flow export becomes one valid Satisfactory blueprint of manifold rows, checked by a validator that consumes the extracted hologram rules, written by the CLI, and pasted in game as checkpoint 2.

**Architecture:** `lab/` gains a `Game` value and the vendored sfy dataset; `flab2bp.sfy` gains a lab-to-game id map, a build spec and rates derived from the flow, a placement model in centimetres with spline builders, a validator with the DSP validator's check registry, one layout strategy (manifold rows: splitter chains feed each machine row, merger chains drain it, corridors along the two side walls carry trunk belts, with bridges where a belt must cross another), power poles and wires, and a pipeline that the CLI dispatches to when the URL is an `sfy` URL. Everything geometric is read from `registry.json` and `hologram_rules.json`; nothing is measured off a blueprint.

**Tech Stack:** Python 3.14, pydantic (spec), dataclasses, pytest; `tools/sfy-extract` (C#) and `tools/sfy-native` (Rust) for the game facts the geometry still lacks; the existing `flab2bp.sfy` codec and templates.

**Spec:** `docs/superpowers/specs/2026-09-13-satisfactory-target-design.md`, sections 6 to 12. The M1 and M1b plans (`docs/superpowers/plans/2026-09-13-satisfactory-m1-format-and-registry.md`, `docs/superpowers/plans/2026-09-14-satisfactory-m1b-structs-and-hologram-rules.md`) are the record of what exists. `.superpowers/sdd/m2-interface-reference.md` (git-ignored, in the worktree) quotes every signature this plan builds on; implementers read the sections their task names.

## Global Constraints

Binding user rulings, carried into every brief and review as compliance checks:

1. **Game data over inference.** Every game fact comes from Docs.json, the cooked assets, the public headers or the shipped binary via its PDB, with the source cited. The blueprint corpus is never a source, cross-check, corroboration, acceptance, oracle, guard or evidence for a game fact or a legality rule; it is used only as format fixtures (byte identity, decoding coverage, template cloning). Port names are not a source. FactorioLab's dataset and source code are the authority for what FactorioLab computed; they are not game data.
2. **Legality is what the hologram does or allows.** A bound the validator enforces names the rule in `hologram_rules.json` it comes from; only a rule with `effect: "refuse"` turns a placement away; a `partial` rule is a bound the validator may not assume it knows, and no bound is invented in its place. Stricter rules of this project's own are stated as such (belts never pass through belts; the lift step multiple).
3. **FactorioLab's chosen flow is authoritative.** Machine counts, recipes and rates come from the flow export; nothing re-solves the flow. The user's SatisfactorySolver is not used.
4. **Format guarantees hold.** Unknown bytes are never dropped; all 49 fixtures round-trip byte-identically; every emitted blueprint decodes to the placement that produced it.
5. **Process.** Subagents run on opus. Every commit ends with the two attribution lines the session reminder gives. `GIT_EDITOR=true`, always `-m`, explicit-path `git add`, never interactive git. Serena is a shared server: implementers use Read/Edit for edits. Scratch files are prefixed with the task name under the session scratchpad; never `pkill` by pattern. `uv run pytest tests/sfy -q`: the summary line never prints here, the exit code is the signal. The worktree runs `uv sync` first and verifies `flab2bp.__file__` is inside `.claude/worktrees/satisfactory`. Generated JSON is written only by its script; the drift tests must pass. Ruff (`E,F,I,UP,B,SIM`, 100 columns) and mypy strict over `src` and `tests`.
6. **Units.** Centimetres, degrees, items per second (`Fraction`) inside the spec and rates; the game's Unreal frame (Z up, yaw turns +X toward +Y). Every placement coordinate is a float in cm; no float reaches the rates.

Controller rulings that shape M2 (recorded here so no implementer re-decides them):

- **R1. The flow export is required on the sfy path.** M2 does not port the MILP: `pipeline.build` for an `sfy` URL needs `--flow` or `--fetch-flow`, and refuses otherwise with the message naming both. Ruling 3 makes this the only honest choice.
- **R2. Belts only.** Lifts, passthroughs and the stacking contract are M3. Where a belt cannot make a turn or a crossing under the rules, M2 refuses with the cause named. Zero-footprint lift turns are M3's first item.
- **R3. Faces.** In M2's single blueprint, external inputs enter through the `-Y` wall and every output leaves through the `+Y` wall. M3 moves inputs to the bus wall.
- **R4. Fluids refuse.** A flow that carries a fluid item (a lab item with no `stack`) or a recipe whose producer has a pipe port refuses with the cause `fluids are M4`.
- **R5. Belt demand above the ceiling refuses** (`run exceeds the belt ceiling`), naming the run and the tier; splitting runs is M3.
- **R6. One candidate per URL.** There is no proliferation trade-off, so `SfyBuildSpec` is one spec, not a set.
- **R7. Machine pitch keeps one grid cell between hard clearance boxes.** `buildable.clearance` is `partial`; touching boxes are not assumed legal. This is stricter than the game may be and is stated as ours.
- **R8. Curvature is enforced everywhere.** `belt.curvature` refuses only in the curve build mode; this project applies the same bound to every belt it authors, as its own stricter rule, because a blueprint carries no build mode.
- **R9. Power in M2 is poles and wires where the wire can be authored from game data**; if the wire's custom serialization cannot be decoded from the binary in Task 9, poles are placed without wires, the report says so, and M4 completes it.

---

## File structure

| File | Responsibility |
|---|---|
| `src/flab2bp/lab/url.py` | `Game` enum; `parse_url` accepts `dsp` and `sfy`; `LabRequest.game` |
| `src/flab2bp/lab/data.py` | per-game dataset and hash URLs and vendored paths; `load_vendored(game)` |
| `src/flab2bp/lab/params.py` | `load_mod_hash(game)` default |
| `src/flab2bp/lab/schema.py` | `Pipe` on items; `minPipe`/`maxPipe` defaults |
| `src/flab2bp/lab/vendored/sfy/data.json`, `hash.json` | the FactorioLab sfy dataset (vendored) |
| `src/flab2bp/sfy/labmap.py`, `src/flab2bp/sfy/data/lab_map.json`, `scripts/sfy_lab_map.py` | lab id to game class tables, derived and drift-tested |
| `src/flab2bp/sfy/spec.py` | `Designer`, `SfyMachineGroup`, `SfyBuildSpec` |
| `src/flab2bp/sfy/rates.py` | `spec_from_flow` |
| `src/flab2bp/sfy/layout/model.py` | placement types in cm |
| `src/flab2bp/sfy/layout/splines.py` | yaw quaternions, Hermite evaluation, straight/turn/incline builders, arc length as the engine computes it |
| `src/flab2bp/sfy/layout/emit.py` | placement to `Blueprint` through the templates |
| `src/flab2bp/sfy/layout/validate.py` | the sfy validator (check registry, findings, report) |
| `src/flab2bp/sfy/layout/manifold.py` | one row: machines, splitter chains, merger chain, feeders |
| `src/flab2bp/sfy/layout/corridors.py` | column assignment, trunks, bridges, entries and exits |
| `src/flab2bp/sfy/layout/strategy.py` | `ManifoldRows` (`LayoutStrategy` for sfy) |
| `src/flab2bp/sfy/layout/power.py` | poles and wires |
| `src/flab2bp/sfy/pipeline.py` | `build(url, designer, flow...) -> SfyBuild` |
| `src/flab2bp/cli.py`, `src/flab2bp/web/jobs.py` | game dispatch, `--designer`, `-o DIR`, web URL validation |
| `src/flab2bp/bench/sfy_corpus.py`, `scripts/sfy_audit.py`, `tests/fixtures/sfy_flows/` | the sfy corpus gate |
| `scripts/sfy_checkpoint2.py` | checkpoint 2 emitter and self-checks |
| `docs/sfy-layout-model.md`, `docs/sfy-regenerating-game-data.md` | the layout model; regeneration steps for the new game facts |

Review batches (user ruling: long plans get batch reviews): **A** = Tasks 1 and 2; **B** = Tasks 3 and 4; **C** = Tasks 5 and 6; **D** = Tasks 7 and 8; **E** = Tasks 9 and 10; **F** = Tasks 11 and 12; then the final whole-branch review.

---

### Task 1: `Game` in `lab/` and the vendored sfy dataset

**Files:**
- Modify: `src/flab2bp/lab/url.py` (`DSP_MOD_ID`, `parse_url` gate at the `mod_id` check, `LabRequest`)
- Modify: `src/flab2bp/lab/data.py` (`DATA_URL`, `HASH_URL`, `load_dataset`, `load_hash_index`, `load_vendored`, `load_vendored_hash_index`)
- Modify: `src/flab2bp/lab/params.py` (`load_mod_hash` default), `src/flab2bp/lab/schema.py` (`_RawItem.pipe`, `Pipe`, `Item.pipe`, `_RawDefaults.min_pipe/max_pipe`, `Defaults`)
- Create: `src/flab2bp/lab/vendored/sfy/data.json`, `src/flab2bp/lab/vendored/sfy/hash.json`
- Modify: `pyproject.toml` package-data (`lab/vendored/*/*.json`)
- Test: `tests/lab/test_game.py` (new), existing `tests/lab/*` stay green

**Interfaces:**
- Produces: `Game` (`StrEnum`: `DSP = "dsp"`, `SFY = "sfy"`), `LabRequest.game: Game` (property over `mod_id`), `load_vendored(game: Game = Game.DSP) -> Dataset`, `load_vendored_hash_index(game)`, `load_dataset(path=None, *, game=Game.DSP, ...)`, `load_hash_index(..., game=...)`, `Dataset.pipe_speed(item_id) -> Fraction`, `Defaults.min_pipe`, `Defaults.max_pipe`.
- Consumes: nothing new.

- [ ] **Step 1: Vendor the dataset**

Fetch `https://factoriolab.github.io/data/sfy/data.json` and `https://factoriolab.github.io/data/sfy/hash.json` into `src/flab2bp/lab/vendored/sfy/`, byte for byte (no reformatting). Record their sha256 and the fetch date in `src/flab2bp/lab/vendored/sfy/SOURCE.md`. Add `"lab/vendored/*/*.json"` to `[tool.setuptools.package-data]` and keep the flat DSP glob.

- [ ] **Step 2: Write the failing tests**

```python
# tests/lab/test_game.py
from fractions import Fraction

import pytest

from flab2bp.lab.data import load_vendored, load_vendored_hash_index
from flab2bp.lab.url import Game, UnsupportedDatasetError, parse_url


def test_an_sfy_url_parses_and_names_its_game() -> None:
    request = parse_url("https://factoriolab.github.io/sfy/list?o=iron-plate*60")
    assert request.game is Game.SFY
    assert request.mod_id == "sfy"


def test_a_dataset_nobody_supports_is_still_refused() -> None:
    with pytest.raises(UnsupportedDatasetError):
        parse_url("https://factoriolab.github.io/factorio/list?o=iron-plate*60")


def test_the_vendored_sfy_dataset_carries_belts_pipes_and_the_six_flags() -> None:
    data = load_vendored(Game.SFY)
    assert data.belt_speed("conveyor-belt-mk3") == Fraction(9, 2)
    assert data.pipe_speed("pipeline-mk2") == Fraction(10)
    assert data.defaults.min_pipe == "pipeline-mk1"
    assert data.defaults.max_belt == "conveyor-belt-mk5"
    assert {"overclock", "somersloop", "resourcePurity", "power", "consumptionAsDrain"} <= set(
        data.flags
    )


def test_the_sfy_hash_tables_index_pipes_among_belts() -> None:
    tables = load_vendored_hash_index(Game.SFY)
    assert tables.belts[5:7] == ["pipeline-mk1", "pipeline-mk2"]


def test_the_dsp_dataset_is_still_the_default() -> None:
    assert load_vendored() is load_vendored(Game.DSP)
```

Run: `uv run pytest tests/lab/test_game.py -q; echo exit=$?` Expected: non-zero (no `Game`).

- [ ] **Step 3: Implement**

In `url.py`: `class Game(StrEnum): DSP = "dsp"; SFY = "sfy"`; keep `DSP_MOD_ID = Game.DSP.value` for existing imports; the gate becomes `if mod_id not in {g.value for g in Game}: raise UnsupportedDatasetError(...)` with a message naming both games; `LabRequest` gains `@property def game(self) -> Game: return Game(self.mod_id)`. In `data.py`: `def data_url(game: Game) -> str` and `hash_url(game)` returning `f"https://factoriolab.github.io/data/{game.value}/data.json"`; `load_dataset`/`load_hash_index` take `game: Game = Game.DSP` and pass `vendored_name = "data.json" if game is Game.DSP else f"{game.value}/data.json"`; `load_vendored` becomes `@cache def load_vendored(game: Game = Game.DSP)` reading `VENDORED_DIR / game.value / "data.json"` for non-DSP. In `params.py`: `load_mod_hash(mod_id: str = Game.DSP.value, ...)` unchanged in behaviour (the `sfy/hash.json` path already matches `_candidate_paths`). In `schema.py`: `_RawPipe(speed)`, `Pipe(speed: Fraction)` parsed like `Belt`, `Item.pipe: Pipe | None`, `Dataset.pipe_speed(item_id)` mirroring `belt_speed`; `_RawDefaults.min_pipe = Field(None, alias="minPipe")`, `max_pipe`, and the two on `Defaults`.

- [ ] **Step 4: Run the tests and the whole lab suite**

`uv run pytest tests/lab -q; echo exit=$?` Expected: 0. Then `uv run pytest tests -q -x --deselect tests/test_pipeline.py; echo exit=$?` for the rest (the four known strategy-race failures live in `tests/test_pipeline.py`).

- [ ] **Step 5: Commit**

`git add src/flab2bp/lab/url.py src/flab2bp/lab/data.py src/flab2bp/lab/params.py src/flab2bp/lab/schema.py src/flab2bp/lab/vendored/sfy pyproject.toml tests/lab/test_game.py` and commit: `Accept FactorioLab sfy URLs and vendor the sfy dataset`.

---

### Task 2: lab id to game class tables

**Files:**
- Create: `scripts/sfy_lab_map.py`, `src/flab2bp/sfy/labmap.py`, `src/flab2bp/sfy/data/lab_map.json`
- Test: `tests/sfy/test_labmap.py`
- Modify: `docs/sfy-regenerating-game-data.md` (a step 8: `lab_map`)

**Interfaces:**
- Consumes: `flab2bp.lab.data.load_vendored(Game.SFY)`, `flab2bp.sfy.registry.load_registry`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class LabMap:
    items: dict[str, str]        # lab item id -> Desc_*_C (solid and fluid alike)
    recipes: dict[str, str]      # lab recipe id -> Recipe_*_C
    machines: dict[str, str]     # lab machine item id -> Build_*_C
    unmapped_recipes: dict[str, str]  # lab recipe id -> why (mining, phase-N, ...)
    provenance: dict[str, Any]

def load_lab_map(path: Path | None = None) -> LabMap
def item_class(m: LabMap, lab_item_id: str) -> str      # KeyError with the id named
def recipe_class(m: LabMap, lab_recipe_id: str) -> str
def machine_class(m: LabMap, lab_machine_id: str) -> str
```

- [ ] **Step 1: Failing tests**

```python
# tests/sfy/test_labmap.py
import json
import subprocess
import sys
from pathlib import Path

from flab2bp.sfy.labmap import LAB_MAP_PATH, item_class, load_lab_map, machine_class, recipe_class


def test_the_three_worked_examples_map_the_way_the_game_names_them() -> None:
    m = load_lab_map()
    assert recipe_class(m, "iron-ingot") == "Recipe_IngotIron_C"
    assert recipe_class(m, "screw-cast") == "Recipe_Alternate_Screw_C"
    assert recipe_class(m, "reinforced-iron-plate-bolted") == "Recipe_Alternate_ReinforcedIronPlate_1_C"
    assert item_class(m, "screw") == "Desc_IronScrew_C"
    assert item_class(m, "iron-ore") == "Desc_OreIron_C"
    assert machine_class(m, "refinery") == "Build_OilRefinery_C"
    assert machine_class(m, "particle-accelerator") == "Build_HadronCollider_C"


def test_every_craftable_lab_recipe_maps_or_says_why_not() -> None:
    from flab2bp.lab.data import load_vendored
    from flab2bp.lab.url import Game

    m = load_lab_map()
    for recipe in load_vendored(Game.SFY).recipes:
        assert recipe.id in m.recipes or recipe.id in m.unmapped_recipes, recipe.id
    assert set(m.unmapped_recipes) >= {"iron-ore", "phase-1", "uranium-waste"}


def test_every_mapped_class_exists_in_the_registry() -> None:
    from flab2bp.sfy.registry import load_registry

    m, reg = load_lab_map(), load_registry()
    assert set(m.recipes.values()) <= set(reg.recipes)
    assert set(m.items.values()) <= set(reg.item_paths)
    assert set(m.machines.values()) <= set(reg.buildables)


def test_the_committed_map_is_what_the_script_derives(tmp_path: Path) -> None:
    out = tmp_path / "lab_map.json"
    subprocess.run(
        [sys.executable, "scripts/sfy_lab_map.py", "--out", str(out)], check=True
    )
    fresh, committed = json.loads(out.read_text()), json.loads(LAB_MAP_PATH.read_text())
    fresh.pop("provenance"), committed.pop("provenance")
    assert fresh == committed
```

- [ ] **Step 2: The derivation script**

`scripts/sfy_lab_map.py --out PATH` (default `src/flab2bp/sfy/data/lab_map.json`):

1. **Machines**: the fixed table from the interface reference section 3.7 (19 entries), each asserted present in `registry.buildables`.
2. **Items**: for each lab item, candidates are registry descriptor classes whose Docs.json display name equals the lab `name` (case-insensitive, after stripping a trailing `s` on one side only when the other side has none). Overrides for the known drifts live in an `ITEM_OVERRIDES` dict in the script with a comment per line stating the two names (`screw` -> `Desc_IronScrew_C`, ...). A lab item with zero or more than one candidate and no override fails the run with the candidates listed; the script never guesses.
3. **Recipes**: for each lab recipe that is not `mining`, compute the signature `(machine_class(producers[0]), time, sorted((item_class(i), amount) for inputs), sorted(outputs))`, with fluid amounts multiplied by 1000 (the lab states m3, the game centilitres; a fluid is a lab item with no `stack`). Match against the registry recipes' `(producers[0], duration_s, ingredients, products)`. Exactly one hit maps; zero or several fail the run with the candidates listed, unless the id is in `UNMAPPED` (mining recipes, `phase-1`..`phase-5`, `uranium-waste`, `plutonium-waste`, `battery` and any other the run proves unmatched) with a reason string.
4. Write `{"items", "recipes", "machines", "unmapped_recipes", "provenance": {"lab_dataset_sha256", "registry_inputs_sha256", "derived": <date>}}` with sorted keys.

- [ ] **Step 3: `labmap.py` loader** with `RulesError`-style `LabMapError`, refusing a payload missing a section.

- [ ] **Step 4: Run tests** (`uv run pytest tests/sfy/test_labmap.py -q; echo exit=$?` → 0), add the runbook step, commit: `Derive the FactorioLab-to-game id tables and drift-test them`.

---

### Task 3: `SfyBuildSpec` and rates from the flow

**Files:**
- Create: `src/flab2bp/sfy/spec.py`, `src/flab2bp/sfy/rates.py`
- Test: `tests/sfy/test_spec.py`, `tests/sfy/test_rates.py`, fixture flows under `tests/fixtures/sfy_flows/` (see Step 1)

**Interfaces:**
- Consumes: `LabRequest` (Task 1), `FlowSelection` (`flab2bp.lab.flow`), `Dataset`, `LabMap` (Task 2), `Registry`.
- Produces:

```python
# src/flab2bp/sfy/spec.py
class Designer(_Frozen):
    mark: Literal["mk1", "mk2", "mk3"]
    dims: tuple[int, int, int]          # foundations of 800 cm, from registry designer_dims
    @property
    def half_cm(self) -> float: return self.dims[0] * 800.0 / 2
    @property
    def height_cm(self) -> float: return self.dims[2] * 800.0

def designer(mark: str, registry: Registry) -> Designer  # reads Build_BlueprintDesigner_C / _MK2_C / _Mk3_C

class SfyMachineGroup(_Frozen):
    recipe_id: str; recipe_class: str; machine_item_id: str; machine_class: str
    count: int = Field(gt=0)
    clock: Fraction = Field(gt=0, le=Fraction(5, 2))       # every machine but the last
    last_clock: Fraction = Field(gt=0)                     # the last machine's clock
    somersloops: int = Field(ge=0)
    power_shards_per_machine: int = Field(ge=0)
    inputs_per_machine: dict[str, Fraction]                # items/s at `clock`
    outputs_per_machine: dict[str, Fraction]
    power_mw_per_machine: Fraction
    @property
    def row_inputs(self) -> dict[str, Fraction]: ...       # (count-1)*r + r*last_clock/clock
    @property
    def row_outputs(self) -> dict[str, Fraction]: ...

class SfyBuildSpec(_Frozen):
    groups: tuple[SfyMachineGroup, ...]
    external_inputs: dict[str, Fraction]
    outputs: dict[str, Fraction]
    surplus_outputs: dict[str, Fraction]
    belt_item_id: str; belt_items_per_second: Fraction      # floor (URL `ibe`, else defaults.minBelt)
    belt_upgrades: tuple[BeltTier, ...]                     # up to the URL max belt, else defaults.maxBelt
    label: str = ""
    # validators: _no_dangling_demand (copied from spec.py), _tiers_are_ordered, _last_clock_le_clock
    @property
    def machine_count(self) -> int
    @property
    def belt_tiers(self) -> tuple[BeltTier, ...]
    @property
    def power_mw(self) -> Fraction
```

```python
# src/flab2bp/sfy/rates.py
class RatesRefusal(ValueError): ...   # carries .cause: str

def spec_from_flow(
    data: Dataset, request: LabRequest, flow: FlowSelection, registry: Registry, labmap: LabMap
) -> SfyBuildSpec
```

- [ ] **Step 1: Flow fixtures**

Capture two FactorioLab sfy exports with the existing `flab2bp.lab.capture.capture_flow_csv` (headless browser) and commit them as `tests/fixtures/sfy_flows/iron-plate-60.csv` and `tests/fixtures/sfy_flows/reinforced-iron-plate-10.csv`, with a `MANIFEST.json` recording each URL. URLs: `https://factoriolab.github.io/sfy/list?o=iron-plate*60` and `https://factoriolab.github.io/sfy/list?o=reinforced-iron-plate*10`. If capture fails on this box, report BLOCKED with the capture error; do not hand-write a CSV.

- [ ] **Step 2: Failing tests**

```python
# tests/sfy/test_rates.py (excerpt)
def test_iron_plate_at_sixty_is_two_constructors_with_the_last_underclocked() -> None:
    spec = _spec("iron-plate-60")
    plate = next(g for g in spec.groups if g.recipe_id == "iron-plate")
    assert plate.machine_class == "Build_ConstructorMk1_C"
    assert plate.recipe_class == "Recipe_IronPlate_C"
    assert plate.count == 3                       # 60/min needs 3 constructors at 20/min
    assert plate.clock == 1 and plate.last_clock == 1
    assert plate.row_outputs["iron-plate"] == Fraction(1)   # 60/min = 1/s


def test_a_fractional_machine_count_rounds_up_and_underclocks_the_last_machine() -> None:
    spec = _spec("reinforced-iron-plate-10")
    rip = next(g for g in spec.groups if g.recipe_id == "reinforced-iron-plate")
    assert rip.count == 2 and rip.last_clock == Fraction(1, 5)   # 10/min needs 2 assemblers at 5/min
    assert rip.row_outputs["reinforced-iron-plate"] == Fraction(1, 6)


def test_the_row_rates_agree_with_the_flow_to_the_fraction() -> None: ...
def test_external_inputs_are_the_flows_mined_items() -> None: ...
def test_a_fluid_in_the_flow_refuses_with_the_cause_named() -> None: ...   # cause == "fluids are M4"
def test_the_belt_floor_and_ceiling_come_from_the_url_or_the_defaults() -> None: ...
def test_overclock_above_one_is_carried_and_shards_are_counted() -> None: ...
def test_somersloops_double_output_the_way_factoriolab_computes_it() -> None: ...
```

The exact expected counts in the first two tests are derived from the vendored dataset (`iron-plate`: time 6, 3 ingots to 2 plates, so 20/min per constructor; `reinforced-iron-plate`: time 12, 1 per craft, 5/min per assembler) and must match the flow's `Machines` column exactly, which the third test pins.

- [ ] **Step 3: Implement `rates.py`**

Per flow row with a `recipe_id` and `machines > 0`:
- `recipe = data.recipe(row.recipe_id)`; `machine_item_id = row.machine_item_id or select_machine(data, recipe, request.machine_rank_ids)` (reuse `flab2bp.rates.adjust.select_machine`, which is game-neutral).
- `clock`: `request.recipes[rid].overclock`, else `request.machines[mid].overclock`, else `request.overclock`, else `1`.
- `somersloops`: the count of `ModuleSetting` with `id == "somersloop"` on the recipe setting, else on the machine setting.
- `speed = data.machine(machine_item_id).speed or 1`; `crafts_per_second = speed * clock / recipe.time`.
- Somersloop multiplier: implement exactly what FactorioLab computes. Read `factoriolab`'s recipe adjustment (`src/app/utils/recipe.utility.ts`, `adjustRecipe`, the `somersloop` flag branch) from `https://github.com/factoriolab/factoriolab` and transcribe it with the source path and commit cited in the docstring; a fixture-flow test then pins that the row rate equals the flow's. If the two disagree for any fixture, that is a `RatesRefusal` with cause `flow rate disagrees with the rate model` naming the row, never a silent fix.
- `outputs_per_machine[item] = amount * crafts_per_second * boost`; `inputs_per_machine[item] = amount * crafts_per_second`.
- `count = machines_needed(row.machines, 1)` (the ceiling of the fractional count, reusing `flab2bp.rates.machine_choice.machines_needed`'s arithmetic on `row.machines`); `last_clock = clock * (row.machines - (count - 1))`.
- `power_shards_per_machine = ceil((clock - 1) / (1/2))` when `clock > 1` else 0 (the potential per shard is read in Task 4 and replaces the literal there; until then the literal is marked as a lab-side constant).
- `power_mw_per_machine = registry.buildables[machine_class].power_mw * clock ** exponent` with `exponent` from Docs.json `mPowerProductionExponent`/`mProductionBoostPowerConsumptionExponent` (read in Task 4; until then `1`, marked).
- External inputs: `flow.external_items(data)` in items/s (convert from the URL display rate with `request.display_rate`). Outputs: objective items at the flow's produced rate; surplus per `row.surplus`.
- Fluids: any lab item without `stack` in any group's inputs/outputs or in external inputs raises `RatesRefusal("fluids are M4")`.
- Belt tiers: floor = `request.belt_id or data.defaults.min_belt`; upgrades = every belt item faster than the floor up to `data.defaults.max_belt` (the URL's max belt when it names one), slowest first, using `data.belt_speed`.

- [ ] **Step 4: Run, commit**: `Turn a FactorioLab sfy flow into a Satisfactory build spec`.

---

### Task 4: the game facts the geometry still lacks

**Files:**
- Modify: `tools/sfy-extract/Program.cs` (mesh bounds), `scripts/sfy_registry.py`, `src/flab2bp/sfy/registry.py`, `scripts/sfy_native_rules.py`, `src/flab2bp/sfy/rules.py`, `src/flab2bp/sfy/data/*.json` (regenerated), `docs/sfy-regenerating-game-data.md`, `docs/sfy-hologram-rules.md`
- Test: `tests/sfy/test_registry.py`, `tests/sfy/test_rules.py`

**Interfaces:**
- Produces on `Buildable`: `mesh_bounds_cm: tuple[Vector, Vector] | None` (the static mesh's local AABB for belts and lifts, source `assets`); on `Limits`: `belt_min_length_cm` (governed by `belt.min_length`), `potential_per_shard`, `potential_shard_slots_default`, `production_boost_per_slot`, `power_exponent` fields with sources; on `Port`: `max_connections` filled for machine power inputs from the native default of `UFGCircuitConnectionComponent::mMaxNumConnectionLinks` (`direction_source`-style `max_connections_source`).
- Produces in `hologram_rules.json`: `belt.clearance` and `lift.clearance` re-read with the chunk-aware tool (Task 5 of M1b): each either `extracted` with the clearance shape stated (what box or capsule `UpdateClearanceData` builds from the spline and which members size it) or still `partial` with the callee named; a new rule `manufacturer.production_boost` (`compute`) quoting where the boost multiplier is computed (`AFGBuildableManufacturer`'s production boost getter or `Factory_Tick`) and `factory.potential` (`compute`) for the potential-per-shard and the power exponent, each with evidence lines.

- [ ] **Step 1: Failing tests** (`test_registry.py`): belt Mk1 carries `mesh_bounds_cm` with source `assets` and its Y extent is what the cooked `SM_ConveyorBelt` (the class's `mMesh`) bounds say; `limits.belt_min_length_cm` is the constant `belt.min_length` compares against and its source is `binary`; every machine `PowerInput` port has `max_connections` from `native`; `production_boost_per_slot` and `potential_per_shard` have game sources. (`test_rules.py`): the two new rule ids exist with `effect: "compute"` and evidence.

- [ ] **Step 2: Extract**

`sfy-extract`: for each conveyor belt and lift class, follow `mMesh` (or the per-mark mesh property the CDO names) to the `UStaticMesh` and emit `Bounds.BoxExtent`/`Origin` (CUE4Parse exposes `UStaticMesh.RenderData.Bounds`); write to `assets.json` as `mesh_bounds` per class with the mesh asset path. `sfy-native disasm` on `AFGConveyorBeltHologram::UpdateClearanceData` (chunk-aware) and `AFGConveyorLiftHologram::UpdateClearance`; on `AFGBuildableFactory::GetProductionBoost`/`GetCurrentPotential`/`CalcProductionCycleTimeForPotential` and whatever computes `mPowerConsumption * potential ^ exponent` (the header `Buildables/FGBuildableFactory.h` names `mPowerProductionExponent`; Docs.json carries the value per class); on `UFGCircuitConnectionComponent`'s constructor for `mMaxNumConnectionLinks`. `potential_per_shard`: `AFGBuildableFactory::mPotentialShardSlots`/`GetPotentialPerShard` or the Docs.json field `mCanChangePotential`/`mMaxPotential` (Docs.json states `mMaxPotential` per class; the +50 % step is `mPotentialPerShard` if a header declares it; otherwise from the disassembly). Every value lands in the registry with its source; a value that cannot be read stays `None` with the reason in provenance, and the rates code then refuses overclock above 1 with the cause named rather than assuming 50 %.

- [ ] **Step 3: Regenerate, tests green, runbook and rules doc updated, commit**: `Read the belt mesh bounds, the clearance shapes, potential and production boost out of the game`.

---

### Task 5: placement model and spline builders

**Files:**
- Create: `src/flab2bp/sfy/layout/__init__.py`, `model.py`, `splines.py`, `emit.py`
- Test: `tests/sfy/test_layout_model.py`, `tests/sfy/test_splines.py`, `tests/sfy/test_emit.py`

**Interfaces (produces):**

```python
# model.py
Vector = tuple[float, float, float]

@dataclass(frozen=True, slots=True)
class Pose:
    x: float; y: float; z: float; yaw_deg: float          # yaw in {0, 90, 180, -90} for grid objects
    def transform(self) -> Transform                       # rotation = yaw_quaternion(yaw_deg), unit scale

@dataclass(frozen=True, slots=True)
class MachineObj:   id: int; class_name: str; pose: Pose; recipe_class: str; clock: Fraction; somersloops: int
@dataclass(frozen=True, slots=True)
class AttachmentObj: id: int; class_name: str; pose: Pose     # splitter or merger
@dataclass(frozen=True, slots=True)
class PoleObj:       id: int; class_name: str; pose: Pose
@dataclass(frozen=True, slots=True)
class FoundationObj: id: int; class_name: str; pose: Pose
@dataclass(frozen=True, slots=True)
class BeltRun:
    id: int; class_name: str                     # Build_ConveyorBeltMkN_C
    points: tuple[tuple[Vector, Vector, Vector], ...]   # world-frame (location, arrive, leave)
    item_id: str; items_per_second: Fraction
    @property
    def start(self) -> Vector; @property def end(self) -> Vector
    def local_points(self) -> tuple[...]         # relative to points[0].location, for set_spline

@dataclass(frozen=True, slots=True)
class Link:  # one wire: (object id, port name) -> (object id, port name)
    a: tuple[int, str]; b: tuple[int, str]

@dataclass(frozen=True, slots=True)
class WireObj: id: int; class_name: str; link: Link

@dataclass(frozen=True, slots=True)
class SfyPlacement:
    designer: Designer
    machines: tuple[MachineObj, ...]; attachments: tuple[AttachmentObj, ...]
    belts: tuple[BeltRun, ...]; poles: tuple[PoleObj, ...]; wires: tuple[WireObj, ...]
    foundations: tuple[FoundationObj, ...]
    links: tuple[Link, ...]          # every belt-to-port and belt-to-belt connection
    description: str = ""; short_desc: str = ""
    def by_id(self, id: int) -> object
```

```python
# splines.py
def yaw_quaternion(yaw_deg: float) -> Quaternion            # (0, 0, sin(y/2), cos(y/2)), snap_zeros applied
def hermite(p0, t0, p1, t1, t) -> Vector                     # UE's cubic Hermite: (2t^3-3t^2+1)p0 + (t^3-2t^2+t)t0 + (-2t^3+3t^2)p1 + (t^3-t^2)t1
def hermite_tangent(p0, t0, p1, t1, t) -> Vector
def segment_length(p0, t0, p1, t1) -> float                  # 5-point Gauss-Legendre over [0,1], as USplineComponent's FSplineCurves::GetSegmentLength (SplineComponent.cpp)
def spline_length(points) -> float
def straight(start: Vector, direction: Vector, length: float) -> points     # world-frame form of templates.straight_spline
def quarter_turn(start: Vector, heading: Vector, left: bool, radius: float) -> points
    # two points: start and start + radius*heading + radius*side; leave tangent at start = heading * k*radius,
    # arrive tangent at end = new_heading * k*radius, k = 4*(sqrt(2)-1) (the cubic Bezier circle constant times 3, i.e. 1.6569),
    # outer tangents are the unit headings (AddSegment's shape, see belt.straight_tangents)
def incline(start: Vector, heading: Vector, run: float, rise: float) -> points   # straight in 3-D; the caller keeps rise/run <= tan(max incline)
def concat(*runs) -> points                                   # joins runs whose shared point is within 1e-6, keeping the arrive/leave pair
```

```python
# emit.py
def emit(placement: SfyPlacement, registry: Registry, library: TemplateLibrary, labmap: LabMap,
         *, build_version: int, version_data: SaveObjectVersionData | None) -> Blueprint
def decode(bp: Blueprint, registry: Registry) -> SfyPlacement      # the inverse, for the round-trip check
```

- [ ] **Step 1: Failing tests**

`test_splines.py`: `yaw_quaternion(90)` turns `+X` into `(0, 1, 0)` under `quat_rotate` and agrees with `port_forward` on the Constructor's `Output0` at every yaw in `{0, 90, 180, -90}`; `straight` of 400 cm reproduces `templates.straight_spline`'s `(1, 200, 200, 1)` shape once translated; `segment_length` of a straight run equals its chord to 1e-9 and of a `quarter_turn(radius=400)` equals `pi/2 * 400` to within 0.5 %; the minimum radius of curvature of `quarter_turn(radius=400)` sampled every 50 cm (the `belt.curvature` sampling) is above 283.5 cm; `incline(run=500, rise=300)` has slope `atan(300/500) < 35 deg`. `test_layout_model.py`: `Pose.transform()` round-trips through `decode(emit(...))` for one machine. `test_emit.py`: a placement of two foundations, one Constructor running `Recipe_IronPlate_C`, one 400 cm belt on `Output0`, one pole, emitted and decoded, equals the placement; the emitted blueprint's cost equals the checkpoint-1 cost; the belt's `flow.entry` component is linked to the port.

- [ ] **Step 2: Implement** `splines.py` first (pure math), then `model.py`, then `emit.py`: `emit` instantiates each object through `TemplateLibrary.instantiate(class_name, 2_000_000_000 + id, pose.transform())`, applies `apply_recipe` for machines (`registry.recipe_paths[recipe_class]`), sets machine `mCurrentPotential`/production boost only if Task 4 read where the game stores them (else the group's `clock` above 1 is reported, not written), sets belt splines with `set_spline(belt_data, run.local_points())`, wires `links` with `connect` (belt ends use `registry.buildables[belt].flow.entry`/`exit`: the run's first point is the entry, its last the exit, the project's stated assumption from M1b), and calls `assemble(objects, designer.dims, registry, build_version=..., version_data=...)`. `decode` reads the same back: machines from actors with `mCurrentRecipe`, belts from `mSplineData` put through the actor transform, links from `mConnectedComponent` pairs.

- [ ] **Step 3: Run, commit**: `Add the Satisfactory placement model, spline builders and emitter`.

---

### Task 6: the validator

**Files:**
- Create: `src/flab2bp/sfy/layout/validate.py`, `docs/sfy-layout-model.md` (section: what the validator checks, and which rule each check names)
- Test: `tests/sfy/test_validate.py`

**Interfaces:**
- Consumes: `SfyPlacement`, `SfyBuildSpec`, `Registry`, `load_rules()`, `splines`.
- Produces: `Severity`, `Finding`, `Report` (copied in shape from `flab2bp.layout.validate`: `findings`, `checks_run`, `skipped`, `ok`, `errors`), `CHECKS` registry with the `@check(id, needs_spec=..., rule=...)` decorator where `rule` names the `hologram_rules.json` id the check enforces (or `"project"` for this project's own rules), and:

```python
def validate(placement: SfyPlacement, spec: SfyBuildSpec | None, registry: Registry,
             *, rules: Mapping[str, HologramRule] | None = None, only: Iterable[str] | None = None) -> Report
```

Checks (id: what, rule, severity):
- `geom.bounds`: every object origin, every clearance-box corner and every spline point inside `[-half, half]^2 x [0, height]` of the designer (`project`; the designer volume is the game's `mDimensions`).
- `geom.hard_clearance`: no two hard clearance boxes intersect (oriented boxes: apply translation, rotation, scale from `ClearanceBox`; separating-axis test on the 8 corners); soft boxes may intersect soft boxes (`buildable.clearance`, `refuse`, `partial`: the check enforces the intersection the rule's read part states, and reports in `skipped` any pair whose boxes carry `exclude_for_snapping`, which the rule leaves unread).
- `belt.capsule`: every belt run's clearance shape, built from its spline the way Task 4 found `UpdateClearanceData` builds it (a box or capsule per segment sized from the mesh bounds), intersects no other belt's shape and no hard box; the belt's own connection points get a 5 cm tolerance (`project`; the shape from `belt.clearance` if `extracted`, else from `mesh_bounds_cm` with the check named `partial` in `skipped` as well as run).
- `belt.max_length`, `belt.min_length` (`refuse`, extracted): `spline_length` against `limits.belt_max_spline_cm` and `limits.belt_min_length_cm`.
- `belt.incline` (`refuse`): per consecutive point pair, `atan2(|dz|, horizontal)` <= `limits.belt_max_incline_deg`, exactly the pairwise-location computation the rule's comparison records.
- `belt.curvature` (`refuse`, `project` scope per R8): `n = round(L * 0.02)` samples, tangents at `i*L/n` normalised in 2-D, radius of curvature from consecutive samples as the rule's comparison states; refuse below `limits.belt_bend_radius_cm * 1.5 - 15`. The check's docstring quotes the rule's evidence addresses; if the `comparison` text does not settle the radius arithmetic, re-run `disasm` on `ValidateCurvature` and add the derivation to the rule's interpretation before implementing.
- `ports.connected_once`: every belt port on every machine and attachment that the spec needs is linked exactly once; a belt end links exactly once; a `snap_only` or `unknown` direction refuses (`project`).
- `ports.direction`: a link joins an `output` (or `any`) to an `input` (or `any`) and the belt's entry end sits on the upstream side (`belt.snap_directions`, `snap`: the rule states how the hologram assigns a pair's directions; the check enforces the pairing it states).
- `ports.position`: a belt's first point is within 1 cm of the upstream port's world position and its last within 1 cm of the downstream port's, and the belt leaves along `port_forward` within 0.01 rad (`project`, the M1b stated assumption).
- `flow.capacity`: each belt's `items_per_second` <= its tier's speed (`project`, from the lab dataset), and every machine input port receives the group's per-machine input rate.
- `flow.balance` (needs spec): per item, sum of row outputs + external inputs >= sum of row inputs + outputs (`project`).
- `spec.machines` (needs spec): machine classes, counts, recipes and clocks match the spec.
- `slab.under_every_foot`: every machine's hard box floor projection is covered by foundation tops at the machine's z (`project`).
- `power.wires`: every wire shorter than `limits.wire_max_cm` for its class; every port's link count <= `max_connections`; every machine's power port is on a connected pole graph (`project`; skipped when the placement has no wires, with the reason).
- `roundtrip`: `decode(emit(placement)) == placement` (`project`).

- [ ] **Step 1: Failing tests** — one test per check with a placement that passes and a mutation that fails it (a belt shifted 3 cm off its port; a 100 cm-radius turn; a 40 deg incline; two constructors 700 cm apart; a belt through a belt; a Mk1 belt carrying 2 items/s; a missing foundation), plus `test_every_check_names_the_rule_it_enforces_or_says_it_is_ours` and `test_a_partial_rule_is_never_enforced_as_a_bound_it_does_not_state`.

- [ ] **Step 2: Implement**, **Step 3: run, commit**: `Validate Satisfactory placements against the hologram rules and our own`.

---

### Task 7: one manifold row

**Files:**
- Create: `src/flab2bp/sfy/layout/manifold.py`
- Test: `tests/sfy/test_manifold.py`

**Interfaces:**
- Consumes: `SfyMachineGroup`, `Registry` (ports, clearance, `mesh_bounds_cm`, limits), `splines`, `model`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class RowGeometry:
    depth_cm: float                     # Y extent of the row band, inputs face at y=0 of the band
    width_cm: float                     # X extent
    chain_in: tuple[ChainEnd, ...]      # per input item: (port name, Pose of the chain's first splitter Input1, z, item, items/s)
    chain_out: ChainEnd                 # the merger chain's Output1
    objects: SfyPlacement fragment (machines, attachments, belts, links) in the row's own frame

def build_row(group: SfyMachineGroup, registry: Registry, *, flip: bool, belt_tiers: tuple[BeltTier, ...],
              limits: Limits) -> RowGeometry
```

Geometry rules (all numbers from the registry; the plan states the arithmetic, the code reads the values):

1. **Machine line.** Machines at `x_i = i * pitch`, `pitch = grid_ceil(hard_box_x_extent + limits.hologram_grid_cm)` (R7), yaw 0 (input face `-Y`, output face `+Y` per the registry ports). Machine z = slab top (100) so belt ports sit at world z 200.
2. **Input chains.** Input ports sorted by x; chain k (k = 0 nearest the machines) runs along X at `y_k` and belt height `z_k = 200 + k * dz`, `dz = grid_ceil(belt_mesh_height + belt_mesh_height)` (twice the mesh height leaves a full belt of air between crossing centrelines; this is the project's crossing gap). Splitter for machine i on chain k at `(x_i + port_k.x, y_k, z_k)`, yaw 0 when `flip` is False (feeder from `Output2` on the `+Y` side) and yaw 180 when True (feeder from `Output3`). Feeder from the splitter side port to `Input_k` is `straight` (k = 0) or `incline` (k > 0) descending `k * dz` over `port.y - (y_k + 100)`, which must be `>= k*dz / tan(max_incline)`; `y_k` is the largest grid value satisfying that and `y_0 = port.y - 100 - grid_ceil(belt_min_length)`. At every crossing of a lower chain `j < k` at `y_j`, the feeder's height there must exceed `z_j + belt_mesh_height`; if not, `y_k` moves outward one grid step and the test repeats (bounded by the designer half-width; beyond it the row refuses with `row too deep`).
3. **Chain belts.** Between consecutive splitters on a chain: `straight` of `pitch - 200` (the splitter is 200 long, ports at `+-100`), which must be `>= belt_min_length`. The first splitter's `Input1` is the chain end (`ChainEnd`); the last splitter's `Output1` is left unconnected only if the registry says an unconnected output is legal (it is: an unwired connection carries no error in the game; the validator's `ports.connected_once` exempts the last splitter's straight-through port and the last merger's spare inputs, stated in its docstring).
4. **Merger chain.** Along X at `y_m = port_out.y + 100 + grid_ceil(belt_min_length)`, z 200, yaw 0 (feeder into `Input3` from `-Y`) or 180 (`Input2`); mergers at `x_i + port_out.x`; between mergers `straight` of `pitch - 200`; the last merger's `Output1` is the row's `chain_out`.
5. **Belt tiers.** Every belt on chain k carries the whole row's demand of its item downstream of the chain start minus what earlier machines took: the belt entering splitter i carries `row_input * (count - i) / count` items/s; the merger chain likewise accumulates; each belt takes the slowest tier `>= its rate`, refusing per R5.
6. **Row band.** `depth_cm = y_m + 100 - y_last_chain + belt_mesh_width`; the row's `+Y` face is the merger chain, its `-Y` face the outermost chain; `flip` mirrors x for odd rows (the chain input at the `+X` end).

- [ ] **Step 1: Failing tests**: a Constructor row of 3 (one chain: 3 splitters, 2 chain belts of `pitch-200`, 3 feeders, 3 mergers) validates clean; an Assembler row of 2 (two chains at heights 200 and `200+dz`, the outer chain's feeders descend under 35 deg and clear the inner chain by a belt height) validates clean; a Manufacturer row of 1 (four chains) validates clean or refuses `row too deep` for Mk1 with the number; belt tiers step up when a row's demand exceeds Mk1; `test_every_position_is_a_registry_port_or_a_grid_multiple`.

- [ ] **Step 2: Implement**, **Step 3: commit**: `Lay out one manifold row from the registry's ports and boxes`.

---

### Task 8: rows, corridors, trunks and bridges: the `ManifoldRows` strategy

**Files:**
- Create: `src/flab2bp/sfy/layout/corridors.py`, `src/flab2bp/sfy/layout/strategy.py`
- Test: `tests/sfy/test_corridors.py`, `tests/sfy/test_strategy.py`
- Modify: `docs/sfy-layout-model.md` (rows, corridors, bridges, refusal causes)

**Interfaces:**

```python
class SfyLayoutStrategy(Protocol):
    name: str
    def lay_out(self, spec: SfyBuildSpec, designer: Designer, *, time_budget_s: float = 15.0,
                absolute_deadline: float | None = None) -> SfyPlacement   # raises NoValidLayout (reused from flab2bp.layout.base)

class ManifoldRows:   name = "manifold-rows"
```

Algorithm:

1. **Row order.** Groups in topological order of the item graph (a group producing an item another consumes comes first; ties by machine count descending). Row r occupies the Y band `[y_r, y_r + depth_r]` with `y_0 = -half + entry_margin` and a gap of one grid cell between bands; even rows are unflipped (chain input at the `-X` end, merger output at the `+X` end), odd rows flipped.
2. **Corridors.** The left corridor is `x < x_L` and the right `x > x_R` where the rows' machine lines span `[x_L, x_R]` centred on 0; corridor columns are at `x_L - (c+1) * belt_pitch` (left) and the mirror (right), `belt_pitch = grid_ceil(belt_mesh_width + limits.hologram_grid_cm)`.
3. **Nets.** For every item: sources = external entry (at the `-Y` wall) and producing rows' `chain_out`; sinks = consuming rows' chain ends and the `+Y` wall exit for outputs and surplus. One source to one sink is a trunk. One source to many sinks: a splitter at the source's corridor entry, `Output1` continuing along the corridor, `Output2`/`Output3` turning into the first column; more sinks chain further splitters one grid pitch apart along Y. Many sources to one sink: mergers before the sink's chain end, in the sink row's corridor. Ratios come from back-pressure (the game's splitters balance against full belts); each branch belt's tier is chosen for its share and the validator's `flow.capacity` holds it.
4. **Corridor intervals and columns.** Each corridor belt is an interval `[y_a, y_b]` in one column, entering the corridor with a transverse segment at `y_a` (from a merger end or the `-Y` wall) and leaving it with a transverse at `y_b` (into a chain end or the `+Y` wall). Assign columns innermost first, in order of `y_b`, subject to: a belt B may take column c only if no belt A already in a column `< c` has an interval strictly containing `y_a(B)` or `y_b(B)` unless that transverse is a bridge (step 5). Choose the innermost column where no bridge is needed; otherwise the innermost column where the crossed columns can be bridged.
5. **Bridges.** A transverse segment that must cross occupied inner columns rises to `z_bridge = 200 + dz` inside its own column before the turn (an `incline` of run `>= dz / tan(max_incline)` along Y, which fits inside the row band because every band is at least one machine deep), turns at that height (`quarter_turn` at radius `R = grid_ceil(2 * limits.belt_bend_radius_cm)`), crosses at that height, and descends after the last crossed column: for a turn-in, over the run between the corridor edge and the chain end, landing at the chain's `z_k`; for a turn-out, inside its column. A crossing whose rise cannot fit refuses with `corridor needs a bridge that does not fit`.
6. **Turns.** Every corridor-to-row transition is a `quarter_turn` of radius `R`; the row's chain end is set back from the corridor edge by `R + belt_min_length` so the turn lands on a straight run into `Input1`.
7. **Entries and exits.** External inputs enter at `(column x, -half, 200)` heading `+Y` (the belt's first point on the wall); outputs leave at `(column x, +half, 200)`; both recorded in the placement description as the manifest's precursor (`entry: <item> at x=...`).
8. **Foundations.** A full floor of 8 m foundations covering the designer footprint at z 50.
9. **Budget.** `WorkBudget` from `flab2bp.layout.budget` bounds the column search; on exhaustion `NoValidLayout("corridor assignment exceeded the budget")`.
10. **Refusals** (`NoValidLayout` with `reason`): `rows exceed the designer depth` (sum of bands), `rows exceed the designer width`, `corridor needs a bridge that does not fit`, `row too deep`, `run exceeds the belt ceiling`, `fluids are M4`.

- [ ] **Step 1: Failing tests**: `iron-plate-60` lays out and validates clean in Mk1; `reinforced-iron-plate-10` (five rows: smelter, plate, rod, screw, assembler; the plate/rod split and the plate/screw merge exercise a splitter and a bridge) lays out and validates clean in Mk1 or refuses with a named cause and the test asserts which; column assignment on synthetic intervals never produces an unbridged crossing; a spec too deep for Mk1 refuses `rows exceed the designer depth`.

- [ ] **Step 2: Implement**, **Step 3: commit**: `Lay out manifold rows with corridors, trunks and bridges in one blueprint`.

---

### Task 9: power poles and wires

**Files:**
- Create: `src/flab2bp/sfy/layout/power.py`
- Modify: `src/flab2bp/sfy/trailers.py` and `properties.py` only if the wire's custom serialization is decoded; `src/flab2bp/sfy/layout/emit.py`, `strategy.py`, `validate.py`
- Test: `tests/sfy/test_power.py`, `tests/sfy/test_trailers.py`

- [ ] **Step 1: Decode the wire.** `Build_PowerLine_C` carries a 206 to 230 byte class trailer in the fixtures (M1 recorded it as opaque). Read `Buildables/FGBuildableWire.h` and disassemble `AFGBuildableWire::Serialize` (or the function `PreSaveGame`/`PostLoadGame` that writes `mConnections`), chunk-aware, and state the layout: expected shape is two `FObjectReferenceDisc` connection references plus the wire's world-space endpoints or a spline; every fixture wire must decode into named fields and re-encode byte-identically (the 49-fixture identity test stays the gate). If the layout cannot be settled from the binary, R9 applies: report it, place poles only, and skip the rest of this task's wire steps.
- [ ] **Step 2: Poles.** `Build_PowerPoleMk2_C` (7 connections; template present) one per `ceil(machines / 5)` along each row in the gap between the machine line and the merger chain, on the 50 cm grid the pole's `grid_snap_cm` states, at a Y where its soft box meets no hard box; poles chained pole to pole along the row and row to row; a wire from every machine `PowerInput`/`PowerConnection` port to the nearest pole, every wire `<= limits.wire_max_cm`.
- [ ] **Step 3: Validator** `power.wires` runs (Task 6 registered it as skipped without wires); `emit` writes wires by instantiating the template and setting its decoded connection fields.
- [ ] **Step 4: Tests, commit**: `Power every row with poles and wires authored from the game's wire layout`.

---

### Task 10: pipeline, CLI and web dispatch

**Files:**
- Create: `src/flab2bp/sfy/pipeline.py`
- Modify: `src/flab2bp/cli.py` (game dispatch, `--designer`, `-o` as a directory on the sfy path, `_sfy_report`), `src/flab2bp/web/jobs.py` (`_validate_web_fetch_url` accepts `/{game}/list|flow` for every `Game`; `allowed` gains `designer`)
- Test: `tests/sfy/test_pipeline.py`, `tests/test_cli_sfy.py`, `tests/web/*` stay green

```python
@dataclass(frozen=True, slots=True)
class SfyBuild:
    spec: SfyBuildSpec; placement: SfyPlacement; report: Report; strategy: str
    blueprint: Blueprint; record: BlueprintRecord
    refused: tuple[LayoutAttemptFailure, ...]

def build(url: str, *, designer: str = "mk1", time_budget_s: float = 15.0, flow: Path | None = None,
          flow_text: str | None = None, fetch_flow: bool = False, fetch_timeout_s: float = 90.0,
          browser: str | None = None, name: str = "", dataset: Dataset | None = None) -> SfyBuild
def write(build: SfyBuild, out_dir: Path) -> tuple[Path, Path]     # <name>.sbp and <name>.sbpcfg
```

`build`: `parse_url` (refuse a non-sfy URL with `ValueError`), R1 flow requirement, `flow_from_text`/`load_flow`/capture, `spec_from_flow`, `designer(mark, registry)`, `ManifoldRows().lay_out`, `validate`, `emit`, and a `LayoutAttemptFailure` for an encoding `ValueError` the way `flab2bp.pipeline` does. CLI: after `parse_url`, `Game.SFY` routes to `sfy.pipeline.build` with the same exit codes (0 ok, 1 validation errors withheld unless `--allow-invalid`, 2 bad URL or spec, 3 `NoValidLayout`); `--designer mk1|mk2|mk3`; on the sfy path `-o` is a directory (created) and stdout gets the report; `_sfy_report`: `manifold-rows: N machines in R rows, B belts, P poles; power X MW; shards S; entries [...]; exits [...]; refusals; validation summary` in the DSP report's shape.

- [ ] **Step 1: Failing tests** (CLI parses `--designer`; an sfy URL without a flow exits 2 with the R1 message; `iron-plate-60` with `--flow tests/fixtures/sfy_flows/iron-plate-60.csv -o tmp` writes both files and exits 0; the written `.sbp` decodes to the build's placement; a DSP URL is unaffected).
- [ ] **Step 2: Implement**, **Step 3: commit**: `Build a Satisfactory blueprint from the CLI for an sfy URL`.

---

### Task 11: the sfy corpus gate

**Files:**
- Create: `src/flab2bp/bench/sfy_corpus.py`, `scripts/sfy_audit.py`, `tests/fixtures/sfy_flows/*.csv` (six to eight more captures)
- Test: `tests/sfy/test_corpus.py` (the corpus entries load and their flows verify provenance)

Corpus: early to late tiers, each with its captured flow: `iron-plate*60`, `iron-rod*60`, `screw*120`, `wire*120` + `cable*60` (two objectives), `concrete*60`, `reinforced-iron-plate*10`, `modular-frame*5`, `rotor*10`, `steel-beam*20` (foundry), `smart-plating*5`, and one Manufacturer recipe (`heavy-modular-frame*2`); plus one fluid URL (`plastic*20`) expected to refuse `fluids are M4`. `scripts/sfy_audit.py --designer mk1 --budget 15` runs every entry, verdicts `CLEAN | REFUSED(cause) | INVALID(check counts) | CRASH | NOT RUN`, exit non-zero on any non-CLEAN except an expected refusal; the report table is written under `docs/superpowers/evidence/sfy-m2-audit-<date>.md`. The gate for M2: every entry CLEAN or a named refusal whose cause is one of the R-rulings' causes.

- [ ] Steps: capture flows, write the corpus module and script, run it, commit the evidence: `Add the Satisfactory corpus gate`.

---

### Task 12: checkpoint 2

**Files:**
- Create: `scripts/sfy_checkpoint2.py`
- Output: `out/sfy/checkpoint2-iron-plate.sbp/.sbpcfg` and `out/sfy/checkpoint2-rip.sbp/.sbpcfg` (uncommitted)

The script runs `sfy.pipeline.build` on `iron-plate*60` and `reinforced-iron-plate*10` with the fixture flows, writes the pairs, re-reads them, runs the validator on the decoded placement (must be clean), and prints the paste instructions: designer mark, which wall the inputs enter (`-Y`, items and x positions), where the output leaves, the expected rate (60/min plates), the power draw, and whether the machines are wired. The user pastes both in game and reports whether the chain runs at the flow's rate; that report closes M2.

- [ ] Steps: write the script, run it, send the files to the user with the instructions, commit the script: `Add the checkpoint 2 emitter`.

---

## Self-review

- **Spec coverage.** Section 6 (Game): Task 1. Section 7 (rates, underclock, shards, sloops, belt tiers, power): Tasks 3 and 4. Section 8.1 to 8.2 (space, objects): Tasks 5, 7, 8. Section 8.3 to 8.4 (stacking, lifts): M3 by R2 and R3, stated. Section 9.1 (manifold rows): Tasks 7 and 8; 9.2 and 9.3 are M5. Section 10 (validation): Task 6, every bullet mapped to a check id or to a later milestone (pipes M4, stacking M3, passthroughs M3). Section 11 (CLI): Task 10; web selectors are M3. Section 12 (gates): Task 11 corpus, Task 12 checkpoint 2.
- **Placeholders.** None: every value the code needs is read from the registry, the rules, the lab dataset or a stated project constant; where a game fact is not yet in the registry, Task 4 extracts it first and names the refusal that stands until it does.
- **Type consistency.** `Designer` is defined in Task 3 and consumed by Tasks 5 to 10; `BeltTier` is reused from `flab2bp.spec`; `NoValidLayout`/`LayoutAttemptFailure` are reused from `flab2bp.layout.base`; `Report`/`Finding` shapes are copied, not imported, because the DSP validator's `Placement` type differs.
