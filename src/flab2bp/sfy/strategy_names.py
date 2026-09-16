"""What the Satisfactory layout strategies are called, and in what order they race.

A leaf on purpose: it imports nothing, from this package or anywhere else.  The
names are wanted in three places that have no business loading a registry, a
game dataset or a solver to learn a string -- the CLI's ``--strategy`` choices,
the corpus's per-strategy pins, and a report that says which strategy won -- and
a list of three words that costs an import of :mod:`flab2bp.sfy.pipeline` is a
list nobody can use from the outside.

The order of :data:`SFY_PRODUCTION_STRATEGIES` is load-bearing: it is the order
the race runs them in AND the tie-break
:func:`~flab2bp.sfy.layout.measure.race_key` breaks a dead heat by, so two
strategies that lay out equally good builds settle it the same way on every run
rather than on whichever finished first.
"""

from __future__ import annotations

from typing import Final, Literal

__all__ = ["SFY_PRODUCTION_STRATEGIES", "SFY_STRATEGY_CHOICES", "SfyStrategyName"]

SfyStrategyName = Literal["best", "manifold-rows", "grid-routed"]
"""What a caller may ask for: either strategy by name, or the race between them."""

SFY_STRATEGY_CHOICES: Final[tuple[SfyStrategyName, ...]] = ("best", "manifold-rows", "grid-routed")
""":data:`SfyStrategyName`'s members as a tuple, for a CLI's ``choices``.

``best`` is first because it is the default: asking for a named strategy is
asking to see what that one does, and the answer a caller wants is the better of
the two builds.
"""

SFY_PRODUCTION_STRATEGIES: Final[tuple[str, ...]] = ("manifold-rows", "grid-routed")
"""The strategies ``best`` actually runs, in the order a tie is settled by.

``best`` is not in it -- it names the race, not a way of laying a build out.
The manifold is first because it is the shape a player builds by hand and the
one the corpus is pinned against today, so a grid-routed build has to be
strictly better to take a cell rather than merely equal to it.
"""
