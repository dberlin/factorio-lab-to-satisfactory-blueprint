# Satisfactory Milestone 1b: Struct Layouts and Hologram Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decode every struct the corpus carries from game data instead of keeping them as opaque bytes, and replace corpus-derived legality with the rules the game's holograms actually enforce, extracted from the headers and the shipped binary.

**Architecture:** Two user rulings drive this: game constants come from game data (Docs.json, cooked assets, headers, then the binary via its PDB), and legality is what the hologram allows, never what community blueprints contain. The Rust PDB tool gains a `disasm` mode that prints a named function with resolved constants and member offsets; the CUE4Parse extractor dumps the usmap's reflected struct schemas; `properties.py` gains typed decoders for the two custom-serialized structs; a new `hologram_rules.json` records each validation rule with status and disassembly evidence, loaded by `flab2bp.sfy.rules`; the registry's limit semantics are rewritten from those rules and the corpus measurements are demoted to statistics.

**Tech Stack:** Python 3.14 stdlib, pytest, uv; Rust (`pdb`, `iced-x86`, `pelite`) in `tools/sfy-native`; dotnet 10 + CUE4Parse 1.2.2.202609 in `tools/sfy-extract`.

**Spec:** `docs/superpowers/specs/2026-09-13-satisfactory-target-design.md` sections 4.3 (guarantees), 5 (registry), 10 (validation). Task 3 amends section 10 with the hologram ruling.

## Global Constraints

- Branch `satisfactory-legality` in the worktree `.claude/worktrees/satisfactory`, cut from master 0db86236 (Milestone 1 merged). Run `uv sync` there once and verify `uv run python -c "import flab2bp; print(flab2bp.__file__)"` is inside the worktree.
- All 49 fixtures keep round-tripping byte-identically (`tests/sfy/test_codec.py`); unknown bytes are never dropped; malformed bytes raise `ArchiveError`. Tests via `uv run pytest tests/sfy -q`, exit code is the signal.
- Game data over inference: a struct layout or a rule is taken from a header, the usmap, or the binary, with the source cited. Blueprint corpus content is never evidence of legality; it is at most a statistic.
- Every rule or layout that cannot be extracted is recorded as such with the function or struct name and the reason, never guessed. A `BinaryStruct` that survives in the corpus fails the suite.
- No new Python dependencies. Nothing under `src/flab2bp/dsp/`, `layout/`, `lab/` changes. `cargo build --release`, `cargo clippy` and `dotnet build` stay warning-free (NuGet audit warnings excepted).
- Commits: imperative subject, ending with the two attribution lines from the session reminder; `GIT_EDITOR=true`, always `-m`, explicit-path `git add`; never commit `target/`, `bin/`, `obj/`, `out/`.
- Review batches: Task 1 alone; Tasks 2 and 3 together.

---

## File Structure

| Path | Responsibility |
|---|---|
| `tools/sfy-native/src/main.rs` (modify), `tools/sfy-native/README.md` | `disasm` mode: symbol lookup, function bounds from `.pdata`, annotated disassembly |
| `tools/sfy-extract/Program.cs` (modify), `README.md` | `struct_schemas` from the usmap for a given name list |
| `src/flab2bp/sfy/data/struct_schemas.json` | Committed reflected schemas for every struct name the corpus uses |
| `src/flab2bp/sfy/properties.py` (modify) | Typed `InventoryItem` and `PlayerInfoHandle` values; `BinaryStruct` only for names not in the corpus |
| `src/flab2bp/sfy/data/hologram_rules.json` | Per-rule status, function, RVA, members read, constants, comparison, evidence |
| `src/flab2bp/sfy/rules.py` | `HologramRule` dataclasses and `load_rules()` |
| `scripts/sfy_native_rules.py` | Runs the `disasm` mode for the rule functions and assembles `hologram_rules.json` from the tool's JSON output plus the header survey |
| `docs/sfy-hologram-rules.md` | Header survey with line citations and the per-rule interpretation |
| `scripts/sfy_registry.py`, `src/flab2bp/sfy/registry.py`, `data/registry.json` (modify) | Limit semantics from rules; `corpus_statistics` replaces `limits_measured` |
| `scripts/sfy_measure_limits.py`, `data/measured.json` (modify) | Output relabelled as statistics, `legality_evidence: false` |
| `tests/sfy/test_native.py`, `test_properties.py`, `test_registry.py`, new `test_rules.py`, `test_struct_schemas.py` | Gates |
| `docs/sfy-regenerating-game-data.md`, spec section 10 (modify) | Runbook step for rules; the hologram ruling |

---

### Task 1: `disasm` mode in the Rust PDB tool

**Files:**
- Modify: `tools/sfy-native/src/main.rs`, `tools/sfy-native/README.md`
- Test: `cargo test` in `tools/sfy-native`; `tests/sfy/test_native.py` (one new test)

**Interfaces:**
- Consumes: the existing `load_pe`, `open_pdb` (public and global symbols with RVAs, TPI class layouts), `disassemble` helpers in `main.rs`.
- Produces: CLI `sfy-native <dll> <pdb> disasm <symbol-substring> --out <json>`; matches every public/global function symbol whose demangled name contains the substring (case-sensitive); for each match writes
```json
{"symbol": "AFGConveyorBeltHologram::ValidateCurvature", "mangled": "?ValidateCurvature@AFGConveyorBeltHologram@@AEAA_NXZ",
 "rva": "0x...", "size": 412, "size_source": "pdata",
 "instructions": [{"rva": "0x...", "bytes": "f30f10..", "text": "movss xmm0,dword ptr [rbx+848h]",
   "member": {"class": "AFGConveyorBeltHologram", "name": "mBendRadius", "offset": 2120},
   "constant": {"kind": "f32", "value": 199.0, "at": "0x..."},
   "call": "USplineComponent::GetSplineLength"}]}
```
  `member` is present when the instruction addresses `[reg+disp]` where `reg` is an alias of `this` (same tracking as the constructor tracer) and `disp` matches a TPI member of the function's class or a base class; `constant` when a `[rip+K]` operand points into `.rdata` (decode as f32, f64 and i32 and include all three, plus the raw 16 bytes); `call` when a `call rel32` target resolves to a symbol. Function bounds come from the `.pdata` entry (`RUNTIME_FUNCTION`) covering the RVA, falling back to first-`ret` with `"size_source": "ret"`. Output is deterministic (sorted by RVA).

- [ ] **Step 1: Write the failing integration test**

Append to `tests/sfy/test_native.py`:
```python
def test_disasm_of_validate_curvature_reads_the_bend_radius(tmp_path):
    """The disasm mode must find the belt hologram's curvature check and see it read mBendRadius."""
    import json
    import shutil
    import subprocess

    dll, pdb = _dll_and_pdb()  # helper: paths from FLAB2BP_SATISFACTORY_DIR; skip if absent
    exe = shutil.which("cargo")
    if exe is None or dll is None:
        pytest.skip("cargo or the game install is not available")
    out = tmp_path / "vc.json"
    subprocess.run(["cargo", "run", "--release", "--quiet", "--", str(dll), str(pdb), "disasm",
                    "AFGConveyorBeltHologram::ValidateCurvature", "--out", str(out)],
                   cwd="tools/sfy-native", check=True, timeout=600)
    data = json.loads(out.read_text())
    assert len(data) == 1
    fn = data[0]
    assert fn["size_source"] == "pdata"
    members = {i["member"]["name"] for i in fn["instructions"] if i.get("member")}
    assert "mBendRadius" in members
```
Add `_dll_and_pdb()` returning the two paths under `FLAB2BP_SATISFACTORY_DIR` (default `~/Satisfactory`) or `(None, None)`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_native.py -q -k disasm`
Expected: non-zero exit (unknown mode `disasm`).

- [ ] **Step 3: Implement**

In `main.rs`: parse the `disasm` subcommand; `find_functions(substring)` over public symbols (`pdb::SymbolData::Public`) and procedure symbols in the global stream (`SymbolData::Procedure`), demangling with the existing helper (or `msvc_demangler` if already a dependency; otherwise keep the mangled name and derive `Class::Method` from `?Method@Class@@`); `pdata_bounds(rva)` reading `.pdata` `RUNTIME_FUNCTION {BeginAddress, EndAddress, UnwindInfo}` entries; decode with iced-x86 over `[begin, end)`; annotate with the existing `this`-alias tracker (the class comes from the demangled name; look up its TPI layout and base chain), `.rdata` constant decoding, and `call rel32` symbol resolution via the RVA→symbol map. Unit tests: a hand-assembled buffer with `movss xmm0,[rbx+0x848]` annotated against a fake layout; a `call rel32` resolved against a fake symbol map; a `.pdata` lookup.

- [ ] **Step 4: Run the tests**

Run: `cd tools/sfy-native && cargo test && cargo build --release && cargo clippy -- -D warnings; cd ../.. && uv run pytest tests/sfy/test_native.py -q`
Expected: exit 0 everywhere.

- [ ] **Step 5: README and commit**

Document the mode, its JSON shape and the annotation rules in `tools/sfy-native/README.md`.
```bash
git add tools/sfy-native/src/main.rs tools/sfy-native/README.md tests/sfy/test_native.py
git commit -m "Add a disasm mode that annotates a named function with members, constants and calls"
```

**Review batch A ends here (Task 1).**

---

### Task 2: Struct layouts from the game: usmap schemas and the two custom serializers

**Files:**
- Modify: `tools/sfy-extract/Program.cs`, `tools/sfy-extract/README.md`, `scripts/sfy_registry.py` (no: schemas are consumed directly), `src/flab2bp/sfy/properties.py`, `src/flab2bp/sfy/versions.py` (if a version gate is needed)
- Create: `src/flab2bp/sfy/data/struct_schemas.json`, `scripts/sfy_struct_names.py`, `tests/sfy/test_struct_schemas.py`
- Test: `tests/sfy/test_properties.py`, `tests/sfy/test_struct_schemas.py`

**Facts (measured 2026-09-14 over the 49 fixtures):**
- `BinaryStruct` survivors: `InventoryItem` (always 12 bytes, 24,161 occurrences, inside `FGInventoryComponent.mInventoryStacks[].Item`) and `PlayerInfoHandle` (2 bytes in some fixtures, 5 bytes in others; the `BuiltBy` property on buildables). No `Opaque` remains.
- Tagged (nested-list) struct names in the corpus: `FactoryCustomizationColorSlot`, `FactoryCustomizationData`, `FeetOffset`, `GlobalPrefabIconElementSaveData`, `InventoryStack`, `LocalUserNetIdBundle`, `PersistentGlobalIconId`, `PrefabIconElementSaveData`, `PrefabTextElementSaveData`, `SplinePointData`, `SplitterSortRule`, `TopLevelAssetPath`, `Transform`, `WireInstance`.
- `FPlayerInfoHandle::operator<<` is inline in `Source/FactoryGame/Public/Online/PlayerInfoCache.h` (lines ~246-270): gated on `FSaveCustomVersion::NewPlayerInfoHandleSerializationFormat` (57) and `FixNewPlayerInfoHandleSerializationFormat` (58); the members are `uint8 ServiceProvider` and `int32 PlayerInfoTableIndex` (a legacy `uint8` index in the old branch). Derive the exact 2-byte and 5-byte layouts from that code, not from the sizes.
- `FInventoryItem` is declared in `Source/FactoryGame/Public/FGInventoryComponent.h` with `WithSerializer`; its `Serialize` body is in the DLL. Use Task 1's `disasm` on `FInventoryItem::Serialize` to confirm the 12-byte layout (expected shape: an item class reference encoded as an int32 index or two int32s plus a state pointer; read the disassembly, do not assume).

**Interfaces:**
- Produces: `properties.InventoryItem(...)` and `properties.PlayerInfoHandle(service_provider: int, table_index: int, wide_index: bool)` frozen dataclasses with `write()`; both registered in the struct dispatch so `read_struct_value` returns them for those names and re-encodes byte-identically; `properties.KNOWN_CUSTOM_STRUCTS: frozenset[str]`.
- `data/struct_schemas.json`: `{"provenance": {...}, "structs": {"SplinePointData": {"super": null, "fields": [{"name": "Location", "type": "StructProperty", "struct": "Vector"}, ...]}}}` for every name in the corpus lists above plus their super chain.
- `scripts/sfy_struct_names.py`: prints the sorted set of struct names (tagged, binary and array-inner) found in the corpus, one per line; the extractor takes that list.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_struct_schemas.py`:
```python
import json
from pathlib import Path

from flab2bp.sfy import docs
from flab2bp.sfy.codec import read_sbp_file
from flab2bp.sfy.properties import Array, Struct
from tests.sfy.conftest import fixture_paths

SCHEMAS = json.loads((Path(docs.__file__).parent / "data" / "struct_schemas.json").read_text())["structs"]


def _fields(name):
    seen, cur = {}, name
    while cur is not None:
        s = SCHEMAS[cur]
        for f in s["fields"]:
            seen.setdefault(f["name"], f)
        cur = s["super"]
    return seen


def test_every_tagged_struct_field_in_the_corpus_is_in_the_usmap_schema():
    missing = set()
    def visit(v):
        if isinstance(v, Struct):
            fields = _fields(v.name)
            for p in v.fields:
                if p.tag.name not in fields or fields[p.tag.name]["type"] != p.tag.type:
                    missing.add((v.name, p.tag.name, p.tag.type))
                visit(p.value)
        elif isinstance(v, Array):
            for it in v.items:
                visit(it)
    for path in fixture_paths():
        for _, d in read_sbp_file(path).objects:
            for p in d.properties:
                visit(p.value)
    assert not missing, sorted(missing)
```
Add to `tests/sfy/test_properties.py`:
```python
def test_no_binary_struct_survives_in_the_corpus():
    from flab2bp.sfy.properties import BinaryStruct
    left = Counter()
    def visit(v, cls):
        if isinstance(v, BinaryStruct): left[(v.name, cls)] += 1
        elif isinstance(v, Struct): [visit(p.value, cls) for p in v.fields]
        elif isinstance(v, Array): [visit(i, cls) for i in v.items]
    for path in fixture_paths():
        for h, d in read_sbp_file(path).objects:
            for p in d.properties: visit(p.value, h.class_name)
    assert not left, dict(left)


def test_player_info_handle_both_layouts_round_trip():
    from flab2bp.sfy.properties import PlayerInfoHandle
    for raw in (b"\x00\x05", b"\x00\x05\x00\x00\x00"):
        v = read_struct_value(Reader(raw), "PlayerInfoHandle", len(raw), modern=True)
        assert isinstance(v, PlayerInfoHandle)
        w = Writer(); v.write(w)
        assert w.getvalue() == raw
```
(adjust `read_struct_value`'s signature to whatever the module exposes; if it is the private `_read_struct_value`, make a public `read_struct_value(r, name, size, modern)` in this task and note it in the report.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_struct_schemas.py tests/sfy/test_properties.py -q`
Expected: non-zero exit (`struct_schemas.json` missing; `BinaryStruct` survivors).

- [ ] **Step 3: Extract the schemas**

`scripts/sfy_struct_names.py` walks the corpus and prints the names. In `Program.cs` add mode `structs <names-file> <out.json>`: for each name, find the usmap struct via `provider.MappingsContainer.MappingsForGame.Types[name]` (or the equivalent lookup in this CUE4Parse version; use the `list` mode's discovery approach if the API differs), emit `super` (`SuperType`) and `fields` with `name`, `type` (the `PropertyType` enum name mapped to the `*Property` tag spelling), `struct` (for struct fields), `inner` (for arrays/sets), `enum` (for enums), walking the super chain and adding those too. Commit `data/struct_schemas.json` with provenance (`usmap_sha256`, `extracted`).

- [ ] **Step 4: Decode `PlayerInfoHandle` from the header**

Transcribe `operator<<` from `PlayerInfoCache.h` into `PlayerInfoHandle.read/write` with the version gate expressed through the tag size the file carries (2 vs 5 bytes) and cross-checked against the save custom version when the reader has it (the fixtures with the 2-byte form must be the ones whose save version is below `NewPlayerInfoHandleSerializationFormat`; assert that in a corpus test and report the counts). Cite the header lines in the class docstring.

- [ ] **Step 5: Decode `InventoryItem` from the binary**

Run `cargo run --release -- <dll> <pdb> disasm FInventoryItem::Serialize --out fixwave-inventory.json` (scratch), read the annotated disassembly, and write `InventoryItem.read/write` for the 12-byte layout with the evidence (function RVA and the instructions that read/write each field) in the class docstring and in `docs/sfy-hologram-rules.md`'s appendix (or a new `docs/sfy-struct-layouts.md`). Verify byte identity across all 24,161 occurrences via the existing corpus identity tests.

- [ ] **Step 6: Run the suite, README, commit**

Run: `uv run pytest tests/sfy -q`
Expected: exit 0; `test_no_binary_struct_survives_in_the_corpus` passes.
```bash
git add tools/sfy-extract/Program.cs tools/sfy-extract/README.md scripts/sfy_struct_names.py src/flab2bp/sfy/data/struct_schemas.json src/flab2bp/sfy/properties.py tests/sfy/test_struct_schemas.py tests/sfy/test_properties.py docs/
git commit -m "Decode InventoryItem and PlayerInfoHandle from the game and check tagged structs against the usmap"
```

---

### Task 3: Hologram rules from the headers and the binary

**Files:**
- Create: `docs/sfy-hologram-rules.md`, `scripts/sfy_native_rules.py`, `src/flab2bp/sfy/data/hologram_rules.json`, `src/flab2bp/sfy/rules.py`, `tests/sfy/test_rules.py`
- Modify: `scripts/sfy_registry.py`, `src/flab2bp/sfy/registry.py`, `src/flab2bp/sfy/data/registry.json`, `scripts/sfy_measure_limits.py`, `src/flab2bp/sfy/data/measured.json`, `tests/sfy/test_registry.py`, `docs/sfy-regenerating-game-data.md`, `docs/superpowers/specs/2026-09-13-satisfactory-target-design.md` (section 10)

**Facts:** the hologram headers (`Headers.zip`, `Source/FactoryGame/Public/Hologram/`) declare, for belts: `IsValidHitResult`, `TrySnapToActor`, `CheckValidFloor`, `CheckValidPlacement`, `ValidateConveyorBelt`, `ValidateIncline`, `ValidateMinLength`, `ValidateCurvature`, `GenerateAndUpdateSpline`, `AutoRouteSpline`, `UpdateSplineComponent`, `UpdateClearanceData`, `ShouldIgnoreClearanceCheckForActor`; for pipes: `ValidatePipeline`, `ValidateMinLength`, `ValidateCurvatureAndReturnFaultyPosition`, `ValidateFluidRequirements`, the `AutoRoute*`/`HorizontalAndVerticalRouteSpline*`/`PathFindingRouteSpline` family; for lifts: `IsValidHitResult`, `CheckValidFloor`, `CheckValidPlacement`, `UpdateClearance`, `GetClearanceData`; base: `AFGHologram::IsValidHitResult`, `UpdateHologramPlacement`, `AFGBuildableHologram::CheckValidPlacement`/clearance and snap handling. Members already known from Task 13: `mBendRadius` (offset 2120, 199.0), `mMaxIncline` (2128, 35.0), `mMaxSplineLength` (2124, 5600.1), lift `mStepHeight`/`mMinimumHeight`/`mMaximumHeight`/`mMinimumHeightWithVerticalConnection` (0x8E0..0x8EC), `mGridSnapSize` (1260, 100.0).

**Interfaces:**
- `data/hologram_rules.json`:
```json
{"provenance": {"dll_sha256": "...", "pdb_guid": "...", "headers_sha256": "...", "extracted": "2026-09-14"},
 "rules": [{"id": "belt.curvature", "class": "AFGConveyorBeltHologram", "function": "ValidateCurvature", "rva": "0x...",
   "status": "extracted", "reads": ["mBendRadius"], "constants": [], "calls": ["..."],
   "comparison": "for each spline segment: radius_of_curvature < mBendRadius -> invalid (ucomiss + jb at 0x...)",
   "interpretation": "The hologram rejects any turn tighter than mBendRadius (199 cm). The corpus minimum of 129.8 cm was not built with the hologram.",
   "evidence": ["0x...: movss xmm0,dword ptr [rbx+848h]  ; mBendRadius", "0x...: comiss xmm1,xmm0", "0x...: jb 0x..."],
   "header": "Hologram/FGConveyorBeltHologram.h:105"}]}
```
  Required rule ids (each present with a status of `extracted`, `partial` or `unextractable` and a reason): `belt.curvature`, `belt.incline`, `belt.min_length`, `belt.max_length`, `belt.clearance`, `belt.snap_directions`, `pipe.min_length`, `pipe.curvature`, `pipe.max_length`, `pipe.fluid_requirements`, `lift.height_range`, `lift.step`, `lift.placement`, `lift.clearance`, `buildable.grid_snap`, `buildable.rotation_step`, `buildable.clearance`.
- `rules.py`: `HologramRule(id, cls, function, rva, status, reads, constants, calls, comparison, interpretation, evidence, header)`, `load_rules() -> dict[str, HologramRule]`, raising `RulesError` when a required id is missing.
- Registry: `Limits` values keep their numbers but `provenance.limits[key]` gains `enforced_by: <rule id>` (or `enforced_by: null` with `reason`); `limits_measured` is renamed `corpus_statistics` with `legality_evidence: false` and the loader field renamed accordingly; the tests `test_the_bend_radius_is_a_default_and_not_a_floor` and `test_the_measured_envelope_lies_inside_the_limits_the_game_states` are removed and replaced by `test_every_limit_names_the_rule_that_enforces_it`.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_rules.py`:
```python
from flab2bp.sfy.rules import REQUIRED_RULE_IDS, load_rules


def test_every_required_rule_is_present_with_a_status_and_evidence():
    rules = load_rules()
    for rule_id in REQUIRED_RULE_IDS:
        r = rules[rule_id]
        assert r.status in ("extracted", "partial", "unextractable"), rule_id
        assert r.header, rule_id
        if r.status != "unextractable":
            assert r.evidence and r.rva, rule_id
        else:
            assert r.interpretation, rule_id


def test_belt_curvature_rule_reads_the_bend_radius():
    r = load_rules()["belt.curvature"]
    assert r.status in ("extracted", "partial")
    assert "mBendRadius" in r.reads
```
In `tests/sfy/test_registry.py` replace the two corpus-vs-limit tests with:
```python
def test_every_limit_names_the_rule_that_enforces_it():
    reg = load_registry()
    for key in ("belt_bend_radius_cm", "belt_max_incline_deg", "belt_max_spline_cm", "lift_min_cm", "lift_max_cm", "lift_step_cm", "hologram_grid_cm"):
        entry = reg.provenance["limits"][key]
        assert "enforced_by" in entry, key
        assert entry["enforced_by"] or entry.get("reason"), key


def test_corpus_statistics_are_not_legality_evidence():
    reg = load_registry()
    assert reg.corpus_statistics and all(v["legality_evidence"] is False for v in reg.corpus_statistics.values())
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_rules.py tests/sfy/test_registry.py -q`
Expected: non-zero exit.

- [ ] **Step 3: Header survey**

Extract the seven hologram headers plus `FGBuildable.h` (clearance data) and `FGSplineBuildableInterface.h` into a `fixwave-`/`task3-` scratch dir and write `docs/sfy-hologram-rules.md`: per class, the validation entry points with file:line, the members each uses (from the header's declarations and comments), and for belts the spline construction chain and where `mBendRadius` enters. This is the map for Step 4.

- [ ] **Step 4: Disassemble and interpret**

`scripts/sfy_native_rules.py` runs `sfy-native disasm` for each rule's function (list in the script, keyed by rule id), reads the JSON, and builds `hologram_rules.json`: `reads` from the `member` annotations, `constants` from `.rdata` decodes, `calls` from resolved targets, `rva`; the `comparison`, `interpretation` and `status` fields are written by hand into a small table in the script (`INTERPRETATIONS: dict[str, tuple[status, comparison, interpretation]]`) after reading each function's disassembly, with the evidence lines copied verbatim from the tool output. Rules of honesty: `extracted` only when the comparison operands and the branch are identified in the evidence lines; `partial` when the member reads are seen but the comparison is inside a callee you did not follow (name the callee); `unextractable` with the reason otherwise. For `belt.curvature`, `belt.incline`, `belt.min_length` and `belt.max_length` the target is `extracted`; if the spline is built so that the radius can never be below `mBendRadius` (the check lives in construction, not validation), say exactly that with the construction function's evidence.

- [ ] **Step 5: Registry semantics and statistics**

`scripts/sfy_registry.py`: add `enforced_by` per limit from a mapping limit key → rule id; rename `limits_measured` → `corpus_statistics` with `legality_evidence: false` and `role: "statistics"`; `scripts/sfy_measure_limits.py` docstring and `measured.json` `provenance.method` say the same; `registry.py` renames the field and validates it; regenerate `registry.json`; update `docs/sfy-regenerating-game-data.md` with the rules step (between native and merge) and the demotion; amend the spec section 10 with one paragraph: legality is what the hologram allows, rules in `hologram_rules.json`, corpus never evidence.

- [ ] **Step 6: Run the suite and commit**

Run: `uv run pytest tests/sfy -q`
Expected: exit 0.
```bash
git add docs/sfy-hologram-rules.md scripts/sfy_native_rules.py src/flab2bp/sfy/data/hologram_rules.json src/flab2bp/sfy/rules.py tests/sfy/test_rules.py scripts/sfy_registry.py src/flab2bp/sfy/registry.py src/flab2bp/sfy/data/registry.json scripts/sfy_measure_limits.py src/flab2bp/sfy/data/measured.json tests/sfy/test_registry.py docs/sfy-regenerating-game-data.md docs/superpowers/specs/2026-09-13-satisfactory-target-design.md
git commit -m "Take legality from the hologram rules and demote the corpus to statistics"
```

**Review batch B ends here (Tasks 2 and 3).**

---

## Self-review

**Spec coverage.** Fix 2 (structs from game data): Task 2 covers both survivors and adds the usmap schema check. Fix 4 (hologram legality): Task 3 covers extraction, registry semantics, statistics demotion, runbook and spec amendment; Task 1 is the tool both depend on.

**Placeholder scan.** The `InventoryItem` layout is deliberately not asserted in the plan (it must come from the disassembly); Step 5 of Task 2 says so. Rule interpretations are written after reading the disassembly with an honesty rubric. No "TBD".

**Type consistency.** `read_struct_value(r, name, size, modern)` is named in Task 2 and must match the module; `HologramRule` fields match the JSON keys; `corpus_statistics` is the single new name across script, loader, JSON and tests.
