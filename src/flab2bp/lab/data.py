"""Locating, caching and loading a FactorioLab dataset.

Resolution order is: an explicit path, then the on-disk HTTP cache, then the
network, then the copy vendored in this package.  The vendored copy is what
makes the tool work offline and what makes the test suite hermetic -- nothing
in ``tests/`` reaches the network.

The cache is a plain directory of JSON bodies plus sidecar metadata holding the
``ETag``, so a refresh costs a conditional GET that normally comes back ``304``.
"""

from __future__ import annotations

import hashlib
import json
import os
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, TypeAdapter

from flab2bp.lab.schema import Dataset, HashIndex
from flab2bp.lab.url import Game


def data_url(game: Game) -> str:
    """Where FactorioLab serves ``data.json`` for ``game``."""
    return f"https://factoriolab.github.io/data/{game.value}/data.json"


def hash_url(game: Game) -> str:
    """Where FactorioLab serves ``hash.json`` for ``game``."""
    return f"https://factoriolab.github.io/data/{game.value}/hash.json"


#: The DSP URLs under their long-standing names, for callers that predate
#: :func:`data_url`.  Derived so the two spellings cannot drift apart.
DATA_URL: Final = data_url(Game.DSP)
HASH_URL: Final = hash_url(Game.DSP)

VENDORED_DIR: Path = Path(__file__).parent / "vendored"

#: Set to a non-empty value to force offline behaviour process-wide.
OFFLINE_ENV_VAR: Final = "FLAB2BP_OFFLINE"

_DEFAULT_TIMEOUT: Final = 30.0


class _CacheMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    etag: str | None = None


_CACHE_METADATA_ADAPTER: Final[TypeAdapter[_CacheMetadata]] = TypeAdapter(_CacheMetadata)


class DatasetNotAvailable(RuntimeError):
    """No source -- explicit path, cache, network or vendored copy -- worked."""


# ---------------------------------------------------------------------------
# Cache plumbing
# ---------------------------------------------------------------------------


def default_cache_dir() -> Path:
    """Where downloaded datasets live between runs."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "flab2bp"


def cache_path_for(url: str, cache_dir: Path | None = None) -> Path:
    """The cached body path for ``url``.

    Keyed by a hash of the URL so unrelated datasets never collide, with a
    readable suffix so the cache directory is browsable.
    """
    directory = cache_dir if cache_dir is not None else default_cache_dir()
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    stem = Path(url).stem or "dataset"
    return directory / f"{stem}-{digest}.json"


def _meta_path(body_path: Path) -> Path:
    return body_path.with_suffix(".meta.json")


def _read_etag(body_path: Path) -> str | None:
    try:
        raw: object = json.loads(_meta_path(body_path).read_text(encoding="utf-8"))
        return _CACHE_METADATA_ADAPTER.validate_python(raw).etag
    except OSError, ValueError:
        return None


def _write_cache(body_path: Path, text: str, etag: str | None) -> None:
    try:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = body_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(body_path)
        if etag:
            _meta_path(body_path).write_text(json.dumps({"etag": etag}), encoding="utf-8")
    except OSError:
        # A broken cache must never break the program; the value is still in hand.
        pass


def _offline_forced() -> bool:
    return bool(os.environ.get(OFFLINE_ENV_VAR))


def _download(url: str, body_path: Path, *, force_refresh: bool) -> str | None:
    """Conditional GET, writing through to the cache.  ``None`` on any failure."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return None

    headers: dict[str, str] = {}
    etag = None if force_refresh else _read_etag(body_path)
    if etag:
        headers["If-None-Match"] = etag

    try:
        response = httpx.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT, follow_redirects=True)
    except Exception:
        return None

    if response.status_code == 304:
        try:
            return body_path.read_text(encoding="utf-8")
        except OSError:
            return None
    if response.status_code != 200:
        return None

    text = response.text
    _write_cache(body_path, text, response.headers.get("ETag"))
    return text


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_json(text: str) -> object:
    """Parse with exact rationals for every decimal literal.

    ``parse_float`` receives the raw token text, so ``0.32`` becomes exactly
    ``Fraction(8, 25)`` rather than the nearest binary double.
    """
    parsed: object = json.loads(text, parse_float=Fraction)
    return parsed


def _resolve_text(
    url: str,
    *,
    path: Path | None,
    vendored_name: str,
    allow_network: bool,
    cache_dir: Path | None,
    force_refresh: bool,
) -> str:
    if path is not None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DatasetNotAvailable(f"could not read dataset at {path}") from exc

    body_path = cache_path_for(url, cache_dir)

    if not force_refresh:
        try:
            return body_path.read_text(encoding="utf-8")
        except OSError:
            pass

    if allow_network and not _offline_forced():
        text = _download(url, body_path, force_refresh=force_refresh)
        if text is not None:
            return text

    try:
        return (VENDORED_DIR / vendored_name).read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetNotAvailable(
            f"no dataset available for {url}: explicit path, cache "
            f"({body_path}), network and vendored copy all failed"
        ) from exc


def _vendored_name(game: Game, filename: str) -> str:
    """Where ``game``'s copy of ``filename`` sits under :data:`VENDORED_DIR`.

    DSP predates the per-game layout and stays flat, so that the paths every
    existing caller and every packaging glob already know keep working.
    """
    return filename if game is Game.DSP else f"{game.value}/{filename}"


def load_dataset(
    path: Path | str | None = None,
    *,
    game: Game = Game.DSP,
    allow_network: bool = True,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> Dataset:
    """Load a game's dataset.

    Args:
        path: Read this file instead of consulting cache, network or vendor.
        game: Which FactorioLab dataset to load.
        allow_network: Whether a cache miss may fetch from FactorioLab.
        cache_dir: Override the on-disk cache location.
        force_refresh: Skip the cached body and re-fetch.
    """
    text = _resolve_text(
        data_url(game),
        path=Path(path) if path is not None else None,
        vendored_name=_vendored_name(game, "data.json"),
        allow_network=allow_network,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
    )
    return Dataset.parse(_parse_json(text))


def load_hash_index(
    path: Path | str | None = None,
    *,
    game: Game = Game.DSP,
    allow_network: bool = True,
    cache_dir: Path | None = None,
    force_refresh: bool = False,
) -> HashIndex:
    """Load ``hash.json``, the id tables that ``z=``-compressed URLs index into."""
    text = _resolve_text(
        hash_url(game),
        path=Path(path) if path is not None else None,
        vendored_name=_vendored_name(game, "hash.json"),
        allow_network=allow_network,
        cache_dir=cache_dir,
        force_refresh=force_refresh,
    )
    return HashIndex.parse(_parse_json(text))


@cache
def _vendored_dataset(game: Game) -> Dataset:
    """One parsed ``Dataset`` per game, however ``load_vendored`` was spelled.

    `functools.cache` keys on the argument tuple, so a cache on
    `load_vendored` alone would hand out two distinct objects for
    `load_vendored()` and `load_vendored(Game.DSP)`.  Keying here, where the
    game is always explicit, keeps one instance per game.
    """
    source = VENDORED_DIR / _vendored_name(game, "data.json")
    return Dataset.parse(_parse_json(source.read_text(encoding="utf-8")))


@cache
def load_vendored(game: Game = Game.DSP) -> Dataset:
    """Load the in-repo copy directly, bypassing cache and network.

    `@cache`d because `bench/runner.py` calls it TWICE per corpus URL --
    `specs_for` and `belt_rules_for_url` -- so a 12-URL run re-read and
    re-parsed an unchanged `data.json` 24 times and rebuilt
    `Dataset.__post_init__`'s indexes 24 times. `Dataset` is
    `@dataclass(frozen=True, slots=True)`, so sharing one instance is safe.
    """
    return _vendored_dataset(game)


def load_vendored_hash_index(game: Game = Game.DSP) -> HashIndex:
    source = VENDORED_DIR / _vendored_name(game, "hash.json")
    return HashIndex.parse(_parse_json(source.read_text(encoding="utf-8")))


__all__ = (
    "DATA_URL",
    "HASH_URL",
    "VENDORED_DIR",
    "DatasetNotAvailable",
    "Game",
    "cache_path_for",
    "data_url",
    "default_cache_dir",
    "hash_url",
    "load_dataset",
    "load_hash_index",
    "load_vendored",
    "load_vendored_hash_index",
)
