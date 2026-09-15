"""The size class both corpora label an entry with.

A leaf on purpose: ``Tier`` is the one name the Satisfactory corpus reuses from
the bake-off side, and while it lived in :mod:`flab2bp.bench.corpus` -- which
imports the DSP rate solver -- reading a list of twelve Satisfactory URLs loaded
the DSP catalog, its rules, its colliders and both geometry kernels.  So this
module imports nothing but ``enum`` and must keep importing nothing but
``enum``; ``tests/sfy/test_corpus.py`` measures the seam in a fresh interpreter.

``flab2bp.bench.corpus`` re-exports the enum under the name it has always had.
"""

from __future__ import annotations

from enum import Enum


class Tier(Enum):
    """Size class, which sets the solver time budget."""

    TRIVIAL = "trivial"
    SMALL = "small"
    MID = "mid"
    LARGE = "large"
    STRESS = "stress"

    @property
    def time_budget_s(self) -> float:
        return {
            Tier.TRIVIAL: 10.0,
            Tier.SMALL: 10.0,
            Tier.MID: 60.0,
            Tier.LARGE: 120.0,
            Tier.STRESS: 300.0,
        }[self]
