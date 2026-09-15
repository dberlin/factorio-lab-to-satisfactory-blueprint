from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.flow import load_flow
from flab2bp.lab.url import Game, parse_url
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.rates import spec_from_flow
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import SfyBuildSpec

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sfy"
FLOWS = Path(__file__).resolve().parent.parent / "fixtures" / "sfy_flows"


def fixture_paths() -> list[Path]:
    return sorted(FIXTURES.glob("*.sbp"))


@cache
def sfy_registry() -> Registry:
    """The shipped registry, parsed once for every test that wants it."""
    return load_registry()


@cache
def flow_spec(name: str) -> SfyBuildSpec:
    """One committed FactorioLab flow, as the spec a layout is handed.

    Nothing about a rate, a count or a belt tier is written by a test: the CSV
    is FactorioLab's own export and its first line is the URL it came from.
    """
    url = (FLOWS / f"{name}.csv").read_text(encoding="utf-8").splitlines()[0].strip().strip('"')
    return spec_from_flow(
        load_vendored(Game.SFY),
        parse_url(url),
        load_flow(FLOWS / f"{name}.csv", url=url),
        sfy_registry(),
        load_lab_map(),
    )


def fixture_pairs() -> list[tuple[Path, Path]]:
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    return [(FIXTURES / e["sbp"], FIXTURES / e["sbpcfg"]) for e in manifest["entries"]]


@pytest.fixture(params=fixture_paths(), ids=lambda p: p.stem)
def sbp_path(request) -> Path:
    return request.param


@pytest.fixture(params=fixture_pairs(), ids=lambda pair: pair[0].stem)
def sbp_pair(request) -> tuple[Path, Path]:
    return request.param
