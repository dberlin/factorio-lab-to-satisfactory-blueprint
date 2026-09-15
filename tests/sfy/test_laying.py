"""The corridor layer: where a column stands and how much of a transverse is clear.

Both are arithmetic on a handful of numbers, and a whole build is a poor place to
read either of them.  What the belts the layer draws actually look like is judged
where a whole placement is: ``tests/sfy/test_strategy.py`` hands every build to
the validator, and ``tests/sfy/test_corridors.py`` pins the path shapes.
"""

from __future__ import annotations

import itertools
import math

import pytest

from flab2bp.layout.budget import WorkBudget
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.layout import strategy
from flab2bp.sfy.layout.corridors import BRIDGE_CLEARANCE_BOXES
from flab2bp.sfy.layout.laying import CorridorLayer
from flab2bp.sfy.layout.nets import CorridorPlan
from flab2bp.sfy.layout.rows import RowPlan
from flab2bp.sfy.spec import designer
from tests.sfy.conftest import flow_spec, sfy_registry

#: The designer the numbers below are read against; see ``test_strategy.py``.
MARK = "mk3"


def _layer(x_edge: float, offset: float) -> CorridorLayer:
    """A layer with its numbers read and nothing to draw.

    The two plans are empty apart from the two distances the columns are
    measured from, which is all either decision below reads.
    """
    spec = flow_spec("iron-plate-60")
    return CorridorLayer(
        spec=spec,
        registry=sfy_registry(),
        lab_map=load_lab_map(),
        budget=WorkBudget(),
        measures=strategy._measure(sfy_registry(), designer(MARK, sfy_registry())),
        ids=itertools.count(1),
        row_plan=RowPlan(rows=(), belt_z=0.0, x_edge=x_edge, band_cm=0.0),
        corridor_plan=CorridorPlan(nets=(), spine={}, offset=offset, reach_cm=0.0),
    )


def test_how_much_of_a_transverse_is_clear_is_measured_from_the_nearest_crossing() -> None:
    """The slope has to be over before the crossing it is closest to.

    At a row's end of a transverse that is the innermost column crossed; at the
    corridor's end it is the outermost.  Taking the closest says both, and it is
    a real number in both cases -- a ``room`` that answered "unbounded" would let
    a belt come down through the belt it just climbed over.
    """
    layer = _layer(x_edge=1300.0, offset=300.0)
    assert (layer._column_x(1, 0), layer._column_x(1, 1)) == (1600.0, 1900.0)
    clear = BRIDGE_CLEARANCE_BOXES * 79.0
    # A row's chain end at x = 1000: the nearest of the two crossings is column 0.
    assert layer._room(1000.0, (0, 1), 1) == pytest.approx(600.0 - clear)
    # A node standing at x = 2200 out in the corridor: the nearest is column 1.
    assert layer._room(2200.0, (0, 1), 1) == pytest.approx(300.0 - clear)
    assert layer._room(1000.0, (), 1) == math.inf
