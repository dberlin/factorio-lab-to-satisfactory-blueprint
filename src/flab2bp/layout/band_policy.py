"""Typed latitude-band selection shared by layout entry points.

The band NAMES live in :mod:`flab2bp.layout.band_names`, which this module
imports and re-exports: the command line has to describe ``--band`` for both
games, and :func:`_validate_planet_geometry` below -- which belongs here -- must
not run because a Satisfactory build printed a help string.
"""

from __future__ import annotations

from dataclasses import dataclass

from flab2bp.layout.band_names import (
    BAND_DIMENSION_BY_SELECTION,
    BAND_DIMENSIONS,
    BAND_SELECTIONS,
    BandDimension,
    BandSelection,
    canonical_selection,
)

__all__ = [
    "BAND_DIMENSION_BY_SELECTION",
    "BAND_DIMENSIONS",
    "BAND_SELECTIONS",
    "BandDimension",
    "BandPolicy",
    "BandSelection",
    "canonical_selection",
]


def _validate_planet_geometry() -> None:
    from flab2bp.dsp import planet

    computed = tuple(
        (band.rows, band.columns)
        for band in sorted(planet.bands(), key=lambda candidate: candidate.area_segments)
    )
    if computed != BAND_DIMENSIONS:
        raise RuntimeError(
            f"latitude_bands.json disagrees with terrestrial planet geometry: {computed!r}"
        )


_validate_planet_geometry()


@dataclass(frozen=True, slots=True)
class BandPolicy:
    """Whether finalization targets portable or one explicit physical band."""

    selection: BandSelection

    def __post_init__(self) -> None:
        canonical = canonical_selection(self.selection)
        if canonical is None:
            raise ValueError(f"unknown latitude band {self.selection!r}")
        object.__setattr__(self, "selection", canonical)

    @classmethod
    def parse(cls, value: str) -> BandPolicy:
        """Parse one supported CLI/API selection into its canonical dimensions."""
        return cls(value)

    @property
    def explicit_segments(self) -> int | None:
        """The requested area-segment count, or ``None`` for portable selection."""
        if self.selection == "portable":
            return None
        return BAND_DIMENSION_BY_SELECTION[self.selection][1] // 5
