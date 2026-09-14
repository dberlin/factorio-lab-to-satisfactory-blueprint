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
