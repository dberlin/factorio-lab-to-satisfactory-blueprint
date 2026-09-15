"""The Satisfactory corpus: twelve FactorioLab ``/sfy/`` URLs and their flows.

Twelve URLs from the first hour of a save to the Manufacturer, each paired with
the CSV FactorioLab itself exported for it.  ``scripts/sfy_audit.py`` builds
every one of them in every Blueprint Designer mark and reports what happened;
this module is the list it reads and the rule it judges by.

Why an entry carries a flow file rather than fetching one
---------------------------------------------------------
R1: nothing on the Satisfactory path re-derives a recipe selection, so a build
exists only where FactorioLab's own solved flow does.  Every file named here was
captured from its entry's URL with :func:`flab2bp.lab.capture.capture_flow_csv`
and committed byte for byte, with its sha256 and length in
``tests/fixtures/sfy_flows/MANIFEST.json``.  ``tests/sfy/test_corpus.py`` holds
each entry to the manifest AND to
:func:`flab2bp.lab.flow.verify_provenance`, which is what stops an entry from
quietly pointing at the wrong capture -- a flow that parses and answers a
different question is exactly the silent wrong answer this project exists to
avoid.

What the gate accepts, and why it is not simply "no exceptions"
---------------------------------------------------------------
Legality is what the hologram does; the audit reports what the validator says.
A refusal is therefore a RESULT, not an error -- but only when its cause is one
the plan already ruled on.  :data:`RULED_CAUSES` is that list, and it is
deliberately a subset of :data:`~flab2bp.sfy.layout.strategy.REFUSALS`: several
of the strategy's causes are excluded on purpose, because a build that ran out
of seconds or of passes, or whose rows contradict the spec, is a build nobody
has shown fits.  The comment above :data:`RULED_CAUSES` names each exclusion.

A width refusal is a missing feature, not a wall
------------------------------------------------
``rows exceed the designer width`` says a recipe group was laid out as ONE
unbroken row wider than the mark -- which is a shape M2 had not written yet, not
a designer that is too small: the same machines sit on the same floor perfectly
well split across two rows.  Row splitting is spec 9.1, and it landed as Task 8c
(``bdd1e8dc``): every one of the corpus's fifteen width refusals turned into a
depth refusal, and ``concrete-60`` in mk2 turned into a build.  The cause stays
in :data:`RULED_CAUSES` because the strategy can still raise it, and this
paragraph stays because the lesson does: a reader who takes a width refusal for
a wall is reading a missing feature as a limit.

What the depth refusals mean, by contrast, is the real M2 ceiling -- R10, one
level of rows inside one designer -- and they say it in centimetres.  The
measured ceiling today is two rows in an mk3.

Tier is orientation, not a budget
---------------------------------
The DSP corpus makes :class:`~flab2bp.bench.corpus.Tier` set the CP-SAT budget.
There is no CP-SAT here: the manifold strategy either lays the rows out in well
under a second or refuses on arithmetic, so the audit's budget is the one number
its ``--budget`` flag carries and the tier says only how big the chain is.

What importing this module still costs
--------------------------------------
Nine ``flab2bp.dsp`` modules, and they are not free-standing: importing ``Tier``
imports :mod:`flab2bp.bench.corpus`, which imports
:class:`flab2bp.rates.CandidatePolicy`, which is the DSP rate solver and brings
``flab2bp.dsp.catalog``, ``registry``, ``rules``, ``colliders``, ``provenance``,
``quaternion`` and the two geometry kernels with it.  A list of twelve
Satisfactory URLs has no use for any of them.

``tests/sfy/test_corpus.py`` pins exactly that and no more: with
``bench.corpus`` already imported, reading this module must add no further DSP
module.  The guard therefore holds the seam where it is rather than closing it
-- closing it means moving ``Tier`` into a leaf module that imports nothing,
which is an M3 chore and deliberately not done here, because moving a name the
DSP corpus reads is a change to the other game's gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from flab2bp.bench.corpus import Tier
from flab2bp.sfy.layout.strategy import REFUSALS
from flab2bp.sfy.pipeline import DESIGNER_MARKS

__all__ = [
    "CLEAN",
    "DESIGNER_MARKS",
    "FLOWS_DIR",
    "RULED_CAUSES",
    "SFY_CORPUS",
    "SfyCorpusEntry",
    "entry",
    "is_ruled_cause",
]

#: What an expectation says when the mark is expected to BUILD rather than
#: refuse.  A plain string beside the causes, so one field says both things and
#: nothing has to carry a cause and a flag that could disagree.
CLEAN: Final = "clean"

#: Where the committed FactorioLab exports live.  The same directory
#: ``tests/sfy/test_rates.py`` reads, because there is one set of captures and
#: a second copy would be a second thing to keep true.
FLOWS_DIR: Final = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sfy_flows"

#: Where an audit run's report is written, by the ``superpowers`` convention.
EVIDENCE_DIR: Final = Path(__file__).resolve().parents[3] / "docs" / "superpowers" / "evidence"

#: Base of every corpus URL.  ``v=11`` first because FactorioLab silently drops
#: settings from a URL that does not carry it.
_LIST: Final = "https://factoriolab.github.io/sfy/list?v=11&o="

#: Refusal causes an M2 gate accepts as a result rather than a miss, each traced
#: to the ruling that allows it:
#:
#: * ``rows exceed the designer depth``, ``rows exceed the designer width`` and
#:   ``row too deep`` -- R10.  M2 lays one level of rows inside one Blueprint
#:   Designer; a chain that wants more band than the mark has is a chain M2 has
#:   no shape for, and says so in centimetres.
#: * ``run exceeds the belt ceiling`` -- R5.  A run above what the build's belt
#:   tier carries.
#: * ``corridor needs a bridge that does not fit`` -- R2/M3.  Belts only, on one
#:   level: where two trunks must cross and the climb does not fit under the
#:   designer's roof there is no lift to fall back on.
#: * ``fluids are M4`` -- R4.
#:
#: Four of the strategy's causes are deliberately absent.  ``corridor
#: assignment exceeded the budget`` is a clock running out, not a shape being
#: impossible, and ``row splitting did not converge`` is the same in passes
#: rather than seconds: the row/corridor walk gave up, which says nothing about
#: whether the build fits.  ``a row makes something the spec never sends out``
#: and ``a row is fed from the corridor on the other side of the build`` are the
#: spec and the placement contradicting each other, which is a defect wherever
#: it appears.  So are the three power causes: a build whose poles cannot be
#: stood or wired is a build this project must fix, not one it may excuse.
RULED_CAUSES: Final = (
    "rows exceed the designer depth",
    "rows exceed the designer width",
    "row too deep",
    "run exceeds the belt ceiling",
    "corridor needs a bridge that does not fit",
    "fluids are M4",
)


def is_ruled_cause(cause: str) -> bool:
    """Whether a refusal with this cause leaves the gate green."""
    return cause in RULED_CAUSES


@dataclass(frozen=True, slots=True)
class SfyCorpusEntry:
    """One URL, its captured flow, and what each designer mark is expected to do.

    ``expects`` pins one outcome per mark -- :data:`CLEAN`, or the ruled cause
    that mark refuses with -- and every one of them is a MEASUREMENT taken by
    ``scripts/sfy_audit.py``, not a wish.  That makes the pair of gates mean two
    different things:

    * the default gate asks only whether each cell is CLEAN or refuses for a
      ruled reason.  An entry that starts BUILDING where it used to refuse is
      progress and must not turn it red, which is exactly what an expectation
      that always gated would do.
    * ``--strict`` asks whether each cell still does what it did, so the same
      corpus doubles as a regression pin: a cause that changes, a clean build
      that starts refusing, and a refusal that starts building are all reported.

    A pin is not an endorsement: it is what the cell MUST do, measured.  When
    ``steel-beam-20`` in mk3 was INVALID, it stayed pinned CLEAN, because
    pinning the defect would have made the gate go green on it.

    Pins go stale when the model improves, and that is the signal working rather
    than a fault: Task 8c turned fifteen width refusals into depth refusals in
    one commit, ``--strict`` named all fifteen, and they were re-measured.  The
    failure message says the cell, its pin and what it got, so it says what to
    write.
    """

    url_id: str
    url: str
    flow_file: str
    tier: Tier
    #: ``(mark, CLEAN or a ruled cause)`` for every mark in ``designers``.  A
    #: tuple of pairs rather than a mapping because the entry is frozen and
    #: hashable, the way ``CorpusEntry.budget_floors`` already is.
    expects: tuple[tuple[str, str], ...] = ()
    designers: tuple[str, ...] = DESIGNER_MARKS
    note: str = ""

    def __post_init__(self) -> None:
        unknown = tuple(m for m in self.designers if m not in DESIGNER_MARKS)
        if unknown:
            raise ValueError(
                f"{self.url_id} asks to be built in {', '.join(unknown)}, which is "
                f"not a Blueprint Designer; the marks are {', '.join(DESIGNER_MARKS)}"
            )
        pinned = dict(self.expects)
        if len(pinned) != len(self.expects):
            raise ValueError(f"{self.url_id} pins the same mark twice in `expects`")
        if set(pinned) != set(self.designers):
            raise ValueError(
                f"{self.url_id} is built in {', '.join(self.designers)} but pins "
                f"{', '.join(sorted(pinned)) or 'nothing'}; --strict needs exactly "
                "one expectation per mark, so a mark with none would be pinned to "
                "nothing at all"
            )
        stray = tuple(
            f"{mark}={outcome!r}"
            for mark, outcome in self.expects
            if outcome != CLEAN and not is_ruled_cause(outcome)
        )
        if stray:
            raise ValueError(
                f"{self.url_id} expects to refuse with {', '.join(stray)}, which "
                f"no ruling allows; an expected refusal must name one of "
                f"{', '.join(RULED_CAUSES)}"
            )

    @property
    def flow_path(self) -> Path:
        """The committed export this entry is built from."""
        return FLOWS_DIR / self.flow_file

    def expectation(self, mark: str) -> str:
        """What ``mark`` is pinned to do: :data:`CLEAN`, or a refusal cause."""
        return dict(self.expects)[mark]

    @property
    def largest(self) -> str:
        """The biggest mark this entry is run in -- its best chance of fitting."""
        return [m for m in DESIGNER_MARKS if m in self.designers][-1]

    @property
    def expected(self) -> Literal["clean", "refuse"]:
        """What the largest mark is expected to do, for a one-line summary."""
        return "clean" if self.expectation(self.largest) == CLEAN else "refuse"

    @property
    def expected_cause(self) -> str | None:
        """The largest mark's expected refusal cause, or ``None`` if it builds."""
        outcome = self.expectation(self.largest)
        return None if outcome == CLEAN else outcome


#: The shape every refusal below takes but the fluid one, spelled once.  Since
#: Task 8c split the over-long rows, depth is the only ceiling left standing.
_DEPTH: Final = "rows exceed the designer depth"
_BRIDGE: Final = "corridor needs a bridge that does not fit"


def _pins(mk1: str, mk2: str, mk3: str) -> tuple[tuple[str, str], ...]:
    """One expectation per mark, in mark order, as ``expects`` wants them."""
    return (("mk1", mk1), ("mk2", mk2), ("mk3", mk3))


SFY_CORPUS: Final[tuple[SfyCorpusEntry, ...]] = (
    SfyCorpusEntry(
        "iron-plate-60",
        _LIST + "iron-plate*60",
        "iron-plate-60.csv",
        Tier.TRIVIAL,
        # Clean in EVERY mark since Task 8d: three smelters make exactly what
        # three constructors eat, so the two groups are laid facing each other
        # with one straight belt per pair and no trunk between them, and the
        # whole build is 2763 cm of band against a mk1's 3200.
        expects=_pins(CLEAN, CLEAN, CLEAN),
        note="the first thing anyone builds: one paired row, smelters facing constructors",
    ),
    SfyCorpusEntry(
        "iron-rod-60",
        _LIST + "iron-rod*60",
        "iron-rod-60.csv",
        Tier.TRIVIAL,
        expects=_pins(_DEPTH, _DEPTH, CLEAN),
        note="the other half of the first hour, and a wider constructor row",
    ),
    SfyCorpusEntry(
        "concrete-60",
        _LIST + "concrete*60",
        "concrete-60.csv",
        Tier.TRIVIAL,
        expects=_pins(_DEPTH, CLEAN, CLEAN),
        note="limestone: the shortest chain in the corpus",
    ),
    SfyCorpusEntry(
        "screw-120",
        _LIST + "screw*120",
        "screw-120.csv",
        Tier.SMALL,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note="three rows at double rate: the first chain no mark holds",
    ),
    SfyCorpusEntry(
        "wire-120-cable-60",
        _LIST + "wire*120&o=cable*60",
        "wire-120-cable-60.csv",
        Tier.SMALL,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note="two objectives, one feeding the other: the only multi-objective URL",
    ),
    SfyCorpusEntry(
        "steel-beam-20",
        _LIST + "steel-beam*20",
        "steel-beam-20.csv",
        Tier.SMALL,
        # The entry that found the gate's first defect: `flow.capacity` compared
        # the belt feeding a group's underclocked last machine against the
        # group's FULL-clock demand, and this is the first corpus flow with a
        # shard on a belt at all.  Fixed in `729a7e81`; mk3 has been CLEAN since.
        # mk2 refuses on the BRIDGE since Task 8d rather than on the depth: the
        # two rows now fit its 4000 cm, and what does not fit is the ore trunk
        # riding over the coal trunk in the 301 cm of column a mk2 leaves at the
        # wall.  Both causes are ruled and both say the build wants a mk3.
        expects=_pins(_DEPTH, _BRIDGE, CLEAN),
        note="the Foundry: two ores into one machine, a footprint no other entry has",
    ),
    SfyCorpusEntry(
        "rotor-10",
        _LIST + "rotor*10",
        "rotor-10.csv",
        Tier.SMALL,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note="rod and screw converging on one Assembler",
    ),
    SfyCorpusEntry(
        "reinforced-iron-plate-10",
        _LIST + "reinforced-iron-plate*10",
        "reinforced-iron-plate-10.csv",
        Tier.SMALL,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note="five rows: measured at 11000 cm of band against mk3's 4800",
    ),
    SfyCorpusEntry(
        "modular-frame-5",
        _LIST + "modular-frame*5",
        "modular-frame-5.csv",
        Tier.MID,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note="six rows: an Assembler over two sub-chains",
    ),
    SfyCorpusEntry(
        "smart-plating-5",
        _LIST + "smart-plating*5",
        "smart-plating-5.csv",
        Tier.MID,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note="seven rows: a project part, rotor and plate together",
    ),
    SfyCorpusEntry(
        "heavy-modular-frame-2",
        _LIST + "heavy-modular-frame*2",
        "heavy-modular-frame-2.csv",
        Tier.LARGE,
        expects=_pins(_DEPTH, _DEPTH, _DEPTH),
        note=(
            "the Manufacturer: four inputs, the deepest chain in the corpus, and "
            "wide before it is deep -- one row reaches x = 5660 cm, past the "
            "4800 cm half-width even an mk3 has"
        ),
    ),
    SfyCorpusEntry(
        "plastic-20",
        _LIST + "plastic*20",
        "plastic-20.csv",
        Tier.TRIVIAL,
        expects=_pins("fluids are M4", "fluids are M4", "fluids are M4"),
        note="the fluid URL, here to be refused: crude oil into a Refinery",
    ),
)

_BY_URL_ID: Final = {item.url_id: item for item in SFY_CORPUS}

# A ruled cause the strategy cannot raise would green the gate on a refusal that
# never happens, so the list is checked against the strategy's own here as well
# as in the tests: importing this module at all is enough to catch it.
_STRAY = tuple(cause for cause in RULED_CAUSES if cause not in REFUSALS)
if _STRAY:  # pragma: no cover - a coding error, caught at import
    raise ValueError(f"ruled causes the strategy never raises: {', '.join(_STRAY)}")


def entry(url_id: str) -> SfyCorpusEntry:
    """The corpus entry with this id."""
    try:
        return _BY_URL_ID[url_id]
    except KeyError:
        raise KeyError(f"no Satisfactory corpus entry {url_id!r}") from None
