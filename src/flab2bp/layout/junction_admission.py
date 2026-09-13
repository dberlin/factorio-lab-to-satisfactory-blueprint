"""Validated memo for junction-admission answers inside one routing pass.

The detailed router asks, for every cell on every already-routed sibling
path of every net, whether a splitter could stand there. On the large
sprayed cells that question is asked tens of times per cell per pass while
its answer changes for roughly one cell in ten, because the inputs it reads
are local: the taps planned at and around the cell, whether the cell is a
junction guard, and which ports hold reservations on the splitter's
keep-out cells.

An entry therefore records exactly those inputs beside the answer, and a
lookup re-reads them and compares before trusting it. Two version counters
make the common case one integer comparison: the planned-tap version owned
here, and the reservation mapping's own version. When a version moved the
lookup falls back to comparing the recorded inputs and, if they still match,
adopts the new version; for taps that comparison is itself one dict read,
because every tap change stamps the version onto the cells of its peer
window. Answers that depended on the whole staked selection
(a projected-frame proof or a junction frame ban) are also pinned to the
staked-path version and are never kept across a tap change.

Denied-by-reservation answers are never stored: they carry a blame side
effect that depends on corridor state this memo does not track. Admitted
answers record the reservations they saw, so a later net whose own ports do
not cover one of them is refused the entry rather than misled by it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

Cell = tuple[int, int, int]


@dataclass(slots=True)
class _Entry:
    admitted: bool
    planned_here: int
    in_guard: bool
    peers: tuple[Cell, ...]
    taps_version: int
    keepout: tuple[Cell, ...]
    reserved_values: tuple[Cell | None, ...]
    reserved_version: int
    paths_version: int
    selection_dependent: bool


class JunctionAdmissionMemo:
    """Answers keyed by ``(cell, *policy)``, valid only while their inputs hold.

    The key's first element must be the cell: tap changes are screened by
    distance from it before the peer list is recomputed.
    """

    __slots__ = ("_disturbed", "_entries", "hits", "invalidated", "misses", "taps_version")

    #: The peer test's collision window: taps within this Chebyshev distance
    #: of a cell, on every axis, are the peers `_can_junction` considers.
    _PEER_WINDOW = 3

    def __init__(self) -> None:
        self._entries: dict[Any, _Entry] = {}
        #: The version at which a tap last changed inside each cell's peer
        #: window.  Written once per tap change over the window's 343 cells,
        #: so a lookup decides in one dict read whether its peers may differ.
        self._disturbed: dict[Cell, int] = {}
        self.taps_version = 0
        self.hits = 0
        self.misses = 0
        self.invalidated = 0

    def taps_changed(self, tap: Cell) -> None:
        """A planned tap was added or withdrawn at ``tap``."""
        self.taps_version += 1
        version = self.taps_version
        disturbed = self._disturbed
        window = range(-self._PEER_WINDOW, self._PEER_WINDOW + 1)
        tx, ty, tz = tap
        for dx in window:
            for dy in window:
                for dz in window:
                    disturbed[tx + dx, ty + dy, tz + dz] = version

    def _peers_may_differ(self, entry: _Entry, cell: Cell) -> bool:
        """Whether a tap within the collision window of ``cell`` changed since."""
        return self._disturbed.get(cell, 0) > entry.taps_version

    def forget_all(self) -> None:
        self.invalidated += len(self._entries)
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def lookup(
        self,
        key: Any,
        *,
        planned_here: int,
        in_guard: bool,
        reserved_version: int,
        read_reserved: Callable[[Cell], Cell | None],
        routing_ports: Collection[Cell],
        paths_version: int,
        peers: Callable[[], tuple[Cell, ...]],
    ) -> bool | None:
        """The remembered answer if every input it depended on still holds.

        ``routing_ports`` are the asking net's own ports, which its keep-out
        reservations may belong to without denying it. An entry stored by a
        net whose ports covered a reservation does not answer for a net whose
        ports do not; it stays stored for the nets it does answer.
        """
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.planned_here != planned_here or entry.in_guard != in_guard:
            return self._drop(key)
        if entry.taps_version != self.taps_version:
            if entry.selection_dependent:
                return self._drop(key)
            if self._peers_may_differ(entry, key[0]) and peers() != entry.peers:
                return self._drop(key)
            entry.taps_version = self.taps_version
        if entry.reserved_version != reserved_version:
            if tuple(read_reserved(cell) for cell in entry.keepout) != entry.reserved_values:
                return self._drop(key)
            entry.reserved_version = reserved_version
        if entry.selection_dependent and entry.paths_version != paths_version:
            return self._drop(key)
        if any(held is not None and held not in routing_ports for held in entry.reserved_values):
            self.misses += 1
            return None
        self.hits += 1
        return entry.admitted

    def store(
        self,
        key: Any,
        admitted: bool,
        *,
        planned_here: int,
        in_guard: bool,
        peers: tuple[Cell, ...],
        keepout: tuple[Cell, ...],
        reserved_values: tuple[Cell | None, ...],
        reserved_version: int,
        paths_version: int,
        selection_dependent: bool,
    ) -> None:
        self._entries[key] = _Entry(
            admitted,
            planned_here,
            in_guard,
            peers,
            self.taps_version,
            keepout,
            reserved_values,
            reserved_version,
            paths_version,
            selection_dependent,
        )

    def _drop(self, key: Any) -> bool | None:
        del self._entries[key]
        self.invalidated += 1
        self.misses += 1
        return None
