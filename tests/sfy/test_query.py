from __future__ import annotations

from flab2bp.sfy.codec import read_pair, read_sbp, read_sbp_file, write_sbp_file
from flab2bp.sfy.query import children, connected, find, find_all, object_index, spline_points
from tests.sfy.conftest import FIXTURES


def test_belt_spline_points_and_connections_in_biofuel():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    index = object_index(bp)
    belts = [(h, d) for h, d in bp.objects if h.class_name.startswith("Build_ConveyorBelt")]
    assert belts
    for _h, d in belts:
        pts = spline_points(d)
        assert len(pts) >= 2
        assert all(len(p) == 3 for p in pts)
        for comp in d.components:
            ch, cd = index[comp.path]
            assert ch.class_name == "FGFactoryConnectionComponent"
            peer = connected(cd)
            if peer is not None:
                assert peer.path in index, peer.path


def test_find_returns_none_for_absent_property():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    _, d = bp.objects[0]
    assert find(d.properties, "mDoesNotExist") is None


def test_find_all_collects_every_match_and_find_takes_the_first():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    _, d = next((h, d) for h, d in bp.objects if h.class_name.startswith("Build_ConveyorBelt"))
    assert find_all(d.properties, "mDoesNotExist") == ()
    values = find_all(d.properties, "mSplineData")
    assert len(values) == 1
    assert find(d.properties, "mSplineData") is values[0]


def test_children_are_the_components_the_actor_lists():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    for h, d in bp.objects:
        if h.kind != 1:
            continue
        assert {ch.path for ch, _ in children(bp, h.path)} == {c.path for c in d.components}


def test_spline_points_is_empty_for_a_non_spline_object():
    bp = read_sbp((FIXTURES / "biofuel.sbp").read_bytes())
    _, d = next((h, d) for h, d in bp.objects if h.class_name == "Build_ConstructorMk1_C")
    assert spline_points(d) == ()


def test_file_helpers_round_trip_a_fixture_byte_for_byte(sbp_path, tmp_path):
    out = tmp_path / sbp_path.name
    write_sbp_file(out, read_sbp_file(sbp_path))
    assert out.read_bytes() == sbp_path.read_bytes()


def test_read_pair_reads_the_blueprint_and_its_config(sbp_pair):
    sbp_path, cfg_path = sbp_pair
    bp, rec = read_pair(sbp_path, cfg_path)
    assert bp.objects
    assert rec.config_version > 0
