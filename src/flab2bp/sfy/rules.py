"""What the build gun's hologram accepts, read out of the shipped binary.

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
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "REQUIRED_RULE_IDS",
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
)

RULE_STATUSES = ("extracted", "partial", "unextractable")

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

    ``comparison`` is the branch in the game's own terms; ``interpretation`` is
    what that means for a placer, and is the only field written by a human.
    ``header`` is the declaration the rule belongs to, as ``file:line`` in
    ``CommunityResources/Headers.zip``.
    """

    id: str
    cls: str
    function: str
    rva: str
    status: str
    reads: tuple[str, ...]
    constants: tuple[str, ...]
    calls: tuple[str, ...]
    comparison: str
    interpretation: str
    evidence: tuple[str, ...]
    header: str


def _strings(raw: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(v) for v in raw)


def _rule(raw: Mapping[str, Any]) -> HologramRule:
    try:
        rule_id = str(raw["id"])
        status = str(raw["status"])
        if status not in RULE_STATUSES:
            raise RulesError(f"rule {rule_id!r} has an unknown status {status!r}")
        return HologramRule(
            id=rule_id,
            cls=str(raw["class"]),
            function=str(raw["function"]),
            rva=str(raw["rva"]),
            status=status,
            reads=_strings(raw["reads"]),
            constants=_strings(raw["constants"]),
            calls=_strings(raw["calls"]),
            comparison=str(raw["comparison"]),
            interpretation=str(raw["interpretation"]),
            evidence=_strings(raw["evidence"]),
            header=str(raw["header"]),
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
