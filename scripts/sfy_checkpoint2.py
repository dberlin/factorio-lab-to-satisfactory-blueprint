"""Build checkpoint 2: the blueprints that say whether a chain runs at its rate.

Checkpoint 1 asked one question -- does a file this project writes load at all --
and a single Constructor answered it. This one asks the question M2 exists for:
**does a whole chain, laid out from FactorioLab's own solved flow, actually run
at that flow's rate when it is pasted?**

Each pair below is one ``flab2bp.sfy.pipeline.build`` of a committed fixture
flow, written out as the ``.sbp`` and ``.sbpcfg`` the game reads side by side.
Nothing here re-solves anything: the flow files under
``tests/fixtures/sfy_flows/`` are FactorioLab's own exports, byte for byte, and
they go through the pipeline unmodified.

The script then holds every file it wrote to what it meant to write -- the
blueprint re-reads identical, the config round-trips, the placement comes back
out of the file, the validator is clean on the placement that came back, every
open belt end is on the wall it claims, and the cost the header advertises is
made of items the registry knows -- and any one of those failing is a non-zero
exit, the way ``scripts/sfy_checkpoint1.py`` gates itself.

Finally it prints the paste instructions and writes them to
``out/sfy/checkpoint2-README.md`` beside the pairs: which designer, which wall
each input belt enters by and where along it, where the output leaves, the rate
to expect, the power draw, and what to look at. Nothing under ``out/`` is
committed.

To run the test, on the Windows machine:

  1. Copy every ``.sbp``/``.sbpcfg`` pair into
     %LOCALAPPDATA%\\FactoryGame\\Saved\\SaveGames\\blueprints\\<session name>\\
  2. Walk into a Blueprint Designer of the mark the instructions name, open it,
     and load the blueprint.
  3. Paste it somewhere powered, belt the named inputs in at the ``-Y`` wall,
     and belt the output away at the ``+Y`` wall.
  4. Watch the output belt for a few minutes and report the rate.

Then report back per pair: (a) does it load, (b) does anything refuse to build,
(c) what rate comes out of the last merger, (d) what clock each machine shows.
That report closes M2.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

from flab2bp.layout.base import NoValidLayout
from flab2bp.sfy import pipeline
from flab2bp.sfy.codec import read_sbp_file, read_sbpcfg, write_sbp, write_sbpcfg
from flab2bp.sfy.layout.emit import decode
from flab2bp.sfy.layout.model import BeltRun, SfyPlacement
from flab2bp.sfy.layout.validate import Report, validate
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import SfyBuildSpec, foundation_cm
from flab2bp.sfy.strategy_names import SfyStrategyName

REPO = Path(__file__).resolve().parent.parent
FLOWS = REPO / "tests" / "fixtures" / "sfy_flows"
OUT_DIR = REPO / "out" / "sfy"
README_NAME = "checkpoint2-README.md"

#: The two checks that name a ``partial`` hologram rule and therefore live in
#: ``Report.skipped`` permanently: ``buildable.clearance`` is partial, so
#: neither the box-against-box decision nor a belt's capsule against one can be
#: judged here.  A report that skips anything ELSE is a build nobody finished
#: looking at, so the gate holds the skipped set to exactly these.
PARTIAL_RULE_CHECKS = ("belt.capsule", "geom.hard_clearance")

#: How far an open belt end may sit from the wall it claims, the same
#: centimetre ``validate.flow.boundary`` allows.
WALL_SLACK_CM = 1.0

SECONDS_PER_MINUTE = 60


@dataclass(frozen=True, slots=True)
class Case:
    """One blueprint to write, and the flow it is built from."""

    name: str
    flow: str
    url: str
    #: Which designer marks to try, in order.  A case with more than one takes
    #: the FIRST that builds: a blueprint a player can paste into the designer
    #: they already own is worth more than one that needs the biggest, and which
    #: mark that is is a measurement rather than a thing to write down here.
    designer: str
    #: What pasting this one is supposed to settle.
    question: str


#: ``iron-plate*60`` in three settings, each written into the SMALLEST Blueprint
#: Designer that holds it.  Since Task 8d that is the Mk.1 for all three: their
#: two groups pair machine for machine, so the build is one row of smelters
#: facing one row of constructors with three straight belts between them.
#: ``reinforced-iron-plate*10`` refuses every mark on depth even with Task 8c's
#: row splitting in -- see :data:`REFUSED_CASES` -- so the somersloop flow
#: stands in as a second pair the paste test can judge just as sharply: one
#: sloop per Constructor doubles what each machine makes, so the same 60 plates
#: a minute come out of fewer machines.
CASES = (
    Case(
        name="checkpoint2-iron-plate-60",
        flow="iron-plate-60.csv",
        url="https://factoriolab.github.io/sfy/list?o=iron-plate*60&v=11",
        designer="mk1 mk2 mk3",
        question="does a plain two-row chain run at the flow's rate",
    ),
    Case(
        name="checkpoint2-iron-plate-60-somersloop",
        flow="iron-plate-60-somersloop.csv",
        url=(
            "https://factoriolab.github.io/sfy/list"
            "?o=iron-plate*60&e=1*somersloop&m=constructor-id*0&v=11"
        ),
        designer="mk1 mk2 mk3",
        question="does a somersloop'd chain make the same rate out of fewer machines",
    ),
    Case(
        name="checkpoint2-iron-plate-60-overclock-250",
        flow="iron-plate-60-overclock-250.csv",
        url="https://factoriolab.github.io/sfy/list?o=iron-plate*60&moc=250&v=11",
        designer="mk1 mk2 mk3",
        question="does the game keep a pasted machine's 250 % potential",
    ),
)

#: The flow the brief named as the second pair, and every designer mark it was
#: tried in.  It is here rather than in :data:`CASES` because it produces no
#: file: the refusal itself is the finding, and it goes in the instructions.
REFUSED_CASES = (
    Case(
        name="reinforced-iron-plate-10",
        flow="reinforced-iron-plate-10.csv",
        url="https://factoriolab.github.io/sfy/list?o=reinforced-iron-plate*10&v=11",
        designer="mk1 mk2 mk3",
        question="does a five-row chain fit any designer",
    ),
)


class CheckFailed(SystemExit):
    """A self-check the written files did not pass; the script exits non-zero."""

    def __init__(self, message: str) -> None:
        super().__init__(f"CHECK FAILED: {message}")


@dataclass(frozen=True, slots=True)
class Written:
    """One case, built and written, with the files it produced."""

    case: Case
    build: pipeline.SfyBuild
    sbp: Path
    cfg: Path
    #: The mark it was actually written for: the first of the case's own that
    #: held it.
    mark: str
    #: The report the round-tripped placement earned, which is the one the gate
    #: judges: the build's own report is about the placement in memory.
    report: Report


def build_case(
    case: Case,
    out_dir: Path,
    *,
    time_budget_s: float,
    strategy: SfyStrategyName = "manifold-rows",
) -> Written:
    """Build one case in the smallest of its marks that holds it, and write it.

    A refusal in a smaller mark is a RESULT, not a failure: it says the build
    wants more floor than that designer has, which is the honest reason to reach
    for a bigger one.  Only a case that fits none of its marks is a failure.
    """
    refusals: list[str] = []
    for mark in case.designer.split():
        try:
            build = pipeline.build(
                case.url,
                designer=mark,
                strategy=strategy,
                flow=FLOWS / case.flow,
                time_budget_s=time_budget_s,
                name=case.name,
            )
        except NoValidLayout as refused:
            refusals.append(f"{mark}: {refused.reason}")
            continue
        if build.blueprint is None:
            raise CheckFailed(
                f"{case.name} produced no blueprint in {mark}: "
                + "; ".join(str(failure) for failure in build.refused)
            )
        sbp, cfg = pipeline.write(build, out_dir)
        return Written(case, build, sbp, cfg, mark, _check(case, build, sbp, cfg))
    raise CheckFailed(f"{case.name} fits none of {case.designer}: " + "; ".join(refusals))


def _check(case: Case, build: pipeline.SfyBuild, sbp: Path, cfg: Path) -> Report:
    """Every gate, on the files that were actually written.  Each one is fatal."""
    registry = pipeline.registry()
    again = read_sbp_file(sbp)
    if again != build.blueprint:
        raise CheckFailed(f"{sbp.name} does not re-read as the blueprint that was assembled")
    if write_sbp(again) != sbp.read_bytes():
        raise CheckFailed(f"{sbp.name} does not decode and re-encode to itself")
    raw = cfg.read_bytes()
    if write_sbpcfg(read_sbpcfg(raw)) != raw:
        raise CheckFailed(f"{cfg.name} does not decode and re-encode to itself")

    placement = decode(again, registry)
    if placement != build.placement:
        raise CheckFailed(f"{sbp.name} does not decode to the placement that was laid out")

    report = validate(
        _redressed(placement, build.placement),
        build.spec,
        registry,
        library=pipeline.template_library(),
    )
    if not report.ok:
        first = report.errors[0]
        raise CheckFailed(
            f"{case.name}: the placement out of {sbp.name} is not clean -- "
            f"{first.check}: {first.message}"
        )
    if tuple(sorted(report.skipped)) != PARTIAL_RULE_CHECKS:
        raise CheckFailed(
            f"{case.name}: the validator skipped {sorted(report.skipped)} and the only "
            f"checks that may ever be skipped are {list(PARTIAL_RULE_CHECKS)}"
        )

    _check_boundaries(case, build.placement)
    _check_cost(case, build, registry)
    return report


def _redressed(placement: SfyPlacement, built: SfyPlacement) -> SfyPlacement:
    """``placement`` wearing the contract the FILE has no property for.

    A belt in a blueprint carries whatever items happen to be sitting on it, not
    a promise about what it will carry, and nothing in the file marks an end as
    deliberately open.  So the item, the rate and the two boundary flags are
    taken back off the build's own belts -- matched by id, which is what the
    actor's name in the file carries -- before the validator is asked whether the
    geometry that came out of the file is clean.  The geometry itself is
    untouched: ``decode(emit(placement)) == placement`` was checked a line
    earlier, and this restores only the fields that comparison deliberately
    leaves out.
    """
    contracts = {run.id: run for run in built.belts}
    belts = []
    for run in placement.belts:
        source = contracts.get(run.id)
        if source is None:
            raise CheckFailed(f"belt {run.id} came out of the file and was never laid out")
        belts.append(
            replace(
                run,
                item_id=source.item_id,
                items_per_second=source.items_per_second,
                boundary_start=source.boundary_start,
                boundary_end=source.boundary_end,
            )
        )
    return replace(placement, belts=tuple(belts), description=built.description)


def _check_boundaries(case: Case, placement: SfyPlacement) -> None:
    """Every end flagged as a boundary stands on the wall it claims.

    The build's external inputs arrive at the ``-Y`` wall and its outputs leave
    at the ``+Y`` one, and a flagged end that is not there is a belt that goes
    nowhere in silence.  The validator judges this too; it is repeated here
    because the instructions below tell a player where to meet each belt, and a
    figure nobody checked is not an instruction.
    """
    half = placement.designer.half_cm
    for run in placement.belts:
        for where, wall, flagged in (
            (run.start, -half, run.boundary_start),
            (run.end, half, run.boundary_end),
        ):
            if flagged and abs(where[1] - wall) > WALL_SLACK_CM:
                raise CheckFailed(
                    f"{case.name}: belt {run.id} is a boundary belt for {run.item_id!r} and "
                    f"its open end is at y = {where[1]:.1f}, not on the y = {wall:.0f} wall"
                )


def _check_cost(case: Case, build: pipeline.SfyBuild, registry: Registry) -> None:
    """The header's cost is made of items the registry can name.

    A blueprint's header advertises what pasting it will take out of the
    player's inventory.  An item the registry has no asset path for is one the
    game will not resolve either, which is a file the build menu cannot price.
    """
    assert build.blueprint is not None
    unknown = [
        entry.item.name
        for entry in build.blueprint.header.cost
        if entry.item.name not in registry.item_paths
    ]
    if unknown:
        raise CheckFailed(
            f"{case.name}: the header costs {unknown}, which the registry has not got"
        )


def refusal(case: Case, *, time_budget_s: float) -> dict[str, str]:
    """Why a case refuses, per designer mark.  A refusal is a finding, not a crash.

    A :data:`REFUSED_CASES` entry that BUILDS fails the script, the way every
    other self-check here does.  The instructions this script writes tell a
    player that this flow fits no designer, and a flow that has started fitting
    makes that a false statement -- and one nobody would notice, because a build
    where a refusal was expected looks like nothing happening at all.  The entry
    belongs in :data:`CASES` at that point, where it earns a blueprint and a
    paste test.
    """
    out: dict[str, str] = {}
    for mark in case.designer.split():
        try:
            pipeline.build(
                case.url,
                designer=mark,
                strategy="manifold-rows",
                flow=FLOWS / case.flow,
                time_budget_s=time_budget_s,
            )
        except Exception as exc:  # noqa: BLE001 -- every refusal shape is a finding here
            out[mark] = f"{type(exc).__name__}: {exc}"
        else:
            raise CheckFailed(
                f"{case.name}: the {mark} designer built it, but it is in REFUSED_CASES, "
                "whose whole content is that it refuses -- move it to CASES and give it a "
                "paste test"
            )
    return out


# --- what to tell the player -----------------------------------------------


def _per_minute(rate: Fraction) -> float:
    return float(rate) * SECONDS_PER_MINUTE


def _foundations_from_west(x_cm: float, placement: SfyPlacement, registry: Registry) -> float:
    """How many foundations east of the ``-X`` corner a point stands.

    A player pacing out a designer counts foundations, not centimetres, so the
    instructions give both.  The designer's own ``half_cm`` is the corner and
    the foundation's own footprint is the pace, both out of the registry.
    """
    return (x_cm + placement.designer.half_cm) / foundation_cm(registry)


def _open_ends(placement: SfyPlacement) -> tuple[list[tuple[BeltRun, bool]], ...]:
    """``(entries, exits)``: the belt ends a player has to meet, west to east."""
    entries = [(run, True) for run in placement.belts if run.boundary_start]
    exits = [(run, False) for run in placement.belts if run.boundary_end]
    entries.sort(key=lambda row: row[0].start[0])
    exits.sort(key=lambda row: row[0].end[0])
    return (entries, exits)


def _wired_machines(placement: SfyPlacement) -> int:
    """How many machines a power line actually reaches."""
    on_a_wire = {side[0] for wire in placement.wires for side in (wire.link.a, wire.link.b)}
    return sum(1 for machine in placement.machines if machine.id in on_a_wire)


def _machine_lines(spec: SfyBuildSpec) -> list[str]:
    lines = []
    for group in spec.groups:
        clocks = f"{float(group.clock) * 100:g} %"
        if group.last_clock != group.clock:
            clocks += f" ({group.count - 1} of them) and {float(group.last_clock) * 100:g} %"
        sloops = f", {group.somersloops} somersloop(s) each" if group.somersloops else ""
        shards = (
            f", {group.row_power_shards} power shard(s) in the row"
            if group.row_power_shards
            else ""
        )
        lines.append(
            f"  - {group.count} x {group.machine_class} running {group.recipe_class} "
            f"at {clocks}{sloops}{shards}"
        )
    return lines


def instructions(written: list[Written], refusals: dict[str, dict[str, str]]) -> str:
    """The paste instructions, as the README beside the pairs."""
    registry = pipeline.registry()
    out: list[str] = [
        "# Checkpoint 2: does the chain run at the flow's rate?",
        "",
        "Every blueprint below was built by `scripts/sfy_checkpoint2.py` from a",
        "FactorioLab flow export committed under `tests/fixtures/sfy_flows/`, through",
        "`flab2bp.sfy.pipeline.build` with nothing changed on the way.  Each one passed",
        "the script's own gates: the file re-reads as what was assembled, the placement",
        "comes back out of it, the validator is clean on what came back, every open belt",
        "end is on the wall it claims, and the header's cost is made of items the game",
        "knows.",
        "",
        "## How to paste one",
        "",
        "1. Copy the `.sbp` and its `.sbpcfg` (both, always) into",
        "   `%LOCALAPPDATA%\\FactoryGame\\Saved\\SaveGames\\blueprints\\<session name>\\`.",
        "2. Walk into a Blueprint Designer of the mark the pair names and open it.",
        "3. Load the blueprint from the list, then paste it on a powered, flat site.",
        "4. Belt the inputs in at the `-Y` wall and the output away at the `+Y` wall,",
        "   at the positions given below.  `-Y` is the wall behind you when you stand",
        "   at the designer's entrance; `-X` is the left-hand corner of that wall.",
        "5. Give it a few minutes to fill, then read the rate off the last merger.",
        "",
    ]
    for entry in written:
        out += _pair_section(entry, registry)
    if refusals:
        out += ["## What would not build", ""]
        for name, marks in refusals.items():
            out.append(f"- **{name}**")
            for mark, why in marks.items():
                out.append(f"  - `{mark}`: {why}")
        out += [
            "",
            "That is why the second pair is the somersloop flow rather than the one the",
            "brief named: a five-row chain is deeper than any designer, and a build that",
            "cannot be laid out is not a build a paste test can judge.",
            "",
        ]
    out += [
        "## What to report",
        "",
        "For each pair: (a) does it load into the designer, (b) does anything refuse to",
        "build or sit outside the walls, (c) what rate the output belt actually carries",
        "after it has been running a few minutes, (d) what clock each machine's panel",
        "shows, and (e) the exact text of any error.",
        "",
    ]
    return "\n".join(out) + "\n"


def _pair_section(entry: Written, registry: Registry) -> list[str]:
    case, build = entry.case, entry.build
    placement, spec = build.placement, build.spec
    entries, exits = _open_ends(placement)
    wired = _wired_machines(placement)
    out = [
        f"## {case.name}",
        "",
        f"- Files: `{entry.sbp.name}` and `{entry.cfg.name}`",
        f"- Designer: **Blueprint Designer {entry.mark.upper()}** "
        f"({'x'.join(str(d) for d in placement.designer.dims)} foundations)"
        + (
            f" -- the smallest of {case.designer} that holds it"
            if len(case.designer.split()) > 1
            else ""
        ),
        f"- FactorioLab flow: `{case.flow}` -- <{case.url}>",
        f"- What it is meant to settle: {case.question}",
        "",
        "Machines:",
        *_machine_lines(spec),
        "",
        f"- Power draw: **{spec.power_mw:.1f} MW** with every machine running "
        "(a report figure: the game's clock exponent is fractional)",
        f"- Power poles: {len(placement.poles)}, power lines: {len(placement.wires)}; "
        f"{wired} of {len(placement.machines)} machines sit on a wire"
        f"{' -- every one' if wired == len(placement.machines) else ' -- NOT every one'}",
        f"- Power shards needed: {spec.power_shards}; somersloops needed: "
        f"{sum(g.somersloops * g.count for g in spec.groups)}",
        "",
        "Belts in, at the `-Y` wall (x measured east from the `-X` corner):",
    ]
    out += _belt_lines(entries, placement, registry)
    out += ["", "Belts out, at the `+Y` wall:"]
    out += _belt_lines(exits, placement, registry)
    out += [
        "",
        "Expected output: "
        + ", ".join(
            f"**{_per_minute(rate):g} {item}/min**" for item, rate in sorted(spec.outputs.items())
        ),
        "",
        "What to look for:",
        "- every belt runs, and transport does not remain backed up",
        "- every machine's panel shows the clock listed above",
        "- all output belts on the `+Y` wall together carry the expected output rate",
        "",
    ]
    if any(group.clock != 1 or group.last_clock != 1 for group in spec.groups):
        over = max(max(g.clock, g.last_clock) for g in spec.groups)
        under = min(min(g.clock, g.last_clock) for g in spec.groups)
        out += [
            "**The open question this pair carries (spec section 7).** The blueprint sets",
            "each machine's saved potential (`mCurrentPotential` and `mPendingPotential`,",
            "both `SaveGame` floats in `FGBuildableFactory.h`), but it puts no Power Shard",
            "in any slot, because a blueprint costs what it pastes and a shard is an item",
            "the player holds. So, after pasting and WITHOUT putting shards in:",
            "",
        ]
        if over > 1:
            out += [
                f"- check whether a machine the list above calls {float(over) * 100:g} % really",
                f"  shows {float(over) * 100:g} %. If it shows 100 %, the game does not keep an",
                "  overclock no shard in the slot pays for, and the `.sbpcfg` description",
                "  becomes the only carrier of the clock -- which is exactly what spec",
                "  section 7 left open.",
            ]
        if under < 1:
            out += [
                f"- check the machine the list calls {float(under) * 100:g} %. An UNDERclock",
                "  needs no shard at all, so if THAT one reads 100 % the game is dropping the",
                "  saved potential outright rather than clamping it to what is paid for.",
            ]
        out += [""]
    if any(group.somersloops for group in spec.groups):
        out += [
            "**And the same question for the somersloop.** The production boost",
            "(`mCurrentProductionBoost`, `mPendingProductionBoost`) is written from the",
            "game's own numbers -- one somersloop in a Constructor is a boost of 2.0 -- but",
            "no somersloop is placed in the slot. If the machines show no boost after",
            "pasting, the output will be half what is listed above until sloops are put in",
            "by hand; say which you saw.",
            "",
        ]
    return out


def _belt_lines(
    ends: list[tuple[BeltRun, bool]],
    placement: SfyPlacement,
    registry: Registry,
) -> list[str]:
    if not ends:
        return ["- (none)"]
    lines = []
    for run, is_start in ends:
        point = run.start if is_start else run.end
        paced = _foundations_from_west(point[0], placement, registry)
        rate = run.items_per_second
        carries = f" carrying {_per_minute(rate):g} {run.item_id}/min" if rate is not None else ""
        lines.append(
            f"- **{run.item_id}** at x = {point[0]:.0f} cm "
            f"({paced:.2f} foundations east of the `-X` corner), "
            f"z = {point[2]:.0f} cm above the designer floor, on a {run.class_name}"
            f"{carries}"
        )
    return lines


# --- running it ------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=OUT_DIR, help=f"directory to write into (default {OUT_DIR})"
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=20.0,
        dest="time_budget_s",
        help="seconds each layout may take (default 20)",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    written = []
    for case in CASES:
        entry = build_case(case, args.out, time_budget_s=args.time_budget_s)
        written.append(entry)
        print(f"wrote {entry.sbp} ({entry.sbp.stat().st_size:,} bytes)")
        print(f"wrote {entry.cfg} ({entry.cfg.stat().st_size:,} bytes)")
        print(
            f"  {len(entry.build.placement.machines)} machines, "
            f"{len(entry.build.placement.belts)} belts, "
            f"{len(entry.build.placement.poles)} poles, "
            f"{len(entry.build.placement.wires)} wires"
        )
        print(
            f"  checks: re-read identical, sbpcfg round-trips, placement decodes, "
            f"validator clean ({len(entry.report.checks_run)} checks run, "
            f"{len(entry.report.skipped)} partial), boundaries on the wall, cost known"
        )

    refusals = {
        case.name: refusal(case, time_budget_s=args.time_budget_s) for case in REFUSED_CASES
    }
    for name, marks in refusals.items():
        for mark, why in marks.items():
            print(f"refused {name} in {mark}: {why}")

    readme = args.out / README_NAME
    readme.write_text(instructions(written, refusals), encoding="utf-8")
    print(f"wrote {readme} ({readme.stat().st_size:,} bytes)")
    print(f"\n{readme.read_text(encoding='utf-8')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
