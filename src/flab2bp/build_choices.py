"""The named choices a build request carries, apart from what implements them.

The same leaf-module argument as :mod:`flab2bp.strategy_names`: one argparse
parser describes every flag for both games, so the vocabulary of a choice has
to be reachable without the machinery that acts on it.  ``CandidatePolicy`` and
``MachineRank`` used to live in :mod:`flab2bp.rates`, whose package ``__init__``
pulls in the solver and DSP's catalog, and ``POWER_TOWER_CHOICES`` in
:mod:`flab2bp.dsp.catalog`; naming a flag should not cost either.

These are NAMES, not game data.  ``POWER_TOWER_CHOICES`` maps the word a
caller types to the FactorioLab item id the spec then resolves through
``catalog.get_item_id`` like every other id -- the resolution, and every number
about a power tower, stays in :mod:`flab2bp.dsp.catalog`.

Every old home re-exports what moved, so no call site changed.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "DEFAULT_CANDIDATE_POLICIES",
    "DEFAULT_POWER_TOWER",
    "POWER_TOWER_CHOICES",
    "CandidatePolicy",
    "MachineRank",
]


class CandidatePolicy(StrEnum):
    """One deterministic proliferation policy exposed to callers."""

    NO_PROLIFERATOR = "no-proliferator"
    ALL_PRODUCTS = "all-products"
    OUTPUT_PRODUCTS = "output-products"


#: Public default and authoritative solver order. Request order is presentation;
#: candidate construction always normalizes a selected subset to this tuple.
DEFAULT_CANDIDATE_POLICIES: tuple[CandidatePolicy, ...] = (
    CandidatePolicy.NO_PROLIFERATOR,
    CandidatePolicy.ALL_PRODUCTS,
    CandidatePolicy.OUTPUT_PRODUCTS,
)


class MachineRank(StrEnum):
    """How the URL's machine rank is read."""

    #: FactorioLab's ``bestMatch``: the ranked machine, always.
    EXACT = "exact"
    #: The ranked machine is a ceiling; take the slowest producer that ties.
    UP_TO = "up-to"


#: The power buildings a build may choose between, keyed by the name the CLI,
#: the web UI and ``BuildSpec`` use.  The values are FactorioLab ids, resolved
#: through :func:`flab2bp.dsp.catalog.get_item_id` like every other id the spec
#: carries.
POWER_TOWER_CHOICES: dict[str, str] = {
    "tesla": "tesla-tower",
    "substation": "satellite-substation",
    "wireless": "wireless-power-tower",
}

#: The choice a build gets when nothing says otherwise.  Keeping this the Tesla
#: Tower is what makes the default arm byte-identical to the era before the
#: choice existed.
DEFAULT_POWER_TOWER: str = "tesla-tower"
