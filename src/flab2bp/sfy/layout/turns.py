"""Registry-backed turn identities and exact immediate-leg legality."""

from __future__ import annotations

from flab2bp.sfy.layout.corridors import (
    Measures,
    Turn,
    arc_turn,
    attachment_turn_tight,
    descent_run_cm,
)
from flab2bp.sfy.layout.lattice import Lattice, Node
from flab2bp.sfy.layout.validate import TOUCH_CM
from flab2bp.sfy.registry import Registry

_EPS = 1e-9


def options(measures: Measures) -> tuple[Turn, ...]:
    """Stable witness actions: arc first, tight attachment second."""
    return (arc_turn(measures), attachment_turn_tight(measures))


def free_run(run: float, rise: float, registry: Registry) -> float:
    """Room left for turns after reserving the registry's exact incline run."""
    return max(0.0, run - descent_run_cm(rise, registry))


def fits_turn(turn: Turn, free: float, previous: Turn | None) -> bool:
    """Share a legal straight, but never share attachment bodies or mouth leads."""
    required = turn.cost
    if previous is not None:
        required = max(turn.cost + previous.reach, previous.cost + turn.reach)
        if turn.is_attachment and previous.is_attachment:
            required = max(required, turn.body_reach + previous.body_reach)
    return required <= free + _EPS


def eligible(
    turn: Turn,
    incoming_rise: float,
    outgoing_rise: float,
    node: Node,
    lattice: Lattice,
) -> bool:
    """An attachment needs flat immediate legs and its own standing domain."""
    return not turn.is_attachment or (
        abs(incoming_rise) <= TOUCH_CM
        and abs(outgoing_rise) <= TOUCH_CM
        and node[0] in lattice.object_lines
        and node[1] in lattice.object_lines
    )
