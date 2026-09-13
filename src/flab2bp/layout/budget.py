"""One clock and one charged-work ledger for every layout phase.

Before this module the concept had five named predicates, three closures,
eight inline comparisons, three "clock ran out" exception types and an
untyped ledger dict at 42 sites, keyed ``"left"`` (see
``docs/superpowers/specs/2026-09-13-abstraction-review.md`` section 5, P1).
That dict is now :class:`WorkBudget` and its ``left`` is a field.
Two rules hold everything together:

* a ``None`` deadline never expires, and the comparison is always ``>=``;
* the clock is resolved at the moment it is read, never captured, so a test
  that patches ``time.monotonic`` is seen by a budget built before the patch.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from flab2bp.layout import process_resources

WorkKind = Literal[
    "arcs", "augmentations", "candidates", "audit_cells", "predicates", "assignments"
]


class BudgetExhausted(Exception):
    """A bound -- clock, allowance or memory -- ended a phase.

    The base of every "ran out" signal in layout. Handlers keep catching the
    concrete subclass they always caught: the conversion at
    ``routing_domain._route_all`` (a caught ``_PreparationDeadline`` re-raised
    as the proposals ``Deadline``) depends on the two being distinguishable.
    """


class TransportRefusal(BudgetExhausted, RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class WorkLimits:
    arcs: int = 100_000
    augmentations: int = 100_000
    candidates: int = 2_000_000
    audit_cells: int = 5_000_000
    predicates: int = 100_000_000
    assignments: int = 100_000


def now(clock: Callable[[], float] | None = None) -> float:
    """The current monotonic reading, or an injected clock's."""
    return time.monotonic() if clock is None else clock()


def expired(deadline: float | None, clock: Callable[[], float] | None = None) -> bool:
    """Has ``deadline`` passed? ``None`` never has.

    Callers in a module whose ``time`` attribute a test may replace must pass
    ``clock=time.monotonic`` so the lookup happens in *their* module.
    """
    return deadline is not None and now(clock) >= deadline


@dataclass
class WorkBudget:
    """One phase's clock, its remaining charged work, and its per-kind counts.

    Mutable and shared by reference on purpose: a routing pass hands the same
    object to every net, and a carved child ledger is reconciled against its
    parent by subtracting what the child did not spend.
    """

    deadline: float | None = None
    #: Charged geometric work units still available to this pass. ``None`` is
    #: "unbounded" and is what a caller that passed no budget at all gets.
    left: int | None = None
    limits: WorkLimits = field(default_factory=WorkLimits)
    clock: Callable[[], float] | None = None
    counts: dict[WorkKind, int] = field(default_factory=dict)
    _next_memory_check_s: float = field(default=0.0, init=False)

    def expired(self) -> bool:
        """Clock only. Never probes memory: layout has no RSS bound."""
        return expired(self.deadline, self.clock)

    def check(self) -> None:
        reading = now(self.clock)
        if self.deadline is not None and reading >= self.deadline:
            raise TransportRefusal("DEADLINE", "absolute transport-routing deadline reached")
        if reading >= self._next_memory_check_s:
            self._next_memory_check_s = reading + 0.05
            if process_resources.usage()[2] > 4 * 1024 * 1024:
                raise TransportRefusal("MEMORY_BOUND", "transport-routing exceeded 4 GiB peak RSS")

    def charge(self, kind: WorkKind, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("work charge must be nonnegative")
        self.check()
        current = self.counts.get(kind, 0)
        if current + amount > getattr(self.limits, kind):
            raise TransportRefusal("POLICY_BOUND", f"{kind} work limit reached at {current}")
        self.counts[kind] = current + amount
