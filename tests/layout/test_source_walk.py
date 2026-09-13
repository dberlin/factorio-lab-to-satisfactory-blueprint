"""`_SourceWalk`: replaying a source-side frontier walk under a wider ``project_taps``."""

from __future__ import annotations

import pytest

from flab2bp.layout import routing_domain
from flab2bp.layout.routing_domain import _TENTATIVE, _Canvas, _SourceWalk

Cell = tuple[int, int, int]

A: Cell = (0, 0, 0)
B: Cell = (2, 0, 0)
C: Cell = (4, 0, 0)


def _walk() -> _SourceWalk:
    """Three taps in walk order; A and B both offer the shared dock (1, 0, 0)."""
    return _SourceWalk(
        project_taps=frozenset(),
        asks=[(A, True, ()), (B, True, ()), (C, False, (7, 9))],
        offers=[(A, A, ((1, 0, 0), (0, 1, 0))), (B, B, ((1, 0, 0), (3, 0, 0)))],
        neighborhood=None,
    )


class _Ask:
    """A scripted predicate that records what it was asked."""

    def __init__(self, answers: dict[Cell, bool]) -> None:
        self.answers = answers
        self.asked: list[Cell] = []

    def __call__(self, x: int, y: int, level: int) -> bool:
        self.asked.append((x, y, level))
        return self.answers[x, y, level]


def _replay(
    walk: _SourceWalk, project_taps: frozenset[Cell], ask: _Ask
) -> tuple[
    set[Cell],
    list[tuple[Cell, bool, tuple[int, ...]]],
    set[int],
    dict[Cell, Cell],
    dict[Cell, set[Cell]],
]:
    asks: list[tuple[Cell, bool, tuple[int, ...]]] = []
    blame: set[int] = set()
    provenance: dict[Cell, Cell] = {}
    choices: dict[Cell, set[Cell]] = {}
    out = walk.replay(
        project_taps, ask, asks=asks, blame=blame, provenance=provenance, source_choices=choices
    )
    return out, asks, blame, provenance, choices


class TestSourceWalkReplay:
    def test_unchanged_cells_are_replayed_without_asking(self) -> None:
        ask = _Ask({})
        out, asks, blame, provenance, choices = _replay(_walk(), frozenset(), ask)

        assert ask.asked == []
        assert out == {(1, 0, 0), (0, 1, 0), (3, 0, 0)}
        assert asks == [(A, True, ()), (B, True, ()), (C, False, (7, 9))]
        assert blame == {7, 9}
        # First-wins provenance in walk order, every competing tap retained.
        assert provenance == {(1, 0, 0): A, (0, 1, 0): A, (3, 0, 0): B}
        assert choices == {(1, 0, 0): {A, B}, (0, 1, 0): {A}, (3, 0, 0): {B}}

    def test_widened_cells_are_asked_again_and_a_refusal_drops_their_offers(self) -> None:
        ask = _Ask({A: False})
        out, asks, blame, provenance, choices = _replay(_walk(), frozenset({A}), ask)

        assert ask.asked == [A]
        assert out == {(1, 0, 0), (3, 0, 0)}
        # The re-asked entry is the predicate's to record; the others are replayed.
        assert asks == [(B, True, ()), (C, False, (7, 9))]
        assert blame == {7, 9}
        assert provenance == {(1, 0, 0): B, (3, 0, 0): B}
        assert choices == {(1, 0, 0): {B}, (3, 0, 0): {B}}

    def test_widened_cell_that_stays_admitted_keeps_its_offers(self) -> None:
        ask = _Ask({A: True, C: False})
        out, _asks, blame, provenance, _choices = _replay(_walk(), frozenset({A, C}), ask)

        assert ask.asked == [A, C]
        assert out == {(1, 0, 0), (0, 1, 0), (3, 0, 0)}
        # C's blame is the re-ask's to contribute, not the record's.
        assert blame == set()
        assert provenance == {(1, 0, 0): A, (0, 1, 0): A, (3, 0, 0): B}

    def test_a_cell_asked_twice_is_asked_twice_again(self) -> None:
        walk = _walk()
        walk.asks.append((A, True, ()))
        ask = _Ask({A: True})
        _replay(walk, frozenset({A}), ask)
        assert ask.asked == [A, A]

    def test_narrowing_project_taps_is_refused(self) -> None:
        walk = _SourceWalk(frozenset({A}), [], [], None)
        with pytest.raises(ValueError):
            _replay(walk, frozenset(), _Ask({}))


class TestMergeFrontierTrace:
    def test_trace_records_every_offer_in_walk_order(self) -> None:
        canvas = _Canvas()
        paths = {0: ((0, 0, 0),), 1: ((2, 0, 0),)}
        canvas.blocked.update(dict.fromkeys(paths[0] + paths[1], _TENTATIVE))
        provenance: dict[Cell, Cell] = {}
        choices: dict[Cell, set[Cell]] = {}
        trace: list[tuple[Cell, Cell, tuple[Cell, ...]]] = []

        frontier = routing_domain._merge_frontier(
            canvas,
            paths,
            (0, 1),
            lambda _x, _y, _level: True,
            provenance=provenance,
            source_choices=choices,
            trace=trace,
        )

        # Replaying the trace with nothing widened reproduces the walk exactly.
        walk = _SourceWalk(frozenset(), [(A, True, ()), (B, True, ())], trace, None)
        out, _asks, _blame, replayed_provenance, replayed_choices = _replay(
            walk, frozenset(), _Ask({})
        )
        assert [asked for asked, _tap, _free in trace] == [A, B]
        assert out == frontier
        assert replayed_provenance == provenance
        assert list(replayed_provenance) == list(provenance)
        assert replayed_choices == choices

    def test_a_refused_cell_leaves_no_trace(self) -> None:
        canvas = _Canvas()
        paths = {0: ((0, 0, 0),), 1: ((2, 0, 0),)}
        canvas.blocked.update(dict.fromkeys(paths[0] + paths[1], _TENTATIVE))
        trace: list[tuple[Cell, Cell, tuple[Cell, ...]]] = []

        routing_domain._merge_frontier(
            canvas, paths, (0, 1), lambda x, _y, _level: x != 0, trace=trace
        )

        assert [asked for asked, _tap, _free in trace] == [B]
