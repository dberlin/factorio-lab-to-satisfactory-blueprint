"""The Satisfactory production planner's public vocabulary.

Kept import-free of game data and layout implementations so clients can validate
requests without loading the planner. Historical strategies are reference code,
not selectable production alternatives.
"""

from __future__ import annotations

from typing import Final, Literal

__all__ = ["SFY_PRODUCTION_STRATEGIES", "SFY_STRATEGY_CHOICES", "SfyStrategyName"]

SfyStrategyName = Literal["sections"]

SFY_STRATEGY_CHOICES: Final[tuple[SfyStrategyName, ...]] = ("sections",)
SFY_PRODUCTION_STRATEGIES: Final[tuple[SfyStrategyName, ...]] = ("sections",)
