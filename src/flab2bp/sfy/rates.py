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

* **Overclock** (adjustment.ts:278-283) -- ``oc = overclock / 100``, then
  ``eff.speed *= oc`` and ``recipe.time /= eff.speed``.  The URL states a
  percentage; :attr:`clock` is the fraction.
* **Somersloop** (adjustment.ts:160-189) -- the boost is scaled by the
  machine's own slot count::

      scale  = machine.modules                 # production-boost slots
      eff.productivity += module.productivity * count / scale
      eff.consumption  += (module.consumption * count / scale + 1) ** 2 - 1

  With ``module.productivity == 1`` and ``module.consumption == 1`` for
  ``somersloop``, that is a productivity multiplier of ``1 + n/slots`` and a
  power multiplier of ``(1 + n/slots) ** 2``.
* **Productivity scales outputs only** (adjustment.ts:336-345) -- inputs are
  per craft and untouched, so a boosted machine draws *fewer* inputs per second
  because it runs fewer crafts for the same output.

``machine.modules`` is read from FactorioLab's dataset, not from the registry's
``production_boost_slots``.  The two disagree for the Smelter (FactorioLab says
1 slot, the game says 0), and this number exists here only to reproduce what
FactorioLab computed.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction

from flab2bp.lab.flow import FlowRow, FlowSelection
from flab2bp.lab.schema import Dataset, Machine, Recipe
from flab2bp.lab.url import (
    DisplayRate,
    LabRequest,
    MachineSetting,
    ObjectiveType,
    ObjectiveUnit,
    RecipeSetting,
)
from flab2bp.rates.adjust import select_machine
from flab2bp.sfy.labmap import LabMap, machine_class, recipe_class
from flab2bp.sfy.registry import Registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup
from flab2bp.spec import BeltTier

#: FactorioLab's module id for a somersloop in sfy's ``hash.modules``.
SOMERSLOOP = "somersloop"

#: Seconds in one unit of each display rate, so a displayed rate divided by
#: this is items per second.
_DISPLAY_SECONDS: dict[DisplayRate, Fraction] = {
    DisplayRate.PerSecond: Fraction(1),
    DisplayRate.PerMinute: Fraction(60),
    DisplayRate.PerHour: Fraction(3600),
}

#: A machine's clock rises by this much per power shard.  The game states it as
#: ``Limits.potential_per_shard`` in the registry; this module reads that rather
#: than assume, and the constant below is only the fallback shape of the sum.
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
    rather than imported: that module pulls in ``flab2bp.dsp.catalog`` at import
    time, and the Satisfactory path has no business loading the DSP catalog.
    """
    return -((-value.numerator) // value.denominator)


def _crafts_per_second(machine: Machine, recipe: Recipe, clock: Fraction) -> Fraction:
    """Crafts one machine completes per second at ``clock``.

    FactorioLab's ``recipe.time / (speed * oc)`` (adjustment.ts:278-334).
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


def _machine_power_mw(
    registry: Registry, machine_cls: str, clock: Fraction, boost: Fraction
) -> Fraction:
    """Megawatts one machine draws at ``clock`` with its somersloops in.

    The somersloop term is exact: FactorioLab squares ``1 + n/slots``
    (adjustment.ts:179-189) and so does the game.

    The clock term is deliberately LINEAR.  Satisfactory's draw is
    superlinear -- FactorioLab raises the clock to ``1.321928``
    (adjustment.ts:349-352) through a float ``Math.pow``, which is both an
    approximation of the game's exponent and a float, and no float may reach
    these rates.  Task 4 reads the game's own
    ``mProductionBoostPowerConsumptionExponent`` out of Docs.json; until then
    this underestimates an overclocked machine and the flow's Power column is
    deliberately not pinned by a test.
    """
    base = Fraction(registry.buildables[machine_cls].power_mw)
    return base * boost * boost * clock


def _fluids(data: Dataset, item_ids: Mapping[str, Fraction]) -> list[str]:
    """Those of ``item_ids`` the lab dataset carries without a stack size.

    No stack is how FactorioLab marks a fluid, and fluids are M4.
    """
    out = []
    for item_id in item_ids:
        item = data.get_item(item_id)
        if item is not None and item.stack is None:
            out.append(item_id)
    return sorted(out)


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
    machine_item_id = row.machine_item_id or select_machine(data, recipe, request.machine_rank_ids)
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

    return SfyMachineGroup(
        recipe_id=row.recipe_id,
        recipe_class=recipe_cls,
        machine_item_id=machine_item_id,
        machine_class=machine_cls,
        count=count,
        clock=clock,
        # The odd machine at the end absorbs the fractional remainder.
        last_clock=clock * (row.machines - (count - 1)),
        somersloops=somersloops,
        power_shards_per_machine=_power_shards(clock, registry),
        inputs_per_machine=inputs,
        outputs_per_machine=outputs,
        power_mw_per_machine=_machine_power_mw(registry, machine_cls, clock, boost),
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
    """The build FactorioLab's chosen flow describes, in exact items/second.

    Raises :class:`RatesRefusal` when the flow and the rate model disagree, when
    the flow needs a fluid (M4), or when a row names something the game tables
    cannot place.
    """
    groups = []
    for row in flow.rows:
        if not row.recipe_id or row.machines is None or row.machines <= 0:
            continue
        recipe = data.get_recipe(row.recipe_id)
        if recipe is not None and (recipe.is_mining or recipe.is_technology):
            # Extraction is never a group: a Blueprint Designer cannot hold a
            # miner or an extractor, and `external_items` already belts the
            # mined item in at the boundary.  This mirrors the DSP path, where
            # extraction columns never become a SolvedGroup either.
            continue
        groups.append(_group_from_row(row, data, request, registry, labmap))

    external_inputs = {
        item_id: _per_second(rate, request.display_rate)
        for item_id, rate in flow.external_items(data).items()
    }

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
        outputs[objective.target_id] = _per_second(target.items, request.display_rate)

    surplus_outputs = {
        row.item_id: _per_second(row.surplus, request.display_rate)
        for row in flow.rows
        if row.item_id and row.surplus is not None and row.surplus > 0
    }

    crossing = dict(external_inputs) | outputs | surplus_outputs
    for group in groups:
        crossing |= group.inputs_per_machine
        crossing |= group.outputs_per_machine
    fluids = _fluids(data, crossing)
    if fluids:
        raise RatesRefusal("fluids are M4", f"this flow moves {', '.join(fluids)}")

    belt_item_id, belt_speed, upgrades = _belts(data, request)
    return SfyBuildSpec(
        groups=tuple(groups),
        external_inputs=external_inputs,
        outputs=outputs,
        surplus_outputs=surplus_outputs,
        belt_item_id=belt_item_id,
        belt_items_per_second=belt_speed,
        belt_upgrades=upgrades,
        label=label,
    )


__all__ = ("SOMERSLOOP", "RatesRefusal", "spec_from_flow")
