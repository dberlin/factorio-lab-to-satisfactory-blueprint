"""What every Satisfactory layout strategy implements.

Written FROM :class:`~flab2bp.sfy.layout.strategy.ManifoldRows` rather than for
it: the shape below is the surface that strategy already had, so naming it takes
nothing away and the second strategy has one thing to satisfy rather than a file
to read.

How it differs from the DSP :class:`~flab2bp.layout.base.LayoutStrategy`, and why
------------------------------------------------------------------------------
Two ways.  ``designer`` is a positional second argument, because a Satisfactory
build is laid out INSIDE a Blueprint Designer and the same spec in a different
mark is a different problem -- the DSP side has no such frame.  And ``registry``
and ``lab_map`` are injectable, because both are cached singletons a test wants
to substitute; ``None`` means "load the shipped one".

The rules the DSP protocol states hold here word for word.  A strategy is pure:
same spec, same designer, same placement, modulo the time budget.  It returns a
placement that ``validate`` passes or raises
:class:`~flab2bp.layout.base.NoValidLayout` with a cause out of
:data:`~flab2bp.sfy.layout.refusals.REFUSALS` -- never a degraded placement so
that a race has two numbers to compare.  ``absolute_deadline`` is a wall someone
else started in the same ``time.monotonic()`` frame, which is how a race hands
both strategies the same clock.

``runtime_checkable`` is on it so a test can assert a class satisfies it at all;
the check that matters is mypy's, because ``isinstance`` against a protocol looks
at attribute NAMES and never at a signature.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from flab2bp.sfy.labmap import LabMap
from flab2bp.sfy.layout.model import SfyPlacement
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import Designer, SfyBuildSpec

__all__ = ["SfyLayoutStrategy"]


@runtime_checkable
class SfyLayoutStrategy(Protocol):
    """One way of laying a Satisfactory build out inside a Blueprint Designer."""

    #: What the strategy is called: one of
    #: :data:`~flab2bp.sfy.strategy_names.SFY_PRODUCTION_STRATEGIES`.
    name: str

    def lay_out(
        self,
        spec: SfyBuildSpec,
        designer: Designer,
        *,
        time_budget_s: float = 15.0,
        absolute_deadline: float | None = None,
        registry: Registry | None = None,
        lab_map: LabMap | None = None,
    ) -> SfyPlacement:
        """Lay ``spec`` out inside ``designer``, or refuse with a named cause."""
        ...
