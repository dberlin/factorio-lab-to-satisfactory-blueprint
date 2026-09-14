"""What the game's own code does, read out of the shipped binary.

Legality in Satisfactory is whatever the hologram does or allows. A blueprint in
a community corpus is not evidence of it: the corpus carries clipped geometry,
hacked saves and builds from older game versions, so a number it contains says
only that *something* once produced it. The rules here come from the machine
code of the validators the hologram itself runs --
``AFGConveyorBeltHologram::ValidateCurvature`` and its siblings -- as extracted
by ``scripts/sfy_native_rules.py`` into ``data/hologram_rules.json``.

Every rule says how well it is known, and nothing here is filled in from a
header comment or from what a blueprint happens to contain:

``extracted``      the comparison's operands and its branch are named in
                   :attr:`HologramRule.evidence`; :attr:`HologramRule.comparison`
                   is a transcription of those instructions
``partial``        the members are seen being read, but the comparison itself is
                   somewhere this extraction did not follow -- a callee, or a
                   ``.pdata`` chunk ``sfy-native disasm`` cannot reach by symbol.
                   :attr:`HologramRule.comparison` names where it is
``unextractable``  nothing was read; :attr:`HologramRule.interpretation` says why

A ``partial`` rule is a bound the placer must not assume it knows. Treat its
:attr:`HologramRule.interpretation` as the lead for the next extraction, never as
a constraint to enforce.

Every rule also says *what the hologram does* with the number --
:attr:`HologramRule.effect`, one of :data:`RULE_EFFECTS`:

``refuse``   a validation disqualifies the hologram, so the placement is rejected
``clamp``    the value is forced into range and the placement goes ahead
``snap``     the value is quantised or aligned and the placement goes ahead
``none``     no enforcement was found in the instructions that were read
``compute``  the function is not a validation at all: it works out a value the
             game then writes, and turns no placement away

The difference matters to a placer and to a validator: a ``clamp`` or a ``snap``
never produces a refusal, so treating one as a bound refuses builds the game
would accept, and treating a ``refuse`` as a clamp ships blueprints the game
turns away. ``none`` is only allowed on a rule that is not ``extracted``: if the
comparison and its branch were read, what the branch does is known. ``compute``
is the other side of that: the code *was* read in full, and what it does is
produce a number rather than judge one. A ``compute`` rule is what this project
reproduces when it authors a blueprint, never a bound it enforces.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "REQUIRED_RULE_IDS",
    "RULE_EFFECTS",
    "RULE_STATUSES",
    "HologramRule",
    "RulesError",
    "load_rules",
]

# Every rule the M2 validator is entitled to ask for. A build of the game that
# renamed or removed one of these functions must fail loudly here rather than
# leave the validator silently unconstrained, so :func:`load_rules` refuses a
# payload that is missing any of them.
REQUIRED_RULE_IDS = (
    "belt.curvature",
    "belt.incline",
    "belt.min_length",
    "belt.max_length",
    "belt.clearance",
    "belt.snap_directions",
    "pipe.min_length",
    "pipe.curvature",
    "pipe.max_length",
    "pipe.fluid_requirements",
    "lift.height_range",
    "lift.step",
    "lift.placement",
    "lift.clearance",
    "buildable.grid_snap",
    "buildable.rotation_step",
    "buildable.clearance",
    "belt.cost",
    "manufacturer.inventory_filters",
    "belt.straight_tangents",
    "factory.potential",
    "manufacturer.production_boost",
)

RULE_STATUSES = ("extracted", "partial", "unextractable")

# What the hologram does with the value, as the instructions show it. See the
# module docstring: ``none`` says nothing was seen enforcing it, which a rule
# whose branch was read cannot claim.
RULE_EFFECTS = ("refuse", "clamp", "snap", "none", "compute")

DATA_DIR = Path(__file__).resolve().parent / "data"
RULES_PATH = DATA_DIR / "hologram_rules.json"


class RulesError(ValueError):
    """The rules payload was missing a required rule or carried a malformed one."""


@dataclass(frozen=True, slots=True)
class HologramRule:
    """One placement rule, with the instructions it was read from.

    ``rva`` is the entry of the function in the shipped module, as a hex string,
    so that a reader can re-run ``sfy-native disasm`` and land on the same
    bytes; it is empty only on an ``unextractable`` rule. ``reads`` are the
    ``this``-relative members the function touches, ``constants`` the ``.rdata``
    values its operands point at and ``calls`` the call targets that resolved to
    a symbol -- all three straight from the tool, never hand-listed.

    ``also_read`` is the other functions the rule was read across, each as
    ``Class::Method @ 0xrva``, for a rule the game spreads over more than one
    function; it is empty for a rule read from ``function`` alone.

    ``comparison`` is the branch in the game's own terms; ``interpretation`` is
    what that means for a placer, and is the only field written by a human.
    ``effect`` is what the hologram does with the value -- see
    :data:`RULE_EFFECTS`; a consumer that reads a bound must read this beside
    it, because only ``refuse`` turns a placement away. ``header`` is the
    declaration the rule belongs to, as ``file:line`` in
    ``CommunityResources/Headers.zip``.
    """

    id: str
    cls: str
    function: str
    rva: str
    status: str
    effect: str
    reads: tuple[str, ...]
    constants: tuple[str, ...]
    calls: tuple[str, ...]
    comparison: str
    interpretation: str
    evidence: tuple[str, ...]
    header: str
    also_read: tuple[str, ...] = ()


def _strings(raw: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(v) for v in raw)


def _rule(raw: Mapping[str, Any]) -> HologramRule:
    try:
        rule_id = str(raw["id"])
        status = str(raw["status"])
        if status not in RULE_STATUSES:
            raise RulesError(f"rule {rule_id!r} has an unknown status {status!r}")
        effect = str(raw["effect"])
        if effect not in RULE_EFFECTS:
            raise RulesError(f"rule {rule_id!r} has an unknown effect {effect!r}")
        if effect == "none" and status == "extracted":
            raise RulesError(
                f"rule {rule_id!r} is 'extracted' but its effect is 'none': a branch that "
                "was read says what it does, so either the effect or the status is wrong"
            )
        return HologramRule(
            id=rule_id,
            cls=str(raw["class"]),
            function=str(raw["function"]),
            rva=str(raw["rva"]),
            status=status,
            effect=effect,
            reads=_strings(raw["reads"]),
            constants=_strings(raw["constants"]),
            calls=_strings(raw["calls"]),
            comparison=str(raw["comparison"]),
            interpretation=str(raw["interpretation"]),
            evidence=_strings(raw["evidence"]),
            header=str(raw["header"]),
            also_read=_strings(raw.get("also_read", ())),
        )
    except KeyError as exc:
        raise RulesError(f"hologram rule is missing the {exc.args[0]!r} key: {raw}") from exc


def load_rules(path: Path | None = None) -> dict[str, HologramRule]:
    """Read ``data/hologram_rules.json``, keyed by rule id.

    Raises :class:`RulesError` when the file is missing, is not the payload
    ``scripts/sfy_native_rules.py`` writes, or does not carry every id in
    :data:`REQUIRED_RULE_IDS` -- the caller gets no half-built rule set.
    """
    path = RULES_PATH if path is None else Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RulesError(f"no hologram rules at {path}") from None
    except json.JSONDecodeError as exc:
        raise RulesError(f"hologram rules at {path} are not JSON: {exc}") from exc
    try:
        raw_rules = data["rules"]
    except (KeyError, TypeError):
        raise RulesError(f"hologram rules at {path} have no 'rules' section") from None
    rules = {}
    for raw in raw_rules:
        rule = _rule(raw)
        if rule.id in rules:
            raise RulesError(f"hologram rules name {rule.id!r} twice")
        rules[rule.id] = rule
    missing = [rule_id for rule_id in REQUIRED_RULE_IDS if rule_id not in rules]
    if missing:
        raise RulesError(f"hologram rules are missing: {missing}")
    return rules
