"""Stage 2 boundary for Satisfactory: one complete thing to build.

The shape mirrors :mod:`flab2bp.spec` -- pydantic models here, exact
``Fraction`` rates across the boundary, no float reaching geometry -- with the
three things Satisfactory has and DSP does not: a machine's *clock* (its
requested potential), *somersloops* in its production-boost slots, and the
*power shards* that pay for a clock above 100 %.

Two clocks per group, not one.  FactorioLab hands us a FRACTIONAL machine
count; the physical build rounds it up and underclocks the odd machine at the
end rather than overproducing, which is what a Satisfactory player does by
hand.  So ``clock`` is every machine's potential but the last's, and
``last_clock`` is the last machine's.  Where they are equal the group is
uniform and ``row_outputs`` is simply ``count`` times the machine rate.
Power shards and megawatts follow the same split, because an underclocked last
machine needs no shards and draws less.

Every RATE here is an exact ``Fraction``.  The ``*_power_mw`` fields are the one
deliberate exception and are ``float``: the game raises a machine's clock to a
fractional exponent, so its draw is irrational.  They are report figures --
they size generators and fill in a summary -- and nothing in the rates or in
geometry reads them.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flab2bp.sfy.registry import Registry
from flab2bp.spec import BeltTier

#: The game's designer buildables, by the mark a URL or CLI names.  The casing
#: is the game's own and is deliberately inconsistent between Mk2 and Mk3.
DESIGNER_CLASSES: dict[str, str] = {
    "mk1": "Build_BlueprintDesigner_C",
    "mk2": "Build_BlueprintDesigner_MK2_C",
    "mk3": "Build_BlueprintDesigner_Mk3_C",
}

#: The buildable whose footprint one Blueprint Designer cell is.  Designer
#: dimensions are stated in foundations, so the centimetres come from the
#: foundation the game ships rather than from a number written here.
FOUNDATION_CLASS = "Build_Foundation_8x1_01_C"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Designer(_Frozen):
    """The Blueprint Designer this build must fit inside."""

    mark: Literal["mk1", "mk2", "mk3"]
    #: Foundations, as the game's own designer buildable declares them.
    dims: tuple[int, int, int]
    #: One foundation's side in centimetres, read from
    #: :data:`FOUNDATION_CLASS`'s clearance box by :func:`designer`.
    foundation_cm: float = Field(gt=0)

    @property
    def half_cm(self) -> float:
        """Half the designer's width: the box is centred on its own origin."""
        return self.dims[0] * self.foundation_cm / 2

    @property
    def height_cm(self) -> float:
        return self.dims[2] * self.foundation_cm


def designer(mark: str, registry: Registry) -> Designer:
    """The designer named by ``mark``, sized from the game's own buildables.

    Both numbers are the game's: the cell count from the designer buildable's
    ``designer_dims`` and the centimetres per cell from the foundation's own
    footprint.  An unknown mark is refused rather than guessed, and so is a
    foundation that is not square -- a wrong box size silently truncates a
    build.
    """
    try:
        class_name = DESIGNER_CLASSES[mark]
    except KeyError:
        raise ValueError(
            f"unknown Blueprint Designer mark {mark!r}; "
            f"the game has {', '.join(sorted(DESIGNER_CLASSES))}"
        ) from None
    dims = registry.buildables[class_name].designer_dims
    if dims is None:
        raise ValueError(
            f"{class_name} declares no designer dimensions, so the build box is unknown"
        )
    return Designer(
        # `mark` is a key of DESIGNER_CLASSES, so it is one of the three marks.
        mark=cast(Literal["mk1", "mk2", "mk3"], mark),
        dims=(int(dims[0]), int(dims[1]), int(dims[2])),
        foundation_cm=foundation_cm(registry),
    )


def foundation_cm(registry: Registry) -> float:
    """One foundation's side in centimetres, from the game's own buildable."""
    foundation = registry.buildables[FOUNDATION_CLASS]
    width, depth = foundation.width_cm, foundation.depth_cm
    if width is None or depth is None:
        raise ValueError(
            f"{FOUNDATION_CLASS} declares no footprint, so a designer cell has no size"
        )
    if width != depth:
        raise ValueError(
            f"{FOUNDATION_CLASS} is {width} x {depth} cm, not square; a Blueprint "
            "Designer's cell count cannot be turned into centimetres by one number"
        )
    return width


class SfyMachineGroup(_Frozen):
    """``count`` machines all running ``recipe_id`` under the same settings.

    Rates are per machine at ``clock``.  The last machine runs at
    ``last_clock``, which is never faster, so a row's real throughput is
    ``(count - 1)`` machines at the group rate plus one scaled by
    ``last_clock / clock``.
    """

    recipe_id: str
    recipe_class: str
    machine_item_id: str
    machine_class: str
    count: int = Field(gt=0)
    #: Every machine's requested potential but the last's, as a fraction of
    #: 100 %.  FactorioLab states this as a percentage in the URL (``moc=250``).
    #: Capped at :attr:`max_clock` by ``_clock_is_buildable`` rather than by a
    #: ``Field(le=...)``, which pydantic cannot put a ``Fraction`` inside.
    clock: Fraction = Field(gt=0)
    last_clock: Fraction = Field(gt=0)
    #: The highest potential THIS machine can be driven to, computed by
    #: :func:`flab2bp.sfy.rates.max_clock` from the game's own
    #: ``max_potential``, ``potential_shard_slots_default`` and
    #: ``potential_per_shard``.  It is a per-class figure, not a constant: a
    #: buildable that takes no shards cannot be overclocked at all.
    max_clock: Fraction = Field(gt=0)
    #: Somersloops in each machine's production-boost slots.
    somersloops: int = Field(ge=0)
    #: Power shards in each machine but the last, to hold it at ``clock``.
    power_shards_per_machine: int = Field(ge=0)
    #: Power shards in the last machine, to hold it at ``last_clock``.  An
    #: underclocked last machine needs none, so this is usually 0 where
    #: ``power_shards_per_machine`` is not, and a row that ignored the
    #: difference would over-order shards.
    last_power_shards: int = Field(ge=0)
    #: Items/second one machine consumes at ``clock``.
    inputs_per_machine: dict[str, Fraction] = Field(default_factory=dict)
    #: Items/second one machine produces at ``clock``.
    outputs_per_machine: dict[str, Fraction] = Field(default_factory=dict)
    #: Megawatts one machine but the last draws at ``clock``.  A ``float``, not
    #: a ``Fraction``: the game raises the clock to a fractional exponent, so
    #: this figure is irrational and is a REPORT number -- it sizes generators
    #: and fills in a summary, and nothing in geometry or in the rates reads it.
    power_mw_per_machine: float = Field(ge=0)
    #: Megawatts the last machine draws at ``last_clock``.  A report figure for
    #: the same reason as :attr:`power_mw_per_machine`.
    last_power_mw: float = Field(ge=0)

    @model_validator(mode="after")
    def _clock_is_buildable(self) -> SfyMachineGroup:
        if self.clock > self.max_clock:
            raise ValueError(
                f"{self.recipe_id}: clock {self.clock} is above {self.machine_class}'s "
                f"{self.max_clock} ceiling; no number of power shards reaches it"
            )
        return self

    @model_validator(mode="after")
    def _last_clock_le_clock(self) -> SfyMachineGroup:
        if self.last_clock > self.clock:
            raise ValueError(
                f"{self.recipe_id}: last_clock {self.last_clock} exceeds the group's "
                f"clock {self.clock}. The odd machine at the end of a row absorbs the "
                "fractional remainder, so it is never the fastest one."
            )
        return self

    @model_validator(mode="after")
    def _rates_are_positive(self) -> SfyMachineGroup:
        for label, rates in (
            ("input", self.inputs_per_machine),
            ("output", self.outputs_per_machine),
        ):
            for item_id, rate in rates.items():
                if rate <= 0:
                    raise ValueError(
                        f"{self.recipe_id}: {label} rate for {item_id!r} is {rate}; "
                        "a machine that moves nothing is not a machine"
                    )
        return self

    @property
    def _last_share(self) -> Fraction:
        """The last machine's throughput as a multiple of the group rate."""
        return self.last_clock / self.clock

    def _row(self, per_machine: dict[str, Fraction]) -> dict[str, Fraction]:
        share = Fraction(self.count - 1) + self._last_share
        return {item_id: rate * share for item_id, rate in per_machine.items()}

    @property
    def row_inputs(self) -> dict[str, Fraction]:
        """Items/second the whole row consumes."""
        return self._row(self.inputs_per_machine)

    @property
    def row_outputs(self) -> dict[str, Fraction]:
        """Items/second the whole row produces."""
        return self._row(self.outputs_per_machine)

    @property
    def row_power_shards(self) -> int:
        """Power shards the whole row needs: the last machine counted apart."""
        return (self.count - 1) * self.power_shards_per_machine + self.last_power_shards

    @property
    def row_power_mw(self) -> float:
        """Megawatts the whole row draws, the last machine at its own clock."""
        return (self.count - 1) * self.power_mw_per_machine + self.last_power_mw


class SfyBuildSpec(_Frozen):
    """One complete, self-consistent Satisfactory build.

    Invariants enforced at construction, mirroring :class:`flab2bp.spec.BuildSpec`:

    * every rate is an exact positive ``Fraction`` -- no float reaches geometry;
    * every item a group consumes is produced by another group or belted in;
    * belt upgrades are strictly faster than the floor and listed slowest first.

    The dangling-demand check runs only when the spec claims to be complete,
    i.e. it declares external inputs or outputs.  A spec with neither is a
    fragment -- hand-built material for the layout stage -- and is left alone.
    """

    groups: tuple[SfyMachineGroup, ...]
    #: Items belted or piped in at the designer's boundary: ores and fluids.
    external_inputs: dict[str, Fraction] = Field(default_factory=dict)
    #: The objective item(s) leaving the boundary, at the achieved rate.
    outputs: dict[str, Fraction] = Field(default_factory=dict)
    #: Unavoidable non-objective production that must also leave the boundary.
    surplus_outputs: dict[str, Fraction] = Field(default_factory=dict)
    #: The belt FactorioLab chose (``ibe``), else the dataset's ``minBelt``.
    #: The FLOOR: no emitted belt is ever slower.
    belt_item_id: str
    belt_items_per_second: Fraction = Field(gt=0)
    #: Faster belts the build may use, slowest first, up to ``maxBelt``.
    belt_upgrades: tuple[BeltTier, ...] = ()
    label: str = ""

    @model_validator(mode="after")
    def _no_dangling_demand(self) -> SfyBuildSpec:
        if not self.external_inputs and not self.outputs:
            return self  # a fragment, not a claim of completeness
        produced = {item for g in self.groups for item in g.outputs_per_machine}
        supplied = produced | set(self.external_inputs)
        missing = {
            item for g in self.groups for item in g.inputs_per_machine if item not in supplied
        }
        if missing:
            raise ValueError(
                f"{self.label or 'spec'}: nothing supplies {sorted(missing)}. Every "
                "consumed item must be produced by a group or listed in "
                "external_inputs, or the build starves on paste."
            )
        return self

    @model_validator(mode="after")
    def _tiers_are_ordered(self) -> SfyBuildSpec:
        previous = self.belt_items_per_second
        for tier in self.belt_upgrades:
            if tier.items_per_second <= previous:
                raise ValueError(
                    f"{self.label or 'spec'}: belt upgrade {tier.item_id!r} at "
                    f"{tier.items_per_second}/s is not faster than the tier before it "
                    f"({previous}/s); upgrades must be strictly faster than the floor "
                    "and listed slowest first"
                )
            previous = tier.items_per_second
        return self

    @property
    def machine_count(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def belt_tiers(self) -> tuple[BeltTier, ...]:
        """Every belt the build may use, floor first."""
        floor = BeltTier(item_id=self.belt_item_id, items_per_second=self.belt_items_per_second)
        return (floor, *self.belt_upgrades)

    @property
    def power_mw(self) -> float:
        """Megawatts every machine placed draws, each row at its own clocks.

        A report figure, like the group's own power: the game's clock exponent
        is fractional, so this cannot be exact and nothing downstream of the
        rates reads it.
        """
        return sum(g.row_power_mw for g in self.groups)

    @property
    def power_shards(self) -> int:
        """Power shards the whole build needs."""
        return sum(g.row_power_shards for g in self.groups)


__all__ = (
    "DESIGNER_CLASSES",
    "FOUNDATION_CLASS",
    "Designer",
    "SfyBuildSpec",
    "SfyMachineGroup",
    "designer",
    "foundation_cm",
)
