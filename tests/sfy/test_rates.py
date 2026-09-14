"""Turning a captured FactorioLab Satisfactory flow into a build spec.

Every expectation here is pinned to a committed flow export.  FactorioLab's
chosen flow is authoritative: where the brief's arithmetic and the export
disagree, these tests state what the export says.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from flab2bp.lab.data import load_vendored
from flab2bp.lab.flow import FlowRow, FlowSelection, load_flow
from flab2bp.lab.url import Game, parse_url
from flab2bp.sfy.labmap import load_lab_map
from flab2bp.sfy.rates import RatesRefusal, max_clock, spec_from_flow
from flab2bp.sfy.registry import load_registry
from flab2bp.sfy.spec import SfyBuildSpec, SfyMachineGroup

REPO_ROOT = Path(__file__).resolve().parents[2]
FLOWS = REPO_ROOT / "tests" / "fixtures" / "sfy_flows"

#: Every fixture is exported at FactorioLab's per-minute display rate.
PER_MINUTE = Fraction(1, 60)


def _url(name: str) -> str:
    """The URL FactorioLab wrote into line 1 of the export."""
    return (FLOWS / f"{name}.csv").read_text(encoding="utf-8").splitlines()[0].strip().strip('"')


def _spec(name: str) -> SfyBuildSpec:
    path = FLOWS / f"{name}.csv"
    url = _url(name)
    return spec_from_flow(
        load_vendored(Game.SFY),
        parse_url(url),
        load_flow(path, url=url),
        load_registry(),
        load_lab_map(),
    )


def _power_column(name: str) -> dict[str, Fraction]:
    """The export's ``Power`` cell per recipe, in kW, exactly as written.

    ``FlowRow`` does not carry ``Power`` -- nothing in the pipeline consumes it
    -- so this reads the committed CSV directly.  FactorioLab writes an exact
    rational behind a leading ``=``.
    """
    rows = list(
        csv.DictReader((FLOWS / f"{name}.csv").read_text(encoding="utf-8").splitlines()[1:])
    )
    return {row["Recipe"]: Fraction(row["Power"].lstrip("=")) for row in rows if row.get("Recipe")}


def _group(spec: SfyBuildSpec, recipe_id: str) -> SfyMachineGroup:
    return next(g for g in spec.groups if g.recipe_id == recipe_id)


def test_iron_plate_at_sixty_is_three_constructors_at_full_clock() -> None:
    spec = _spec("iron-plate-60")
    plate = _group(spec, "iron-plate")
    assert plate.machine_class == "Build_ConstructorMk1_C"
    assert plate.recipe_class == "Recipe_IronPlate_C"
    assert plate.count == 3  # 60/min needs 3 constructors at 20/min
    assert plate.clock == 1 and plate.last_clock == 1
    assert plate.row_outputs["iron-plate"] == Fraction(1)  # 60/min = 1/s


def test_ten_reinforced_plates_a_minute_is_two_assemblers_at_full_clock() -> None:
    # The brief predicted an underclocked last assembler.  The captured flow
    # says Machines=2 exactly -- 10/min over 5/min per assembler divides -- and
    # the flow is authoritative, so the whole count is what this pins.
    spec = _spec("reinforced-iron-plate-10")
    rip = _group(spec, "reinforced-iron-plate")
    assert rip.machine_class == "Build_AssemblerMk1_C"
    assert rip.count == 2 and rip.last_clock == Fraction(1)
    assert rip.row_outputs["reinforced-iron-plate"] == Fraction(1, 6)


def test_a_fractional_machine_count_rounds_up_and_underclocks_the_last_machine() -> None:
    spec = _spec("iron-plate-50")
    plate = _group(spec, "iron-plate")
    assert plate.count == 3  # FactorioLab asks for 2.5 constructors
    assert plate.clock == Fraction(1) and plate.last_clock == Fraction(1, 2)
    # Two constructors at 20/min plus one at 10/min is the 50/min asked for.
    assert plate.row_outputs["iron-plate"] == Fraction(5, 6)


@pytest.mark.parametrize(
    "name",
    [
        "iron-plate-60",
        "reinforced-iron-plate-10",
        "iron-plate-50",
        "iron-plate-60-overclock-250",
        "iron-plate-60-somersloop",
        "iron-plate-60-belt-mk3",
    ],
)
def test_the_row_rates_agree_with_the_flow_to_the_fraction(name: str) -> None:
    path = FLOWS / f"{name}.csv"
    url = path.read_text(encoding="utf-8").splitlines()[0].strip().strip('"')
    flow = load_flow(path, url=url)
    spec = _spec(name)
    assert spec.groups, "the flow should have produced at least one group"
    for group in spec.groups:
        row = flow.by_recipe[group.recipe_id]
        assert row.machines is not None and row.items is not None
        produced = row.items + (row.surplus or 0)
        # FactorioLab's fractional machine count times our per-machine rate must
        # land exactly on the rate FactorioLab wrote for the row's own item.
        assert group.outputs_per_machine[row.item_id] * row.machines == produced * PER_MINUTE, (
            f"{name}: {group.recipe_id} disagrees with the flow"
        )


def test_external_inputs_are_the_flows_mined_items() -> None:
    spec = _spec("iron-plate-60")
    assert spec.external_inputs == {"iron-ore": Fraction(3, 2)}  # 90/min
    # The miner itself is never a group: a Blueprint Designer cannot hold one.
    assert [g.recipe_id for g in spec.groups] == ["iron-plate", "iron-ingot"]
    assert spec.outputs == {"iron-plate": Fraction(1)}
    assert spec.surplus_outputs == {}


def test_a_fluid_in_the_flow_refuses_with_the_cause_named() -> None:
    with pytest.raises(RatesRefusal) as caught:
        _spec("plastic-10")
    assert caught.value.cause == "fluids are M4"
    assert "crude-oil" in str(caught.value)


def _spec_from_rows(name: str, rows: tuple[FlowRow, ...]) -> SfyBuildSpec:
    """Build a spec from a fixture flow with its rows edited.

    Every committed fixture is a flow FactorioLab really produced, and none of
    them happens to exercise the two paths below.  Rather than hand-write a CSV
    -- which would make it a fixture that is not an export -- this takes a real
    flow and states the one difference in Python, where it is visible.
    """
    flow = load_flow(FLOWS / f"{name}.csv", url=_url(name))
    edited = FlowSelection(source_url=flow.source_url, rows=rows, columns=flow.columns)
    return spec_from_flow(
        load_vendored(Game.SFY),
        parse_url(_url(name)),
        edited,
        load_registry(),
        load_lab_map(),
    )


def test_a_miners_spare_ore_is_not_something_the_build_has_to_belt_out() -> None:
    """An extraction row's surplus is a rounding artefact, not a product.

    Rounding a node's miner count up leaves spare ore at the node, outside the
    Blueprint Designer.  Listing it in `surplus_outputs` would ask the layout
    stage to belt ore OUT of a block that never mined it.
    """
    flow = load_flow(FLOWS / "iron-plate-60.csv", url=_url("iron-plate-60"))
    rows = tuple(
        replace(row, surplus=Fraction(30)) if row.recipe_id == "iron-ore" else row
        for row in flow.rows
    )
    assert any(r.recipe_id == "iron-ore" and r.surplus == 30 for r in rows)

    spec = _spec_from_rows("iron-plate-60", rows)
    assert spec.surplus_outputs == {}

    # A byproduct row inside the build IS carried out, so the cut above is
    # about extraction and not about surpluses in general.  This is the shape
    # `heavy-oil-residue` really has in the plastic-10 fixture: an item nothing
    # draws on, with no recipe of its own.
    byproduct = (*flow.rows, FlowRow(item_id="screw", items=Fraction(0), surplus=Fraction(30)))
    assert _spec_from_rows("iron-plate-60", byproduct).surplus_outputs == {"screw": Fraction(1, 2)}


def test_an_item_the_dataset_does_not_carry_is_refused_by_name() -> None:
    """An unknown id must not slip past the fluid gate and be belted as a solid."""
    flow = load_flow(FLOWS / "iron-plate-60.csv", url=_url("iron-plate-60"))
    rows = (
        *flow.rows,
        FlowRow(item_id="unobtanium", items=Fraction(0), surplus=Fraction(5)),
    )
    with pytest.raises(RatesRefusal) as caught:
        _spec_from_rows("iron-plate-60", rows)
    assert caught.value.cause == "unknown item"
    assert "unobtanium" in str(caught.value)


def test_the_belt_floor_and_ceiling_come_from_the_url_or_the_defaults() -> None:
    default = _spec("iron-plate-60")
    assert default.belt_item_id == "conveyor-belt-mk1"
    assert default.belt_items_per_second == Fraction(1)
    # Mk6 exists in the dataset but defaults.maxBelt stops at Mk5.
    assert [t.item_id for t in default.belt_upgrades] == [
        "conveyor-belt-mk2",
        "conveyor-belt-mk3",
        "conveyor-belt-mk4",
        "conveyor-belt-mk5",
    ]

    chosen = _spec("iron-plate-60-belt-mk3")
    assert chosen.belt_item_id == "conveyor-belt-mk3"
    assert chosen.belt_items_per_second == Fraction(9, 2)
    assert [t.item_id for t in chosen.belt_upgrades] == [
        "conveyor-belt-mk4",
        "conveyor-belt-mk5",
    ]


def test_overclock_above_one_is_carried_and_shards_are_counted() -> None:
    spec = _spec("iron-plate-60-overclock-250")
    plate = _group(spec, "iron-plate")
    assert plate.clock == Fraction(5, 2)  # the URL says moc=250, a percentage
    assert plate.max_clock == Fraction(5, 2)  # max_potential 1 + 3 shards at 0.5
    assert plate.count == 2  # FactorioLab asks for 1.2 constructors
    assert plate.last_clock == Fraction(1, 2)
    # Three shards take a machine from 100% to 250% at half a shard each, but
    # the last constructor runs at 50% and needs none: the row buys 3, not 6.
    assert plate.power_shards_per_machine == 3
    assert plate.last_power_shards == 0
    assert plate.row_power_shards == 3
    assert plate.row_outputs["iron-plate"] == Fraction(1)


def test_a_group_at_the_default_clock_needs_no_power_shards() -> None:
    spec = _spec("iron-plate-60")
    assert all(g.power_shards_per_machine == 0 for g in spec.groups)
    assert all(g.last_power_shards == 0 for g in spec.groups)
    assert spec.power_shards == 0


def test_the_clock_ceiling_is_the_games_own_potential_plus_its_shard_slots() -> None:
    registry = load_registry()
    limits = registry.limits
    assert limits.potential_shard_slots_default == 3
    assert limits.potential_per_shard == 0.5
    for machine_cls in ("Build_ConstructorMk1_C", "Build_AssemblerMk1_C", "Build_OilRefinery_C"):
        assert registry.buildables[machine_cls].max_potential == 1.0
        assert max_clock(registry, machine_cls) == Fraction(5, 2)
    # And every group carries the ceiling its own machine has.
    assert all(g.max_clock == Fraction(5, 2) for g in _spec("iron-plate-60").groups)


def test_somersloops_double_output_the_way_factoriolab_computes_it() -> None:
    spec = _spec("iron-plate-60-somersloop")
    plate = _group(spec, "iron-plate")
    assert plate.somersloops == 1
    # One sloop in the Constructor's one slot is +100% output: 40/min, not 20.
    assert plate.outputs_per_machine["iron-plate"] == Fraction(2, 3)
    # Inputs are per craft and untouched by the boost, so they fall with the
    # craft count: the flow asks for 45 ingots a minute, not 90.
    assert plate.inputs_per_machine["iron-ingot"] == Fraction(1, 2)
    assert plate.count == 2 and plate.last_clock == Fraction(1, 2)
    assert plate.row_outputs["iron-plate"] == Fraction(1)
    assert plate.row_inputs["iron-ingot"] == Fraction(3, 4)
    # Power is (1 + filled/total) squared, so a full Constructor draws 4x.
    assert plate.power_mw_per_machine == pytest.approx(16.0)
    # The smelters were left alone by the URL's machine setting.
    ingot = _group(spec, "iron-ingot")
    assert ingot.somersloops == 0
    assert ingot.power_mw_per_machine == pytest.approx(4.0)


def test_power_per_machine_is_the_games_draw_raised_to_the_games_exponent() -> None:
    plain = _group(_spec("iron-plate-60"), "iron-plate")
    assert plain.power_mw_per_machine == 4.0  # registry Build_ConstructorMk1_C, clock 1
    assert plain.last_power_mw == 4.0
    assert plain.row_power_mw == 12.0  # three constructors, all at clock 1

    fast = _group(_spec("iron-plate-60-overclock-250"), "iron-plate")
    exponent = load_registry().buildables["Build_ConstructorMk1_C"].power_exponent
    assert exponent == 1.321929
    assert fast.power_mw_per_machine == pytest.approx(4.0 * 2.5**1.321929)
    # The last constructor runs at 50%, so it draws LESS than its rating.
    assert fast.last_power_mw == pytest.approx(4.0 * 0.5**1.321929)
    assert fast.last_power_mw < 4.0


def test_per_machine_power_agrees_with_factoriolabs_power_column() -> None:
    """FactorioLab bills `ceil(machines)` at the full clock; ours is per machine.

    FactorioLab's Power cell is one number for the whole row, and it charges
    every machine -- including the odd underclocked one -- at the group's clock.
    Dividing it by `ceil(machines)` therefore recovers exactly the figure
    `power_mw_per_machine` states, and the two may differ only by FactorioLab's
    hard-coded 1.321928 against the game's own 1.321929.
    """
    name = "iron-plate-60-overclock-250"
    powers = _power_column(name)
    spec = _spec(name)
    for group in spec.groups:
        theirs_kw = powers[group.recipe_id] / group.count
        ours_kw = group.power_mw_per_machine * 1000
        assert ours_kw == pytest.approx(float(theirs_kw), rel=1e-6), group.recipe_id
        # Tight enough to be the same model, loose enough for that last digit.
        # This inequality needs an OVERCLOCKED row: at clock 1 both exponents
        # raise 1 to a power and the two figures agree exactly, so a fixture at
        # the default clock would assert nothing here.
        assert ours_kw != float(theirs_kw)


def test_no_satisfactory_module_loads_the_dsp_catalog() -> None:
    """No module under `flab2bp.sfy` may drag DSP's catalog in behind it.

    `flab2bp.rates.adjust`, `flab2bp.rates.machine_choice` and
    `flab2bp.lab.flow` all import `flab2bp.dsp.catalog` at module scope, which
    is why `rates.py` copies `select_machine` and the ceiling and takes the flow
    types under TYPE_CHECKING.  Guarding `rates.py` alone would leave every
    other sfy module free to open the door unnoticed, so this imports the WHOLE
    package -- every submodule `pkgutil.walk_packages` finds, 22 of them today,
    including `layout.emit` and `templates` -- and then asks what is loaded.

    A fresh interpreter is the only honest check: inside this one the DSP
    modules are long since imported by other tests.  A failure names the
    submodule that pulled them in; the fix is to sever that import, never to
    allowlist it here.
    """
    loaded, failed, walked = _dsp_modules_after_importing_all_of_sfy()
    assert failed == {}, f"these sfy modules would not import at all: {failed}"
    # The pipeline is the one that reaches `flab2bp.lab.flow` and
    # `flab2bp.lab.capture`, so a walk that somehow missed it would clear this
    # test without ever opening the door it is about.
    assert "flab2bp.sfy.pipeline" in walked, (
        "the walk did not import flab2bp.sfy.pipeline, so this test says nothing "
        f"about the module most likely to pull DSP in: {walked}"
    )
    assert loaded == [], (
        "importing flab2bp.sfy loaded DSP modules; find the sfy module that "
        f"imports flab2bp.dsp (or flab2bp.lab.flow, or flab2bp.rates) and sever it: {loaded}"
    )


def test_the_satisfactory_pipeline_alone_loads_no_dsp_module() -> None:
    """`flab2bp.sfy.pipeline` on its own, which is what the CLI imports.

    The whole-package walk above would still pass if `pipeline` were clean only
    because some OTHER sfy module happened to be imported first and pre-empted
    it. This is the module the `sfy` CLI arm actually loads, and it is the one
    that reaches `flab2bp.lab.flow`'s CSV parser and `flab2bp.lab.capture`, so
    it gets its own fresh interpreter.
    """
    loaded, failed, _ = _dsp_modules_after_importing("flab2bp.sfy.pipeline")
    assert failed == {}, f"flab2bp.sfy.pipeline would not import at all: {failed}"
    assert loaded == [], (
        "importing flab2bp.sfy.pipeline loaded DSP modules; the door is most "
        "likely flab2bp.lab.flow's catalog import, which belongs inside its "
        f"`df-` branch rather than at module scope: {loaded}"
    )


def _dsp_modules_after_importing_all_of_sfy() -> tuple[list[str], dict[str, str], list[str]]:
    """Import every `flab2bp.sfy` submodule in a fresh interpreter.

    Returns the DSP modules that ended up in `sys.modules`, any submodule that
    raised on import -- an sfy module that cannot be imported would otherwise
    make this test pass by never running -- and the submodules actually walked.
    """
    return _dsp_modules_after_importing(None)


def _dsp_modules_after_importing(module: str | None) -> tuple[list[str], dict[str, str], list[str]]:
    """The DSP fallout of importing one sfy module, or every one of them.

    `module` `None` walks the whole package; a name imports just that module.
    A fresh interpreter is the only honest check: inside this one the DSP
    modules are long since imported by other tests.
    """
    probe = """
import importlib, json, pkgutil, sys
import flab2bp.sfy
wanted = json.loads(sys.argv[1])
if wanted is None:
    names = [info.name for info in pkgutil.walk_packages(flab2bp.sfy.__path__, "flab2bp.sfy.")]
else:
    names = [wanted]
failed = {}
walked = []
for name in names:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failed[name] = f"{type(exc).__name__}: {exc}"
    else:
        walked.append(name)
print(json.dumps({
    "dsp": sorted(m for m in sys.modules if m.startswith("flab2bp.dsp")),
    "failed": failed,
    "walked": walked,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, json.dumps(module)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    parsed = json.loads(result.stdout.strip().splitlines()[-1])
    return parsed["dsp"], parsed["failed"], parsed["walked"]


def test_every_fixture_flow_is_the_file_its_manifest_describes() -> None:
    """The manifest is the fixtures' provenance, so it must still be true.

    A CSV edited by hand -- to make a test pass, or by a stray formatter --
    would break the one rule these fixtures exist to hold: that they are what
    FactorioLab wrote.  The hash catches that, and comparing line 1 to
    `requested_url` catches a manifest row wired to the wrong file.
    """
    manifest = json.loads((FLOWS / "MANIFEST.json").read_text(encoding="utf-8"))
    listed = {entry["file"] for entry in manifest["flows"]}
    on_disk = {path.name for path in FLOWS.glob("*.csv")}
    assert listed == on_disk, "every committed CSV must have a manifest entry and vice versa"

    for entry in manifest["flows"]:
        raw = (FLOWS / entry["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], entry["file"]
        assert len(raw) == entry["bytes"], entry["file"]
        exported = raw.decode("utf-8").splitlines()[0].strip().strip('"')
        assert exported == entry["exported_url"], entry["file"]
        # FactorioLab reorders the query and appends v=11, so the requested and
        # exported URLs are the same state seen twice, not the same string.
        assert _query(exported) == _query(entry["requested_url"]), entry["file"]


def _query(url: str) -> set[str]:
    return set(url.partition("?")[2].split("&"))


def test_a_flow_whose_rates_contradict_the_model_is_refused_with_the_row_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import flab2bp.sfy.rates as rates

    real = rates._crafts_per_second

    def wrong(*args: object, **kwargs: object) -> Fraction:
        return real(*args, **kwargs) * 2  # type: ignore[arg-type]

    monkeypatch.setattr(rates, "_crafts_per_second", wrong)
    with pytest.raises(RatesRefusal) as caught:
        _spec("iron-plate-60")
    assert caught.value.cause == "flow rate disagrees with the rate model"
    assert "iron-plate" in str(caught.value)
