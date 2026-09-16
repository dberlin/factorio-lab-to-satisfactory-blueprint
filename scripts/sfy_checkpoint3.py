"""Write checkpoint 3: two complete factories and a separate conveyor witness.

The concrete/mk1 and plate/mk1 factories use connected production sections.
The user-approved third pair independently exercises a lift and attachment turn.
It is a transport component, not a complete corpus factory or a routing benchmark.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from fractions import Fraction
from itertools import count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from flab2bp.bench.sfy_corpus import entry  # noqa: E402
from flab2bp.sfy import pipeline  # noqa: E402
from flab2bp.sfy.codec import read_pair, write_sbp, write_sbp_file, write_sbpcfg  # noqa: E402
from flab2bp.sfy.header import BlueprintRecord  # noqa: E402
from flab2bp.sfy.layout.emit import decode, emit  # noqa: E402
from flab2bp.sfy.layout.floor import foundations  # noqa: E402
from flab2bp.sfy.layout.lattice import GROUND_LEVEL, Lattice  # noqa: E402
from flab2bp.sfy.layout.model import SfyPlacement  # noqa: E402
from flab2bp.sfy.layout.motion import lift_heights  # noqa: E402
from flab2bp.sfy.layout.realise import Terminal, realise  # noqa: E402
from flab2bp.sfy.layout.strategy import _measure  # noqa: E402
from flab2bp.sfy.layout.validate import Report, validate  # noqa: E402
from flab2bp.sfy.spec import SfyBuildSpec, designer  # noqa: E402
from scripts.sfy_checkpoint2 import (  # noqa: E402
    OUT_DIR,
    PARTIAL_RULE_CHECKS,
    Case,
    CheckFailed,
    Written,
    _open_ends,
    _pair_section,
    _redressed,
    build_case,
)

README_NAME = "checkpoint3-README.md"
WITNESS_NAME = "checkpoint3-lift-attachment-transport"
BELT_CLASS = "Build_ConveyorBeltMk1_C"
LIFT_CLASS = "Build_ConveyorLiftMk1_C"
WITNESS_ITEM = "iron-ore"
WITNESS_RATE = Fraction(1)
WITNESS_SKIPS = frozenset((*PARTIAL_RULE_CHECKS, "power.wires"))


def _case(url_id: str, question: str) -> Case:
    corpus = entry(url_id)
    return Case(f"checkpoint3-{url_id}", corpus.flow_file, corpus.url, "mk1", question)


CASES = (
    _case("concrete-60", "does the smallest factory deliver 60 concrete/min"),
    _case("iron-plate-60", "does the connected factory deliver 60 plates/min"),
)


@dataclass(frozen=True, slots=True)
class TransportWitness:
    """A checked conveyor component, deliberately distinct from a factory build."""

    placement: SfyPlacement
    spec: SfyBuildSpec
    sbp: Path
    cfg: Path
    report: Report


def build_witness(out_dir: Path) -> TransportWitness:
    registry = pipeline.registry()
    mark = designer("mk1", registry)
    lattice = Lattice.over(mark, registry)
    measures = _measure(registry, mark)
    heights = lift_heights(lattice, registry)
    if not heights:
        raise CheckFailed("the registry/designer offers no legal conveyor lift")
    height = heights[1] if len(heights) > 1 else heights[0]
    top = GROUND_LEVEL + height
    # Deliberate witness geometry, not a factory placement policy. Four grid
    # steps fit the shipped arc radius; the realiser chooses the actual turn.
    leg = math.ceil(measures.radius / lattice.grid_cm)
    x = lattice.n // 4
    y = lattice.n // 2 - leg
    lift_x = x + leg
    path = (
        *((x, row, GROUND_LEVEL) for row in range(1, y + 1)),
        *((column, y, GROUND_LEVEL) for column in range(x + 1, lift_x + 1)),
        (lift_x, y, top),
        *((lift_x, row, top) for row in range(y + 1, lattice.n)),
    )
    start, end = lattice.world(path[0]), lattice.world(path[-1])
    source = Terminal(path[0], (start[0], -mark.half_cm, start[2]), (0, 1, 0), None, "wall")
    sink = Terminal(path[-1], (end[0], mark.half_cm, end[2]), (0, -1, 0), None, "wall")
    ids = count(1)
    built = realise(
        path,
        source=source,
        sink=sink,
        lattice=lattice,
        measures=measures,
        registry=registry,
        belt_class=BELT_CLASS,
        lift_class=LIFT_CLASS,
        item_id=WITNESS_ITEM,
        rate=WITNESS_RATE,
        ids=ids,
    )
    if not built.lifts or "attachment" not in built.turns:
        raise CheckFailed("the transport witness did not author both a lift and an attachment turn")
    speed = registry.buildables[BELT_CLASS].belt_speed_per_min
    if speed is None or speed <= 0:
        raise CheckFailed("the registry does not state the witness belt's positive speed")
    spec = SfyBuildSpec(
        groups=(),
        external_inputs={WITNESS_ITEM: WITNESS_RATE},
        outputs={WITNESS_ITEM: WITNESS_RATE},
        belt_item_id="conveyor-belt-mk1",
        belt_items_per_second=Fraction(str(speed)) / 60,
        label="60 iron ore/min transport witness; no production machines",
    )
    placement = SfyPlacement(
        designer=mark,
        machines=(),
        belts=built.belts,
        attachments=built.attachments,
        lifts=built.lifts,
        links=built.links,
        foundations=foundations(mark, registry, ids),
        short_desc=WITNESS_NAME,
        description=(
            "Checkpoint 3 standalone transport witness, not a complete corpus factory. "
            "Feed 60 iron ore/min at the -Y wall and remove it at the raised +Y exit. "
            "Tests the authored attachment turn and both lift connections; no power needed."
        ),
    )
    _validate_witness(placement, spec)
    header = pipeline.newest_fixture_header()
    blueprint = emit(
        placement,
        registry,
        pipeline.template_library(),
        pipeline.lab_map(),
        build_version=header.build_version,
        version_data=header.version_data,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    sbp = out_dir / f"{WITNESS_NAME}.sbp"
    cfg = out_dir / f"{WITNESS_NAME}.sbpcfg"
    write_sbp_file(sbp, blueprint)
    cfg.write_bytes(write_sbpcfg(BlueprintRecord.new(placement.description)))
    again, record = read_pair(sbp, cfg)
    if (
        again != blueprint
        or write_sbp(again) != sbp.read_bytes()
        or write_sbpcfg(record) != cfg.read_bytes()
    ):
        raise CheckFailed("the written transport pair does not round-trip byte-for-byte")
    decoded = decode(again, registry)
    if decoded != placement:
        raise CheckFailed("the transport blueprint does not decode to its physical placement")
    report = _validate_witness(_redressed(decoded, placement), spec)
    return TransportWitness(placement, spec, sbp, cfg, report)


def _validate_witness(placement: SfyPlacement, spec: SfyBuildSpec) -> Report:
    report = validate(placement, spec, pipeline.registry(), library=pipeline.template_library())
    if not report.ok:
        raise CheckFailed("transport witness: " + "; ".join(f.message for f in report.errors))
    if set(report.skipped) != WITNESS_SKIPS:
        raise CheckFailed(f"unexpected transport witness skipped checks: {report.skipped}")
    return report


def instructions(factories: tuple[Written, ...], witness: TransportWitness) -> str:
    out = [
        "# Checkpoint 3: grid factories and a separate transport witness",
        "",
        "Generated by `scripts/sfy_checkpoint3.py`. The two factories use the committed",
        "FactorioLab flows and the production `sections` planner without overrides.",
        "The separate transport witness exercises the codec's belt and lift geometry.",
        "",
        "The third pair is an explicitly separate conveyor component, not a complete",
        "corpus factory or a claim that factory routing needed a lift. It exercises",
        "the same realiser, registry LiftGeometry, emitter and connection checks.",
        "This separation was approved rather than forcing unnecessary geometry into",
        "the smallest converted factory.",
        "",
        "## Installation and common checks",
        "",
        "1. Copy each `.sbp` together with its same-name `.sbpcfg` into",
        "   `%LOCALAPPDATA%\\FactoryGame\\Saved\\SaveGames\\blueprints\\<session name>\\`.",
        "2. Open the pair in a Blueprint Designer Mk.1 and paste on a flat site.",
        "3. Connect the factories' listed bottom inputs and top outputs, and power them.",
        "   The standalone conveyor witness uses -Y/+Y wall ends and needs no power.",
        "4. Let buffers settle, keep outputs unblocked, then measure total output",
        "   across all exit belts for at least one minute.",
        "",
        "Factory pairs passed byte/physical round trips, full validation, boundary",
        "contracts and known header cost. Exactly two checks remain partial:",
        "`geom.hard_clearance` and `belt.capsule`; these are not game-clearance proof.",
        "In-game loading, snapping and throughput still require this paste test.",
        "",
    ]
    for factory in factories:
        out.extend(_pair_section(factory, pipeline.registry()))
    incoming, outgoing = _open_ends(witness.placement)
    lift = witness.placement.lifts[0]
    out.extend(
        [
            "## Standalone lift-plus-attachment transport witness",
            "",
            f"- Files: `{witness.sbp.name}` and `{witness.cfg.name}`",
            "- Mk.1 designer; no production machines, no power, no recipe conversion.",
            f"- Feed **60 iron ore/min** at {incoming[0][0].start} cm on the -Y wall.",
            f"- Remove **60 iron ore/min** at {outgoing[0][0].end} cm on the +Y wall.",
            f"- Lift height: **{lift.height_cm:g} cm**; "
            f"local top yaw: **{lift.top_yaw_deg:g} degrees**.",
            f"- Authored objects: {len(witness.placement.lifts)} lift, "
            f"{len(witness.placement.attachments)} attachment turn, "
            f"{len(witness.placement.belts)} belts.",
            "- Watch items enter and leave both lift connectors, traverse the attachment",
            "  corner, and reach the raised exit without stalling. Do not rebuild the",
            "  suspect connection before recording whether the original paste worked.",
            "- The written pair round-trips to the physical component and passes all",
            "  applicable checks against an explicit input=output transport contract.",
            "  The same two clearance rules remain partial; `power.wires` is also",
            "  skipped because this component has no wires or powered machines.",
            "",
            "## What to report",
            "",
            "For each pair: whether it loads and pastes; the exact refusal text; total",
            "steady output rate; and screenshots of any misaligned/stalled connection.",
            "For factories, also report machine clocks. For the witness, identify whether",
            "the inlet, attachment corner, lift bottom, lift top or exit is the first stall.",
            "",
            "These files do not declare M3 complete. The full corpus budget gate and",
            "remaining packing/routing acceptance failures are tracked separately.",
            "",
        ]
    )
    return "\n".join(out)


def build_checkpoint(
    out_dir: Path, *, time_budget_s: float = 15.0
) -> tuple[tuple[Written, ...], TransportWitness]:
    factories = tuple(
        build_case(case, out_dir, time_budget_s=time_budget_s, strategy="sections")
        for case in CASES
    )
    witness = build_witness(out_dir)
    (out_dir / README_NAME).write_text(instructions(factories, witness), encoding="utf-8")
    return factories, witness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--time-budget", type=float, default=15.0, dest="time_budget_s")
    args = parser.parse_args()
    factories, witness = build_checkpoint(args.out, time_budget_s=args.time_budget_s)
    for factory in factories:
        print(f"FACTORY: {factory.sbp} + {factory.cfg}; checks passed")
    print(f"TRANSPORT WITNESS: {witness.sbp} + {witness.cfg}; checks passed")
    print(f"Instructions: {args.out / README_NAME}")
    print("Checkpoint files verified; in-game paste test and M3 corpus acceptance remain open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
