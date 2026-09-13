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
from fractions import Fraction
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


@dataclass(slots=True)
class StagedWorkBudget:
    """One deterministic ledger with a proxy-inaccessible closure reserve."""

    total: int
    discovery_by_height: dict[int, int] = field(default_factory=dict, init=False)
    shared_left: int = field(init=False)
    final_reserved: int = field(init=False)
    final_left: int = field(init=False)
    _spent: int = field(default=0, init=False, repr=False)
    _unsettled_discovery: set[int] = field(default_factory=set, init=False, repr=False)
    _discovery_spent: dict[int, int] = field(default_factory=dict, init=False, repr=False)
    _pending_discovery_return: int = field(default=0, init=False, repr=False)
    _configured: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.total) is not int or self.total < 0:
            raise ValueError("total work budget must be a non-negative integer")
        self.final_reserved = fraction_ceiling(self.total, Fraction(1, 4))
        self.final_left = self.final_reserved
        self.shared_left = self.total - self.final_reserved

    @property
    def spent(self) -> int:
        """Expansions charged exactly once across every routing role."""
        return self._spent

    @property
    def discovery_complete(self) -> bool:
        return self._configured and not self._unsettled_discovery

    def configure(self, heights: tuple[int, ...], reserve_fraction: Fraction) -> None:
        """Partition searchable expansions equally across first height stages."""
        if self._configured:
            if tuple(self.discovery_by_height) != heights:
                raise ValueError("work budget is already configured for other heights")
            return
        if not heights or len(set(heights)) != len(heights):
            raise ValueError("candidate heights must be a non-empty tuple of unique values")
        if not isinstance(reserve_fraction, Fraction) or not 0 <= reserve_fraction < 1:
            raise ValueError("reserve fraction must be a Fraction from zero to one")

        self.final_reserved = fraction_ceiling(self.total, reserve_fraction)
        self.final_left = self.final_reserved
        searchable = self.total - self.final_reserved
        discovery_slice, remainder = divmod(searchable, len(heights))
        self.discovery_by_height = {height: discovery_slice for height in heights}
        self.shared_left = remainder
        self._unsettled_discovery = set(heights)
        self._discovery_spent = dict.fromkeys(heights, 0)
        self._configured = True

    def discovery_allowance(self, height: int) -> int:
        if height not in self._unsettled_discovery:
            raise ValueError("height has no unsettled discovery reservation")
        return self.discovery_by_height[height] - self._discovery_spent[height]

    def charge_discovery(self, height: int, spent: int) -> None:
        """Charge part of one height reservation without closing discovery."""
        allowance = self.discovery_allowance(height)
        check_spend(spent, allowance)
        self._discovery_spent[height] += spent
        self._spent += spent

    def detailed_discovery_allowance(self, height: int) -> int:
        """All remaining work, exposed only to one authoritative seed closure."""
        self.discovery_allowance(height)
        return (
            sum(
                self.discovery_allowance(candidate)
                for candidate in self.discovery_by_height
                if candidate in self._unsettled_discovery
            )
            + self.final_left
            + self.shared_left
        )

    def charge_detailed_discovery(self, height: int, spent: int) -> None:
        """Atomically charge closure without exposing borrowed work to proxies."""
        check_spend(spent, self.detailed_discovery_allowance(height))
        remaining = spent

        current = self.discovery_allowance(height)
        take = min(remaining, current)
        self._discovery_spent[height] += take
        remaining -= take

        take = min(remaining, self.final_left)
        self.final_left -= take
        remaining -= take

        for candidate in self.discovery_by_height:
            if remaining == 0:
                break
            if candidate == height or candidate not in self._unsettled_discovery:
                continue
            allowance = self.discovery_allowance(candidate)
            take = min(remaining, allowance)
            self._discovery_spent[candidate] += take
            remaining -= take

        take = min(remaining, self.shared_left)
        self.shared_left -= take
        remaining -= take
        if remaining:
            raise AssertionError("detailed closure charge exceeded decomposed budget")
        self._spent += spent

    def settle_detailed_discovery(self, height: int, spent: int) -> None:
        """Close one discovery after its authoritative route borrowed future work."""
        self.charge_detailed_discovery(height, spent)
        self._pending_discovery_return += self.discovery_allowance(height)
        self._unsettled_discovery.remove(height)
        if not self._unsettled_discovery:
            self.shared_left += self._pending_discovery_return
            self._pending_discovery_return = 0

    def settle_discovery(self, height: int, spent: int) -> None:
        allowance = self.discovery_allowance(height)
        check_spend(spent, allowance)
        self.charge_discovery(height, spent)
        self._pending_discovery_return += allowance - spent
        self._unsettled_discovery.remove(height)
        if not self._unsettled_discovery:
            self.shared_left += self._pending_discovery_return
            self._pending_discovery_return = 0

    def shared_allowance(self) -> int:
        if not self.discovery_complete:
            raise ValueError("shared work budget is locked until discovery completes")
        return self.shared_left

    def settle_shared(self, spent: int) -> None:
        allowance = self.shared_allowance()
        check_spend(spent, allowance)
        self.shared_left -= spent
        self._spent += spent


def fraction_ceiling(total: int, fraction: Fraction) -> int:
    numerator = total * fraction.numerator
    return (numerator + fraction.denominator - 1) // fraction.denominator


def check_spend(spent: int, allowance: int) -> None:
    if type(spent) is not int or not 0 <= spent <= allowance:
        raise ValueError("adapter work spend must be within its allowance")
