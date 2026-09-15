"""Which latitude bands there are, by name, and nothing about what they mean.

Split out of :mod:`flab2bp.layout.band_policy` for the reason
:mod:`flab2bp.strategy_names` exists: one argparse parser describes ``--band``
for both games, and ``band_policy`` checks ``latitude_bands.json`` against
``flab2bp.dsp.planet``'s computed geometry at import time -- a check that is
right where it is and has no business running because a Satisfactory build
printed a help string.

The band table itself has not moved: this reads the same
``flab2bp/dsp/data/latitude_bands.json``, and it is traversed to from the
``flab2bp`` package rather than the ``flab2bp.dsp`` one so that reading it
imports no DSP module.  :mod:`flab2bp.layout.band_policy` imports every name
below and re-exports it, so nothing that used it had to change.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import cast

__all__ = [
    "BAND_DIMENSIONS",
    "BAND_DIMENSION_BY_SELECTION",
    "BAND_SELECTIONS",
    "BandDimension",
    "BandSelection",
    "canonical_selection",
]

type BandSelection = str
type BandDimension = tuple[int, int]


def _load_band_dimensions() -> tuple[BandDimension, ...]:
    source = files("flab2bp").joinpath("dsp/data/latitude_bands.json")
    raw = cast(object, json.loads(source.read_text(encoding="utf-8")))
    if not isinstance(raw, list):
        raise RuntimeError(f"{source} must contain a JSON array")

    dimensions: list[BandDimension] = []
    segments: set[int] = set()
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"{source} contains a malformed latitude band")
        record = cast(dict[object, object], item)
        if set(record) != {"height", "width"}:
            raise RuntimeError(f"{source} contains a malformed latitude band")
        height = record["height"]
        width = record["width"]
        if (
            not isinstance(height, int)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or isinstance(width, bool)
            or height <= 0
            or width <= 0
            or width % 5
        ):
            raise RuntimeError(f"{source} contains an invalid latitude-band dimension")
        dimension = (height, width)
        area_segments = width // 5
        if dimension in dimensions or area_segments in segments:
            raise RuntimeError(f"{source} contains a duplicate latitude band")
        dimensions.append(dimension)
        segments.add(area_segments)
    return tuple(dimensions)


#: One authoritative pole-to-equator list, stored as ``(height, width)``.
BAND_DIMENSIONS: tuple[BandDimension, ...] = _load_band_dimensions()
BAND_SELECTIONS: tuple[BandSelection, ...] = (
    "portable",
    *(f"{height}x{width}" for height, width in BAND_DIMENSIONS),
)
#: Each named band's own ``(height, width)``, so a caller that has a selection
#: need not scan the list to get back the numbers behind it.
BAND_DIMENSION_BY_SELECTION: dict[BandSelection, BandDimension] = {
    f"{height}x{width}": (height, width) for height, width in BAND_DIMENSIONS
}
_SELECTION_BY_SEGMENTS = {str(width // 5): f"{height}x{width}" for height, width in BAND_DIMENSIONS}


def canonical_selection(value: str) -> BandSelection | None:
    """``value`` as one of :data:`BAND_SELECTIONS`, or ``None`` if it is none of them.

    A caller may name a band by its dimensions (``10x200``) or by its
    area-segment count (``40``); both come back as the dimensions spelling.
    """
    if value == "portable" or value in BAND_DIMENSION_BY_SELECTION:
        return value
    return _SELECTION_BY_SEGMENTS.get(value)
