from __future__ import annotations

from collections import Counter

import pytest

from flab2bp.sfy.archive import ArchiveError, Reader, Writer
from flab2bp.sfy.codec import payload_bytes, read_sbp
from flab2bp.sfy.trailers import (
    BuildableTrailer,
    ComponentTrailer,
    ConveyorTrailer,
    PlainTrailer,
    PowerLineTrailer,
    read_trailer,
    trailer_for_new,
    write_trailer,
)
from tests.sfy.conftest import fixture_paths


def test_trailer_kinds_by_class_in_current_family():
    kinds = Counter()
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        if bp.header.save_version < 58:
            continue
        for h, d in bp.objects:
            kinds[(h.class_name, type(d.trailer).__name__)] += 1
    assert kinds[("Build_ConstructorMk1_C", "BuildableTrailer")] > 0
    assert kinds[("FGFactoryConnectionComponent", "ComponentTrailer")] > 0
    assert kinds[("Build_ConveyorBeltMk1_C", "ConveyorTrailer")] > 0
    assert kinds[("Build_PowerLine_C", "PowerLineTrailer")] > 0
    plain = {cls for (cls, kind) in kinds if kind == "PlainTrailer"}
    assert not (
        plain
        & {
            "Build_ConstructorMk1_C",
            "Build_ConveyorBeltMk1_C",
            "Build_PowerPoleMk1_C",
            "Build_Foundation_8x1_01_C",
            "FGFactoryConnectionComponent",
            "BP_ResourceNode_C",
        }
    )


def test_every_fixture_trailer_is_classified_by_object_kind():
    """Actors carry four zero bytes and components eight, across all four save versions.

    ``BP_ResourceNode_C`` is the class that proves the rule is the object kind
    and not a ``Build_`` name prefix: it is an actor with a 4-byte trailer.
    """
    kinds = Counter()
    for path in fixture_paths():
        bp = read_sbp(path.read_bytes())
        for h, d in bp.objects:
            kinds[(h.class_name, type(d.trailer).__name__)] += 1
    assert kinds[("BP_ResourceNode_C", "BuildableTrailer")] > 0
    assert not {cls for (cls, kind) in kinds if kind == "PlainTrailer"}


def test_payload_identity_still_holds_with_decoded_bodies(sbp_path):
    from flab2bp.sfy.chunks import decompress_stream, find_stream_start
    from flab2bp.sfy.header import read_header

    data = sbp_path.read_bytes()
    r = Reader(data)
    read_header(r)
    assert payload_bytes(read_sbp(data)) == decompress_stream(data, find_stream_start(data, r.pos))


def test_trailer_for_new_classes():
    assert trailer_for_new("Build_ConstructorMk1_C", 1) == BuildableTrailer()
    assert trailer_for_new("Build_ConveyorBeltMk3_C", 1) == ConveyorTrailer(())
    assert trailer_for_new("Build_ConveyorLiftMk1_C", 1) == ConveyorTrailer(())
    assert trailer_for_new("FGFactoryConnectionComponent", 0) == ComponentTrailer()
    with pytest.raises(KeyError):
        trailer_for_new("Build_PowerLine_C", 1)


def test_each_trailer_shape_round_trips():
    for class_name, kind, expected in (
        ("Build_ConstructorMk1_C", 1, BuildableTrailer()),
        ("FGFactoryConnectionComponent", 0, ComponentTrailer()),
        ("Build_ConveyorBeltMk1_C", 1, ConveyorTrailer(())),
        ("Build_PowerLine_C", 1, PowerLineTrailer(b"\x00\x00\x00\x00\x07wire")),
    ):
        w = Writer()
        write_trailer(w, expected)
        r = Reader(w.getvalue())
        assert read_trailer(r, class_name, kind) == expected
        r.expect_end()


def test_unexpected_trailer_bytes_stay_plain():
    """A shape the class does not predict is kept verbatim rather than guessed at."""
    raw = b"\x01\x02\x03\x04\x05"
    assert read_trailer(Reader(raw), "Build_ConstructorMk1_C", 1) == PlainTrailer(raw)
    assert read_trailer(Reader(raw), "FGFactoryConnectionComponent", 0) == PlainTrailer(raw)
    counted = b"\x00\x00\x00\x00\x02\x00\x00\x00rest"
    assert read_trailer(Reader(counted), "Build_ConveyorBeltMk1_C", 1) == PlainTrailer(counted)
    w = Writer()
    write_trailer(w, PlainTrailer(counted))
    assert w.getvalue() == counted


def test_writing_something_that_is_not_a_trailer_raises():
    """A future trailer kind must fail loudly rather than write zero bytes."""
    with pytest.raises(ArchiveError):
        write_trailer(Writer(), "not a trailer")
