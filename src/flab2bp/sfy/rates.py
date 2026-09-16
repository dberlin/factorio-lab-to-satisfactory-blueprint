"""FactorioLab's chosen Satisfactory flow, read out as a :class:`SfyBuildSpec`.

Nothing here re-solves anything.  FactorioLab already picked the recipes, the
machines and the fractional machine counts; this module reproduces the
*per-machine* arithmetic behind those counts, checks that it lands exactly on
the rates FactorioLab wrote, and refuses if it does not.  A disagreement is a
bug in this file or a change in FactorioLab, and either way the honest answer
is a refusal naming the row -- never a silent correction of the flow.

The rate model, transcribed from FactorioLab's own recipe adjustment
(``src/state/adjustment.ts`` in https://github.com/factoriolab/factoriolab at
commit ``c2dd576c695f44f8bb9031615ab5a7f5fafa86e4``, read 2026-09-14):

* **Overclock** (adjustment.ts:278-283, applied at :327) -- ``oc = overclock /
  100``, then ``eff.speed *= oc`` and, twelve lines further down,
  ``recipe.time = recipe.time.div(eff.speed)``.  The URL states a percentage;
  :attr:`clock` is the fraction.
* **Somersloop** (adjustment.ts:160-189) -- the boost is scaled by the
  machine's own slot count::

      scale  = machine.modules                 # production-boost slots
      eff.productivity += module.productivity * count / scale
      eff.consumption  += (module.consumption * count / scale + 1) ** 2 - 1

  With ``module.productivity == 1`` and ``module.consumption == 1`` for
  ``somersloop``, that is a productivity multiplier of ``1 + n/slots`` and a
  power multiplier of ``(1 + n/slots) ** 2``.
* **Productivity scales outputs only** (adjustment.ts:336-346) -- inputs are
  per craft and untouched, so a boosted machine draws *fewer* inputs per second
  because it runs fewer crafts for the same output.

``machine.modules`` is read from FactorioLab's dataset, not from the registry's
``production_boost_slots``.  The two disagree for the Smelter (FactorioLab says
1 slot, the game says 0), and this number exists here only to reproduce what
FactorioLab computed.

Power is the one figure NOT taken from FactorioLab: see :func:`_machine_power_mw`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import TYPE_CHECKING

from flab2bp.lab.schema import Dataset, Machine, Recipe
from flab2bp.lab.url import (
    DisplayRate,
    LabRequest,
    MachineSetting,
    ObjectiveType,
    ObjectiveUnit,
    RecipeSetting,
)
from flab2bp.sfy.labmap import LabMap, machine_class, recipe_class
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import PipeTier, SfyBuildSpec, SfyMachineGroup
from flab2bp.spec import BeltTier

if TYPE_CHECKING:  # `flab2bp.lab.flow` imports the DSP catalog at module scope
    from flab2bp.lab.flow import FlowRow, FlowSelection

#: FactorioLab's module id for a somersloop in sfy's ``hash.modules``.
SOMERSLOOP = "somersloop"

#: Seconds in one unit of each display rate, so a displayed rate divided by
#: this is items per second.
_DISPLAY_SECONDS: dict[DisplayRate, Fraction] = {
    DisplayRate.PerSecond: Fraction(1),
    DisplayRate.PerMinute: Fraction(60),
    DisplayRate.PerHour: Fraction(3600),
}

#: A machine running at its rated 100 %.  Every other clock figure -- the
#: ceiling, the step a power shard buys -- is read from the game through the
#: registry; this one is just the unit the game states those in.
_FULL_CLOCK = Fraction(1)


class RatesRefusal(ValueError):
    """The flow and the rate model cannot both be right, so neither is used."""

    def __init__(self, cause: str, detail: str = "") -> None:
        self.cause = cause
        super().__init__(f"{cause}: {detail}" if detail else cause)


def _per_second(displayed: Fraction, display_rate: DisplayRate) -> Fraction:
    """A rate in the URL's display unit, exactly, as items per second."""
    try:
        seconds = _DISPLAY_SECONDS[display_rate]
    except KeyError:  # pragma: no cover - DisplayRate has exactly three members
        raise RatesRefusal("unknown display rate", str(display_rate)) from None
    return displayed / seconds


def _ceiling(value: Fraction) -> int:
    """Exact ceiling of a ``Fraction``.

    The two lines of ``flab2bp.rates.machine_choice.machines_needed``, copied
    rather than imported.  See :func:`_select_machine` for why nothing under
    ``flab2bp.rates`` is imported here at all.
    """
    return -((-value.numerator) // value.denominator)


def _select_machine(recipe: Recipe, rank: Sequence[str] | None) -> str:
    """FactorioLab's ``bestMatch``: first ranked producer, else ``producers[0]``.

    The six lines of ``flab2bp.rates.adjust.select_machine``, copied rather than
    imported.  Both that module (adjust.py:18) and ``machine_choice`` import
    ``flab2bp.dsp.catalog`` at module scope, so importing either would load the
    whole DSP catalog on the Satisfactory path.  For the same reason the
    ``FlowRow``/``FlowSelection`` types come in under ``TYPE_CHECKING``:
    ``flab2bp.lab.flow`` imports the catalog too (flow.py:103), and design §6
    has severing that as its own job.  ``test_rates.py`` holds the line:
    importing ``flab2bp.sfy.rates`` in a fresh interpreter must leave
    ``flab2bp.dsp`` out of ``sys.modules``.
    """
    for machine_id in rank or ():
        if machine_id in recipe.producers:
            return machine_id
    if not recipe.producers:
        raise RatesRefusal("the recipe has no producers", recipe.id)
    return recipe.producers[0]


def _crafts_per_second(machine: Machine, recipe: Recipe, clock: Fraction) -> Fraction:
    """Crafts one machine completes per second at ``clock``.

    FactorioLab's ``recipe.time / (speed * oc)`` (adjustment.ts:278-283 sets
    ``eff.speed``, :327 divides the time by it).
    """
    speed = machine.speed if machine.speed is not None else Fraction(1)
    time = recipe.time
    if time is None or time <= 0:
        raise RatesRefusal("recipe has no craft time", recipe.id)
    return speed * clock / time


def _somersloops(setting: MachineSetting | RecipeSetting | None) -> int:
    """Somersloops declared on one machine or recipe setting."""
    if setting is None or setting.modules is None:
        return 0
    total = Fraction(0)
    for module in setting.modules:
        if module.id == SOMERSLOOP and module.count is not None:
            total += module.count
    if total.denominator != 1:
        raise RatesRefusal("fractional somersloop count", str(total))
    return int(total)


def _clock(request: LabRequest, recipe_id: str, machine_item_id: str) -> Fraction:
    """The requested potential for one row, most specific setting first.

    FactorioLab states an overclock as a percentage; a machine at 100 % runs at
    clock 1.
    """
    percent: Fraction | None = None
    recipe_setting = request.recipes.get(recipe_id)
    if recipe_setting is not None:
        percent = recipe_setting.overclock
    if percent is None:
        machine_setting = request.machines.get(machine_item_id)
        if machine_setting is not None:
            percent = machine_setting.overclock
    if percent is None:
        percent = request.overclock
    if percent is None:
        return _FULL_CLOCK
    return percent / 100


def _boost_scale(data: Dataset, machine_item_id: str, somersloops: int) -> Fraction:
    """``1 + n/slots``: FactorioLab's scaled somersloop effect."""
    if somersloops == 0:
        return Fraction(1)
    slots = data.machine(machine_item_id).modules
    if not slots:
        raise RatesRefusal(
            "machine has no production-boost slots",
            f"{machine_item_id} cannot hold {somersloops} somersloop(s)",
        )
    return Fraction(1) + Fraction(somersloops, slots)


def _power_shards(clock: Fraction, registry: Registry) -> int:
    """Power shards one machine needs to reach ``clock``.

    ``potential_per_shard`` is the game's own figure, read from the registry
    rather than assumed; it is 0.5, so 250 % costs three shards.
    """
    if clock <= _FULL_CLOCK:
        return 0
    per_shard = registry.limits.potential_per_shard
    if not per_shard:
        raise RatesRefusal(
            "the game states no potential per shard",
            "a clock above 100% cannot be paid for",
        )
    return _ceiling((clock - _FULL_CLOCK) / Fraction(per_shard))


def max_clock(registry: Registry, machine_cls: str) -> Fraction:
    """The highest potential ``machine_cls`` can be driven to.

    Every term is the game's own: the buildable's ``mMaxPotential`` plus as many
    ``potential_per_shard`` steps as it has ``potential_shard_slots_default``
    slots to hold shards in.  For a production building that is
    ``1 + 3 x 0.5 = 250 %``, but it is read rather than written down, because a
    buildable that takes no shards cannot be overclocked at all.
    """
    buildable = registry.buildables[machine_cls]
    if buildable.max_potential is None:
        raise RatesRefusal("the game states no maximum potential", machine_cls)
    limits = registry.limits
    if limits.potential_shard_slots_default is None or limits.potential_per_shard is None:
        raise RatesRefusal(
            "the game states no power-shard ceiling",
            "potential_shard_slots_default or potential_per_shard is missing",
        )
    # `Fraction(float)` is exact for these two ONLY because the registry holds
    # them as binary-exact values today -- max_potential 1.0 and
    # potential_per_shard 0.5, both whole multiples of a power of two.  A future
    # game value like 0.1 would arrive as the nearest double and this would
    # silently carry that error into an exact-looking ceiling; the fix then is
    # for the merge to carry these as decimal strings, not to round here.
    slots = Fraction(limits.potential_shard_slots_default)
    return Fraction(buildable.max_potential) + slots * Fraction(limits.potential_per_shard)


def _boost_power_factor(registry: Registry, machine_cls: str, boost: Fraction) -> float:
    """What the somersloops multiply a machine's draw by.

    ``boost`` is ``1 + filled/total``, and the game raises it to the buildable's
    own ``mProductionBoostPowerConsumptionExponent`` (2.0 for every production
    building).  FactorioLab writes the same square out longhand --
    ``effect.div(scale).add(one)``, then ``effect.mul(effect).sub(one)``
    (adjustment.ts:182-186), under the comment "Overall effect = (1 + filled
    slots / total slots) ^ 2" -- so the two agree, and the exponent here is the
    game's rather than FactorioLab's hard-coded 2.
    """
    exponent = registry.buildables[machine_cls].production_boost_power_exponent
    if exponent is None:
        raise RatesRefusal("the game states no production-boost power exponent", machine_cls)
    return float(float(boost) ** exponent)


def _machine_power_mw(
    registry: Registry, machine_cls: str, clock: Fraction, boost: Fraction
) -> float:
    """Megawatts one machine draws at ``clock`` with its somersloops in.

    ``base x boost^boost_exponent x clock^power_exponent``, both exponents read
    from the buildable itself: ``mPowerConsumptionExponent`` (1.321929) and
    ``mProductionBoostPowerConsumptionExponent`` (2.0), which the extractor
    lifts out of Docs.json.

    The result is a ``float`` because the clock exponent is fractional, so no
    exact value exists.  That is why this is a report figure and not a rate.
    FactorioLab computes the same product (adjustment.ts:348-355, with
    ``usage.mul(oc.pow(1.321928))`` at :353); its exponent is the game's
    rounded one digit short, and ``Rational.pow`` (rational.ts:153-157) goes
    through ``Math.pow`` anyway, so FactorioLab's own answer is a float too.
    ``test_rates.py`` pins ours against FactorioLab's Power column to 1e-6
    relative, which is the width of that last-digit disagreement.
    """
    buildable = registry.buildables[machine_cls]
    exponent = buildable.power_exponent
    if exponent is None:
        raise RatesRefusal("the game states no power exponent", machine_cls)
    factor = _boost_power_factor(registry, machine_cls, boost)
    return buildable.power_mw * factor * float(float(clock) ** exponent)


def _fluids(data: Dataset, item_ids: Mapping[str, Fraction]) -> frozenset[str]:
    """Those of ``item_ids`` the lab dataset carries without a stack size.

    An id the dataset does not carry at all is refused rather than waved
    through: it would otherwise slip past classification and be belted as a solid.
    """
    out = []
    for item_id in item_ids:
        item = data.get_item(item_id)
        if item is None:
            raise RatesRefusal(
                "unknown item",
                f"the flow moves {item_id!r}, which the dataset does not carry",
            )
        if item.stack is None:
            out.append(item_id)
    return frozenset(out)


def _belts(data: Dataset, request: LabRequest) -> tuple[str, Fraction, tuple[BeltTier, ...]]:
    """The belt floor and every faster belt the build may use, slowest first.

    ``LabRequest`` carries no maximum-belt channel -- FactorioLab's URL has one
    only for the floor (``ibe``) -- so the ceiling is always the dataset's
    ``defaults.maxBelt``, which for sfy is Mk5 even though Mk6 exists.
    """
    floor_id = request.belt_id or data.defaults.min_belt
    if floor_id is None:
        raise RatesRefusal("the dataset names no belt", "neither the URL nor defaults.minBelt")
    floor_speed = data.belt_speed(floor_id)

    ceiling_id = data.defaults.max_belt
    ceiling_speed = data.belt_speed(ceiling_id) if ceiling_id else floor_speed

    tiers = []
    for item in data.items:
        if item.belt is None or item.id == floor_id:
            continue
        speed = data.belt_speed(item.id)
        if floor_speed < speed <= ceiling_speed:
            tiers.append(BeltTier(item_id=item.id, items_per_second=speed))
    tiers.sort(key=lambda t: t.items_per_second)
    return floor_id, floor_speed, tuple(tiers)


def _pipes(data: Dataset, request: LabRequest) -> tuple[PipeTier, ...]:
    """Allowed pipelines, floor first, at their dataset-rated cubic metres/s.

    Like belts, the URL selects a floor and the dataset supplies the ceiling.
    Unlike belts, ``pipe_tiers`` includes the floor itself.
    """
    floor_id = request.pipe_id or data.defaults.min_pipe
    if floor_id is None:
        return ()
    floor_speed = data.pipe_speed(floor_id)
    ceiling_id = data.defaults.max_pipe
    ceiling_speed = data.pipe_speed(ceiling_id) if ceiling_id else floor_speed
    tiers = [PipeTier(item_id=floor_id, cubic_metres_per_second=floor_speed)]
    for item in data.items:
        if item.pipe is None or item.id == floor_id:
            continue
        speed = data.pipe_speed(item.id)
        if floor_speed < speed <= ceiling_speed:
            tiers.append(PipeTier(item_id=item.id, cubic_metres_per_second=speed))
    tiers.sort(key=lambda tier: tier.cubic_metres_per_second)
    return tuple(tiers)


def _is_extraction(data: Dataset, row: FlowRow) -> bool:
    """Is this row a miner, an extractor or a research bench?

    Extraction is never part of the build.  A Blueprint Designer cannot hold a
    miner -- it needs a resource node -- and ``flow.external_items`` already
    belts the mined item in at the boundary, so reading the row as a group as
    well would supply the ore twice.  This mirrors the DSP path, where
    extraction columns never become a ``SolvedGroup`` either (solve.py:1291).

    The same cut applies to an extraction row's SURPLUS: an over-provisioned
    miner's spare ore is an artefact of rounding the node up, not something the
    build has to belt out.
    """
    if not row.recipe_id:
        return False
    recipe = data.get_recipe(row.recipe_id)
    return recipe is not None and (recipe.is_mining or recipe.is_technology)


def _group_from_row(
    row: FlowRow,
    data: Dataset,
    request: LabRequest,
    registry: Registry,
    labmap: LabMap,
) -> SfyMachineGroup:
    """One flow row as a row of machines, at FactorioLab's own rates."""
    assert row.machines is not None  # the caller filters on machines > 0
    recipe = data.recipe(row.recipe_id)
    machine_item_id = row.machine_item_id or _select_machine(recipe, request.machine_rank_ids)
    machine = data.machine(machine_item_id)

    clock = _clock(request, row.recipe_id, machine_item_id)
    # A recipe setting that declares modules at all speaks for the row; only a
    # row with nothing said about it falls back to the machine-wide setting.
    modules: MachineSetting | RecipeSetting | None = request.recipes.get(row.recipe_id)
    if modules is None or modules.modules is None:
        modules = request.machines.get(machine_item_id)
    somersloops = _somersloops(modules)
    boost = _boost_scale(data, machine_item_id, somersloops)

    crafts = _crafts_per_second(machine, recipe, clock)
    inputs = {item: amount * crafts for item, amount in recipe.inputs.items()}
    outputs = {item: amount * crafts * boost for item, amount in recipe.outputs.items()}

    # FactorioLab's fractional count times our per-machine rate must land
    # exactly on the rate FactorioLab wrote for this row's own item.
    if row.item_id and row.items is not None:
        produced = _per_second(row.items + (row.surplus or Fraction(0)), request.display_rate)
        ours = outputs.get(row.item_id, Fraction(0)) * row.machines
        if ours != produced:
            raise RatesRefusal(
                "flow rate disagrees with the rate model",
                f"row {row.recipe_id!r} produces {row.item_id!r} at {produced}/s in the "
                f"flow but {ours}/s under the rate model",
            )

    count = _ceiling(row.machines)
    machine_cls = machine_class(labmap, machine_item_id)
    try:
        recipe_cls = recipe_class(labmap, row.recipe_id)
    except KeyError as exc:
        raise RatesRefusal("the recipe has no game class", str(exc)) from None

    # The odd machine at the end absorbs the fractional remainder, so it runs
    # slower than the rest and is costed on its own clock throughout.
    last_clock = clock * (row.machines - (count - 1))
    return SfyMachineGroup(
        recipe_id=row.recipe_id,
        recipe_class=recipe_cls,
        machine_item_id=machine_item_id,
        machine_class=machine_cls,
        count=count,
        clock=clock,
        last_clock=last_clock,
        max_clock=max_clock(registry, machine_cls),
        somersloops=somersloops,
        power_shards_per_machine=_power_shards(clock, registry),
        last_power_shards=_power_shards(last_clock, registry),
        inputs_per_machine=inputs,
        outputs_per_machine=outputs,
        power_mw_per_machine=_machine_power_mw(registry, machine_cls, clock, boost),
        last_power_mw=_machine_power_mw(registry, machine_cls, last_clock, boost),
    )


def spec_from_flow(
    data: Dataset,
    request: LabRequest,
    flow: FlowSelection,
    registry: Registry,
    labmap: LabMap,
    *,
    label: str = "",
) -> SfyBuildSpec:
    """The chosen build, in exact items/second or cubic metres/second for fluids.

    Raises :class:`RatesRefusal` when the flow and the rate model disagree or
    when a row names something the game tables cannot place.
    """
    groups = []
    for row in flow.rows:
        if not row.recipe_id or row.machines is None or row.machines <= 0:
            continue
        if _is_extraction(data, row):
            continue
        groups.append(_group_from_row(row, data, request, registry, labmap))

    external_inputs = {
        item_id: _per_second(rate, request.display_rate)
        for item_id, rate in flow.external_items(data).items()
    }

    consumed: dict[str, Fraction] = {}
    for group in groups:
        for item, rate in group.row_inputs.items():
            consumed[item] = consumed.get(item, Fraction()) + rate

    outputs: dict[str, Fraction] = {}
    for objective in request.objectives:
        if objective.type is not ObjectiveType.Output:
            raise RatesRefusal(
                "only output objectives are supported",
                f"objective {objective.target_id!r} is {objective.type.name}",
            )
        if objective.unit is not ObjectiveUnit.Items:
            raise RatesRefusal(
                "only item objectives are supported",
                f"objective {objective.target_id!r} is measured in {objective.unit.name}",
            )
        target = flow.by_item.get(objective.target_id)
        if target is None or target.items is None:
            raise RatesRefusal(
                "the flow does not produce the objective",
                f"no row for {objective.target_id!r}",
            )
        # CSV Items includes material consumed by downstream recipes; Surplus
        # is separate. Only the unconsumed objective rate crosses the boundary.
        exported = _per_second(target.items, request.display_rate) - consumed.get(
            objective.target_id, Fraction()
        )
        if exported <= 0:
            raise RatesRefusal(
                "the flow does not export the objective",
                f"{objective.target_id!r} has {exported}/s left after internal consumption",
            )
        outputs[objective.target_id] = exported

    surplus_outputs = {
        row.item_id: _per_second(row.surplus, request.display_rate)
        for row in flow.rows
        if row.item_id
        and row.surplus is not None
        and row.surplus > 0
        and not _is_extraction(data, row)
    }

    crossing = dict(external_inputs) | outputs | surplus_outputs
    for group in groups:
        crossing |= group.inputs_per_machine
        crossing |= group.outputs_per_machine
    fluids = _fluids(data, crossing)

    belt_item_id, belt_speed, upgrades = _belts(data, request)
    return SfyBuildSpec(
        groups=tuple(groups),
        external_inputs=external_inputs,
        outputs=outputs,
        surplus_outputs=surplus_outputs,
        belt_item_id=belt_item_id,
        belt_items_per_second=belt_speed,
        belt_upgrades=upgrades,
        fluid_items=fluids,
        pipe_tiers=_pipes(data, request),
        label=label,
    )


__all__ = ("SOMERSLOOP", "RatesRefusal", "max_clock", "spec_from_flow")
