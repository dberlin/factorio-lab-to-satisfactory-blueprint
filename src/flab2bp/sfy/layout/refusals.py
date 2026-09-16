"""Every reason a Satisfactory layout strategy refuses, and the one place one is built.

A refusal is a RESULT, not an error: a strategy either hands back a placement the
validator passes or says, in a named cause, why it will not.  :data:`REFUSALS` is
the whole vocabulary of those causes and :func:`refuse` is the only constructor
of the exception that carries one, so nothing anywhere below a strategy can
invent a cause -- a stage is handed :func:`refuse` rather than the table, and a
string that is not in the table raises ``ValueError`` at the moment it is used
rather than reaching a caller as a cause nobody declared.

Why this is a module of its own
-------------------------------
Two strategies name these causes, and neither of them is where the list belongs:
:mod:`flab2bp.sfy.layout.strategy` used to own it, which meant that
:mod:`flab2bp.bench.sfy_corpus` -- twelve URLs and a rule for judging them --
imported a whole layout engine to check that a pinned cause is one somebody can
raise.  This module imports the exception class and nothing else, so reading the
vocabulary costs the vocabulary.

Legality is the validator's
---------------------------
Nothing here decides what the game accepts.  A cause says which shape THIS
project could not lay out; whether a placement is legal is what
``validate(placement, spec, registry)`` answers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from flab2bp.layout.base import NoValidLayout

if TYPE_CHECKING:  # pragma: no cover - the spec is read, never imported at runtime
    from flab2bp.sfy.spec import SfyBuildSpec

__all__ = ["GAME_DATA", "GAME_LIMITS", "REFUSALS", "refuse"]

GAME_DATA = "the game data does not describe a machine this build needs"
"""One cause for every way the extraction leaves a hole a build falls into.

A missing buildable, a machine with no hard clearance box or no belt output port,
a belt tier the lab map has no class for: all of them are the same answer to the
caller -- this build cannot be authored from the game data we have -- and all of
them carry the detail that says which.
"""

GAME_LIMITS = "the game data states no limit this build needs"
"""The same, for a ``limits`` field the registry does not carry: the hologram
grid, the minimum belt length, the maximum incline, the bend radius, the maximum
spline.  Separate from :data:`GAME_DATA` because a missing bound is a different
gap from a missing machine, and a reader chasing one is not chasing the other."""

REFUSALS: Final[tuple[str, ...]] = (
    # --- MANIFOLD: only flab2bp.sfy.layout.strategy raises these --------------
    #
    # The M2 brief's own list, minus the shared causes further down: the five
    # named ones plus GAME_DATA and GAME_LIMITS.
    "rows exceed the designer depth",
    "rows exceed the designer width",
    "corridor needs a bridge that does not fit",
    "row too deep",
    "corridor assignment exceeded the budget",
    # The row builder's other causes, each kept as itself rather than folded into
    # a designer bound it has nothing to do with.
    "row too tall",
    "more input items than the machine has belt ports",
    "a row drains one of several products",
    "a feeder crosses the chain inside it",
    # The corridor's own, beyond the three the brief names.
    "a trunk would have to run back down the corridor",
    "a corridor path has no length",
    "a curved leg is longer than a belt may be",
    "layout exceeded the budget",
    # Two more this shape of build can hit that the brief does not name.
    "a row makes something the spec never sends out",
    "a row is fed from the corridor on the other side of the build",
    # The row/corridor walk's own backstop, kept separate from the width bound it
    # used to borrow: a build that ran out of PASSES has not been shown not to
    # fit, and a reader told "rows exceed the designer width" would go measuring
    # a designer when what happened is that the walk gave up.
    "row splitting did not converge",
    # The belt the spec asks for, which only the row builder reads today.
    "this spec names no belt",
    #
    # --- GRID: only the grid-routed strategy raises these ---------------------
    #
    # Named here before that strategy is written, so that the corpus can pin a
    # cause and a reviewer can read the whole vocabulary in one place.  A cause
    # a strategy cannot raise greens nothing: an entry pinned to one simply
    # never matches.
    "a port is off the hologram lattice",
    "the packer found no arrangement",
    "a belt could not be routed",
    "a belt could not be laid",
    "a tap has no room for a splitter",
    "routing exceeded the budget",
    "packing exceeded the budget",
    #
    # --- SHARED: either strategy raises these --------------------------------
    #
    # The power stage and the belt ceiling are the same code under both, and a
    # fluid is refused before either lays any geometry at all.  Listed once,
    # which is the point of one table: a second copy under GRID would be a
    # second string to keep equal to this one.
    "no room for a power pole",
    "a machine has no power connection",
    "wire exceeds the maximum length",
    "run exceeds the belt ceiling",
    "fluids are M5",
    # The extraction's two gaps, shared because both strategies are laid out
    # against the same ``registry.json``.
    GAME_DATA,
    GAME_LIMITS,
)
"""Every reason a Satisfactory strategy refuses with, and the only ones it may use."""


def refuse(spec: SfyBuildSpec, reason: str, detail: str = "") -> NoValidLayout:
    """The one place a refusal is built, so every cause is one of the named ones."""
    if reason not in REFUSALS:
        raise ValueError(f"{reason!r} is not one of the named refusals")
    return NoValidLayout(
        reason,
        spec_label=spec.label or "this build",
        attempt_reasons=(detail,) if detail else (),
    )
