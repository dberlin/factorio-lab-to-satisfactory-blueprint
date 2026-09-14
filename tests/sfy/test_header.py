import struct

from flab2bp.sfy.archive import ObjectRef, Reader, Writer
from flab2bp.sfy.header import (
    BlueprintHeader,
    BlueprintRecord,
    EngineVersion,
    ItemAmount,
    SaveObjectVersionData,
    has_version_block,
    read_header,
    read_record,
    write_header,
    write_record,
)
from flab2bp.sfy.versions import BLUEPRINT_CONFIG_VERSION, LATEST, SaveCustomVersion
from tests.sfy.conftest import FIXTURES


def test_latest_matches_newest_fixture_save_version():
    assert LATEST == 60
    assert SaveCustomVersion.SerializeDataPackageVersionAndCustomVersions == 53


def test_header_round_trip_with_version_block():
    h = BlueprintHeader(
        header_version=2,
        save_version=60,
        build_version=493833,
        dimensions=(4, 4, 4),
        cost=(
            ItemAmount(
                ObjectRef(
                    "",
                    "/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C",
                ),
                12,
            ),
        ),
        recipes=(
            ObjectRef(
                "",
                "/Game/FactoryGame/Recipes/Buildings/Recipe_ConstructorMk1.Recipe_ConstructorMk1_C",
            ),
        ),
        version_data=SaveObjectVersionData(
            data_version=0,
            ue4=522,
            ue5=1017,
            licensee=3,
            engine=EngineVersion(5, 6, 1, 2147977481, "++FactoryGame+rel-main-1.2.0"),
            custom_versions=((bytes(range(16)), 60),),
        ),
    )
    w = Writer()
    write_header(w, h)
    r = Reader(w.getvalue())
    assert read_header(r) == h
    r.expect_end()


def test_header_without_version_block_for_old_saves():
    assert not has_version_block(52) and has_version_block(58)
    h = BlueprintHeader(2, 52, 463028, (6, 6, 6), (), (), None)
    w = Writer()
    write_header(w, h)
    assert read_header(Reader(w.getvalue())) == h


def test_every_fixture_header_parses(sbp_path):
    data = sbp_path.read_bytes()
    h = read_header(Reader(data))
    assert h.header_version == 2
    assert h.dimensions[0] in (4, 5, 6, 12)
    assert (h.version_data is not None) == has_version_block(h.save_version)


def test_record_round_trip_on_every_fixture(sbp_pair):
    sbp, sbpcfg = sbp_pair
    raw = sbpcfg.read_bytes()
    rec = read_record(raw)
    # The corpus spans three FBlueprintConfigVersion values: AddedIconLibraryPath (3)
    # for save version 46, AddedLastEditedBy (4) for 52 and
    # RemovedFilteredProfanityName (6) for everything since the version block.
    assert 3 <= rec.config_version <= BLUEPRINT_CONFIG_VERSION
    if has_version_block(read_header(Reader(sbp.read_bytes())).save_version):
        assert rec.config_version == BLUEPRINT_CONFIG_VERSION
    assert write_record(rec) == raw


def test_record_fields_of_biofuel():
    rec = read_record((FIXTURES / "biofuel.sbpcfg").read_bytes())
    assert rec.description == ""
    assert rec.icon_id == 282
    assert rec.icon_library_path == "/Game/FactoryGame/-Shared/Blueprint/IconLibrary"
    assert rec.icon_library_name == "IconLibrary"
    assert abs(rec.color[3] - 1.0) < 1e-6
    assert rec.tail == struct.pack("<iB", 6, 0)


def test_new_record_has_the_shape_the_game_writes_today():
    biofuel = read_record((FIXTURES / "biofuel.sbpcfg").read_bytes())
    fresh = BlueprintRecord.new("a description")
    assert fresh.config_version == biofuel.config_version == BLUEPRINT_CONFIG_VERSION
    assert fresh.icon_library_path == biofuel.icon_library_path
    assert fresh.icon_library_name == biofuel.icon_library_name
    assert fresh.tail == biofuel.tail
    raw = write_record(fresh)
    assert write_record(read_record(raw)) == raw
