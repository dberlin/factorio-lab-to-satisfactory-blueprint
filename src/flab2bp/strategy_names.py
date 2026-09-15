"""What the layout strategies are called, and which of them compete.

A leaf module on purpose, in the shape :mod:`flab2bp.lab.games` already uses.
The command line has to be able to DESCRIBE every flag it accepts -- argparse
wants ``choices`` at parser-construction time -- and it builds one parser for
both games.  Taking these names from :mod:`flab2bp.pipeline` meant that
describing ``--strategy`` loaded eighteen Dyson Sphere Program modules before a
Satisfactory build had read its URL.

Nothing here imports anything else from this package, and
:mod:`flab2bp.pipeline` re-exports every name below, so
``pipeline.STRATEGY_CHOICES`` keeps meaning what it always meant.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "PRODUCTION_STRATEGIES",
    "PRODUCTION_STRATEGY_COUNT",
    "STRATEGY_CHOICES",
    "ExplicitStrategyName",
    "StrategyName",
]

ExplicitStrategyName = Literal["freeform", "sequence-pair", "hierarchical", "transport-routing"]
StrategyName = Literal["best", "freeform", "sequence-pair", "hierarchical", "transport-routing"]

STRATEGY_CHOICES: tuple[StrategyName, ...] = (
    "best",
    "freeform",
    "sequence-pair",
    "hierarchical",
    "transport-routing",
)
#: Explicit strategies competing for the smallest valid result under ``best``.
PRODUCTION_STRATEGIES: tuple[ExplicitStrategyName, ...] = (
    "freeform",
    "sequence-pair",
    "transport-routing",
    "hierarchical",
)
PRODUCTION_STRATEGY_COUNT = len(PRODUCTION_STRATEGIES)
