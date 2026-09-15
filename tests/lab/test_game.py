"""The `Game` parameter over the FactorioLab side, and the vendored sfy dataset."""

from fractions import Fraction

import pytest

from flab2bp.lab.data import load_vendored, load_vendored_hash_index
from flab2bp.lab.url import Game, UnsupportedDatasetError, parse_url


def test_an_sfy_url_parses_and_names_its_game() -> None:
    request = parse_url("https://factoriolab.github.io/sfy/list?o=iron-plate*60&v=11")
    assert request.game is Game.SFY
    assert request.mod_id == "sfy"


def test_a_dataset_nobody_supports_is_still_refused() -> None:
    with pytest.raises(UnsupportedDatasetError):
        parse_url("https://factoriolab.github.io/factorio/list?o=iron-plate*60&v=11")


def test_the_vendored_sfy_dataset_carries_belts_pipes_and_the_five_named_flags() -> None:
    data = load_vendored(Game.SFY)
    assert data.belt_speed("conveyor-belt-mk3") == Fraction(9, 2)
    assert data.pipe_speed("pipeline-mk2") == Fraction(10)
    assert data.defaults.min_pipe == "pipeline-mk1"
    assert data.defaults.max_belt == "conveyor-belt-mk5"
    assert {"overclock", "somersloop", "resourcePurity", "power", "consumptionAsDrain"} <= set(
        data.flags
    )


def test_the_sfy_hash_tables_index_pipes_among_belts() -> None:
    tables = load_vendored_hash_index(Game.SFY)
    assert tables.belts[5:7] == ("pipeline-mk1", "pipeline-mk2")


def test_the_dsp_dataset_is_still_the_default() -> None:
    assert load_vendored() is load_vendored(Game.DSP)


def test_a_bare_game_string_loads_the_same_dataset_as_the_enum() -> None:
    assert load_vendored("sfy") is load_vendored(Game.SFY)
    assert load_vendored("dsp") is load_vendored(Game.DSP)
    assert load_vendored_hash_index("sfy").belts == load_vendored_hash_index(Game.SFY).belts


def test_a_game_nobody_supports_names_itself_in_the_error() -> None:
    with pytest.raises(ValueError, match="sdy"):
        load_vendored("sdy")


def test_game_lives_in_a_leaf_module_and_is_re_exported() -> None:
    from flab2bp.lab import games, url

    assert url.Game is games.Game
