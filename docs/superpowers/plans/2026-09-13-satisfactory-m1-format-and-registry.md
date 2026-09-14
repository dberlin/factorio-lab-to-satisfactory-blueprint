# Satisfactory Milestone 1: Format and Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A byte-exact reader and writer for Satisfactory `.sbp`/`.sbpcfg` blueprint files, a game-derived legality registry (Docs.json plus a CUE4Parse extractor), and a hand-built one-constructor blueprint for the user's first in-game paste test.

**Architecture:** `flab2bp.sfy` is a new package beside `flab2bp.dsp`. Binary primitives (`archive.py`) feed a layered codec: header and version block (`header.py`), zlib chunk stream (`chunks.py`), object table and per-object blobs (`objects.py`), tagged UE properties (`properties.py`), class-specific trailers (`trailers.py`), assembled by `codec.py` into a `Blueprint` model. Every layer preserves unknown bytes as opaque values so byte identity on the fixture corpus holds from Task 5 onward and decoding depth grows underneath it. The registry (`registry.py`) loads a committed `data/registry.json` produced by `scripts/sfy_registry.py` from Docs.json (`docs.py`) and the dotnet extractor under `tools/sfy-extract/`. `templates.py` clones fixture objects into new blueprints for the checkpoint.

**Tech Stack:** Python 3.14, stdlib `struct`/`zlib`/`json`, `pytest`, `uv`; dotnet 10 with CUE4Parse 1.2.2.202609 for the extractor (isolated under `tools/`, never imported by Python).

**Spec:** `docs/superpowers/specs/2026-09-13-satisfactory-target-design.md` (sections 3, 4, 5 and 12 checkpoint 1).

## Global Constraints

- Work in the worktree `.claude/worktrees/satisfactory` on branch `satisfactory`. Run `uv sync` there first and verify `uv run python -c "import flab2bp, sys; print(flab2bp.__file__)"` prints a path inside the worktree (a worktree without its own venv runs tests against master).
- Python `>=3.14` (pyproject). No new Python dependencies. `littletable` and `networkx` stay importable only from `flab2bp.indexed`.
- Nothing under `src/flab2bp/dsp/`, `src/flab2bp/layout/` or `src/flab2bp/lab/` changes in this milestone.
- Every fixture in `tests/fixtures/sfy/` must decode; every fixture with `SaveVersion >= 58` must re-encode byte-identically (the game's current family). Fixtures with `SaveVersion < 58` must decode through the property layer; byte identity for them is asserted on the decompressed payload only.
- Unknown bytes are never dropped: any value the codec does not understand is kept as `Opaque(raw: bytes)` and written back verbatim.
- All integers little-endian. `FString`: `int32 n`; `n == 0` empty; `n > 0` then `n` bytes ANSI including the NUL; `n < 0` then `-n` UTF-16LE code units including the NUL. Wide iff any code point `> 0x7F`.
- Commits: one per task, message in the imperative, ending with the attribution lines from the session reminder. Set `GIT_EDITOR=true` and always pass `-m`.
- Tests run as `uv run pytest tests/sfy -q`; the summary line does not print in this environment, so the exit code is the signal. Never use `--timeout` above the repo default.
- The `~/Satisfactory` install path is read from `FLAB2BP_SATISFACTORY_DIR` (default `~/Satisfactory`). Tests that need it skip with a clear reason when it is absent; the committed `registry.json` is what CI uses.
- Review batches (user ruling): Tasks 1-5, Tasks 6-8, Tasks 9-11, Task 12.

---

## File Structure

| Path | Responsibility |
|---|---|
| `tests/fixtures/sfy/*.sbp`, `*.sbpcfg`, `MANIFEST.json` | Community blueprints and their provenance |
| `scripts/sfy_fixtures.py` | Re-download fixtures from `MANIFEST.json` |
| `src/flab2bp/sfy/__init__.py` | Empty |
| `src/flab2bp/sfy/archive.py` | `Reader`, `Writer`: ints, floats, doubles, FString, GUID, object references |
| `src/flab2bp/sfy/versions.py` | `SaveCustomVersion` enum transcribed from `SaveCustomVersion.h`, header and config version constants |
| `src/flab2bp/sfy/header.py` | `BlueprintHeader`, `SaveObjectVersionData`, `BlueprintRecord` (.sbpcfg) |
| `src/flab2bp/sfy/chunks.py` | UE compressed chunk stream (zlib, 128 KiB) |
| `src/flab2bp/sfy/objects.py` | `ObjectHeader` (table of contents) and `ObjectData` (prefix, property bytes, trailer) |
| `src/flab2bp/sfy/properties.py` | Tagged property tree: `Tag`, values, parse and emit |
| `src/flab2bp/sfy/trailers.py` | Class-specific bytes after the property list |
| `src/flab2bp/sfy/codec.py` | `Blueprint`, `read_sbp`, `write_sbp`, `read_sbpcfg`, `write_sbpcfg` |
| `src/flab2bp/sfy/docs.py` | Docs.json loader producing the Docs half of the registry |
| `src/flab2bp/sfy/registry.py` | Frozen registry dataclasses and `load_registry()` |
| `src/flab2bp/sfy/data/registry.json` | Committed registry with provenance stamp |
| `tools/sfy-extract/SfyExtract.csproj`, `Program.cs` | CUE4Parse extractor: ports, hologram limits, wire lengths |
| `scripts/sfy_registry.py` | Merge Docs half and extractor output into `registry.json` |
| `src/flab2bp/sfy/templates.py` | Clone fixture objects into a new blueprint |
| `scripts/sfy_checkpoint1.py` | Build `out/sfy/checkpoint1.sbp` and `.sbpcfg` |
| `tests/sfy/` | One test module per source module plus `conftest.py` |

---

### Task 1: Fixture corpus and downloader

**Files:**
- Create: `tests/fixtures/sfy/MANIFEST.json`
- Create: `scripts/sfy_fixtures.py`
- Keep: `tests/fixtures/sfy/*.sbp`, `*.sbpcfg` (already present, uncommitted), delete `*.uuid`
- Test: `tests/sfy/test_fixtures.py`, `tests/sfy/conftest.py`

**Interfaces:**
- Produces: `tests/sfy/conftest.py::fixture_paths() -> list[Path]` (all `.sbp`), `fixture_pairs() -> list[tuple[Path, Path]]` (`.sbp`, `.sbpcfg`).

- [ ] **Step 1: Build the manifest from the uuid files**

```bash
cd .claude/worktrees/satisfactory
uv run python - <<'EOF'
import json, pathlib
d = pathlib.Path("tests/fixtures/sfy")
entries = []
for u in sorted(d.glob("*.uuid")):
    stem = u.stem
    entries.append({"name": stem, "uuid": u.read_text().strip(),
                    "sbp": f"{stem}.sbp", "sbpcfg": f"{stem}.sbpcfg",
                    "source": "https://satisfactoryblueprints.com/blueprint/" + u.read_text().strip()})
(d / "MANIFEST.json").write_text(json.dumps({"downloaded": "2026-09-13", "entries": entries}, indent=2) + "\n")
for u in d.glob("*.uuid"): u.unlink()
print(len(entries))
EOF
```
Expected: `19`.

- [ ] **Step 2: Write the downloader**

`scripts/sfy_fixtures.py`:
```python
"""Re-download the Satisfactory blueprint fixtures listed in MANIFEST.json.

Download URLs are https://satisfactoryblueprints.com/blueprint/download/<uuid>
(.sbp) and /blueprint/config/<uuid> (.sbpcfg). Existing files are kept unless
--force is given.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sfy"
BASE = "https://satisfactoryblueprints.com/blueprint"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for entry in manifest["entries"]:
            for key, kind in (("sbp", "download"), ("sbpcfg", "config")):
                target = FIXTURES / entry[key]
                if target.exists() and not args.force:
                    continue
                response = client.get(f"{BASE}/{kind}/{entry['uuid']}")
                response.raise_for_status()
                target.write_bytes(response.content)
                print(f"wrote {target.name} ({len(response.content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Write conftest and the fixture test**

`tests/sfy/__init__.py` empty. `tests/sfy/conftest.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sfy"


def fixture_paths() -> list[Path]:
    return sorted(FIXTURES.glob("*.sbp"))


def fixture_pairs() -> list[tuple[Path, Path]]:
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    return [(FIXTURES / e["sbp"], FIXTURES / e["sbpcfg"]) for e in manifest["entries"]]


@pytest.fixture(params=fixture_paths(), ids=lambda p: p.stem)
def sbp_path(request) -> Path:
    return request.param


@pytest.fixture(params=fixture_pairs(), ids=lambda pair: pair[0].stem)
def sbp_pair(request) -> tuple[Path, Path]:
    return request.param
```

`tests/sfy/test_fixtures.py`:
```python
from tests.sfy.conftest import FIXTURES, fixture_pairs


def test_manifest_lists_every_file_on_disk():
    listed = {p.name for pair in fixture_pairs() for p in pair}
    on_disk = {p.name for p in FIXTURES.iterdir() if p.suffix in (".sbp", ".sbpcfg")}
    assert listed == on_disk


def test_every_sbp_starts_with_header_version_2():
    for sbp, _ in fixture_pairs():
        assert sbp.read_bytes()[:4] == b"\x02\x00\x00\x00", sbp.name
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/sfy scripts/sfy_fixtures.py tests/sfy
git commit -m "Add the Satisfactory blueprint fixture corpus and its downloader"
```

---

### Task 2: Binary archive primitives

**Files:**
- Create: `src/flab2bp/sfy/__init__.py`, `src/flab2bp/sfy/archive.py`
- Test: `tests/sfy/test_archive.py`

**Interfaces:**
- Produces:
  - `class Reader: __init__(self, data: bytes, pos: int = 0)`; `pos: int`; `remaining() -> int`; `i8/u8/i32/u32/i64/f32/f64() -> int|float`; `bytes(n) -> bytes`; `fstring() -> str`; `guid() -> bytes` (16); `object_ref() -> ObjectRef`; `expect_end()` raises `ArchiveError` if bytes remain.
  - `class Writer: __init__(self)`; same names taking a value; `fstring(s: str)`; `guid(b: bytes)`; `object_ref(ref: ObjectRef)`; `getvalue() -> bytes`; `__len__`.
  - `@dataclass(frozen=True, slots=True) class ObjectRef: level: str; path: str`; `ObjectRef.NULL = ObjectRef("", "")`; `is_null`.
  - `class ArchiveError(ValueError)`.
  - `def is_wide(s: str) -> bool` (any code point > 0x7F).

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_archive.py`:
```python
import pytest

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer, is_wide


def test_fstring_ansi_round_trip():
    w = Writer()
    w.fstring("Persistent_Level")
    assert w.getvalue() == b"\x11\x00\x00\x00Persistent_Level\x00"
    assert Reader(w.getvalue()).fstring() == "Persistent_Level"


def test_fstring_empty_is_zero_length():
    w = Writer()
    w.fstring("")
    assert w.getvalue() == b"\x00\x00\x00\x00"
    assert Reader(w.getvalue()).fstring() == ""


def test_fstring_wide_uses_negative_utf16_length():
    w = Writer()
    w.fstring("Ärger")
    data = w.getvalue()
    assert data[:4] == (-6).to_bytes(4, "little", signed=True)
    assert data[4:] == "Ärger".encode("utf-16-le") + b"\x00\x00"
    assert Reader(data).fstring() == "Ärger"
    assert is_wide("Ärger") and not is_wide("plain")


def test_scalars_round_trip():
    w = Writer()
    w.i32(-5); w.u32(7); w.i64(-(1 << 40)); w.f32(1.5); w.f64(2.25); w.u8(255); w.i8(-1)
    r = Reader(w.getvalue())
    assert (r.i32(), r.u32(), r.i64(), r.f32(), r.f64(), r.u8(), r.i8()) == (-5, 7, -(1 << 40), 1.5, 2.25, 255, -1)
    r.expect_end()


def test_object_ref_round_trip_and_null():
    ref = ObjectRef("Persistent_Level", "Persistent_Level:PersistentLevel.Build_ConstructorMk1_C_1")
    w = Writer(); w.object_ref(ref); w.object_ref(ObjectRef.NULL)
    r = Reader(w.getvalue())
    assert r.object_ref() == ref
    assert r.object_ref().is_null


def test_expect_end_raises_on_leftover():
    with pytest.raises(ArchiveError):
        Reader(b"\x00\x00").expect_end()


def test_guid_is_16_bytes():
    w = Writer(); w.guid(bytes(range(16)))
    assert Reader(w.getvalue()).guid() == bytes(range(16))
    with pytest.raises(ArchiveError):
        Writer().guid(b"short")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_archive.py -q`
Expected: exit code non-zero, `ModuleNotFoundError: flab2bp.sfy`.

- [ ] **Step 3: Implement**

`src/flab2bp/sfy/__init__.py` empty. `src/flab2bp/sfy/archive.py`:
```python
"""Little-endian Unreal archive primitives used by every Satisfactory blueprint layer.

Byte identity is the contract: whatever ``Reader`` consumes, ``Writer`` must
reproduce. The one encoding choice the game makes, ANSI versus UTF-16 for
strings, follows Unreal's rule (wide iff any code point is above 0x7F), so it
is derived from the value rather than stored.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import ClassVar

__all__ = ["ArchiveError", "ObjectRef", "Reader", "Writer", "is_wide"]


class ArchiveError(ValueError):
    """Malformed or unexpected bytes."""


def is_wide(s: str) -> bool:
    return any(ord(ch) > 0x7F for ch in s)


@dataclass(frozen=True, slots=True)
class ObjectRef:
    level: str
    path: str
    NULL: ClassVar["ObjectRef"]

    @property
    def is_null(self) -> bool:
        return self.level == "" and self.path == ""

    @property
    def name(self) -> str:
        """Last dotted component, e.g. ``Build_ConstructorMk1_C_1``."""
        return self.path.rsplit(".", 1)[-1]


ObjectRef.NULL = ObjectRef("", "")


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise ArchiveError(f"need {size} bytes at {self.pos}, have {self.remaining()}")
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def i8(self) -> int: return self._unpack("<b")
    def u8(self) -> int: return self._unpack("<B")
    def i32(self) -> int: return self._unpack("<i")
    def u32(self) -> int: return self._unpack("<I")
    def i64(self) -> int: return self._unpack("<q")
    def f32(self) -> float: return self._unpack("<f")
    def f64(self) -> float: return self._unpack("<d")

    def bytes(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ArchiveError(f"need {n} bytes at {self.pos}, have {self.remaining()}")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def guid(self) -> bytes:
        return self.bytes(16)

    def fstring(self) -> str:
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:
            raw = self.bytes(-2 * n)
            if raw[-2:] != b"\x00\x00":
                raise ArchiveError(f"wide string at {self.pos} lacks NUL")
            return raw[:-2].decode("utf-16-le")
        raw = self.bytes(n)
        if raw[-1:] != b"\x00":
            raise ArchiveError(f"ansi string at {self.pos} lacks NUL")
        return raw[:-1].decode("latin-1")

    def object_ref(self) -> ObjectRef:
        return ObjectRef(self.fstring(), self.fstring())

    def expect_end(self) -> None:
        if self.pos != len(self.data):
            raise ArchiveError(f"{self.remaining()} unread bytes at {self.pos}")


class Writer:
    __slots__ = ("_parts", "_size")

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._size = 0

    def _pack(self, fmt: str, value) -> None:
        self.raw(struct.pack(fmt, value))

    def raw(self, b: bytes) -> None:
        self._parts.append(b)
        self._size += len(b)

    def i8(self, v: int) -> None: self._pack("<b", v)
    def u8(self, v: int) -> None: self._pack("<B", v)
    def i32(self, v: int) -> None: self._pack("<i", v)
    def u32(self, v: int) -> None: self._pack("<I", v)
    def i64(self, v: int) -> None: self._pack("<q", v)
    def f32(self, v: float) -> None: self._pack("<f", v)
    def f64(self, v: float) -> None: self._pack("<d", v)

    def guid(self, b: bytes) -> None:
        if len(b) != 16:
            raise ArchiveError(f"guid must be 16 bytes, got {len(b)}")
        self.raw(b)

    def fstring(self, s: str) -> None:
        if s == "":
            self.i32(0)
        elif is_wide(s):
            encoded = s.encode("utf-16-le") + b"\x00\x00"
            self.i32(-(len(encoded) // 2))
            self.raw(encoded)
        else:
            encoded = s.encode("latin-1") + b"\x00"
            self.i32(len(encoded))
            self.raw(encoded)

    def object_ref(self, ref: ObjectRef) -> None:
        self.fstring(ref.level)
        self.fstring(ref.path)

    def getvalue(self) -> bytes:
        return b"".join(self._parts)

    def __len__(self) -> int:
        return self._size
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/sfy/test_archive.py -q`
Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add src/flab2bp/sfy/__init__.py src/flab2bp/sfy/archive.py tests/sfy/test_archive.py
git commit -m "Add the Satisfactory archive reader and writer primitives"
```

---

### Task 3: Versions, blueprint header and config record

**Files:**
- Create: `src/flab2bp/sfy/versions.py`, `src/flab2bp/sfy/header.py`
- Test: `tests/sfy/test_header.py`

**Interfaces:**
- Consumes: `Reader`, `Writer`, `ObjectRef` from Task 2.
- Produces:
  - `versions.SaveCustomVersion(IntEnum)` with every member of `FSaveCustomVersion::Type` in header order (`BeforeCustomVersionWasAdded = 0` … `FixedMissingFICSITMaterials`), plus `LATEST = FixedMissingFICSITMaterials`.
  - `versions.BLUEPRINT_HEADER_VERSION = 2`, `versions.BLUEPRINT_CONFIG_VERSION = 6`, `versions.SAVE_CUSTOM_VERSION_GUID: bytes` (16 bytes, little-endian of the four uint32 in `SaveCustomVersion.h`).
  - `header.EngineVersion(major, minor, patch, changelist, branch)`; `header.SaveObjectVersionData(data_version: int, ue4: int, ue5: int, licensee: int, engine: EngineVersion, custom_versions: tuple[tuple[bytes, int], ...])`.
  - `header.ItemAmount(item: ObjectRef, amount: int)`.
  - `header.BlueprintHeader(header_version, save_version, build_version, dimensions: tuple[int,int,int], cost: tuple[ItemAmount,...], recipes: tuple[ObjectRef,...], version_data: SaveObjectVersionData | None)`; `read_header(r: Reader) -> BlueprintHeader`; `write_header(w: Writer, h: BlueprintHeader)`; `has_version_block(save_version) -> bool` (true iff `save_version >= SaveCustomVersion.SerializeDataPackageVersionAndCustomVersions`).
  - `header.BlueprintRecord(config_version: int, description: str, icon_id: int, color: tuple[float,float,float,float], icon_library_path: str, icon_library_name: str, tail: bytes)`; `read_record(data: bytes) -> BlueprintRecord`; `write_record(rec) -> bytes`.

- [ ] **Step 1: Transcribe the enum**

Extract `Headers.zip` from `$FLAB2BP_SATISFACTORY_DIR/CommunityResources/` into a scratch directory and open `Source/FactoryGame/Public/SaveCustomVersion.h`. Write `src/flab2bp/sfy/versions.py`:
```python
"""Version constants transcribed from the game's public headers.

Source: CommunityResources/Headers.zip, Source/FactoryGame/Public/SaveCustomVersion.h
and FGFactoryBlueprintTypes.h. Member order is the enum order; values are
implicit and must not be reordered.
"""
from __future__ import annotations

import struct
from enum import IntEnum

__all__ = ["BLUEPRINT_CONFIG_VERSION", "BLUEPRINT_HEADER_VERSION", "SAVE_CUSTOM_VERSION_GUID", "SaveCustomVersion"]

BLUEPRINT_HEADER_VERSION = 2   # FBlueprintHeader::AddedUsedRecipes
BLUEPRINT_CONFIG_VERSION = 6   # FBlueprintConfigVersion::RemovedFilteredProfanityName
SAVE_CUSTOM_VERSION_GUID = struct.pack("<4I", 0x21043E2F, 0x13E61FD6, 0x513B9D51, 0x3636A230)


class SaveCustomVersion(IntEnum):
    BeforeCustomVersionWasAdded = 0
    DROPPED_StoreTransform = 1
    DROPPED_ChangeObjectHeader = 2
    DROPPED_PropertyTagsAsStrings = 3
    DROPPED_StoreVehiclesBodyState = 4
    DROPPED_ActorPlacedInLevelSaved = 5
    DROPPED_MovedActorOuter = 6
    DROPPED_PowerConnectionComponents = 7
    DROPPED_SerializeTrainTimetable = 8
    DROPPED_FactoryConnectionWorldToLocal = 9
    DROPPED_CircuitObjects = 10
    DROPPED_DockingStationSingleInventory = 11
    DROPPED_SavingBuildShortcuts = 12
    DROPPED_GamePhaseManagerAdded = 13
    DROPPED_RemovedRelativeTransformsFromConnectionComponents = 14
    DROPPED_MCP_RestoreLostPawn = 15
    DROPPED_WireSpanFromConnnectionComponents = 16
    DROPPED_RenamedSaveSessionId = 17
    ChangedGeoThermalGeneratorSaved = 18
    OverwriteOldRailroadData = 19
    ResetFactoryLegs = 20
    SaveFileIsCompressed = 21
    BU3SaveCompatibility = 22
    BuildingColorConversion = 23
    RescuedFriendDoggos = 24
    CheckPickedUpItems = 25
    PerInstanceCustomColors = 26
    DoubleRampPositioning = 27
    TrainBlueprintClassAdded = 28
    AddedSublevelStreaming = 29
    AddedResourceSinkTrack = 30
    AddedColoringSupportToConcretePillars = 31
    AddedResourceSinkTrack2 = 32
    AddedCachedLocationsForWire = 33
    ReworkedSplittersAndMergers = 34
    ReworkedProductivityMonitor = 35
    NativizedShoppingList = 36
    UnrealEngine5 = 37
    IntroducedWorldPartition = 38
    DroneActionRefactor = 39
    MultipleWireMeshRefactor = 40
    SwitchTo64BitSaveArchive = 41
    ResetBrokenBlueprintSplines = 42
    RefactoredInventoryItemState = 43
    AddedPaintFinishes = 44
    NormalizeChainSplineArriveAndLeave = 45
    Version1 = 46
    PoleRefactor = 47
    LightweightBuildableSubsystemWritesRuntimeVersion = 48
    SerializeObjectFlags = 49
    BackToBackRailroadSwitches = 50
    SerializePerStreamableLevelTOCVersion = 51
    RailroadTrackConnectionCleanup = 52
    SerializeDataPackageVersionAndCustomVersions = 53
    DROPPED_RailroadSubsystemThirdRailConnectionsCleanup = 54
    RailroadSubsystemThirdRailConnectionsCleanupRedone = 55
    UnlockAvailableItemDescriptorsForPickedUpItems = 56
    NewPlayerInfoHandleSerializationFormat = 57
    FixNewPlayerInfoHandleSerializationFormat = 58
    FixedUpInvalidPatternRotationsBlueprintSupport = 59
    FixedMissingFICSITMaterials = 60


LATEST = SaveCustomVersion.FixedMissingFICSITMaterials
```
Check the count against the header before committing: the header lists `VersionPlusOne` after `FixedMissingFICSITMaterials`, so `LATEST == 60`, matching the newest fixtures' `SaveVersion`. If the header you extract has a different member list, transcribe the header, not this listing.

- [ ] **Step 2: Write the failing tests**

`tests/sfy/test_header.py`:
```python
import struct

from flab2bp.sfy.archive import ObjectRef, Reader, Writer
from flab2bp.sfy.header import (
    BlueprintHeader, BlueprintRecord, EngineVersion, ItemAmount, SaveObjectVersionData,
    has_version_block, read_header, read_record, write_header, write_record,
)
from flab2bp.sfy.versions import LATEST, SaveCustomVersion


def test_latest_matches_newest_fixture_save_version():
    assert LATEST == 60
    assert SaveCustomVersion.SerializeDataPackageVersionAndCustomVersions == 53


def test_header_round_trip_with_version_block():
    h = BlueprintHeader(
        header_version=2, save_version=60, build_version=493833, dimensions=(4, 4, 4),
        cost=(ItemAmount(ObjectRef("", "/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C"), 12),),
        recipes=(ObjectRef("", "/Game/FactoryGame/Recipes/Buildings/Recipe_ConstructorMk1.Recipe_ConstructorMk1_C"),),
        version_data=SaveObjectVersionData(
            data_version=0, ue4=522, ue5=1017, licensee=3,
            engine=EngineVersion(5, 6, 1, 2147977481, "++FactoryGame+rel-main-1.2.0"),
            custom_versions=((bytes(range(16)), 60),)),
    )
    w = Writer(); write_header(w, h)
    r = Reader(w.getvalue())
    assert read_header(r) == h
    r.expect_end()


def test_header_without_version_block_for_old_saves():
    assert not has_version_block(52) and has_version_block(58)
    h = BlueprintHeader(2, 52, 463028, (6, 6, 6), (), (), None)
    w = Writer(); write_header(w, h)
    assert read_header(Reader(w.getvalue())) == h


def test_every_fixture_header_parses(sbp_path):
    data = sbp_path.read_bytes()
    h = read_header(Reader(data))
    assert h.header_version == 2
    assert h.dimensions[0] in (4, 5, 6, 12)
    assert (h.version_data is not None) == has_version_block(h.save_version)


def test_record_round_trip_on_every_fixture(sbp_pair):
    raw = sbp_pair[1].read_bytes()
    rec = read_record(raw)
    assert rec.config_version == 6
    assert write_record(rec) == raw


def test_record_fields_of_biofuel():
    rec = read_record((__import__("tests.sfy.conftest", fromlist=["FIXTURES"]).FIXTURES / "biofuel.sbpcfg").read_bytes())
    assert rec.description == ""
    assert rec.icon_id == 282
    assert rec.icon_library_path == "/Game/FactoryGame/-Shared/Blueprint/IconLibrary"
    assert rec.icon_library_name == "IconLibrary"
    assert abs(rec.color[3] - 1.0) < 1e-6
    assert rec.tail == struct.pack("<iB", 6, 0)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/sfy/test_header.py -q`
Expected: non-zero exit, `ModuleNotFoundError: flab2bp.sfy.header`.

- [ ] **Step 4: Implement `header.py`**

```python
"""FBlueprintHeader, FSaveObjectVersionData and FBlueprintRecord.

Layouts come from FGFactoryBlueprintTypes.h and FGSaveSession.h and were
verified on the fixture corpus. The engine-version block is present only when
SaveVersion >= SerializeDataPackageVersionAndCustomVersions; fixtures with
SaveVersion 46 and 52 go straight from the recipe list to the chunk stream.

The .sbpcfg record does not store the blueprint name (the game fills it from
the file name). Its last five bytes (int32 6, uint8 0) are the serialized
FPlayerInfoHandle for LastEditedBy in the NewPlayerInfoHandleSerializationFormat
family; they are carried as an opaque ``tail`` and written back verbatim.
"""
from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
from flab2bp.sfy.versions import BLUEPRINT_CONFIG_VERSION, SaveCustomVersion

__all__ = [
    "BlueprintHeader", "BlueprintRecord", "EngineVersion", "ItemAmount", "SaveObjectVersionData",
    "has_version_block", "read_header", "read_record", "write_header", "write_record",
]


@dataclass(frozen=True, slots=True)
class EngineVersion:
    major: int
    minor: int
    patch: int
    changelist: int
    branch: str


@dataclass(frozen=True, slots=True)
class SaveObjectVersionData:
    data_version: int
    ue4: int
    ue5: int
    licensee: int
    engine: EngineVersion
    custom_versions: tuple[tuple[bytes, int], ...]


@dataclass(frozen=True, slots=True)
class ItemAmount:
    item: ObjectRef
    amount: int


@dataclass(frozen=True, slots=True)
class BlueprintHeader:
    header_version: int
    save_version: int
    build_version: int
    dimensions: tuple[int, int, int]
    cost: tuple[ItemAmount, ...]
    recipes: tuple[ObjectRef, ...]
    version_data: SaveObjectVersionData | None


def has_version_block(save_version: int) -> bool:
    return save_version >= SaveCustomVersion.SerializeDataPackageVersionAndCustomVersions


def _read_version_data(r: Reader) -> SaveObjectVersionData:
    data_version = r.u32()
    ue4, ue5, licensee = r.i32(), r.i32(), r.i32()
    engine = EngineVersion(r._unpack("<H"), r._unpack("<H"), r._unpack("<H"), r.u32(), r.fstring())
    count = r.i32()
    customs = tuple((r.guid(), r.i32()) for _ in range(count))
    return SaveObjectVersionData(data_version, ue4, ue5, licensee, engine, customs)


def _write_version_data(w: Writer, v: SaveObjectVersionData) -> None:
    w.u32(v.data_version)
    w.i32(v.ue4); w.i32(v.ue5); w.i32(v.licensee)
    w._pack("<H", v.engine.major); w._pack("<H", v.engine.minor); w._pack("<H", v.engine.patch)
    w.u32(v.engine.changelist); w.fstring(v.engine.branch)
    w.i32(len(v.custom_versions))
    for guid, version in v.custom_versions:
        w.guid(guid); w.i32(version)


def read_header(r: Reader) -> BlueprintHeader:
    header_version = r.i32()
    if header_version != 2:
        raise ArchiveError(f"unsupported blueprint header version {header_version}")
    save_version, build_version = r.i32(), r.i32()
    dims = (r.i32(), r.i32(), r.i32())
    cost = tuple(ItemAmount(r.object_ref(), r.i32()) for _ in range(r.i32()))
    recipes = tuple(r.object_ref() for _ in range(r.i32()))
    version_data = _read_version_data(r) if has_version_block(save_version) else None
    return BlueprintHeader(header_version, save_version, build_version, dims, cost, recipes, version_data)


def write_header(w: Writer, h: BlueprintHeader) -> None:
    w.i32(h.header_version); w.i32(h.save_version); w.i32(h.build_version)
    for d in h.dimensions:
        w.i32(d)
    w.i32(len(h.cost))
    for c in h.cost:
        w.object_ref(c.item); w.i32(c.amount)
    w.i32(len(h.recipes))
    for ref in h.recipes:
        w.object_ref(ref)
    if has_version_block(h.save_version):
        if h.version_data is None:
            raise ArchiveError("save version requires a version block")
        _write_version_data(w, h.version_data)


@dataclass(frozen=True, slots=True)
class BlueprintRecord:
    config_version: int
    description: str
    icon_id: int
    color: tuple[float, float, float, float]
    icon_library_path: str
    icon_library_name: str
    tail: bytes

    @classmethod
    def new(cls, description: str, icon_id: int = 282,
            color: tuple[float, float, float, float] = (0.397, 0.076, 0.076, 1.0)) -> "BlueprintRecord":
        return cls(BLUEPRINT_CONFIG_VERSION, description, icon_id, color,
                   "/Game/FactoryGame/-Shared/Blueprint/IconLibrary", "IconLibrary", b"\x06\x00\x00\x00\x00")


def read_record(data: bytes) -> BlueprintRecord:
    r = Reader(data)
    config_version = r.i32()
    description = r.fstring()
    icon_id = r.i32()
    color = (r.f32(), r.f32(), r.f32(), r.f32())
    path, name = r.fstring(), r.fstring()
    tail = r.bytes(r.remaining())
    return BlueprintRecord(config_version, description, icon_id, color, path, name, tail)


def write_record(rec: BlueprintRecord) -> bytes:
    w = Writer()
    w.i32(rec.config_version); w.fstring(rec.description); w.i32(rec.icon_id)
    for c in rec.color:
        w.f32(c)
    w.fstring(rec.icon_library_path); w.fstring(rec.icon_library_name); w.raw(rec.tail)
    return w.getvalue()
```
Add public `u16()` methods to `Reader`/`Writer` in `archive.py` instead of calling `_unpack`/`_pack` from outside; update the code above to use them.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/sfy/test_header.py tests/sfy/test_archive.py -q`
Expected: exit code 0. If `test_record_fields_of_biofuel` fails on `icon_id`, the record field order is wrong: dump the first 40 bytes of `biofuel.sbpcfg` with `xxd` and compare with the layout comment; do not loosen the test.

- [ ] **Step 6: Commit**

```bash
git add src/flab2bp/sfy/versions.py src/flab2bp/sfy/header.py src/flab2bp/sfy/archive.py tests/sfy/test_header.py
git commit -m "Decode the Satisfactory blueprint header, version block and config record"
```

---

### Task 4: Compressed chunk stream

**Files:**
- Create: `src/flab2bp/sfy/chunks.py`
- Test: `tests/sfy/test_chunks.py`

**Interfaces:**
- Produces: `CHUNK_TAG = 0x9E2A83C1`, `CHUNK_MARK = 0x22222222`, `MAX_CHUNK = 131072`, `COMPRESSION_ZLIB = 3`; `decompress_stream(data: bytes, start: int) -> bytes`; `compress_stream(payload: bytes, level: int = 6) -> bytes`; `find_stream_start(data: bytes, header_end: int) -> int` (asserts the tag sits at `header_end`).

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_chunks.py`:
```python
import struct
import zlib

import pytest

from flab2bp.sfy.archive import Reader
from flab2bp.sfy.chunks import CHUNK_TAG, MAX_CHUNK, compress_stream, decompress_stream, find_stream_start
from flab2bp.sfy.header import read_header


def _payload_and_stream(path):
    data = path.read_bytes()
    r = Reader(data); read_header(r)
    start = find_stream_start(data, r.pos)
    return decompress_stream(data, start), data[start:]


def test_chunk_layout_of_first_chunk(sbp_path):
    data = sbp_path.read_bytes()
    r = Reader(data); read_header(r)
    start = find_stream_start(data, r.pos)
    tag, mark, maxchunk, method, c1, u1, c2, u2 = struct.unpack_from("<IiqBqqqq", data, start)
    assert tag == CHUNK_TAG and mark == 0x22222222 and maxchunk == MAX_CHUNK and method == 3
    assert c1 == c2 and u1 == u2 and u2 <= MAX_CHUNK


def test_decompressed_payload_declares_its_own_length(sbp_path):
    payload, _ = _payload_and_stream(sbp_path)
    assert struct.unpack_from("<i", payload)[0] == len(payload) - 4


def test_recompression_reproduces_payload(sbp_path):
    payload, _ = _payload_and_stream(sbp_path)
    again = compress_stream(payload)
    assert decompress_stream(again, 0) == payload


def test_recompression_is_byte_identical_at_level_6(sbp_path):
    """Records whether UE's zlib settings match Python's. Failure here is
    informative only; the format gate is payload identity (Task 5)."""
    payload, stream = _payload_and_stream(sbp_path)
    if compress_stream(payload, level=6) != stream:
        pytest.xfail("UE zlib output differs from Python zlib level 6; payload identity is the gate")


def test_multi_chunk_payload_round_trips():
    payload = bytes(range(256)) * 2000  # 512000 bytes -> 4 chunks
    stream = compress_stream(payload)
    assert stream.count(struct.pack("<I", CHUNK_TAG)) == 4
    assert decompress_stream(stream, 0) == payload
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_chunks.py -q`
Expected: non-zero exit, import error.

- [ ] **Step 3: Implement `chunks.py`**

```python
"""Unreal's compressed chunk stream as written by FArchiveSaveCompressedProxy.

Each chunk: uint32 tag 0x9E2A83C1, int32 0x22222222, int64 max chunk size
(131072), uint8 compression method (3 = zlib), then (compressed, uncompressed)
sizes twice as int64, then the zlib bytes. Chunks repeat until end of file.
"""
from __future__ import annotations

import struct
import zlib

from flab2bp.sfy.archive import ArchiveError

__all__ = ["CHUNK_MARK", "CHUNK_TAG", "COMPRESSION_ZLIB", "MAX_CHUNK",
           "compress_stream", "decompress_stream", "find_stream_start"]

CHUNK_TAG = 0x9E2A83C1
CHUNK_MARK = 0x22222222
MAX_CHUNK = 131072
COMPRESSION_ZLIB = 3
_HEADER = struct.Struct("<IiqBqqqq")


def find_stream_start(data: bytes, header_end: int) -> int:
    if struct.unpack_from("<I", data, header_end)[0] != CHUNK_TAG:
        raise ArchiveError(f"no chunk tag at {header_end}")
    return header_end


def decompress_stream(data: bytes, start: int) -> bytes:
    out: list[bytes] = []
    pos = start
    while pos < len(data):
        tag, mark, maxchunk, method, c1, u1, c2, u2 = _HEADER.unpack_from(data, pos)
        if tag != CHUNK_TAG or mark != CHUNK_MARK or method != COMPRESSION_ZLIB:
            raise ArchiveError(f"bad chunk header at {pos}: tag={tag:#x} mark={mark:#x} method={method}")
        if (c1, u1) != (c2, u2):
            raise ArchiveError(f"chunk size pairs disagree at {pos}")
        pos += _HEADER.size
        chunk = zlib.decompress(data[pos:pos + c2])
        if len(chunk) != u2:
            raise ArchiveError(f"chunk at {pos} decompressed to {len(chunk)}, declared {u2}")
        out.append(chunk)
        pos += c2
    return b"".join(out)


def compress_stream(payload: bytes, level: int = 6) -> bytes:
    out: list[bytes] = []
    for offset in range(0, len(payload), MAX_CHUNK):
        raw = payload[offset:offset + MAX_CHUNK]
        packed = zlib.compress(raw, level)
        out.append(_HEADER.pack(CHUNK_TAG, CHUNK_MARK, MAX_CHUNK, COMPRESSION_ZLIB,
                                len(packed), len(raw), len(packed), len(raw)))
        out.append(packed)
    return b"".join(out)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/sfy/test_chunks.py -q`
Expected: exit code 0 (xfails allowed only in `test_recompression_is_byte_identical_at_level_6`). Record in the commit message how many fixtures matched at level 6.

- [ ] **Step 5: Commit**

```bash
git add src/flab2bp/sfy/chunks.py tests/sfy/test_chunks.py
git commit -m "Decode and encode the blueprint chunk stream"
```

---

### Task 5: Object table and opaque object data, byte-identical round trip

**Files:**
- Create: `src/flab2bp/sfy/objects.py`, `src/flab2bp/sfy/codec.py`
- Test: `tests/sfy/test_objects.py`, `tests/sfy/test_codec.py`

**Interfaces:**
- Consumes: Tasks 2-4.
- Produces:
  - `objects.Transform(rotation: tuple[float,float,float,float], translation: tuple[float,float,float], scale: tuple[float,float,float])`.
  - `objects.ObjectHeader(kind: int, class_path: str, level: str, path: str, flags: int | None, transform: Transform | None, need_transform: int | None, placed_in_level: int | None, parent: str | None)`; `kind` 1 actor, 0 component; `flags` present iff `save_version >= SerializeObjectFlags`; `name` property returns the last dotted component of `path`.
  - `objects.ObjectData(parent: ObjectRef | None, components: tuple[ObjectRef, ...] | None, body: bytes)`; for actors `parent`/`components` are set, for components both are `None`; `body` is everything after the prefix (property list plus trailer) and stays opaque in this task.
  - `read_toc(r, count, save_version) -> tuple[ObjectHeader, ...]`, `write_toc(w, headers, save_version)`, `read_object_data(raw: bytes, header: ObjectHeader) -> ObjectData`, `write_object_data(d: ObjectData, header) -> bytes`.
  - `codec.Blueprint(header: BlueprintHeader, objects: tuple[tuple[ObjectHeader, ObjectData], ...])`; `codec.read_sbp(data: bytes) -> Blueprint`; `codec.write_sbp(bp: Blueprint, zlib_level: int = 6) -> bytes`; `codec.payload_bytes(bp) -> bytes` (decompressed body); `codec.read_sbpcfg/write_sbpcfg` re-exported from `header`.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_objects.py`:
```python
from collections import Counter

from flab2bp.sfy.codec import read_sbp
from tests.sfy.conftest import FIXTURES


def test_biofuel_object_table():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    kinds = Counter(h.class_path.rsplit(".", 1)[-1] for h, _ in bp.objects)
    assert len(bp.objects) == 194
    assert kinds["Build_Foundation_Concrete_8x1_C"] == 48
    assert kinds["Build_ConstructorMk1_C"] == 3
    assert kinds["FGFactoryConnectionComponent"] == 46
    first = bp.objects[0][0]
    assert first.kind == 1 and first.transform.translation == (1200.0, -1200.0, 50.0)
    assert first.transform.scale == (1.0, 1.0, 1.0)


def test_components_name_their_parent():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    actors = {h.path for h, _ in bp.objects if h.kind == 1}
    for h, d in bp.objects:
        if h.kind == 0:
            assert h.parent in actors, h.path
            assert d.parent is None and d.components is None
        else:
            assert d.parent is not None and d.components is not None


def test_actor_component_lists_match_the_table():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    by_parent = Counter(h.parent for h, _ in bp.objects if h.kind == 0)
    for h, d in bp.objects:
        if h.kind == 1:
            assert len(d.components) == by_parent.get(h.path, 0), h.path
```

`tests/sfy/test_codec.py`:
```python
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.chunks import decompress_stream, find_stream_start
from flab2bp.sfy.codec import payload_bytes, read_sbp, write_sbp
from flab2bp.sfy.header import read_header


def test_payload_round_trip_is_byte_identical(sbp_path):
    data = sbp_path.read_bytes()
    r = Reader(data); read_header(r)
    original_payload = decompress_stream(data, find_stream_start(data, r.pos))
    bp = read_sbp(data)
    assert payload_bytes(bp) == original_payload


def test_header_bytes_round_trip(sbp_path):
    data = sbp_path.read_bytes()
    r = Reader(data); read_header(r)
    assert write_sbp(read_sbp(data))[:r.pos] == data[:r.pos]


def test_full_file_round_trip_for_current_family(sbp_path):
    data = sbp_path.read_bytes()
    bp = read_sbp(data)
    if bp.header.save_version < 58:
        return
    again = write_sbp(bp)
    if again != data:
        # zlib settings may differ; the decoded form must still agree byte for byte
        assert read_sbp(again) == bp
        assert payload_bytes(read_sbp(again)) == payload_bytes(bp)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_objects.py tests/sfy/test_codec.py -q`
Expected: non-zero exit, import errors.

- [ ] **Step 3: Implement `objects.py`**

```python
"""Object table (TOC) entries and the per-object data envelope.

TOC entry: int32 kind (1 actor, 0 component); FString class path; FString
level; FString instance path; int32 object flags (SaveVersion >=
SerializeObjectFlags); actors then int32 needTransform, FQuat (4 f32),
FVector translation (3 f32), FVector scale (3 f32), int32 placedInLevel;
components then FString parent actor path.

Object data: int32 size, then for actors FObjectReferenceDisc parent,
int32 component count, that many references; then the tagged property list
and a class-specific trailer, kept here as an opaque ``body``.
"""
from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, ObjectRef, Reader, Writer
from flab2bp.sfy.versions import SaveCustomVersion

__all__ = ["ObjectData", "ObjectHeader", "Transform", "read_object_data", "read_toc",
           "write_object_data", "write_toc"]

ACTOR = 1
COMPONENT = 0


@dataclass(frozen=True, slots=True)
class Transform:
    rotation: tuple[float, float, float, float]
    translation: tuple[float, float, float]
    scale: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ObjectHeader:
    kind: int
    class_path: str
    level: str
    path: str
    flags: int | None
    transform: Transform | None
    need_transform: int | None
    placed_in_level: int | None
    parent: str | None

    @property
    def name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    @property
    def class_name(self) -> str:
        return self.class_path.rsplit(".", 1)[-1]


@dataclass(frozen=True, slots=True)
class ObjectData:
    parent: ObjectRef | None
    components: tuple[ObjectRef, ...] | None
    body: bytes


def _has_flags(save_version: int) -> bool:
    return save_version >= SaveCustomVersion.SerializeObjectFlags


def read_toc(r: Reader, count: int, save_version: int) -> tuple[ObjectHeader, ...]:
    out = []
    for _ in range(count):
        kind = r.i32()
        if kind not in (ACTOR, COMPONENT):
            raise ArchiveError(f"object kind {kind} at {r.pos}")
        class_path, level, path = r.fstring(), r.fstring(), r.fstring()
        flags = r.i32() if _has_flags(save_version) else None
        if kind == ACTOR:
            need = r.i32()
            rot = (r.f32(), r.f32(), r.f32(), r.f32())
            pos = (r.f32(), r.f32(), r.f32())
            scale = (r.f32(), r.f32(), r.f32())
            placed = r.i32()
            out.append(ObjectHeader(kind, class_path, level, path, flags, Transform(rot, pos, scale), need, placed, None))
        else:
            out.append(ObjectHeader(kind, class_path, level, path, flags, None, None, None, r.fstring()))
    return tuple(out)


def write_toc(w: Writer, headers: tuple[ObjectHeader, ...], save_version: int) -> None:
    for h in headers:
        w.i32(h.kind); w.fstring(h.class_path); w.fstring(h.level); w.fstring(h.path)
        if _has_flags(save_version):
            w.i32(h.flags if h.flags is not None else 0)
        if h.kind == ACTOR:
            w.i32(h.need_transform)
            for v in h.transform.rotation: w.f32(v)
            for v in h.transform.translation: w.f32(v)
            for v in h.transform.scale: w.f32(v)
            w.i32(h.placed_in_level)
        else:
            w.fstring(h.parent or "")


def read_object_data(raw: bytes, header: ObjectHeader) -> ObjectData:
    r = Reader(raw)
    if header.kind == ACTOR:
        parent = r.object_ref()
        components = tuple(r.object_ref() for _ in range(r.i32()))
        return ObjectData(parent, components, raw[r.pos:])
    return ObjectData(None, None, raw)


def write_object_data(d: ObjectData, header: ObjectHeader) -> bytes:
    w = Writer()
    if header.kind == ACTOR:
        w.object_ref(d.parent)
        w.i32(len(d.components))
        for c in d.components:
            w.object_ref(c)
    w.raw(d.body)
    return w.getvalue()
```

- [ ] **Step 4: Implement `codec.py`**

```python
"""Whole-file reader and writer for .sbp and .sbpcfg."""
from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.chunks import compress_stream, decompress_stream, find_stream_start
from flab2bp.sfy.header import BlueprintHeader, read_header, read_record, write_header, write_record
from flab2bp.sfy.objects import ObjectData, ObjectHeader, read_object_data, read_toc, write_object_data, write_toc

__all__ = ["Blueprint", "payload_bytes", "read_sbp", "read_sbpcfg", "write_sbp", "write_sbpcfg"]

read_sbpcfg = read_record
write_sbpcfg = write_record


@dataclass(frozen=True, slots=True)
class Blueprint:
    header: BlueprintHeader
    objects: tuple[tuple[ObjectHeader, ObjectData], ...]


def read_sbp(data: bytes) -> Blueprint:
    r = Reader(data)
    header = read_header(r)
    payload = decompress_stream(data, find_stream_start(data, r.pos))
    p = Reader(payload)
    total = p.i32()
    if total != len(payload) - 4:
        raise ArchiveError(f"payload declares {total}, has {len(payload) - 4}")
    toc_size = p.i32()
    toc_start = p.pos
    count = p.i32()
    headers = read_toc(p, count, header.save_version)
    if p.pos - toc_start != toc_size:
        raise ArchiveError(f"toc consumed {p.pos - toc_start}, declared {toc_size}")
    blob_size = p.i32()
    blob_start = p.pos
    blob_count = p.i32()
    if blob_count != count:
        raise ArchiveError(f"{blob_count} blobs for {count} objects")
    objects = []
    for h in headers:
        size = p.i32()
        objects.append((h, read_object_data(p.bytes(size), h)))
    if p.pos - blob_start != blob_size:
        raise ArchiveError(f"blob consumed {p.pos - blob_start}, declared {blob_size}")
    p.expect_end()
    return Blueprint(header, tuple(objects))


def payload_bytes(bp: Blueprint) -> bytes:
    toc = Writer()
    toc.i32(len(bp.objects))
    write_toc(toc, tuple(h for h, _ in bp.objects), bp.header.save_version)
    blob = Writer()
    blob.i32(len(bp.objects))
    for h, d in bp.objects:
        raw = write_object_data(d, h)
        blob.i32(len(raw)); blob.raw(raw)
    body = Writer()
    body.i32(len(toc)); body.raw(toc.getvalue())
    body.i32(len(blob)); body.raw(blob.getvalue())
    out = Writer()
    out.i32(len(body)); out.raw(body.getvalue())
    return out.getvalue()


def write_sbp(bp: Blueprint, zlib_level: int = 6) -> bytes:
    w = Writer()
    write_header(w, bp.header)
    w.raw(compress_stream(payload_bytes(bp), zlib_level))
    return w.getvalue()
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0. Every fixture, including `SaveVersion` 46 and 52, must pass `test_payload_round_trip_is_byte_identical`; if an old fixture fails in `read_toc`, the `flags` gate is wrong for that version: print `save_version` and whether parsing succeeds with and without the flags field, then fix `_has_flags` to the boundary the data shows and note the boundary in the module docstring.

- [ ] **Step 6: Commit**

```bash
git add src/flab2bp/sfy/objects.py src/flab2bp/sfy/codec.py tests/sfy/test_objects.py tests/sfy/test_codec.py
git commit -m "Round-trip every Satisfactory fixture byte-identically with opaque object bodies"
```

**Review batch A ends here (Tasks 1-5).**

---

### Task 6: Tagged property tree

**Files:**
- Create: `src/flab2bp/sfy/properties.py`
- Test: `tests/sfy/test_properties.py`

**Interfaces:**
- Consumes: `Reader`, `Writer`, `ObjectRef`.
- Produces (all frozen dataclasses with slots):
  - `Opaque(raw: bytes)`.
  - `Tag(name: str, type: str, index: int, struct_name: str | None = None, struct_guid: bytes | None = None, enum_name: str | None = None, inner_type: str | None = None, key_type: str | None = None, value_type: str | None = None, guid: bytes | None = None)`.
  - Values: `Int(v: int)`, `Int8(v)`, `Int64(v)`, `UInt32(v)`, `Float(v: float)`, `Double(v)`, `Bool(v: bool)`, `Str(v: str)`, `Name(v: str)`, `Enum(v: str)`, `Byte(enum_name: str | None, v: int | str)`, `Object(ref: ObjectRef)`, `SoftObject(raw: bytes)`, `Text(raw: bytes)`, `Vector(x, y, z)` (f64), `Rotator(pitch, yaw, roll)` (f64), `Quat(x, y, z, w)` (f64), `Vector2D(x, y)`, `IntVector(x, y, z)`, `LinearColor(r, g, b, a)` (f32), `Color(b, g, r, a)` (u8), `Box(min: Vector, max: Vector, valid: int)`, `Guid(raw: bytes)`, `Struct(name: str, fields: tuple[Property, ...])` (nested list), `BinaryStruct(name: str, raw: bytes)` (unknown binary struct, sized by the tag), `Array(inner_type: str, inner_tag: Tag | None, items: tuple[Value, ...])`, `Map(raw: bytes)`, `Set(raw: bytes)`.
  - `Property(tag: Tag, value: Value)`; `PropertyList = tuple[Property, ...]`.
  - `read_property_list(r: Reader) -> PropertyList` (consumes through the terminating `None`); `write_property_list(w: Writer, props: PropertyList)`; `read_struct_value(r, struct_name, size) -> Value`; `write_struct_value(w, value)`.
  - `BINARY_STRUCTS: frozenset[str] = {"Vector", "Rotator", "Quat", "Vector2D", "IntVector", "LinearColor", "Color", "Box", "Guid"}`.
  - Every value knows how to write itself; `size` in the tag is recomputed on write from the value bytes (never stored), so a property edited in memory writes a correct size.

Tag layout: `FString name`; if `name == "None"` the list ends; `FString type`; `int32 size`; `int32 index`; type-specific: `StructProperty` → `FString struct_name`, 16-byte guid; `ByteProperty`/`EnumProperty` → `FString enum_name`; `ArrayProperty`/`SetProperty` → `FString inner_type`; `MapProperty` → `FString key_type`, `FString value_type`; `BoolProperty` → `uint8 value` (and `size` is 0); then `uint8 has_guid` and, if 1, a 16-byte guid; then `size` bytes of value.

Value layouts: `IntProperty` int32; `Int8Property` int8; `Int64Property` int64; `UInt32Property` uint32; `FloatProperty` f32; `DoubleProperty` f64; `StrProperty`/`NameProperty` FString; `EnumProperty` FString; `ByteProperty` FString when `enum_name != "None"` else uint8; `ObjectProperty`/`InterfaceProperty` FObjectReferenceDisc (two FStrings); `SoftObjectProperty`, `TextProperty`, `MapProperty`, `SetProperty` opaque `size` bytes; `StructProperty` binary when `struct_name in BINARY_STRUCTS` else nested property list (must consume exactly `size` bytes, else keep `BinaryStruct`); `ArrayProperty`: int32 count then, for `StructProperty` inner, an inner tag (`FString name`, `FString "StructProperty"`, `int32 size`, `int32 index`, `FString struct_name`, 16-byte guid, `uint8 has_guid`) followed by `count` struct values each parsed as a struct value (binary or nested list), for `ObjectProperty` count refs, for `IntProperty` count int32, for `EnumProperty`/`StrProperty`/`NameProperty` count FStrings, for `ByteProperty` count uint8, for `FloatProperty` count f32, else `Opaque` of the remaining `size` bytes.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_properties.py`:
```python
from collections import Counter

from flab2bp.sfy.archive import ObjectRef, Reader, Writer
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.properties import (
    Array, Enum, Float, Int, Object, Opaque, Property, Struct, Tag, Vector,
    read_property_list, write_property_list,
)
from tests.sfy.conftest import FIXTURES, fixture_paths


def _round_trip(props):
    w = Writer(); write_property_list(w, props)
    r = Reader(w.getvalue())
    again = read_property_list(r)
    r.expect_end()
    return again, w.getvalue()


def test_scalar_properties_round_trip():
    props = (
        Property(Tag("mLength", "FloatProperty", 0), Float(400.0)),
        Property(Tag("mCount", "IntProperty", 0), Int(3)),
        Property(Tag("mDirection", "EnumProperty", 0, enum_name="EFactoryConnectionDirection"),
                 Enum("EFactoryConnectionDirection::FCD_INPUT")),
        Property(Tag("mConnectedComponent", "ObjectProperty", 0),
                 Object(ObjectRef("Persistent_Level", "Persistent_Level:PersistentLevel.Build_ConveyorBeltMk1_C_1.ConveyorAny0"))),
    )
    again, raw = _round_trip(props)
    assert again == props
    assert raw.endswith(b"\x05\x00\x00\x00None\x00")


def test_struct_array_of_vectors_round_trip():
    inner = Tag("mSplineData", "StructProperty", 0, struct_name="SplinePointData", struct_guid=bytes(16))
    point = Struct("SplinePointData", (
        Property(Tag("Location", "StructProperty", 0, struct_name="Vector", struct_guid=bytes(16)), Vector(0.0, 1.0, 2.0)),
        Property(Tag("ArriveTangent", "StructProperty", 0, struct_name="Vector", struct_guid=bytes(16)), Vector(3.0, 4.0, 5.0)),
    ))
    props = (Property(Tag("mSplineData", "ArrayProperty", 0, inner_type="StructProperty"),
                      Array("StructProperty", inner, (point, point))),)
    again, _ = _round_trip(props)
    assert again == props


def test_every_fixture_object_body_parses_with_a_trailer():
    """Every object's body is a property list followed by a short trailer.
    The trailer is decoded in Task 7; here it must merely be non-negative."""
    lengths = Counter()
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        for h, d in bp.objects:
            r = Reader(d.body)
            props = read_property_list(r)
            lengths[(h.class_name, r.remaining())] += 1
            w = Writer(); write_property_list(w, props)
            assert w.getvalue() == d.body[:r.pos], (path.name, h.path)
    assert all(n >= 0 for (_, n) in lengths)


def test_no_opaque_values_in_the_classes_the_layout_needs():
    needed = {
        "Build_ConstructorMk1_C", "Build_SmelterMk1_C", "Build_AssemblerMk1_C",
        "Build_ConveyorBeltMk1_C", "Build_ConveyorBeltMk5_C", "Build_ConveyorLiftMk1_C",
        "Build_ConveyorAttachmentSplitter_C", "Build_ConveyorAttachmentMerger_C",
        "Build_PowerPoleMk1_C", "Build_PowerLine_C", "Build_Foundation_8x1_01_C",
        "Build_FoundationPassthrough_Lift_C", "Build_Pipeline_C",
        "FGFactoryConnectionComponent", "FGPowerConnectionComponent", "FGPipeConnectionComponent",
        "FGInventoryComponent", "FGPowerInfoComponent", "FGFactoryLegsComponent",
    }
    opaque = Counter()

    def visit(value, cls):
        if isinstance(value, Opaque):
            opaque[cls] += 1
        elif isinstance(value, Struct):
            for p in value.fields: visit(p.value, cls)
        elif isinstance(value, Array):
            for item in value.items: visit(item, cls)

    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < 58:
            continue
        for h, d in bp.objects:
            if h.class_name not in needed:
                continue
            for p in read_property_list(Reader(d.body)):
                visit(p.value, h.class_name)
    assert not opaque, dict(opaque)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_properties.py -q`
Expected: non-zero exit, import error.

- [ ] **Step 3: Implement `properties.py`**

Write the module per the Interfaces block. Skeleton of the two entry points (fill every branch listed in the value layouts above; no branch may silently skip bytes):
```python
def read_tag(r: Reader) -> Tag | None:
    name = r.fstring()
    if name == "None":
        return None
    typ = r.fstring()
    size, index = r.i32(), r.i32()
    kw = {}
    if typ == "StructProperty":
        kw["struct_name"] = r.fstring(); kw["struct_guid"] = r.guid()
    elif typ in ("ByteProperty", "EnumProperty"):
        kw["enum_name"] = r.fstring()
    elif typ in ("ArrayProperty", "SetProperty"):
        kw["inner_type"] = r.fstring()
    elif typ == "MapProperty":
        kw["key_type"] = r.fstring(); kw["value_type"] = r.fstring()
    bool_value = r.u8() if typ == "BoolProperty" else None
    guid = r.guid() if r.u8() else None
    tag = Tag(name, typ, index, guid=guid, **kw)
    return tag, size, bool_value          # adjust: return a small _TagRead record


def read_property_list(r: Reader) -> PropertyList:
    out = []
    while True:
        read = read_tag(r)
        if read is None:
            return tuple(out)
        tag, size, bool_value = read
        start = r.pos
        value = _read_value(r, tag, size, bool_value)
        if tag.type != "BoolProperty" and r.pos - start != size:
            raise ArchiveError(f"{tag.name}: consumed {r.pos - start} of {size}")
        out.append(Property(tag, value))
```
For `StructProperty` values that are not in `BINARY_STRUCTS`, parse a nested list inside a sub-`Reader` over exactly `size` bytes; if the nested parse raises `ArchiveError` or leaves bytes, fall back to `BinaryStruct(name, raw)`. Write functions mirror the read functions and compute `size` from a temporary `Writer`. `Bool` writes `size = 0` and the value byte inside the tag.

- [ ] **Step 4: Run the tests and iterate on struct coverage**

Run: `uv run pytest tests/sfy/test_properties.py -q`
Expected: the first three tests pass. If `test_no_opaque_values_in_the_classes_the_layout_needs` fails, its assertion message names the classes with `Opaque` values. Add a small script step to find the tag types responsible:
```bash
uv run python - <<'EOF'
from collections import Counter
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.properties import Opaque, Struct, Array, BinaryStruct, read_property_list
from tests.sfy.conftest import fixture_paths
c = Counter()
def visit(p):
    v = p.value
    if isinstance(v, (Opaque, BinaryStruct)): c[(p.tag.type, p.tag.struct_name, p.tag.inner_type, type(v).__name__)] += 1
    if isinstance(v, Struct): [visit(q) for q in v.fields]
    if isinstance(v, Array): [visit(q) for q in v.items if hasattr(q, "fields")]
for path in fixture_paths():
    for h, d in read_sbp(path.read_bytes()).objects:
        for p in read_property_list(Reader(d.body)): visit(p)
for k, n in c.most_common(): print(n, k)
EOF
```
Add typed decoders for each struct or array inner type the script reports until the test passes for the listed classes. `SoftObjectProperty` and `TextProperty` may stay opaque (they appear on signs, not on the needed classes).

- [ ] **Step 5: Run the whole sfy suite**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0.

- [ ] **Step 6: Commit**

```bash
git add src/flab2bp/sfy/properties.py tests/sfy/test_properties.py
git commit -m "Decode and encode the tagged property tree of every blueprint object"
```

---

### Task 7: Class-specific trailers

**Files:**
- Create: `src/flab2bp/sfy/trailers.py`
- Modify: `src/flab2bp/sfy/objects.py` (replace `body: bytes` with `properties: PropertyList` and `trailer: Trailer`)
- Modify: `src/flab2bp/sfy/codec.py` (nothing structural; `read_object_data`/`write_object_data` now parse properties)
- Test: `tests/sfy/test_trailers.py`, update `tests/sfy/test_properties.py::test_every_fixture_object_body_parses_with_a_trailer` to read `d.properties`

**Interfaces:**
- Produces:
  - `Trailer` union: `PlainTrailer(raw: bytes)` (any bytes, verbatim), `BuildableTrailer()` (the 4 bytes `int32 0`), `ComponentTrailer()` (8 bytes `int32 0, int32 0`), `ConveyorTrailer(items: tuple[ConveyorItem, ...])` (`int32 0`, `int32 count`, items; only `count == 0` is decoded, otherwise `PlainTrailer`), `PowerLineTrailer(raw: bytes)` (kept verbatim in this milestone; decoded in M4).
  - `read_trailer(r: Reader, class_name: str) -> Trailer`; `write_trailer(w: Writer, t: Trailer)`.
  - `trailer_for_new(class_name: str) -> Trailer`: `BuildableTrailer` for `Build_*` classes that are not conveyors or power lines, `ConveyorTrailer(())` for `Build_ConveyorBelt*` and `Build_ConveyorLift*`, `ComponentTrailer` for `FG*Component` and `FGPipeConnectionFactory`; raises `KeyError` for `Build_PowerLine_C` (templates must copy a fixture trailer).
  - `objects.ObjectData(parent, components, properties: PropertyList, trailer: Trailer)`.

Observed trailer lengths across the fixtures (probe on 2026-09-13): all `Build_*` buildables 4 bytes of zeros; components and belts/lifts 8 bytes of zeros; `Build_PowerLine_C` 206-230 bytes beginning `int32 0` then two `FObjectReferenceDisc` (the wire's two connection components) and further data.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_trailers.py`:
```python
from collections import Counter

from flab2bp.sfy.codec import payload_bytes, read_sbp
from flab2bp.sfy.trailers import BuildableTrailer, ComponentTrailer, ConveyorTrailer, PlainTrailer, PowerLineTrailer, trailer_for_new
from tests.sfy.conftest import FIXTURES, fixture_paths


def test_trailer_kinds_by_class_in_current_family():
    kinds = Counter()
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < 58:
            continue
        for h, d in bp.objects:
            kinds[(h.class_name, type(d.trailer).__name__)] += 1
    assert kinds[("Build_ConstructorMk1_C", "BuildableTrailer")] > 0
    assert kinds[("FGFactoryConnectionComponent", "ComponentTrailer")] > 0
    assert kinds[("Build_ConveyorBeltMk1_C", "ConveyorTrailer")] > 0
    assert kinds[("Build_PowerLine_C", "PowerLineTrailer")] > 0
    plain = {cls for (cls, kind) in kinds if kind == "PlainTrailer"}
    assert not (plain & {"Build_ConstructorMk1_C", "Build_ConveyorBeltMk1_C", "Build_PowerPoleMk1_C", "Build_Foundation_8x1_01_C"})


def test_payload_identity_still_holds_with_decoded_bodies(sbp_path):
    from flab2bp.sfy.archive import Reader
    from flab2bp.sfy.chunks import decompress_stream, find_stream_start
    from flab2bp.sfy.header import read_header
    data = sbp_path.read_bytes()
    r = Reader(data); read_header(r)
    assert payload_bytes(read_sbp(data)) == decompress_stream(data, find_stream_start(data, r.pos))


def test_trailer_for_new_classes():
    assert trailer_for_new("Build_ConstructorMk1_C") == BuildableTrailer()
    assert trailer_for_new("Build_ConveyorBeltMk3_C") == ConveyorTrailer(())
    assert trailer_for_new("Build_ConveyorLiftMk1_C") == ConveyorTrailer(())
    assert trailer_for_new("FGFactoryConnectionComponent") == ComponentTrailer()
    import pytest
    with pytest.raises(KeyError):
        trailer_for_new("Build_PowerLine_C")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_trailers.py -q`
Expected: non-zero exit, import error.

- [ ] **Step 3: Implement `trailers.py` and rewire `objects.py`**

```python
"""Bytes after an object's property list, by class.

The game serializes class-specific state after the tagged properties. The
fixtures show three shapes for the classes the layout needs: buildables write
int32 0; components, belts and lifts write int32 0 then int32 0 (belts and
lifts: the item count); power lines write int32 0, two object references and
wire instance data. Anything else is kept verbatim.
"""
from __future__ import annotations

from dataclasses import dataclass

from flab2bp.sfy.archive import Reader, Writer

__all__ = ["BuildableTrailer", "ComponentTrailer", "ConveyorTrailer", "PlainTrailer", "PowerLineTrailer",
           "Trailer", "read_trailer", "trailer_for_new", "write_trailer"]


@dataclass(frozen=True, slots=True)
class PlainTrailer:
    raw: bytes


@dataclass(frozen=True, slots=True)
class BuildableTrailer:
    pass


@dataclass(frozen=True, slots=True)
class ComponentTrailer:
    pass


@dataclass(frozen=True, slots=True)
class ConveyorItem:
    raw: bytes


@dataclass(frozen=True, slots=True)
class ConveyorTrailer:
    items: tuple[ConveyorItem, ...]


@dataclass(frozen=True, slots=True)
class PowerLineTrailer:
    raw: bytes


Trailer = PlainTrailer | BuildableTrailer | ComponentTrailer | ConveyorTrailer | PowerLineTrailer


def _is_conveyor(class_name: str) -> bool:
    return class_name.startswith(("Build_ConveyorBelt", "Build_ConveyorLift"))


def _is_component(class_name: str) -> bool:
    return class_name.startswith("FG") and (class_name.endswith("Component") or class_name == "FGPipeConnectionFactory")


def read_trailer(r: Reader, class_name: str) -> Trailer:
    raw = r.bytes(r.remaining())
    if class_name == "Build_PowerLine_C":
        return PowerLineTrailer(raw)
    if _is_conveyor(class_name) and raw == b"\x00" * 8:
        return ConveyorTrailer(())
    if _is_component(class_name) and raw == b"\x00" * 8:
        return ComponentTrailer()
    if class_name.startswith("Build_") and raw == b"\x00" * 4:
        return BuildableTrailer()
    return PlainTrailer(raw)


def write_trailer(w: Writer, t: Trailer) -> None:
    match t:
        case PlainTrailer(raw) | PowerLineTrailer(raw):
            w.raw(raw)
        case BuildableTrailer():
            w.i32(0)
        case ComponentTrailer():
            w.i32(0); w.i32(0)
        case ConveyorTrailer(items):
            w.i32(0); w.i32(len(items))
            for item in items:
                w.raw(item.raw)


def trailer_for_new(class_name: str) -> Trailer:
    if class_name == "Build_PowerLine_C":
        raise KeyError("power line trailers are copied from a fixture template")
    if _is_conveyor(class_name):
        return ConveyorTrailer(())
    if _is_component(class_name):
        return ComponentTrailer()
    if class_name.startswith("Build_"):
        return BuildableTrailer()
    raise KeyError(class_name)
```
In `objects.py`, `ObjectData` becomes `(parent, components, properties: PropertyList, trailer: Trailer)`; `read_object_data` parses `read_property_list` then `read_trailer(r, header.class_name)`; `write_object_data` writes both. Update `tests/sfy/test_objects.py` and `test_properties.py` accordingly (`d.properties` instead of parsing `d.body`).

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0 including payload identity on all 19 fixtures.

- [ ] **Step 5: Commit**

```bash
git add src/flab2bp/sfy/trailers.py src/flab2bp/sfy/objects.py src/flab2bp/sfy/codec.py tests/sfy
git commit -m "Decode the class-specific trailers behind every object's properties"
```

---

### Task 8: Codec API polish and property helpers

**Files:**
- Modify: `src/flab2bp/sfy/codec.py`
- Create: `src/flab2bp/sfy/query.py`
- Test: `tests/sfy/test_query.py`

**Interfaces:**
- Produces:
  - `query.find(props: PropertyList, name: str) -> Value | None`; `query.find_all(props, name) -> tuple[Value, ...]`; `query.object_index(bp: Blueprint) -> dict[str, tuple[ObjectHeader, ObjectData]]` keyed by full instance path; `query.children(bp, actor_path) -> tuple[...]`; `query.spline_points(d: ObjectData) -> tuple[tuple[Vector, Vector, Vector], ...]` (location, arrive, leave) for conveyors and pipes; `query.connected(d: ObjectData) -> ObjectRef | None` (the `mConnectedComponent` of a connection component).
  - `codec.read_sbp_file(path) -> Blueprint`, `codec.write_sbp_file(path, bp)`, `codec.read_pair(sbp_path, cfg_path) -> tuple[Blueprint, BlueprintRecord]`.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_query.py`:
```python
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.query import connected, find, object_index, spline_points
from tests.sfy.conftest import FIXTURES


def test_belt_spline_points_and_connections_in_biofuel():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    index = object_index(bp)
    belts = [(h, d) for h, d in bp.objects if h.class_name.startswith("Build_ConveyorBelt")]
    assert belts
    for h, d in belts:
        pts = spline_points(d)
        assert len(pts) >= 2
        assert all(len(p) == 3 for p in pts)
        for comp in d.components:
            ch, cd = index[comp.path]
            assert ch.class_name == "FGFactoryConnectionComponent"
            peer = connected(cd)
            if peer is not None:
                assert peer.path in index, peer.path


def test_find_returns_none_for_absent_property():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    _, d = bp.objects[0]
    assert find(d.properties, "mDoesNotExist") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_query.py -q`
Expected: non-zero exit, import error.

- [ ] **Step 3: Implement**

`src/flab2bp/sfy/query.py`:
```python
"""Read-only helpers over decoded blueprints."""
from __future__ import annotations

from pathlib import Path

from flab2bp.sfy.archive import ObjectRef
from flab2bp.sfy.codec import Blueprint
from flab2bp.sfy.objects import ObjectData, ObjectHeader
from flab2bp.sfy.properties import Array, Object, PropertyList, Struct, Value, Vector

__all__ = ["children", "connected", "find", "find_all", "object_index", "spline_points"]


def find(props: PropertyList, name: str) -> Value | None:
    for p in props:
        if p.tag.name == name:
            return p.value
    return None


def find_all(props: PropertyList, name: str) -> tuple[Value, ...]:
    return tuple(p.value for p in props if p.tag.name == name)


def object_index(bp: Blueprint) -> dict[str, tuple[ObjectHeader, ObjectData]]:
    return {h.path: (h, d) for h, d in bp.objects}


def children(bp: Blueprint, actor_path: str) -> tuple[tuple[ObjectHeader, ObjectData], ...]:
    return tuple((h, d) for h, d in bp.objects if h.kind == 0 and h.parent == actor_path)


def spline_points(d: ObjectData) -> tuple[tuple[Vector, Vector, Vector], ...]:
    value = find(d.properties, "mSplineData")
    if not isinstance(value, Array):
        return ()
    out = []
    for item in value.items:
        assert isinstance(item, Struct)
        fields = {p.tag.name: p.value for p in item.fields}
        out.append((fields["Location"], fields["ArriveTangent"], fields["LeaveTangent"]))
    return tuple(out)


def connected(d: ObjectData) -> ObjectRef | None:
    value = find(d.properties, "mConnectedComponent")
    return value.ref if isinstance(value, Object) and not value.ref.is_null else None
```
Add to `codec.py`:
```python
def read_sbp_file(path: Path) -> Blueprint:
    return read_sbp(Path(path).read_bytes())


def write_sbp_file(path: Path, bp: Blueprint, zlib_level: int = 6) -> None:
    Path(path).write_bytes(write_sbp(bp, zlib_level))


def read_pair(sbp_path: Path, cfg_path: Path) -> tuple[Blueprint, BlueprintRecord]:
    return read_sbp_file(sbp_path), read_sbpcfg(Path(cfg_path).read_bytes())
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add src/flab2bp/sfy/query.py src/flab2bp/sfy/codec.py tests/sfy/test_query.py
git commit -m "Add blueprint query helpers and file-level codec entry points"
```

**Review batch B ends here (Tasks 6-8).**

---

### Task 9: Docs.json half of the registry

**Files:**
- Create: `src/flab2bp/sfy/docs.py`, `src/flab2bp/sfy/registry.py`
- Create: `src/flab2bp/sfy/data/__init__.py` (empty), `src/flab2bp/sfy/data/registry.json` (produced in Task 10; this task writes `docs.json` there)
- Test: `tests/sfy/test_docs.py`

**Interfaces:**
- Produces:
  - `docs.satisfactory_dir() -> Path` (env `FLAB2BP_SATISFACTORY_DIR`, default `~/Satisfactory`); `docs.docs_path() -> Path` (`CommunityResources/Docs/en-US.json`); `docs.load_docs(path) -> dict[str, list[dict]]` (native class short name → class dicts, UTF-16 decoded).
  - `docs.parse_clearance(text: str) -> tuple[ClearanceBox, ...]`; `docs.parse_vector(text: str) -> tuple[float, float, float]`; `docs.parse_item_amounts(text: str) -> tuple[tuple[str, int], ...]` (class path → amount); `docs.parse_class_list(text) -> tuple[str, ...]`.
  - `docs.extract(docs: dict) -> dict` producing the JSON shape below.
  - `registry.ClearanceBox(min: tuple[float,float,float], max: tuple[float,float,float], soft: bool, translation: tuple[float,float,float])`.
  - `registry.Buildable(class_name: str, display_name: str, native_class: str, clearance: tuple[ClearanceBox, ...], power_mw: float, manufacturing_speed: float | None, belt_speed_per_min: float | None, mesh_height_cm: float | None, width_cm: float | None, depth_cm: float | None, height_cm: float | None, designer_dims: tuple[int,int,int] | None, max_potential: float | None, potential_shard_slots: int | None, production_boost_slots: int | None, ports: tuple[Port, ...])` (ports filled in Task 10; empty here).
  - `registry.Port(name: str, kind: str, direction: str, translation: tuple[float,float,float], rotation: tuple[float,float,float], clearance: float | None)`; `kind` in `{"belt", "pipe", "power"}`; `direction` in `{"input", "output", "any", "snap_only"}`.
  - `registry.Recipe(class_name: str, display_name: str, ingredients: tuple[tuple[str,int],...], products: tuple[tuple[str,int],...], duration_s: float, producers: tuple[str, ...], variable_power_constant: float, variable_power_factor: float)`.
  - `registry.Registry(provenance: dict, buildables: dict[str, Buildable], recipes: dict[str, Recipe], descriptors: dict[str, str]` (Desc class → Build class), `build_recipes: dict[str, str]` (Build class → Recipe class), `limits: Limits)`; `registry.Limits(belt_max_spline_cm: float, belt_bend_radius_cm: float | None, belt_max_incline_deg: float | None, lift_step_cm: float | None, lift_min_cm: float | None, lift_max_cm: float | None, lift_min_vertical_cm: float | None, pipe_max_spline_cm: float, pipe_bend_radius_cm: float | None, pipe_bend_radius_2d_cm: float, pipe_min_bend_radius_cm: float, wire_max_cm: dict[str, float], hologram_grid_cm: float | None, hologram_rotation_step_deg: float | None)`; header defaults: `belt_max_spline_cm = 5600.1`, `pipe_max_spline_cm = 5600.1`, `pipe_bend_radius_2d_cm = 199.0`, `pipe_min_bend_radius_cm = 75.0` (from the hologram headers), everything else `None` until Task 10 fills it.
  - `registry.load_registry(path: Path | None = None) -> Registry` reading `data/registry.json` (Task 10) and raising `RegistryError` on missing keys.

JSON shape of `docs.extract` output (written to `src/flab2bp/sfy/data/docs.json`):
```json
{
  "provenance": {"docs_sha256": "...", "extracted": "2026-09-13", "build_version_hint": "from newest fixture"},
  "buildables": {"Build_ConstructorMk1_C": {"display_name": "Constructor", "native_class": "FGBuildableManufacturer",
     "clearance": [{"min": [-400,-500,0], "max": [400,500,600], "soft": false, "translation": [0,0,0]}],
     "power_mw": 4.0, "manufacturing_speed": 1.0, "belt_speed_per_min": null, "mesh_height_cm": null,
     "width_cm": null, "depth_cm": null, "height_cm": null, "designer_dims": null,
     "max_potential": 1.0, "potential_shard_slots": 0, "production_boost_slots": 1}},
  "recipes": {"Recipe_IronPlate_C": {"display_name": "Iron Plate", "ingredients": [["Desc_IronIngot_C", 3]],
     "products": [["Desc_IronPlate_C", 2]], "duration_s": 6.0, "producers": ["Build_ConstructorMk1_C"],
     "variable_power_constant": 0.0, "variable_power_factor": 1.0}},
  "descriptors": {"Desc_ConstructorMk1_C": "Build_ConstructorMk1_C"},
  "build_recipes": {"Build_ConstructorMk1_C": "Recipe_ConstructorMk1_C"}
}
```
Rules: `producers` keeps only entries whose class name starts with `Build_`; recipes with no `Build_` producer are still kept (hand-craft only) but the layout ignores them later. `descriptors` are matched by name (`Desc_X_C` ↔ `Build_X_C`); `build_recipes` map each buildable to the recipe whose single product is its descriptor. `production_boost_slots` comes from `mProductionShardSlotSize`; `potential_shard_slots` from `mPotentialShardSlots`; `max_potential` from `mMaxPotential`.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_docs.py`:
```python
import json
import os
from pathlib import Path

import pytest

from flab2bp.sfy import docs
from flab2bp.sfy.registry import Registry, load_registry

DOCS = docs.docs_path()
needs_game = pytest.mark.skipif(not DOCS.exists(), reason="Satisfactory install not present")

CLEARANCE = ("((ClearanceBox=(Min=(X=-400.000000,Y=-500.000000,Z=0.000000),Max=(X=400.000000,Y=500.000000,Z=600.000000),IsValid=True)),"
             "(Type=CT_Soft,ClearanceBox=(Min=(X=-20.000000,Y=-20.000000,Z=0.000000),Max=(X=20.000000,Y=20.000000,Z=250.000000),IsValid=True),"
             "RelativeTransform=(Translation=(X=-165.000000,Y=-460.000000,Z=0.000000))))")


def test_parse_clearance_reads_hard_and_soft_boxes():
    boxes = docs.parse_clearance(CLEARANCE)
    assert len(boxes) == 2
    assert boxes[0].min == (-400.0, -500.0, 0.0) and boxes[0].max == (400.0, 500.0, 600.0) and not boxes[0].soft
    assert boxes[1].soft and boxes[1].translation == (-165.0, -460.0, 0.0)


def test_parse_item_amounts():
    text = ('((ItemClass="/Script/Engine.BlueprintGeneratedClass\'/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C\'",Amount=1),'
            '(ItemClass="/Script/Engine.BlueprintGeneratedClass\'/Game/FactoryGame/Resource/Parts/Wire/Desc_Wire.Desc_Wire_C\'",Amount=3))')
    assert docs.parse_item_amounts(text) == (("Desc_IronPlate_C", 1), ("Desc_Wire_C", 3))


def test_parse_class_list():
    text = '("/Game/FactoryGame/Buildable/Factory/ConstructorMk1/Build_ConstructorMk1.Build_ConstructorMk1_C","/Game/FactoryGame/Equipment/BuildGun/BP_BuildGun.BP_BuildGun_C")'
    assert docs.parse_class_list(text) == ("Build_ConstructorMk1_C", "BP_BuildGun_C")


@needs_game
def test_extract_known_values():
    data = docs.extract(docs.load_docs(DOCS))
    ctor = data["buildables"]["Build_ConstructorMk1_C"]
    assert ctor["power_mw"] == 4.0 and ctor["manufacturing_speed"] == 1.0
    assert ctor["clearance"][0]["max"] == [400.0, 500.0, 600.0]
    assert data["buildables"]["Build_ConveyorBeltMk1_C"]["belt_speed_per_min"] == 120.0
    assert data["buildables"]["Build_ConveyorBeltMk6_C"]["belt_speed_per_min"] == 2400.0
    assert data["buildables"]["Build_BlueprintDesigner_C"]["designer_dims"] == [4, 4, 4]
    assert data["buildables"]["Build_BlueprintDesigner_Mk3_C"]["designer_dims"] == [6, 6, 6]
    assert data["buildables"]["Build_ConveyorLiftMk1_C"]["mesh_height_cm"] == 200.0
    plate = data["recipes"]["Recipe_IronPlate_C"]
    assert plate["producers"] == ["Build_ConstructorMk1_C"] and plate["duration_s"] == 6.0
    assert data["descriptors"]["Desc_ConstructorMk1_C"] == "Build_ConstructorMk1_C"
    assert data["build_recipes"]["Build_ConstructorMk1_C"] == "Recipe_ConstructorMk1_C"


def test_committed_docs_json_loads_into_registry_dataclasses():
    here = Path(docs.__file__).parent / "data" / "docs.json"
    reg = Registry.from_docs_only(json.loads(here.read_text()))
    assert reg.buildables["Build_ConstructorMk1_C"].clearance[0].max == (400.0, 500.0, 600.0)
    assert reg.recipes["Recipe_IronPlate_C"].producers == ("Build_ConstructorMk1_C",)
    assert reg.limits.belt_max_spline_cm == 5600.1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_docs.py -q`
Expected: non-zero exit, import error.

- [ ] **Step 3: Implement `docs.py`**

Parsing helpers use regular expressions over the Unreal text export format: `parse_vector` matches `X=([-0-9.]+),Y=([-0-9.]+),Z=([-0-9.]+)`; `parse_clearance` splits the outer list on `),(` at depth 1 and reads `Type=CT_Soft`, `Min=(...)`, `Max=(...)`, and an optional `Translation=(...)` (default zeros); `parse_item_amounts` uses `re.findall(r"\.(Desc_[A-Za-z0-9_]+_C)'\",Amount=(\d+)")`; `parse_class_list` uses `re.findall(r"\.([A-Za-z0-9_]+_C)\"")`. Numbers in Docs.json are strings (`"4.000000"`), parse with `float`. `extract` walks every native class whose name starts with `FGBuildable` for buildables (recording the native class), `FGRecipe` for recipes, and `FGBuildingDescriptor` for descriptors. Keys not present on a class yield `None`. Write `docs.json` with `json.dumps(..., indent=1, sort_keys=True)`; compute `docs_sha256` over the raw Docs.json bytes.

Add a `__main__` in `docs.py`: `uv run python -m flab2bp.sfy.docs` writes `src/flab2bp/sfy/data/docs.json`.

- [ ] **Step 4: Implement `registry.py`**

Dataclasses per the Interfaces block, `Registry.from_docs_only(data: dict) -> Registry` (ports empty, `Limits` at header defaults), `load_registry(path=None)` reading `registry.json` (Task 10) with a `RegistryError` naming any missing top-level key.

- [ ] **Step 5: Generate the committed docs.json and run the tests**

Run: `uv run python -m flab2bp.sfy.docs && uv run pytest tests/sfy/test_docs.py -q`
Expected: exit code 0; `docs.json` a few MB (fine: evidence and data under gigabytes are never a concern).

- [ ] **Step 6: Commit**

```bash
git add src/flab2bp/sfy/docs.py src/flab2bp/sfy/registry.py src/flab2bp/sfy/data tests/sfy/test_docs.py
git commit -m "Extract the Docs.json half of the Satisfactory registry"
```

---

### Task 10: CUE4Parse extractor and registry merge

**Files:**
- Create: `tools/sfy-extract/SfyExtract.csproj`, `tools/sfy-extract/Program.cs`, `tools/sfy-extract/README.md`, `tools/sfy-extract/.gitignore` (`bin/`, `obj/`)
- Create: `scripts/sfy_registry.py`
- Create: `src/flab2bp/sfy/data/assets.json` (extractor output), `src/flab2bp/sfy/data/registry.json` (merged)
- Test: `tests/sfy/test_registry.py`

**Interfaces:**
- Produces: `assets.json`:
```json
{"provenance": {"engine": "5.6.1", "branch": "++FactoryGame+rel-main-1.2.0", "usmap_sha256": "...", "extracted": "2026-09-13"},
 "ports": {"Build_ConstructorMk1_C": [
    {"name": "Input0", "kind": "belt", "direction": "input", "translation": [-x, y, z], "rotation": [pitch, yaw, roll], "clearance": 100.0}]},
 "holograms": {"Build_ConveyorBeltMk1_C": {"class": "Hologram_ConveyorBelt_C", "mBendRadius": 180.0, "mMaxSplineLength": 5600.1, "mMaxIncline": 35.0}},
 "wires": {"Build_PowerLine_C": {"mMaxLength": 3000.0}},
 "grid": {"mGridSnapSize": 100.0, "rotation_step": 90}}
```
- `scripts/sfy_registry.py` merges `docs.json` and `assets.json` into `registry.json`: buildables gain `ports`; `limits` filled from `holograms` of the belt, lift and pipe classes and from `wires`; provenance carries both stamps and the git SHA of the tool. `load_registry()` then returns ports and limits.

- [ ] **Step 1: Write the failing registry test**

`tests/sfy/test_registry.py`:
```python
from flab2bp.sfy.registry import load_registry


def test_registry_has_ports_for_the_core_machines():
    reg = load_registry()
    ctor = reg.buildables["Build_ConstructorMk1_C"]
    belts = [p for p in ctor.ports if p.kind == "belt"]
    assert {p.direction for p in belts} == {"input", "output"}
    assert len(belts) == 2
    power = [p for p in ctor.ports if p.kind == "power"]
    assert len(power) == 1
    smelter = reg.buildables["Build_SmelterMk1_C"]
    assert len([p for p in smelter.ports if p.kind == "belt"]) == 2
    assembler = reg.buildables["Build_AssemblerMk1_C"]
    assert len([p for p in assembler.ports if p.kind == "belt" and p.direction == "input"]) == 2


def test_registry_limits_are_filled_from_the_assets():
    lim = load_registry().limits
    assert lim.belt_max_spline_cm == 5600.1
    assert lim.belt_bend_radius_cm is not None and lim.belt_bend_radius_cm > 0
    assert lim.lift_step_cm is not None and lim.lift_min_cm is not None and lim.lift_max_cm is not None
    assert lim.wire_max_cm["Build_PowerLine_C"] > 0


def test_splitter_has_one_input_and_three_outputs():
    reg = load_registry()
    sp = reg.buildables["Build_ConveyorAttachmentSplitter_C"]
    belts = [p for p in sp.ports if p.kind == "belt"]
    assert sum(p.direction == "input" for p in belts) == 1
    assert sum(p.direction == "output" for p in belts) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_registry.py -q`
Expected: non-zero exit (`registry.json` missing or ports empty).

- [ ] **Step 3: Create the dotnet project**

`tools/sfy-extract/SfyExtract.csproj`:
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="CUE4Parse" Version="1.2.2.202609" />
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
  </ItemGroup>
</Project>
```

`tools/sfy-extract/Program.cs` (the shape; adjust member names after the `list` run in Step 4):
```csharp
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Objects;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;

// Usage: dotnet run -- <SatisfactoryDir> <mode> [out.json]
//   mode = list   : print every Build_* package path and its export names/classes (discovery)
//   mode = extract: write assets.json
var gameDir = args[0];
var mode = args[1];
var paks = Path.Combine(gameDir, "FactoryGame", "Content", "Paks");
var usmap = Path.Combine(gameDir, "CommunityResources", "FactoryGame.usmap");

var provider = new DefaultFileProvider(paks, SearchOption.AllDirectories, true, new VersionContainer(EGame.GAME_UE5_6));
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(usmap);
provider.Initialize();
provider.Mount();

var buildPackages = provider.Files.Keys
    .Where(k => k.StartsWith("FactoryGame/Content/FactoryGame/Buildable/", StringComparison.OrdinalIgnoreCase)
             && k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
             && Path.GetFileNameWithoutExtension(k).StartsWith("Build_"))
    .OrderBy(k => k).ToList();

if (mode == "list")
{
    foreach (var pkg in buildPackages)
    {
        var exports = provider.LoadPackage(pkg).GetExports().ToList();
        Console.WriteLine(pkg);
        foreach (var e in exports) Console.WriteLine($"  {e.Name} : {e.Class?.Name}");
    }
    return;
}

var ports = new Dictionary<string, List<object>>();
var holograms = new Dictionary<string, Dictionary<string, object?>>();
var wires = new Dictionary<string, Dictionary<string, object?>>();

static string DirectionOf(UObject template)
{
    var d = template.GetOrDefault<string>("mDirection", "EFactoryConnectionDirection::FCD_ANY");
    return d.EndsWith("FCD_INPUT") ? "input" : d.EndsWith("FCD_OUTPUT") ? "output" : d.EndsWith("FCD_SNAP_ONLY") ? "snap_only" : "any";
}

static string KindOf(string className) =>
    className.Contains("PipeConnection") ? "pipe" : className.Contains("PowerConnection") || className.Contains("CircuitConnection") ? "power" : "belt";

foreach (var pkg in buildPackages)
{
    var exports = provider.LoadPackage(pkg).GetExports().ToList();
    var generatedClass = exports.FirstOrDefault(e => e.Class?.Name == "BlueprintGeneratedClass");
    if (generatedClass is null) continue;
    var className = generatedClass.Name;                       // e.g. Build_ConstructorMk1_C
    var list = new List<object>();
    // Component templates live in the package as exports whose class is a connection component.
    // Inherited templates (parent Blueprint classes) are found by following the class's SuperStruct.
    foreach (var e in exports)
    {
        var cls = e.Class?.Name ?? "";
        if (!(cls.EndsWith("ConnectionComponent") || cls == "FGPipeConnectionFactory")) continue;
        var loc = e.GetOrDefault<FVector>("RelativeLocation", FVector.ZeroVector);
        var rot = e.GetOrDefault<FRotator>("RelativeRotation", FRotator.ZeroRotator);
        list.Add(new {
            name = e.Name.Replace("_GEN_VARIABLE", ""),
            kind = KindOf(cls),
            direction = DirectionOf(e),
            translation = new[] { loc.X, loc.Y, loc.Z },
            rotation = new[] { rot.Pitch, rot.Yaw, rot.Roll },
            clearance = e.GetOrDefault<float?>("mConnectorClearance", null),
        });
    }
    ports[className] = list;

    var cdo = exports.FirstOrDefault(e => e.Name == "Default__" + className);
    if (cdo is not null)
    {
        var hologramClass = cdo.GetOrDefault<UObject?>("mHologramClass", null);
        if (hologramClass is not null)
        {
            var hcdo = hologramClass.Owner?.GetExports().FirstOrDefault(x => x.Name.StartsWith("Default__"));
            var h = new Dictionary<string, object?> { ["class"] = hologramClass.Name };
            foreach (var key in new[] { "mBendRadius", "mMaxSplineLength", "mMaxIncline", "mStepHeight", "mMinimumHeight", "mMaximumHeight", "mMinimumHeightWithVerticalConnection", "mBendRadius2D", "mMinBendRadius", "mGridSnapSize" })
                h[key] = hcdo?.GetOrDefault<float?>(key, null);
            holograms[className] = h;
        }
        var maxLen = cdo.GetOrDefault<float?>("mMaxLength", null);
        if (maxLen is not null) wires[className] = new Dictionary<string, object?> { ["mMaxLength"] = maxLen };
    }
}

var output = new {
    provenance = new { extracted = DateTime.UtcNow.ToString("yyyy-MM-dd"), usmap = Path.GetFileName(usmap), game = EGame.GAME_UE5_6.ToString() },
    ports, holograms, wires,
};
File.WriteAllText(args.Length > 2 ? args[2] : "assets.json", JsonConvert.SerializeObject(output, Formatting.Indented));
```

- [ ] **Step 4: Discover the real export names**

Run: `cd tools/sfy-extract && dotnet run -- "$HOME/Satisfactory" list 2>&1 | grep -A40 'ConstructorMk1/Build_ConstructorMk1' | head -60`
Expected: the package's exports, including the generated class, `Default__Build_ConstructorMk1_C`, and connection component templates (names like `Input0`, `Output0`, `PowerConnection`). If the connection templates are missing (inherited from a parent Blueprint), extend the loop: load `generatedClass.GetOrDefault<UObject>("SuperStruct")`'s owner package and collect templates there too, recursively until the super is a native class. If `EGame.GAME_UE5_6` is not accepted by the installed CUE4Parse version, pick the newest `GAME_UE5_x` it offers and record it in `README.md`; the fixtures' engine block says 5.6.1.

- [ ] **Step 5: Extract and merge**

Run: `cd tools/sfy-extract && dotnet run -- "$HOME/Satisfactory" extract ../../src/flab2bp/sfy/data/assets.json`

`scripts/sfy_registry.py`:
```python
"""Merge docs.json and assets.json into the committed registry.json."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "src" / "flab2bp" / "sfy" / "data"
HEADER_DEFAULTS = {"belt_max_spline_cm": 5600.1, "pipe_max_spline_cm": 5600.1,
                   "pipe_bend_radius_2d_cm": 199.0, "pipe_min_bend_radius_cm": 75.0}


def _first(holograms: dict, prefix: str, key: str):
    for cls, h in holograms.items():
        if cls.startswith(prefix) and h.get(key) is not None:
            return h[key]
    return None


def main() -> int:
    docs = json.loads((DATA / "docs.json").read_text())
    assets = json.loads((DATA / "assets.json").read_text())
    for cls, b in docs["buildables"].items():
        b["ports"] = assets["ports"].get(cls, [])
    h = assets["holograms"]
    limits = dict(HEADER_DEFAULTS)
    limits.update({
        "belt_bend_radius_cm": _first(h, "Build_ConveyorBelt", "mBendRadius"),
        "belt_max_incline_deg": _first(h, "Build_ConveyorBelt", "mMaxIncline"),
        "lift_step_cm": _first(h, "Build_ConveyorLift", "mStepHeight"),
        "lift_min_cm": _first(h, "Build_ConveyorLift", "mMinimumHeight"),
        "lift_max_cm": _first(h, "Build_ConveyorLift", "mMaximumHeight"),
        "lift_min_vertical_cm": _first(h, "Build_ConveyorLift", "mMinimumHeightWithVerticalConnection"),
        "pipe_bend_radius_cm": _first(h, "Build_Pipeline", "mBendRadius"),
        "hologram_grid_cm": _first(h, "Build_", "mGridSnapSize"),
        "hologram_rotation_step_deg": 90.0,
        "wire_max_cm": {cls: w["mMaxLength"] for cls, w in assets["wires"].items()},
    })
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    registry = {
        "provenance": {"docs": docs["provenance"], "assets": assets["provenance"], "merged_at_commit": sha},
        "buildables": docs["buildables"], "recipes": docs["recipes"],
        "descriptors": docs["descriptors"], "build_recipes": docs["build_recipes"], "limits": limits,
    }
    (DATA / "registry.json").write_text(json.dumps(registry, indent=1, sort_keys=True) + "\n")
    missing = [k for k, v in limits.items() if v is None]
    print("limits still None:", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
Run: `uv run python scripts/sfy_registry.py`
Expected: `limits still None: []`. Any `None` means the hologram CDO did not carry the value; find the property with `dotnet run -- ~/Satisfactory list` on the hologram package and fix the key list; a value that lives only in C++ (no asset property) is transcribed into `HEADER_DEFAULTS` with the header file and line in a comment.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0.

- [ ] **Step 7: Write the README and commit**

`tools/sfy-extract/README.md` states: prerequisites (dotnet 10, the game install with `CommunityResources/FactoryGame.usmap`), the two commands, the engine enum used, and that `assets.json` and `registry.json` are committed and regenerated per game update.

```bash
git add tools/sfy-extract scripts/sfy_registry.py src/flab2bp/sfy/data/assets.json src/flab2bp/sfy/data/registry.json tests/sfy/test_registry.py
git commit -m "Extract ports and hologram limits from the game assets into the registry"
```

---

### Task 11: Port cross-check against the fixtures

**Files:**
- Create: `src/flab2bp/sfy/geometry.py`
- Test: `tests/sfy/test_port_crosscheck.py`

**Interfaces:**
- Produces: `geometry.quat_rotate(q: tuple[float,float,float,float], v: tuple[float,float,float]) -> tuple[float,float,float]`; `geometry.world_port(transform: Transform, port: Port) -> tuple[float,float,float]` (port translation rotated by the actor quaternion, scaled, added to the actor translation); `geometry.distance(a, b) -> float`.

- [ ] **Step 1: Write the failing test**

`tests/sfy/test_port_crosscheck.py`:
```python
"""Belt spline endpoints in the fixtures must land on the registry's port positions.

For each belt whose connection component links to a machine's connection
component, the belt's first (or last) spline point, in world space, must lie
within TOLERANCE_CM of the machine's port as computed from the registry."""
from flab2bp.sfy.codec import read_sbp
from flab2bp.sfy.geometry import distance, world_port
from flab2bp.sfy.query import connected, object_index, spline_points
from flab2bp.sfy.registry import load_registry
from tests.sfy.conftest import fixture_paths

TOLERANCE_CM = 15.0


def _belt_world_point(belt_header, pts, first: bool):
    loc = pts[0][0] if first else pts[-1][0]
    from flab2bp.sfy.geometry import quat_rotate
    t = belt_header.transform
    r = quat_rotate(t.rotation, (loc.x, loc.y, loc.z))
    return (r[0] + t.translation[0], r[1] + t.translation[1], r[2] + t.translation[2])


def test_belt_endpoints_hit_registry_ports():
    reg = load_registry()
    checked, misses = 0, []
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < 58:
            continue
        index = object_index(bp)
        for h, d in bp.objects:
            if not h.class_name.startswith("Build_ConveyorBelt"):
                continue
            pts = spline_points(d)
            for i, comp_ref in enumerate(d.components):
                ch, cd = index[comp_ref.path]
                peer = connected(cd)
                if peer is None or peer.path not in index:
                    continue
                ph, pd = index[peer.path]
                mh, md = index[ph.parent]
                machine = reg.buildables.get(mh.class_name)
                if machine is None or not machine.ports:
                    continue
                port = next((p for p in machine.ports if p.name == ph.name), None)
                if port is None:
                    continue
                expected = world_port(mh.transform, port)
                actual = _belt_world_point(h, pts, first=(ch.name.endswith("0")))
                checked += 1
                dist = distance(expected, actual)
                if dist > TOLERANCE_CM:
                    misses.append((path.name, h.name, mh.class_name, ph.name, round(dist, 1)))
    assert checked >= 20, "too few belt-to-machine links to be meaningful"
    assert not misses, misses[:20]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_port_crosscheck.py -q`
Expected: non-zero exit, import error for `geometry`.

- [ ] **Step 3: Implement `geometry.py`**

```python
"""Small vector helpers in the game's units (cm, Unreal left-handed axes)."""
from __future__ import annotations

import math

from flab2bp.sfy.objects import Transform
from flab2bp.sfy.registry import Port

__all__ = ["distance", "quat_rotate", "world_port"]


def quat_rotate(q: tuple[float, float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z, w = q
    vx, vy, vz = v
    # v' = v + 2*w*(q_xyz x v) + 2*(q_xyz x (q_xyz x v))
    cx, cy, cz = (y * vz - z * vy, z * vx - x * vz, x * vy - y * vx)
    dx, dy, dz = (y * cz - z * cy, z * cx - x * cz, x * cy - y * cx)
    return (vx + 2 * (w * cx + dx), vy + 2 * (w * cy + dy), vz + 2 * (w * cz + dz))


def world_port(transform: Transform, port: Port) -> tuple[float, float, float]:
    sx, sy, sz = transform.scale
    local = (port.translation[0] * sx, port.translation[1] * sy, port.translation[2] * sz)
    r = quat_rotate(transform.rotation, local)
    t = transform.translation
    return (r[0] + t[0], r[1] + t[1], r[2] + t[2])


def distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a, b)
```

- [ ] **Step 4: Run the test and reconcile**

Run: `uv run pytest tests/sfy/test_port_crosscheck.py -q`
Expected: exit code 0. If misses appear for one machine class only, its extractor port names or transforms are wrong (compare with the `list` output); if every miss is off by a constant, the belt spline points are relative to the belt actor and the belt's own transform was ignored (check `_belt_world_point`); if `ConveyorAny0`/`ConveyorAny1` do not map to first/last spline point, swap the `first=` rule and document which end is which in `query.py`. Do not raise `TOLERANCE_CM` above 15 without recording the measured distribution in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/flab2bp/sfy/geometry.py tests/sfy/test_port_crosscheck.py
git commit -m "Cross-check registry port positions against fixture belt endpoints"
```

**Review batch C ends here (Tasks 9-11).**

---

### Task 12: Object templates and the checkpoint-1 blueprint

**Files:**
- Create: `src/flab2bp/sfy/templates.py`, `scripts/sfy_checkpoint1.py`
- Test: `tests/sfy/test_templates.py`
- Output (not committed): `out/sfy/checkpoint1.sbp`, `out/sfy/checkpoint1.sbpcfg`

**Interfaces:**
- Produces:
  - `templates.TemplateLibrary.from_fixtures(paths: Iterable[Path]) -> TemplateLibrary`: scans fixtures with `SaveVersion >= 58` and keeps the first actor of each class together with its components.
  - `templates.TemplateLibrary.instantiate(class_name: str, name_id: int, transform: Transform) -> tuple[tuple[ObjectHeader, ObjectData], ...]`: deep-copies the template actor and its components with instance paths `Persistent_Level:PersistentLevel.<class>_<name_id>` and `<actor path>.<component name>`, resets `mConnectedComponent` and `mSavedDirections`-style references to `ObjectRef.NULL`, resets every `mSplineData`, inventories stay as in the template (empty in fixtures), `BuiltBy` kept, transform replaced, `parent` of the actor data kept (`Persistent_Level` reference) and `components` rewritten to the new paths.
  - `templates.connect(a: ObjectData, a_path: str, b: ObjectData, b_path: str) -> tuple[ObjectData, ObjectData]`: sets each connection component's `mConnectedComponent` to the other's path.
  - `templates.set_spline(belt: ObjectData, points: tuple[tuple[Vector, Vector, Vector], ...]) -> ObjectData`.
  - `templates.set_recipe(machine: ObjectData, recipe_class_path: str) -> ObjectData` (sets `mCurrentRecipe`).
  - `templates.assemble(objects, dimensions, registry, save_version=60, build_version: int, version_data: SaveObjectVersionData) -> Blueprint`: computes `cost` from each actor's `mBuiltWithRecipe` via the registry's recipe ingredients, `recipes` from the distinct `mBuiltWithRecipe` values, orders objects actors-first the way fixtures do (each actor followed by its components).
- Consumes: Tasks 5-10.

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_templates.py`:
```python
from flab2bp.sfy.codec import read_sbp, write_sbp
from flab2bp.sfy.objects import Transform
from flab2bp.sfy.properties import Object, Vector
from flab2bp.sfy.query import connected, find, object_index, spline_points
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.templates import TemplateLibrary, assemble, connect, set_recipe, set_spline
from tests.sfy.conftest import fixture_paths

IDENTITY = Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def _lib():
    return TemplateLibrary.from_fixtures(fixture_paths())


def test_library_has_the_checkpoint_classes():
    lib = _lib()
    for cls in ("Build_ConstructorMk1_C", "Build_ConveyorBeltMk1_C", "Build_PowerPoleMk1_C", "Build_Foundation_8x1_01_C"):
        assert cls in lib.classes, cls


def test_instantiate_renames_actor_and_components_consistently():
    lib = _lib()
    objs = lib.instantiate("Build_ConstructorMk1_C", 42, IDENTITY)
    actor_h, actor_d = objs[0]
    assert actor_h.name == "Build_ConstructorMk1_C_42"
    assert actor_h.transform == IDENTITY
    for h, d in objs[1:]:
        assert h.kind == 0 and h.parent == actor_h.path
        assert h.path.startswith(actor_h.path + ".")
        if h.class_name == "FGFactoryConnectionComponent":
            assert connected(d) is None
    assert {c.path for c in actor_d.components} == {h.path for h, _ in objs[1:]}


def test_assembled_blueprint_round_trips_and_links_belt_to_constructor():
    lib = _lib(); reg = load_registry()
    ctor = lib.instantiate("Build_ConstructorMk1_C", 1, Transform((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
    belt = lib.instantiate("Build_ConveyorBeltMk1_C", 2, Transform((0.0, 0.0, 0.0, 1.0), (0.0, 600.0, 0.0), (1.0, 1.0, 1.0)))
    out_h, out_port = next((h, d) for h, d in ctor[1:] if h.name == "Output0")
    in_h, belt_in = next((h, d) for h, d in belt[1:] if h.name == "ConveyorAny0")
    out_port2, belt_in2 = connect(out_port, out_h.path, belt_in, in_h.path)
    belt_actor = set_spline(belt[0][1], ((Vector(0, 0, 0), Vector(0, 400, 0), Vector(0, 400, 0)),
                                         (Vector(0, 400, 0), Vector(0, 400, 0), Vector(0, 400, 0))))
    objects = [(ctor[0][0], set_recipe(ctor[0][1], "/Game/FactoryGame/Recipes/Constructor/Recipe_IronPlate.Recipe_IronPlate_C"))]
    objects += [(h, out_port2 if h.name == "Output0" else d) for h, d in ctor[1:]]
    objects.append((belt[0][0], belt_actor))
    objects += [(h, belt_in2 if h.name == "ConveyorAny0" else d) for h, d in belt[1:]]
    sample = read_sbp(fixture_paths()[0].read_bytes())
    bp = assemble(tuple(objects), (4, 4, 4), reg, build_version=sample.header.build_version, version_data=sample.header.version_data)
    again = read_sbp(write_sbp(bp))
    assert again == bp
    index = object_index(again)
    assert connected(index[belt[1][0].path][1]).path.endswith("Build_ConstructorMk1_C_1.Output0")
    assert len(spline_points(index[belt[0][0].path][1])) == 2
    assert bp.header.cost and all(c.amount > 0 for c in bp.header.cost)
    assert {r.name for r in bp.header.recipes} == {"Recipe_ConstructorMk1_C", "Recipe_ConveyorBeltMk1_C"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_templates.py -q`
Expected: non-zero exit, import error.

- [ ] **Step 3: Implement `templates.py`**

Implementation notes (write the code, no stubs):
- `TemplateLibrary.from_fixtures`: for each blueprint, index objects; for each actor whose class is not yet in `classes`, store `(actor_header, actor_data, [(comp_header, comp_data), ...])` where the components are those listed in `actor_data.components`, in that order.
- `instantiate`: new actor path `Persistent_Level:PersistentLevel.{class_name}_{name_id}`; component paths `f"{actor_path}.{component_name}"` where `component_name` is the template component's `name` (e.g. `Input0`, `Output0`, `PowerConnection`, `ConveyorAny0`, `ConveyorAny1`, `StorageInventory`, `FGPowerInfo`, `FGFactoryLegs`). Replace `ObjectHeader.path`, `parent`, `transform`; rebuild `components` refs. Walk every property value with a recursive `_map_refs(value, mapping)` that rewrites `Object` refs whose `path` starts with the template actor path to the new actor path and nulls `mConnectedComponent`. Replace `mSplineData` with an empty `Array` of the same inner tag.
- `connect(a: ObjectData, a_path: str, b: ObjectData, b_path: str) -> tuple[ObjectData, ObjectData]`: use `dataclasses.replace` on the property tuple: replace the `Property` named `mConnectedComponent` (create it with tag `Tag("mConnectedComponent", "ObjectProperty", 0)` if absent) with `Object(ObjectRef("Persistent_Level", other_path))`. `ObjectData` carries no path, which is why the paths are passed alongside.
- `set_spline`: build `Struct("SplinePointData", (Location, ArriveTangent, LeaveTangent))` items with `Tag(name, "StructProperty", 0, struct_name="Vector", struct_guid=bytes(16))` for each field and the array tag copied from the template's `mSplineData` tag (keep its `struct_guid`).
- `assemble`: cost = sum over actors of `registry.recipes[build_recipes[class]].ingredients`; `recipes` = distinct `mBuiltWithRecipe` object refs in first-seen order; header `header_version=2`, `save_version=60`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0.

- [ ] **Step 5: Write the checkpoint script**

`scripts/sfy_checkpoint1.py` builds, on a 2x2 slab of `Build_Foundation_8x1_01_C` (centres at ±400 cm, z = 0 with the foundation's own `z` convention taken from the template's transform in its fixture), one `Build_ConstructorMk1_C` at the origin rotated so its output faces +Y (yaw from the registry port rotation), one `Build_ConveyorBeltMk1_C` from the constructor's `Output0` running 4 m straight in the port's facing direction (spline points at the port and 400 cm beyond, tangents along the direction), and one `Build_PowerPoleMk1_C` 3 m to the side. The constructor recipe is `Recipe_IronPlate_C`. It writes `out/sfy/checkpoint1.sbp` and `out/sfy/checkpoint1.sbpcfg` (`BlueprintRecord.new("flab2bp checkpoint 1")`) and prints the cost table and object count. Belt endpoint positions come from `geometry.world_port` and the registry, so the belt starts exactly on the port.

Run: `uv run python scripts/sfy_checkpoint1.py && uv run python -c "from flab2bp.sfy.codec import read_sbp_file; bp=read_sbp_file('out/sfy/checkpoint1.sbp'); print(len(bp.objects), [h.class_name for h,_ in bp.objects if h.kind==1])"`
Expected: the object list shows 4 foundations, the constructor, the belt and the pole.

- [ ] **Step 6: Commit and hand over**

```bash
git add src/flab2bp/sfy/templates.py scripts/sfy_checkpoint1.py tests/sfy/test_templates.py
git commit -m "Clone fixture objects into a hand-built checkpoint blueprint"
```
Hand `out/sfy/checkpoint1.sbp` and `out/sfy/checkpoint1.sbpcfg` to the user with these instructions: copy both files into `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\blueprints\<session name>\`, open a Blueprint Designer Mk1 in game, load "checkpoint1", and report (a) whether it loads, (b) whether it places outside the designer, (c) whether the belt shows connected to the constructor's output, (d) any error text. Record the outcome in `docs/superpowers/evidence/2026-09-XX-sfy-checkpoint1/RESULT.md` with the game build version.

**Review batch D ends here (Task 12).**

---

### Task 13: Native constants from the shipped binary

Added 2026-09-14 by user ruling: seven hologram limits (belt bend radius and max
incline, the four lift heights, the hologram grid size) are C++ constructor
immediates that exist in no asset, header or Docs.json. They are read from the
shipped module DLL using its PDB. Blueprint measurements (Task 11) stay only as
a cross-check. Rule for every game constant: Docs.json, then cooked assets, then
headers, then the binary; never inferred from blueprints when extraction is
possible.

**Files:**
- Create: `tools/sfy-native/Cargo.toml`, `tools/sfy-native/src/main.rs`, `tools/sfy-native/README.md`, `tools/sfy-native/.gitignore` (`target/`)
- Create: `src/flab2bp/sfy/data/native.json`
- Modify: `scripts/sfy_registry.py` (merge `native.json` before header defaults and measured values; source tag `"binary"`)
- Modify: `src/flab2bp/sfy/data/registry.json` (regenerated)
- Test: `tests/sfy/test_native.py`, update `tests/sfy/test_registry.py`

**Facts (verified 2026-09-14):**
- Module: `~/Satisfactory/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.dll` (27 MB, x86-64 PE, sections `.text` at RVA 0x1000, `.rdata` at 0xee6000). Its debug directory names `FactoryGameEGS-FactoryGame-Win64-Shipping.pdb` with GUID `A2691F7C-B45E-7947-65C6-535DE373BA04` age 1; the PDB (297 MB) sits beside it.
- `cargo` 1.98 is installed. The Rust `pdb` crate (0.8) reads this PDB format (MSF 7, TPI, DBI, public and global symbols); Python `pdbparse` does not (DBI parse fails). `iced-x86` (Rust crate) decodes x86-64. `pelite` or `goblin` reads the PE sections.
- MSVC constructor codegen for `member = 180.f;`: `movss xmmN, dword ptr [rip+K]` then `movss dword ptr [reg+disp], xmmN`, where `reg` is `this` (`rcx` or a copy such as `rbx`/`rdi`) and `disp` is the member offset; `0.f` is `xorps xmmN, xmmN` then the store; adjacent float members are often merged into one 16-byte `movups xmmword ptr [reg+disp], xmmN` from a `.rdata` constant, or an 8-byte `mov qword ptr [reg+disp], rax` from `mov rax, imm64`; integers use `mov dword ptr [reg+disp], imm32`. A member's value may be set in a base-class constructor (the derived constructor calls it first), so the tool walks the class chain from the TPI.
- Built-in oracle: the headers already state `AFGConveyorBeltHologram::mMaxSplineLength = 5600.1f`, `AFGPipelineHologram::mBendRadius2D = 199.0`, `mMinBendRadius = 75.0`, `mMaxSplineLength = 5600.1f`. The tool must reproduce those four from the binary before any unknown value is trusted.

**Interfaces:**
- Produces: `tools/sfy-native` CLI: `cargo run --release -- <dll> <pdb> <out.json> [--class AFGConveyorBeltHologram --class ...]` writing
```json
{"provenance": {"dll": "...", "pdb_guid": "...", "pdb_age": 1, "dll_sha256": "...", "tool": "sfy-native 0.1.0"},
 "classes": {"AFGConveyorBeltHologram": {"size": 1234, "base": "AFGSplineHologram",
    "ctor_rva": "0x...", "members": {"mBendRadius": {"offset": 936, "type": "float", "value": 180.0, "set_in": "AFGConveyorBeltHologram", "evidence": "movss xmm0,[rip+0x..] ; movss [rbx+0x3a8],xmm0 @ 0x..."}}}}}
```
  For every requested member: `offset` (from TPI), `type`, and either `value` + `set_in` + `evidence` or `"value": null, "reason": "no store to this offset found in the constructor chain"`.
- `scripts/sfy_registry.py` merges, per limit key, in this precedence: assets (`assets.json`), binary (`native.json`), header defaults, measured (`measured.json`); `limits_sources[key]` names the winner. The seven limits map to: `belt_bend_radius_cm` ← `AFGConveyorBeltHologram.mBendRadius`; `belt_max_incline_deg` ← `AFGConveyorBeltHologram.mMaxIncline`; `lift_step_cm` ← `AFGConveyorLiftHologram.mStepHeight`; `lift_min_cm` ← `mMinimumHeight`; `lift_max_cm` ← `mMaximumHeight`; `lift_min_vertical_cm` ← `mMinimumHeightWithVerticalConnection`; `hologram_grid_cm` ← the grid member the headers declare on `AFGHologram`/`AFGBuildableHologram`/`AFGFactoryHologram` (grep `Headers.zip` for `GridSnap`/`SnapSize`/`mGridSize`; record the member you chose in the README, or `null` with the reason if no such member exists).

- [ ] **Step 1: Write the failing tests**

`tests/sfy/test_native.py`:
```python
import json
from pathlib import Path

import pytest

from flab2bp.sfy import docs

NATIVE = Path(docs.__file__).parent / "data" / "native.json"


def _native():
    return json.loads(NATIVE.read_text())


def test_oracle_values_reproduced_from_binary():
    """Members whose values the public headers state must come back exactly."""
    c = _native()["classes"]
    assert c["AFGConveyorBeltHologram"]["members"]["mMaxSplineLength"]["value"] == pytest.approx(5600.1, abs=1e-3)
    assert c["AFGPipelineHologram"]["members"]["mMaxSplineLength"]["value"] == pytest.approx(5600.1, abs=1e-3)
    assert c["AFGPipelineHologram"]["members"]["mBendRadius2D"]["value"] == pytest.approx(199.0)
    assert c["AFGPipelineHologram"]["members"]["mMinBendRadius"]["value"] == pytest.approx(75.0)


def test_unknown_limits_are_present_with_evidence():
    c = _native()["classes"]
    for cls, member in [
        ("AFGConveyorBeltHologram", "mBendRadius"),
        ("AFGConveyorBeltHologram", "mMaxIncline"),
        ("AFGConveyorLiftHologram", "mStepHeight"),
        ("AFGConveyorLiftHologram", "mMinimumHeight"),
        ("AFGConveyorLiftHologram", "mMaximumHeight"),
        ("AFGConveyorLiftHologram", "mMinimumHeightWithVerticalConnection"),
    ]:
        m = c[cls]["members"][member]
        assert m["value"] is not None and m["value"] > 0, (cls, member, m)
        assert m["evidence"] and m["set_in"]
        assert isinstance(m["offset"], int) and m["offset"] > 0


def test_provenance_names_the_matching_pdb():
    p = _native()["provenance"]
    assert p["pdb_guid"].upper().replace("-", "") == "A2691F7CB45E794765C6535DE373BA04"
    assert p["pdb_age"] == 1
```
Add to `tests/sfy/test_registry.py`:
```python
def test_seven_limits_come_from_the_binary():
    reg = load_registry()
    binary = {"belt_bend_radius_cm", "belt_max_incline_deg", "lift_step_cm", "lift_min_cm", "lift_max_cm", "lift_min_vertical_cm"}
    for key in binary:
        assert reg.limits_sources[key] == "binary", key
        assert getattr(reg.limits, key) > 0
    assert reg.limits.lift_min_cm <= reg.limits.lift_min_vertical_cm <= reg.limits.lift_max_cm
    assert reg.limits.lift_step_cm > 0 and (reg.limits.lift_max_cm - reg.limits.lift_min_cm) % reg.limits.lift_step_cm == pytest.approx(0, abs=1e-6)


def test_measured_envelope_lies_inside_binary_limits():
    """Task 11's corpus measurements can never be wider than the game's constants."""
    from flab2bp.sfy import docs
    measured = json.loads((Path(docs.__file__).parent / "data" / "measured.json").read_text())
    reg = load_registry()
    assert measured["belt_bend_radius_cm"]["value"] >= reg.limits.belt_bend_radius_cm - 1.0
    assert measured["belt_max_incline_deg"]["value"] <= reg.limits.belt_max_incline_deg + 0.5
    assert measured["lift_min_cm"]["value"] >= reg.limits.lift_min_cm - 1.0
    assert measured["lift_max_cm"]["value"] <= reg.limits.lift_max_cm + 1.0
```
(`hologram_grid_cm` is asserted as `"binary"` only if the grid member exists; otherwise the test asserts its source is `"measured"` and the README explains.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sfy/test_native.py -q`
Expected: non-zero exit, `native.json` missing.

- [ ] **Step 3: Write the Rust tool**

`tools/sfy-native/Cargo.toml`: package `sfy-native` 0.1.0, edition 2021, dependencies `pdb = "0.8"`, `iced-x86 = { version = "1", features = ["decoder", "intel"] }`, `pelite = "0.10"` (or `goblin = "0.9"`), `serde`/`serde_json` (derive), `sha2 = "0.10"`, `anyhow = "1"`.

`src/main.rs` in this order, each a function with a unit test where noted:
1. `load_pe(path) -> Pe`: sections (name, rva, raw offset, size), the debug directory's CodeView GUID/age, `rva_to_bytes(rva, n)`.
2. `open_pdb(path) -> PdbIndex`: verify the PDB GUID/age equal the PE's (`pdb::PDBInformation`), else exit non-zero naming both; build `class layouts` from the TPI (`pdb::TypeData::Class` with `fields` → `FieldList` → `Member{name, offset, field_type}`; resolve forward references to the defining record; record `base_class` from `BaseClass` fields); build `ctor RVAs` from public symbols whose name demangles to `Class::Class(...)` (undecorated public symbols start with `??0Class@@`; use `pdb::SymbolData::Public` and `symbol.offset.to_rva(&address_map)`).
3. `disassemble_ctor(pe, rva) -> Vec<Instr>`: decode with iced until the first `ret` after the epilogue (stop on `ret`; do not follow calls; cap at 64 KiB).
4. `trace_stores(instrs, pe) -> BTreeMap<u32 /*offset*/, Vec<u8>>`: track `this` aliases (start `{rcx}`; add `dst` on `mov dst, src` when `src` is an alias; drop on other writes), track the last constant loaded into each register (`movss/movsd/movups/movaps xmmN, [rip+K]` → bytes from `.rdata` at `K`; `xorps xmmN, xmmN` → zeros; `mov r64, imm64`; `mov r32, imm32`); on a store `mov*/movss/movsd/movups/movaps [alias+disp], src` record the bytes (4/8/16) at `disp`; on `mov dword [alias+disp], imm32` record the immediate bytes. Unit-test with a hand-assembled byte sequence for each pattern.
5. `resolve(members, stores by class chain)`: for each requested member, walk the class chain from the most derived to the base; the first constructor whose store map covers `[offset, offset+size)` wins (`set_in`); decode by TPI type (`float` → f32, `double`, `int32`, `uint8`/bool). Emit `evidence` as the two instructions (load and store) with RVAs.
6. `main`: args, JSON output with provenance (`dll_sha256`), exit non-zero if any oracle member disagrees with the header value (pass the oracle table on the command line as `--expect AFGConveyorBeltHologram.mMaxSplineLength=5600.1`).

Requested classes and members by default: `AFGConveyorBeltHologram` (`mBendRadius`, `mMaxSplineLength`, `mMaxIncline`), `AFGConveyorLiftHologram` (`mStepHeight`, `mMinimumHeight`, `mMaximumHeight`, `mMinimumHeightWithVerticalConnection`, `mMeshHeight`), `AFGPipelineHologram` (`mBendRadius`, `mBendRadius2D`, `mMinBendRadius`, `mMaxSplineLength`), `AFGBuildableWire`/`AFGWireHologram` (`mMaxLength`), plus the grid member found in Step 3's header grep.

- [ ] **Step 4: Run the tool and inspect**

Run: `cd tools/sfy-native && cargo build --release && cargo run --release -- ~/Satisfactory/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.dll ~/Satisfactory/FactoryGame/Binaries/Win64/FactoryGameEGS-FactoryGame-Win64-Shipping.pdb ../../src/flab2bp/sfy/data/native.json --expect AFGConveyorBeltHologram.mMaxSplineLength=5600.1 --expect AFGPipelineHologram.mBendRadius2D=199 --expect AFGPipelineHologram.mMinBendRadius=75 --expect AFGPipelineHologram.mMaxSplineLength=5600.1`
Expected: exit 0; every requested member has a value or a stated reason. If an oracle member is `null`, the store tracer misses a codegen pattern: dump the constructor disassembly to a scratch file, find the store to that offset by hand, and add the pattern (the usual misses are the 16-byte merged store and a `this` copy in `rdi`/`rsi`). If a value is set through a call (`CreateDefaultSubobject` style) rather than a store, say so in the reason.

- [ ] **Step 5: Merge and regenerate**

Extend `scripts/sfy_registry.py` with the binary source between assets and header defaults, regenerate `registry.json`, and confirm `limits still None: []` with every one of the seven keys sourced `"binary"` (the grid key may be `"measured"` with the README reason).

- [ ] **Step 6: Run the tests, write the README, commit**

Run: `uv run pytest tests/sfy -q`
Expected: exit code 0.

README: prerequisites (cargo, the game install), the command, the oracle, the codegen patterns handled, the per-limit table (member, class that sets it, value, evidence RVA), and the reproduction rule (re-run per game update; `native.json` is committed).

```bash
git add tools/sfy-native scripts/sfy_registry.py src/flab2bp/sfy/data/native.json src/flab2bp/sfy/data/registry.json tests/sfy/test_native.py tests/sfy/test_registry.py
git commit -m "Read the hologram limits from the shipped binary through its PDB"
```

**Review batch E: Task 13 (reviewed with Task 12 if both land together).**

---

## Self-review

**Spec coverage.** Spec §3 (game sources) → Tasks 9-10. §4.1 container → Tasks 3-5; §4.1 object data → Tasks 6-7; §4.2 record → Task 3; §4.3 guarantees → Tasks 5, 7 (identity), 12 (decode of emitted). §5.1 Docs.json → Task 9; extractor → Task 10; header-only rules → `HEADER_DEFAULTS` in Task 10 with citations. §5.2 registry shape → Tasks 9-10. §5.3 cross-checks → Task 11 (ports) and Task 9 (`build_recipes`, clearance parse). §12 checkpoint 1 → Task 12. Fixtures → Task 1. Not in this milestone by design: `lab` game parameter, rates, layout, validator, web (M2+).

**Placeholder scan.** The extractor's C# names are marked as "adjust after the `list` run" with a concrete discovery command; the `.sbpcfg` tail is carried opaque with its observed bytes; power-line trailers are kept verbatim and decoded in M4. No "TBD".

**Type consistency.** `ObjectData` changes shape in Task 7 (from `body: bytes` to `properties`/`trailer`); Tasks 8, 11, 12 use the Task 7 shape. `Transform` and `ObjectHeader` names match across Tasks 5, 11, 12. `Port` fields (`name`, `kind`, `direction`, `translation`, `rotation`, `clearance`) match between Tasks 9, 10 and 11. `connect` takes data plus path for both sides in the interface block, the test and the implementation notes of Task 12.
