from flab2bp.indexed import PortReservations


def test_first_cell_survives_reassignment_but_moves_after_reinsertion() -> None:
    first, second, third = (0, 0, 0), (1, 0, 0), (2, 0, 0)
    port, other = (9, 0, 0), (10, 0, 0)
    reserved = PortReservations({first: port, second: other, third: port})

    reserved[first] = other
    assert reserved.first_for(port) == third
    assert reserved.first_for(other) == first
    reserved[second] = port
    assert reserved.first_for(port) == second
    reserved[first] = port
    assert reserved.first_for(port) == first
    del reserved[first]
    reserved[first] = port
    assert reserved.first_for(port) == second
    assert tuple(reserved) == (second, third, first)


def test_release_restore_and_copy_keep_independent_reverse_ownership() -> None:
    first, second, port = (0, 0, 0), (1, 0, 0), (9, 0, 0)
    reserved = PortReservations({first: port, second: port})
    saved = dict(reserved)
    copied = PortReservations(reserved)

    assert reserved.pop(first) == port
    assert reserved.first_for(port) == second
    assert copied.first_for(port) == first
    reserved.clear()
    assert reserved.first_for(port) is None
    reserved.update(saved)
    assert reserved.first_for(port) == first
    reserved.pop(first)
    reserved.pop(second)
    assert reserved.first_for(port) is None
    assert reserved.setdefault(second, port) == port
    assert reserved.first_for(port) == second


def test_version_moves_only_when_the_mapping_actually_changes() -> None:
    reserved = PortReservations()
    assert reserved.version == 0
    reserved[(0, 0, 0)] = (5, 5, 0)
    assert reserved.version == 1
    reserved[(0, 0, 0)] = (5, 5, 0)
    assert reserved.version == 1, "reassigning the same port is not a change"
    reserved[(0, 0, 0)] = (6, 6, 0)
    assert reserved.version == 2
    del reserved[(0, 0, 0)]
    assert reserved.version == 3
    reserved.clear()
    assert reserved.version == 3, "clearing an empty mapping is not a change"
    reserved.update({(1, 0, 0): (5, 5, 0), (2, 0, 0): (5, 5, 0)})
    assert reserved.version == 5
    reserved.popitem()
    assert reserved.version == 6
    reserved.clear()
    assert reserved.version == 7
    copied = PortReservations(reserved)
    assert copied.version == 0, "a copy starts its own history"
