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
deliberately a subset of :data:`~flab2bp.sfy.layout.strategy.REFUSALS`: three of
the strategy's causes are excluded on purpose, because a build that ran out of
seconds, or whose rows contradict the spec, is a build nobody has shown fits.

Tier is orientation, not a budget
---------------------------------
The DSP corpus makes :class:`~flab2bp.bench.corpus.Tier` set the CP-SAT budget.
There is no CP-SAT here: the manifold strategy either lays the rows out in well
under a second or refuses on arithmetic, so the audit's budget is the one number
its ``--budget`` flag carries and the tier says only how big the chain is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from flab2bp.bench.corpus import Tier
from flab2bp.sfy.layout.strategy import REFUSALS
from flab2bp.sfy.pipeline import DESIGNER_MARKS

__all__ = [
    "DESIGNER_MARKS",
    "FLOWS_DIR",
    "RULED_CAUSES",
    "SFY_CORPUS",
    "SfyCorpusEntry",
    "entry",
    "is_ruled_cause",
]

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
#: Three of the strategy's causes are deliberately absent.  ``corridor
#: assignment exceeded the budget`` is a clock running out, not a shape being
#: impossible.  ``a row makes something the spec never sends out`` and ``a row
#: is fed from the corridor on the other side of the build`` are the spec and
#: the placement contradicting each other, which is a defect wherever it
#: appears.  So are the three power causes: a build whose poles cannot be stood
#: or wired is a build this project must fix, not one it may excuse.
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
    """One URL, its captured flow, and what it is expected to do.

    ``expected`` and ``expected_cause`` describe the LARGEST mark in
    ``designers`` -- the one with the best chance of holding the chain -- and
    they are documentation of a measurement rather than the gate's rule.  The
    gate's rule is :data:`RULED_CAUSES`, applied to every mark: an entry that
    starts building where it used to refuse must not turn the gate red, and an
    expectation that gated would do exactly that.
    """

    url_id: str
    url: str
    flow_file: str
    tier: Tier
    expected: Literal["clean", "refuse"] = "clean"
    expected_cause: str | None = None
    designers: tuple[str, ...] = DESIGNER_MARKS
    note: str = ""

    def __post_init__(self) -> None:
        if self.expected == "refuse" and not is_ruled_cause(self.expected_cause or ""):
            raise ValueError(
                f"{self.url_id} expects to refuse with {self.expected_cause!r}, which "
                "no ruling allows; an expected refusal must name one of "
                f"{', '.join(RULED_CAUSES)}"
            )
        if self.expected == "clean" and self.expected_cause is not None:
            raise ValueError(
                f"{self.url_id} expects a clean build and also names the cause "
                f"{self.expected_cause!r}; it can only be one of the two"
            )
        unknown = tuple(m for m in self.designers if m not in DESIGNER_MARKS)
        if unknown:
            raise ValueError(
                f"{self.url_id} asks to be built in {', '.join(unknown)}, which is "
                f"not a Blueprint Designer; the marks are {', '.join(DESIGNER_MARKS)}"
            )

    @property
    def flow_path(self) -> Path:
        """The committed export this entry is built from."""
        return FLOWS_DIR / self.flow_file


SFY_CORPUS: Final[tuple[SfyCorpusEntry, ...]] = (
    SfyCorpusEntry(
        "iron-plate-60",
        _LIST + "iron-plate*60",
        "iron-plate-60.csv",
        Tier.TRIVIAL,
        note="the first thing anyone builds: two rows, smelter then constructor",
    ),
    SfyCorpusEntry(
        "iron-rod-60",
        _LIST + "iron-rod*60",
        "iron-rod-60.csv",
        Tier.TRIVIAL,
        note="the other half of the first hour, and a wider constructor row",
    ),
    SfyCorpusEntry(
        "concrete-60",
        _LIST + "concrete*60",
        "concrete-60.csv",
        Tier.TRIVIAL,
        note="limestone: the shortest chain in the corpus",
    ),
    SfyCorpusEntry(
        "screw-120",
        _LIST + "screw*120",
        "screw-120.csv",
        Tier.SMALL,
        expected="refuse",
        expected_cause="rows exceed the designer depth",
        note="three rows at double rate: the first chain no mark holds",
    ),
    SfyCorpusEntry(
        "wire-120-cable-60",
        _LIST + "wire*120&o=cable*60",
        "wire-120-cable-60.csv",
        Tier.SMALL,
        expected="refuse",
        expected_cause="rows exceed the designer width",
        note="two objectives, one feeding the other: the only multi-objective URL",
    ),
    SfyCorpusEntry(
        "steel-beam-20",
        _LIST + "steel-beam*20",
        "steel-beam-20.csv",
        Tier.SMALL,
        note="the Foundry: two ores into one machine, a footprint no other entry has",
    ),
    SfyCorpusEntry(
        "rotor-10",
        _LIST + "rotor*10",
        "rotor-10.csv",
        Tier.SMALL,
        expected="refuse",
        expected_cause="rows exceed the designer width",
        note="rod and screw converging on one Assembler",
    ),
    SfyCorpusEntry(
        "reinforced-iron-plate-10",
        _LIST + "reinforced-iron-plate*10",
        "reinforced-iron-plate-10.csv",
        Tier.SMALL,
        expected="refuse",
        expected_cause="rows exceed the designer depth",
        note="five rows: measured at 11000 cm of band against mk3's 4800",
    ),
    SfyCorpusEntry(
        "modular-frame-5",
        _LIST + "modular-frame*5",
        "modular-frame-5.csv",
        Tier.MID,
        expected="refuse",
        expected_cause="rows exceed the designer depth",
        note="six rows: an Assembler over two sub-chains",
    ),
    SfyCorpusEntry(
        "smart-plating-5",
        _LIST + "smart-plating*5",
        "smart-plating-5.csv",
        Tier.MID,
        expected="refuse",
        expected_cause="rows exceed the designer depth",
        note="seven rows: a project part, rotor and plate together",
    ),
    SfyCorpusEntry(
        "heavy-modular-frame-2",
        _LIST + "heavy-modular-frame*2",
        "heavy-modular-frame-2.csv",
        Tier.LARGE,
        expected="refuse",
        expected_cause="rows exceed the designer width",
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
        expected="refuse",
        expected_cause="fluids are M4",
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
