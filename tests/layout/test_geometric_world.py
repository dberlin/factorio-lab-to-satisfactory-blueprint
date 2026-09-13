from __future__ import annotations

from flab2bp.layout.geometric_world import GridIndex


def test_grid_index_round_trips_every_cell_of_a_small_box() -> None:
    codec = GridIndex(gx0=-3, gy0=5, rows=4, levels=3)
    seen = set()
    for x in range(-3, 3):
        for y in range(5, 9):
            for z in range(3):
                index = codec.encode((x, y, z))
                assert codec.decode(index) == (x, y, z)
                seen.add(index)
    assert seen == set(range(6 * 4 * 3))


def test_grid_index_matches_the_routing_grid_layout() -> None:
    # _Grid computes (x - gx0) * xstep + (y - gy0) * levels + lvl with xstep = gh * levels.
    codec = GridIndex(gx0=2, gy0=-1, rows=7, levels=2)
    assert codec.encode((2, -1, 0)) == 0
    assert codec.encode((2, 0, 1)) == 3
    assert codec.encode((3, -1, 0)) == 7 * 2
