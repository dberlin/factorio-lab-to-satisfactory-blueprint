"""A FactorioLab Satisfactory URL, end to end, into two files the game loads.

:func:`build` is the Satisfactory counterpart of :func:`flab2bp.pipeline.build`
and runs the same stages in the same order: parse the URL, take FactorioLab's
own solved flow, turn it into a :class:`~flab2bp.sfy.spec.SfyBuildSpec`, lay
that out, judge it, and write it into a blueprint.  :func:`write` puts the two
halves the game wants -- ``<name>.sbp`` and ``<name>.sbpcfg`` -- side by side in
a directory.

The connected-section planner runs under one monotonic deadline. Its returned
placement must pass physical validation before measurement and emission.
Reference layout implementations are not production alternatives or fallbacks.

FactorioLab's flow is not optional here
---------------------------------------
The DSP path may re-derive a recipe selection when no flow is supplied; this one
may not (R1).  ``build`` refuses without ``flow``, ``flow_text`` or
``fetch_flow``, and nothing below this module solves rates of its own:
:func:`flab2bp.sfy.rates.spec_from_flow` reads the export and refuses where the
export and the rate model disagree.  So every build that exists at all is
FactorioLab's own selection, and :attr:`SfyBuild.flow_pinned` is always true.

Nothing here imports :mod:`flab2bp.dsp`
---------------------------------------
Spec §6 keeps flow capture and CSV parsing shared between the two games, and
``tests/sfy/test_rates.py`` holds the whole ``flab2bp.sfy`` package -- this
module included -- to loading no DSP module at all.  That is why
:mod:`flab2bp.lab.flow` reaches DSP's catalog lazily, from inside its own
``df-`` branch, rather than at module scope.

What is read once
-----------------
:func:`registry`, :func:`template_library`, :func:`lab_map` and
:func:`newest_fixture_header` are ``functools.cache``\\ d: the registry is a
1.2 MB parse, the template library reads and decompresses every blueprint in
the corpus, and a CLI that paid for those twice would pay for them on every
build in a batch.  They are the game's own tables, so one copy per process is
one copy of the truth.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Final, cast

from flab2bp.lab.capture import UrlValidator, capture_flow_csv
from flab2bp.lab.data import load_vendored
from flab2bp.lab.flow import FlowSelection, flow_from_text, load_flow
from flab2bp.lab.games import Game
from flab2bp.lab.schema import Dataset
from flab2bp.lab.url import LabRequest, parse_url
from flab2bp.layout.base import LayoutAttemptFailure, NoValidLayout, PlacementStats, SpecInfeasible
from flab2bp.layout.budget import expired
from flab2bp.sfy.archive import Reader
from flab2bp.sfy.codec import Blueprint, write_sbp_file, write_sbpcfg
from flab2bp.sfy.header import BlueprintHeader, BlueprintRecord, read_header
from flab2bp.sfy.labmap import LabMap, load_lab_map
from flab2bp.sfy.layout.emit import emit
from flab2bp.sfy.layout.measure import Measure, measure
from flab2bp.sfy.layout.model import SfyPlacement
from flab2bp.sfy.layout.protocol import SfyLayoutStrategy
from flab2bp.sfy.layout.validate import Report, validate
from flab2bp.sfy.rates import RatesRefusal, spec_from_flow
from flab2bp.sfy.registry import Registry, load_registry
from flab2bp.sfy.spec import DESIGNER_CLASSES, Designer, SfyBuildSpec
from flab2bp.sfy.spec import designer as designer_for_mark
from flab2bp.sfy.strategy_names import (
    SFY_STRATEGY_CHOICES,
    SfyStrategyName,
)
from flab2bp.sfy.templates import TemplateLibrary

__all__ = [
    "CORPUS_DIR",
    "DEFAULT_DESIGNER_MARK",
    "DESIGNER_MARKS",
    "STRATEGIES",
    "SfyBuild",
    "build",
    "lab_map",
    "newest_fixture_header",
    "registry",
    "template_library",
    "write",
]

#: The blueprint corpus, which is where a template to clone and a build version
#: to claim both come from.  The same directory
#: :func:`flab2bp.sfy.layout.validate.validate` falls back to, and the one
#: ``scripts/sfy_checkpoint1.py`` reads: these are files the GAME wrote, used
#: as a source of format, never of geometry or of a bound.
CORPUS_DIR: Final = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "sfy"

#: The Blueprint Designer marks a build may be laid out in, taken from the
#: designer buildables :mod:`flab2bp.sfy.spec` maps rather than restated here:
#: a CLI that offered a mark the spec cannot size would be offering a choice
#: nothing can honour.
DESIGNER_MARKS: Final = tuple(sorted(DESIGNER_CLASSES))

#: The designer a build is laid out in when the caller names none.  The
#: smallest one, because it is the one every save has: a default of mk3 would
#: build something most players cannot paste.  Every caller that offers the
#: choice -- the CLI's ``--designer``, the web ``Options`` -- defaults to this
#: rather than restating it.
DEFAULT_DESIGNER_MARK: Final = "mk1"

#: Loaded on first use, never while importing a corpus, CLI or audit vocabulary.
STRATEGIES: dict[str, SfyLayoutStrategy] = {}


def _strategy(name: str) -> SfyLayoutStrategy:
    if name not in STRATEGIES:
        if name != "sections":
            raise ValueError(f"unknown Satisfactory strategy {name!r}")
        from flab2bp.sfy.sections.compose import SectionLayout

        STRATEGIES[name] = SectionLayout()
    return STRATEGIES[name]


#: Characters a blueprint file name may carry.  The game reads its blueprints
#: out of a directory by file name, and a name is free text: a slug keeps a
#: player's ``--name "plates for the hub"`` a single, openable file.
_UNSAFE_IN_A_FILE_NAME: Final = re.compile(r"[^A-Za-z0-9._-]+")


@cache
def registry() -> Registry:
    """``data/registry.json``, parsed once: it is 1.2 MB of game tables."""
    return load_registry()


@cache
def lab_map() -> LabMap:
    """FactorioLab ids to game classes, read once."""
    return load_lab_map()


@cache
def template_library() -> TemplateLibrary:
    """One actor of each class to clone, out of the corpus, read once.

    :meth:`TemplateLibrary.from_fixtures` decompresses and decodes every
    blueprint in :data:`CORPUS_DIR` -- 49 files today -- so this costs seconds
    the first time and nothing afterwards.
    """
    return TemplateLibrary.from_fixtures(_corpus_paths())


@cache
def newest_fixture_header() -> BlueprintHeader:
    """The newest corpus blueprint's header, read once.

    Its ``build_version`` and engine-version block go into what we write, the
    way ``scripts/sfy_checkpoint1.py`` takes them: they tell the game the file
    comes from the family it has installed.
    """
    return max(
        (read_header(Reader(path.read_bytes())) for path in _corpus_paths()),
        key=lambda header: (header.save_version, header.build_version),
    )


def _corpus_paths() -> list[Path]:
    paths = sorted(CORPUS_DIR.glob("*.sbp")) if CORPUS_DIR.is_dir() else []
    if not paths:
        raise ValueError(
            f"there is no blueprint corpus at {CORPUS_DIR}, so no template to clone "
            "and no build version to claim; a Satisfactory blueprint cannot be "
            "authored without one"
        )
    return paths


@dataclass(frozen=True, slots=True)
class SfyBuild:
    """One finished Satisfactory build, and everything a report wants to say.

    ``blueprint`` and ``record`` are ``None`` on exactly one path: the placement
    was laid out and judged, and then could not be WRITTEN -- a class the corpus
    has no template of, a link to a port an object does not have.  That is a
    refusal rather than a crash, so the reason is in ``refused`` and there is
    no file to hand over.
    """

    spec: SfyBuildSpec
    placement: SfyPlacement
    report: Report
    strategy: str
    designer: Designer
    measure: Measure
    blueprint: Blueprint | None
    record: BlueprintRecord | None
    refused: tuple[LayoutAttemptFailure, ...] = ()
    #: Always true on this path: R1 makes FactorioLab's own flow a condition of
    #: building at all.  It is a field rather than a constant because a report
    #: that simply asserted the pin would be stating a policy, and this states
    #: the fact about THIS build.
    flow_pinned: bool = True


def build(
    url: str,
    *,
    strategy: SfyStrategyName = "sections",
    designer: str = DEFAULT_DESIGNER_MARK,
    time_budget_s: float = 15.0,
    flow: Path | None = None,
    flow_text: str | None = None,
    fetch_flow: bool = False,
    fetch_timeout_s: float = 90.0,
    browser: str | None = None,
    #: Injected by a caller that fetches on someone else's behalf, exactly as
    #: :func:`flab2bp.pipeline.build` takes it: the web layer decides which
    #: pages a server may be driven to, and this module never decides for it.
    fetch_url_validator: UrlValidator | None = None,
    name: str = "",
    dataset: Dataset | None = None,
) -> SfyBuild:
    """Turn a FactorioLab Satisfactory URL into a pasteable blueprint.

    :param strategy: ``sections``, the connected production-section planner.
    :param designer: which Blueprint Designer mark to build inside, one of
        :data:`DESIGNER_MARKS`.  Its floor and height come from the registry.
    :param flow: a FactorioLab CSV export; :param flow_text: the same export as
        text; :param fetch_flow: drive a headless browser to ``url`` and take
        the export ourselves.  Exactly one is required (R1).

    :raises ValueError: the URL is not a Satisfactory one, no flow was supplied,
        or two were.
    :raises SpecInfeasible: the flow and the rate model cannot both be right, or
        the build needs something M2 does not carry (a fluid).
    :raises NoValidLayout: the planner refused, exceeded its deadline, or returned
        a placement that failed validation. Diagnostics preserve the cause.
    """
    if strategy not in SFY_STRATEGY_CHOICES:
        raise ValueError("Satisfactory strategy must be one of " + ", ".join(SFY_STRATEGY_CHOICES))
    if not math.isfinite(time_budget_s) or time_budget_s <= 0:
        raise ValueError("time_budget_s must be a positive finite number")
    request = _request(url)
    selection = _flow(
        url,
        flow=flow,
        flow_text=flow_text,
        fetch_flow=fetch_flow,
        fetch_timeout_s=fetch_timeout_s,
        browser=browser,
        fetch_url_validator=fetch_url_validator,
    )

    data = dataset if dataset is not None else load_vendored(Game.SFY)
    # `RatesRefusal` knows nothing about `NoValidLayout` and must not -- that
    # would give `rates` a dependency on `layout` -- so this boundary, the one
    # place that imports both, is where it becomes the same REFUSED shape a
    # failed layout gets rather than an unclassified crash.
    try:
        spec = spec_from_flow(
            data, request, selection, registry(), lab_map(), label=_label(request)
        )
    except RatesRefusal as exc:
        raise SpecInfeasible(exc.cause, item=_label(request)) from exc

    mark = designer_for_mark(designer, registry())
    # Load shared data before starting the single layout deadline.
    game_registry, mapping, library = registry(), lab_map(), template_library()
    planner = _strategy(strategy)
    deadline = time.monotonic() + time_budget_s
    try:
        placement = planner.lay_out(
            spec,
            mark,
            time_budget_s=time_budget_s,
            absolute_deadline=deadline,
            registry=game_registry,
            lab_map=mapping,
        )
        if expired(deadline, time.monotonic):
            raise NoValidLayout("layout exceeded the budget")
        report = validate(placement, spec, game_registry, library=library)
        if not report.ok:
            raise NoValidLayout(
                "validation failed: "
                + "; ".join(f"{finding.check}: {finding.message}" for finding in report.errors)
            )
        measured = measure(placement, game_registry)
        if expired(deadline, time.monotonic):
            raise NoValidLayout("layout exceeded the budget")
    except NoValidLayout as exc:
        failure = LayoutAttemptFailure(
            candidate=spec.label or "this build",
            strategy=strategy,
            reason=exc.reason,
            projection_failures=exc.projection_failures,
            stats=cast(PlacementStats, dict(exc.stats)),
            children=exc.attempt_failures
            or tuple(
                LayoutAttemptFailure(spec.label or "this build", strategy, detail)
                for detail in exc.attempt_reasons
            ),
        )
        raise NoValidLayout(
            exc.reason,
            spec_label=spec.label,
            budget_s=time_budget_s,
            attempt_reasons=(str(failure),),
            attempt_failures=(failure,),
        ) from exc
    placement = replace(placement, short_desc=name or _slug(spec, mark))
    placement = replace(
        placement,
        description=_description(spec, placement, data, game_registry),
    )

    header = newest_fixture_header()
    try:
        blueprint: Blueprint | None = emit(
            placement,
            registry(),
            template_library(),
            lab_map(),
            build_version=header.build_version,
            version_data=header.version_data,
        )
    except ValueError as exc:
        # Mirrors `flab2bp.pipeline`'s encoding arm: a placement that cannot be
        # written is a refusal with a reason, not a traceback, and the caller
        # gets the report it earned plus an honest "there is no file".
        return SfyBuild(
            spec=spec,
            placement=placement,
            report=report,
            strategy=strategy,
            designer=mark,
            measure=measured,
            blueprint=None,
            record=None,
            refused=(
                LayoutAttemptFailure(
                    candidate=spec.label or "this build",
                    strategy=strategy,
                    reason=f"blueprint encoding failed: {exc}",
                ),
            ),
        )

    return SfyBuild(
        spec=spec,
        placement=placement,
        report=report,
        strategy=strategy,
        designer=mark,
        measure=measured,
        blueprint=blueprint,
        record=BlueprintRecord.new(placement.description),
    )


def write(build: SfyBuild, out_dir: Path) -> tuple[Path, Path]:
    """Write ``<name>.sbp`` and ``<name>.sbpcfg`` into ``out_dir``.

    The game reads a blueprint out of a save's ``blueprints/<session>`` folder
    as a PAIR of files sharing one stem, so this writes both or neither and
    returns them in that order.  ``out_dir`` is created if it does not exist.
    """
    if build.blueprint is None or build.record is None:
        raise ValueError(
            "this build produced no blueprint, so there is nothing to write: "
            + "; ".join(str(failure) for failure in build.refused)
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _file_stem(build.placement.short_desc)
    sbp, cfg = out_dir / f"{stem}.sbp", out_dir / f"{stem}.sbpcfg"
    write_sbp_file(sbp, build.blueprint)
    cfg.write_bytes(write_sbpcfg(build.record))
    return (sbp, cfg)


def _request(url: str) -> LabRequest:
    """The URL's request, refusing anything that is not a Satisfactory one.

    ``parse_url`` already refuses a dataset outside :class:`Game`; this refuses
    the OTHER game, which parses perfectly well and would otherwise be laid out
    against Satisfactory's tables.
    """
    request = parse_url(url)
    if request.game is not Game.SFY:
        raise ValueError(
            f"this is a {request.game.value!r} URL and this is the Satisfactory "
            f"pipeline; build it with flab2bp.pipeline.build instead"
        )
    return request


def _flow(
    url: str,
    *,
    flow: Path | None,
    flow_text: str | None,
    fetch_flow: bool,
    fetch_timeout_s: float,
    browser: str | None,
    fetch_url_validator: UrlValidator | None,
) -> FlowSelection:
    """FactorioLab's own solved flow for ``url``, by whichever door was opened.

    R1: there is no fourth door.  Nothing in this pipeline re-solves a flow, so
    a build without one is refused here rather than built from a selection
    FactorioLab never made.
    """
    # Not a precedence rule. Each of these is a different recipe selection --
    # a file, a paste, and whatever FactorioLab solves when a browser is driven
    # to this URL right now -- and picking one silently would pin the build to a
    # selection the caller did not choose, which is the exact failure R1 exists
    # to remove. `flab2bp.pipeline` refuses the file/text pair for that reason;
    # `--fetch-flow` alongside either is the same mistake and is refused too.
    supplied = [
        name
        for name, given in (
            ("a flow file", flow is not None),
            ("flow text", flow_text is not None),
            ("--fetch-flow", fetch_flow),
        )
        if given
    ]
    if len(supplied) > 1:
        raise ValueError(
            f"{' and '.join(supplied)} were all supplied. Pass one: they are "
            "different recipe selections and there is no right guess."
        )
    if flow is not None:
        return load_flow(flow, url=url)
    if flow_text is not None:
        return flow_from_text(flow_text, url=url)
    if fetch_flow:
        return flow_from_text(
            capture_flow_csv(
                url,
                timeout_s=fetch_timeout_s,
                browser=browser,
                url_validator=fetch_url_validator,
            ),
            url=url,
        )
    raise ValueError(
        "a Satisfactory build needs FactorioLab's own solved flow, and nothing "
        "here re-derives one: pass --flow with the CSV the list view's "
        "'download as CSV' button writes, or --fetch-flow to have it captured "
        "from this URL"
    )


def _description(
    spec: SfyBuildSpec,
    placement: SfyPlacement,
    data: Dataset,
    game_registry: Registry,
) -> str:
    """The in-game field sheet: net boundary rates and the settings that produce them."""
    exports = dict(spec.outputs)
    for item, rate in spec.surplus_outputs.items():
        exports[item] = exports.get(item, Fraction(0)) + rate
    rows = [
        placement.short_desc,
        f"Blueprint Designer Mk.{placement.designer.mark[2:]}; "
        f"{spec.machine_count} production machines.",
    ]
    boundary_labels = (
        ("Outputs (bottom)", "Inputs (bottom)")
        if placement.stack_lanes
        else ("Outputs (+Y)", "Inputs (-Y)")
    )
    for label, rates in zip(boundary_labels, (exports, spec.external_inputs), strict=True):
        summary = "; ".join(
            f"{data.item(item).name}: {rate * 60} "
            f"{'m³/min' if item in spec.fluid_items else 'items/min'}"
            for item, rate in sorted(rates.items())
        )
        rows.append(f"{label}: {summary or 'none'}.")
    if placement.stack_lanes:
        rows.extend(
            (
                "",
                f"Vertical stack pitch: {placement.stack_height_cm / 100:g} m.",
                "Keep matching XY outlines and orientation. Connect corresponding floor/ceiling "
                f"holes manually across the {placement.stack_connection_gap_cm / 100:g} m gap; "
                "automatic lift joining is not promised.",
                "Inputs rise from the bottom, branch into this module and continue upward. "
                "Outputs descend from the top, collect this module's production and drain "
                "to bottom extraction. Boundary rates are local net demand/export; size "
                "supply and extraction for all stacked modules.",
            )
        )
        for lane in placement.stack_lanes:
            unit = "m³/min" if lane.kind == "pipe" else "items/min"
            rows.append(
                f"- {data.item(lane.item_id).name} trunk capacity: "
                f"{lane.capacity_per_second * 60} {unit}."
            )
            if lane.required_input_head_m > 0:
                rows.append(
                    f"  Required external input head: at least "
                    f"{lane.required_input_head_m:g} m above the bottom inlet; "
                    "external pressure is not measured by this blueprint."
                )
    rows.extend(("", "Machines and recipes:"))
    for group in spec.groups:
        machine = game_registry.buildables[group.machine_class].display_name
        recipe = game_registry.recipes[group.recipe_class].display_name
        if group.clock == group.last_clock:
            clocks = f"{group.count} at {group.clock * 100}%"
        elif group.count == 1:
            clocks = f"1 at {group.last_clock * 100}%"
        else:
            clocks = f"{group.count - 1} at {group.clock * 100}%; 1 at {group.last_clock * 100}%"
        rows.append(f"- {machine} / {recipe}: {clocks}.")
    auxiliary_power = sum(
        game_registry.buildables[obj.class_name].power_mw for obj in placement.pipe_attachments
    )
    rows.extend(
        (
            "",
            f"Estimated active production power: {spec.power_mw:.2f} MW.",
            f"Auxiliary pump power: {auxiliary_power:.2f} MW.",
            f"Consumables: {spec.power_shards} Power Shards; "
            f"{sum(group.count * group.somersloops for group in spec.groups)} Somersloops.",
            "Connect external power and every listed input. Keep outputs clear; "
            "allow manifolds to fill before measuring throughput.",
            "Fluid rates are m³/min; solid rates are items/min; fractions are exact.",
            "Generated by flab2bp. Not verified in-game.",
        )
    )
    return "\n".join(rows)


def _label(request: LabRequest) -> str:
    """What this build is for, in the URL's own words."""
    asked = " + ".join(f"{o.target_id}*{o.value}" for o in request.objectives)
    return asked or "this build"


def _slug(spec: SfyBuildSpec, designer: Designer) -> str:
    """The default name: what the build makes, and what it fits inside.

    The objective items rather than the label, because a label carries the rate
    (``iron-plate*60``) and a rate in a file name ages badly next to the same
    build at a different one.
    """
    items = "-".join(sorted(spec.outputs)) or "build"
    return f"{items}-{designer.mark}"


def _file_stem(name: str) -> str:
    """``name`` as something a directory of blueprints can hold."""
    stem = _UNSAFE_IN_A_FILE_NAME.sub("-", name).strip("-.")
    return stem or "blueprint"
