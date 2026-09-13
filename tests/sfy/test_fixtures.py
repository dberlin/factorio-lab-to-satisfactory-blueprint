from tests.sfy.conftest import FIXTURES, fixture_pairs


def test_manifest_lists_every_file_on_disk():
    listed = {p.name for pair in fixture_pairs() for p in pair}
    on_disk = {p.name for p in FIXTURES.iterdir() if p.suffix in (".sbp", ".sbpcfg")}
    assert listed == on_disk


def test_every_sbp_starts_with_header_version_2():
    for sbp, _ in fixture_pairs():
        assert sbp.read_bytes()[:4] == b"\x02\x00\x00\x00", sbp.name
