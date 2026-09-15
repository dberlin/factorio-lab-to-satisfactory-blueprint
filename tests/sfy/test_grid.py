"""The ``GridRouted`` strategy: machines packed on the grid, belts found by search.

Where the strategy refuses, the test names the cause.  A refusal is a RESULT --
the strategy hands back a placement the validator passes or says, in one of
:data:`~flab2bp.sfy.layout.refusals.REFUSALS`, why it will not -- so the test
that matters most here is the one that walks every stage, makes it raise its own
error type, and asserts that what comes out is a named cause rather than a stack
trace.

Two shapes of build, and why
----------------------------
The build that is laid out, validated and round-tripped is :func:`_chain`, a
hand-built fragment of one machine per stage, stated here the way
:mod:`tests.sfy.test_packer`'s and :mod:`tests.sfy.test_rrr`'s fragments are.
FactorioLab's own flows are in
:func:`test_a_corpus_flow_does_not_route_yet_and_says_which_nets`, and that test
is an ``xfail(strict=True)``: every committed flow runs several machines on one
recipe, so its ore net is ONE stream from the ``-Y`` wall to several machine
ports, and two defects below this strategy stop that being belted today --

1. :func:`~flab2bp.sfy.layout.packer.pack` prices a wall-fed sink at its
   distance to the wall line, so every such port lands exactly
   ``port_apron_nodes`` (four) nodes off the wall, and the trunk that reaches it
   is four nodes long where a tap needs ``2 * clear + 1`` (nine) straight nodes.
   The second and third sinks then have nowhere to start and the net strands
   ``DYNAMIC_ACCESS``;
2. given room -- the same six machines placed by hand well off the wall -- the
   trunk IS tapped, and the branch that leaves the tap turns after three grid
   steps.  An attachment turn costs ``box + lead_in`` = 301 cm and three steps
   are 300, so :func:`~flab2bp.sfy.layout.realise.realise` refuses the corner by
   one centimetre; the router's cost is one per flat step and gives it no reason
   to leave a fourth.

Both are reproduced in ``task-10-report.md`` and neither is this module's to
fix.  The test is strict, so it fails the moment either is, which is when the
corpus flows belong in it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from fractions import Fraction
from typing import Any

import pytest

from flab2bp.layout.base import NoValidLayout
from flab2bp.layout.budget import TransportRefusal
from flab2bp.sfy import pipeline
from flab2bp.sfy.layout import grid
from flab2bp.sfy.layout.corridors import CorridorError
from flab2bp.sfy.layout.emit import decode, emit
from flab2bp.sfy.layout.grid import ARRANGEMENTS, GridRouted
from flab2bp.sfy.layout.grid_nets import NetError
from flab2bp.sfy.layout.manifold import RowError
from flab2bp.sfy.layout.measure import measure
from flab2bp.sfy.layout.model import SfyPlacement
from flab2bp.sfy.layout.packer import NO_ARRANGEMENT, OVER_BUDGET, PackError, pack
from flab2bp.sfy.layout.power import PowerError
from flab2bp.sfy.layout.protocol import SfyLayoutStrategy
from flab2bp.sfy.layout.realise import RealiseError
from flab2bp.sfy.layout.refusals import GAME_DATA, GAME_LIMITS, REFUSALS
from flab2bp.sfy.layout.router import BudgetCause, Routed, RouteFailureKind
from flab2bp.sfy.layout.rrr import RoutingOutcome
from flab2bp.sfy.layout.validate import validate
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup, designer
from flab2bp.sfy.strategy_names import SFY_PRODUCTION_STRATEGIES
from flab2bp.spec import BeltTier
from tests.sfy.conftest import flow_spec, sfy_registry

#: The designer every build in this file is laid out in.  The smallest one: a
#: grid-routed build stands its machines where the packer likes rather than in
#: rows, so it has no band to outgrow.
MARK = "mk1"

#: A wall long enough that a slow box never turns a routing failure into a clock
#: failure.  The strategy's own default is 15 s and the report says what these
#: builds really took.
BUDGET_S = 120.0

#: What a clean report stands aside on, and nothing else: the two partial rules.
ALWAYS_SKIPPED = {"geom.hard_clearance", "belt.capsule"}

#: The one belt the fragments below fund, and the one upgrade above it.  Written
#: here because a fragment is not a FactorioLab flow: the committed exports are
#: what ``spec_from_flow`` reads and they are used in the ``xfail`` below.
MK1_BELT = BeltTier(item_id="conveyor-belt-mk1", items_per_second=Fraction(1))
MK2_BELT = BeltTier(item_id="conveyor-belt-mk2", items_per_second=Fraction(2))

#: One machine per stage, in production order: ore in at the ``-Y`` wall, rod out
#: at the ``+Y`` wall.
_STAGES: tuple[tuple[str, str, str, str], ...] = (
    ("Build_SmelterMk1_C", "Recipe_IngotIron_C", "iron-ore", "iron-ingot"),
    ("Build_ConstructorMk1_C", "Recipe_IronRod_C", "iron-ingot", "iron-rod"),
)


def _registry() -> Registry:
    return sfy_registry()


def _chain() -> SfyBuildSpec:
    """A two-stage fragment: a smelter feeding a constructor, one machine each.

    A fragment rather than a flow, and stated here for the reason
    :mod:`tests.sfy.test_rrr`'s is: what is being tested is the STRATEGY's loop
    -- that it packs, routes, powers, floors and describes one build -- and the
    committed flows all run several machines on a recipe, which is the shape two
    defects below this module refuse (see this file's docstring).
    """
    groups = tuple(
        SfyMachineGroup(
            recipe_id=recipe.lower(),
            recipe_class=recipe,
            machine_item_id="machine",
            machine_class=machine,
            count=1,
            clock=Fraction(1),
            last_clock=Fraction(1),
            max_clock=Fraction(1),
            somersloops=0,
            power_shards_per_machine=0,
            last_power_shards=0,
            inputs_per_machine={eats: Fraction(1)},
            outputs_per_machine={makes: Fraction(1)},
            power_mw_per_machine=0.0,
            last_power_mw=0.0,
        )
        for machine, recipe, eats, makes in _STAGES
    )
    return SfyBuildSpec(
        groups=groups,
        belt_item_id=MK1_BELT.item_id,
        belt_items_per_second=MK1_BELT.items_per_second,
        belt_upgrades=(MK2_BELT,),
        external_inputs={_STAGES[0][2]: Fraction(1)},
        outputs={_STAGES[-1][3]: Fraction(1)},
        label="iron rod",
    )


def _lay_out(spec: SfyBuildSpec, mark: str = MARK, **kwargs: Any) -> SfyPlacement:
    return GridRouted().lay_out(spec, designer(mark, _registry()), **kwargs)


def _refusal(spec: SfyBuildSpec, mark: str = MARK, **kwargs: Any) -> str:
    with pytest.raises(NoValidLayout) as caught:
        _lay_out(spec, mark, **kwargs)
    return caught.value.reason


@pytest.fixture(scope="module")
def laid_out() -> SfyPlacement:
    """One build, laid out once and read by several tests."""
    return _lay_out(_chain(), time_budget_s=BUDGET_S)


# --- the headline -----------------------------------------------------------


def test_a_chain_lays_out_in_mk1_and_validates_clean(laid_out: SfyPlacement) -> None:
    """A whole build: packed, routed, powered, floored -- and judged clean.

    The spec is handed over with the placement, so the three checks that need
    one really run: every machine the spec calls for is standing, every item it
    belts in arrives and everything it sends out leaves.
    """
    spec = _chain()
    report = validate(laid_out, spec, _registry())

    assert [finding.message for finding in report.errors] == []
    assert report.ok
    assert set(report.skipped) == ALWAYS_SKIPPED
    assert len(laid_out.machines) == spec.machine_count
    assert laid_out.belts
    assert laid_out.poles
    assert laid_out.wires
    assert laid_out.foundations
    # Every id in the build is the build's own numbering, and unique across the
    # kinds: the packer numbers the machines from one and the strategy carries
    # on from where it stopped.
    ids = [obj.id for obj in laid_out.objects]
    assert len(ids) == len(set(ids))
    assert min(machine.id for machine in laid_out.machines) == 1


@pytest.mark.xfail(
    strict=True,
    raises=NoValidLayout,
    reason=(
        "a corpus flow runs several machines on one recipe, so its wall net is one stream "
        "to several ports; the packer stands those ports four nodes off the wall where a tap "
        "needs nine, and a tap branch that turns after three grid steps is refused by one "
        "centimetre.  Both are defects below this strategy -- see task-10-report.md"
    ),
)
def test_a_corpus_flow_does_not_route_yet_and_says_which_nets() -> None:
    """FactorioLab's own ``iron-plate-60``, which this strategy cannot belt today.

    Kept as a strict ``xfail`` rather than deleted: it is the test the brief
    asks for, it names the two defects in its reason, and it turns into a
    failure the moment either of them is fixed.
    """
    spec = flow_spec("iron-plate-60")
    placement = _lay_out(spec, time_budget_s=BUDGET_S)
    report = validate(placement, spec, _registry())
    assert [finding.message for finding in report.errors] == []
    assert report.ok


def test_the_description_names_the_strategy_and_counts_lifts_and_attachments(
    laid_out: SfyPlacement,
) -> None:
    """The ``.sbpcfg``'s text: what a reader cannot measure off the blueprint.

    Which arrangement the packer settled on, how many rip-up rounds the router
    needed, and what the build authored to turn its belts -- none of which
    survives into the ``.sbp``.  The counts are read back through
    :func:`~flab2bp.sfy.layout.measure.measure`, so the description cannot claim
    a lift or an attachment the placement does not hold.
    """
    description = laid_out.description
    counted = measure(laid_out, _registry())

    assert description.splitlines()[0].startswith("iron rod")
    assert "grid-routed" in description
    assert f"of {ARRANGEMENTS}" in description
    assert "rip-up rounds" in description
    assert f"{counted.lifts} conveyor lifts" in description
    assert f"{counted.attachments} conveyor attachments" in description
    assert "entry: iron-ore at the -Y wall" in description
    assert "exit: iron-rod at the +Y wall" in description
    assert "turns: " in description
    assert any(line.startswith("power: ") for line in description.splitlines())
    assert laid_out.short_desc == f"iron rod: {len(laid_out.machines)} machines"


def test_the_placement_round_trips_through_emit_and_decode(laid_out: SfyPlacement) -> None:
    """``decode(emit(placement)) == placement``: the file is the build.

    The same door the pipeline writes through -- the shipped template library
    and the newest fixture header's build version -- so what is compared is what
    would land on disk.
    """
    header = pipeline.newest_fixture_header()
    blueprint = emit(
        laid_out,
        pipeline.registry(),
        pipeline.template_library(),
        pipeline.lab_map(),
        build_version=header.build_version,
        version_data=header.version_data,
    )
    assert decode(blueprint, pipeline.registry()) == laid_out


# --- the protocol -----------------------------------------------------------


def test_grid_routed_satisfies_the_protocol() -> None:
    """One of the two production strategies, with the surface the race calls.

    ``isinstance`` against a ``runtime_checkable`` protocol looks at attribute
    names only; the check that matters is mypy's, over the whole file.
    """
    strategy: SfyLayoutStrategy = GridRouted()
    assert isinstance(strategy, SfyLayoutStrategy)
    assert strategy.name == "grid-routed"
    assert strategy.name in SFY_PRODUCTION_STRATEGIES


# --- the refusals -----------------------------------------------------------


def test_a_fluid_spec_refuses_fluids_are_m4() -> None:
    """A fluid is refused before any geometry, the way the manifold refuses one.

    FactorioLab's dataset is what says an item is a fluid -- it carries no stack
    size -- and piping is a later milestone, so the build is turned away at the
    top of the run rather than after a pack nobody can use.
    """
    chain = _chain()
    wet = chain.model_copy(
        update={"external_inputs": {**chain.external_inputs, "water": Fraction(1)}}
    )
    assert _refusal(wet) == "fluids are M5"


#: One stage, one error it really raises, and the named cause a caller sees.
#: Every entry is a cause out of :data:`~flab2bp.sfy.layout.refusals.REFUSALS`.
STAGE_REFUSALS: tuple[tuple[str, str, Callable[[], Exception], str], ...] = (
    ("pack", "pack", lambda: PackError(NO_ARRANGEMENT, "nowhere to stand"), NO_ARRANGEMENT),
    ("pack", "pack", lambda: PackError(OVER_BUDGET, "no incumbent"), OVER_BUDGET),
    ("pack", "pack", lambda: TransportRefusal("DEADLINE", "the wall"), OVER_BUDGET),
    ("measure", "_measure", lambda: RowError("the registry states no hologram grid"), GAME_LIMITS),
    ("measure", "_measure", lambda: CorridorError("limits", "no bend radius"), GAME_LIMITS),
    ("measure", "_measure", lambda: CorridorError("data", "no splitter box"), GAME_DATA),
    (
        "nets",
        "nets_for",
        lambda: NetError("ceiling", "past every belt this spec allows"),
        "run exceeds the belt ceiling",
    ),
    (
        "nets",
        "nets_for",
        lambda: NetError("lattice", "off the lattice"),
        "a port is off the hologram lattice",
    ),
    (
        "nets",
        "nets_for",
        lambda: NetError("ports", "too few belt ports"),
        "more input items than the machine has belt ports",
    ),
    ("nets", "nets_for", lambda: NetError("data", "no such buildable"), GAME_DATA),
    (
        "route",
        "route_all",
        lambda: RealiseError("corner", (), "no room for a turn"),
        "a belt could not be laid",
    ),
    (
        "route",
        "route_all",
        lambda: TransportRefusal("DEADLINE", "the wall"),
        "routing exceeded the budget",
    ),
    (
        "power",
        "place_on_free_nodes",
        lambda: PowerError("room", "no free node"),
        "no room for a power pole",
    ),
    (
        "power",
        "place_on_free_nodes",
        lambda: PowerError("wire", "too far"),
        "wire exceeds the maximum length",
    ),
    (
        "power",
        "place_on_free_nodes",
        lambda: PowerError("port", "on no wire"),
        "a machine has no power connection",
    ),
    ("power", "place_on_free_nodes", lambda: PowerError("data", "no pole class"), GAME_DATA),
    ("power", "place_on_free_nodes", lambda: PowerError("limits", "no wire length"), GAME_LIMITS),
)


@pytest.mark.parametrize(
    ("stage", "name", "raiser", "cause"),
    STAGE_REFUSALS,
    ids=[f"{entry[0]}-{entry[3]}" for entry in STAGE_REFUSALS],
)
def test_no_refusal_escapes_the_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    name: str,
    raiser: Callable[[], Exception],
    cause: str,
) -> None:
    """Every stage's own error reaches a caller as a NAMED cause.

    A stage raises what it raises -- a ``PackError``, a ``NetError``, a
    ``RealiseError``, a ``PowerError``, a ``RowError``, a ``CorridorError``, a
    budget's ``TransportRefusal`` -- and this strategy is the one place each is
    turned into one of :data:`~flab2bp.sfy.layout.refusals.REFUSALS`.  A cause
    that escaped the table would reach the corpus as a pin nobody declared.
    """

    def fail(*args: object, **kwargs: object) -> object:
        raise raiser()

    monkeypatch.setattr(grid, name, fail)
    reason = _refusal(_chain(), time_budget_s=BUDGET_S)
    assert reason == cause
    assert reason in REFUSALS
    assert stage  # the id carries which stage this is; the assertion is the cause


def _stranded(nets: object, kind: RouteFailureKind) -> RoutingOutcome:
    """Every net of ``nets`` stranded for ``kind``, and nothing routed."""
    listed = list(nets)  # type: ignore[call-overload]
    cause = BudgetCause.DEADLINE if kind is RouteFailureKind.BUDGET else BudgetCause.UNKNOWN
    return RoutingOutcome(
        trees=(),
        stranded=tuple((net, Routed(None, kind, (), 0, cause)) for net in listed),
        realised=(),
        rounds=1,
        work=0,
        blame={},
    )


def test_a_net_nobody_could_route_is_named_in_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every arrangement tried and a net still stranded: the cause names the nets.

    The router RETURNS its failures rather than raising them, so this is the one
    refusal the strategy reaches by exhausting its own loop.  The packer is left
    real, so the loop really packs :data:`ARRANGEMENTS` arrangements.
    """
    tried: list[int] = []

    def stranding(nets: object, occupancy: object, **kwargs: object) -> RoutingOutcome:
        tried.append(len(list(nets)))  # type: ignore[call-overload]
        return _stranded(nets, RouteFailureKind.SEALED_POCKET)

    monkeypatch.setattr(grid, "route_all", stranding)
    with pytest.raises(NoValidLayout) as caught:
        _lay_out(_chain(), time_budget_s=BUDGET_S)
    assert caught.value.reason == "a belt could not be routed"
    assert caught.value.attempt_reasons
    assert "iron-ore" in caught.value.attempt_reasons[0]
    assert len(tried) == ARRANGEMENTS


def test_a_net_the_clock_never_reached_refuses_the_budget_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A net stranded by a bound says "more seconds", not "this build cannot be belted".

    The distinction is the router's own: a ``BUDGET`` result proves nothing about
    the geometry, so naming the nets would send a reader looking for a pocket
    that was never censused.
    """

    def stranding(nets: object, occupancy: object, **kwargs: object) -> RoutingOutcome:
        return _stranded(nets, RouteFailureKind.BUDGET)

    monkeypatch.setattr(grid, "route_all", stranding)
    assert _refusal(_chain(), time_budget_s=BUDGET_S) == "routing exceeded the budget"


def test_a_later_pack_that_runs_out_of_clock_reports_what_routing_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second arrangement's clock is not the run's answer, the first one's routing is.

    Once a build has been packed and routed, "packing exceeded the budget" would
    send a reader off to shrink machines that really did stand.  A FIRST
    arrangement that runs out has nothing behind it and says so, which is the
    test above this one.
    """
    packs: list[int] = []

    def once(*args: object, **kwargs: object) -> object:
        packs.append(1)
        if len(packs) > 1:
            raise PackError(OVER_BUDGET, "the clock stopped the second arrangement")
        return pack(*args, **kwargs)  # type: ignore[arg-type]

    def stranding(nets: object, occupancy: object, **kwargs: object) -> RoutingOutcome:
        return _stranded(nets, RouteFailureKind.SEALED_POCKET)

    monkeypatch.setattr(grid, "pack", once)
    monkeypatch.setattr(grid, "route_all", stranding)
    assert _refusal(_chain(), time_budget_s=BUDGET_S) == "a belt could not be routed"
    assert len(packs) == 2


def test_a_pack_proved_infeasible_is_the_answer_however_late_it_comes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build PROVED not to stand is not a bound, so it is reported as itself.

    The two wear one exception type, and only the budget one is a reason to fall
    back on what the routing found.
    """
    packs: list[int] = []

    def once(*args: object, **kwargs: object) -> object:
        packs.append(1)
        if len(packs) > 1:
            raise PackError(NO_ARRANGEMENT, "nowhere for the second arrangement to stand")
        return pack(*args, **kwargs)  # type: ignore[arg-type]

    def stranding(nets: object, occupancy: object, **kwargs: object) -> RoutingOutcome:
        return _stranded(nets, RouteFailureKind.SEALED_POCKET)

    monkeypatch.setattr(grid, "pack", once)
    monkeypatch.setattr(grid, "route_all", stranding)
    assert _refusal(_chain(), time_budget_s=BUDGET_S) == NO_ARRANGEMENT


def test_the_time_budget_is_a_wall_the_strategy_keeps() -> None:
    """A deadline already gone refuses rather than packing against no clock.

    ``absolute_deadline`` is somebody else's wall in this process's own
    ``time.monotonic()`` frame, which is how a race hands both strategies the
    same one, and the packer is the first stage to read it.
    """
    reason = _refusal(_chain(), absolute_deadline=time.monotonic() - 1.0)
    assert reason == OVER_BUDGET
    assert reason in REFUSALS
