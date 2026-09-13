from __future__ import annotations

from flab2bp.layout.junction_admission import JunctionAdmissionMemo

Cell = tuple[int, int, int]
KEY: tuple[Cell, bool, bool, bool] = ((3, 4, 0), False, False, False)
KEEPOUT: tuple[Cell, ...] = ((2, 4, 0), (4, 4, 0), (3, 5, 0))
OWN_PORTS: frozenset[Cell] = frozenset({(9, 9, 0)})


def _store(
    memo: JunctionAdmissionMemo,
    admitted: bool = True,
    *,
    peers: tuple[Cell, ...] = ((6, 6, 0),),
    reserved_values: tuple[Cell | None, ...] = (None, None, (9, 9, 0)),
    selection_dependent: bool = False,
) -> None:
    memo.store(
        KEY,
        admitted,
        planned_here=0,
        in_guard=False,
        peers=peers,
        keepout=KEEPOUT,
        reserved_values=reserved_values,
        reserved_version=5,
        paths_version=2,
        selection_dependent=selection_dependent,
    )


def _lookup(
    memo: JunctionAdmissionMemo,
    *,
    planned_here: int = 0,
    in_guard: bool = False,
    reserved_version: int = 5,
    reserved: dict[Cell, Cell] | None = None,
    paths_version: int = 2,
    peers: tuple[Cell, ...] = ((6, 6, 0),),
    routing_ports: frozenset[Cell] = OWN_PORTS,
) -> bool | None:
    table = {(3, 5, 0): (9, 9, 0)} if reserved is None else reserved
    return memo.lookup(
        KEY,
        planned_here=planned_here,
        in_guard=in_guard,
        reserved_version=reserved_version,
        read_reserved=table.get,
        routing_ports=routing_ports,
        paths_version=paths_version,
        peers=lambda: peers,
    )


def test_a_fresh_memo_misses_and_a_stored_answer_hits() -> None:
    memo = JunctionAdmissionMemo()
    assert _lookup(memo) is None
    _store(memo, False)
    assert _lookup(memo) is False
    assert (memo.hits, memo.misses) == (1, 1)


def test_a_tap_at_the_cell_or_a_guard_claim_forgets_the_answer() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    assert _lookup(memo, planned_here=1) is None
    _store(memo)
    assert _lookup(memo, in_guard=True) is None
    assert len(memo) == 0


def test_a_far_tap_change_keeps_the_answer_without_consulting_the_peers() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    memo.taps_changed((30, 30, 0))

    def not_asked() -> tuple[Cell, ...]:
        raise AssertionError("a tap outside the collision window needs no peer scan")

    assert (
        memo.lookup(
            KEY,
            planned_here=0,
            in_guard=False,
            reserved_version=5,
            read_reserved={(3, 5, 0): (9, 9, 0)}.get,
            routing_ports=OWN_PORTS,
            paths_version=2,
            peers=not_asked,
        )
        is True
    )
    assert _lookup(memo) is True, "the entry adopted the new taps version"


def test_a_near_tap_change_keeps_the_answer_only_while_the_peers_agree() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    memo.taps_changed((5, 6, 0))
    assert _lookup(memo) is True, "same peers: the collision test cannot have changed"
    memo.taps_changed((4, 4, 0))
    assert _lookup(memo, peers=((6, 6, 0), (4, 4, 0))) is None, "a new peer may collide"


def test_a_long_tap_history_falls_back_to_comparing_the_peers() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    for _ in range(JunctionAdmissionMemo._LOG_SCAN_LIMIT + 1):
        memo.taps_changed((40, 40, 0))
    assert _lookup(memo, peers=((6, 6, 0),)) is True
    _store(memo)
    for _ in range(JunctionAdmissionMemo._LOG_SCAN_LIMIT + 1):
        memo.taps_changed((40, 40, 0))
    assert _lookup(memo, peers=()) is None


def test_a_reservation_change_keeps_the_answer_while_the_keepout_cells_agree() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    assert _lookup(memo, reserved_version=6) is True
    assert _lookup(memo, reserved_version=6) is True
    changed = {(3, 5, 0): (9, 9, 0), (2, 4, 0): (1, 1, 0)}
    assert _lookup(memo, reserved_version=7, reserved=changed) is None
    assert len(memo) == 0


def test_a_selection_dependent_answer_survives_neither_a_tap_nor_a_path_change() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo, selection_dependent=True)
    assert _lookup(memo) is True
    assert _lookup(memo, paths_version=3) is None
    _store(memo, selection_dependent=True)
    memo.taps_changed((30, 30, 0))
    assert _lookup(memo) is None, "even a far tap changes the whole selection"


def test_a_net_whose_ports_do_not_cover_a_seen_reservation_is_refused_the_entry() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    assert _lookup(memo, routing_ports=frozenset({(1, 1, 0)})) is None
    assert len(memo) == 1, "the entry still answers for the net whose ports cover it"
    assert _lookup(memo) is True
    _store(memo, reserved_values=(None, None, None))
    assert _lookup(memo, routing_ports=frozenset()) is True, "no reservation seen: any net"


def test_forget_all_counts_what_it_threw_away() -> None:
    memo = JunctionAdmissionMemo()
    _store(memo)
    memo.forget_all()
    assert memo.invalidated == 1
    assert _lookup(memo) is None
